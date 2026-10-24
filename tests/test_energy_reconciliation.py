"""Regression cases from the Oct 21 cloud report. No HA or DB dependency."""
from copy import deepcopy
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType('_ariston_reconciliation_test')
pkg.__path__ = [str(ROOT / 'custom_components/ariston')]
sys.modules[pkg.__name__] = pkg
for name in ('energy_counter', 'energy_reconciliation'):
    spec = importlib.util.spec_from_file_location(pkg.__name__ + '.' + name, Path(pkg.__path__[0]) / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / 'tests/fixtures/energy_sources_oct21.json').read_text())

    def test_all_eight_channels_and_conservation(self):
        sources = mod.verified_daily_sources(self.data)
        self.assertEqual(len(sources), 8)
        for suffix in ('heating', 'dhw'):
            gas, electric, total = [sources[mod.ENTITIES[k + '_' + suffix]] for k in ('gas_kwh', 'electricity', 'total')]
            for day in gas:
                self.assertAlmostEqual(gas[day] + electric[day], total[day], places=5)
        self.assertEqual(sources[mod.ENTITIES['gas_kwh_heating']]['2026-10-19'], 61.644)
        self.assertEqual(sources[mod.ENTITIES['gas_m3_heating']]['2026-10-20'], 10)
        self.assertEqual(sources[mod.ENTITIES['gas_kwh_dhw']]['2026-10-19'], 7.25)
        self.assertEqual(sources[mod.ENTITIES['electricity_dhw']]['2026-10-19'], .03)
        self.assertEqual(sources[mod.ENTITIES['gas_m3_dhw']]['2026-10-20'], 0)
        self.assertTrue(all('2026-10-21' not in days for days in sources.values()))

    def test_mismatched_boundary_is_rejected(self):
        self.data['source_timestamps']['energy_sequences_time'] = '2026-10-21T05:59:00+05:00'
        with self.assertRaises(ValueError):
            mod.verified_daily_sources(self.data)

    def test_inconsistent_electricity_total_is_rejected(self):
        self.data['r2_raw_boiler_electricity'][1]['value'] += 1
        with self.assertRaises(ValueError):
            mod.verified_daily_sources(self.data)

    def test_missing_physical_day_is_rejected(self):
        row = next(r for r in self.data['r2_as_physical_unit_histogram']
                   if r['period'] == 'CurrentMonth' and r['series'] == 'Heating')
        row['items'].pop(0)
        with self.assertRaises(ValueError):
            mod.verified_daily_sources(self.data)

    def test_utc_plus_five_day_and_incomplete_rows(self):
        entity = mod.ENTITIES['gas_kwh_heating']
        begin = datetime.fromisoformat('2026-10-19T00:00:00+05:00')
        rows = [{'start': (begin + timedelta(hours=i)).timestamp() * 1000,
                 'end': (begin + timedelta(hours=i+1)).timestamp() * 1000,
                 'change': 1.0} for i in range(24)]
        sources = {entity: {'2026-10-19': 61.644}}
        def report(values):
            return mod.compare_statistics(sources, {entity: values}, 'Asia/Almaty')[0]
        self.assertEqual(report(rows)['ha'], 24)
        self.assertAlmostEqual(report(rows)['difference'], -37.644)
        for broken in (rows[:-1], rows[:-1]+[rows[0]], deepcopy(rows)):
            if len(broken) == 24 and broken[-1] != rows[0]:
                broken[-1]['change'] = None
            self.assertEqual(report(broken)['status'], 'incomplete_ha_day')
            self.assertIsNone(report(broken)['difference'])


if __name__ == '__main__':
    unittest.main()
