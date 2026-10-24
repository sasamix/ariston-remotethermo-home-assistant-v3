"""Consistent completed-interval gas readings; never infer missing data as zero."""
from datetime import timedelta
from math import isfinite

LABELS = ('12-02 AM', '02-04 AM', '04-06 AM', '06-08 AM', '08-10 AM',
          '10-12 PM', '12-02 PM', '02-04 PM', '04-06 PM', '06-08 PM',
          '08-10 PM', '10-12 AM')
SUPPORTED = {
    prefix + suffix
    for prefix in ('Central heating gas ', 'Domestic hot water gas ')
    for suffix in ('current 2h volume', 'current 2h average flow', 'hourly average power')
}


def _values(report, series, period):
    if not isinstance(report, dict):
        return None
    rows = [r for r in report.get('histogramData', []) if isinstance(r, dict)
            and r.get('tab') == 'ConsumedGas' and r.get('series') == series
            and r.get('period') == period]
    if len(rows) != 1:
        return None
    items = rows[0].get('items', [])
    if len(items) != 12 or any(not isinstance(r, dict) for r in items):
        return None
    if tuple(r.get('x') for r in items) != LABELS:
        return None
    values = [r.get('y') for r in items]
    if any(isinstance(v, bool) or not isinstance(v, (float, int))
           or not isfinite(v) or v < 0 for v in values):
        return None
    return values


def reading(device, now):
    gas = getattr(device, 'r2_gas_snapshot_time', None)
    kwh = getattr(device, 'r2_kwh_snapshot_time', None)
    if not gas or not kwh or not gas.tzinfo or not kwh.tzinfo:
        return None
    gas, kwh = gas.astimezone(now.tzinfo), kwh.astimezone(now.tzinfo)
    if any(not 0 <= (now - at).total_seconds() <= 900 for at in (gas, kwh)):
        return None
    boundary = lambda at: at.replace(hour=at.hour // 2 * 2, minute=0, second=0, microsecond=0)
    end = boundary(gas)
    if end != boundary(kwh):
        return None
    start = end - timedelta(hours=2)
    period = 'CurrentDay' if start.date() == gas.date() else 'PreviousDay'
    index = start.hour // 2
    result = {}
    for series, prefix in (('Heating', 'Central heating gas '), ('Dhw', 'Domestic hot water gas ')):
        volume = _values(getattr(device, 'gas_physical_unit_data', None), series, period)
        energy = _values(getattr(device, 'r2_kwh_raw', None), series, period)
        if volume is None or energy is None:
            return None
        result[prefix + 'current 2h volume'] = volume[index]
        result[prefix + 'current 2h average flow'] = volume[index] / 2
        result[prefix + 'hourly average power'] = energy[index] / 2
    return {'values': result, 'attributes': {
        'source_status': 'verified_r2_interval',
        'measurement_period': 'last_completed_two_hour_interval',
        'source_interval_start': start.isoformat(), 'source_interval_end': end.isoformat(),
        'r2_gas_snapshot_time': gas.isoformat(), 'r2_kwh_snapshot_time': kwh.isoformat(),
    }}
