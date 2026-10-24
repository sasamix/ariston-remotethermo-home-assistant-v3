"""One-off repair of statistics before the dated-accounting start (real SQL)."""
import ast
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from test_accounting_database import Base, Meta, Hour, Short
from test_accounting_ledger import ledger_module

ZONE = 'Asia/Qyzylorda'
TZ = ZoneInfo(ZONE)
KEYS = ledger_module.KEYS
TARIFF = 76.0


@contextmanager
def scope(session):
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


source = (Path(__file__).parents[1]/'custom_components/ariston/accounting_runtime.py').read_text()
namespace = {'select': select, 'func': func, 'Statistics': Hour, 'StatisticsShortTerm': Short,
             'StatisticsMeta': Meta, 'RecorderTask': object, 'datetime': datetime, 'Path': Path,
             'ZoneInfo': ZoneInfo, 'isfinite': __import__('math').isfinite,
             'json': json, 'os': __import__('os'), 'session_scope': scope,
             'KEYS': KEYS, 'UNITS': ledger_module.UNITS, 'curves': ledger_module.curves,
             'time': SimpleNamespace(time=lambda: datetime(2026, 10, 24, 11, tzinfo=TZ).timestamp())}
nodes = [n for n in ast.parse(source).body if isinstance(n, (ast.ClassDef, ast.FunctionDef))
         and n.name in ('AccountingTask', 'HistoryRepairTask', 'atomic_json', 'numeric')]
exec(compile(ast.Module(body=nodes, type_ignores=[]), 'accounting_runtime.py', 'exec'), namespace)
Task = namespace['HistoryRepairTask']


def ts(*args):
    return int(datetime(*args, tzinfo=TZ).timestamp())


DAYS = ['2026-10-15', '2026-10-16', '2026-10-17']
GAS_M3 = {'heating': [11.0, 14.0, 6.09], 'dhw': [0.0, 2.0, 2.16]}
ELEC = {'heating': [0.2, 0.3, 0.1], 'dhw': [0.0, 0.05, 0.02]}


def window_days():
    days = {}
    for suffix in ('heating', 'dhw'):
        gas = GAS_M3[suffix]
        days['gas_m3_'+suffix] = dict(zip(DAYS, gas))
        days['gas_kwh_'+suffix] = dict(zip(DAYS, [round(v*10.6, 6) for v in gas]))
        days['electricity_'+suffix] = dict(zip(DAYS, ELEC[suffix]))
        days['total_'+suffix] = {d: round(days['gas_kwh_'+suffix][d]+days['electricity_'+suffix][d], 6) for d in DAYS}
    return days


