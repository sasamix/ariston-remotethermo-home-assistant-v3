"""Real GALEVO electrical arrays plus counter boundary/regression cases."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import unittest
from test_energy_counter import module

KEY = 'Central heating electricity consumption'


def fixture():
    rows = json.loads((Path(__file__).parent / 'fixtures/electricity_sequences.json').read_text())
    # Calendar gas values from the independent R2 response (rounded to 3 decimals).
    heating = [0,.841,41.205,0,0,0,0,0,0,84.508,160.902,124.811,120.738,104.852,117.65,146.583,63.997,15.069]
    dhw = [38.347,35.616,28.266,38.866,0,0,12.594,0,41.811,32.116,29.575,0,0,9.63,0,20.977,22.727,12.158]
    dhw_previous = [39.191,43.266,24.208,22.669,44.755,41.333,13.372,4.13,0,11.683,37.333,30.972,0,39.572,33.63,28.925,34.694,35.375,34.888,13.688,22.702,1.197,31.288,4.125,38.427,38.958,3.247,21.536,25.016,34.186]
    raw = {'boilerElectricity': [
        {'period':'PreviousMonth','value':3.263},
        {'period':'CurrentMonth','value':14.789}], 'histogramData': []}
    for series, prev, curr in [('Heating',[0]*30,heating),('Dhw',dhw_previous,dhw)]:
        for period, values in [('PreviousMonth',prev),('CurrentMonth',curr)]:
            raw['histogramData'].append({'tab':'ConsumedGas','series':series,'period':period,
                'items':[{'x':str(i+1),'y':v} for i,v in enumerate(values)]})
    labels = ['12-02 AM','02-04 AM','04-06 AM','06-08 AM','08-10 AM','10-12 PM',
              '12-02 PM','02-04 PM','04-06 PM','06-08 PM','08-10 PM','10-12 AM']
    for series, prev, today in [
        ('Heating',[0,0,4.744,1.305,0,0,5.005,0,4.013,0,0,0],[0,0,0,0,6.536,5.705]+[0]*6),
        ('Dhw',[0,0,2.288,2.855,0,0,0,2.855,4.158,0,0,0],[3.05,4.2]+[0]*10)]:
        for period, values in [('PreviousDay',prev),('CurrentDay',today)]:
            raw['histogramData'].append({'tab':'ConsumedGas','series':series,'period':period,
                'items':[{'x':label,'y':v} for label,v in zip(labels,values)]})
    return rows, raw


class ElectricTests(unittest.TestCase):
    def test_real_report(self):
        rows, raw = fixture()
        expected = [(KEY,13.396),('Domestic hot water electricity consumption',1.393)]
        for key, total in expected:
            days = module.electricity_days(rows,raw,date(2026,10,19),key)
            self.assertIsNotNone(days)
            self.assertEqual(min(days),'2026-09-01')
            self.assertEqual(max(days),'2026-10-18')
            self.assertAlmostEqual(sum(v for k,v in days.items() if k >= '2026-10-01'),total)

    def test_reject_wrong_date_shape_duplicate_missing_and_mismatch(self):
        rows, raw = fixture()
        self.assertIsNone(module.electricity_days(rows,raw,date(2026,10,20),KEY))
        self.assertIsNone(module.electricity_days(rows+[next(r for r in rows if r["p"]==3)],raw,date(2026,10,19),KEY))
        self.assertIsNone(module.electricity_days(rows[:-1],raw,date(2026,10,19),KEY))
        raw['boilerElectricity'][1]['value'] += 1
        self.assertIsNone(module.electricity_days(rows,raw,date(2026,10,19),KEY))
        rows, raw = fixture()
        next(r for r in rows if r['p']==3)['v'][-1] += .1
        self.assertIsNone(module.electricity_days(rows,raw,date(2026,10,19),KEY))

    def test_baseline_restart_repeated_poll_and_corrections(self):
        c=module.EnergyCounter({'value':.077,'source':None,'year':None,'last_reset':'1970-01-01'})
        days={'2026-10-17':.872,'2026-10-18':.205}
        self.assertTrue(c.update_days(days)); self.assertEqual(c.value,.077)
        c=module.EnergyCounter(c.as_dict())
        c.update_days(days); self.assertEqual(c.value,.077)
        days['2026-10-19']=.4
        c.update_days(days); self.assertEqual(c.value,.477)
        days['2026-10-19']=.1; c.update_days(days)
        days['2026-10-19']=.4; c.update_days(days)
        self.assertEqual(c.value,.477)
        days['2026-10-19']=.5; c.update_days(days)
        self.assertEqual(c.value,.577)
        self.assertEqual(c.last_reset,'1970-01-01')

    def test_rollover_missing_gap_and_stale(self):
        c=module.EnergyCounter(initial_value=10)
        c.update_days({'2026-12-30':1,'2026-12-31':2})
        self.assertTrue(c.update_days({'2026-12-31':2,'2027-01-01':3}))
        self.assertEqual(c.value,13)
        self.assertFalse(c.update_days({'2027-02-01':20}))
        self.assertFalse(c.update_days({'2026-12-30':1}))
        self.assertEqual(c.value,13)

    def test_actual_sensor_electric_lifecycle(self):
        import asyncio
        from types import SimpleNamespace
        from test_energy_sensor_lifecycle import Sensor, Storage
        async def run():
            Storage.persisted={'value':.077,'source':None,'year':None,'last_reset':None}
            rows, raw=fixture()
            device=SimpleNamespace(energy_sequences_snapshot=rows,energy_sequences_date=date(2026,10,19),
                r2_kwh_raw=raw,r2_kwh_snapshot_date=date(2026,10,19),
                energy_sequences_time=datetime(2026,10,19,12,31),
                r2_kwh_snapshot_time=datetime(2026,10,19,13,50))
            sensor=Sensor(SimpleNamespace(device=device),SimpleNamespace(key=KEY))
            await sensor.async_added_to_hass()
            self.assertTrue(sensor.available); self.assertEqual(sensor.native_value,.077)
            sensor._handle_coordinator_update(); self.assertEqual(sensor.native_value,.077)
            self.assertEqual(sensor.extra_state_attributes['source_through_date'],'2026-10-19')
            device.energy_sequences_date=date(2026,10,20)
            sensor._handle_coordinator_update(); self.assertFalse(sensor.available)
            self.assertEqual(sensor.native_value,.077)
        asyncio.run(run())


    def test_real_two_hour_intervals(self):
        rows,raw=fixture()
        for key, expected in [(KEY,.165),('Domestic hot water electricity consumption',.027)]:
            days=module.electricity_periods(rows,raw,datetime(2026,10,19,12,31),key)
            self.assertIsNotNone(days)
            self.assertEqual(days['2026-10-19'],expected)
        self.assertIsNone(module.electricity_periods(rows,raw,datetime(2026,10,19,10,31),KEY))
        self.assertIsNone(module.electricity_periods(rows,raw,datetime(2026,10,19,14,31),KEY))
        next(r for r in rows if r['p']==1 and r['k']==7)['v'][-1] += 1
        self.assertIsNone(module.electricity_periods(rows,raw,datetime(2026,10,19,12,31),KEY))

    def test_partial_day_then_final_day_not_double_counted(self):
        c=module.EnergyCounter(initial_value=.077)
        c.update_days({'2026-10-18':.205,'2026-10-19':.165})
        c.update_days({'2026-10-18':.205,'2026-10-19':.265})
        self.assertEqual(c.value,.177)
        c=module.EnergyCounter(c.as_dict())
        c.update_days({'2026-10-18':.205,'2026-10-19':.265,'2026-10-20':0})
        self.assertEqual(c.value,.177)
        c.update_days({'2026-10-19':.268,'2026-10-20':.04})
        self.assertEqual(c.value,.22)
