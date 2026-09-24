"""Button entity for saving the current Ariston DHW schedule."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .const import COORDINATOR, DOMAIN
from .coordinator import DeviceDataUpdateCoordinator
from .dhw_scenarios import DHW_SCENARIO_MANAGER
from .heating_scenarios import HEATING_SCENARIO_MANAGERS
from .entity import AristonEntity


@dataclass(kw_only=True, frozen=True)
class AristonButtonEntityDescription(ButtonEntityDescription):
    """Button description compatible with AristonEntity."""

    extra_states: list | None = None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up DHW and heating save-current-scenario buttons."""
    data = hass.data[DOMAIN][entry.unique_id]
    coordinator: DeviceDataUpdateCoordinator = data[COORDINATOR]
    entities = []

    manager = data.get(DHW_SCENARIO_MANAGER)
    if manager is not None:
        entities.append(AristonSaveCurrentDhwScenario(coordinator, manager))

    for zone, heating_manager in data.get(
        HEATING_SCENARIO_MANAGERS, {}
    ).items():
        entities.append(
            AristonSaveCurrentHeatingScenario(
                coordinator,
                heating_manager,
                zone,
            )
        )

    async_add_entities(entities)


class AristonSaveCurrentDhwScenario(AristonEntity, ButtonEntity):
    """Save the currently loaded Ariston schedule under the entered HA name."""

    entity_description = AristonButtonEntityDescription(
        key="SaveCurrentDhwScenario",
        name="Ariston DHW scenario save",
        icon="mdi:content-save",
        entity_category=EntityCategory.CONFIG,
    )

    def __init__(self, coordinator, manager) -> None:
        super().__init__(coordinator, self.entity_description)
        self.manager = manager

    @property
    def unique_id(self) -> str:
        """Keep the original unique id after moving the button into the DHW group."""
        return f"{self.device.gateway}-Ariston save current DHW scenario"

    async def async_press(self) -> None:
        """Save the current cloud schedule under the text entity's draft name."""
        await self.manager.async_save_current(self.manager.draft_name)


class AristonSaveCurrentHeatingScenario(AristonEntity, ButtonEntity):
    """Save the current heating-zone schedule under the entered HA name."""

    def __init__(self, coordinator, manager, zone: int) -> None:
        description = AristonButtonEntityDescription(
            key=f"SaveCurrentHeatingScenarioZone{zone}",
            name=f"Ariston CH scenario zone {zone} save",
            translation_key="heating_scenario_save",
            translation_placeholders={"zone": str(zone)},
            icon="mdi:content-save",
            entity_category=EntityCategory.CONFIG,
        )
        super().__init__(coordinator, description, zone)
        self.manager = manager

    @property
    def unique_id(self) -> str:
        """Keep the original unique id while changing display order/name."""
        return (
            f"{self.device.gateway}-"
            f"Ariston save current heating scenario zone {self.zone}-{self.zone}"
        )

    async def async_press(self) -> None:
        """Save the current heating schedule under the text entity draft."""
        await self.manager.async_save_current(self.manager.draft_name)
