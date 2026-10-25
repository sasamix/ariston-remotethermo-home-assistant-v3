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
from custom_components.ariston.accounting_runtime import AccountingTask
from homeassistant.components.recorder.statistics import get_short_term_statistics_run_cache
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
            class Hass:
                pass
            hass=Hass()
            hass.data={}
            hass.loop=SimpleNamespace(call_soon_threadsafe=lambda fn,*args:fn(*args))
            task.runtime.hass.data=hass.data
            task.runtime.hass.loop=hass.loop
            instance=SimpleNamespace(get_session=sessions,hass=hass)
            cache=get_short_term_statistics_run_cache(hass)
            with sessions() as session:
                old=session.scalars(select(StatisticsShortTerm).where(StatisticsShortTerm.metadata_id==1)).first()
                cache.set_latest_id_for_metadata_id(1,old.id)
            result=task.execute(instance)
            self.assertGreater(result['rows'],0)
            self.assertEqual(set(result['values']),set(KEYS))
            self.assertEqual(task.execute(instance)['rows'],0)
            self.assertTrue(Path(result['backup']).is_file())
            outcomes=[]
            task.finish=lambda result,error:outcomes.append((result,error))
            task.run(instance)
            self.assertIsNone(outcomes[0][1])
            with sessions() as session:
                latest=session.scalars(select(StatisticsShortTerm).where(StatisticsShortTerm.metadata_id==1).order_by(StatisticsShortTerm.start_ts.desc())).first()
                self.assertEqual(cache.get_latest_ids({1})[1],latest.id)
            engine.dispose()
