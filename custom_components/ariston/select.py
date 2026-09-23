"""Support for Ariston sensors."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .const import ARISTON_SELECT_TYPES, DOMAIN, AristonSelectEntityDescription
from .coordinator import DeviceDataUpdateCoordinator
from .dhw_scenarios import DHW_SCENARIO_MANAGER
from .heating_scenarios import HEATING_SCENARIO_MANAGERS
from .entity import AristonEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(kw_only=True, frozen=True)
class AristonHeatingSelectEntityDescription(SelectEntityDescription):
    """Select description compatible with AristonEntity."""

    extra_states: list | None = None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the Ariston binary sensors from config entry."""
    ariston_select: list[AristonSelect] = []
    dhw_scenario_manager = hass.data[DOMAIN][entry.unique_id].get(
        DHW_SCENARIO_MANAGER
    )

    for description in ARISTON_SELECT_TYPES:
        coordinator: DeviceDataUpdateCoordinator = hass.data[DOMAIN][entry.unique_id][
            description.coordinator
        ]
        if (
            coordinator
            and coordinator.device
            and coordinator.device.are_device_features_available(
                description.device_features,
                description.system_types,
                description.whe_types,
            )
        ):
            ariston_select.append(
                AristonSelect(
                    coordinator,
                    description,
                    dhw_scenario_manager,
                )
            )

    heating_managers = hass.data[DOMAIN][entry.unique_id].get(
        HEATING_SCENARIO_MANAGERS, {}
    )
    for zone, manager in heating_managers.items():
        coordinator: DeviceDataUpdateCoordinator = hass.data[DOMAIN][entry.unique_id][
            "coordinator"
        ]
        ariston_select.append(
            AristonHeatingScenarioSelect(coordinator, manager, zone)
        )

    async_add_entities(ariston_select)


class AristonHeatingScenarioSelect(AristonEntity, SelectEntity):
    """Per-zone heating scenario selector."""

    def __init__(self, coordinator, manager, zone: int) -> None:
        description = AristonHeatingSelectEntityDescription(
            key=f"HeatingScenarioZone{zone}",
            name=f"Ariston heating scenario zone {zone}",
            icon="mdi:radiator",
            entity_category=EntityCategory.CONFIG,
        )
        super().__init__(coordinator, description, zone)
        self.manager = manager

    @property
    def current_option(self):
        """Return the schedule name matching the current heating program."""
        return self.manager.current_name

    @property
    def options(self):
        """Return standard and Home Assistant-defined heating scenarios."""
        return self.manager.options

    async def async_select_option(self, option: str):
        """Apply the selected heating scenario."""
        await self.manager.async_apply(option)
        self.async_write_ha_state()


class AristonSelect(AristonEntity, SelectEntity):
    """Base class for specific ariston binary sensors."""

    def __init__(
        self,
        coordinator: DeviceDataUpdateCoordinator,
        description: AristonSelectEntityDescription,
        dhw_scenario_manager=None,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator, description)
        self._dhw_scenario_manager = dhw_scenario_manager

    @property
    def current_option(self):
        """Return current selected option."""
        if (
            self.entity_description.key == "DhwScenario"
            and self._dhw_scenario_manager is not None
        ):
            return self._dhw_scenario_manager.current_name
        return self.entity_description.get_current_option(self)

    @property
    def options(self):
        """Return options."""
        if (
            self.entity_description.key == "DhwScenario"
            and self._dhw_scenario_manager is not None
        ):
            return self._dhw_scenario_manager.options
        return self.entity_description.get_options(self)

    async def async_select_option(self, option: str):
        """Change the selected option."""
        if (
            self.entity_description.key == "DhwScenario"
            and self._dhw_scenario_manager is not None
        ):
            await self._dhw_scenario_manager.async_apply(option)
        else:
            await self.entity_description.select_option(self, option)
        self.async_write_ha_state()
