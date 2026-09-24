"""Home Assistant-managed heating scenario names and schedules."""

from __future__ import annotations

from copy import deepcopy
import logging

from ariston.const import ZoneMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DHW_SCENARIOS, DOMAIN
from .coordinator import DeviceDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

HEATING_SCENARIO_MANAGERS = "heating_scenario_managers"
_STORAGE_VERSION = 1

_HEATING_SCENARIO_LABELS = {
    "ru": {
        "Всегда включен": "Всегда включен",
        "Семья дома": "Семья дома",
        "Полуденный": "Полуденный",
        "Без обеденного перерыва": "Без обеденного перерыва",
    },
    "en": {
        "Всегда включен": "Always on",
        "Семья дома": "Family at home",
        "Полуденный": "Midday",
        "Без обеденного перерыва": "No lunch break",
    },
}

# Ariston's mobile app uses the same logical Comfort/Economy weekly-plan
# templates for heating zones.  User-defined names are learned directly in HA.
HEATING_STANDARD_SCENARIOS = {
    "Всегда включен": DHW_SCENARIOS["Всегда Comfort"],
    "Семья дома": DHW_SCENARIOS["Семья"],
    "Полуденный": DHW_SCENARIOS["Дома днём"],
    "Без обеденного перерыва": DHW_SCENARIOS["Без обеда"],
}


def _normalize_temp_marker(value):
    """Normalize Ariston 0/1 markers without changing other temperature values."""
    if value in (0, 0.0, "0"):
        return 0
    if value in (1, 1.0, "1"):
        return 1
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _plan_signature(plan):
    """Return a semantic per-day signature for comparing heating weekly plans."""
    if not isinstance(plan, dict):
        return None

    plans = plan.get("plans", [])
    if not isinstance(plans, list) or not plans:
        return None

    default_temp = _normalize_temp_marker(plan.get("defaultTemp", 0))
    per_day = {}

    for day_plan in plans:
        if not isinstance(day_plan, dict):
            continue

        raw_slices = day_plan.get("slices", [])
        events = [(0, default_temp)]
        for item in raw_slices:
            if not isinstance(item, dict):
                continue
            try:
                minute = int(item.get("from", 0))
            except (TypeError, ValueError):
                minute = 0
            events.append(
                (minute, _normalize_temp_marker(item.get("temp", default_temp)))
            )

        # Server/app versions may regroup identical days differently or emit
        # redundant slices. Compare effective day timelines instead of raw JSON
        # grouping so the same scenario keeps its name after a cloud refresh.
        by_minute = {}
        for minute, temp in sorted(events, key=lambda item: item[0]):
            by_minute[minute] = temp

        compact = []
        for minute, temp in sorted(by_minute.items()):
            if compact and compact[-1][1] == temp:
                continue
            compact.append((minute, temp))

        day_signature = tuple(compact)
        for day in day_plan.get("days", []):
            try:
                per_day[int(day)] = day_signature
            except (TypeError, ValueError):
                continue

    if not per_day:
        return None

    # Include all seven days explicitly. Missing days use the default state.
    default_day = ((0, default_temp),)
    return tuple((day, per_day.get(day, default_day)) for day in range(7))


def extract_heating_plan(program_data, zone: int):
    """Extract the ChZn weekly-plan object from any known Ariston response shape."""
    if isinstance(program_data, list):
        for item in program_data:
            nested = extract_heating_plan(item, zone)
            if nested is not None:
                return nested
        return None

    if not isinstance(program_data, dict):
        return None

    if isinstance(program_data.get("plans"), list):
        return program_data

    weekly_plan = program_data.get("weeklyPlan")
    if isinstance(weekly_plan, dict) and isinstance(weekly_plan.get("plans"), list):
        return weekly_plan

    for key in (
        f"ChZn{zone}",
        f"chZn{zone}",
        f"CHZn{zone}",
        f"Zone{zone}",
        f"zone{zone}",
    ):
        value = program_data.get(key)
        nested = extract_heating_plan(value, zone)
        if nested is not None:
            return nested

    # Ariston has used both dict wrappers and list-based timeProgs responses
    # across its API generations. Recurse through either so naming/saving does
    # not depend on one exact JSON envelope.
    for value in program_data.values():
        if isinstance(value, (dict, list)):
            nested = extract_heating_plan(value, zone)
            if nested is not None:
                return nested

    return None


def _selected_schedule_temp(plan):
    """Return the active schedule temp marker/value for the current local time."""
    if not isinstance(plan, dict):
        return None

    plans = plan.get("plans", [])
    if not plans:
        return None

    now = dt_util.now()

    # Ariston: Sunday=0, Monday=1, ... Saturday=6.
    ariston_day = (now.weekday() + 1) % 7
    minutes_now = now.hour * 60 + now.minute
    selected_temp = plan.get("defaultTemp", 0)

    for day_plan in plans:
        if ariston_day not in day_plan.get("days", []):
            continue

        for time_slice in sorted(
            day_plan.get("slices", []),
            key=lambda item: item.get("from", 0),
        ):
            if time_slice.get("from", 0) <= minutes_now:
                selected_temp = time_slice.get("temp", selected_temp)
            else:
                break
        break

    return selected_temp


