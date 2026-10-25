# Gas statistics reconciliation, October 25

Based on v0.20.6 (a28a165). No entity IDs, units, reset markers or version changes.

The supplied October 25 report disagrees internally for October 24: heating
intervals total 9.11 m³ versus calendar day 6.18; DHW 3.50 versus 2.29.
Daily totals remain the accounting authority. Clamping the live counter would
preserve over-counting and is deliberately not introduced.

Fixes:
- Rebase existing, completed Recorder rows between the source boundary and the
  snapshot time onto the same cumulative baseline. These are provisional held
  readings, not newly measured intervals. Do not insert unobserved tail rows or
  modify rows after the captured snapshot.
- Refresh HA's short-term latest-row ID cache after the SQL transaction. Without
  this, a subsequent Recorder compile can continue from a superseded row.
- Apply cost-state baselines to the latest affected short-term row as well as
  hourly rows, avoiding a second booking of the same source increase.
- Reject a queued reconciliation if a different live snapshot is already active.

Validation: unit/database suite, actual Home Assistant 2026.10.3 Recorder models
and cache, ariston 0.19.9, plus integration package build. Tests cover all eight
meter channels, both costs, transaction rollback, idempotence, snapshot cutoff,
and the anonymized October 25 source report.

Limitations: the diagnostic is a single snapshot, not a Recorder export.
The exact historical midnight notch cannot be uniquely attributed to one cause.
Raw live TOTAL states can still decrease when Ariston revises a past day; the
repair corrects dated statistics rather than inventing consumption to hide this.
No Home Assistant installation or production database was accessed.
