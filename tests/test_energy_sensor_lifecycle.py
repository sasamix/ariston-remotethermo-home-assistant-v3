"""Exercise the actual sensor class with lightweight HA lifecycle doubles."""

import ast
from datetime import date, datetime, timedelta
from functools import partial
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest

from test_energy_counter import EnergyCounter, calendar_total, dataset, module


class BaseSensor:
    def __init__(self, coordinator, description):
        self.device = coordinator.device
        self.entity_description = description
        self.hass = SimpleNamespace(config=SimpleNamespace(components=set()))
        self.unique_id = "gateway-Ariston central heating electricity consumption"
        self.entity_id = "sensor.elektrichestvo_dlia_otopleniia"
        self.old_state = None

    async def async_added_to_hass(self):
        pass

    def _handle_coordinator_update(self):
        pass

    @property
    def available(self):
        return True

    @property
    def extra_state_attributes(self):
        return None


class Restore:
    async def async_get_last_state(self):
        return self.old_state


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


source = (Path(__file__).parents[1] / "custom_components/ariston/sensor.py").read_text()
node = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "AristonCalendarEnergySensor")
namespace = {
    "AristonSensor": BaseSensor, "RestoreEntity": Restore, "Store": Storage,
    "ENERGY_SOURCES": module.ENERGY_SOURCES, "EnergyCounter": EnergyCounter,
    "DAILY_KEYS": module.DAILY_KEYS, "electricity_periods": module.electricity_periods,
    "calendar_total": calendar_total, "nonnegative": module.nonnegative,
    "DOMAIN": "ariston", "hashlib": hashlib, "partial": partial, "timedelta": timedelta,
    "dt_util": SimpleNamespace(parse_datetime=datetime.fromisoformat),
    "EnergyConverter": SimpleNamespace(convert=lambda value, old, new: value / 1000 if old == "Wh" else value),
}
mixin = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "DatedAccountingMixin")
exec(compile(ast.Module(body=[mixin, node], type_ignores=[]), "sensor.py", "exec"), namespace)
Sensor = namespace["AristonCalendarEnergySensor"]


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Storage.persisted = None
        self.device = SimpleNamespace(r2_kwh_snapshot_date=date(2026, 2, 2), r2_kwh_data={
            "histogramData": [
                dataset("CurrentYear", [10] + [0] * 11, tab="ConsumedGas"),
                dataset("CurrentMonth", [2, 3], tab="ConsumedGas"),
            ],
        })

    def sensor(self, value=None, unit="kWh"):
        sensor = Sensor(SimpleNamespace(device=self.device), SimpleNamespace(key="Central heating gas consumption"))
        if value is not None:
            sensor.old_state = SimpleNamespace(state=str(value), attributes={
                "unit_of_measurement": unit, "last_reset": "2026-02-02T03:00:00+00:00",
            })
        return sensor

    async def test_migrate_update_reload_without_spike(self):
        sensor = self.sensor(0.077)
        await sensor.async_added_to_hass()
        self.assertEqual(sensor.native_value, 0.077)
        self.device.r2_kwh_data["histogramData"][1]["items"][1]["y"] = 4
        sensor._handle_coordinator_update()
        self.assertEqual(sensor.native_value, 1.077)
        restored = self.sensor(1.077)
        await restored.async_added_to_hass()
        self.assertEqual(restored.native_value, 1.077)
        self.assertEqual(restored.last_reset, sensor.last_reset)
        for _ in range(5):
            restored._handle_coordinator_update()
            self.assertEqual(restored.native_value, 1.077)

    async def test_missing_report_then_recovery(self):
        report = self.device.r2_kwh_data
        self.device.r2_kwh_data = None
        sensor = self.sensor(0.077)
        await sensor.async_added_to_hass()
        # A previously verified HA baseline remains visible when R2 is down.
        # This is the requested protection against unavailable Energy meters.
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, 0.077)
        self.device.r2_kwh_data = report
        sensor._handle_coordinator_update()
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, 0.077)

    async def test_zero_is_a_valid_restored_electricity_baseline(self):
        self.device.r2_kwh_data = None
        sensor = self.sensor(0.0)
        await sensor.async_added_to_hass()
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, 0.0)

    async def test_never_seen_meter_does_not_fake_zero(self):
        self.device.r2_kwh_data = None
        sensor = self.sensor()
        await sensor.async_added_to_hass()
        self.assertFalse(sensor.available)
        self.assertIsNone(sensor.native_value)

    async def test_placeholder_store_does_not_block_recorder_recovery(self):
        from unittest.mock import AsyncMock
        self.device.r2_kwh_data = None
        Storage.persisted = {"value": 0.0, "source": None, "year": None}
        sensor = self.sensor()
        sensor._async_migration_state = AsyncMock(return_value=SimpleNamespace(
            state="0.215", attributes={"unit_of_measurement": "kWh",
                                     "last_reset": "1970-01-01T00:00:00+00:00"}))
        await sensor.async_added_to_hass()
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, .215)
        self.assertEqual(Storage.persisted['value'], .215)
        self.assertEqual(Storage.persisted['last_reset'], "1970-01-01T00:00:00+00:00")

    async def test_confirmed_zero_survives_restart_without_cloud(self):
        self.device.r2_kwh_data = None
        first = self.sensor(0.0)
        await first.async_added_to_hass()
        second = self.sensor()
        await second.async_added_to_hass()
        self.assertTrue(second.available)
        self.assertEqual(second.native_value, 0.0)

    async def test_display_wh_restores_native_kwh(self):
        sensor = self.sensor(77, "Wh")
        await sensor.async_added_to_hass()
        self.assertEqual(sensor.native_value, 0.077)

    async def test_unconfirmed_store_zero_remains_unavailable_on_restart(self):
        self.device.r2_kwh_data = None
        first = self.sensor()
        await first.async_added_to_hass()
        self.assertIsNone(first.native_value)
        second = self.sensor()
        await second.async_added_to_hass()
        self.assertFalse(second.available)
        self.assertIsNone(second.native_value)

    async def test_store_behind_restored_state_does_not_replay_delta(self):
        sensor = self.sensor(0)
        await sensor.async_added_to_hass()
        # Recorder has seen one more kWh but the pending Store write was lost.
        restored = self.sensor(1)
        await restored.async_added_to_hass()
        self.assertEqual(restored.native_value, 1)
        self.device.r2_kwh_data["histogramData"][1]["items"][1]["y"] = 4
        restored._handle_coordinator_update()
        self.assertEqual(restored.native_value, 1)
        self.device.r2_kwh_data["histogramData"][1]["items"][1]["y"] = 5
        restored._handle_coordinator_update()
        self.assertEqual(restored.native_value, 2)

    async def test_failed_initial_report_does_not_lose_migration_on_reload(self):
        report = self.device.r2_kwh_data
        self.device.r2_kwh_data = None
        sensor = self.sensor(0.077)
        await sensor.async_added_to_hass()
        restored = self.sensor()
        await restored.async_added_to_hass()
        self.device.r2_kwh_data = report
        restored._handle_coordinator_update()
        self.assertEqual(restored.native_value, 0.077)
        self.assertEqual(restored.last_reset, sensor.last_reset)


if __name__ == "__main__":
    unittest.main()
