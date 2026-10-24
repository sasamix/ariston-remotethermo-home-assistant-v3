"""Regression tests for GALEVO Energy device layout and legacy controls."""

import ast
from dataclasses import dataclass, replace
import logging
import sys
from types import ModuleType
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).parents[1] / "custom_components" / "ariston"
SENSOR_SOURCE = (ROOT / "sensor.py").read_text(encoding="utf-8")
BUTTON_SOURCE = (ROOT / "button.py").read_text(encoding="utf-8")
INIT_SOURCE = (ROOT / "__init__.py").read_text(encoding="utf-8")


def compile_def(source, name, namespace):
    node = next(
        n for n in ast.parse(source).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), "isolated.py", "exec"),
         namespace)
    return namespace[name]


@dataclass(frozen=True)
class Desc:
    key: str
    coordinator: str
    device_features: tuple = ()
    system_types: tuple = ()
    whe_types: tuple = ()
    entity_category: str = "diagnostic"


class FakeDevice:
    system_type = "GALEVO"

    def are_device_features_available(self, *args):
        # SDK does NOT announce any split kWh/total channels on this GALEVO.
        return False


class FakeSensor:
    def __init__(self, coordinator, description):
        self.entity_description = description
        self.device_info = {
            "identifiers": {("ariston", "GALEVO-SERIAL")},
            "name": "Печка",
            "manufacturer": "ariston",
        }


class FakeCalendarSensor(FakeSensor):
    pass


class FakeGasSensor:
    def __init__(self, coordinator, entry, suffix, name, series, device_info):
        self.suffix = suffix
        self.device_info = device_info


class LayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_six_energy_entities_are_never_filtered_by_sdk_flags(self):
        keys = {
            "Central heating electricity consumption",
            "Domestic hot water electricity consumption",
            "Central heating gas consumption",
            "Domestic hot water gas consumption",
            "Central heating total energy consumption",
            "Domestic hot water total energy consumption",
        }
        descriptors = [Desc(k, "energy_coordinator") for k in sorted(keys)]
        device = FakeDevice()
        energy = SimpleNamespace(device=device)
        parent = FakeDevice()
        parent.are_device_features_available = lambda *args: True
        normal = SimpleNamespace(device=parent)
        # Parent must be included even if the energy source is independent.
        descriptors.append(Desc("NORMAL_PARENT", "coordinator"))

        async def attach(*args):
            pass
        dependency=ModuleType("layout_test_package.accounting_runtime")
        dependency.attach=attach
        sys.modules[dependency.__name__]=dependency
        namespace = {
            "__package__":"layout_test_package",
            "HomeAssistant": object, "ConfigEntry": object,
            "DeviceDataUpdateCoordinator": object,
            "ARISTON_SENSOR_TYPES": descriptors,
            "DOMAIN": "ariston", "ENERGY_COORDINATOR": "energy_coordinator",
            "SystemType": SimpleNamespace(GALEVO="GALEVO"),
            "ENERGY_SOURCES": {k: None for k in keys},
            "METERING_READING_KEYS": set(),
            "AristonCalendarEnergySensor": FakeCalendarSensor,
            "AristonSensor": FakeSensor,
            "AristonGasEnergySensor": FakeGasSensor,
            "AristonHeatingZoneSensor": FakeSensor,
            "HEATING_SCENARIO_MANAGERS": "heating_scenarios",
            "replace": replace, "_LOGGER": logging.getLogger(__name__),
            "COORDINATOR": "coordinator",
        }
        namespace["_metering_device_info"] = compile_def(
            SENSOR_SOURCE, "_metering_device_info",
            {"DOMAIN": "ariston"},
        )
        setup = compile_def(SENSOR_SOURCE, "async_setup_entry", namespace)
        hass = SimpleNamespace(data={"ariston": {"gateway": {
            "energy_coordinator": energy,
            "coordinator": normal,
            "heating_scenarios": {},
        }}})
        entry = SimpleNamespace(unique_id="gateway", entry_id="entry")
        collected = []
        await setup(hass, entry, collected.extend)

        core = [e for e in collected if isinstance(e, FakeCalendarSensor)]
        gas = [e for e in collected if isinstance(e, FakeGasSensor)]
        self.assertEqual(len(core), 6)
        self.assertEqual(len(gas), 2)
        self.assertTrue(all(e.entity_description.entity_category is None for e in core))
        self.assertEqual({e.suffix for e in gas}, {"dhw", "heating"})
        self.assertTrue(all(e.device_info["name"] == "Энергопотребление" for e in gas))

    def test_delete_callback_was_removed(self):
        definitions = {
            n.name for n in ast.parse(INIT_SOURCE).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertNotIn("async_remove_config_entry_device", definitions)

    def test_native_download_replaces_custom_file_button(self):
        """Export uses HA's native diagnostics.py, not a hidden /config file."""
        diagnostics_source = (ROOT / "diagnostics.py").read_text(encoding="utf-8")
        self.assertNotIn("AristonExportR2Diagnostics", BUTTON_SOURCE)
        self.assertIn("async_get_config_entry_diagnostics", diagnostics_source)
        self.assertIn("async_get_device_diagnostics", diagnostics_source)
        self.assertIn('"r2_as_physical_unit_histogram"', diagnostics_source)
        self.assertIn('"api_v2_sequences"', diagnostics_source)
        self.assertNotIn("config.path(", diagnostics_source)
        self.assertNotIn('"password"', diagnostics_source)
        # The exact obsolete button is removed from the entity registry.
        self.assertIn("Ariston export R2 diagnostics", INIT_SOURCE)

    def test_gas_hour_and_month_are_regular_sensors(self):
        required = (
            "Central heating gas current 2h volume",
            "Domestic hot water gas current 2h volume",
            "Central heating gas current month native",
            "Domestic hot water gas current month native",
            "Central heating gas current 2h average flow",
            "Domestic hot water gas current 2h average flow",
        )
        for name in required:
            self.assertIn(f'"{name}"', SENSOR_SOURCE)

    def test_monthly_gas_totals_reset_instead_of_going_negative(self):
        import ast
        source = (Path(__file__).parents[1] / "custom_components/ariston/const.py").read_text()
        found = {}
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "AristonSensorEntityDescription":
                kw = {k.arg: k.value for k in node.keywords}
                key = kw.get("key")
                if isinstance(key, ast.Constant) and str(key.value).endswith("current month native"):
                    found[key.value] = ast.unparse(kw["state_class"])
        self.assertEqual(sorted(found.values()), ["SensorStateClass.TOTAL_INCREASING"]*2)


if __name__ == "__main__":
    unittest.main()
