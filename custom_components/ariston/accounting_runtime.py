"""Opt-in, backed-up dated accounting for the existing eight GALEVO entities.

Writes run on Recorder's own queue, never concurrently with its statistics
compiler. All meter and gas-cost rows commit in one transaction. Entity IDs,
metadata IDs, the pre-repair history and the tariff history are preserved.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
import logging
from math import isfinite
from pathlib import Path
import os
import time
from zoneinfo import ZoneInfo

from sqlalchemy import select, func
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import Statistics, StatisticsMeta, StatisticsShortTerm
from homeassistant.components.recorder.tasks import RecorderTask
from homeassistant.components.recorder.util import session_scope
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .accounting_ledger import KEYS, UNITS, snapshot, merge, curves

_LOGGER = logging.getLogger(__name__)


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+'.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, allow_nan=False, separators=(',',':'))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp,path)


def numeric(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value):
        raise ValueError('Non-numeric Recorder baseline')
    return float(value)


class AccountingTask(RecorderTask):
    def __init__(self, runtime, ledger, mapping, costmap, tariffs, dry_run=False):
        self.runtime, self.ledger, self.mapping = runtime, ledger, mapping
        self.costmap, self.tariffs, self.dry_run = costmap, tariffs, dry_run
        self.future = runtime.hass.loop.create_future()

    def run(self, instance):
        try:
            if getattr(self.runtime,"closed",False):
                raise RuntimeError("Accounting runtime was unloaded before transaction")
            result = self.execute(instance)
        except Exception as exc:
            self.runtime.hass.loop.call_soon_threadsafe(self.finish, None, exc)
        else:
            self.runtime.hass.loop.call_soon_threadsafe(self.finish, result, None)

    def finish(self, result, error):
        if self.future.done():
            return
        if error:
            self.future.set_exception(error)
        else:
            self.future.set_result(result)

    def execute(self, instance):
        origin = int(datetime.fromisoformat(self.ledger['start_date']).replace(
            tzinfo=ZoneInfo(self.ledger['timezone'])).timestamp())
        now = int(time.time())
        lines, resolutions = curves(self.ledger)
        through = datetime.fromisoformat(self.ledger['through']).timestamp()
        backups, changes, values = [], [], {}
        base = Path(self.runtime.hass.config.path('ariston_accounting'))
        with session_scope(session=instance.get_session()) as session:
            ids = list(self.mapping.values()) + list(self.costmap.values())
            metadata = {m.statistic_id:m for m in session.execute(
                select(StatisticsMeta).where(StatisticsMeta.statistic_id.in_(ids))).scalars()}
            if set(metadata) != set(ids):
                raise ValueError('Missing statistics metadata: '+str(set(ids)-set(metadata)))
            first_short = session.scalar(select(func.min(StatisticsShortTerm.start_ts)))
            if first_short is None:
                raise ValueError('Recorder has no five-minute statistics yet')
            for key in KEYS:
                entity = self.mapping[key]
                meta = metadata[entity]
                if not meta.has_sum or meta.unit_of_measurement not in {UNITS[key], 'm3' if UNITS[key]=='m³' else UNITS[key]}:
                    raise ValueError('Unexpected unit or statistics type: '+entity)
                anchor = session.scalar(select(Statistics).where(
                    Statistics.metadata_id == meta.id, Statistics.start_ts == origin-3600))
                if anchor is None:
                    raise ValueError('Missing hour before repair start: '+entity)
                anchor_sum, anchor_state = numeric(anchor.sum), numeric(anchor.state)
                if key.startswith("gas_m3_"):
                    anchor_state=numeric(self.ledger["meter_bases"][key])
                if key.startswith('total_'):
                    suffix=key.removeprefix('total_')
                    anchor_state=0.0
                    for component in ('gas_kwh_'+suffix,'electricity_'+suffix):
                        component_meta=metadata[self.mapping[component]]
                        component_anchor=session.scalar(select(Statistics).where(
                            Statistics.metadata_id==component_meta.id,Statistics.start_ts==origin-3600))
                        if component_anchor is None:raise ValueError('Missing component baseline')
                        anchor_state+=numeric(component_anchor.state)
                values[key] = round(anchor_state + lines[key].at(through), 6)
                if values[key] < 0:
                    raise ValueError('Negative reconstructed meter state: '+entity)
                targets = [(meta, lines[key], anchor_sum, anchor_state, True)]
                if key.startswith('gas_m3'):
                    cm = metadata[self.costmap[entity]]
                    if not cm.has_sum or cm.unit_of_measurement != 'KZT':
                        raise ValueError('Unexpected gas cost currency')
                    ca = session.scalar(select(Statistics).where(
                        Statistics.metadata_id == cm.id, Statistics.start_ts == origin-3600))
                    if ca is None:
                        raise ValueError('Missing gas cost baseline: '+cm.statistic_id)
                    targets.append((cm, lines[key].priced(self.tariffs['gas']), numeric(ca.sum), None, False))
                for target, curve, sum_base, state_base, meter in targets:
                    for model, step in ((Statistics,3600),(StatisticsShortTerm,300)):
                        begin = origin if step==3600 else max(origin, int(first_short))
                        end = (int(min(now, through))//step)*step
                        rows = {int(r.start_ts):r for r in session.execute(select(model).where(
                            model.metadata_id==target.id, model.start_ts>=begin,
                            model.start_ts<end)).scalars()}
                        for start in range(begin,end,step):
                            row = rows.get(start)
                            total = round(sum_base+curve.at(start+step),9)
                            state = round(state_base+curve.at(start+step),6) if meter else (row.state if row is not None else None)
                            # TOTAL meters have a fixed reset marker; source corrections
                            # are revisions, never a new lifetime/yearly meter reset.
                            reset = 0.0 if meter else (row.last_reset_ts if row is not None else None)
                            if row is not None and row.sum is not None and abs(float(row.sum)-total)<1e-8 and row.state==state and row.last_reset_ts==reset:
                                continue
                            backups.append({'table':model.__tablename__,'metadata_id':target.id,
                                'start_ts':start,'id':row.id if row is not None else None,
                                'before':None if row is None else {'sum':row.sum,'state':row.state,'last_reset_ts':row.last_reset_ts},
                                'after':{'sum':total,'state':state,'last_reset_ts':reset}})
                            changes.append((model,target.id,start,row,total,state,reset))
            report = {'source_at':self.ledger['at'], 'through':self.ledger['through'],
                      'start_date':self.ledger['start_date'], 'timezone':self.ledger['timezone'],
                      'rows':len(changes), 'values':values, 'resolution':resolutions,
                      'dry_run':self.dry_run}
            if self.dry_run:
                return report
            if changes:
                stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                path = base/'backups'/('accounting_'+stamp+'.json')
                atomic_json(path, {'report':report,'rows':backups})
                report['backup'] = str(path)
                for model,mid,start,row,total,state,reset in changes:
                    if row is None:
                        session.add(model(metadata_id=mid,start_ts=start,sum=total,state=state,last_reset_ts=reset))
                    else:
                        row.sum,row.state,row.last_reset_ts=total,state,reset
                session.flush()
                # Verify every modified and inserted row inside the same transaction.
                for model,mid,start,_,total,state,reset in changes:
                    row = session.scalar(select(model).where(model.metadata_id==mid,model.start_ts==start))
                    if row is None or abs(row.sum-total)>1e-8 or row.state != state:
                        raise RuntimeError('Accounting SQL verification failed')
        atomic_json(base/'last_report.json',report)
        return report


class HistoryRepairTask(AccountingTask):
    """One-off rewrite of statistics SUM rows before the accounting start.

    The window [start_date, accounting start) is re-allocated from the dated
    daily cloud reports (uniform within each day). Recorded meter STATE values
    and reset markers in the window are kept. Every row from the accounting
    start onward is shifted by the same SUM delta, so daily changes after the
    window are unchanged and the next hourly compile continues seamlessly.
    Runs on Recorder's queue in one transaction, with a JSON backup.
    """

    def __init__(self, runtime, window, origin, mapping, costmap, tariffs, dry_run=False):
        super().__init__(runtime, window, mapping, costmap, tariffs, dry_run)
        self.origin = int(origin)

    def execute(self, instance):
        window = self.ledger
        start = int(datetime.fromisoformat(window['start_date']).replace(
            tzinfo=ZoneInfo(window['timezone'])).timestamp())
        origin = self.origin
        if not start < origin or (origin-start) % 3600:
            raise ValueError('Invalid repair window')
        lines, resolutions = curves(window)
        backups, changes, summary = [], [], {}
        base = Path(self.runtime.hass.config.path('ariston_accounting'))
        with session_scope(session=instance.get_session()) as session:
            ids = list(self.mapping.values()) + list(self.costmap.values())
            metadata = {m.statistic_id:m for m in session.execute(
                select(StatisticsMeta).where(StatisticsMeta.statistic_id.in_(ids))).scalars()}
            if set(metadata) != set(ids):
                raise ValueError('Missing statistics metadata: '+str(set(ids)-set(metadata)))
            first_short = session.scalar(select(func.min(StatisticsShortTerm.start_ts)))
            targets = []
            for key in KEYS:
                entity = self.mapping[key]
                meta = metadata[entity]
                if not meta.has_sum or meta.unit_of_measurement not in {UNITS[key], 'm3' if UNITS[key]=='m³' else UNITS[key]}:
                    raise ValueError('Unexpected unit or statistics type: '+entity)
                targets.append((key, meta, lines[key]))
                if key.startswith('gas_m3'):
                    cm = metadata[self.costmap[entity]]
                    if not cm.has_sum or cm.unit_of_measurement != 'KZT':
                        raise ValueError('Unexpected gas cost currency')
                    targets.append((key+'_cost', cm, lines[key].priced(self.tariffs['gas'])))
            for label, meta, curve in targets:
                hour = lambda ts: session.scalar(select(Statistics).where(
                    Statistics.metadata_id == meta.id, Statistics.start_ts == ts))
                anchor, boundary = hour(start-3600), hour(origin-3600)
                if anchor is None or boundary is None:
                    raise ValueError('Missing hour row at repair boundary: '+meta.statistic_id)
                anchor_sum, old_end = numeric(anchor.sum), numeric(boundary.sum)
                new_end = round(anchor_sum + curve.at(origin), 9)
                delta = round(new_end - old_end, 9)
                summary[meta.statistic_id] = {
                    'window_before': round(old_end-anchor_sum, 6),
                    'window_after': round(curve.at(origin), 6),
                    'shift_after_window': round(delta, 6)}
                for model, step in ((Statistics,3600),(StatisticsShortTerm,300)):
                    rows = {int(r.start_ts):r for r in session.execute(select(model).where(
                        model.metadata_id == meta.id, model.start_ts >= start)).scalars()}
                    # Inside the window: every hour (missing hours are filled);
                    # five-minute rows only where Recorder still keeps them.
                    window_starts = range(start, origin, step) if step == 3600 else \
                        sorted(t for t in rows if t < origin)
                    for ts in window_starts:
                        row = rows.get(ts)
                        total = round(anchor_sum + curve.at(ts+step), 9)
                        if row is None:
                            state, reset = None, anchor.last_reset_ts
                        elif row.sum is not None and abs(float(row.sum)-total) < 1e-8:
                            continue
                        else:
                            state, reset = row.state, row.last_reset_ts
                        backups.append({'table':model.__tablename__,'metadata_id':meta.id,
                            'start_ts':ts,'id':row.id if row is not None else None,
                            'before':None if row is None else {'sum':row.sum,'state':row.state,'last_reset_ts':row.last_reset_ts},
                            'after':{'sum':total,'state':state,'last_reset_ts':reset}})
                        changes.append((model,meta.id,ts,row,total,state,reset))
                    # After the window: shift SUM only, keeping daily changes.
                    if abs(delta) <= 1e-9:
                        continue
                    for ts in sorted(t for t in rows if t >= origin):
                        row = rows[ts]
                        if row.sum is None:
                            continue
                        total = round(float(row.sum) + delta, 9)
                        backups.append({'table':model.__tablename__,'metadata_id':meta.id,
                            'start_ts':ts,'id':row.id,
                            'before':{'sum':row.sum,'state':row.state,'last_reset_ts':row.last_reset_ts},
                            'after':{'sum':total,'state':row.state,'last_reset_ts':row.last_reset_ts}})
                        changes.append((model,meta.id,ts,row,total,row.state,row.last_reset_ts))
            report = {'mode':'history_repair','source_at':window['at'],
                      'start_date':window['start_date'],
                      'accounting_start':datetime.fromtimestamp(origin, ZoneInfo(window['timezone'])).isoformat(),
                      'timezone':window['timezone'],'rows':len(changes),
                      'first_short_term':first_short,'statistics':summary,
                      'resolution':sorted({r for v in resolutions.values() for r in v.values()}),
                      'dry_run':self.dry_run}
            if self.dry_run:
                return report
            if changes:
                stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                path = base/'backups'/('history_repair_'+stamp+'.json')
                atomic_json(path, {'report':report,'rows':backups})
                report['backup'] = str(path)
                for model,mid,ts,row,total,state,reset in changes:
                    if row is None:
                        session.add(model(metadata_id=mid,start_ts=ts,sum=total,state=state,last_reset_ts=reset))
                    else:
                        row.sum = total
                session.flush()
                for model,mid,ts,_,total,state,reset in changes:
                    row = session.scalar(select(model).where(model.metadata_id==mid,model.start_ts==ts))
                    if row is None or abs(row.sum-total) > 1e-8 or row.state != state:
                        raise RuntimeError('History repair SQL verification failed')
        atomic_json(base/'last_history_repair.json', report)
        return report


class AccountingRuntime:
    def __init__(self,hass,entry,coordinator,entities,config):
        self.hass,self.entry,self.coordinator,self.entities,self.config=hass,entry,coordinator,entities,config
        self.store=Store(hass,1,'ariston.dated_accounting.'+entry.unique_id)
        self.ledger={}
        self.values={}
        self.error=None
        self.busy=False
        self.closed=False
        self.retry_handle=None
        self.reconcile_task=None
        self.statistics_through=None
        # Shared across reloads: a previous runtime must finish saving before
        # the replacement runtime reads or writes the same Store.
        self.publication_lock=hass.data.setdefault(
            "ariston_accounting_store_lock_"+entry.unique_id,asyncio.Lock())

    def value(self,key):
        return self.values.get(key)

    def attributes(self,key):
        return {'energy_counter_source':'verified dated R2/API v2 ledger',
                'source_interval_end':self.ledger.get('through'),
                'source_snapshot_date':self.ledger.get('at','')[:10] or None,
                'accounting_status':'waiting_for_source' if self.error else 'verified',
                'statistics_allocation':'two-hour estimates; daily fallback where intervals missing',
                'accounting_start_date':self.config['start_date']}

    async def start(self):
        async with self.publication_lock:
            saved=await self.store.async_load()
        self.ledger=(saved or {}).get('ledger') or deepcopy(self.config['seed'])
        confirmed=(saved or {}).get('values',{})
        if set(confirmed)==set(KEYS) and all(isinstance(v,(int,float)) and not isinstance(v,bool) and isfinite(v) and v>=0 for v in confirmed.values()):
            self.values=dict(confirmed)
        # Restore only previously committed values, never archived seed values.
        # Fresh source validation precedes every new Recorder transaction.
        def tick(_now):
            if not self.closed:
                self.hass.async_create_task(self.refresh())
        cancel=async_track_time_interval(self.hass,tick,timedelta(minutes=5))
        self.entry.async_on_unload(cancel)
        self.entry.async_on_unload(self.close)
        self.retry_handle=self.hass.loop.call_later(30,tick,None)

    def close(self):
        self.closed=True
        if self.retry_handle:
            self.retry_handle.cancel()

    async def refresh(self):
        if self.busy or self.closed:
            return
        self.busy=True
        if self.retry_handle:
            self.retry_handle.cancel()
            self.retry_handle=None
        try:
            from .diagnostics import _report
            d=_report(self.hass,self.entry)
            observation=snapshot(d)
            age=(dt_util.now()-datetime.fromisoformat(observation['at'])).total_seconds()
            if not 0<=age<=15*60:
                raise ValueError('Waiting for fresh matching Ariston reports')
            mapping={key:entity.entity_id for key,entity in self.entities.items()}
            if len(mapping)!=8 or any(not v for v in mapping.values()):
                raise ValueError('Waiting for all eight existing entity IDs')
            async with self.publication_lock:
                if self.closed:
                    return
                ledger=merge(self.ledger,observation,self.config['start_date'],self.hass.config.time_zone)
                if self.values:
                    # Preserve the confirmed lifetime offset. Tariffs and Recorder
                    # must never be prerequisites for a live meter update.
                    old_lines,_=curves(self.ledger)
                    new_lines,_=curves(ledger)
                    old_end=datetime.fromisoformat(self.ledger['through']).timestamp()
                    new_end=datetime.fromisoformat(ledger['through']).timestamp()
                    values={key:round(self.values[key]-old_lines[key].at(old_end)
                                      +new_lines[key].at(new_end),6) for key in KEYS}
                    if any(not isfinite(v) or v<0 for v in values.values()):
                        raise ValueError('Invalid reconstructed live meter')
                    await self.publish(ledger,values)
            if self.closed:
                return
            if self.reconcile_task is None or self.reconcile_task.done():
                self.reconcile_task=self.hass.async_create_background_task(
                    self.reconcile(ledger,mapping), "Ariston statistics reconciliation",
                    eager_start=False)
        except Exception as exc:
            if self.closed:
                return
            message=str(exc)
            if message!=self.error:
                _LOGGER.warning('Ariston dated accounting paused; retained last values: %s',message)
            self.error=message
            self.hass.states.async_set("sensor.ariston_accounting_status","waiting", {"friendly_name":"Проверка учёта Ariston", "through":self.ledger.get("through"), "error":message})
            if not self.values and not self.closed:
                self.retry_handle=self.hass.loop.call_later(30,lambda:self.hass.async_create_task(self.refresh()))
        finally:
            self.busy=False

    async def publish(self,ledger,values):
        # Save before publication so a restart cannot lose the accepted offset.
        await self.store.async_save({'ledger':ledger,'values':values})
        if self.closed:
            return
        self.ledger,self.values=ledger,values
        self.error=None
        for entity in self.entities.values():
            entity.async_write_ha_state()

    async def reconcile(self,ledger,mapping):
        """One background transaction; a slow Recorder never blocks refresh.

        Keep the tariff lock until the actual queued transaction finishes.
        Cancelling the waiter would not cancel Recorder's database work.
        """
        try:
            self.statistics_status('statistics_pending')
            async with self.hass.data.setdefault('tariff_accounting_lock',asyncio.Lock()):
                if self.closed:
                    return
                costmap,tariffs=await self.cost_inputs(mapping)
                task=AccountingTask(self,ledger,mapping,costmap,tariffs)
                get_instance(self.hass).queue_task(task)
                result=await asyncio.shield(task.future)
                if self.closed:
                    return
                # Only bootstrap may publish a Recorder result. An older queued
                # snapshot must never overwrite newer live readings or Store.
                async with self.publication_lock:
                    if self.closed:
                        return
                    if not self.values:
                        await self.publish(ledger,result['values'])
                self.statistics_through=ledger['through']
                self.statistics_status('verified' if not self.error and self.ledger==ledger else 'statistics_pending')
        except Exception as exc:
            if not self.closed:
                _LOGGER.warning('Ariston statistics reconciliation pending: %s',exc)
                self.statistics_status('statistics_pending',str(exc))

    async def cost_inputs(self,mapping):
        """Gas cost statistics and tariff history; call with the tariff lock held."""
        cost_sensors=self.hass.data.get('energy',{}).get('cost_sensors',{})
        costmap={mapping[key]:cost_sensors.get(mapping[key])
                 for key in ('gas_m3_heating','gas_m3_dhw')}
        if any(not v for v in costmap.values()):
            raise ValueError('Energy gas cost sensors are not ready')
        journal=Path(self.hass.config.path('tariff_cost_manager/pending_transaction.json'))
        if await self.hass.async_add_executor_job(journal.exists):
            raise ValueError('Waiting for tariff transaction recovery')
        path=Path(self.hass.config.path('tariff_cost_manager/history.json'))
        tariffs=await self.hass.async_add_executor_job(lambda:json.loads(path.read_text()))
        return costmap,tariffs

    async def repair_history(self,start_date,dry_run=True):
        """Re-allocate statistics before the accounting start from dated reports.

        Uses the same fresh, cross-checked cloud snapshot as live accounting.
        Days that the reports do not cover are rejected, never assumed zero.
        """
        if self.closed:
            raise RuntimeError('Accounting runtime is unloaded')
        first=date.fromisoformat(start_date)
        accounting_start=date.fromisoformat(self.config['start_date'])
        if first>=accounting_start:
            raise ValueError('start_date must be before the accounting start '+self.config['start_date'])
        from .diagnostics import _report
        observation=snapshot(_report(self.hass,self.entry))
        age=(dt_util.now()-datetime.fromisoformat(observation['at'])).total_seconds()
        if not 0<=age<=15*60:
            raise ValueError('Waiting for fresh matching Ariston reports')
        mapping={key:entity.entity_id for key,entity in self.entities.items()}
        if len(mapping)!=8 or any(not v for v in mapping.values()):
            raise ValueError('Waiting for all eight existing entity IDs')
        timezone=self.ledger.get('timezone') or self.hass.config.time_zone
        days={key:{} for key in KEYS}
        cursor=first
        while cursor<accounting_start:
            for key in KEYS:
                value=observation['days'][key].get(cursor.isoformat())
                if value is None:
                    raise ValueError(f'No dated report for {key} {cursor.isoformat()}')
                days[key][cursor.isoformat()]=value
            cursor+=timedelta(days=1)
        origin=datetime.combine(accounting_start,datetime.min.time(),ZoneInfo(timezone))
        window={'start_date':first.isoformat(),'timezone':timezone,'at':observation['at'],
                'through':origin.isoformat(),'days':days,'profiles':{}}
        async with self.hass.data.setdefault('tariff_accounting_lock',asyncio.Lock()):
            if self.closed:
                raise RuntimeError('Accounting runtime is unloaded')
            costmap,tariffs=await self.cost_inputs(mapping)
            task=HistoryRepairTask(self,window,origin.timestamp(),mapping,costmap,tariffs,dry_run)
            get_instance(self.hass).queue_task(task)
            result=await asyncio.shield(task.future)
        result['days']={key:days[key] for key in ('gas_m3_heating','gas_m3_dhw')}
        _LOGGER.warning('Ariston history repair %s: %s rows, backup %s',
                        'dry run' if dry_run else 'committed',result['rows'],result.get('backup'))
        return result

    def statistics_status(self,status,error=None):
        self.hass.states.async_set('sensor.ariston_accounting_status',status,{
            'friendly_name':'Проверка учёта Ariston',
            'through':self.statistics_through,
            'source_through':self.ledger.get('through'),
            'error':error})


async def attach(hass,entry,coordinator,sensors):
    path=Path(hass.config.path('ariston_accounting/config.json'))
    if not await hass.async_add_executor_job(path.exists):
        return
    config=await hass.async_add_executor_job(lambda:json.loads(path.read_text()))
    if config.get('gateway')!=entry.unique_id:
        return
    from .sensor import AristonCalendarEnergySensor,AristonGasEnergySensor
    names={'Central heating electricity consumption':'electricity_heating',
           'Domestic hot water electricity consumption':'electricity_dhw',
           'Central heating gas consumption':'gas_kwh_heating',
           'Domestic hot water gas consumption':'gas_kwh_dhw',
           'Central heating total energy consumption':'total_heating',
           'Domestic hot water total energy consumption':'total_dhw'}
    entities={}
    for entity in sensors:
        if isinstance(entity,AristonCalendarEnergySensor):
            key=names[entity.entity_description.key]
        elif isinstance(entity,AristonGasEnergySensor):
            key='gas_m3_'+('heating' if entity._series=='Heating' else 'dhw')
        else:
            continue
        entities[key]=entity
    if set(entities)!=set(KEYS):
        raise ValueError('Cannot attach accounting: expected all eight counters')
    runtime=AccountingRuntime(hass,entry,coordinator,entities,config)
    runtimes=hass.data.setdefault('ariston_accounting_runtimes',{})
    runtimes[entry.unique_id]=runtime
    entry.async_on_unload(lambda:runtimes.pop(entry.unique_id,None) if runtimes.get(entry.unique_id) is runtime else None)
    for key,entity in entities.items():
        entity._dated_accounting=runtime
        entity._dated_key=key
    await runtime.start()
