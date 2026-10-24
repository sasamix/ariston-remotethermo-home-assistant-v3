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
                   'update_listener':Mock(),'SET_ITEM_BY_ID_SCHEMA':{},'PLATFORMS':['sensor','climate']}
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

    async def test_weekly_programs_refresh_at_most_every_ten_minutes(self):
        from datetime import datetime, timezone
        clock={'now':datetime(2026,10,24,6,0,tzinfo=timezone.utc)}
        dhw,heating,state=AsyncMock(),AsyncMock(),AsyncMock(return_value='ok')
        ns={'async_update_state_resilient':state,'async_update_dhw_program':dhw,
            'async_update_heating_programs':heating,'program_refresh':{'at':None},
            'TIME_PROGRAM_REFRESH':timedelta(minutes=10),
            'dt_util':NS(utcnow=lambda:clock['now'])}
        update=self._function('async_update_state_with_time_programs',ns)
        for minute in range(0,31,3):   # normal 3-minute state polling
            clock['now']=datetime(2026,10,24,6,minute,tzinfo=timezone.utc)
            self.assertEqual(await update(),'ok')
        self.assertEqual(state.await_count,11)
        self.assertEqual(dhw.await_count,3)      # minutes 0, 12, 24
        self.assertEqual(heating.await_count,3)

    async def test_energy_refresh_reads_r2_and_survives_r2_failure(self):
        from datetime import datetime, timezone
        now=datetime(2026,10,24,6,5,tzinfo=timezone.utc)
        device=NS(consumptions_sequences=[{'k':7}])
        gas=AsyncMock(side_effect=RuntimeError('R2 down'))
        ns={'original_async_update_energy':AsyncMock(return_value='api'),'device':device,
            'async_update_physical_gas':gas,'deepcopy':deepcopy,'_LOGGER':logging.getLogger('test.polling'),
            'dt_util':NS(now=lambda:now)}
        update=self._function('async_update_energy_with_web_metering',ns)
        self.assertEqual(await update(),'api')
        gas.assert_awaited_once()
        self.assertEqual(device.energy_sequences_time,now)

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
            def __init__(self,*args,**kwargs):self.update_method=kwargs['update_method']
        ns={'DataUpdateCoordinator':Base,'ConnectionException':ConnectionException,
            'UpdateFailed':UpdateFailed,'ConfigEntryAuthFailed':AuthFailed,
            'HomeAssistant':object,'AristonBaseDevice':object,'Callable':object,
            'timedelta':timedelta,'DOMAIN':'ariston','_LOGGER':logging.getLogger('test.sdk')}
        node=next(n for n in ast.parse((ROOT/'coordinator.py').read_text()).body if isinstance(n,ast.ClassDef))
        exec(compile(ast.Module(body=[node],type_ignores=[]),'coordinator','exec'),ns)
        for status,error_type in ((500,UpdateFailed),(599,UpdateFailed),(401,AuthFailed),(403,AuthFailed)):
            update=AsyncMock(side_effect=ConnectionException(status))
            c=ns['DeviceDataUpdateCoordinator'](None,NS(name='test'),600,'bus',update)
            with self.assertRaises(error_type):await c.update_method()
            update.side_effect=None;update.return_value={'ok':True}
            self.assertEqual(await c.update_method(),{'ok':True})
