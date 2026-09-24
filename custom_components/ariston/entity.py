"""Entity object for shared properties of Ariston entities."""

from __future__ import annotations

from abc import ABC
import logging

from ariston.const import ConsumptionProperties, ThermostatProperties, WheType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    EXTRA_STATE_ATTRIBUTE,
    EXTRA_STATE_DEVICE_METHOD,
    AristonBaseEntityDescription,
)
from .coordinator import DeviceDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# These entities existed before their display names were reorganized.  Their
# unique IDs historically included the old display name, so keep that portion
# stable to preserve the entity registry, entity_id and Recorder history.
_LEGACY_UNIQUE_ID_NAMES = {
    ConsumptionProperties.CURRENCY: "Ariston currency",
    ConsumptionProperties.ELEC_COST: "Ariston elec cost",
    ConsumptionProperties.GAS_COST: "Ariston gas cost",
    ConsumptionProperties.GAS_TYPE: "Ariston gas type",
    ConsumptionProperties.GAS_ENERGY_UNIT: "Ariston gas energy unit",
    ThermostatProperties.HEATING_FLOW_TEMP: "Ariston heating flow temperature",
    ThermostatProperties.HEATING_FLOW_OFFSET: "Ariston heating flow offset",
}


class AristonEntity(CoordinatorEntity, ABC):
    """Generic Ariston entity (base class)."""

    def __init__(
        self,
        coordinator: DeviceDataUpdateCoordinator,
        description: AristonBaseEntityDescription,
        zone: int = 0,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)

        self.device = coordinator.device
        self.entity_description: AristonBaseEntityDescription = description
        self.zone = zone

    @property
    def device_info(self) -> DeviceInfo:
        """Return device specific attributes."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.serial_number or "")},
            manufacturer=DOMAIN,
            name=self.device.name,
            sw_version=self.device.firmware_version,
            model=self.model,
        )

    @property
    def model(self) -> str:
        """Return the model of the entity."""
        if self.device.whe_model_type == 0:
            if self.device.whe_type is WheType.Unknown:
                return f"{self.device.system_type.name}"
            return f"{self.device.system_type.name} {self.device.whe_type.name}"
        return f"{self.device.system_type.name} {self.device.whe_type.name} | Model {self.device.whe_model_type}"

    @property
    def extra_state_attributes(self):
        """Return the holiday end date."""
        state_attributes = {}

        if self.entity_description.extra_states is None:
            return None

        for extra_state in self.entity_description.extra_states:
            device_method = extra_state.get(EXTRA_STATE_DEVICE_METHOD)

            if device_method is None:
                continue

            state_attribute = device_method(self)

            if state_attribute is None:
                continue

            state_attributes[extra_state.get(EXTRA_STATE_ATTRIBUTE)] = state_attribute

        return state_attributes

    @property
    def unique_id(self):
        """Return the unique id."""
        unique_name = _LEGACY_UNIQUE_ID_NAMES.get(
            self.entity_description.key,
            self.name,
        )
        return (
            f"{self.device.gateway}-{unique_name}-{self.zone}"
            if self.zone
            else f"{self.device.gateway}-{unique_name}"
        )
