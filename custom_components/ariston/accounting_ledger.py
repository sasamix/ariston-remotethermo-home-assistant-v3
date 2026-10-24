"""Dated accounting for GALEVO. Pure functions; no Recorder/HA side effects.

Cloud reports are at best two-hourly. Hourly and five-minute allocations are
estimates within those intervals, scaled to the authoritative completed day.
Missing interval profiles use a uniform daily allocation, explicitly reported.
"""
from bisect import bisect_right
from datetime import date, datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

from .energy_reconciliation import ENTITIES, verified_daily_sources
from .energy_counter import electricity_periods, nonnegative

KEYS = tuple(ENTITIES)
UNITS = {k: 'm³' if k.startswith('gas_m3') else 'kWh' for k in KEYS}
API_KEYS = {'gas_kwh_heating': (7,), 'gas_kwh_dhw': (10,),
            'electricity_heating': (20,), 'electricity_dhw': (21,),
            'total_heating': (7, 20), 'total_dhw': (10, 21)}
LABELS = ['12-02 AM','02-04 AM','04-06 AM','06-08 AM','08-10 AM','10-12 PM',
          '12-02 PM','02-04 PM','04-06 PM','06-08 PM','08-10 PM','10-12 AM']


def snapshot(diagnostic):
    d = diagnostic.get('data', diagnostic)
    days = verified_daily_sources(d)
    at = datetime.fromisoformat(d['source_timestamps']['energy_sequences_time'])
    gas_at = datetime.fromisoformat(d['source_timestamps']['r2_gas_snapshot_time'])
    if at.date() != gas_at.date() or at.hour // 2 != gas_at.hour // 2:
        raise ValueError('Gas and API snapshots are in different two-hour intervals')
    boundary = at.replace(hour=(at.hour//2)*2, minute=0, second=0, microsecond=0)
    arrays = {r['k']: r['v'] for r in d['api_v2_sequences'] if r.get('p') == 1 and r.get('k') in (7,10,20,21)}
    raw = {'histogramData': d['r2_raw_histogram'], 'boilerElectricity': d['r2_raw_boiler_electricity']}
    by_key = {key: dict(days[entity]) for key, entity in ENTITIES.items()}
    profiles = {key: {} for key in KEYS}
    for key, indexes in API_KEYS.items():
        for i in range(24):
            start = boundary - timedelta(hours=48-2*i)
            slots = profiles[key].setdefault(start.date().isoformat(), [None]*12)
            slots[start.hour//2] = round(sum(arrays[k][i] for k in indexes), 6)
        count = boundary.hour//2
        by_key[key][at.date().isoformat()] = round(sum(sum(arrays[k][-count:]) for k in indexes),6) if count else 0.0
    meter_totals = {}
    for key, series in [('gas_m3_heating','Heating'), ('gas_m3_dhw','Dhw')]:
        for period, offset in [('PreviousDay', -1), ('CurrentDay', 0)]:
            reports = [r for r in d['r2_as_physical_unit_histogram']
                       if r.get('tab') == 'ConsumedGas' and r.get('series') == series and r.get('period') == period]
            if len(reports) != 1:
                raise ValueError('Missing physical interval report')
            items = reports[0]['items']
            if len(items) != 12 or [r.get('x') for r in items] != LABELS:
                raise ValueError('Unknown physical interval labels')
            values = [nonnegative(r.get('y')) for r in items]
            if any(v is None for v in values):
                raise ValueError('Invalid physical consumption')
            day = (at.date()+timedelta(days=offset)).isoformat()
            if offset == 0:
                count = boundary.hour//2
                if any(v > .005 for v in values[count:]):
                    raise ValueError('Physical report is ahead of its snapshot')
                by_key[key][day] = round(sum(values[:count]),6)
                values = values[:count] + [None]*(12-count)
            profiles[key][day] = values
        year_reports=[r for r in d['r2_as_physical_unit_histogram'] if r.get('tab')=='ConsumedGas' and r.get('series')==series and r.get('period')=='CurrentYear']
        if len(year_reports)!=1 or len(year_reports[0]['items'])!=12:
            raise ValueError('Missing physical calendar-year baseline')
        months=[nonnegative(item.get('y')) for item in year_reports[0]['items'][:at.month-1]]
        if any(v is None for v in months):raise ValueError('Invalid physical calendar-year baseline')
        first=at.date().replace(day=1).isoformat()
        meter_totals[key]=round(sum(months)+sum(v for day,v in by_key[key].items() if day>=first),6)
    return {'at': at.isoformat(), 'through': boundary.isoformat(), 'days': by_key, 'profiles': profiles, 'meter_totals':meter_totals}


def merge(saved, observation, start_date, timezone):
    """Replace dated observations, including downward revisions, idempotently."""
    result = {**saved, 'start_date':start_date, 'timezone':timezone}
    if saved.get('at') and datetime.fromisoformat(observation['at']) < datetime.fromisoformat(saved['at']):
        return result
    result['at'], result['through'] = observation['at'], observation['through']
    result['days'], result['profiles'] = {}, {}
    for key in KEYS:
        result['days'][key] = dict(saved.get('days',{}).get(key,{}))
        result['profiles'][key] = {d:list(v) for d,v in saved.get('profiles',{}).get(key,{}).items()}
        result['days'][key].update({d:v for d,v in observation['days'][key].items() if d >= start_date})
        for day, slots in observation['profiles'][key].items():
            if day < start_date:
                continue
            prior = result['profiles'][key].get(day, [None]*12)
            result['profiles'][key][day] = [v if v is not None else prior[i] for i,v in enumerate(slots)]
    # Seed native gas counters from the real cloud meter, not an already
    # corrupted HA state. Keep this offset fixed across later year rollovers;
    # dated consumption remains continuous and the historical SUM is separate.
    result['meter_bases']=dict(saved.get('meter_bases',{}))
    for key,total in observation.get('meter_totals',{}).items():
        if key not in result['meter_bases']:
            base=round(total-sum(result['days'][key].values()),6)
            if base<0:raise ValueError('Cannot infer an initial cross-year gas baseline')
            result['meter_bases'][key]=base
    return result


class Curve:
    """Piecewise linear accumulated consumption in timestamp seconds."""
    def __init__(self, segments):
        self.segments = sorted(segments)
        self.ends, self.prefix = [], [0.0]
        prior_end = None
        for left,right,amount in self.segments:
            if right <= left or not isfinite(amount) or amount < 0:
                raise ValueError('Invalid consumption segment')
            if prior_end is not None and left < prior_end:
                raise ValueError('Overlapping consumption segments')
            self.ends.append(right)
            self.prefix.append(self.prefix[-1] + amount)
            prior_end = right

    def at(self, timestamp):
        i = bisect_right(self.ends, timestamp)
        value = self.prefix[i]
        if i < len(self.segments):
            left, right, amount = self.segments[i]
            if timestamp > left:
                value += amount*(timestamp-left)/(right-left)
        return value

    def priced(self, tariffs):
        if not tariffs or any(b[0] <= a[0] for a,b in zip(tariffs,tariffs[1:])):
            raise ValueError('Missing or unordered tariff history')
        if any(not isfinite(float(t)) or not isfinite(float(v)) or v < 0 for t,v in tariffs):
            raise ValueError('Invalid tariff history')
        events = [t for t,_ in tariffs]
        segments = []
        for left,right,amount in self.segments:
            cuts = [left]+[t for t in events if left < t < right]+[right]
            for a,b in zip(cuts,cuts[1:]):
                index = bisect_right(events,a)-1
                if index < 0:
                    raise ValueError('Tariff not defined at source period')
                segments.append((a,b,amount*(b-a)/(right-left)*tariffs[index][1]))
        return Curve(segments)


def curves(ledger):
    zone = ZoneInfo(ledger['timezone'])
    through = datetime.fromisoformat(ledger['through']).astimezone(zone)
    first = date.fromisoformat(ledger['start_date'])
    result, resolutions = {}, {}
    for key in KEYS:
        segments, resolution = [], {}
        cursor = first
        while cursor <= through.date():
            day = cursor.isoformat()
            left = datetime.combine(cursor, datetime.min.time(), zone)
            right = min(left+timedelta(days=1), through)
            if right <= left:
                break
            amount = ledger['days'][key].get(day)
            if nonnegative(amount) is None:
                raise ValueError(f'Missing dated source: {key} {day}')
            slots = ledger.get('profiles',{}).get(key,{}).get(day, [None]*12)
            count = 12 if cursor < through.date() else through.hour//2
            profile = slots[:count]
            if len(profile) == count and all(nonnegative(v) is not None for v in profile) and sum(profile) > 0:
                denominator = sum(profile)
                for i,v in enumerate(profile):
                    a,b = left+timedelta(hours=i*2),left+timedelta(hours=i*2+2)
                    segments.append((a.timestamp(),b.timestamp(),amount*v/denominator))
                resolution[day] = 'two_hour_profile_scaled_to_daily_report'
            else:
                segments.append((left.timestamp(),right.timestamp(),amount))
                resolution[day] = 'daily_uniform_estimate' if amount else 'zero_consumption'
            cursor += timedelta(days=1)
        result[key], resolutions[key] = Curve(segments), resolution
    # Combined energy is exactly the sum of its two components at every
    # timestamp, not an independently rescaled approximation.
    for suffix in ('heating','dhw'):
        gas,electric=result['gas_kwh_'+suffix],result['electricity_'+suffix]
        points=sorted({x for curve in (gas,electric) for a,b,_ in curve.segments for x in (a,b)})
        result['total_'+suffix]=Curve([(a,b,max(0.0,gas.at(b)-gas.at(a)+electric.at(b)-electric.at(a)))
                                      for a,b in zip(points,points[1:])])
    return result, resolutions
