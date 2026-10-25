"""Opt-in, backed-up dated accounting for the existing eight GALEVO entities.

Writes run on Recorder's own queue, never concurrently with its statistics
compiler. All meter and gas-cost rows commit in one transaction. Entity IDs,
metadata IDs, the pre-repair history and the tariff history are preserved.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
import logging
from math import isfinite
from pathlib import Path
import os
import time
from zoneinfo import ZoneInfo

from sqlalchemy import select, func
from homeassistant.core import callback
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import Statistics, StatisticsMeta, StatisticsShortTerm
from homeassistant.components.recorder.tasks import RecorderTask
from homeassistant.components.recorder.statistics import (
    get_short_term_statistics_run_cache,
    cache_latest_short_term_statistic_id_for_metadata_id,
)
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
    def __init__(self, runtime, ledger, mapping, costmap, tariffs, dry_run=False, cost_states=None):
        self.runtime, self.ledger, self.mapping = runtime, ledger, mapping
        self.costmap, self.tariffs, self.dry_run = costmap, tariffs, dry_run
        # Current (state, last_reset_ts) of each gas cost entity, read on the
        # event loop right before queueing; see the cost baseline below.
        self.cost_states = cost_states or {}
        self.future = runtime.hass.loop.create_future()

    def run(self, instance):
        try:
            if getattr(self.runtime,"closed",False):
                raise RuntimeError("Accounting runtime was unloaded before transaction")
            result = self.execute(instance)
            # Direct SQL inserts must refresh Recorder's latest-row ID cache.
            # Otherwise its next compile can use a row before our repair.
            cache = get_short_term_statistics_run_cache(instance.hass)
            with session_scope(session=instance.get_session()) as session:
                for metadata_id in result["metadata_ids"]:
                    cache_latest_short_term_statistic_id_for_metadata_id(
                        cache, session, metadata_id)
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
        current = getattr(self.runtime, 'ledger', {})
        if current.get('at') and current['at'] != self.ledger['at']:
            raise ValueError('Newer accounting snapshot is waiting; skip stale repair')
        now = int(time.time())
        lines, resolutions = curves(self.ledger)
        through = datetime.fromisoformat(self.ledger['through']).timestamp()
        backups, changes, values = [], [], {}
        cost_states = getattr(self, 'cost_states', None) or {}
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
                        # Rebase only already compiled rows observed with this
                        # snapshot. The meter is held after `through`; these are
                        # provisional holds, not additional measured consumption.
                        observed_end = int(min(now, datetime.fromisoformat(
                            self.ledger['at']).timestamp())) // step * step
                        rows = {int(r.start_ts):r for r in session.execute(select(model).where(
                            model.metadata_id==target.id, model.start_ts>=begin,
                            model.start_ts<max(end, observed_end))).scalars()}
                        starts = sorted(set(range(begin,end,step)) | set(rows))
                        for start in starts:
                            row = rows.get(start)
                            total = round(sum_base+curve.at(start+step),9)
                            state = round(state_base+curve.at(start+step),6) if meter else (row.state if row is not None else None)
                            # TOTAL meters have a fixed reset marker; source corrections
                            # are revisions, never a new lifetime/yearly meter reset.
                            reset = 0.0 if meter else (row.last_reset_ts if row is not None else None)
                            newest = session.scalar(select(func.max(model.start_ts)).where(
                                model.metadata_id==target.id)) if (not meter and start==starts[-1]) else None
                            if (not meter and start == starts[-1]
                                    and target.statistic_id in cost_states
                                    and (newest is None or newest <= start)):
                                # The newest compiled cost row is the baseline
                                # for Recorder's next compile (sum + state delta).
                                # Its SUM now already includes the reconciled
                                # interval, and the cost entity already counted
                                # that interval when the gas meter changed. Use
                                # the entity's current state as the baseline, so
                                # the same interval is not booked a second time.
                                state, reset = cost_states[target.statistic_id]
                            if row is not None and row.sum is not None and abs(float(row.sum)-total)<1e-8 and row.state==state and row.last_reset_ts==reset:
                                continue
                            backups.append({'table':model.__tablename__,'metadata_id':target.id,
                                'start_ts':start,'id':row.id if row is not None else None,
                                'before':None if row is None else {'sum':row.sum,'state':row.state,'last_reset_ts':row.last_reset_ts},
                                'after':{'sum':total,'state':state,'last_reset_ts':reset}})
                            changes.append((model,target.id,start,row,total,state,reset))
            report = {'source_at':self.ledger['at'], 'through':self.ledger['through'],
                      'start_date':self.ledger['start_date'], 'timezone':self.ledger['timezone'],
                      'metadata_ids':[m.id for m in metadata.values()],
                      'provisional_holds_through':self.ledger['at'],
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
        self.last_attempt=None
        self.last_success=None
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
        @callback
        def tick(_now=None):
            if not self.closed:
                self.hass.async_create_background_task(
                    self.refresh(), "Ariston live accounting refresh", eager_start=False)
        cancel=async_track_time_interval(self.hass,tick,timedelta(minutes=5))
        self.entry.async_on_unload(cancel)
        if self.coordinator is not None:
            self.entry.async_on_unload(self.coordinator.async_add_listener(tick))
        self.entry.async_on_unload(self.close)
        self.retry_handle=self.hass.loop.call_later(30,tick,None)

    def close(self):
        self.closed=True
        if self.retry_handle:
            self.retry_handle.cancel()

    async def refresh(self):
        if self.closed:
            return
        if self.busy:
            attempted=getattr(self,'last_attempt',None)
            if attempted and (dt_util.now()-datetime.fromisoformat(attempted)).total_seconds()>120:
                self.statistics_status('waiting','Live accounting refresh is taking too long')
            return
        self.busy=True
        self.last_attempt=dt_util.now().isoformat()
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
        self.last_success=dt_util.now().isoformat()
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
                task=AccountingTask(self,ledger,mapping,costmap,tariffs,
                                    cost_states=self.cost_states(costmap.values()))
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

    def cost_states(self,entity_ids):
        """Current state and reset marker of each gas cost entity."""
        result={}
        for entity_id in entity_ids:
            state=self.hass.states.get(entity_id)
            if state is None:
                continue
            try:
                value=float(state.state)
            except (TypeError,ValueError):
                continue
            if not isfinite(value):
                continue
            last_reset=state.attributes.get('last_reset')
            parsed=dt_util.parse_datetime(last_reset) if isinstance(last_reset,str) else None
            result[entity_id]=(value, parsed.timestamp() if parsed else None)
        return result

    def statistics_status(self,status,error=None):
        self.hass.states.async_set('sensor.ariston_accounting_status',status,{
            'friendly_name':'Проверка учёта Ariston',
            'through':self.statistics_through,
            'source_through':self.ledger.get('through'),
            'last_attempt':getattr(self,'last_attempt',None),
            'last_success':getattr(self,'last_success',None),
            'source_received_at':self.ledger.get('at'),
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
    for key,entity in entities.items():
        entity._dated_accounting=runtime
        entity._dated_key=key
    await runtime.start()
