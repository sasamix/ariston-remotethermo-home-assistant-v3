"""The Ariston integration."""

from __future__ import annotations

import json
import asyncio
from copy import deepcopy

from datetime import timedelta

import html
import logging
import re
from urllib.parse import urljoin

import aiohttp

import voluptuous as vol

from ariston import Ariston, DeviceAttribute, SystemType
from ariston.ariston_api import AristonAPI, ConnectionException
from ariston.const import ARISTON_API_URL, ARISTON_USER_AGENT
from ariston.galevo_device import AristonGalevoDevice
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.const import (
    ATTR_DEVICE_ID,
    CONF_DEVICE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util.unit_system import METRIC_SYSTEM
from homeassistant.util import dt as dt_util

from .const import (
    API_URL_SETTING,
    API_USER_AGENT,
    BUS_ERRORS_COORDINATOR,
    BUS_ERRORS_SCAN_INTERVAL,
    COORDINATOR,
    DEFAULT_BUS_ERRORS_SCAN_INTERVAL_SECONDS,
    DEFAULT_ENERGY_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    ENERGY_COORDINATOR,
    ENERGY_SCAN_INTERVAL,
    DEFAULT_TIME_PROGRAM_SCAN_INTERVAL_SECONDS,
    TIME_PROGRAM_SCAN_INTERVAL,
)
from .cloud_governor import CloudGovernor, CloudPaused, category_of, is_block
from .cloud_pacing import AdaptivePacer
from .coordinator import DeviceDataUpdateCoordinator
from .dhw_scenarios import async_setup_dhw_scenario_manager
from .heating_scenarios import async_setup_heating_scenario_managers

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.WATER_HEATER,
]

SERVICE_SET_ITEM_BY_ID = "set_item_by_id"
PACING_TICK = timedelta(seconds=30)  # local due-check only, no request
# After each two-hour boundary Ariston publishes the finished interval some
# minutes later. Poll energy faster until it arrives, then fall back.
ENERGY_BURST_INTERVAL = timedelta(minutes=2)
ENERGY_BURST_WINDOW = timedelta(minutes=40)
# Outside the post-boundary window no new energy interval is expected.
ENERGY_QUIET_INTERVAL = timedelta(minutes=15)
# menuItems carry only signal strength and heating return temperature.
MENU_ITEMS_REFRESH = timedelta(minutes=15)
# Ariston energy settings/account (costs, last month) change rarely.
ENERGY_AUX_REFRESH = timedelta(hours=6)
GOVERNOR_STATUS_ENTITY = "sensor.ariston_cloud_status"
GOVERNORS = "ariston_cloud_governors"
ATTR_ITEM_ID = "item_id"
ATTR_ZONE = "zone"
ATTR_VALUE = "value"

