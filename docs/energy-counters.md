# GALEVO electricity counters

The original entities exposed API-v2 interval samples with a shared changing
reset marker. The first draft then incorrectly assumed this boiler's R2
`asKwh` contained electricity. The actual response contains only gas there;
`asKwhRaw.boilerElectricity` provides combined electricity monthly totals.

## Verified source

The supplied October 19 API-v2 p=3 arrays contain September's 30 completed days
and October 1–18. k=20 is heating electricity, k=21 is DHW electricity.
September sums are 0 + 3.263 kWh; October sums are 13.396 + 1.393 kWh.
Both exactly match the independent R2 boilerElectricity monthly totals.
The parser verifies both totals, array lengths, and each gas day's values
against R2's explicit calendar labels before assigning dates to electricity.
Unsupported layouts or mismatched snapshots remain unavailable, not zero.

## Accounting

Electricity and combined gas-plus-electricity entities track completed days.
Each day's high-water mark is persisted with the counter. Repeated polls and
restarts add nothing; new days and upward corrections add only their delta.
A downward correction and recovery do not re-add previously counted usage.
The first valid report establishes a baseline while preserving the old value
and reset marker. It does not book the historical monthly total as new usage.
Month/year transitions retain overlapping daily marks. A missing gap beyond
the returned window pauses accounting instead of inventing consumption.
API and R2 snapshots must have the same HA local date; requests crossing
midnight are not relabelled. R2 data cannot reinterpret an old API snapshot.

Gas kWh retains the preceding calendar-counter implementation. Gas volume m³
entities are unchanged. IDs, units, state_class, version and dependencies are
unchanged. Storage and Recorder statistics are not deleted or rewritten.

## Limits

This is daily accounting: today's electricity is added after the cloud returns
that completed day. HA records that increment at arrival time, not retroactively
in yesterday's hourly statistics. This patch does not repair historical Energy
statistics or provide an instantaneous electricity meter. Cloud catch-up and
upward revisions similarly appear at receipt time. The HA local calendar must
match the boiler reporting calendar.

Electricity's `source_through_date` identifies the last completed day processed.
`source_total_kwh` for these daily counters is the accumulated increment since
baseline, not an annual electricity total. `energy_counter_source` identifies
`API v2 completed days verified against R2`.

## Installation and verification

The installer accepts the original verified bases and the previous draft,
backs up the integration, preserves the release's heating-number migration,
and updates only __init__.py, sensor.py and energy_counter.py. It does not
modify the manifest, registry, configuration or existing stored counter data.

Run `python -m unittest discover -s tests -v`. Tests include the supplied API
arrays and independent R2 calendar values, duplicate polls, restart, corrections,
month/year transitions, gaps, mismatch rejection and actual sensor lifecycle
with HA doubles. Installation was checked against both previous patched bases.
Live-device validation is still required after restarting HA Core. Verify the
existing electricity entities become available and source_through_date is the
last completed day. No new entity IDs should appear.
