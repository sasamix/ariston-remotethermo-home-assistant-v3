"""Exercise the real transaction planner against a transactional SQL database."""
import ast
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from sqlalchemy import create_engine,Column,Integer,Float,String,Boolean,UniqueConstraint,select,func
from sqlalchemy.orm import declarative_base,sessionmaker
from test_accounting_ledger import DatedLedgerTests,ledger_module

Base=declarative_base()
class Meta(Base):
    __tablename__='statistics_meta'
    id=Column(Integer,primary_key=True)
    statistic_id=Column(String,unique=True)
    has_sum=Column(Boolean)
    unit_of_measurement=Column(String)
class Fields:
    id=Column(Integer,primary_key=True)
    metadata_id=Column(Integer)
    start_ts=Column(Float)
    sum=Column(Float)
    state=Column(Float)
    last_reset_ts=Column(Float)
class Hour(Fields,Base):
    __tablename__='statistics'
    __table_args__=(UniqueConstraint('metadata_id','start_ts'),)
class Short(Fields,Base):
    __tablename__='statistics_short_term'
    __table_args__=(UniqueConstraint('metadata_id','start_ts'),)

@contextmanager
def scope(session):
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback();raise
    finally:session.close()

source=(Path(__file__).parents[1]/'custom_components/ariston/accounting_runtime.py').read_text()
namespace={'select':select,'func':func,'Statistics':Hour,'StatisticsShortTerm':Short,
           'StatisticsMeta':Meta,'RecorderTask':object,'datetime':datetime,'Path':Path,
           'ZoneInfo':__import__('zoneinfo').ZoneInfo,'isfinite':__import__('math').isfinite,
           'json':json,'os':__import__('os'),'session_scope':scope,
           'KEYS':ledger_module.KEYS,'UNITS':ledger_module.UNITS,'curves':ledger_module.curves,
           'time':SimpleNamespace(time=lambda:datetime.fromisoformat('2026-10-21T08:00:00+05:00').timestamp())}
nodes=[n for n in ast.parse(source).body if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name in ('AccountingTask','atomic_json','numeric')]
exec(compile(ast.Module(body=nodes,type_ignores=[]),'accounting_runtime.py','exec'),namespace)
Task=namespace['AccountingTask']

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        fixture=DatedLedgerTests();fixture.setUp();self.ledger=fixture.make()
        self.tmp=tempfile.TemporaryDirectory()
        self.engine=create_engine('sqlite:///'+self.tmp.name+'/recorder.db')
        Base.metadata.create_all(self.engine)
        self.sessions=sessionmaker(bind=self.engine)
        self.instance=SimpleNamespace(get_session=self.sessions)
        self.task=object.__new__(Task)
        self.task.ledger=self.ledger
        self.task.mapping={key:'sensor.'+key for key in ledger_module.KEYS}
        self.task.costmap={'sensor.gas_m3_heating':'sensor.heat_cost','sensor.gas_m3_dhw':'sensor.dhw_cost'}
        self.task.tariffs={'gas':[[0,76]]}
        self.task.dry_run=False
        self.task.runtime=SimpleNamespace(hass=SimpleNamespace(config=SimpleNamespace(path=lambda p:self.tmp.name+'/'+p)))
        self.origin=int(datetime.fromisoformat('2026-10-18T00:00:00+05:00').timestamp())
        with scope(self.sessions()) as s:
            for i,(key,entity) in enumerate(self.task.mapping.items(),1):
                s.add(Meta(id=i,statistic_id=entity,has_sum=True,unit_of_measurement=ledger_module.UNITS[key]))
                s.add(Hour(metadata_id=i,start_ts=self.origin-3600,sum=100,state=50,last_reset_ts=0))
                s.add(Hour(metadata_id=i,start_ts=self.origin,sum=9999,state=9999,last_reset_ts=0))
                s.add(Short(metadata_id=i,start_ts=self.origin,sum=9999,state=9999,last_reset_ts=0))
            for i,entity in enumerate(self.task.costmap.values(),9):
                s.add(Meta(id=i,statistic_id=entity,has_sum=True,unit_of_measurement='KZT'))
                s.add(Hour(metadata_id=i,start_ts=self.origin-3600,sum=1000,state=500,last_reset_ts=0))

    def tearDown(self):
        self.engine.dispose();self.tmp.cleanup()

    def test_transaction_all_channels_daily_totals_costs_and_idempotence(self):
        result=self.task.execute(self.instance)
        self.assertGreater(result['rows'],100)
        self.assertTrue(Path(result['backup']).is_file())
        lines,_=ledger_module.curves(self.ledger)
        with scope(self.sessions()) as s:
            for i,key in enumerate(ledger_module.KEYS,1):
                before=s.scalar(select(Hour).where(Hour.metadata_id==i,Hour.start_ts==self.origin-3600))
                self.assertEqual(before.sum,100);self.assertEqual(before.state,50)
                for day in range(3):
                    end=self.origin+86400*(day+1)
                    a=s.scalar(select(Hour).where(Hour.metadata_id==i,Hour.start_ts==end-3600))
                    b=s.scalar(select(Short).where(Short.metadata_id==i,Short.start_ts==end-300))
                    self.assertAlmostEqual(a.sum,100+lines[key].at(end),places=7)
                    self.assertEqual(a.sum,b.sum);self.assertEqual(a.state,b.state)
            cost=s.scalar(select(Hour).where(Hour.metadata_id==9,Hour.start_ts==self.origin+86400*3-3600))
            self.assertAlmostEqual(cost.sum,1000+(1.44+5.87+10)*76)
        self.assertEqual(self.task.execute(self.instance)['rows'],0)

    def test_invalid_last_metadata_rolls_back_everything(self):
        with scope(self.sessions()) as s:s.get(Meta,10).unit_of_measurement='USD'
        with self.assertRaises(ValueError):self.task.execute(self.instance)
        with scope(self.sessions()) as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(Hour)),18)
            self.assertEqual(s.scalar(select(Hour.sum).where(Hour.metadata_id==1,Hour.start_ts==self.origin)),9999)

    def test_dry_run_does_not_write(self):
        self.task.dry_run=True
        self.assertGreater(self.task.execute(self.instance)['rows'],0)
        with scope(self.sessions()) as s:self.assertEqual(s.scalar(select(func.count()).select_from(Hour)),18)

    def test_unconfirmed_future_rows_are_never_flattened(self):
        through=int(datetime.fromisoformat(self.ledger['through']).timestamp())
        with scope(self.sessions()) as session:
            session.add(Hour(metadata_id=1,start_ts=through,sum=12345,state=12345,last_reset_ts=0))
            session.add(Short(metadata_id=1,start_ts=through,sum=12345,state=12345,last_reset_ts=0))
        self.task.execute(self.instance)
        with scope(self.sessions()) as session:
            for model in (Hour,Short):
                row=session.scalar(select(model).where(model.metadata_id==1,model.start_ts==through))
                self.assertEqual(row.sum,12345)
                self.assertEqual(row.state,12345)
                # No speculative rows are inserted after the confirmed boundary.
                self.assertEqual(session.scalar(select(func.count()).select_from(model).where(model.start_ts>through)),0)
