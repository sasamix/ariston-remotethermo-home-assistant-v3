"""Validate backward-compatible energy updater without a live Home Assistant."""

from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/install_energy_counter_fix.py"
spec = importlib.util.spec_from_file_location("ariston_installer", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cfg = Path(self.temporary.name)
        self.dst = self.cfg / "custom_components" / "ariston"
        self.dst.mkdir(parents=True)
        for filename in ("__init__.py", "sensor.py", "energy_counter.py", "const.py", "manifest.json"):
            shutil.copy2(ROOT / "custom_components" / "ariston" / filename,
                         self.dst / filename)

    def launch(self, *extra):
        args = [
            str(SCRIPT), "--source-dir", str(ROOT), "--config", str(self.cfg),
            *extra,
        ]
        out = io.StringIO()
        with patch.object(sys, "argv", args), redirect_stdout(out):
            installer.main()
        return out.getvalue()

    def test_default_update_ref_is_pinned_to_a_commit(self):
        self.assertEqual(
            installer.PINNED_REF,
            "310c3e54df3ed806e45d15a8220a7d90fe9ebdb8",
        )

    def test_repeated_installation_is_idempotent(self):
        result = self.launch()
        self.assertIn("Исправление уже установлено", result)
        self.assertFalse((self.cfg / "ariston_backups").exists())

    def test_check_does_not_touch_installation(self):
        before = (self.dst / "sensor.py").read_bytes()
        result = self.launch("--check")
        self.assertIn("Исправление уже установлено", result)
        self.assertEqual((self.dst / "sensor.py").read_bytes(), before)

    def test_unknown_local_changes_are_not_overwritten(self):
        path = self.dst / "sensor.py"
        path.write_bytes(path.read_bytes() + b"\\n# user-customization\\n")
        before = path.read_bytes()
        with self.assertRaises(SystemExit) as err:
            self.launch()
        self.assertIn("отличается от проверенной основы", str(err.exception))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.cfg / "ariston_backups").exists())

    def test_keeps_existing_heating_number_migration(self):
        path = self.dst / "__init__.py"
        before = path.read_bytes()
        self.assertEqual(before.count(installer.SETUP_MARKER), 1)
        with_legacy = before.replace(
            installer.SETUP_MARKER,
            installer.LEGACY_INIT_BLOCK + installer.SETUP_MARKER, 1,
        )
        path.write_bytes(with_legacy)
        result = self.launch("--check")
        self.assertIn("Уже установлен", result)
        self.assertEqual(path.read_bytes(), with_legacy)


if __name__ == "__main__":
    unittest.main()
