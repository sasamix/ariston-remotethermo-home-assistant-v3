"""Native downloadable Home Assistant diagnostics for Ariston GALEVO.

The standard integration/device "Download diagnostics" link delivers a JSON file
to the authenticated browser. No unprotected /local files and no custom button.
This module reads current HA state and already-fetched R2/API v2 data; it does
not log in, refresh cloud data, reset a meter, or alter Recorder statistics.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN, ENERGY_COORDINATOR


_SOURCE_ATTRIBUTES = (
    "last_r2_accepted_at",
    "r2_snapshot_time",
    "r2_source_total_m3",
    "r2_pending_increase_m3",
    "r2_source_gap_m3",
    "source_sync_status",
    "api_v2_snapshot_time",
    "source_interval_end",
    "source_through_date",
    "energy_counter_source",
    "source_year",
    "source_total_kwh",
    "source_snapshot_date",
)


def _scalar(value: Any) -> str | float | int | bool | None:
    """Only serializable, non-sensitive primitive values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _safe_histogram(report: Any) -> list[dict[str, Any]]:
    """Whitelist measured R2 histogram fields; never export login/session data."""
    if not isinstance(report, dict) or not isinstance(report.get("histogramData"), list):
        return []
    result = []
    for series in report["histogramData"][:200]:
        if not isinstance(series, dict):
            continue
        items = series.get("items")
        result.append({
            "tab": _scalar(series.get("tab")),
            "period": _scalar(series.get("period")),
            "series": _scalar(series.get("series")),
            "items": [
                {"x": _scalar(item.get("x")), "y": _scalar(item.get("y"))}
                for item in items[:64] if isinstance(item, dict)
            ] if isinstance(items, list) else [],
        })
    return result


def _safe_sequences(sequences: Any) -> list[dict[str, Any]]:
    """Only API-v2 energy interval keys and numeric quantities."""
    if not isinstance(sequences, list):
        return []
    result = []
    for row in sequences[:100]:
        if not isinstance(row, dict) or not isinstance(row.get("v"), list):
            continue
        result.append({
            "p": _scalar(row.get("p")),
            "k": _scalar(row.get("k")),
            "v": [
                value if isinstance(value, (int, float)) and not isinstance(value, bool)
                else None for value in row["v"][:100]
            ],
        })
    return result


def _report(hass: HomeAssistant, entry: ConfigEntry, selected_device_id: str | None = None) -> dict[str, Any]:
    """Collect a frozen read-only summary of registered and live metering."""
    config_data = hass.data.get(DOMAIN, {}).get(entry.unique_id, {})
    coordinator = config_data.get(ENERGY_COORDINATOR)
    device = getattr(coordinator, "device", None)
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    device_labels = {
        device_entry.id: {
            "name": device_entry.name,
            "name_by_user": device_entry.name_by_user,
            "disabled_by": str(device_entry.disabled_by) if device_entry.disabled_by else None,
        }
        for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    }

    entities = []
    # Always include the *entire* Ariston config entry. In particular, include
    # unavailable legacy sensors that remain bound to the old physical device.
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.platform != DOMAIN:
            continue
        state = hass.states.get(entity.entity_id)
        attrs = state.attributes if state else {}
        entities.append({
            "entity_id": entity.entity_id,
            "unique_id": entity.unique_id,
            "device_id": entity.device_id,
            "device_name": device_labels.get(entity.device_id, {}).get("name_by_user")
                           or device_labels.get(entity.device_id, {}).get("name"),
            "entity_category": str(entity.entity_category) if entity.entity_category else None,
            "disabled_by": str(entity.disabled_by) if entity.disabled_by else None,
            "state": state.state if state else None,
            "last_updated": state.last_updated.isoformat() if state else None,
            "unit": attrs.get("unit_of_measurement"),
            "source": {key: _scalar(attrs.get(key))
                       for key in _SOURCE_ATTRIBUTES if key in attrs},
        })

    timestamps = {}
    if device is not None:
        for key in (
            "r2_gas_snapshot_date", "r2_gas_snapshot_time",
            "r2_kwh_snapshot_date", "r2_kwh_snapshot_time",
            "energy_sequences_date", "energy_sequences_time",
        ):
            v = getattr(device, key, None)
            timestamps[key] = v.isoformat() if hasattr(v, "isoformat") else None

    physical = getattr(device, "gas_physical_unit_data", None)
    kwh = getattr(device, "r2_kwh_data", None)
    raw = getattr(device, "r2_kwh_raw", None)
    report = {
        "selected_device_id": selected_device_id,
        "energy_coordinator_present": coordinator is not None,
        "source_timestamps": timestamps,
        "device_registry": device_labels,
        "entities": sorted(entities, key=lambda row: row["entity_id"]),
        "r2_as_physical_unit_histogram": _safe_histogram(physical),
        "r2_as_kwh_histogram": _safe_histogram(kwh),
        "r2_raw_histogram": _safe_histogram(raw),
        "r2_raw_boiler_electricity": [
            {"period": _scalar(item.get("period")), "value": _scalar(item.get("value"))}
            for item in raw.get("boilerElectricity", [])[:60]
            if isinstance(item, dict)
        ] if isinstance(raw, dict) and isinstance(raw.get("boilerElectricity"), list)
        else [],
        "api_v2_sequences": _safe_sequences(
            getattr(device, "energy_sequences_snapshot", None)
        ),
    }
    return report


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return downloadable integration diagnostics."""
    return _report(hass, entry)


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> dict[str, Any]:
    """Return downloadable device diagnostics, including orphaned old meter states."""
    return _report(hass, entry, selected_device_id=device.id)