SET_ITEM_BY_ID_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_ITEM_ID): cv.string,
        vol.Required(ATTR_ZONE): cv.positive_int,
        vol.Required(ATTR_VALUE): vol.Coerce(float),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ariston from a config entry."""
    ariston = Ariston()
    try:
        api_url_setting = entry.data.get(API_URL_SETTING, ARISTON_API_URL)

        api_user_agent = entry.data.get(API_USER_AGENT, ARISTON_USER_AGENT)

        governor, publish_governor = await async_get_governor(hass, entry)
        if governor.paused(dt_util.utcnow()):
            # Ariston blocked this account; do not even log in until the
            # pause ends (a reload must not restart the block).
            raise ConfigEntryNotReady(
                f"Ariston cloud paused until {governor.paused_until.isoformat()}"
            )

        # Same as Ariston.async_connect(), with every request of the
        # account passing through the governor (including the login).
        ariston.api = AristonAPI(
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            api_url_setting,
            api_user_agent,
        )
        govern_api(ariston.api, governor, publish_governor)
        reponse = await ariston.api.async_connect()
        if not reponse:
            _LOGGER.error(
                "Failed to connect to Ariston with device: %s",
                entry.data[CONF_DEVICE].get(DeviceAttribute.NAME),
            )
            raise ConfigEntryAuthFailed

        device = await ariston.async_hello(
            entry.data[CONF_DEVICE].get(DeviceAttribute.GW),
            hass.config.units is METRIC_SYSTEM,
        )
        if device is None:
            return False

        await device.async_get_features()

        original_async_update_state = device.async_update_state
        menu_refresh = {"at": None}

        async def async_update_galevo_state():
            """Properties every poll; menuItems (2 diagnostic values) rarely."""
            now = dt_util.utcnow()
            if not governor.allow(now, "high", 1, "state"):
                return None  # over the hourly budget: keep the last state
            if len(device.features) == 0:
                await device.async_get_features()
            data = await device.api.async_get_properties(
                device.gw, device.features, device.language_tag, device.umsys,
            )
            if data is None:
                raise UpdateFailed("Ariston returned no device properties")
            device.data = data
            last = menu_refresh["at"]
            if (last is None or now - last >= MENU_ITEMS_REFRESH) and governor.allow(
                    now, "normal", 1, "menu_items"):
                menu_refresh["at"] = now
                try:
                    device.menu_items = await device.api.async_get_menu_items(device.gw)
                except CloudPaused:
                    pass
                except Exception as err:  # keep previous diagnostics values
                    _LOGGER.debug("Ariston menuItems unavailable: %r", err)
            device._update_state()
            return None

        async def async_update_state_resilient():
            """Update normal device state even if Ariston menuItems temporarily returns 500.

            python-ariston-api fetches properties and menuItems together with
            asyncio.gather().  A server-side menuItems failure therefore used to
            discard an otherwise valid properties response and made the whole
            config entry unavailable.  Retry the essential properties request
            alone and keep the previous menuItems snapshot.
            """
            try:
                return await original_async_update_state()
            except CloudPaused:
                raise
            except Exception as err:
                if is_block(err):
                    raise  # retrying during a block only extends it
                _LOGGER.warning(
                    "Ariston full state update failed (%r); retrying essential properties only",
                    err,
                )

                data = await device.api.async_get_properties(
                    device.gw,
                    device.features,
                    device.language_tag,
                    device.umsys,
                )
                if data is None:
                    raise

                device.data = data
                # Preserve the last valid menu_items list. On first start the
                # library initializes it to an empty list, which is safe.
                device._update_state()

                _LOGGER.warning(
                    "Ariston state updated without menuItems; integration remains available"
                )
                return None

        async def async_update_dhw_program(notify=False):
            """Update only the DHW time program."""
            if device.system_type != SystemType.GALEVO:
                return

            try:
                api = device.api
                base_url = getattr(api, "_AristonAPI__api_url", None)
                if not base_url:
                    return

                url = f"{base_url}remote/timeProgs/{device.gw}/Dhw"

                umsys = getattr(device, "umsys", None)
                if umsys is None:
                    umsys = getattr(device, "_umsys", None)
                if umsys is not None:
                    url += f"?umsys={umsys}"

                new_program = await api._async_get(url)
                changed = new_program != getattr(device, "dhw_time_program", None)
                device.dhw_time_program = new_program

                if changed and notify:
                    coordinator.async_update_listeners()

            except Exception as err:
                _LOGGER.warning(
                    "Unable to update Ariston DHW time program: %r",
                    err,
                )
                return err
            return None

        async def async_update_heating_programs(notify=False):
            """Update all reported heating-zone time programs."""
            if device.system_type != SystemType.GALEVO:
                return

            changed = False
            programs = getattr(device, "heating_time_programs", None)
            if not isinstance(programs, dict):
                programs = {}
                device.heating_time_programs = programs

            try:
                api = device.api
                base_url = getattr(api, "_AristonAPI__api_url", None)
                if not base_url:
                    return

                umsys = getattr(device, "umsys", None)
                if umsys is None:
                    umsys = getattr(device, "_umsys", None)
                suffix = f"?umsys={umsys}" if umsys is not None else ""

                for zone in device.zone_numbers:
                    if not zone:
                        continue

                    url = (
                        f"{base_url}remote/timeProgs/{device.gw}/"
                        f"ChZn{zone}{suffix}"
                    )
                    new_program = await api._async_get(url)
                    if new_program is None:
                        continue

                    if new_program != programs.get(zone):
                        changed = True
                    programs[zone] = new_program

                if changed and notify:
                    coordinator.async_update_listeners()

            except Exception as err:
                _LOGGER.warning(
                    "Unable to update Ariston heating time programs: %r",
                    err,
                )
                return err
            return None

        # Weekly programs: refreshed on their own adaptive schedule
        # (configurable, default 3 minutes), backing off while the cloud is
        # overloaded. HA re-reads a program right after writing it, so this
        # only has to catch edits made in the mobile app.
        program_pacer = AdaptivePacer(timedelta(seconds=entry.options.get(
            TIME_PROGRAM_SCAN_INTERVAL, DEFAULT_TIME_PROGRAM_SCAN_INTERVAL_SECONDS)))

        async def async_refresh_time_programs(notify=True):
            """Fetch DHW and heating programs once and record the outcome."""
            errors = [error for error in (
                await async_update_dhw_program(notify),
                await async_update_heating_programs(notify),
            ) if error is not None]
            now = dt_util.utcnow()
            if errors:
                program_pacer.failure(now, errors[0])
            else:
                program_pacer.success(now)

        scan_interval_seconds = entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS
        )
        coordinator = DeviceDataUpdateCoordinator(
            hass,
            device,
            scan_interval_seconds,
            COORDINATOR,
            async_update_galevo_state
            if isinstance(device, AristonGalevoDevice)
            else async_update_state_resilient,
        )
        coordinator.governor = governor

        hass.data.setdefault(DOMAIN, {}).setdefault(
            entry.unique_id, {COORDINATOR: {}, ENERGY_COORDINATOR: {}}
        )
        hass.data[DOMAIN][entry.unique_id][COORDINATOR] = coordinator
        await coordinator.async_config_entry_first_refresh()

        # Load Home Assistant-managed names for DHW schedules. Ariston's API
        # returns the weekly plan but not user-defined scenario names from the
        # mobile app, so HA stores only the local name -> schedule mapping.
        if device.system_type == SystemType.GALEVO:
            await async_refresh_time_programs(notify=False)
            await async_setup_dhw_scenario_manager(hass, entry, coordinator)
            await async_setup_heating_scenario_managers(hass, entry, coordinator)

            async def async_time_program_tick(_now):
                # Local check only; a request is made when the pacer is due.
                now = dt_util.utcnow()
                cost = 1 + len([zone for zone in device.zone_numbers if zone])
                if program_pacer.due(now) and governor.allow(now, "low", cost, "time_programs"):
                    await async_refresh_time_programs()
                publish_governor()

            entry.async_on_unload(
                async_track_time_interval(hass, async_time_program_tick, PACING_TICK)
            )

        async def async_update_bus_errors():
            if governor.allow(dt_util.utcnow(), "low", 1, "bus_errors"):
                await device.async_get_bus_errors()

        bus_errors_scan_interval_seconds = entry.options.get(
            BUS_ERRORS_SCAN_INTERVAL, DEFAULT_BUS_ERRORS_SCAN_INTERVAL_SECONDS
        )
        bus_errors_coordinator = DeviceDataUpdateCoordinator(
            hass,
            device,
            bus_errors_scan_interval_seconds,
            BUS_ERRORS_COORDINATOR,
            async_update_bus_errors,
        )
        bus_errors_coordinator.governor = governor
        hass.data[DOMAIN][entry.unique_id][BUS_ERRORS_COORDINATOR] = (
            bus_errors_coordinator
        )
        try:
            await bus_errors_coordinator.async_config_entry_first_refresh()
        except ConfigEntryNotReady:
            _LOGGER.warning(
                "Ariston bus-error list unavailable; continuing setup. "
                "Diagnostics will retry on the normal polling interval."
            )

        if device.has_metering:
            # Keep API v2 as the primary/normal energy source.
            original_async_update_energy = device.async_update_energy

            web_base_url = "https://www.ariston-net.remotethermo.com"
            web_session = aiohttp.ClientSession(
                trace_configs=[governor_trace(governor, publish_governor)],
                cookie_jar=aiohttp.CookieJar(unsafe=True),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/153 Safari/537.36"
                    )
                },
            )
            web_logged_in = False

            async def async_close_web_session():
                if not web_session.closed:
                    await web_session.close()

            entry.async_on_unload(async_close_web_session)

            def _parse_login_form(page):
                """Extract login form action and fields without hard-coding names."""
                form_match = re.search(
                    r"<form\b[^>]*action=[\"']([^\"']+)[\"'][^>]*>(.*?)</form>",
                    page,
                    flags=re.I | re.S,
                )
                if form_match:
                    action = html.unescape(form_match.group(1))
                    form_html = form_match.group(2)
                else:
                    action = "/R2/Account/Login"
                    form_html = page

                fields = {}
                username_field = None
                password_field = None

                for tag in re.findall(r"<input\b[^>]*>", form_html, flags=re.I):
                    attrs = {
                        key.lower(): html.unescape(value)
                        for key, _, value in re.findall(
                            r"""([:\w-]+)\s*=\s*([\"'])(.*?)\2""",
                            tag,
                            flags=re.I | re.S,
                        )
                    }
                    name = attrs.get("name")
                    if not name:
                        continue

                    input_type = attrs.get("type", "text").lower()
                    fields[name] = attrs.get("value", "")

                    if input_type == "password":
                        password_field = name
                    elif (
                        username_field is None
                        and input_type in ("text", "email")
                        and "verificationtoken" not in name.lower()
                    ):
                        username_field = name

                return action, fields, username_field, password_field

            async def async_web_login():
                # Authenticate using the exact ASP.NET R2 login form.
                nonlocal web_logged_in
                # Exact login used by the current R2 browser frontend.
                login_url = (
                    f"{web_base_url}/R2/Account/Login"
                    "?returnUrl=%2FR2%2FHome"
                )
                login_payload = {
                    "email": entry.data[CONF_USERNAME],
                    "password": entry.data[CONF_PASSWORD],
                    "rememberMe": False,
                    "language": "Russian",
                }
                async with web_session.post(
                    login_url,
                    json=login_payload,
                    headers={
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "Content-Type": "application/json; charset=UTF-8",
                        "Origin": web_base_url,
                        "Referer": f"{web_base_url}/R2/Account/Login",
                        "ajax-request": "json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    allow_redirects=False,
                    timeout=30,
                ) as response:
                    response_text = await response.text()
                    if response.status != 200:
                        raise RuntimeError(
                            f"R2 JSON login failed, HTTP {response.status}"
                        )
                    try:
                        login_result = json.loads(response_text)
                    except Exception as err:
                        raise RuntimeError(
                            "R2 JSON login returned invalid JSON"
                        ) from err

                    redirect_hint = response.headers.get("x-responded-json", "")
                    # Ariston escapes slashes in X-Responded-JSON:
                    # {"status":200,"headers":{"location":"\\/R2\\/Home"}}
                    # Normalize it before checking the successful location.
                    normalized_hint = redirect_hint.replace("\\/", "/")
                    header_status = None
                    header_location = None
                    try:
                        responded = json.loads(normalized_hint)
                        if isinstance(responded, dict):
                            header_status = responded.get("status")
                            headers_obj = responded.get("headers")
                            if isinstance(headers_obj, dict):
                                header_location = headers_obj.get("location")
                    except Exception:
                        pass

                    login_ok = (
                        response.status == 200
                        and (
                            (
                                header_status in (200, "200")
                                and header_location == "/R2/Home"
                            )
                            or (
                                isinstance(login_result, dict)
                                and (
                                    login_result.get("success") is True
                                    or login_result.get("status") in (200, "200")
                                )
                            )
                            or "/R2/Home" in normalized_hint
                            or "/R2/Home" in response_text.replace("\\/", "/")
                        )
                    )
                    if not login_ok:
                        raise RuntimeError(
                            "R2 JSON login rejected: no successful Home response"
                        )

                web_logged_in = True

            r2_lock = asyncio.Lock()

            async def async_update_physical_gas():
                async with r2_lock:
                    await async_update_physical_gas_locked()

            async def async_update_physical_gas_locked():
                """Fetch Ariston's native asPhysicalUnit metering values."""
                nonlocal web_logged_in

                snapshot_started = dt_util.now()

                if not web_logged_in:
                    await async_web_login()

                features = getattr(device, "features", None)
                if not isinstance(features, dict):
                    raise RuntimeError(
                        f"Unsupported device.features type: {type(features).__name__}"
                    )

                url = f"{web_base_url}/R2/PlantMetering/GetData/{device.gw}"
                payload = {
                    "features": features,
                    "hasCooling": False,
                }
                headers = {
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Ajax-Request": "json",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/json; charset=UTF-8",
                    "Referer": f"{web_base_url}/R2/PlantMetering/Index/{device.gw}",
                }

                async with web_session.post(
                    url,
                    json=payload,
                    headers=headers,
                    allow_redirects=True,
                    timeout=30,
                ) as response:
                    # Session may have expired. Re-login once.
                    if "/Account/Login" in str(response.url):
                        web_logged_in = False
                        await async_web_login()
                        async with web_session.post(
                            url,
                            json=payload,
                            headers=headers,
                            allow_redirects=True,
                            timeout=30,
                        ) as retry:
                            if retry.status != 200:
                                raise RuntimeError(
                                    f"R2 metering returned HTTP {retry.status}"
                                )
                            result = await retry.json(content_type=None)
                    else:
                        if response.status != 200:
                            raise RuntimeError(
                                f"R2 metering returned HTTP {response.status}"
                            )
                        result = await response.json(content_type=None)

                physical = (
                    result.get("data", {}).get("asPhysicalUnit")
                    if isinstance(result, dict)
                    else None
                )
                if not isinstance(physical, dict):
                    raise RuntimeError("R2 response has no data.asPhysicalUnit")

                snapshot_finished = dt_util.now()
                # Prevent a request that straddled a local calendar/two-hour
                # boundary from publishing yesterday's report as today's gas.
                # Keep the last confirmed meter state until a fresh R2 read.
                if (
                    snapshot_started.date() != snapshot_finished.date()
                    or snapshot_started.hour // 2 != snapshot_finished.hour // 2
                ):
                    _LOGGER.debug(
                        "Ariston R2 gas ignored cross-boundary snapshot: %s -> %s",
                        snapshot_started.isoformat(),
                        snapshot_finished.isoformat(),
                    )
                    return

                device.gas_physical_unit_data = physical
                device.r2_gas_snapshot_date = snapshot_finished.date()
                device.r2_gas_snapshot_time = snapshot_finished
                # Keep calendar reports to verify and date the API-v2 arrays.
                # Unverified rolling arrays must never be added to a counter.
                kwh = result.get("data", {}).get("asKwh")
                if (
                    isinstance(kwh, dict)
                    and snapshot_started.date() == snapshot_finished.date()
                    and snapshot_started.hour // 2 == snapshot_finished.hour // 2
                ):
                    device.r2_kwh_snapshot_time = snapshot_finished
                    device.r2_kwh_raw = result.get("data", {}).get("asKwhRaw")
                    device.r2_kwh_data = kwh
                    device.r2_kwh_snapshot_date = snapshot_finished.date()
                # A request crossing local midnight is not a dated snapshot.
                # Keep the preceding snapshot with its original date instead.
                _LOGGER.info("Ariston native physical gas metering updated")

            # R2 is read on every energy refresh unless it reported overload.
            r2_pacer = AdaptivePacer(timedelta(minutes=1))
            energy_burst = {"block": None, "start": None, "last": None, "arrived": False}

            def energy_fingerprint():
                physical = getattr(device, "gas_physical_unit_data", None)
                return repr((
                    physical.get("histogramData") if isinstance(physical, dict) else None,
                    getattr(device, "energy_sequences_snapshot", None),
                ))

            def schedule_energy_burst():
                """Poll every 2 minutes after a boundary until new data arrives."""
                now = dt_util.now()
                block = (now.date(), now.hour // 2)
                current = energy_fingerprint()
                if energy_burst["block"] != block:
                    energy_burst.update(block=block, start=energy_burst["last"], arrived=False)
                if energy_burst["start"] is not None and current != energy_burst["start"]:
                    energy_burst["arrived"] = True
                energy_burst["last"] = current
                block_start = now.replace(hour=(now.hour // 2) * 2, minute=0, second=0, microsecond=0)
                burst = (device.system_type == SystemType.GALEVO
                         and not energy_burst["arrived"]
                         and now - block_start < ENERGY_BURST_WINDOW)
                coordinator_ = hass.data[DOMAIN][entry.unique_id].get(ENERGY_COORDINATOR)
                if isinstance(coordinator_, DeviceDataUpdateCoordinator):
                    coordinator_.preferred_interval = (
                        ENERGY_BURST_INTERVAL if burst
                        else max(coordinator_.base_interval, ENERGY_QUIET_INTERVAL)
                        if device.system_type == SystemType.GALEVO else None
                    )

            energy_aux = {"at": None}

            async def async_update_api_energy():
                """API v2 energy; for GALEVO the settings/account part rarely."""
                if not isinstance(device, AristonGalevoDevice):
                    return await original_async_update_energy()
                now = dt_util.utcnow()
                last = energy_aux["at"]
                if (last is None or now - last >= ENERGY_AUX_REFRESH) and governor.allow(
                        now, "low", 3, "energy_aux"):
                    energy_aux["at"] = now
                    return await original_async_update_energy()
                # Only the consumption sequences (one request).
                return await super(AristonGalevoDevice, device).async_update_energy()

            def energy_priority():
                coordinator_ = hass.data[DOMAIN][entry.unique_id].get(ENERGY_COORDINATOR)
                burst = getattr(coordinator_, "preferred_interval", None) == ENERGY_BURST_INTERVAL
                return "high" if burst or energy_burst["block"] is None else "low"

            async def async_update_energy_with_web_metering():
                """Refresh normal API energy plus optional native R2 m³ data."""
                if not governor.allow(dt_util.utcnow(), energy_priority(), 2, "energy"):
                    schedule_energy_burst()
                    return None  # keep the last energy data within the budget
                api_started = dt_util.now()
                result = await async_update_api_energy()
                api_finished = dt_util.now()
                if (api_started.date() == api_finished.date()
                        and api_started.hour // 2 == api_finished.hour // 2):
                    device.energy_sequences_snapshot = deepcopy(device.consumptions_sequences)
                    device.energy_sequences_date = api_finished.date()
                    device.energy_sequences_time = api_finished


                if r2_pacer.due(dt_util.utcnow()):
                    try:
                        await async_update_physical_gas()
                        r2_pacer.success(dt_util.utcnow())
                    except Exception as err:
                        # Important: a web-login problem must never break normal Ariston
                        # sensors or the API-v2 energy coordinator.
                        r2_pacer.failure(dt_util.utcnow(), err)
                        _LOGGER.warning(
                            "Ariston native R2 gas metering unavailable (next try in %s): %r",
                            r2_pacer.interval, err,
                        )

                schedule_energy_burst()
                return result

            energy_interval_minutes = entry.options.get(
                ENERGY_SCAN_INTERVAL, DEFAULT_ENERGY_SCAN_INTERVAL_MINUTES
            )
            if device.system_type == SystemType.GALEVO:
                # GALEVO p=1 carries finished two-hour periods, but API v2
                # can publish a new finished period before the configured
                # hourly energy poll. Read every <=10 minutes to detect it
                # promptly; R2 shares this refresh and its own error backoff.
                # Do not accelerate normal/non-GALEVO energy requests.
                energy_interval_minutes = max(2, min(energy_interval_minutes, 10))
            energy_coordinator = DeviceDataUpdateCoordinator(
                hass,
                device,
                energy_interval_minutes * 60,
                ENERGY_COORDINATOR,
                async_update_energy_with_web_metering,
            )
            hass.data[DOMAIN][entry.unique_id][ENERGY_COORDINATOR] = energy_coordinator
            energy_coordinator.governor = governor
            try:
                await energy_coordinator.async_config_entry_first_refresh()
            except ConfigEntryNotReady:
                _LOGGER.warning(
                    "Ariston energy reports unavailable at startup; "
                    "continuing with restored meters and scheduled retries."
                )

            # R2 reports change once per two-hour interval. They are read
            # together with API v2 on every energy refresh (<=10 minutes for
            # GALEVO), which also keeps both snapshots in the same interval.

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Remove ONLY our obsolete custom JSON-export button. Home Assistant's
        # built-in /diagnostics API now provides a real browser download.
        # Never delete devices, other buttons, user-disabled entities, or IDs
        # used by Energy / automation.
        registry = er.async_get(hass)
        export_uid = f"{device.gateway}-Ariston export R2 diagnostics"
        export_entity_id = registry.async_get_entity_id("button", DOMAIN, export_uid)
        if export_entity_id:
            old_button = registry.async_get(export_entity_id)
            if old_button is not None and old_button.config_entry_id == entry.entry_id:
                registry.async_remove(export_entity_id)
                _LOGGER.info("Ariston obsolete R2 JSON button removed from registry")

        entry.async_on_unload(entry.add_update_listener(update_listener))

        if device.system_type == SystemType.GALEVO:

            async def async_set_item_by_id_service(service_call):
                """Create a vacation on the target device."""
                device_id = service_call.data.get(ATTR_DEVICE_ID)
                item_id = service_call.data.get(ATTR_ITEM_ID)
                zone = service_call.data.get(ATTR_ZONE)
                value = service_call.data.get(ATTR_VALUE)

                device_registry = dr.async_get(hass)
                device = device_registry.devices[device_id]

                entry = hass.config_entries.async_get_entry(
                    next(iter(device.config_entries))
                )
                coordinator: DeviceDataUpdateCoordinator = hass.data[DOMAIN][
                    entry.unique_id
                ][COORDINATOR]
                await coordinator.device.async_set_item_by_id(item_id, value, zone)

            hass.services.async_register(
                DOMAIN,
                SERVICE_SET_ITEM_BY_ID,
                async_set_item_by_id_service,
                schema=SET_ITEM_BY_ID_SCHEMA,
            )
    except (ConfigEntryAuthFailed, ConfigEntryNotReady):
        raise
    except Exception as error:
        _LOGGER.exception("")
        raise ConfigEntryNotReady from error

    return True


async def async_get_governor(hass: HomeAssistant, entry: ConfigEntry):
    """Account governor that survives reloads (kept in hass.data and a Store)."""
    governors = hass.data.setdefault(GOVERNORS, {})
    holder = governors.get(entry.entry_id)
    if holder is None:
        store = Store(hass, 1, f"ariston_cloud_governor.{entry.entry_id}")
        saved = await store.async_load()
        governor = CloudGovernor(dt_util.utcnow(), saved)
        state = {"saved_at": None}

        def publish(persist: bool = False) -> None:
            now = dt_util.utcnow()
            status = governor.status(now)
            hass.states.async_set(
                GOVERNOR_STATUS_ENTITY,
                status.pop("state"),
                {**status, "friendly_name": "Ariston: облако", "icon": "mdi:cloud-lock-outline"},
            )
            last = state["saved_at"]
            if persist or last is None or now - last >= timedelta(minutes=30):
                state["saved_at"] = now
                store.async_delay_save(governor.to_store, 5)

        holder = governors[entry.entry_id] = (governor, publish)
    return holder


def govern_api(api, governor: CloudGovernor, publish) -> None:
    """Route every request of this AristonAPI instance through the governor."""
    original = api._AristonAPI__async_request

    async def governed(method, path, params=None, body=None, is_retry=False):
        governor.before_request(dt_util.utcnow(), category_of(str(path)))
        try:
            result = await original(method, path, params, body, is_retry)
        except ConnectionException as error:
            if is_block(error):
                governor.after_block(dt_util.utcnow())
                _LOGGER.warning(
                    "Ariston blocked the account (%s requests in the last hour); "
                    "all cloud requests paused until %s, budget now %s/h",
                    governor.status(dt_util.utcnow())["requests_last_hour"],
                    governor.paused_until, governor.budget,
                )
                publish(True)
            raise
        governor.after_success(dt_util.utcnow())
        return result

    api._AristonAPI__async_request = governed


def governor_trace(governor: CloudGovernor, publish) -> aiohttp.TraceConfig:
    """Count R2 web requests of the same account and honour its blocks."""
    trace = aiohttp.TraceConfig()

    async def on_start(session, context, params):
        governor.before_request(dt_util.utcnow(), "r2")

    async def on_end(session, context, params):
        if params.response.status == 429:
            governor.after_block(dt_util.utcnow())
            publish(True)
        elif params.response.status < 400:
            governor.after_success(dt_util.utcnow())

    trace.on_request_start.append(on_start)
    trace.on_request_end.append(on_end)
    return trace


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.unique_id)

    return unload_ok

