"""Account-wide limiter: budget, block pause, probe, learning, and wiring."""
import ast
import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).parents[1] / "custom_components/ariston"
spec = importlib.util.spec_from_file_location("ariston_cloud_governor", ROOT / "cloud_governor.py")
gov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gov)

T0 = datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc)


class ConnectionException(Exception):
    pass


class GovernorUnit(unittest.TestCase):
    def test_priorities_share_the_hourly_budget(self):
        g = gov.CloudGovernor(T0, {"budget": 100})
        for minute in range(60):
            g.before_request(T0 + timedelta(seconds=minute), "x")
        now = T0 + timedelta(minutes=1)
        self.assertFalse(g.allow(now, "low", 1, "time_programs"))     # 60 used, low share 60
        self.assertTrue(g.allow(now, "normal", 1))                    # normal share 85
        self.assertTrue(g.allow(now, "high", 40))                     # high share 100
        self.assertFalse(g.allow(now, "high", 41))
        self.assertEqual(g.status(now)["skipped"], {"time_programs": 1})
        # One hour later the window has rolled over.
        self.assertTrue(g.allow(T0 + timedelta(hours=1, seconds=1), "low", 1))

    def test_block_pauses_everything_then_one_probe(self):
        g = gov.CloudGovernor(T0, {"budget": 120})
        for i in range(100):
            g.before_request(T0 + timedelta(seconds=i), "state")
        now = T0 + timedelta(minutes=5)
        g.after_block(now)
        self.assertEqual(g.observed_limit, 100)
        self.assertEqual(g.budget, 70)                    # 70 % of the blocked rate
        self.assertEqual(g.paused_until, now + timedelta(minutes=10))
        with self.assertRaises(gov.CloudPaused):
            g.before_request(now + timedelta(minutes=9), "state")
        self.assertFalse(g.allow(now + timedelta(minutes=9), "high", 1))
        after = now + timedelta(minutes=10)
        self.assertFalse(g.allow(after, "low", 1))        # only high may probe
        self.assertTrue(g.allow(after, "high", 1))
        g.before_request(after, "state")
        g.after_block(after)                               # still blocked: 20 min
        self.assertEqual(g.paused_until, after + timedelta(minutes=20))
        later = after + timedelta(minutes=20)
        g.before_request(later, "state")
        g.after_success(later)                             # probe ok: back to normal
        self.assertEqual((g.probe_pending, g.consecutive_blocks), (False, 0))
        self.assertEqual(g.status(later)["state"], "ok")

    def test_pause_grows_to_one_hour(self):
        g = gov.CloudGovernor(T0)
        now = T0
        pauses = []
        for _ in range(5):
            g.after_block(now)
            pauses.append((g.paused_until - now).total_seconds() / 60)
            now = g.paused_until
        self.assertEqual(pauses, [10, 20, 40, 60, 60])
        self.assertEqual(g.budget, gov.MIN_BUDGET)

    def test_budget_grows_slowly_and_stays_below_recent_limit(self):
        g = gov.CloudGovernor(T0, {"budget": 60})
        now = T0
        g.allow(now + timedelta(hours=3), "low", 1)
        self.assertEqual(g.budget, 60)                     # nothing was held back
        for _ in range(10):
            now += timedelta(hours=3)
            for i in range(60):
                g.before_request(now - timedelta(minutes=30) + timedelta(seconds=i), "x")
            g.allow(now - timedelta(minutes=1), "high", g.budget, "x")   # held back
            g.allow(now, "low", 1)
        self.assertEqual(g.budget, 160)
        g.observed_limit, g.last_block = 100, now
        g.last_change = now - timedelta(hours=3)
        g.budget, g.limited_since_change = 85, True
        g.allow(now, "low", 1)
        self.assertEqual(g.budget, 90)                     # ceiling 0.9 x 100
        g.last_change, g.limited_since_change = now - timedelta(hours=3), True
        g.allow(now, "low", 1)
        self.assertEqual(g.budget, 90)

    def test_state_survives_restart(self):
        g = gov.CloudGovernor(T0, {"budget": 120})
        g.after_block(T0)
        saved = g.to_store()
        g2 = gov.CloudGovernor(T0 + timedelta(minutes=1), saved)
        self.assertTrue(g2.paused(T0 + timedelta(minutes=1)))
        self.assertEqual(g2.budget, g.budget)
        g3 = gov.CloudGovernor(T0 + timedelta(hours=1), saved)
        self.assertFalse(g3.paused(T0 + timedelta(hours=1)))

    def test_categories(self):
        self.assertEqual(gov.category_of("https://x/api/v2/remote/timeProgs/GW/Dhw"), "time_programs")
        self.assertEqual(gov.category_of("https://x/api/v2/remote/plants/GW/menuItems?x"), "menu_items")
        self.assertEqual(gov.category_of("https://x/api/v2/remote/dataItems/GW/get"), "state")
        self.assertEqual(gov.category_of("https://x/api/v2/accounts/login"), "login")
        self.assertTrue(gov.is_block(ConnectionException(429, b"Requests are blocked")))
        self.assertFalse(gov.is_block(ConnectionException(500)))


