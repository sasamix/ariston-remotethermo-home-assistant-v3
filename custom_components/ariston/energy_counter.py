"""Persistent kWh accounting from verified, non-overlapping source periods.

Gas uses R2 calendar totals. Electricity and combined energy use API-v2
completed days, dated and cross-checked against the independent R2 report.
"""

from __future__ import annotations

from datetime import date, timedelta
import calendar
import math


ENERGY_SOURCES = {
    "Central heating electricity consumption": ("Heating", ("ConsumedElectricity",)),
    "Domestic hot water electricity consumption": ("Dhw", ("ConsumedElectricity",)),
    "Central heating gas consumption": ("Heating", ("ConsumedGas",)),
    "Domestic hot water gas consumption": ("Dhw", ("ConsumedGas",)),
    "Central heating total energy consumption": (
        "Heating", ("ConsumedGas", "ConsumedElectricity"),
    ),
    "Domestic hot water total energy consumption": (
        "Dhw", ("ConsumedGas", "ConsumedElectricity"),
    ),
}


def nonnegative(value):
    """Reject missing, negative and non-finite readings (including booleans)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def calendar_total(payload, series, tabs, snapshot_date, previous_year=False):
    """Read non-overlapping R2 calendar totals, or None for incomplete data.

    CurrentYear contains twelve ordered month buckets. Replace its current
    month with CurrentMonth's day buckets, including today. Do not splice
    CurrentDay into this report: its independent rounding/update cadence
    caused the old daily boundary decreases.
    """
    if not isinstance(payload, dict) or not isinstance(snapshot_date, date):
        return None
    histogram = payload.get("histogramData")
    if not isinstance(histogram, list):
        return None
    total = 0.0
    for tab in tabs:
        datasets = {}
        for dataset in histogram:
            if not isinstance(dataset, dict):
                continue
            if dataset.get("series") != series or dataset.get("tab") != tab:
                continue
            period = dataset.get("period")
            if period in datasets:
                return None
            datasets[period] = dataset.get("items")
        year = datasets.get("PreviousYear" if previous_year else "CurrentYear")
        if not isinstance(year, list) or len(year) != 12:
            return None
        months = year if previous_year else year[:snapshot_date.month - 1]
        for item in months:
            value = nonnegative(item.get("y")) if isinstance(item, dict) else None
            if value is None:
                return None
            total += value
        if previous_year:
            continue
        month = datasets.get("CurrentMonth")
        if not isinstance(month, list):
            return None
        days = {}
        for item in month:
            if not isinstance(item, dict):
                return None
            try:
                day = int(str(item.get("x")))
            except (TypeError, ValueError):
                return None
            if not 1 <= day <= 31 or day in days:
                return None
            days[day] = nonnegative(item.get("y"))
        for day in range(1, snapshot_date.day + 1):
            value = days.get(day)
            if value is None:
                return None
            total += value
    return round(total, 6)


class EnergyCounter:
    """Persistent high-water mark and output, with no artificial meter resets."""

    def __init__(self, saved=None, initial_value=0.0):
        self.value = nonnegative(initial_value) or 0.0
        self.source = None
        self.year = None
        self.last_reset = None
        self.daily = None
        self.baseline_confirmed = False
        # Last successfully cross-checked API v2 / R2 interval, not the
        # timestamp of the most recent (possibly unverified) HTTP response.
        self.source_interval_end = None
        self.source_snapshot_date = None
        if isinstance(saved, dict):
            self.baseline_confirmed = saved.get("baseline_confirmed") is True
            if isinstance(saved.get("source_interval_end"), str):
                self.source_interval_end = saved["source_interval_end"]
            if isinstance(saved.get("source_snapshot_date"), str):
                self.source_snapshot_date = saved["source_snapshot_date"]
            if isinstance(saved.get("daily"), dict):
                self.daily = dict(saved["daily"])
            value = nonnegative(saved.get("value"))
            source = nonnegative(saved.get("source"))
            year = saved.get("year")
            if value is not None:
                self.value = value
                self.last_reset = saved.get("last_reset")
            if value is not None and source is not None and type(year) is int:
                self.value, self.source, self.year = value, source, year

    def update(self, source, year, previous_year=None):
        """Apply only new consumption; first reading establishes a baseline.

        Keep the high-water mark on a downward correction so recovery is not
        counted twice. At New Year, finish the old year using PreviousYear
        before adding the new year's consumption. No guessed multi-year gap.
        """
        source = nonnegative(source)
        if source is None:
            return False
        if self.source is None:
            self.source, self.year = source, year
            return True
        if year == self.year:
            delta = source - self.source
        elif year == self.year + 1:
            previous_year = nonnegative(previous_year)
            if previous_year is None:
                return False
            delta = previous_year - self.source + source
        else:
            return False
        if delta < -0.000001:
            return False
        self.value = round(self.value + max(0, delta), 6)
        self.source = max(source, self.source) if year == self.year else source
        self.year = year
        return True

    def update_days(self, days):
        """Persist day high-water marks; never add the same interval twice."""
        if not days:
            return False
        ordered = sorted(days)
        if self.daily is None:
            # Preserve the previously displayed value; establish a baseline.
            self.daily = dict(days)
            self.source = 0.0
        else:
            last = max(self.daily)
            if ordered[-1] < last:
                return False
            if date.fromisoformat(ordered[0]) > date.fromisoformat(last) + timedelta(days=1):
                return False  # Cannot recover a gap outside the retained report.
            delta = 0.0
            for day in ordered:
                if day in self.daily:
                    delta += max(0.0, days[day] - self.daily[day])
                elif day > last:
                    delta += days[day]
            self.value = round(self.value + delta, 6)
            self.source = round((self.source or 0.0) + delta, 6)
            self.daily = {day: max(value, self.daily.get(day, 0.0))
                          for day, value in days.items()}
        self.year = date.fromisoformat(ordered[-1]).year
        return True

    def mark_verified_period(self, api_time, snapshot_date):
        """Record the source period only after a valid, aligned cross-check."""
        self.source_interval_end = api_time.replace(
            hour=(api_time.hour // 2) * 2, minute=0, second=0, microsecond=0
        ).isoformat()
        self.source_snapshot_date = snapshot_date.isoformat()

    def as_dict(self):
        return {
            "value": self.value,
            "baseline_confirmed": self.baseline_confirmed,
            "source": self.source,
            "year": self.year,
            "last_reset": self.last_reset,
            "source_interval_end": self.source_interval_end,
            "source_snapshot_date": self.source_snapshot_date,
            **({"daily": dict(self.daily)} if self.daily is not None else {}),
        }


DAILY_KEYS = {
    "Central heating electricity consumption": (20,),
    "Domestic hot water electricity consumption": (21,),
    "Central heating total energy consumption": (7, 20),
    "Domestic hot water total energy consumption": (10, 21),
}


def electricity_days(sequences, raw, snapshot_date, key):
    """Date API-v2 completed-day arrays only after matching R2 calendar data.

    Observed GALEVO p=3 covers the previous month and completed current days.
    Reject a different layout rather than guessing dates. R2 boilerElectricity
    independently verifies the split electricity sums for both months.
    """
    if not isinstance(snapshot_date, date) or not isinstance(raw, dict):
        return None
    if not isinstance(sequences, list) or key not in DAILY_KEYS:
        return None
    first_current = snapshot_date.replace(day=1)
    last_previous = first_current - timedelta(days=1)
    first = last_previous.replace(day=1)
    previous_count = calendar.monthrange(last_previous.year, last_previous.month)[1]
    count = previous_count + snapshot_date.day - 1
    arrays = {}
    for row in sequences:
        if not isinstance(row, dict) or row.get("p") != 3 or row.get("k") not in (7, 10, 20, 21):
            continue
        k = row["k"]
        values = row.get("v")
        if k in arrays or not isinstance(values, list) or len(values) != count:
            return None
        values = [nonnegative(v) for v in values]
        if any(v is None for v in values):
            return None
        arrays[k] = values
    if set(arrays) != {7, 10, 20, 21}:
        return None
    totals = raw.get("boilerElectricity")
    if not isinstance(totals, list):
        return None
    for period, section in (("PreviousMonth", slice(0, previous_count)),
                            ("CurrentMonth", slice(previous_count, None))):
        matches = [r for r in totals if isinstance(r, dict) and r.get("period") == period]
        if len(matches) != 1:
            return None
        expected = nonnegative(matches[0].get("value"))
        actual = sum(arrays[20][section]) + sum(arrays[21][section])
        if expected is None or abs(actual - expected) > 0.005:
            return None
        # Date anchors: every gas day must match the explicitly dated R2 report.
        for k, series in ((7, "Heating"), (10, "Dhw")):
            reports = [r for r in raw.get("histogramData", []) if isinstance(r, dict)
                       and r.get("tab") == "ConsumedGas" and r.get("series") == series
                       and r.get("period") == period]
            if len(reports) != 1:
                return None
            items = reports[0].get("items")
            if not isinstance(items, list):
                return None
            days = {}
            for item in items:
                if not isinstance(item, dict):
                    return None
                label = str(item.get("x"))
                if label in days:
                    return None
                days[label] = nonnegative(item.get("y"))
            for index, value in enumerate(arrays[k][section], 1):
                expected = days.get(str(index))
                if expected is None or abs(value - expected) > 0.005:
                    return None
    return {(first + timedelta(days=i)).isoformat():
            round(sum(arrays[k][i] for k in DAILY_KEYS[key]), 6)
            for i in range(count)}


def electricity_periods(sequences, raw, snapshot_time, key):
    """Add today's completed two-hour intervals to verified completed days.

    GALEVO p=1 is a 24-bucket rolling sequence, not 24 clock-hour values.
    Anchor its last buckets to the request's local two-hour boundary and
    verify both gas channels against R2 PreviousDay and CurrentDay labels.
    Keep one high-water mark per date so the eventual daily report replaces
    today's partial report without counting its intervals a second time.
    """
    if snapshot_time is None or not hasattr(snapshot_time, 'hour'):
        return None
    days = electricity_days(sequences, raw, snapshot_time.date(), key)
    if days is None:
        return None
    count = snapshot_time.hour // 2
    arrays = {}
    for row in sequences:
        if not isinstance(row, dict) or row.get('p') != 1 or row.get('k') not in (7,10,20,21):
            continue
        k = row['k']
        values = row.get('v')
        if k in arrays or not isinstance(values, list) or len(values) != 24:
            return None
        arrays[k] = [nonnegative(v) for v in values]
        if any(v is None for v in arrays[k]):
            return None
    if set(arrays) != {7,10,20,21}:
        return None
    labels = ['12-02 AM','02-04 AM','04-06 AM','06-08 AM','08-10 AM','10-12 PM',
              '12-02 PM','02-04 PM','04-06 PM','06-08 PM','08-10 PM','10-12 AM']
    for k, series in ((7,'Heating'),(10,'Dhw')):
        for period in ('PreviousDay','CurrentDay'):
            reports = [r for r in raw.get('histogramData', []) if isinstance(r,dict)
                       and r.get('tab') == 'ConsumedGas' and r.get('series') == series
                       and r.get('period') == period]
            if len(reports) != 1:
                return None
            items = reports[0].get('items')
            if not isinstance(items,list) or len(items) != 12:
                return None
            if [r.get('x') for r in items if isinstance(r,dict)] != labels:
                return None
            expected = [nonnegative(r.get('y')) for r in items]
            if any(v is None for v in expected):
                return None
            actual = (arrays[k][-count:] if count else []) if period == 'CurrentDay' else arrays[k][12-count:24-count]
            for index, value in enumerate(actual):
                if abs(value - expected[index]) > .005:
                    return None
            if period == 'CurrentDay' and any(v > .005 for v in expected[count:]):
                return None  # Cloud is ahead of the proposed date/interval anchor.
    days[snapshot_time.date().isoformat()] = round(
        sum(sum(arrays[k][-count:]) for k in DAILY_KEYS[key]), 6
    ) if count else 0.0
    return days