class HeatingScenarioManager:
    """Persist and apply named heating schedules for one Ariston zone."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: DeviceDataUpdateCoordinator,
        zone: int,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.device = coordinator.device
        self.zone = zone
        self.store = Store(
            hass,
            _STORAGE_VERSION,
            f"{DOMAIN}.heating_scenarios.{entry.entry_id}.zone_{zone}",
        )
        self.custom_scenarios: dict[str, dict] = {}
        self.draft_name = ""

    async def async_load(self) -> None:
        """Load HA-defined scenario names and schedules."""
        data = await self.store.async_load() or {}
        scenarios = data.get("scenarios", {})
        if isinstance(scenarios, dict):
            self.custom_scenarios = {
                str(name): scenario
                for name, scenario in scenarios.items()
                if isinstance(name, str) and isinstance(scenario, dict)
            }

        current = self.current_name
        if current:
            self.draft_name = current

    @property
    def raw_program(self):
        """Return the raw API response last read for this heating zone."""
        programs = getattr(self.device, "heating_time_programs", {}) or {}
        return programs.get(self.zone)

    @property
    def current_plan(self):
        """Return the normalized weekly-plan object for this zone."""
        return extract_heating_plan(self.raw_program, self.zone)

    @property
    def _language(self) -> str:
        """Return the supported backend language used for scenario labels."""
        language = (self.hass.config.language or "en").lower()
        return "ru" if language.startswith("ru") else "en"

    def _localized_builtin_name(self, canonical_name: str) -> str:
        """Return the built-in heating scenario label for the active language."""
        return _HEATING_SCENARIO_LABELS[self._language].get(
            canonical_name, canonical_name
        )

    def _canonical_builtin_name(self, displayed_name: str) -> str | None:
        """Resolve either localized or canonical built-in scenario name."""
        if displayed_name in HEATING_STANDARD_SCENARIOS:
            return displayed_name
        for canonical_name, label in _HEATING_SCENARIO_LABELS[
            self._language
        ].items():
            if displayed_name == label:
                return canonical_name
        return None

    @property
    def options(self) -> list[str]:
        """Return localized built-in and HA-defined heating scenario names."""
        builtin_labels = [
            self._localized_builtin_name(name)
            for name in HEATING_STANDARD_SCENARIOS
        ]
        reserved = set(HEATING_STANDARD_SCENARIOS) | set(builtin_labels)
        return builtin_labels + [
            name for name in self.custom_scenarios if name not in reserved
        ]

    @property
    def current_name(self) -> str | None:
        """Return the name whose stored schedule matches the current cloud plan."""
        current_signature = _plan_signature(self.current_plan)
        if current_signature is None:
            return None

        for name, scenario in HEATING_STANDARD_SCENARIOS.items():
            if current_signature == _plan_signature(scenario):
                return self._localized_builtin_name(name)

        for name, scenario in self.custom_scenarios.items():
            if current_signature == _plan_signature(scenario):
                return name

        return None

    @property
    def active_program(self) -> str | None:
        """Return Off, Manual, Comfort, Economy, or the active numeric value."""
        zone_mode = self.device.get_zone_mode(self.zone)
        if zone_mode == ZoneMode.OFF:
            return "Off"
        if not self.device.is_zone_in_time_program_mode(self.zone):
            return "Manual"

        selected_temp = _selected_schedule_temp(self.current_plan)
        if selected_temp is None:
            return None

        # Most GALEVO schedules use 0/1 as Economy/Comfort selectors.
        if selected_temp in (0, 0.0):
            return "Economy"
        if selected_temp in (1, 1.0):
            return "Comfort"

        # Some variants may expose the actual scheduled temperature.
        try:
            value = float(selected_temp)
            comfort = self.device.get_comfort_temp_value(self.zone)
            economy = self.device.get_zone_economy_temp_value(self.zone)

            if comfort is not None and abs(value - float(comfort)) < 0.01:
                return "Comfort"
            if economy is not None and abs(value - float(economy)) < 0.01:
                return "Economy"

            return f"{value:g} °C"
        except (TypeError, ValueError):
            return str(selected_temp)

    @property
    def active_target_temperature(self):
        """Return the effective heating target for the current schedule slot."""
        if not self.device.is_zone_in_time_program_mode(self.zone):
            return self.device.get_target_temp_value(self.zone)

        selected_temp = _selected_schedule_temp(self.current_plan)
        if selected_temp is None:
            return self.device.get_target_temp_value(self.zone)

        if selected_temp in (0, 0.0):
            return self.device.get_zone_economy_temp_value(self.zone)
        if selected_temp in (1, 1.0):
            return self.device.get_comfort_temp_value(self.zone)

        try:
            return float(selected_temp)
        except (TypeError, ValueError):
            return self.device.get_target_temp_value(self.zone)

    def _build_url(self) -> str:
        api = self.device.api
        base_url = getattr(api, "_AristonAPI__api_url", None)
        if not base_url:
            raise RuntimeError("Ariston API base URL unavailable")

        umsys = getattr(self.device, "umsys", None)
        if umsys is None:
            umsys = getattr(self.device, "_umsys", None)
        suffix = f"?umsys={umsys}" if umsys is not None else ""

        return (
            f"{base_url}remote/timeProgs/{self.device.gw}/"
            f"ChZn{self.zone}{suffix}"
        )

    async def async_save_current(self, name: str) -> None:
        """Save the current cloud heating schedule under a user-supplied name."""
        name = (name or "").strip()
        if not name:
            raise ValueError("Heating scenario name must not be empty")
        if self._canonical_builtin_name(name) is not None:
            raise ValueError(
                f"'{name}' is a built-in heating scenario name; choose another name"
            )

        current = self.current_plan
        if not isinstance(current, dict) or not current.get("plans"):
            raise RuntimeError(
                f"Current Ariston heating schedule for zone {self.zone} is unavailable"
            )

        # Store only the schedule object. No credentials, gateway id or account
        # data are persisted in this Home Assistant mapping.
        self.custom_scenarios[name] = deepcopy(current)
        self.draft_name = name
        await self.store.async_save({"scenarios": self.custom_scenarios})

        _LOGGER.info(
            "Saved current Ariston heating zone %s schedule as '%s'",
            self.zone,
            name,
        )
        self.coordinator.async_set_updated_data(self.coordinator.data)

    async def async_apply(self, name: str) -> None:
        """Apply a standard or HA-defined heating scenario through API v2."""
        canonical_name = self._canonical_builtin_name(name)
        scenario = (
            HEATING_STANDARD_SCENARIOS.get(canonical_name)
            if canonical_name is not None
            else None
        )
        if scenario is None:
            scenario = self.custom_scenarios.get(name)
        if scenario is None:
            raise ValueError(f"Unknown heating scenario: {name}")

        # For standard scenarios preserve metadata returned by this installation
        # and replace only the actual weekly slices.  Custom scenarios already
        # contain the exact payload previously read from this plant.
        if canonical_name is not None:
            current = self.current_plan or {}
            weekly_plan = deepcopy(current)
            weekly_plan["plans"] = deepcopy(scenario["plans"])
            weekly_plan.setdefault("ext", False)
            weekly_plan.setdefault("allowedTemp", [0, 1])
            weekly_plan.setdefault("defaultTemp", 0)
            weekly_plan.setdefault("baseTemp", 0)
            weekly_plan.setdefault("tick", 0)
            weekly_plan.setdefault("maxSwitches", 0)
            weekly_plan.setdefault("pilot", None)
        else:
            weekly_plan = deepcopy(scenario)

        url = self._build_url()
        await self.device.api._async_post(url, weekly_plan)

        new_program = await self.device.api._async_get(url)
        new_plan = extract_heating_plan(new_program, self.zone)
        if new_plan is None:
            raise RuntimeError(
                f"Ariston did not return heating zone {self.zone} program after write"
            )

        programs = getattr(self.device, "heating_time_programs", None)
        if not isinstance(programs, dict):
            programs = {}
            self.device.heating_time_programs = programs
        programs[self.zone] = new_program

        # Selecting a heating scenario should also put the zone into time-program
        # mode, matching what the mobile app does when a schedule is applied.
        if (
            self.device.is_zone_mode_options_contains_time_program(self.zone)
            and not self.device.is_zone_in_time_program_mode(self.zone)
        ):
            await self.device.async_set_zone_mode(ZoneMode.TIME_PROGRAM, self.zone)

        # Mirror the selected scenario in the text field for both standard and
        # HA-defined scenarios, exactly like the selector state.
        self.draft_name = name
        await self.coordinator.async_request_refresh()


async def async_setup_heating_scenario_managers(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: DeviceDataUpdateCoordinator,
) -> dict[int, HeatingScenarioManager]:
    """Create and load a manager for every reported heating zone."""
    entry_data = hass.data[DOMAIN][entry.unique_id]
    managers = entry_data.get(HEATING_SCENARIO_MANAGERS)
    if isinstance(managers, dict):
        return managers

    managers = {}
    for zone in coordinator.device.zone_numbers:
        if not zone:
            continue
        manager = HeatingScenarioManager(hass, entry, coordinator, zone)
        await manager.async_load()
        managers[zone] = manager

    entry_data[HEATING_SCENARIO_MANAGERS] = managers
    return managers
