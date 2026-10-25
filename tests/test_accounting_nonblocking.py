"""Live readings must advance while tariff/Recorder work is blocked."""
import ast
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
import logging
from math import isfinite
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock
from test_accounting_ledger import DatedLedgerTests, ledger_module

source=(Path(__file__).parents[1]/'custom_components/ariston/accounting_runtime.py').read_text()
node=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='AccountingRuntime')
for method in node.body:
    if isinstance(method,ast.AsyncFunctionDef) and method.name=='refresh':
        for statement in ast.walk(method):
            if isinstance(statement,ast.Try):
                statement.body=[n for n in statement.body if not isinstance(n,ast.ImportFrom)]

class NonblockingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixture=DatedLedgerTests();fixture.setUp();self.ledger=fixture.make()
        self.tmp=tempfile.TemporaryDirectory()
        self.jobs=[];self.tasks=[];self.states=[]
        loop=asyncio.get_running_loop()
        class Task:
            def __init__(task,*args,**kwargs):task.future=loop.create_future();task.kwargs=kwargs
        def create(coro,*args,**kwargs):
            task=asyncio.create_task(coro);self.tasks.append(task);return task
        async def executor(fn):return fn()
        self.ns=dict(callback=lambda fn:fn,asyncio=asyncio,deepcopy=deepcopy,datetime=datetime,timedelta=timedelta,
            json=json,Path=Path,isfinite=isfinite,KEYS=ledger_module.KEYS,
            curves=ledger_module.curves,merge=lambda *args:deepcopy(self.next),
            snapshot=lambda d:{'at':datetime.now().astimezone().isoformat()},
            _report=lambda *args:{},dt_util=SimpleNamespace(now=lambda:datetime.now().astimezone()),
            _LOGGER=logging.getLogger(__name__),AccountingTask=Task,
            get_instance=lambda h:SimpleNamespace(queue_task=self.jobs.append))
        exec(compile(ast.Module(body=[deepcopy(node)],type_ignores=[]),'runtime','exec'),self.ns)
        r=self.r=object.__new__(self.ns['AccountingRuntime'])
        r.hass=SimpleNamespace(loop=loop,data={},async_create_task=create,async_create_background_task=create,async_add_executor_job=executor,
            config=SimpleNamespace(time_zone='Asia/Almaty',path=lambda p:self.tmp.name+'/'+p),
            states=SimpleNamespace(async_set=lambda *args:self.states.append(args),get=lambda entity_id:None))
        r.entry=object();r.config={'start_date':self.ledger['start_date']}
        r.ledger=deepcopy(self.ledger);r.values={k:1000.0 for k in ledger_module.KEYS}
        r.entities={k:SimpleNamespace(entity_id='sensor.'+k,async_write_ha_state=lambda:None) for k in ledger_module.KEYS}
        r.store=SimpleNamespace(async_save=AsyncMock());r.error=None;r.busy=False;r.closed=False
        r.retry_handle=None;r.reconcile_task=None;r.statistics_through=None
        r.publication_lock=asyncio.Lock()
        self.next=deepcopy(self.ledger)
        # Revise a historical day: all channels must consume the revised curve.
        day=sorted(self.next['days']['electricity_heating'])[0]
        self.next['days']['electricity_heating'][day]+=1

    async def asyncTearDown(self):
        for task in self.tasks:task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        self.tmp.cleanup()

    async def test_locked_tariff_does_not_block_live_refresh(self):
        lock=asyncio.Lock();await lock.acquire();self.r.hass.data['tariff_accounting_lock']=lock
        await asyncio.wait_for(self.r.refresh(),1)
        await asyncio.sleep(0)
        self.assertFalse(self.r.busy)
        self.assertEqual(self.r.values['electricity_heating'],1001)
        await asyncio.wait_for(self.r.refresh(),1)
        self.assertEqual(len(self.tasks),1)
        self.assertEqual(self.r.values['electricity_heating'],1001)

    async def test_pending_recorder_does_not_overwrite_newer_values(self):
        self.r.hass.data['energy']={'cost_sensors':{'sensor.gas_m3_heating':'sensor.cost_h','sensor.gas_m3_dhw':'sensor.cost_d'}}
        p=Path(self.tmp.name)/'tariff_cost_manager/history.json';p.parent.mkdir();p.write_text('{"gas": [[0,76]]}')
        await self.r.refresh();await asyncio.sleep(0)
        self.assertEqual(len(self.jobs),1)
        day=sorted(self.next['days']['electricity_heating'])[0];self.next['days']['electricity_heating'][day]+=1
        await asyncio.wait_for(self.r.refresh(),1)
        self.assertEqual(self.r.values['electricity_heating'],1002)
        self.assertEqual(len(self.jobs),1)
        self.jobs[0].future.set_result({'values':{k:0 for k in ledger_module.KEYS},'rows':0})
        await self.r.reconcile_task
        self.assertEqual(self.r.values['electricity_heating'],1002)
        self.assertEqual(self.states[-1][1],'statistics_pending')

    async def test_missing_cost_sensors_does_not_block_live(self):
        await self.r.refresh();await self.r.reconcile_task
        self.assertEqual(self.r.values['electricity_heating'],1001)
        self.assertIsNone(self.r.error)
        self.assertEqual(self.states[-1][1],'statistics_pending')

    async def test_pending_tariff_recovery_keeps_live_values(self):
        self.r.hass.data['energy']={'cost_sensors':{'sensor.gas_m3_heating':'sensor.cost_h','sensor.gas_m3_dhw':'sensor.cost_d'}}
        p=Path(self.tmp.name)/'tariff_cost_manager/pending_transaction.json';p.parent.mkdir();p.write_text('{}')
        await self.r.refresh();await self.r.reconcile_task
        self.assertEqual(self.r.values['electricity_heating'],1001)
        self.assertEqual(len(self.jobs),0)
        self.assertIn('recovery',self.states[-1][2]['error'])

    async def test_unloaded_waiter_never_enqueues_transaction(self):
        lock=asyncio.Lock();await lock.acquire();self.r.hass.data['tariff_accounting_lock']=lock
        await self.r.refresh();await asyncio.sleep(0)
        self.r.close();lock.release();await self.r.reconcile_task
        self.assertEqual(len(self.jobs),0)
