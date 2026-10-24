"""Run with python -m unittest discover -s tests; no HA installation needed."""

from datetime import date
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "energy_counter",
    Path(__file__).parents[1] / "custom_components/ariston/energy_counter.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
EnergyCounter = module.EnergyCounter
calendar_total = module.calendar_total


def dataset(period, values, tab="ConsumedElectricity", series="Heating"):
    return {
        "tab": tab, "series": series, "period": period,
        "items": [{"x": str(i + 1), "y": value} for i, value in enumerate(values)],
    }


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.report = {"histogramData": [
            dataset("CurrentYear", [10, 999] + [0] * 10),
            dataset("CurrentMonth", [2, 3] + [None] * 26),
            dataset("CurrentDay", [999]),
        ]}
        self.day = date(2026, 2, 2)

    def value(self, report=None):
        return calendar_total(report or self.report, "Heating", ("ConsumedElectricity",), self.day)

    def test_no_overlapping_month_or_current_day(self):
        self.assertEqual(self.value(), 15)

    def test_missing_completed_day_not_zero(self):
        self.report["histogramData"][1]["items"].pop(0)
        self.assertIsNone(self.value())

    def test_empty_report_unavailable(self):
        self.assertIsNone(self.value({"histogramData": []}))

    def test_invalid_values_rejected(self):
        for invalid in [None, True, -1, float("nan"), float("inf"), "bad"]:
            with self.subTest(value=invalid):
                self.report["histogramData"][1]["items"][0]["y"] = invalid
                self.assertIsNone(self.value())

    def test_duplicate_day_rejected(self):
        self.report["histogramData"][1]["items"].append({"x": "1", "y": 2})
        self.assertIsNone(self.value())

    def test_total_energy_requires_both_sources(self):
        tabs = ("ConsumedElectricity", "ConsumedGas")
        self.assertIsNone(calendar_total(self.report, "Heating", tabs, self.day))
        self.report["histogramData"] += [
            dataset("CurrentYear", [100] + [0] * 11, "ConsumedGas"),
            dataset("CurrentMonth", [20, 30], "ConsumedGas"),
        ]
        self.assertEqual(calendar_total(self.report, "Heating", tabs, self.day), 165)

    def test_month_rollover_same_total_not_new_consumption(self):
        counter = EnergyCounter()
        counter.update(self.value(), 2026)
        next_month = {"histogramData": [
            dataset("CurrentYear", [10, 5] + [0] * 10),
            dataset("CurrentMonth", [0]),
        ]}
        value = calendar_total(next_month, "Heating", ("ConsumedElectricity",), date(2026, 3, 1))
        counter.update(value, 2026)
        self.assertEqual(counter.value, 0)

    def test_previous_year_requires_all_months(self):
        self.report["histogramData"].append(dataset("PreviousYear", [2] * 12))
        self.assertEqual(calendar_total(self.report, "Heating", ("ConsumedElectricity",), self.day, True), 24)


class CounterTests(unittest.TestCase):
    def test_migration_does_not_book_history(self):
        counter = EnergyCounter(initial_value=0.077)
        counter.update(250, 2026)
        self.assertEqual(counter.value, 0.077)
        counter.update(250.4, 2026)
        self.assertEqual(counter.value, 0.477)

    def test_restart_and_repeated_polls(self):
        counter = EnergyCounter()
        counter.last_reset = "2026-10-19T06:00:00+00:00"
        counter.update(100, 2026)
        counter.update(101, 2026)
        counter = EnergyCounter(counter.as_dict())
        for _ in range(10):
            counter.update(101, 2026)
        counter.update(102, 2026)
        self.assertEqual(counter.value, 2)
        self.assertEqual(counter.last_reset, "2026-10-19T06:00:00+00:00")

    def test_downward_revision_recovery_not_counted_twice(self):
        counter = EnergyCounter()
        counter.update(100, 2026)
        for value in [101, 100.5, 100.9, 101, 102]:
            counter.update(value, 2026)
        self.assertEqual(counter.value, 2)

    def test_new_year_catches_up_old_year(self):
        counter = EnergyCounter()
        counter.update(100, 2026)
        counter.update(2, 2027, previous_year=103)
        counter.update(2, 2027, previous_year=103)
        self.assertEqual(counter.value, 5)
        self.assertEqual(counter.year, 2027)

    def test_new_year_negative_correction_carries_debt(self):
        counter = EnergyCounter()
        counter.update(100, 2026)
        self.assertFalse(counter.update(1, 2027, previous_year=98))
        self.assertEqual(counter.value, 0)
        counter.update(3, 2027, previous_year=98)
        self.assertEqual(counter.value, 1)

    def test_missing_previous_year_and_multi_year_gap_do_not_guess(self):
        counter = EnergyCounter()
        counter.update(100, 2026)
        self.assertFalse(counter.update(2, 2027))
        self.assertFalse(counter.update(20, 2028, previous_year=500))
        self.assertEqual(counter.value, 0)

    def test_invalid_data_does_not_erase_baseline(self):
        counter = EnergyCounter()
        counter.update(100, 2026)
        self.assertFalse(counter.update(None, 2026))
        self.assertEqual(counter.source, 100)

    def test_verified_source_timestamp_survives_restart(self):
        from datetime import datetime
        counter = EnergyCounter(initial_value=0.077)
        self.assertTrue(counter.update_days({"2026-10-18": 0.2, "2026-10-19": 0.1}))
        counter.mark_verified_period(datetime(2026, 10, 19, 15, 52),
                                     date(2026, 10, 19))
        before = counter.as_dict()
        restored = EnergyCounter(before)
        self.assertEqual(restored.source_interval_end, "2026-10-19T14:00:00")
        self.assertEqual(restored.source_snapshot_date, "2026-10-19")
        self.assertEqual(restored.value, 0.077)
        # A failed verification leaves both the stored reading and its time.
        self.assertFalse(restored.update_days(None))
        self.assertEqual(restored.as_dict(), before)


if __name__ == "__main__":
    unittest.main()
