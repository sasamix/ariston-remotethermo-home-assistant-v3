"""Read-only reconciliation of all eight counters against dated cloud reports.

No Home Assistant imports, database writes, inferred hourly allocation or changes
of meter baselines. A repair must use the dated observations, not simply subtract
an apparent spike from every subsequent reading.
"""
from datetime import datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

from .energy_counter import electricity_periods, nonnegative

# Logical fixture IDs only; live entity IDs are resolved from registered entities.
ENTITIES = {
    "electricity_heating": "sensor.ariston_electricity_heating",
    "electricity_dhw": "sensor.ariston_electricity_dhw",
    "gas_kwh_heating": "sensor.ariston_gas_kwh_heating",
    "gas_kwh_dhw": "sensor.ariston_gas_kwh_dhw",
    "total_heating": "sensor.ariston_total_heating",
    "total_dhw": "sensor.ariston_total_dhw",
    "gas_m3_heating": "sensor.ariston_gas_m3_heating",
    "gas_m3_dhw": "sensor.ariston_gas_m3_dhw",
}


def _calendar_days(histogram, series, snapshot):
    result = {}
    current = snapshot.date().replace(day=1)
    previous = (current - timedelta(days=1)).replace(day=1)
    for period, first in (("PreviousMonth", previous), ("CurrentMonth", current)):
        datasets = [r for r in histogram if r.get("tab") == "ConsumedGas"
                    and r.get("series") == series and r.get("period") == period]
        if len(datasets) != 1:
            raise ValueError(f"Missing/duplicate {series} {period}")
        seen = set()
        for item in datasets[0]["items"]:
            day = int(item["x"])
            date = first.replace(day=day)
            if date in seen:
                raise ValueError("Duplicate calendar day")
            seen.add(date)
            value = nonnegative(item.get("y"))
            if value is None:
                raise ValueError("Invalid consumption")
            if date < snapshot.date():
                result[date.isoformat()] = value
        end = current if period == "PreviousMonth" else snapshot.date()
        cursor = first
        while cursor < end:
            if cursor not in seen:
                raise ValueError(f"Missing calendar day {cursor}")
            cursor += timedelta(days=1)
    return result


def verified_daily_sources(diagnostic):
    """Use full-day physical reports and independently checked kWh arrays.

    The current partial day is intentionally excluded: its final daily total
    cannot be checked yet. Raw gas and electricity share the same dated arrays.
    """
    d = diagnostic.get("data", diagnostic)
    timestamps = d["source_timestamps"]
    api = datetime.fromisoformat(timestamps["energy_sequences_time"])
    r2 = datetime.fromisoformat(timestamps["r2_kwh_snapshot_time"])
    physical = datetime.fromisoformat(timestamps["r2_gas_snapshot_time"])
    if any(t.tzinfo is None for t in (api, r2, physical)):
        raise ValueError("Source timezone missing")
    if api.date() != r2.date() or api.hour // 2 != r2.hour // 2:
        raise ValueError("API/R2 snapshots are not aligned")
    if physical.date() != api.date():
        raise ValueError("Physical report has a different date")
    raw = {"histogramData": d["r2_raw_histogram"],
           "boilerElectricity": d["r2_raw_boiler_electricity"]}
    result = {}
    for suffix, series, prefix in (("heating", "Heating", "Central heating"),
                                   ("dhw", "Dhw", "Domestic hot water")):
        elec = electricity_periods(d["api_v2_sequences"], raw, api,
                                  prefix + " electricity consumption")
        total = electricity_periods(d["api_v2_sequences"], raw, api,
                                   prefix + " total energy consumption")
        if elec is None or total is None:
            raise ValueError(f"Source cross-check failed: {series}")
        elec = {day: v for day, v in elec.items() if day < api.date().isoformat()}
        total = {day: v for day, v in total.items() if day < api.date().isoformat()}
        gas = _calendar_days(d["r2_raw_histogram"], series, api)
        if set(elec) != set(total) or set(gas) != set(elec):
            raise ValueError("Source date coverage differs")
        for day in elec:
            if abs(total[day] - elec[day] - gas[day]) > .005001:
                raise ValueError(f"Gas/electricity/total mismatch at {day}")
        result[ENTITIES["electricity_" + suffix]] = elec
        result[ENTITIES["total_" + suffix]] = total
        result[ENTITIES["gas_kwh_" + suffix]] = gas
        result[ENTITIES["gas_m3_" + suffix]] = _calendar_days(
            d["r2_as_physical_unit_histogram"], series, physical)
    return result


def compare_statistics(sources, statistics, timezone):
    """Compare only complete 24-hour HA days; label gaps rather than guess.

    Millisecond timestamps are the format returned by HA WebSocket statistics.
    A numeric zero is valid; a missing/null change is not a zero.
    """
    zone = ZoneInfo(timezone)
    report = []
    for entity, source_days in sources.items():
        by_day = {}
        for row in statistics.get(entity, []):
            start = float(row["start"]) / 1000
            day = datetime.fromtimestamp(start, zone).date().isoformat()
            by_day.setdefault(day, []).append(row)
        for day, rows in sorted(by_day.items()):
            if day not in source_days:
                continue
            begin = datetime.fromisoformat(day).replace(tzinfo=zone)
            end = begin + timedelta(days=1)
            expected_starts = set(range(int(begin.timestamp()), int(end.timestamp()), 3600))
            starts = [float(r["start"]) / 1000 for r in rows]
            changes = [r.get("change") for r in rows]
            complete = (len(starts) == len(expected_starts)
                        and set(starts) == expected_starts
                        and all(not isinstance(v, bool) and isinstance(v, (int, float))
                                and isfinite(v) for v in changes)
                        and all(float(r["end"]) - float(r["start"]) == 3600000 for r in rows))
            actual = round(sum(changes), 6) if complete else None
            expected = source_days[day]
            report.append({"entity_id": entity, "date": day,
                           "unit": "m³" if entity in (ENTITIES["gas_m3_heating"], ENTITIES["gas_m3_dhw"]) else "kWh",
                           "source": expected, "ha": actual,
                           "difference": round(actual - expected, 6) if complete else None,
                           "status": "comparable" if complete else "incomplete_ha_day",
                           "ha_rows": len(rows)})
    return report
