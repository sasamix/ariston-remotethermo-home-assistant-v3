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
