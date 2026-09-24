"""Home Assistant-managed DHW scenario names and schedules."""

from __future__ import annotations

from copy import deepcopy
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DHW_SCENARIOS, DOMAIN
from .coordinator import DeviceDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

DHW_SCENARIO_MANAGER = "dhw_scenario_manager"
_STORAGE_VERSION = 1

_DHW_SCENARIO_LABELS = {
    "ru": {
        "Всегда Comfort": "Всегда Comfort",
        "Семья": "Семья",
        "Без обеда": "Без обеда",
        "Дома днём": "Дома днём",
    },
    "en": {
        "Всегда Comfort": "Always Comfort",
        "Семья": "Family",
        "Без обеда": "No lunch",
        "Дома днём": "Home during the day",
    },
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
    """Return a semantic per-day signature for comparing DHW weekly plans."""
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


class DhwScenarioManager:
    """Persist user-defined names for schedules that Ariston returns unnamed."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: DeviceDataUpdateCoordinator,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.device = coordinator.device
        self.store = Store(
            hass,
            _STORAGE_VERSION,
            f"{DOMAIN}.dhw_scenarios.{entry.entry_id}",
        )
        self.custom_scenarios: dict[str, dict] = {}
        self.draft_name = ""
        self._selected_name: str | None = None

    async def async_load(self) -> None:
        """Load user-defined DHW scenarios from Home Assistant storage."""
        data = await self.store.async_load() or {}
        scenarios = data.get("scenarios", {})
        if isinstance(scenarios, dict):
            self.custom_scenarios = {
                str(name): scenario
                for name, scenario in scenarios.items()
                if isinstance(name, str) and isinstance(scenario, dict)
            }
        self._selected_name = data.get("selected")

        current = self.current_name
        if current:
            self.draft_name = current

    @property
    def _language(self) -> str:
        """Return the supported backend language used for scenario labels."""
        language = (self.hass.config.language or "en").lower()
        return "ru" if language.startswith("ru") else "en"

    def _localized_builtin_name(self, canonical_name: str) -> str:
        """Return the built-in scenario label for the active HA language."""
        return _DHW_SCENARIO_LABELS[self._language].get(
            canonical_name, canonical_name
        )

    def _canonical_builtin_name(self, displayed_name: str) -> str | None:
        """Resolve either localized or canonical built-in scenario name."""
        if displayed_name in DHW_SCENARIOS:
            return displayed_name
        for canonical_name, label in _DHW_SCENARIO_LABELS[self._language].items():
            if displayed_name == label:
                return canonical_name
        return None

    @property
    def options(self) -> list[str]:
        """Return localized built-in and user-defined scenario names."""
        builtin_labels = [
            self._localized_builtin_name(name) for name in DHW_SCENARIOS
        ]
        reserved = set(DHW_SCENARIOS) | set(builtin_labels)
        return builtin_labels + [
            name for name in self.custom_scenarios if name not in reserved
        ]

    @property
    def current_name(self) -> str | None:
        """Return the name whose stored schedule matches the current Ariston plan."""
        program_data = getattr(self.device, "dhw_time_program", None) or {}
        current = program_data.get("Dhw", {})
        current_signature = _plan_signature(current)
        if current_signature is None:
            return None

        selected = self._selected_name
        selected_builtin = self._canonical_builtin_name(selected) if selected else None
        selected_plan = (
            DHW_SCENARIOS.get(selected_builtin)
            if selected_builtin is not None
            else self.custom_scenarios.get(selected)
        )
        if selected_plan is not None and current_signature == _plan_signature(selected_plan):
            return self._localized_builtin_name(selected_builtin) if selected_builtin else selected

        # Custom plans can share the same schedule as a built-in. Prefer the
        # user's saved name when no explicit selection is known.
        for name, scenario in self.custom_scenarios.items():
            if current_signature == _plan_signature(scenario):
                return name

        for name, scenario in DHW_SCENARIOS.items():
            if current_signature == _plan_signature(scenario):
                return self._localized_builtin_name(name)

        return None

    async def async_save_current(self, name: str) -> None:
        """Save the current cloud schedule under a user supplied HA name."""
        name = (name or "").strip()
        if not name:
            raise ValueError("DHW scenario name must not be empty")
        if self._canonical_builtin_name(name) is not None:
            raise ValueError(
                f"'{name}' is a built-in DHW scenario name; choose another name"
            )

        program_data = getattr(self.device, "dhw_time_program", None) or {}
        current = program_data.get("Dhw")
        if not isinstance(current, dict) or not current.get("plans"):
            raise RuntimeError("Current Ariston DHW schedule is unavailable")

        # Keep only the schedule payload. No credentials, gateway id or account data
        # are written to HA storage.
        saved = {
            "ext": current.get("ext", False),
            "plans": deepcopy(current.get("plans", [])),
            "allowedTemp": deepcopy(current.get("allowedTemp", [0, 1])),
            "defaultTemp": current.get("defaultTemp", 0),
            "baseTemp": current.get("baseTemp", 0),
            "tick": current.get("tick", 0),
            "maxSwitches": current.get("maxSwitches", 0),
            "pilot": deepcopy(current.get("pilot")),
        }

        self.custom_scenarios[name] = saved
        self._selected_name = name
        self.draft_name = name
        await self.store.async_save({"scenarios": self.custom_scenarios, "selected": name})

        _LOGGER.info("Saved current Ariston DHW schedule as '%s'", name)
        self.coordinator.async_set_updated_data(self.coordinator.data)

    async def async_apply(self, name: str) -> None:
        """Apply a built-in or HA-defined DHW scenario through Ariston API v2."""
        canonical_name = self._canonical_builtin_name(name)
        scenario = (
            DHW_SCENARIOS.get(canonical_name)
            if canonical_name is not None
            else None
        )
        if scenario is None:
            scenario = self.custom_scenarios.get(name)
        if scenario is None:
            raise ValueError(f"Unknown DHW scenario: {name}")

        api = self.device.api
        base_url = getattr(api, "_AristonAPI__api_url", None)
        if not base_url:
            raise RuntimeError("Ariston API base URL unavailable")

        umsys = getattr(self.device, "umsys", None)
        if umsys is None:
            umsys = getattr(self.device, "_umsys", None)
        suffix = f"?umsys={umsys}" if umsys is not None else ""
        url = f"{base_url}remote/timeProgs/{self.device.gw}/Dhw{suffix}"

        weekly_plan = {
            "ext": scenario.get("ext", False),
            "plans": deepcopy(scenario["plans"]),
            "allowedTemp": deepcopy(scenario.get("allowedTemp", [0, 1])),
            "defaultTemp": scenario.get("defaultTemp", 0),
            "baseTemp": scenario.get("baseTemp", 0),
            "tick": scenario.get("tick", 0),
            "maxSwitches": scenario.get("maxSwitches", 0),
            "pilot": deepcopy(scenario.get("pilot")),
        }

        await api._async_post(url, weekly_plan)
        new_program = await api._async_get(url)
        if not isinstance(new_program, dict) or "Dhw" not in new_program:
            raise RuntimeError("Ariston did not return the DHW program after write")

        self.device.dhw_time_program = new_program
        self._selected_name = canonical_name or name
        self.draft_name = name
        await self.store.async_save({
            "scenarios": self.custom_scenarios,
            "selected": self._selected_name,
        })
        self.coordinator.async_set_updated_data(self.coordinator.data)


async def async_setup_dhw_scenario_manager(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: DeviceDataUpdateCoordinator,
) -> DhwScenarioManager:
    """Create and load the manager once for this config entry."""
    entry_data = hass.data[DOMAIN][entry.unique_id]
    manager = entry_data.get(DHW_SCENARIO_MANAGER)
    if manager is not None:
        return manager

    manager = DhwScenarioManager(hass, entry, coordinator)
    await manager.async_load()
    entry_data[DHW_SCENARIO_MANAGER] = manager
    return manager
