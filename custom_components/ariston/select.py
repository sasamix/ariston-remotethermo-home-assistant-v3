"""Support for Ariston sensors."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import ARISTON_SELECT_TYPES, DOMAIN, AristonSelectEntityDescription
from .coordinator import DeviceDataUpdateCoordinator
from .dhw_scenarios import DHW_SCENARIO_MANAGER
from .entity import AristonEntity

_LOGGER = logging.getLogger(__name__)


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

    async_add_entities(ariston_select)


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
