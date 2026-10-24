"""Use HA's actual Recorder SQL models and transaction implementation."""
import homeassistant
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from homeassistant.components.recorder.db_schema import Base, Statistics, StatisticsShortTerm, StatisticsMeta
from custom_components.ariston.accounting_runtime import AccountingTask, HistoryRepairTask
from custom_components.ariston.accounting_ledger import snapshot, merge, KEYS, UNITS

class RecorderSchemaTests(unittest.TestCase):
    def test_full_transaction_matches_real_schema_and_is_idempotent(self):
        fixture=Path(__file__).parents[1]/'tests/fixtures/energy_sources_oct21.json'
        ledger=merge({},snapshot(json.loads(fixture.read_text())),'2026-10-18','Asia/Almaty')
        origin=int(datetime.fromisoformat('2026-10-18T00:00:00+05:00').timestamp())
        with tempfile.TemporaryDirectory() as tmp:
            engine=create_engine('sqlite:///'+tmp+'/recorder.db')
            Base.metadata.create_all(engine)
            sessions=sessionmaker(bind=engine)
            task=object.__new__(AccountingTask)
            task.ledger=ledger;task.mapping={k:'sensor.'+k for k in KEYS}
            task.costmap={'sensor.gas_m3_heating':'sensor.cost_h','sensor.gas_m3_dhw':'sensor.cost_d'}
            task.tariffs={'gas':[[0,76]]};task.dry_run=False
            task.runtime=SimpleNamespace(hass=SimpleNamespace(config=SimpleNamespace(path=lambda p:tmp+'/'+p)))
            with sessions.begin() as session:
                for i,(key,entity) in enumerate(task.mapping.items(),1):
                    session.add(StatisticsMeta(id=i,statistic_id=entity,source='recorder',unit_of_measurement=UNITS[key],has_mean=False,has_sum=True))
                    session.add(Statistics(metadata_id=i,start_ts=origin-3600,sum=100,state=50,last_reset_ts=0))
                    session.add(StatisticsShortTerm(metadata_id=i,start_ts=origin,sum=999,state=999,last_reset_ts=0))
                for i,entity in enumerate(task.costmap.values(),9):
                    session.add(StatisticsMeta(id=i,statistic_id=entity,source='recorder',unit_of_measurement='KZT',has_mean=False,has_sum=True))
                    session.add(Statistics(metadata_id=i,start_ts=origin-3600,sum=1000,state=500,last_reset_ts=0))
            instance=SimpleNamespace(get_session=sessions)
            result=task.execute(instance)
            self.assertGreater(result['rows'],0)
            self.assertEqual(set(result['values']),set(KEYS))
            self.assertEqual(task.execute(instance)['rows'],0)
            self.assertTrue(Path(result['backup']).is_file())
            # One-off repair of the day before the accounting start, then the
            # ordinary transaction must find nothing left to change.
            day_start=origin-86400
            with sessions.begin() as session:
                for i in range(1,11):
                    session.add(Statistics(metadata_id=i,start_ts=day_start-3600,sum=88,state=1,last_reset_ts=0))
                    for n,t in enumerate(range(day_start,origin-3600,3600),1):
                        session.add(Statistics(metadata_id=i,start_ts=t,sum=88+n*0.5,state=1,last_reset_ts=0))
            days={}
            for suffix,gas,elec in (('heating',6.09,0.1),('dhw',2.16,0.02)):
                days['gas_m3_'+suffix]={'2026-10-17':gas}
                days['gas_kwh_'+suffix]={'2026-10-17':round(gas*10.6,6)}
                days['electricity_'+suffix]={'2026-10-17':elec}
                days['total_'+suffix]={'2026-10-17':round(gas*10.6+elec,6)}
            repair=object.__new__(HistoryRepairTask)
            repair.ledger={'start_date':'2026-10-17','timezone':ledger['timezone'],'at':ledger['at'],
                           'through':'2026-10-18T00:00:00+05:00','days':days,'profiles':{}}
            repair.origin=origin;repair.mapping=task.mapping;repair.costmap=task.costmap
            repair.tariffs=task.tariffs;repair.dry_run=False;repair.runtime=task.runtime
            report=repair.execute(instance)
            self.assertGreater(report['rows'],0)
            self.assertEqual(repair.execute(instance)['rows'],0)
            self.assertEqual(task.execute(instance)['rows'],0)
            engine.dispose()
