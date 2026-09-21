"""The Ariston integration."""

from __future__ import annotations

import json

from datetime import timedelta

import html
import logging
import re
from urllib.parse import urljoin

import aiohttp

import voluptuous as vol

from ariston import Ariston, DeviceAttribute, SystemType
from ariston.const import ARISTON_API_URL, ARISTON_USER_AGENT
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
from homeassistant.util.unit_system import METRIC_SYSTEM

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
)
from .coordinator import DeviceDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]

SERVICE_SET_ITEM_BY_ID = "set_item_by_id"
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

        reponse = await ariston.async_connect(
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            api_url_setting,
            api_user_agent,
        )
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
                    coordinator.async_set_updated_data(coordinator.data)

            except Exception as err:
                _LOGGER.warning(
                    "Unable to update Ariston DHW time program: %r",
                    err,
                )

        async def async_update_state_with_dhw_program():
            """Update Ariston state and DHW time program."""
            result = await original_async_update_state()
            await async_update_dhw_program()
            return result

        scan_interval_seconds = entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS
        )
        coordinator = DeviceDataUpdateCoordinator(
            hass,
            device,
            scan_interval_seconds,
            COORDINATOR,
            async_update_state_with_dhw_program,
        )

        hass.data.setdefault(DOMAIN, {}).setdefault(
            entry.unique_id, {COORDINATOR: {}, ENERGY_COORDINATOR: {}}
        )
        hass.data[DOMAIN][entry.unique_id][COORDINATOR] = coordinator
        await coordinator.async_config_entry_first_refresh()

        # Refresh only the lightweight DHW schedule every 30 seconds.
        if device.system_type == SystemType.GALEVO:
            async def async_dhw_program_timer(_now):
                await async_update_dhw_program(notify=True)

            entry.async_on_unload(
                async_track_time_interval(
                    hass,
                    async_dhw_program_timer,
                    timedelta(seconds=30),
                )
            )

        bus_errors_scan_interval_seconds = entry.options.get(
            BUS_ERRORS_SCAN_INTERVAL, DEFAULT_BUS_ERRORS_SCAN_INTERVAL_SECONDS
        )
        bus_errors_coordinator = DeviceDataUpdateCoordinator(
            hass,
            device,
            bus_errors_scan_interval_seconds,
            BUS_ERRORS_COORDINATOR,
            device.async_get_bus_errors,
        )
        hass.data[DOMAIN][entry.unique_id][BUS_ERRORS_COORDINATOR] = (
            bus_errors_coordinator
        )
        await bus_errors_coordinator.async_config_entry_first_refresh()

        if device.has_metering:
            # Keep API v2 as the primary/normal energy source.
            original_async_update_energy = device.async_update_energy

            web_base_url = "https://www.ariston-net.remotethermo.com"
            web_session = aiohttp.ClientSession(
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

            async def async_update_physical_gas():
                """Fetch Ariston's native asPhysicalUnit metering values."""
                nonlocal web_logged_in

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

                device.gas_physical_unit_data = physical
                _LOGGER.info("Ariston native physical gas metering updated")

            async def async_update_energy_with_web_metering():
                """Refresh normal API energy plus optional native R2 m³ data."""
                result = await original_async_update_energy()

                try:
                    await async_update_physical_gas()
                except Exception as err:
                    # Important: a web-login problem must never break normal Ariston
                    # sensors or the API-v2 energy coordinator.
                    _LOGGER.warning(
                        "Ariston native R2 gas metering unavailable: %r",
                        err,
                    )

                return result

            energy_interval_minutes = entry.options.get(
                ENERGY_SCAN_INTERVAL, DEFAULT_ENERGY_SCAN_INTERVAL_MINUTES
            )
            energy_coordinator = DeviceDataUpdateCoordinator(
                hass,
                device,
                energy_interval_minutes * 60,
                ENERGY_COORDINATOR,
                async_update_energy_with_web_metering,
            )
            hass.data[DOMAIN][entry.unique_id][ENERGY_COORDINATOR] = energy_coordinator
            await energy_coordinator.async_config_entry_first_refresh()

            # Native R2 gas metering is lightweight and useful for Energy.
            # Refresh it independently every 2 minutes without increasing the
            # normal API-v2 energy polling rate.
            async def async_r2_gas_metering_timer(_now):
                try:
                    await async_update_physical_gas()
                    # Notify Energy sensor listeners immediately with the fresh R2 data.
                    energy_coordinator.async_set_updated_data(energy_coordinator.data)
                except Exception as err:
                    _LOGGER.warning(
                        "Ariston native R2 gas metering timer unavailable: %r",
                        err,
                    )

            entry.async_on_unload(
                async_track_time_interval(
                    hass,
                    async_r2_gas_metering_timer,
                    timedelta(minutes=2),
                )
            )

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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
    except ConfigEntryAuthFailed:
        raise
    except Exception as error:
        _LOGGER.exception("")
        raise ConfigEntryNotReady from error

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.unique_id)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow manual removal of stale empty Ariston devices."""
    entity_registry = er.async_get(hass)

    # Never allow deletion while any entity is still attached to this device.
    if er.async_entries_for_device(
        entity_registry,
        device_entry.id,
        include_disabled_entities=True,
    ):
        return False

    # The current device registry is single-config-entry. Only handle devices
    # actually owned by this Ariston config entry.
    if device_entry.config_entry_id != config_entry.entry_id:
        return False

    return True
