from copy import deepcopy
from datetime import datetime,timedelta
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from test_energy_reconciliation import pkg
m=importlib.import_module(pkg.__name__+'.metering_interval')

class IntervalTests(unittest.TestCase):
    def setUp(self):
        d=json.loads((Path(__file__).parent/'fixtures/energy_sources_oct21.json').read_text())
        self.at=datetime.fromisoformat('2026-10-21T07:50:00+05:00')
        self.device=SimpleNamespace(r2_gas_snapshot_time=self.at,r2_kwh_snapshot_time=self.at,
            gas_physical_unit_data={'histogramData':d['r2_as_physical_unit_histogram']},
            r2_kwh_raw={'histogramData':d['r2_raw_histogram']})
    def test_real_report_same_completed_interval_and_units(self):
        result=m.reading(self.device,self.at)
        self.assertEqual(result['attributes']['source_interval_end'],'2026-10-21T06:00:00+05:00')
        for series,prefix in [('Heating','Central heating gas '),('Dhw','Domestic hot water gas ')]:
            volume=m._values(self.device.gas_physical_unit_data,series,'CurrentDay')[2]
            energy=m._values(self.device.r2_kwh_raw,series,'CurrentDay')[2]
            self.assertEqual(result['values'][prefix+'current 2h volume'],volume)
            self.assertEqual(result['values'][prefix+'current 2h average flow'],volume/2)
            self.assertEqual(result['values'][prefix+'hourly average power'],energy/2)
    def test_every_boundary_including_midnight_and_noon_labels(self):
        for month,day in ((10,21),(11,1),(1,1)):
            for hour in range(0,24,2):
                at=self.at.replace(month=month,day=day,hour=hour)
                self.device.r2_gas_snapshot_time=self.device.r2_kwh_snapshot_time=at
                r=m.reading(self.device,at)
                self.assertIsNotNone(r)
                self.assertEqual(datetime.fromisoformat(r['attributes']['source_interval_start']),
                                 at.replace(minute=0,second=0)-timedelta(hours=2))
    def test_missing_stale_and_mismatched_reports_are_not_zero(self):
        self.assertIsNone(m.reading(self.device,self.at+timedelta(minutes=16)))
        self.device.r2_kwh_snapshot_time=self.at-timedelta(hours=2)
        self.assertIsNone(m.reading(self.device,self.at))
        self.device.r2_kwh_snapshot_time=self.at
        self.device.r2_kwh_raw=None
        self.assertIsNone(m.reading(self.device,self.at))
    def test_null_negative_duplicate_and_unknown_labels_rejected(self):
        original=deepcopy(self.device.r2_kwh_raw)
        for mode in ('null','negative','duplicate','label'):
            self.device.r2_kwh_raw=deepcopy(original)
            row=next(r for r in self.device.r2_kwh_raw['histogramData'] if r.get('period')=='CurrentDay' and r.get('series')=='Heating')
            if mode=='null':row['items'][2]['y']=None
            if mode=='negative':row['items'][2]['y']=-1
            if mode=='duplicate':self.device.r2_kwh_raw['histogramData'].append(deepcopy(row))
            if mode=='label':row['items'][2]['x']='unknown'
            self.assertIsNone(m.reading(self.device,self.at))
