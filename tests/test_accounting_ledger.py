from copy import deepcopy
from datetime import datetime
import importlib
import unittest
from test_energy_reconciliation import ReconciliationTests, pkg

ledger_module=importlib.import_module(pkg.__name__+'.accounting_ledger')

class DatedLedgerTests(ReconciliationTests):
    def make(self):
        observation=ledger_module.snapshot(self.data)
        ledger=ledger_module.merge({},observation,'2026-10-18','Asia/Almaty')
        return ledger

    def test_completed_day_conservation_all_eight(self):
        ledger=self.make(); curves,resolutions=ledger_module.curves(ledger)
        for key,curve in curves.items():
            for day in ('18','19','20'):
                start=datetime.fromisoformat('2026-10-'+day+'T00:00:00+05:00').timestamp()
                self.assertAlmostEqual(curve.at(start+86400)-curve.at(start),ledger['days'][key]['2026-10-'+day],places=8)
        self.assertEqual(resolutions['gas_m3_heating']['2026-10-18'],'daily_uniform_estimate')
        self.assertEqual(resolutions['gas_m3_heating']['2026-10-20'],'two_hour_profile_scaled_to_daily_report')

    def test_downward_revision_is_replaced_not_added_now(self):
        ledger=self.make(); observation=ledger_module.snapshot(self.data)
        observation['days']['gas_m3_dhw']['2026-10-19']=.6
        revised=ledger_module.merge(ledger,observation,'2026-10-18','Asia/Almaty')
        self.assertEqual(revised['days']['gas_m3_dhw']['2026-10-19'],.6)
        self.assertEqual(revised,ledger_module.merge(revised,observation,'2026-10-18','Asia/Almaty'))
        oldcurves,_=ledger_module.curves(ledger);newcurves,_=ledger_module.curves(revised)
        self.assertAlmostEqual(oldcurves['gas_m3_dhw'].at(2e9)-newcurves['gas_m3_dhw'].at(2e9),.1)

    def test_tariff_boundary_and_repeated_pricing(self):
        curve=ledger_module.Curve([(0,7200,10)])
        priced=curve.priced([[-1,2],[3600,3]])
        self.assertEqual(priced.at(3600),10)
        self.assertEqual(priced.at(7200),25)
        self.assertEqual(curve.priced([[-1,2],[3600,3]]).at(7200),25)

    def test_missing_day_and_stale_snapshot_rejected(self):
        ledger=self.make();observation=ledger_module.snapshot(self.data)
        observation['at']='2026-10-20T07:00:00+05:00'
        self.assertEqual(ledger,ledger_module.merge(ledger,observation,'2026-10-18','Asia/Almaty'))
        del ledger['days']['gas_kwh_heating']['2026-10-19']
        with self.assertRaises(ValueError): ledger_module.curves(ledger)

    def test_export_daily_total_wins_over_inconsistent_interval_sum(self):
        # Ariston export 2026-10-24: previous-day intervals sum to 6.80/3.00,
        # while its completed daily reports are 5.07/2.32 m³. Preserve daily
        # totals; intervals provide only the distribution within that day.
        day='2026-10-23'
        ledger={'start_date':day,'timezone':'Asia/Almaty','through':'2026-10-24T00:00:00+05:00',
                'days':{key:{day:0.0} for key in ledger_module.KEYS},
                'profiles':{key:{} for key in ledger_module.KEYS}}
        samples={'gas_m3_heating':(5.07,[1,.9,.8,1.57,.7,.4,.8,.5,.1,0,.03,0]),
                 'gas_m3_dhw':(2.32,[0,0,.4,.3,.4,.3,.4,.3,0,0,.5,.4])}
        for key,(total,profile) in samples.items():
            ledger['days'][key][day]=total
            ledger['profiles'][key][day]=profile
        lines,_=ledger_module.curves(ledger)
        end=datetime.fromisoformat(ledger['through']).timestamp()
        for key,(total,_) in samples.items():
            self.assertAlmostEqual(lines[key].at(end),total,places=8)
            self.assertAlmostEqual(sum(lines[key].at(t+3600)-lines[key].at(t)
                                       for t in range(int(end)-86400,int(end),3600)),total,places=8)


class October25RegressionTests(unittest.TestCase):
    def test_both_gas_totals_and_midnight_daily_reconciliation(self):
        import json
        from pathlib import Path
        data=json.loads((Path(__file__).parent/'fixtures/energy_sources_oct25.json').read_text())
        observation=ledger_module.snapshot(data)
        ledger=ledger_module.merge({},observation,'2026-10-18','Asia/Almaty')
        lines,_=ledger_module.curves(ledger)
        midnight=datetime.fromisoformat('2026-10-25T00:00:00+05:00').timestamp()
        for key,daily,total in [('gas_m3_heating',6.18,1354.95),('gas_m3_dhw',2.29,989.66)]:
            curve=lines[key]
            self.assertAlmostEqual(curve.at(midnight)-curve.at(midnight-86400),daily)
            self.assertAlmostEqual(ledger['meter_bases'][key]+curve.at(2e9),total)
            # The reconstructed curve is continuous through midnight even
            # though the cloud's interval and daily reports disagree.
            points=[curve.at(t) for t in range(int(midnight)-86400,int(midnight)+28801,300)]
            self.assertTrue(all(b>=a for a,b in zip(points,points[1:])))
