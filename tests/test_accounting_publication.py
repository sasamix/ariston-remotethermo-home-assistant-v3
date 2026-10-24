"""Actual concrete sensor properties must publish committed values, including zero."""
from datetime import datetime
from types import SimpleNamespace
import unittest
import test_energy_sensor_lifecycle as calendar
import test_gas_live_persistence as gas

class PublicationTests(unittest.TestCase):
    def test_all_eight_concrete_entities_override_obsolete_internal_counters(self):
        calendar.namespace['SensorStateClass']=SimpleNamespace(TOTAL='total')
        gas.GasClassConstants.TOTAL='total'
        gas.FixedDate.parse_datetime=staticmethod(datetime.fromisoformat)
        keys=('electricity_heating','electricity_dhw','gas_kwh_heating','gas_kwh_dhw',
              'total_heating','total_dhw','gas_m3_heating','gas_m3_dhw')
        for key in keys:
            for confirmed in (0.0, 984.41):
                with self.subTest(key=key,value=confirmed):
                    cls=gas.Sensor if key.startswith('gas_m3') else calendar.Sensor
                    entity=object.__new__(cls)
                    entity._dated_key=key
                    entity._dated_accounting=SimpleNamespace(
                        value=lambda k:confirmed,
                        attributes=lambda k:{'energy_counter_source':'verified dated R2/API v2 ledger',
                                             'source_interval_end':'2026-10-21T14:00:00+05:00'})
                    # Missing legacy fields deliberately make any obsolete branch fail.
                    self.assertEqual(entity.native_value,confirmed)
                    self.assertTrue(entity.available)
                    self.assertEqual(entity.state_class,'total')
                    self.assertEqual(entity.last_reset.timestamp(),0)
                    self.assertEqual(entity.extra_state_attributes['energy_counter_source'],
                                     'verified dated R2/API v2 ledger')
                    # Repeated reads and a source outage cannot change committed output.
                    self.assertEqual(entity.native_value,confirmed)

    def test_restored_runtime_values_survive_missing_cloud(self):
        # Exercise the real runtime start path and bind its loaded Store values
        # to both concrete sensor classes before any cloud update succeeds.
        import ast, asyncio
        from pathlib import Path
        from datetime import timedelta
        source=(Path(__file__).parents[1]/'custom_components/ariston/accounting_runtime.py').read_text()
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='AccountingRuntime')
        keys=('electricity_heating','electricity_dhw','gas_kwh_heating','gas_kwh_dhw',
              'total_heating','total_dhw','gas_m3_heating','gas_m3_dhw')
        saved={k:0.0 if k.endswith('dhw') else 12.3 for k in keys}
        ns={'KEYS':keys,'isfinite':__import__('math').isfinite,'deepcopy':__import__('copy').deepcopy,
            'timedelta':timedelta,'async_track_time_interval':lambda *args:lambda:None}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'runtime','exec'),ns)
        async def run():
            r=object.__new__(ns['AccountingRuntime'])
            async def load():return {'ledger':{'through':'2026-10-21T14:00:00+05:00'},'values':saved}
            r.store=SimpleNamespace(async_load=load)
            r.config={'seed':{},'start_date':'2026-10-18'}
            r.values={};r.error=None;r.closed=False
            r.publication_lock=asyncio.Lock()
            r.entry=SimpleNamespace(async_on_unload=lambda f:None)
            r.hass=SimpleNamespace(loop=asyncio.get_running_loop())
            await r.start()
            for key in keys:
                e=object.__new__(gas.Sensor if key.startswith('gas_m3') else calendar.Sensor)
                e._dated_key=key;e._dated_accounting=r
                self.assertEqual(e.native_value,saved[key]);self.assertTrue(e.available)
                self.assertEqual(e.extra_state_attributes['source_interval_end'],'2026-10-21T14:00:00+05:00')
            r.close()
        asyncio.run(run())
