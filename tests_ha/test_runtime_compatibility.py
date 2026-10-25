"""Run with HA 2026.10.2 and Python 3.14, not mocked HA imports."""
import homeassistant
import asyncio
from copy import deepcopy
from datetime import datetime
import importlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from homeassistant.core import HomeAssistant, HassJobType
from homeassistant.helpers import frame
from homeassistant.util import dt as dt_util
from custom_components.ariston.accounting_runtime import AccountingRuntime
from custom_components.ariston.accounting_ledger import snapshot, merge, KEYS
from custom_components.ariston.coordinator import DeviceDataUpdateCoordinator
from ariston.ariston_api import ConnectionException

ROOT=Path(__file__).parents[1]

class Imports(unittest.TestCase):
    def test_every_integration_module_imports(self):
        for file in (ROOT/'custom_components/ariston').glob('*.py'):
            with self.subTest(module=file.stem):
                importlib.import_module('custom_components.ariston'+('' if file.stem=='__init__' else '.'+file.stem))

class RealRuntime(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.hass=HomeAssistant(self.tmp.name)
        frame.async_setup(self.hass)
        await self.hass.config.async_set_time_zone('Asia/Almaty')
        self.callbacks=[]
        self.entry=SimpleNamespace(unique_id='test-gateway',async_on_unload=self.callbacks.append)
        data=json.loads((ROOT/'tests/fixtures/energy_sources_oct21.json').read_text())
        self.observation=snapshot(data)
        self.ledger=merge({},self.observation,'2026-10-18','Asia/Almaty')
        self.entities={k:SimpleNamespace(entity_id='sensor.'+k,async_write_ha_state=lambda:None) for k in KEYS}
        self.config={'seed':self.ledger,'start_date':'2026-10-18'}
        self.runtime=AccountingRuntime(self.hass,self.entry,None,self.entities,self.config)
        await self.runtime.store.async_save({'ledger':self.ledger,'values':{k:1000.0 for k in KEYS}})
        await self.runtime.start()

    async def asyncTearDown(self):
        for callback in reversed(self.callbacks):callback()
        task=self.runtime.reconcile_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        await self.hass.async_stop(force=True)
        self.tmp.cleanup()

    async def test_periodic_job_runs_on_event_loop_repeatedly(self):
        from unittest.mock import AsyncMock
        timer=self.callbacks[0].__self__
        self.assertEqual(timer._run_job.job_type,HassJobType.Callback)
        with patch.object(self.runtime,'refresh',new_callable=AsyncMock) as refresh:
            for _ in range(2):
                timer._interval_listener(None)
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            self.assertEqual(refresh.await_count,2)

    async def test_coordinator_refresh_triggers_accounting_without_cloud_call(self):
        from unittest.mock import AsyncMock
        listeners=[]
        coordinator=SimpleNamespace(async_add_listener=lambda fn:(listeners.append(fn) or (lambda:None)))
        runtime=AccountingRuntime(self.hass,self.entry,coordinator,self.entities,self.config)
        await runtime.start()
        with patch.object(runtime,'refresh',new_callable=AsyncMock) as refresh:
            listeners[0]()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            refresh.assert_awaited_once()
        runtime.close()

    async def test_real_afternoon_report_advances_all_counters(self):
        data=json.loads((ROOT/'tests/fixtures/energy_sources_oct25_1657.json').read_text())
        observation=snapshot(data)
        lock=asyncio.Lock();await lock.acquire()
        self.hass.data['tariff_accounting_lock']=lock
        with patch('custom_components.ariston.diagnostics._report',return_value=data), \
             patch('custom_components.ariston.accounting_runtime.dt_util.now',return_value=datetime.fromisoformat(observation['at'])):
            await self.runtime.refresh()
        self.assertEqual(self.runtime.ledger['through'],'2026-10-25T16:00:00+05:00')
        self.assertGreater(self.runtime.values['gas_m3_heating'],1000)
        self.assertGreater(self.runtime.values['gas_m3_dhw'],1000)
        self.assertEqual((await self.runtime.store.async_load())['values'],self.runtime.values)
        lock.release()
        await self.runtime.reconcile_task

    async def test_real_store_reload_preserves_all_eight_values(self):
        second=AccountingRuntime(self.hass,self.entry,None,self.entities,self.config)
        await second.start()
        self.assertEqual(second.values,self.runtime.values)
        self.assertIs(second.publication_lock,self.runtime.publication_lock)
        second.close()

    async def test_real_store_and_ha_remain_responsive_with_tariff_lock(self):
        lock=asyncio.Lock();await lock.acquire()
        self.hass.data['tariff_accounting_lock']=lock
        observation=deepcopy(self.observation)
        day='2026-10-18'
        observation['days']['electricity_heating'][day]+=1
        with patch('custom_components.ariston.diagnostics._report',return_value={}), \
             patch('custom_components.ariston.accounting_runtime.snapshot',return_value=observation), \
             patch('custom_components.ariston.accounting_runtime.dt_util.now',return_value=datetime.fromisoformat(observation['at'])):
            await asyncio.wait_for(self.runtime.refresh(),2)
        self.assertFalse(self.runtime.busy)
        self.assertEqual(self.runtime.values['electricity_heating'],1001)
        saved=await self.runtime.store.async_load()
        self.assertEqual(saved['values'],self.runtime.values)
        await asyncio.sleep(0)
        self.assertTrue(self.hass.states.get('sensor.ariston_accounting_status'))
        # A background tariff wait must not hold HA's startup/drain barrier.
        await asyncio.wait_for(self.hass.async_block_till_done(),1)
        lock.release()
        await self.runtime.reconcile_task

    async def test_hung_cloud_request_times_out_and_recovers(self):
        gate=asyncio.Event()
        async def update():
            await gate.wait()
            return {'ok':True}
        c=DeviceDataUpdateCoordinator(self.hass,SimpleNamespace(name='test'),120,'energy',update)
        timeout=asyncio.timeout
        with patch('custom_components.ariston.coordinator.asyncio.timeout',side_effect=lambda _:timeout(.01)):
            await c.async_refresh()
        self.assertFalse(c.last_update_success)
        self.assertEqual(c.update_interval.total_seconds(),240)
        gate.set()
        await c.async_refresh()
        self.assertTrue(c.last_update_success)
        self.assertEqual(c.update_interval.total_seconds(),120)
        await c.async_shutdown()

    async def test_actual_coordinator_recovers_after_http_500(self):
        calls=0
        async def update():
            nonlocal calls
            calls+=1
            if calls==1:raise ConnectionException(500)
            return {'ok':True}
        c=DeviceDataUpdateCoordinator(self.hass,SimpleNamespace(name='test'),600,'test',update)
        await c.async_refresh()
        self.assertFalse(c.last_update_success)
        await c.async_refresh()
        self.assertTrue(c.last_update_success)
        self.assertEqual(c.data,{'ok':True})
        await c.async_shutdown()

    async def test_reload_waits_for_old_save_without_losing_latest_values(self):
        started=asyncio.Event();release=asyncio.Event()
        original=self.runtime.store.async_save
        async def slow_save(data):
            started.set();await release.wait();await original(data)
        observation=deepcopy(self.observation)
        observation['days']['electricity_heating']['2026-10-18']+=1
        with patch('custom_components.ariston.diagnostics._report',return_value={}), \
             patch('custom_components.ariston.accounting_runtime.snapshot',return_value=observation), \
             patch('custom_components.ariston.accounting_runtime.dt_util.now',return_value=datetime.fromisoformat(observation['at'])), \
             patch.object(self.runtime.store,'async_save',side_effect=slow_save):
            refreshing=asyncio.create_task(self.runtime.refresh())
            await started.wait()
            self.runtime.close()
            second=AccountingRuntime(self.hass,self.entry,None,self.entities,self.config)
            loading=asyncio.create_task(second.start())
            await asyncio.sleep(0)
            self.assertFalse(loading.done())
            release.set()
            await refreshing;await loading
        self.assertEqual(second.values['electricity_heating'],1001)
        self.assertIsNone(self.runtime.reconcile_task)
        second.close()

    async def test_real_number_entities_keep_legacy_unique_ids(self):
        from ariston.const import ThermostatProperties
        from custom_components.ariston.const import ARISTON_NUMBER_TYPES
        from custom_components.ariston.number import AristonNumber
        async def update():return {}
        coordinator=DeviceDataUpdateCoordinator(self.hass,SimpleNamespace(name='test',gateway='test-gateway'),600,'test',update)
        expected={ThermostatProperties.HEATING_FLOW_TEMP:'test-gateway-Ariston heating flow temperature-1',
                  ThermostatProperties.HEATING_FLOW_OFFSET:'test-gateway-Ariston heating flow offset-1'}
        for description in ARISTON_NUMBER_TYPES:
            if description.key in expected:
                entity=AristonNumber(coordinator,description,1)
                self.assertEqual(entity.unique_id,expected[description.key])
        await coordinator.async_shutdown()
