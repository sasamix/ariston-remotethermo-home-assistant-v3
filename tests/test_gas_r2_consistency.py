"""R2 gas calendar rollover and linked meter-device regressions."""

import ast
from datetime import date, datetime, timedelta, timezone
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "ariston"


def isolated_function(filename, name, globals_):
    source = (ROOT / filename).read_text(encoding="utf-8")
    node = next(item for item in ast.parse(source).body
                if isinstance(item, ast.FunctionDef) and item.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]),
                 filename, "exec"), globals_)
    return globals_[name]


class Clock:
    TZ = timezone(timedelta(hours=5))
    current = datetime(2026, 10, 20, 0, 0, 19, tzinfo=TZ)

    @classmethod
    def now(cls):
        return cls.current


class MidnightGasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calculate = staticmethod(isolated_function(
            "const.py", "get_native_gas_energy_live_total",
            {"dt_util": Clock, "math": math},
        ))

    def setUp(self):
        Clock.current = datetime(2026, 10, 20, 0, 0, 19, tzinfo=Clock.TZ)

    @staticmethod
    def dataset(period, items):
        return {"tab": "ConsumedGas", "series": "Dhw",
                "period": period, "items": items}

    @staticmethod
    def buckets(first=0.0, future=False):
        return [{"x": f"{i * 2:02d}-{(i * 2 + 2) % 24:02d}",
                 "y": first if i == 0 else 0.5 if future and i == 11 else 0.0}
                for i in range(12)]

    def device(self, first=0.0, future=False, previous=None,
               snapshot_date=None, missing_last_day=False):
        prev = self.buckets(0.3)
        prev[1]["y"] = 0.4
        if previous is not None:
            prev = previous
        month = [{"x": str(i), "y": 0.7 if i == 19 else 0.0}
                 for i in range(1, 32)
                 if not (missing_last_day and i == 19)]
        return SimpleNamespace(
            r2_gas_snapshot_date=snapshot_date or Clock.current.date(),
            gas_physical_unit_data={"histogramData": [
                self.dataset("CurrentYear",
                             [{"y": 953.0 if i == 8 else 0.0}
                              for i in range(12)]),
                self.dataset("CurrentMonth", month),
                self.dataset("PreviousDay", prev),
                self.dataset("CurrentDay", self.buckets(first, future)),
            ]},
        )

    def test_midnight_transient_2_56_not_booked(self):
        self.assertEqual(self.calculate(self.device(first=2.56), "Dhw"), 953.7)

    def test_future_buckets_reject_stale_day(self):
        self.assertIsNone(self.calculate(
            self.device(first=2.56, future=True), "Dhw"))

    def test_completed_first_bucket_is_booked_at_02_10(self):
        Clock.current = datetime(2026, 10, 20, 2, 10, tzinfo=Clock.TZ)
        self.assertEqual(self.calculate(self.device(first=0.35), "Dhw"),
                         954.05)

    def test_active_bucket_ignored_at_01_59(self):
        Clock.current = datetime(2026, 10, 20, 1, 59, tzinfo=Clock.TZ)
        self.assertEqual(self.calculate(self.device(first=0.35), "Dhw"),
                         953.7)

    def test_previous_day_mismatch_holds_counter(self):
        self.assertIsNone(self.calculate(
            self.device(previous=self.buckets(1.7)), "Dhw"))

    def test_missing_month_day_holds_counter(self):
        self.assertIsNone(self.calculate(
            self.device(missing_last_day=True), "Dhw"))

    def test_yesterday_snapshot_cannot_be_relabelled(self):
        self.assertIsNone(self.calculate(
            self.device(snapshot_date=date(2026, 10, 19)), "Dhw"))


class MeteringDeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = staticmethod(isolated_function(
            "sensor.py", "_metering_device_info", {"DOMAIN": "ariston"}))

    def test_linked_device_reuses_parent_but_not_unique_id(self):
        parent = {"identifiers": {("ariston", "SERIAL")},
                  "name": "Печка", "manufacturer": "ariston",
                  "sw_version": "1.0"}
        child = self.helper(parent)
        self.assertEqual(child["identifiers"],
                         {("ariston", "SERIAL:energy-metering")})
        self.assertEqual(child["via_device"], ("ariston", "SERIAL"))
        self.assertEqual(child["name"], "Энергопотребление")
        self.assertEqual(parent["identifiers"], {("ariston", "SERIAL")})

    def test_missing_serial_leaves_parent_unchanged(self):
        parent = {"identifiers": {("ariston", "")}, "name": "Печка"}
        self.assertIs(self.helper(parent), parent)


if __name__ == "__main__":
    unittest.main()
