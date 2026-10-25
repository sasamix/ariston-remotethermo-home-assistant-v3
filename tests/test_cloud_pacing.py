"""Adaptive pacing: configured speed while the cloud answers, back off on overload."""
import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "ariston_cloud_pacing", Path(__file__).parents[1] / "custom_components/ariston/cloud_pacing.py")
pacing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pacing)


class ConnectionException(Exception):
    pass


class ClientConnectorError(Exception):
    pass


class PacingTests(unittest.TestCase):
    def test_overload_classification(self):
        for error in (ConnectionException(429), ConnectionException(500), ConnectionException(503),
                      asyncio.TimeoutError(), ConnectionResetError(), ClientConnectorError("x")):
            self.assertTrue(pacing.is_overload(error), error)
        for error in (ConnectionException(401), ConnectionException(403), ConnectionException(404),
                      ValueError("bad"), RuntimeError("R2 response has no data.asPhysicalUnit")):
            self.assertFalse(pacing.is_overload(error), error)

    def test_backoff_doubles_and_caps(self):
        base = timedelta(minutes=3)
        self.assertEqual([pacing.backoff_interval(base, n).total_seconds() / 60 for n in range(6)],
                         [3, 6, 12, 24, 30, 30])

    def test_r2_http_errors_and_long_outages(self):
        for status in (429,500,503):
            self.assertTrue(pacing.is_overload(RuntimeError(f'R2 metering returned HTTP {status}')))
        self.assertFalse(pacing.is_overload(RuntimeError('R2 metering returned HTTP 401')))
        class ClientConnectorDNSError(ClientConnectorError):
            pass
        self.assertTrue(pacing.is_overload(ClientConnectorDNSError('DNS failed')))
        self.assertEqual(pacing.backoff_interval(timedelta(minutes=2),10000),timedelta(minutes=30))

    def test_pacer_due_success_failure(self):
        now = datetime(2026, 10, 24, 6, tzinfo=timezone.utc)
        p = pacing.AdaptivePacer(timedelta(minutes=3))
        self.assertTrue(p.due(now))
        p.success(now)
        self.assertFalse(p.due(now + timedelta(minutes=2, seconds=59)))
        self.assertTrue(p.due(now + timedelta(minutes=3)))
        p.failure(now, ConnectionException(503))
        self.assertEqual(p.next_at, now + timedelta(minutes=6))
        p.failure(now, ValueError("not overload"))      # keeps the current backoff level
        self.assertEqual(p.failures, 1)
        p.success(now)
        self.assertEqual((p.failures, p.next_at), (0, now + timedelta(minutes=3)))


if __name__ == "__main__":
    unittest.main()
