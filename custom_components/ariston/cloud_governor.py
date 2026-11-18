"""Account-wide limiter for Ariston cloud requests.

Ariston answers ``429 Requests are blocked`` when one account sends too many
requests; every request of the account counts, whichever coordinator sends it.
This governor sees every request of the config entry and:

* keeps a rolling one-hour count, with a learned hourly budget;
* lets callers skip optional work (``allow``) before the budget is reached,
  highest priority last;
* on a 429 pauses *all* cloud traffic of the account, then probes once; each
  further block doubles the pause (10 -> 20 -> 40 -> 60 minutes);
* lowers the budget below the rate at which the block happened and raises it
  slowly again after hours without a block (additive increase, multiplicative
  decrease), so the integration settles just under Ariston's real limit.

It performs no I/O itself; persistence is a plain dict for the caller to store.
"""
from __future__ import annotations

from collections import Counter, deque
from datetime import datetime, timedelta

HOUR = timedelta(hours=1)

DEFAULT_BUDGET = 120          # requests per hour on first start
MIN_BUDGET = 40
MAX_BUDGET = 360
DECREASE_FACTOR = 0.7         # budget after a block: 70 % of the rate that was blocked
INCREASE_STEP = 10            # requests/hour added ...
INCREASE_AFTER = timedelta(hours=1)   # ... after this long without a block
FIRST_PAUSE = timedelta(minutes=10)
MAX_PAUSE = timedelta(minutes=60)

# Share of the hourly budget each priority may use. High-priority work
# (device state, energy right after a two-hour boundary, user writes) can use
# the whole budget; optional work stops earlier and leaves room for it.
PRIORITY_SHARE = {"high": 1.0, "normal": 0.85, "low": 0.6}


class CloudPaused(Exception):
    """Raised instead of sending a request while the account is paused."""

    def __init__(self, until: datetime) -> None:
        super().__init__(f"Ariston cloud paused until {until.isoformat()}")
        self.until = until


def is_block(error: BaseException) -> bool:
    """True for Ariston's account block (HTTP 429)."""
    args = getattr(error, "args", ())
    return bool(args) and args[0] == 429


