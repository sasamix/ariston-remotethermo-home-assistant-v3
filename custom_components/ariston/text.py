"""Text entity for naming the current Ariston DHW schedule."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.text import TextEntity, TextEntityDescription, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .const import COORDINATOR, DOMAIN
from .coordinator import DeviceDataUpdateCoordinator
from .dhw_scenarios import DHW_SCENARIO_MANAGER
from .entity import AristonEntity


@dataclass(kw_only=True, frozen=True)
class AristonTextEntityDescription(TextEntityDescription):
    """Text description compatible with AristonEntity."""

    extra_states: list | None = None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the DHW scenario-name text entity."""
    data = hass.data[DOMAIN][entry.unique_id]
    coordinator: DeviceDataUpdateCoordinator = data[COORDINATOR]
    manager = data.get(DHW_SCENARIO_MANAGER)
    if manager is None:
        return

    async_add_entities([AristonDhwScenarioName(coordinator, manager)])


class AristonDhwScenarioName(AristonEntity, TextEntity):
    """Name entered by the user for the currently loaded DHW schedule."""

    entity_description = AristonTextEntityDescription(
        key="DhwScenarioName",
        name="Ariston DHW scenario name",
        icon="mdi:form-textbox",
        entity_category=EntityCategory.CONFIG,
    )
    _attr_native_max = 100
    _attr_mode = TextMode.TEXT

    def __init__(self, coordinator, manager) -> None:
        super().__init__(coordinator, self.entity_description)
        self.manager = manager

    @property
    def native_value(self) -> str:
        """Return the current draft name."""
        return self.manager.draft_name

    async def async_set_value(self, value: str) -> None:
        """Update the draft; saving is explicit through the button entity."""
        self.manager.draft_name = value.strip()
        self.async_write_ha_state()
