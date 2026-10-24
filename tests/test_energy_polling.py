"""Guard GALEVO API-v2 refresh cadence without touching other systems."""

import ast
from pathlib import Path
import unittest


class EnergyPollingTests(unittest.TestCase):
    def test_10_min_cap_only_for_galevo(self):
        path = Path(__file__).parents[1] / "custom_components/ariston/__init__.py"
        tree = ast.parse(path.read_text())
        candidates = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            try:
                condition = ast.unparse(node.test)
            except Exception:
                continue
            if "device.system_type == SystemType.GALEVO" not in condition:
                continue
            body = [ast.unparse(item) for item in node.body]
            if any("energy_interval_minutes = min(energy_interval_minutes, 10)"
                   in item for item in body):
                candidates.append(node)
        self.assertEqual(len(candidates), 1)

    def test_r2_poll_independent_and_two_minute_cadence(self):
        source = (Path(__file__).parents[1] /
                  "custom_components/ariston/__init__.py").read_text()
        self.assertIn("async_r2_gas_metering_timer", source)
        self.assertIn("timedelta(minutes=2)", source)


if __name__ == "__main__":
    unittest.main()