class CloudGovernor:
    """Rolling budget, learned limit and pause for one Ariston account."""

    def __init__(self, now: datetime, saved: dict | None = None) -> None:
        saved = saved or {}
        self.budget = int(min(MAX_BUDGET, max(MIN_BUDGET, saved.get("budget", DEFAULT_BUDGET))))
        self.observed_limit: int | None = saved.get("observed_limit")
        if self.observed_limit is not None and self.observed_limit < MIN_BUDGET:
            # Learned from a block right after a restart (0.21.0): the rolling
            # count was incomplete, so the "limit" was far too low. Forget it.
            self.observed_limit = None
            self.budget = max(self.budget, DEFAULT_BUDGET)
        self.blocks_total = int(saved.get("blocks_total", 0))
        self.last_block = _parse(saved.get("last_block"))
        self.last_change = _parse(saved.get("last_change")) or now
        self.paused_until: datetime | None = None
        paused = _parse(saved.get("paused_until"))
        if paused and paused > now:
            self.paused_until = paused
        self.consecutive_blocks = int(saved.get("consecutive_blocks", 0)) if self.paused_until else 0
        self.probe_pending = self.paused_until is not None
        self.requests: deque[tuple[datetime, str]] = deque()
        # The rolling count is complete only one hour after start.
        self.started = now
        self.skipped: Counter[str] = Counter()
        # Grow only when the budget actually held work back.
        self.limited_since_change = False

    # ---- state -------------------------------------------------------
    def _trim(self, now: datetime) -> None:
        while self.requests and now - self.requests[0][0] >= HOUR:
            self.requests.popleft()

    def used(self, now: datetime) -> int:
        self._trim(now)
        return len(self.requests)

    def by_category(self, now: datetime) -> dict[str, int]:
        self._trim(now)
        return dict(Counter(category for _, category in self.requests))

    def paused(self, now: datetime) -> bool:
        if self.paused_until is not None and now >= self.paused_until:
            # Pause over; the next request is a probe.
            self.paused_until = None
        return self.paused_until is not None

    # ---- decisions ---------------------------------------------------
    def allow(self, now: datetime, priority: str = "normal", cost: int = 1,
              category: str | None = None) -> bool:
        """May optional work spend ``cost`` requests now?"""
        self._grow(now)
        if self.paused(now):
            ok = False
        elif self.probe_pending:
            # Only one probe after a pause: high-priority work sends it.
            ok = priority == "high" and cost <= 2
        else:
            limit = self.budget * PRIORITY_SHARE.get(priority, 1.0)
            ok = self.used(now) + cost <= limit
        if not ok and category:
            self.skipped[category] += 1
            if not self.paused_until and not self.probe_pending:
                self.limited_since_change = True
        return ok

    def before_request(self, now: datetime, category: str) -> None:
        """Count one request or refuse it while paused."""
        if self.paused(now):
            raise CloudPaused(self.paused_until)
        self.requests.append((now, category))

    def after_success(self, now: datetime) -> None:
        if self.probe_pending:
            self.probe_pending = False
            self.consecutive_blocks = 0

    def after_block(self, now: datetime) -> None:
        """Ariston blocked the account: pause everything, lower the budget."""
        rate = self.used(now)
        if now - self.started >= HOUR:
            recent = self.last_block is not None and now - self.last_block < timedelta(days=1)
            if rate:
                self.observed_limit = (min(self.observed_limit, rate)
                                       if (recent and self.observed_limit) else rate)
            basis = min(self.budget, rate) if rate else self.budget
            self.budget = max(MIN_BUDGET, int(basis * DECREASE_FACTOR))
        # Within the first hour the count misses earlier requests (e.g. a block
        # carried over from before a restart): pause, but learn nothing.
        self.consecutive_blocks += 1
        pause = min(MAX_PAUSE, FIRST_PAUSE * (2 ** (self.consecutive_blocks - 1)))
        self.paused_until = now + pause
        self.probe_pending = True
        self.last_block = now
        self.last_change = now
        self.blocks_total += 1

    def _grow(self, now: datetime) -> None:
        """Additive increase after a long quiet period."""
        if self.paused_until or self.probe_pending:
            return
        if (now - self.last_change >= INCREASE_AFTER and self.budget < MAX_BUDGET
                and self.limited_since_change):
            ceiling = MAX_BUDGET
            if self.observed_limit and self.last_block and now - self.last_block < timedelta(days=2):
                # Do not walk straight back into a limit seen recently.
                ceiling = max(MIN_BUDGET, int(self.observed_limit * 0.9))
            if self.budget < ceiling:
                self.budget = min(ceiling, self.budget + INCREASE_STEP)
            self.last_change = now
            self.limited_since_change = False

    # ---- reporting / persistence --------------------------------------
    def status(self, now: datetime) -> dict:
        paused = self.paused(now)
        return {
            "state": "paused" if paused else ("probing" if self.probe_pending else "ok"),
            "paused_until": self.paused_until.isoformat() if paused else None,
            "requests_last_hour": self.used(now),
            "budget_per_hour": self.budget,
            "observed_limit_per_hour": self.observed_limit,
            "by_category": self.by_category(now),
            "skipped": dict(self.skipped),
            "blocks_total": self.blocks_total,
            "last_block": self.last_block.isoformat() if self.last_block else None,
        }

    def to_store(self) -> dict:
        return {
            "budget": self.budget,
            "observed_limit": self.observed_limit,
            "blocks_total": self.blocks_total,
            "last_block": self.last_block.isoformat() if self.last_block else None,
            "last_change": self.last_change.isoformat() if self.last_change else None,
            "paused_until": self.paused_until.isoformat() if self.paused_until else None,
            "consecutive_blocks": self.consecutive_blocks,
        }


def _parse(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def category_of(path: str) -> str:
    """Short label of an Ariston API path for the per-category counters."""
    lowered = path.lower()
    for key, label in (
        ("timeprogs", "time_programs"), ("menuitems", "menu_items"), ("buserrors", "bus_errors"),
        ("consumptions", "energy"), ("energyaccount", "energy"), ("/login", "login"),
        ("dataitems", "state"), ("plantdata", "state"), ("features", "features"),
        ("plantmetering", "r2"),
    ):
        if key in lowered:
            return label
    return "other"
