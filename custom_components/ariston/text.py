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
from .heating_scenarios import HEATING_SCENARIO_MANAGERS
from .entity import AristonEntity


@dataclass(kw_only=True, frozen=True)
class AristonTextEntityDescription(TextEntityDescription):
    """Text description compatible with AristonEntity."""

    extra_states: list | None = None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up DHW and heating scenario-name text entities."""
    data = hass.data[DOMAIN][entry.unique_id]
    coordinator: DeviceDataUpdateCoordinator = data[COORDINATOR]
    entities = []

    manager = data.get(DHW_SCENARIO_MANAGER)
    if manager is not None:
        entities.append(AristonDhwScenarioName(coordinator, manager))

    for zone, heating_manager in data.get(
        HEATING_SCENARIO_MANAGERS, {}
    ).items():
        entities.append(
            AristonHeatingScenarioName(
                coordinator,
                heating_manager,
                zone,
            )
        )

    async_add_entities(entities)


class AristonDhwScenarioName(AristonEntity, TextEntity):
    """Name entered by the user for the currently loaded DHW schedule."""

    entity_description = AristonTextEntityDescription(
        key="DhwScenarioName",
        name=None,
        translation_key="dhw_scenario_name",
        icon="mdi:form-textbox",
        entity_category=EntityCategory.CONFIG,
    )
    _attr_native_max = 100
    _attr_mode = TextMode.TEXT

    def __init__(self, coordinator, manager) -> None:
        super().__init__(coordinator, self.entity_description)
        self.manager = manager

    @property
    def unique_id(self) -> str:
        """Keep the pre-localization unique id."""
        return f"{self.device.gateway}-Ariston DHW scenario name"

    @property
    def native_value(self) -> str:
        """Return the current draft name."""
        return self.manager.draft_name

    async def async_set_value(self, value: str) -> None:
        """Edit the name; the adjacent button explicitly saves the schedule."""
        self.manager.draft_name = value
        self.async_write_ha_state()


class AristonHeatingScenarioName(AristonEntity, TextEntity):
    """Name entered by the user for the currently loaded heating schedule."""

    _attr_native_max = 100
    _attr_mode = TextMode.TEXT

    def __init__(self, coordinator, manager, zone: int) -> None:
        description = AristonTextEntityDescription(
            key=f"HeatingScenarioNameZone{zone}",
            name=None,
            translation_key="heating_scenario_name",
            translation_placeholders={"zone": str(zone)},
            icon="mdi:form-textbox",
            entity_category=EntityCategory.CONFIG,
        )
        super().__init__(coordinator, description, zone)
        self.manager = manager

    @property
    def unique_id(self) -> str:
        """Keep the original unique id while changing display order/name."""
        return (
            f"{self.device.gateway}-"
            f"Ariston heating scenario name zone {self.zone}-{self.zone}"
        )

    @property
    def native_value(self) -> str:
        """Show the user's draft, including edits to a recognized scenario."""
        return self.manager.draft_name

    async def async_set_value(self, value: str) -> None:
        """Edit the name; the adjacent button explicitly saves the schedule."""
        self.manager.draft_name = value
        self.async_write_ha_state()
