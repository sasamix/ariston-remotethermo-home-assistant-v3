# Repairing statistics before the accounting start

Dated accounting (from `start_date` in `ariston_accounting/config.json`) keeps the
Energy statistics aligned with Ariston's reports. Rows recorded before that date come
from the older counters and can be shifted between days.

`ariston.repair_history` rewrites that earlier window:

```yaml
action: ariston.repair_history
data:
  start_date: "2026-10-01"
  dry_run: true   # set to false to write
```

- Source: the same fresh, cross-checked snapshot as live accounting. Ariston returns
  daily values for the previous and current month only; earlier dates are rejected.
- Allocation: each day's reported total spread uniformly over its 24 hours (no
  two-hour profiles exist for those days).
- Meter `state` values, reset markers, entity IDs and metadata are not changed.
- Rows from the accounting start onward get the same SUM shift, so every hourly,
  daily and five-minute change after the window is preserved; the next regular
  reconciliation finds nothing to change.
- Gas cost statistics are recalculated from the tariff history; a tariff gap aborts.
- One Recorder transaction; before/after values of every row are saved to
  `ariston_accounting/backups/history_repair_<time>.json`. Re-running is a no-op.
