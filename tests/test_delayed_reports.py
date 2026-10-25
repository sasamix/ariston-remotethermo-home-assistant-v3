"""Exercise delayed and revised reports through real reconciliation SQL.

The actual 21:01 report contains 0.01/0.00 m³ for 18–20, not missing rows.
Later report revisions below are synthetic and do not predict cloud readings.
"""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import select
import test_accounting_database as database
from test_accounting_database import Hour, Short, scope, namespace
from test_accounting_ledger import ledger_module

class DelayedReportTests(unittest.TestCase):
    def setUp(self):
        self.db=database.DatabaseTests('test_dry_run_does_not_write')
        self.db.setUp();self.addCleanup(self.db.tearDown)
        self.task=self.db.task
        source=json.loads((Path(__file__).parent/'fixtures/energy_sources_oct25_2101.json').read_text())
        self.observation=ledger_module.snapshot(source)
        self.task.ledger=ledger_module.merge({},self.observation,'2026-10-18','Asia/Almaty')
        self.now=datetime.fromisoformat('2026-10-25T21:03:00+05:00').timestamp()
        patched=patch.object(namespace['time'],'time',return_value=self.now)
        patched.start();self.addCleanup(patched.stop)
        self.keys=list(ledger_module.KEYS)

    def hour(self,stamp):
        return datetime.fromisoformat(stamp).timestamp()

    def readings(self,key,left,right,cost=False):
        mid=self.keys.index(key)+1
        if cost:mid=9 if key.endswith('heating') else 10
        result=[]
        with scope(self.db.sessions()) as session:
            for model,step in ((Hour,3600),(Short,300)):
                a=session.scalar(select(model).where(model.metadata_id==mid,model.start_ts==left-step))
                b=session.scalar(select(model).where(model.metadata_id==mid,model.start_ts==right-step))
                result.append(b.sum-a.sum)
        return result

    def test_actual_report_zero_is_not_missing_or_a_stopped_timer(self):
        morning=json.loads((Path(__file__).parent/'fixtures/energy_sources_oct25_1818.json').read_text())
        before=ledger_module.snapshot(morning)
        self.assertEqual(self.observation['through'],'2026-10-25T20:00:00+05:00')
        self.assertAlmostEqual(self.observation['meter_totals']['gas_m3_heating']-before['meter_totals']['gas_m3_heating'],.01)
        self.assertAlmostEqual(self.observation['meter_totals']['gas_m3_dhw']-before['meter_totals']['gas_m3_dhw'],0)
        self.task.execute(self.db.instance)
        left=self.hour('2026-10-25T18:00:00+05:00');right=left+7200
        for key,expected in [('gas_m3_heating',.01),('gas_m3_dhw',0)]:
            for amount in self.readings(key,left,right):self.assertAlmostEqual(amount,expected)
        self.assertEqual(self.task.execute(self.db.instance)['rows'],0)

    def test_delayed_interval_revision_changes_original_hours_and_tariff_period(self):
        left=self.hour('2026-10-25T18:00:00+05:00');right=left+7200
        self.task.tariffs={'gas':[[0,76],[left+3600,100]]}
        self.task.execute(self.db.instance)
        # Recorder has compiled held live readings during 20–21 while the
        # 18–20 report was still preliminary. Reconciliation must rebase them.
        with scope(self.db.sessions()) as session:
            for mid in range(1,11):
                for model,step in ((Hour,3600),(Short,300)):
                    anchor=session.scalar(select(model).where(model.metadata_id==mid,model.start_ts==right-step))
                    for start in range(int(right),int(right+3600),step):
                        session.add(model(metadata_id=mid,start_ts=start,sum=anchor.sum,
                                          state=anchor.state,last_reset_ts=anchor.last_reset_ts))
        revised=deepcopy(self.observation);revised['at']='2026-10-25T21:05:00+05:00'
        # Hypothetical delayed correction, not an observation of the boiler.
        additions={'gas_m3_heating':2,'gas_m3_dhw':.4,
                   'gas_kwh_heating':21,'gas_kwh_dhw':4,
                   'electricity_heating':.3,'electricity_dhw':.02,
                   'total_heating':21.3,'total_dhw':4.02}
        for key,extra in additions.items():
            revised['days'][key]['2026-10-25']+=extra
            revised['profiles'][key]['2026-10-25'][9]+=extra
        self.task.ledger=ledger_module.merge(self.task.ledger,revised,'2026-10-18','Asia/Almaty')
        self.task.execute(self.db.instance)
        for key,expected in [('gas_m3_heating',2.01),('gas_m3_dhw',.4)]:
            for amount in self.readings(key,left,right):self.assertAlmostEqual(amount,expected)
            for cost in self.readings(key,left,right,cost=True):self.assertAlmostEqual(cost,expected*88)
        # No correction is charged in the hour it arrived (20–21).
        for key in ('gas_m3_heating','gas_m3_dhw'):
            for amount in self.readings(key,right,right+3600):self.assertAlmostEqual(amount,0)
        self.assertEqual(self.task.execute(self.db.instance)['rows'],0)

    def test_next_day_calendar_revision_does_not_create_midnight_consumption(self):
        self.task.execute(self.db.instance)
        revised=deepcopy(self.observation)
        revised['at']='2026-10-26T02:05:00+05:00';revised['through']='2026-10-26T02:00:00+05:00'
        for key in ledger_module.KEYS:
            revised['days'][key]['2026-10-25']*=.9
            revised['days'][key]['2026-10-26']=0
        self.now=self.hour('2026-10-26T02:06:00+05:00')
        namespace['time'].time.return_value=self.now
        self.task.ledger=ledger_module.merge(self.task.ledger,revised,'2026-10-18','Asia/Almaty')
        self.task.execute(self.db.instance)
        left=self.hour('2026-10-25T00:00:00+05:00');right=left+86400
        for key in ('gas_m3_heating','gas_m3_dhw'):
            expected=revised['days'][key]['2026-10-25']
            for amount in self.readings(key,left,right):self.assertAlmostEqual(amount,expected)
            for amount in self.readings(key,right,right+7200):self.assertAlmostEqual(amount,0)
        self.assertEqual(self.task.execute(self.db.instance)['rows'],0)
