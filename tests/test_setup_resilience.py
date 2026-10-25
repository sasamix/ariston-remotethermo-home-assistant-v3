"""Execute the real setup flow with independently failing HTTP coordinators."""
import ast
import asyncio
from copy import deepcopy
from datetime import timedelta
import logging
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

ROOT=Path(__file__).parents[1]/'custom_components/ariston'
import importlib.util as _ilu
_spec=_ilu.spec_from_file_location('ariston_cloud_pacing',ROOT/'cloud_pacing.py')
pacing=_ilu.module_from_spec(_spec);_spec.loader.exec_module(pacing)
class NotReady(Exception):pass
class AuthFailed(Exception):pass

class SetupTests(unittest.IsolatedAsyncioTestCase):
    def build(self, failures):
        entry=NS(data={'username':'test','password':'test','device':{'gw':'test','name':'test'}},options={},unique_id='test',entry_id='entry',async_on_unload=Mock(),add_update_listener=Mock())
        device=NS(system_type='GALEVO',has_metering=True,gw='test',gateway='test',
                  async_get_features=AsyncMock(),async_update_state=AsyncMock(),async_get_bus_errors=AsyncMock(),async_update_energy=AsyncMock())
        api=NS(async_connect=AsyncMock(return_value=True),async_hello=AsyncMock(return_value=device))
        hass=NS(data={},config=NS(units='metric'),config_entries=NS(async_forward_entry_setups=AsyncMock()),services=NS(async_register=Mock()))
        instances={}
        class Coordinator:
            def __init__(self,hass,device,seconds,name,update):
                self.device=device;self.name=name;self.update=update;self.last_update_success=True
                instances[name]=self
            async def async_config_entry_first_refresh(self):
                if failure:=failures.get(self.name):
                    self.last_update_success=False
                    raise failure
        namespace={'HomeAssistant':object,'ConfigEntry':object,'Ariston':lambda:api,
                   'DeviceAttribute':NS(NAME='name',GW='gw'),'SystemType':NS(GALEVO='GALEVO'),
                   'METRIC_SYSTEM':'metric','ConfigEntryAuthFailed':AuthFailed,'ConfigEntryNotReady':NotReady,
                   '_LOGGER':logging.getLogger('test.setup'),'DeviceDataUpdateCoordinator':Coordinator,
                   'async_setup_dhw_scenario_manager':AsyncMock(),'async_setup_heating_scenario_managers':AsyncMock(),
                   'async_track_time_interval':Mock(return_value=lambda:None), 'timedelta':timedelta,
                   'asyncio':asyncio,'deepcopy':deepcopy,
                   'aiohttp':NS(ClientSession=lambda **kw:NS(closed=False,close=AsyncMock()),CookieJar=lambda **kw:None),
                   'er':NS(async_get=lambda h:NS(async_get_entity_id=lambda *a:None)),
                   'update_listener':Mock(),'SET_ITEM_BY_ID_SCHEMA':{},
                   'AdaptivePacer':pacing.AdaptivePacer,'TIME_PROGRAM_SCAN_INTERVAL':'time_program_scan_interval',
                   'DEFAULT_TIME_PROGRAM_SCAN_INTERVAL_SECONDS':180,'PACING_TICK':timedelta(seconds=30),
                   'ENERGY_BURST_INTERVAL':timedelta(minutes=2),'ENERGY_BURST_WINDOW':timedelta(minutes=40),
                   'dt_util':NS(utcnow=lambda:__import__('datetime').datetime.now(__import__('datetime').timezone.utc),now=lambda:__import__('datetime').datetime.now().astimezone()),'PLATFORMS':['sensor','climate']}
        for key in ('DOMAIN','API_URL_SETTING','ARISTON_API_URL','API_USER_AGENT','ARISTON_USER_AGENT',
                    'CONF_USERNAME','CONF_PASSWORD','CONF_DEVICE','CONF_SCAN_INTERVAL','COORDINATOR',
                    'ENERGY_COORDINATOR','BUS_ERRORS_COORDINATOR','BUS_ERRORS_SCAN_INTERVAL','ENERGY_SCAN_INTERVAL',
                    'SERVICE_SET_ITEM_BY_ID'):
            namespace[key]={'DOMAIN':'ariston','CONF_USERNAME':'username','CONF_PASSWORD':'password','CONF_DEVICE':'device'}.get(key,key)
        for key in ('DEFAULT_SCAN_INTERVAL_SECONDS','DEFAULT_BUS_ERRORS_SCAN_INTERVAL_SECONDS','DEFAULT_ENERGY_SCAN_INTERVAL_MINUTES'):
            namespace[key]=600
        node=next(n for n in ast.parse((ROOT/'__init__.py').read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='async_setup_entry')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'setup','exec'),namespace)
        return namespace['async_setup_entry'],hass,entry,instances

    async def test_bus_500_and_energy_500_still_load_platforms(self):
        for failures in ({'BUS_ERRORS_COORDINATOR':NotReady('500')},
                         {'ENERGY_COORDINATOR':NotReady('500')},
                         {'BUS_ERRORS_COORDINATOR':NotReady('500'),'ENERGY_COORDINATOR':NotReady('500')},{}):
            with self.subTest(failures=list(failures)):
                setup,hass,entry,instances=self.build(failures)
                self.assertTrue(await setup(hass,entry))
                hass.config_entries.async_forward_entry_setups.assert_awaited_once()
                for name in failures:self.assertFalse(instances[name].last_update_success)

    async def test_essential_state_failure_still_retries_setup(self):
        setup,hass,entry,_=self.build({'COORDINATOR':NotReady('500')})
        with self.assertRaises(NotReady):await setup(hass,entry)
        hass.config_entries.async_forward_entry_setups.assert_not_awaited()

    async def test_authentication_errors_are_not_suppressed(self):
        for name in ('COORDINATOR','BUS_ERRORS_COORDINATOR','ENERGY_COORDINATOR'):
            setup,hass,entry,_=self.build({name:AuthFailed()})
            with self.assertRaises(AuthFailed):await setup(hass,entry)
            hass.config_entries.async_forward_entry_setups.assert_not_awaited()