class HistoryRepairTests(unittest.TestCase):
    start = ts(2026, 10, 15)
    origin = ts(2026, 10, 18)
    last = ts(2026, 10, 18, 6)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = create_engine('sqlite:///'+self.tmp.name+'/recorder.db')
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.mapping = {key: 'sensor.'+key for key in KEYS}
        self.costmap = {'sensor.gas_m3_heating': 'sensor.heat_cost', 'sensor.gas_m3_dhw': 'sensor.dhw_cost'}
        self.ids = {}
        with scope(self.sessions()) as s:
            stats = [(e, ledger_module.UNITS[k]) for k, e in self.mapping.items()]
            stats += [(c, 'KZT') for c in self.costmap.values()]
            for i, (entity, unit) in enumerate(stats, 1):
                self.ids[entity] = i
                s.add(Meta(id=i, statistic_id=entity, has_sum=True, unit_of_measurement=unit))
                # Old, wrongly dated history: 0.5 per even hour, then 0.3/hour after origin.
                total, state = 1000.0 + i, 50.0 + i
                for start in range(self.start-3600, self.last, 3600):
                    if start >= self.start:
                        step = 0.3 if start >= self.origin else (0.5 if (start//3600) % 2 == 0 else 0.0)
                        total, state = total+step, state+step
                    s.add(Hour(metadata_id=i, start_ts=start, sum=round(total, 6), state=round(state, 6), last_reset_ts=0))
                stotal = 5000.0 + i
                for start in range(self.origin-3600, self.last, 300):
                    stotal += 0.025
                    s.add(Short(metadata_id=i, start_ts=start, sum=round(stotal, 6), state=1.0, last_reset_ts=0))
        self.before_hours = self.rows(Hour)
        self.before_short = self.rows(Short)

    def rows(self, model):
        with scope(self.sessions()) as s:
            return {(r.metadata_id, int(r.start_ts)): (r.sum, r.state, r.last_reset_ts)
                    for r in s.execute(select(model)).scalars()}

    def task(self, dry_run=False, days=None):
        task = object.__new__(Task)
        task.ledger = {'start_date': DAYS[0], 'timezone': ZONE, 'at': '2026-10-24T10:48:00+05:00',
                       'through': datetime.fromtimestamp(self.origin, TZ).isoformat(),
                       'days': days or window_days(), 'profiles': {}}
        task.origin = self.origin
        task.mapping, task.costmap = self.mapping, self.costmap
        task.tariffs = {'gas': [[0, TARIFF]]}
        task.dry_run = dry_run
        task.runtime = SimpleNamespace(hass=SimpleNamespace(config=SimpleNamespace(path=lambda p: self.tmp.name+'/'+p)))
        return task

    def run_task(self, **kw):
        return self.task(**kw).execute(SimpleNamespace(get_session=self.sessions))

    def daily_change(self, hours, mid, day):
        begin = ts(*map(int, day.split('-')))
        return round(hours[(mid, begin+23*3600)][0] - hours[(mid, begin-3600)][0], 6)

    def test_dry_run_reports_without_writing(self):
        report = self.run_task(dry_run=True)
        self.assertTrue(report['dry_run'])
        self.assertGreater(report['rows'], 0)
        self.assertEqual(self.rows(Hour), self.before_hours)
        self.assertEqual(self.rows(Short), self.before_short)
        self.assertFalse((Path(self.tmp.name)/'ariston_accounting/backups').exists())

    def test_window_days_match_reports_and_later_changes_are_preserved(self):
        report = self.run_task()
        hours, shorts = self.rows(Hour), self.rows(Short)
        days = window_days()
        for key in KEYS:
            mid = self.ids[self.mapping[key]]
            for day in DAYS:
                self.assertAlmostEqual(self.daily_change(hours, mid, day), days[key][day], places=6, msg=(key, day))
        for suffix in ('heating', 'dhw'):
            mid = self.ids[self.costmap['sensor.gas_m3_'+suffix]]
            for day in DAYS:
                self.assertAlmostEqual(self.daily_change(hours, mid, day),
                                       round(days['gas_m3_'+suffix][day]*TARIFF, 6), places=5)
        for mid in self.ids.values():
            # Recorded meter states and reset markers are untouched everywhere.
            for start in range(self.start-3600, self.last, 3600):
                self.assertEqual(hours[(mid, start)][1:], self.before_hours[(mid, start)][1:])
            # After the window every hourly and five-minute change is unchanged.
            for table, before, step in ((hours, self.before_hours, 3600), (shorts, self.before_short, 300)):
                starts = sorted(t for m, t in before if m == mid and t >= self.origin)
                for a, b in zip(starts, starts[1:]):
                    self.assertAlmostEqual(table[(mid, b)][0]-table[(mid, a)][0],
                                           before[(mid, b)][0]-before[(mid, a)][0], places=6)
            # Continuity at the boundary: first post-window hour keeps its change.
            self.assertAlmostEqual(hours[(mid, self.origin)][0]-hours[(mid, self.origin-3600)][0],
                                   self.before_hours[(mid, self.origin)][0]-self.before_hours[(mid, self.origin-3600)][0], places=6)
            # The anchor hour before the window is never modified.
            self.assertEqual(hours[(mid, self.start-3600)], self.before_hours[(mid, self.start-3600)])
        backup = json.loads(Path(report['backup']).read_text())
        self.assertEqual(len(backup['rows']), report['rows'])
        for row in backup['rows']:
            model = self.before_hours if row['table'] == 'statistics' else self.before_short
            self.assertEqual(row['before']['sum'], model[(row['metadata_id'], int(row['start_ts']))][0])
        summary = report['statistics']['sensor.gas_m3_heating']
        self.assertAlmostEqual(summary['window_after'], sum(GAS_M3['heating']), places=6)
        self.assertAlmostEqual(summary['window_before'], 0.5*36, places=6)

    def test_combined_energy_equals_components_every_hour(self):
        self.run_task()
        hours = self.rows(Hour)
        for suffix in ('heating', 'dhw'):
            t, g, e = (self.ids[self.mapping[k+suffix]] for k in ('total_', 'gas_kwh_', 'electricity_'))
            for start in range(self.start, self.origin, 3600):
                change = lambda mid: hours[(mid, start)][0]-hours[(mid, start-3600)][0]
                self.assertAlmostEqual(change(t), change(g)+change(e), places=6)

    def test_second_run_is_a_no_op(self):
        self.run_task()
        after = (self.rows(Hour), self.rows(Short))
        report = self.run_task()
        self.assertEqual(report['rows'], 0)
        self.assertEqual((self.rows(Hour), self.rows(Short)), after)

    def test_missing_hour_inside_window_is_filled(self):
        mid = self.ids['sensor.gas_m3_dhw']
        gap = ts(2026, 10, 16, 5)
        with scope(self.sessions()) as s:
            s.delete(s.scalar(select(Hour).where(Hour.metadata_id == mid, Hour.start_ts == gap)))
        self.run_task()
        hours = self.rows(Hour)
        self.assertIn((mid, gap), hours)
        self.assertIsNone(hours[(mid, gap)][1])
        self.assertAlmostEqual(self.daily_change(hours, mid, '2026-10-16'), 2.0, places=6)

    def test_missing_anchor_or_report_day_changes_nothing(self):
        mid = self.ids['sensor.electricity_dhw']
        with scope(self.sessions()) as s:
            s.delete(s.scalar(select(Hour).where(Hour.metadata_id == mid, Hour.start_ts == self.start-3600)))
        before = self.rows(Hour)
        with self.assertRaisesRegex(ValueError, 'Missing hour row'):
            self.run_task()
        self.assertEqual(self.rows(Hour), before)
        days = window_days()
        del days['gas_m3_heating']['2026-10-16']
        with self.assertRaisesRegex(ValueError, 'Missing dated source'):
            self.run_task(days=days)
        self.assertEqual(self.rows(Hour), before)

    def test_tariff_missing_for_window_is_rejected(self):
        task = self.task()
        task.tariffs = {'gas': [[ts(2026, 10, 16), TARIFF]]}
        with self.assertRaisesRegex(ValueError, 'Tariff not defined'):
            task.execute(SimpleNamespace(get_session=self.sessions))
        self.assertEqual(self.rows(Hour), self.before_hours)


if __name__ == '__main__':
    unittest.main()


class RepairThenReconcileTests(unittest.TestCase):
    """After the repair, the regular accounting transaction finds nothing to change."""

    def test_reconciliation_is_consistent_after_repair(self):
        from test_accounting_database import DatabaseTests
        db = DatabaseTests('test_dry_run_does_not_write')
        db.setUp()
        self.addCleanup(db.tearDown)
        instance = db.instance
        self.assertGreater(db.task.execute(instance)['rows'], 0)
        zone = ZoneInfo(db.ledger['timezone'])
        start = db.origin - 86400
        with scope(db.sessions()) as s:
            for i in range(1, 11):
                anchor = s.scalar(select(Hour).where(Hour.metadata_id == i, Hour.start_ts == db.origin-3600))
                # Old history for the day before the accounting start: lumpy and wrong.
                total = anchor.sum - 12.0
                s.add(Hour(metadata_id=i, start_ts=start-3600, sum=total, state=1.0, last_reset_ts=0))
                for t in range(start, db.origin-3600, 3600):
                    total += 0.5
                    s.add(Hour(metadata_id=i, start_ts=t, sum=total, state=1.0, last_reset_ts=0))
        day = datetime.fromtimestamp(start, zone).date().isoformat()
        days = {}
        for suffix, gas, elec in (('heating', 6.09, 0.1), ('dhw', 2.16, 0.02)):
            days['gas_m3_'+suffix] = {day: gas}
            days['gas_kwh_'+suffix] = {day: round(gas*10.6, 6)}
            days['electricity_'+suffix] = {day: elec}
            days['total_'+suffix] = {day: round(gas*10.6+elec, 6)}
        repair = object.__new__(Task)
        repair.ledger = {'start_date': day, 'timezone': db.ledger['timezone'], 'at': db.ledger['at'],
                         'through': datetime.fromtimestamp(db.origin, zone).isoformat(),
                         'days': days, 'profiles': {}}
        repair.origin, repair.mapping, repair.costmap = db.origin, db.task.mapping, db.task.costmap
        repair.tariffs, repair.dry_run, repair.runtime = db.task.tariffs, False, db.task.runtime
        report = repair.execute(instance)
        self.assertGreater(report['rows'], 0)
        self.assertNotEqual(report['statistics']['sensor.gas_m3_heating']['shift_after_window'], 0)
        # The ordinary reconciliation, anchored at the repaired boundary, is a no-op.
        self.assertEqual(db.task.execute(instance)['rows'], 0)
