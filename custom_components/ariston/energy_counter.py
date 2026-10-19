"""Calendar-based kWh counters, independent of Home Assistant and API polling.

The API-v2 v[-1] value is an interval sample, not a meter reading. Use the
R2 asKwh calendar histograms instead. Never add overlapping day/month/year
reports, nor sum a rolling window into a lifetime counter.
"""

from __future__ import annotations

from datetime import date
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
        if isinstance(saved, dict):
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

    def as_dict(self):
        return {
            "value": self.value,
            "source": self.source,
            "year": self.year,
            "last_reset": self.last_reset,
        }
