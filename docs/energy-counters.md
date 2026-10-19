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
Unsupported layouts or mismatched snapshots are never counted as zero. Before the first verified baseline the entity stays unavailable; afterwards the last confirmed kWh value and its source time remain visible until both sources agree again.

## Accounting

Electricity and combined gas-plus-electricity entities track completed days
plus today's completed two-hour intervals. API-v2 p=1 contains 24 rolling
two-hour buckets. Its gas channels match all 12 PreviousDay R2 buckets and
today's completed buckets. At 12:31 on October 19 the electricity totals for
today are 0.165 kWh heating and 0.027 kWh DHW. Both snapshot requests must
complete in the same local two-hour block. The parser fails closed for a
different bucket layout or label format.
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

The shortest verified source period is two hours. The GALEVO energy
coordinator now polls API v2 every at most 10 minutes (even if an older
energy configuration says 60); R2 still polls every 2 minutes independently.
This reduces how late a new finished two-hour interval can be detected; it
does **not** create sub-two-hour electricity source readings. Polling may
take longer when the Ariston cloud is slow, unavailable or returning errors.
Counters advance only when API-v2 returns a new completed interval.
HA records the increment at receipt time, not retroactively in the original
interval's hourly statistics. This patch does not repair historical Energy
statistics or provide an instantaneous electricity meter. Cloud catch-up and
upward revisions similarly appear at receipt time. The HA local calendar must
match the boiler reporting calendar.

Electricity's `source_through_date` identifies the last date processed, including
the partial current day. `source_interval_end` identifies the end of the latest **verified** completed
two-hour block. Together with `source_snapshot_date`, it is persisted alongside
the counter: new R2 responses do not overwrite these attributes until a
matching API-v2 report is validated. The diagnostic attributes
`api_v2_snapshot_time`, `r2_snapshot_time` and `source_sync_status`
show the independently fetched cloud data and any temporary mismatch. Verified values survive temporary source
failures, two-hour boundaries and HA reloads.
`source_total_kwh` for these daily counters is the accumulated increment since
baseline, not an annual electricity total. `energy_counter_source` identifies
`API v2 two-hour intervals verified against R2`.

## Installation and verification

The installer accepts the original verified bases and the previous draft,
backs up the integration, preserves the release's heating-number migration,
and updates only __init__.py, sensor.py and energy_counter.py. It does not
modify the manifest, registry, configuration or existing stored counter data.

Run `python -m unittest discover -s tests -v`. Tests include the supplied API
arrays and independent R2 calendar values, duplicate polls, restart, corrections,
month/year transitions, gaps, mismatch rejection/retention, persistence of verified source time and actual sensor lifecycle
with HA doubles. Installation was checked against both previous patched bases.
Live-device validation is still required after restarting HA Core. Verify the
existing electricity entities become available and source_through_date is the
current date and source_interval_end matches the completed two-hour boundary.
No new entity IDs should appear.
