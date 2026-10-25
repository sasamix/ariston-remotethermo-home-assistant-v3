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
            if any("energy_interval_minutes = max(2, min(energy_interval_minutes, 10))"
                   in item for item in body):
                candidates.append(node)
        self.assertEqual(len(candidates), 1)

    def test_no_high_frequency_cloud_timers(self):
        """R2 and weekly programs must not have their own fast timers."""
        source = (Path(__file__).parents[1] /
                  "custom_components/ariston/__init__.py").read_text()
        tree = ast.parse(source)
        intervals = [ast.unparse(node.args[2]) for node in ast.walk(tree)
                     if isinstance(node, ast.Call)
                     and ast.unparse(node.func) == "async_track_time_interval"]
        # Only the local 30-second due-check; it makes no request by itself.
        self.assertEqual(intervals, ["PACING_TICK"])
        tick = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "async_time_program_tick")
        self.assertIn("if program_pacer.due(", ast.unparse(tick))
        self.assertNotIn("async_r2_gas_metering_timer", source)
        energy = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                      and n.name == "async_update_energy_with_web_metering")
        self.assertIn("await async_update_physical_gas()", ast.unparse(energy))

if __name__ == "__main__":
    unittest.main()