def load(names, namespace):
    tree = ast.parse((ROOT / "__init__.py").read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "init", "exec"), namespace)
    return namespace


class WrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_sdk_request_is_counted_and_a_block_pauses_the_account(self):
        clock = {"now": T0}
        g = gov.CloudGovernor(T0)
        published = Mock()
        responses = []

        class Api:
            async def _AristonAPI__async_request(self, method, path, params=None, body=None, is_retry=False):
                result = responses.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

        api = Api()
        ns = load({"govern_api"}, {"CloudGovernor": gov.CloudGovernor, "ConnectionException": ConnectionException,
                                    "is_block": gov.is_block, "category_of": gov.category_of,
                                    "dt_util": NS(utcnow=lambda: clock["now"]),
                                    "_LOGGER": logging.getLogger("test.gov")})
        ns["govern_api"](api, g, published)
        responses[:] = [{"ok": 1}, {"ok": 2}]
        self.assertEqual(await api._AristonAPI__async_request("GET", "https://x/remote/timeProgs/GW/Dhw"), {"ok": 1})
        self.assertEqual(await api._AristonAPI__async_request("POST", "https://x/remote/dataItems/GW/get"), {"ok": 2})
        self.assertEqual(g.by_category(T0), {"time_programs": 1, "state": 1})
        responses[:] = [ConnectionException(429, b"Requests are blocked")]
        with self.assertRaises(ConnectionException):
            await api._AristonAPI__async_request("GET", "https://x/remote/menuItems")
        published.assert_called_with(True)
        self.assertTrue(g.paused(T0))
        # Nothing reaches Ariston while paused.
        with self.assertRaises(gov.CloudPaused):
            await api._AristonAPI__async_request("GET", "https://x/remote/dataItems")
        self.assertEqual(responses, [])

    async def test_r2_trace_counts_and_blocks(self):
        g = gov.CloudGovernor(T0)
        events = {}

        class Trace:
            def __init__(self):
                self.on_request_start, self.on_request_end = [], []

        ns = load({"governor_trace"}, {"aiohttp": NS(TraceConfig=Trace), "CloudGovernor": gov.CloudGovernor,
                                        "dt_util": NS(utcnow=lambda: T0)})
        publish = Mock()
        trace = ns["governor_trace"](g, publish)
        await trace.on_request_start[0](None, None, None)
        await trace.on_request_end[0](None, None, NS(response=NS(status=200)))
        self.assertEqual(g.by_category(T0), {"r2": 1})
        await trace.on_request_end[0](None, None, NS(response=NS(status=429)))
        self.assertTrue(g.paused(T0))
        with self.assertRaises(gov.CloudPaused):
            await trace.on_request_start[0](None, None, None)


