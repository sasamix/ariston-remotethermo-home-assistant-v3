"""Button entity for saving the current Ariston DHW schedule."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .const import COORDINATOR, DOMAIN
from .coordinator import DeviceDataUpdateCoordinator
from .dhw_scenarios import DHW_SCENARIO_MANAGER
from .entity import AristonEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the save-current-DHW-scenario button."""
    data = hass.data[DOMAIN][entry.unique_id]
    coordinator: DeviceDataUpdateCoordinator = data[COORDINATOR]
    manager = data.get(DHW_SCENARIO_MANAGER)
    if manager is None:
        return

    async_add_entities([AristonSaveCurrentDhwScenario(coordinator, manager)])


class AristonSaveCurrentDhwScenario(AristonEntity, ButtonEntity):
    """Save the currently loaded Ariston schedule under the entered HA name."""

    entity_description = ButtonEntityDescription(
        key="SaveCurrentDhwScenario",
        name="Ariston save current DHW scenario",
        icon="mdi:content-save",
        entity_category=EntityCategory.CONFIG,
    )

    def __init__(self, coordinator, manager) -> None:
        super().__init__(coordinator, self.entity_description)
        self.manager = manager

    async def async_press(self) -> None:
        """Save the current cloud schedule under the text entity's draft name."""
        await self.manager.async_save_current(self.manager.draft_name)
