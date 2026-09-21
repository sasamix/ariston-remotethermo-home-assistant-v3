"""Support for Ariston sensors."""

from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ARISTON_SENSOR_TYPES,
    DOMAIN,
    ENERGY_COORDINATOR,
    AristonSensorEntityDescription,
    get_native_gas_energy_live_total,
)
from .coordinator import DeviceDataUpdateCoordinator
from .entity import AristonEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the Ariston sensors from config entry."""
    ariston_sensors: list[AristonSensor] = []

    for description in ARISTON_SENSOR_TYPES:
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
            ariston_sensors.append(
                AristonSensor(
                    coordinator,
                    description,
                )
            )

    async_add_entities(ariston_sensors)

    # Independent R2 gas counters for Home Assistant Energy Dashboard.
    # They intentionally do not use AristonSensorEntityDescription/AristonEntity.
    energy_coordinator: DeviceDataUpdateCoordinator = hass.data[DOMAIN][entry.unique_id][
        ENERGY_COORDINATOR
    ]
    if energy_coordinator and energy_coordinator.device:
        # IMPORTANT: never guess Ariston device identifiers here.
        # Reuse device_info from an actual normal AristonSensor already created
        # by this platform. That is the exact HA device currently named "Печка".
        real_device_info = None
        for _normal_sensor in ariston_sensors:
            _info = getattr(_normal_sensor, "device_info", None)
            if _info:
                real_device_info = _info
                break

        if real_device_info is not None:
            async_add_entities(
                [
                AristonGasEnergySensor(
                    energy_coordinator,
                    entry,
                    "heating",
                    "Газ отопление — Energy Live",
                    "Heating",
                    real_device_info,
                ),
                AristonGasEnergySensor(
                    energy_coordinator,
                    entry,
                    "dhw",
                    "Газ ГВС — Energy Live",
                    "Dhw",
                    real_device_info,
                ),
            ]
        )



class AristonGasEnergySensor(CoordinatorEntity, SensorEntity):
    """Native R2 cumulative gas counter for Home Assistant Energy."""

    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_icon = "mdi:meter-gas"
    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: DeviceDataUpdateCoordinator,
        entry: ConfigEntry,
        suffix: str,
        name: str,
        series: str,
        real_device_info,
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_device_info = real_device_info
        self._series = series
        self._last_valid_value: float | None = None
        self._last_valid_year: int | None = None
        base_id = entry.unique_id or getattr(coordinator.device, "gw", "ariston")
        self._config_entry_unique_id = entry.unique_id
        # Keep the existing unique_id so HA preserves the same entity/history.
        self._attr_unique_id = f"{base_id}_r2_gas_energy_live_v3_{suffix}"

    @property
    def native_value(self):
        value = get_native_gas_energy_live_total(
            self.coordinator.device, self._series
        )
        current_year = datetime.now().year

        # No usable R2 snapshot: hold the previous valid counter value.
        if value is None:
            return self._last_valid_value

        try:
            value = float(value)
        except (TypeError, ValueError):
            return self._last_valid_value

        # A fresh entity may legitimately start at zero, but once a positive
        # yearly total has been seen, a transient zero/decrease is not published.
        if self._last_valid_year == current_year and self._last_valid_value is not None:
            if value < self._last_valid_value:
                _LOGGER.warning(
                    "Ariston R2 gas Energy ignored decreasing value for %s: %.3f -> %.3f m3",
                    self._series,
                    self._last_valid_value,
                    value,
                )
                return self._last_valid_value

        # On a real calendar-year change a reset is allowed.
        self._last_valid_year = current_year
        self._last_valid_value = value
        return value



class AristonSensor(AristonEntity, SensorEntity):
    """Base class for specific ariston sensors."""

    def __init__(
        self,
        coordinator: DeviceDataUpdateCoordinator,
        description: AristonSensorEntityDescription,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator, description)

    @property
    def native_value(self):
        """Return value of sensor."""
        return self.entity_description.get_native_value(self)

    @property
    def native_unit_of_measurement(self):
        """Return the nateive unit of measurement."""
        if self.entity_description.get_native_unit_of_measurement is not None:
            return self.entity_description.get_native_unit_of_measurement(self)

        if self.entity_description.native_unit_of_measurement is not None:
            return self.entity_description.native_unit_of_measurement

        return None

    @property
    def device_class(self):
        """Return the device class."""
        if self.entity_description.get_device_class is not None:
            return self.entity_description.get_device_class(self)

        return self.entity_description.device_class

    @property
    def last_reset(self) -> datetime | None:
        """Return the time when the sensor was last reset, if any."""
        if self.entity_description.get_last_reset is not None:
            return self.entity_description.get_last_reset(self)

        return None