class PollingTests(unittest.IsolatedAsyncioTestCase):
    def _function(self, name, namespace):
        tree=ast.parse((ROOT/'__init__.py').read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
        exec(compile(ast.Module(body=[node],type_ignores=[]),name,'exec'),namespace)
        return namespace[name]

    async def test_weekly_programs_follow_pacer_and_back_off_on_overload(self):
        from datetime import datetime, timezone
        clock={'now':datetime(2026,10,24,6,0,tzinfo=timezone.utc)}
        class Overload(Exception):pass
        dhw=AsyncMock(return_value=None);heating=AsyncMock(return_value=None)
        pacer=pacing.AdaptivePacer(timedelta(minutes=3))
        ns={'async_update_dhw_program':dhw,'async_update_heating_programs':heating,
            'program_pacer':pacer,'dt_util':NS(utcnow=lambda:clock['now'])}
        refresh=self._function('async_refresh_time_programs',ns)
        ns['async_refresh_time_programs']=refresh
        tick=self._function('async_time_program_tick',ns)
        def at(minute,second=0):clock['now']=datetime(2026,10,24,6,minute,second,tzinfo=timezone.utc)
        for half in range(0,13):          # 30-second local ticks for 6 minutes
            at(half//2,30*(half%2));await tick(None)
        self.assertEqual(dhw.await_count,3)   # minutes 0, 3, 6
        dhw.return_value=Overload(503)       # cloud overloaded from now on
        at(9);await tick(None)               # fails -> next try in 6 min
        for minute in range(10,15):at(minute);await tick(None)
        self.assertEqual(dhw.await_count,4)
        at(15);await tick(None)              # fails again -> 12 min
        self.assertEqual(dhw.await_count,5)
        dhw.return_value=None
        at(26);await tick(None);self.assertEqual(dhw.await_count,5)
        at(27);await tick(None);self.assertEqual(dhw.await_count,6)
        self.assertEqual(pacer.interval,timedelta(minutes=3))  # recovered
        at(30);await tick(None);self.assertEqual(dhw.await_count,7)

    async def test_energy_refresh_reads_r2_and_survives_r2_failure(self):
        from datetime import datetime, timezone
        now=datetime(2026,10,24,6,5,tzinfo=timezone.utc)
        device=NS(consumptions_sequences=[{'k':7}])
        gas=AsyncMock(side_effect=RuntimeError('R2 down'))
        ns={'original_async_update_energy':AsyncMock(return_value='api'),'device':device,
            'async_update_physical_gas':gas,'deepcopy':deepcopy,'_LOGGER':logging.getLogger('test.polling'),
            'dt_util':NS(now=lambda:now,utcnow=lambda:now),'r2_pacer':pacing.AdaptivePacer(timedelta(minutes=1)),
            'schedule_energy_burst':lambda:None}
        update=self._function('async_update_energy_with_web_metering',ns)
        self.assertEqual(await update(),'api')
        gas.assert_awaited_once()
        self.assertEqual(device.energy_sequences_time,now)
        # A failing R2 is retried on the next refresh (1 min base), not hammered.
        self.assertEqual(ns['r2_pacer'].next_at,now+timedelta(minutes=1))

    def test_energy_burst_until_new_interval_arrives(self):
        from datetime import datetime, timezone
        tz=timezone(timedelta(hours=5))
        clock={'now':datetime(2026,10,24,9,50,tzinfo=tz)}
        device=NS(system_type='GALEVO',gas_physical_unit_data={'histogramData':[1]},energy_sequences_snapshot=[1])
        class Coord:
            preferred_interval=None
        coordinator=Coord()
        ns={'dt_util':NS(now=lambda:clock['now']),'device':device,'SystemType':NS(GALEVO='GALEVO'),
            'energy_burst':{'block':None,'start':None,'last':None,'arrived':False},
            'ENERGY_BURST_INTERVAL':timedelta(minutes=2),'ENERGY_BURST_WINDOW':timedelta(minutes=40),
            'hass':NS(data={'ariston':{'gw':{'energy':coordinator}}}),'entry':NS(unique_id='gw'),
            'DOMAIN':'ariston','ENERGY_COORDINATOR':'energy','DeviceDataUpdateCoordinator':Coord}
        tree=ast.parse((ROOT/'__init__.py').read_text())
        for name in ('energy_fingerprint','schedule_energy_burst'):
            node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name)
            exec(compile(ast.Module(body=[node],type_ignores=[]),name,'exec'),ns)
        burst=ns['schedule_energy_burst']
        burst();self.assertIsNone(coordinator.preferred_interval)           # 09:50, mid-interval
        clock['now']=datetime(2026,10,24,10,2,tzinfo=tz)
        burst();self.assertEqual(coordinator.preferred_interval,timedelta(minutes=2))  # waiting for 08-10
        clock['now']=datetime(2026,10,24,10,14,tzinfo=tz)
        device.gas_physical_unit_data={'histogramData':[1,2]}               # 08-10 published
        burst();self.assertIsNone(coordinator.preferred_interval)
        clock['now']=datetime(2026,10,24,10,30,tzinfo=tz)
        burst();self.assertIsNone(coordinator.preferred_interval)           # stays slow in this block
        clock['now']=datetime(2026,10,24,12,45,tzinfo=tz)
        burst();self.assertIsNone(coordinator.preferred_interval)           # window over, nothing new

    async def test_r2_requests_are_serialized(self):
        tree=ast.parse((ROOT/'__init__.py').read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='async_update_physical_gas')
        busy=0;peak=0
        async def fetch():
            nonlocal busy,peak
            busy+=1;peak=max(peak,busy)
            await asyncio.sleep(0)
            busy-=1
        ns={'r2_lock':asyncio.Lock(),'async_update_physical_gas_locked':fetch}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'r2','exec'),ns)
        await asyncio.gather(*(ns[node.name]() for _ in range(5)))
        self.assertEqual(peak,1)

