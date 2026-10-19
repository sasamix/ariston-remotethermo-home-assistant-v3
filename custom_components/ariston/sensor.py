"""Support for Ariston sensors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
import hashlib
import logging

from ariston import SystemType

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .const import (
    ARISTON_SENSOR_TYPES,
    DOMAIN,
    ENERGY_COORDINATOR,
    AristonSensorEntityDescription,
    get_native_gas_energy_live_total,
)
from .coordinator import DeviceDataUpdateCoordinator
from .entity import AristonEntity
from .heating_scenarios import HEATING_SCENARIO_MANAGERS
from .energy_counter import ENERGY_SOURCES, DAILY_KEYS, EnergyCounter, calendar_total, electricity_periods, nonnegative

_LOGGER = logging.getLogger(__name__)


@dataclass(kw_only=True, frozen=True)
class AristonHeatingSensorEntityDescription(SensorEntityDescription):
    """Sensor description compatible with AristonEntity."""

    extra_states: list | None = None


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
            sensor_type = (
                AristonCalendarEnergySensor
                if coordinator.device.system_type == SystemType.GALEVO
                and description.key in ENERGY_SOURCES
                else AristonSensor
            )
            ariston_sensors.append(
                sensor_type(
                    coordinator,
                    description,
                )
            )

    heating_managers = hass.data[DOMAIN][entry.unique_id].get(
        HEATING_SCENARIO_MANAGERS, {}
    )
    for zone, manager in heating_managers.items():
        coordinator: DeviceDataUpdateCoordinator = hass.data[DOMAIN][entry.unique_id][
            "coordinator"
        ]
        ariston_sensors.extend(
            [
                AristonHeatingZoneSensor(
                    coordinator,
                    manager,
                    zone,
                    "active_program",
                    f"Ariston heating active program zone {zone}",
                    "mdi:radiator",
                ),
                AristonHeatingZoneSensor(
                    coordinator,
                    manager,
                    zone,
                    "active_target",
                    f"Ariston heating active target temperature zone {zone}",
                    "mdi:thermometer-check",
                    temperature=True,
                ),
                AristonHeatingZoneSensor(
                    coordinator,
                    manager,
                    zone,
                    "comfort",
                    f"Ariston heating comfort temperature zone {zone}",
                    "mdi:thermometer-chevron-up",
                    temperature=True,
                ),
                AristonHeatingZoneSensor(
                    coordinator,
                    manager,
                    zone,
                    "economy",
                    f"Ariston heating economy temperature zone {zone}",
                    "mdi:thermometer-chevron-down",
                    temperature=True,
                ),
            ]
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



class AristonHeatingZoneSensor(AristonEntity, SensorEntity):
    """Per-zone heating schedule/status sensor."""

    def __init__(
        self,
        coordinator,
        manager,
        zone: int,
        kind: str,
        name: str,
        icon: str,
        temperature: bool = False,
    ) -> None:
        translation_key = {
            "active_program": "heating_active_program",
            "active_target": "heating_active_target_temperature",
            "comfort": "heating_comfort_temperature",
            "economy": "heating_economy_temperature",
        }[kind]
        self._legacy_name = name
        description = AristonHeatingSensorEntityDescription(
            key=f"Heating{kind.title().replace('_', '')}Zone{zone}",
            name=None,
            translation_key=translation_key,
            translation_placeholders={"zone": str(zone)},
            icon=icon,
            device_class=(
                SensorDeviceClass.TEMPERATURE
                if temperature
                else SensorDeviceClass.ENUM
                if kind == "active_program"
                else None
            ),
            options=(
                ["Comfort", "Economy", "Manual", "Off"]
                if kind == "active_program"
                else None
            ),
            state_class=SensorStateClass.MEASUREMENT if temperature else None,
            native_unit_of_measurement=(
                UnitOfTemperature.CELSIUS if temperature else None
            ),
        )
        super().__init__(coordinator, description, zone)
        self.manager = manager
        self.kind = kind

    @property
    def unique_id(self) -> str:
        """Keep the pre-localization unique id."""
        return f"{self.device.gateway}-{self._legacy_name}-{self.zone}"

    @property
    def native_value(self):
        """Return the requested heating-zone value."""
        if self.kind == "active_program":
            return self.manager.active_program
        if self.kind == "active_target":
            return self.manager.active_target_temperature
        if self.kind == "comfort":
            return self.device.get_comfort_temp_value(self.zone)
        if self.kind == "economy":
            return self.device.get_zone_economy_temp_value(self.zone)
        return None


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
        self._attr_has_entity_name = True
        self._attr_translation_key = (
            "gas_heating_energy_live" if suffix == "heating" else "gas_dhw_energy_live"
        )
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


class AristonCalendarEnergySensor(AristonSensor, RestoreEntity):
    """Existing GALEVO kWh entity backed by a persistent calendar counter.

    Preserve the descriptor, unique_id, unit and state_class. In particular,
    a first installation of this fix must not book the year's total as new
    consumption. Recorder history is not rewritten by this migration.
    """

    def __init__(self, coordinator, description):
        super().__init__(coordinator, description)
        self._counter = None
        self._store = None
        self._source_available = False
        self._restored_floor = 0.0

    async def async_added_to_hass(self):
        key = hashlib.sha256(self.unique_id.encode()).hexdigest()
        self._store = Store(self.hass, 1, f"{DOMAIN}.energy_counter.{key}")
        saved = await self._store.async_load()
        old_state = await self.async_get_last_state()
        if saved is None and (old_state is None or nonnegative(old_state.state) is None):
            # The old AristonSensor did not implement RestoreEntity. Read its
            # last numeric Recorder state during this one-time migration.
            old_state = await self._async_migration_state()
        initial = nonnegative(old_state.state) if old_state else None
        if initial is not None:
            initial = EnergyConverter.convert(
                initial, old_state.attributes.get("unit_of_measurement", "kWh"), "kWh"
            )
        self._restored_floor = initial or 0.0
        self._counter = EnergyCounter(saved, initial or 0.0)
        if saved is None and old_state:
            # Keep the old reset marker constant through migration. Changing
            # it would tell Recorder to count the whole restored value again.
            self._counter.last_reset = old_state.attributes.get("last_reset")
        self._update_counter()
        # Persist the baseline before this entity starts publishing states.
        await self._store.async_save(self._counter.as_dict())
        await super().async_added_to_hass()

    async def _async_migration_state(self):
        if "recorder" not in self.hass.config.components:
            return None
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.history import get_significant_states
        from homeassistant.components.recorder.statistics import (
            get_last_short_term_statistics, get_last_statistics,
        )
        from homeassistant.core import State

        recorder = get_instance(self.hass)
        states = await recorder.async_add_executor_job(partial(
            get_significant_states, self.hass, dt_util.utcnow() - timedelta(days=1),
            entity_ids=[self.entity_id], significant_changes_only=False,
        ))
        for state in reversed(states.get(self.entity_id, [])):
            if nonnegative(state.state) is not None:
                return state
        # An entity may have been unavailable longer than the retained state
        # history. Its last statistics state still anchors the existing sum.
        for getter in (get_last_short_term_statistics, get_last_statistics):
            result = await recorder.async_add_executor_job(
                getter, self.hass, 1, self.entity_id, True, {"state", "last_reset"}
            )
            for row in result.get(self.entity_id, []):
                if nonnegative(row.get("state")) is not None:
                    reset = row.get("last_reset")
                    attrs = {"unit_of_measurement": self.unit_of_measurement}
                    if reset is not None:
                        attrs["last_reset"] = dt_util.utc_from_timestamp(reset).isoformat()
                    return State(self.entity_id, str(row["state"]), attrs)
        return None

    def _update_counter(self):
        if self._counter is None:
            return
        payload = getattr(self.device, "r2_kwh_data", None)
        snapshot_date = getattr(self.device, "r2_kwh_snapshot_date", None)
        series, tabs = ENERGY_SOURCES[self.entity_description.key]
        before = self._counter.as_dict()
        if self.entity_description.key in DAILY_KEYS:
            api_date = getattr(self.device, "energy_sequences_date", None)
            api_time = getattr(self.device, "energy_sequences_time", None)
            r2_time = getattr(self.device, "r2_kwh_snapshot_time", None)
            days = electricity_periods(
                getattr(self.device, "energy_sequences_snapshot", None),
                getattr(self.device, "r2_kwh_raw", None),
                api_time, self.entity_description.key,
            ) if (api_date == snapshot_date and api_time and r2_time
                  and api_time.hour // 2 == r2_time.hour // 2) else None
            self._source_available = bool(days) and self._counter.update_days(days)
        else:
            source = calendar_total(payload, series, tabs, snapshot_date)
            self._source_available = source is not None
            if source is None:
                return
            previous = calendar_total(payload, series, tabs, snapshot_date, previous_year=True)
            self._counter.update(source, snapshot_date.year, previous)
        if self._counter.as_dict() != before and self._store is not None:
            self._store.async_delay_save(self._counter.as_dict, 1)

    def _handle_coordinator_update(self):
        self._update_counter()
        super()._handle_coordinator_update()

    @property
    def available(self):
        return super().available and self._source_available

    @property
    def native_value(self):
        if self._counter is None or self._counter.source is None:
            return None
        # Store and Recorder flush independently. After an abrupt shutdown,
        # never publish less than the last visible value while the persisted
        # source/output pair catches up. Do not raise the stored output alone:
        # that would replay the difference on the next source update.
        return max(self._counter.value, self._restored_floor)

    @property
    def last_reset(self):
        if self._counter and self._counter.last_reset:
            return dt_util.parse_datetime(self._counter.last_reset)
        return None

    @property
    def extra_state_attributes(self):
        attrs = dict(super().extra_state_attributes or {})
        daily = self.entity_description.key in DAILY_KEYS
        attrs["energy_counter_source"] = (
            "API v2 two-hour intervals verified against R2" if daily
            else "R2 asKwh calendar reports"
        )
        if daily and self._counter and self._counter.daily:
            attrs["source_through_date"] = max(self._counter.daily)
            stamp = getattr(self.device, "energy_sequences_time", None)
            attrs["source_interval_end"] = (
                stamp.replace(hour=(stamp.hour // 2) * 2, minute=0, second=0, microsecond=0).isoformat()
                if stamp else None
            )
        if self._counter:
            attrs["source_year"] = self._counter.year
            attrs["source_total_kwh"] = self._counter.source
        snapshot_date = getattr(self.device, "r2_kwh_snapshot_date", None)
        attrs["source_snapshot_date"] = snapshot_date.isoformat() if snapshot_date else None
        return attrs
