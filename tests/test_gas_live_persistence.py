"""Exercise existing R2 gas entity with lightweight HA lifecycle doubles."""

import ast
from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest

from test_energy_counter import module


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.hass = SimpleNamespace()

    async def async_added_to_hass(self):
        pass

    @property
    def unique_id(self):
        return self._attr_unique_id


class SensorEntity:
    pass


class RestoreEntity:
    async def async_get_last_state(self):
        return getattr(self, "old_state", None)


class Storage:
    persisted = None

    def __init__(self, *args):
        pass

    async def async_load(self):
        return Storage.persisted

    async def async_save(self, value):
        Storage.persisted = dict(value)

    def async_delay_save(self, getter, delay):
        Storage.persisted = dict(getter())


class FixedDate:
    year = 2026

    @classmethod
    def now(cls):
        return datetime(cls.year, 10, 19, 18, 15, tzinfo=timezone.utc)

    @staticmethod
    def as_local(value):
        return value


class GasClassConstants:
    GAS = "gas"
    TOTAL_INCREASING = "total_increasing"
    CUBIC_METERS = "m3"


source = (Path(__file__).parents[1] / "custom_components/ariston/sensor.py").read_text()
node = next(
    n for n in ast.parse(source).body
    if isinstance(n, ast.ClassDef) and n.name == "AristonGasEnergySensor"
)
namespace = {
    "CoordinatorEntity": CoordinatorEntity,
    "SensorEntity": SensorEntity,
    "RestoreEntity": RestoreEntity,
    "SensorDeviceClass": GasClassConstants,
    "SensorStateClass": GasClassConstants,
    "UnitOfVolume": GasClassConstants,
    "DeviceDataUpdateCoordinator": object,
    "ConfigEntry": object,
    "Store": Storage,
    "DOMAIN": "ariston",
    "hashlib": hashlib,
    "dt_util": FixedDate,
    "nonnegative": module.nonnegative,
    "get_native_gas_energy_live_total": lambda device, series: device.value,
    "_LOGGER": logging.getLogger(__name__),
}
exec(compile(ast.Module(body=[node], type_ignores=[]), "sensor.py", "exec"), namespace)
Sensor = namespace["AristonGasEnergySensor"]


class GasLivePersistence(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FixedDate.year = 2026
        Storage.persisted = None
        self.device = SimpleNamespace(value=930.1, gw="gateway")
        self.coordinator = SimpleNamespace(device=self.device)
        self.entry = SimpleNamespace(unique_id="gateway")

    def make_sensor(self, previous=None):
        entity = Sensor(
            self.coordinator, self.entry, "dhw", "DHW", "Dhw", {},
        )
        entity.old_state = (
            SimpleNamespace(
                state=str(previous),
                last_updated=datetime(2026, 10, 19, 17, 0, tzinfo=timezone.utc),
            )
            if previous is not None else None
        )
        return entity

    async def test_restart_retains_previous_high_water_even_on_lower_cloud(self):
        sensor = self.make_sensor(930.52)
        await sensor.async_added_to_hass()
        self.assertEqual(sensor.native_value, 930.52)
        self.assertEqual(Storage.persisted["value"], 930.52)
        self.assertEqual(Storage.persisted["year"], 2026)

        sensor2 = self.make_sensor()
        await sensor2.async_added_to_hass()
        self.assertEqual(sensor2.native_value, 930.52)

        self.device.value = 930.87
        self.assertEqual(sensor2.native_value, 930.87)
        self.assertEqual(Storage.persisted["value"], 930.87)

        self.device.value = 930.2
        self.assertEqual(sensor2.native_value, 930.87)

    async def test_missing_source_and_invalid_number_hold_value(self):
        sensor = self.make_sensor(930.52)
        await sensor.async_added_to_hass()
        for value in (None, "", float("nan"), -1, False):
            self.device.value = value
            self.assertEqual(sensor.native_value, 930.52)

    async def test_year_rollover_is_allowed_and_previous_year_not_restored(self):
        sensor = self.make_sensor(930.52)
        await sensor.async_added_to_hass()
        self.device.value = 931
        self.assertEqual(sensor.native_value, 931)

        FixedDate.year = 2027
        self.device.value = 0.01
        new_sensor = self.make_sensor()
        await new_sensor.async_added_to_hass()
        self.assertEqual(new_sensor.native_value, 0.01)
        self.assertEqual(Storage.persisted["year"], 2027)

    async def test_entity_id_identity_is_unchanged(self):
        sensor = self.make_sensor()
        self.assertEqual(sensor.unique_id, "gateway_r2_gas_energy_live_v3_dhw")


if __name__ == "__main__":
    unittest.main()