class CoordinatorKeepsData(unittest.IsolatedAsyncioTestCase):
    async def test_block_keeps_last_data_for_two_hours(self):
        clock = {"now": T0}

        class UpdateFailed(Exception):
            pass

        class Base:
            def __init__(self, *args, **kwargs):
                self.update_method = kwargs["update_method"]
                self.update_interval = kwargs["update_interval"]
                self.name = kwargs["name"]
                self.data = None

        ns = {"DataUpdateCoordinator": Base, "ConnectionException": ConnectionException,
              "UpdateFailed": UpdateFailed, "ConfigEntryAuthFailed": Exception, "HomeAssistant": object,
              "AristonBaseDevice": object, "Callable": object, "timedelta": timedelta, "DOMAIN": "ariston",
              "_LOGGER": logging.getLogger("test.coord"), "asyncio": asyncio,
              "is_overload": lambda e: True, "backoff_interval": lambda b, n: b * 2,
              "CloudPaused": gov.CloudPaused, "is_block": gov.is_block, "STALE_LIMIT": timedelta(hours=2),
              "dt_util": NS(utcnow=lambda: clock["now"])}
        node = next(n for n in ast.parse((ROOT / "coordinator.py").read_text()).body if isinstance(n, ast.ClassDef))
        exec(compile(ast.Module(body=[node], type_ignores=[]), "coordinator", "exec"), ns)
        outcome = {"value": "fresh"}

        async def update():
            if isinstance(outcome["value"], Exception):
                raise outcome["value"]
            return outcome["value"]

        c = ns["DeviceDataUpdateCoordinator"](None, NS(name="t"), 180, "state", update)
        c.data = await c.update_method()
        outcome["value"] = ConnectionException(429, b"Requests are blocked")
        clock["now"] = T0 + timedelta(minutes=5)
        self.assertEqual(await c.update_method(), "fresh")          # available, last data
        self.assertEqual(c.update_interval, timedelta(seconds=180))  # no extra coordinator backoff
        outcome["value"] = gov.CloudPaused(T0 + timedelta(minutes=30))
        clock["now"] = T0 + timedelta(hours=1, minutes=59)
        self.assertEqual(await c.update_method(), "fresh")
        clock["now"] = T0 + timedelta(hours=2, minutes=1)
        with self.assertRaises(UpdateFailed):                        # honest after 2 h
            await c.update_method()


class DaySimulation(unittest.TestCase):
    """24 h against a cloud that blocks above an unknown hourly limit."""

    def simulate(self, limit, hours=24):
        g = gov.CloudGovernor(T0)
        sent = []                 # timestamps Ariston received
        blocked_until = None
        state_ok, blocks, block_times = [], 0, []
        now = T0
        step = timedelta(seconds=30)
        due = {"state": T0, "programs": T0, "energy": T0, "bus": T0, "menu": T0}
        every = {"state": timedelta(minutes=3), "programs": timedelta(minutes=3),
                 "energy": timedelta(minutes=15), "bus": timedelta(minutes=10), "menu": timedelta(minutes=15)}
        work = {"state": ("high", 1), "programs": ("low", 2), "energy": ("low", 2),
                "bus": ("low", 1), "menu": ("normal", 1)}

        def request(category):
            nonlocal blocked_until, blocks
            g.before_request(now, category)
            sent.append(now)
            while sent and now - sent[0] >= timedelta(hours=1):
                sent.pop(0)
            if (blocked_until and now < blocked_until) or len(sent) > limit:
                blocked_until = now + timedelta(minutes=15)
                blocks += 1
                block_times.append(now)
                g.after_block(now)
                return False
            g.after_success(now)
            return True

        while now < T0 + timedelta(hours=hours):
            for name, (priority, cost) in work.items():
                if now >= due[name]:
                    due[name] = now + every[name]
                    if g.allow(now, priority, cost, name):
                        try:
                            for _ in range(cost):
                                if not request(name):
                                    break
                            else:
                                if name == "state":
                                    state_ok.append(now)
                        except gov.CloudPaused:
                            pass
            now += step
        gaps = [(b - a).total_seconds() / 60 for a, b in zip(state_ok, state_ok[1:])]
        late = [b for b in block_times if b >= T0 + timedelta(hours=hours - 24)]
        return blocks, max(gaps), g, len(late)

    def test_settles_under_an_unknown_limit(self):
        for limit in (45, 60, 75):
            with self.subTest(limit=limit):
                blocks, worst_gap, g, _ = self.simulate(limit)
                self.assertLessEqual(blocks, 4)
                self.assertLess(g.budget, limit)
                # State keeps flowing; worst gap is one pause, not hours.
                self.assertLessEqual(worst_gap, 41)
                # Second day: at most one probing block.
                _, _, _, second_day = self.simulate(limit, hours=48)
                self.assertLessEqual(second_day, 1)

    def test_no_block_when_limit_is_generous(self):
        blocks, worst_gap, g, _ = self.simulate(400)
        self.assertEqual(blocks, 0)
        self.assertLessEqual(worst_gap, 3.5)


if __name__ == "__main__":
    unittest.main()