class CoordinatorErrors(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_http_failure_is_recoverable_and_auth_is_preserved(self):
        class ConnectionException(Exception):pass
        class UpdateFailed(Exception):pass
        class Base:
            def __init__(self,*args,**kwargs):
                self.update_method=kwargs['update_method'];self.update_interval=kwargs['update_interval'];self.name=kwargs['name']
        ns={'asyncio':asyncio,'DataUpdateCoordinator':Base,'ConnectionException':ConnectionException,
            'UpdateFailed':UpdateFailed,'ConfigEntryAuthFailed':AuthFailed,
            'HomeAssistant':object,'AristonBaseDevice':object,'Callable':object,
            'timedelta':timedelta,'DOMAIN':'ariston','_LOGGER':logging.getLogger('test.sdk'),
            'is_overload':pacing.is_overload,'backoff_interval':pacing.backoff_interval}
        node=next(n for n in ast.parse((ROOT/'coordinator.py').read_text()).body if isinstance(n,ast.ClassDef))
        exec(compile(ast.Module(body=[node],type_ignores=[]),'coordinator','exec'),ns)
        for status,error_type in ((500,UpdateFailed),(599,UpdateFailed),(401,AuthFailed),(403,AuthFailed)):
            update=AsyncMock(side_effect=ConnectionException(status))
            c=ns['DeviceDataUpdateCoordinator'](None,NS(name='test'),600,'bus',update)
            with self.assertRaises(error_type):await c.update_method()
            update.side_effect=None;update.return_value={'ok':True}
            self.assertEqual(await c.update_method(),{'ok':True})
        # Overload doubles the interval (capped at 30 min); success restores it.
        update=AsyncMock(side_effect=ConnectionException(503))
        c=ns['DeviceDataUpdateCoordinator'](None,NS(name='test'),180,'state',update)
        seen=[]
        for _ in range(5):
            with self.assertRaises(UpdateFailed):await c.update_method()
            seen.append(c.update_interval.total_seconds())
        self.assertEqual(seen,[360,720,1440,1800,1800])
        update.side_effect=None;update.return_value='ok'
        self.assertEqual(await c.update_method(),'ok')
        self.assertEqual(c.update_interval,timedelta(seconds=180))
        c.preferred_interval=timedelta(seconds=120)
        await c.update_method();self.assertEqual(c.update_interval,timedelta(seconds=120))
        # Authorization errors are not overload and do not slow polling.
        update.side_effect=ConnectionException(401);c.preferred_interval=None
        with self.assertRaises(AuthFailed):await c.update_method()
        self.assertEqual(c.update_interval,timedelta(seconds=120))
        # A client-side bug (not overload) is re-raised without backoff.
        update.side_effect=ValueError('bad payload')
        with self.assertRaises(ValueError):await c.update_method()
        self.assertEqual(c.overload_failures,0)
