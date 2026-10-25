# Gas statistics reconciliation, October 25

Based on v0.20.6 (a28a165). No entity IDs, units, reset markers changes. Release version: 0.20.7.

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

## Afternoon freeze and polling

The 16:57 diagnostic contains valid completed gas reports through 16:00, but
entities still show 12:00. The report is accepted by the unmodified snapshot
parser (1362.04 heating / 990.86 DHW m³). The recurring sync `tick` was missing
HA's callback decorator, so HA classifies it as an executor job; calling
async_create_task from that thread violates HA's scheduling contract. The
initial loop.call_later invocation works, explaining the one-shot behavior.

The tick now uses @callback and background tasks, and the energy coordinator
notifies the accounting runtime directly. A real HA interval scheduler test
runs it twice and verifies Callback job type. A coordinator-listener test
verifies immediate local refresh without another cloud request. Both downloads
are included as source-only anonymized fixtures.

Cloud reports still use completed two-hour measurements: the supplied current
unfinished block is zero, so faster measurement availability cannot be inferred.
Polling is not claimed to manufacture instantaneous measured consumption.
Default energy polling is now 2 minutes, GALEVO bounds 2–10 minutes. Existing
explicit options are retained. The separate schedule interval is retained.
SDK requests time out after 90 seconds; overload backs off up to 30 minutes.
R2 429/5xx string errors and aiohttp DNS subclasses now trigger backoff.

Validation includes Home Assistant 2026.10.3, Python 3.14.8 and ariston 0.19.9.
No claim is made to have installed the build on the user's HA or queried their
cloud account. The release workflow separately tests HA 2026.10.2.
