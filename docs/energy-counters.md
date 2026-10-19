# GALEVO energy counter correction

The six existing GALEVO kWh entities (electricity, gas energy and combined
energy for heating and DHW) previously exposed the last API-v2 interval sample.
Their shared reset timestamp changed whenever any consumption array changed.
This was not a reliable cumulative meter for Recorder.

The correction uses the already-fetched R2 `data.asKwh.histogramData` report.
Ariston's public R2 web frontend uses this section for electricity histograms;
its calendar periods are explicit. The existing gas-volume (m³) entities are
not migrated by this change.

## Accounting

- Use completed months from `CurrentYear` plus days 1 through today from
  `CurrentMonth`. Exclude the current month's year bucket and all `CurrentDay`
  buckets, so there is no overlapping addition of independently updated reports.
- Save the source year, source high-water mark, published counter base and
  original reset marker in HA storage, separately for each existing unique ID.
- Establish a baseline on first installation, retaining the last numeric
  entity state from restore data or Recorder. Do not add the historical yearly
  total to today's consumption. Existing IDs, units and `TOTAL` state class stay
  unchanged; the old reset marker becomes constant.
- Repeated polls add nothing. Downward revisions hold the source high-water
  mark; recovery to that mark is not counted again.
- At New Year use `PreviousYear` to finish the preceding year and then account
  for the new year. Missing previous-year data or a gap longer than one year
  pauses accumulation rather than guessing missing consumption.
- Keep a cached snapshot's original date; do not reinterpret it at midnight.
  Discard new kWh snapshots whose HTTP request crossed local midnight.
- Missing/incomplete source reports do not produce a fabricated zero.
- A restored state ahead of the last storage flush is a publication floor;
  it is not added to the persisted counter base and then counted again.

## Limits and rollout verification

This fixes future accumulation, not previously miscounted hourly/day/month
statistics. Historical repair is separate. Counter values start at their old
entity value and accumulate new consumption; they are not annual totals.

The reports have the cloud's update latency. After an outage, catch-up usage
appears when the report arrives; this patch does not backdate that usage.
The local HA calendar must match the boiler's reporting calendar. Calendar
bucket dates are inferred from the request date, because R2's relative period
names do not provide an authoritative snapshot timestamp. A stale response
across a calendar boundary remains a cloud-data limitation.

The supplied API-v2 debug log proves the old interval-sample problem, but does
not contain the actual `asKwh` R2 payload. The new parser must still be checked
on the user's HA before merging/releasing. It fails closed for unsupported
shapes. Do not claim that the local tests constitute a live-device test.

After installation and HA restart, inspect the six existing entity IDs. Their
attributes should include `energy_counter_source`, `source_year`,
`source_total_kwh`, and `source_snapshot_date`. Check two successful R2 refreshes
with real consumption and one HA restart. A repeat response/restart must not
increase consumption. No second set of entities should appear.

No version, dependency, registry or database modifications are included.

## Local validation

`python -m unittest discover -s tests -v`

The tests exercise calendar boundaries, unavailable/malformed data, revisions,
baseline migration, repeated polls, restart persistence, display-unit conversion
and a lost storage flush. Lifecycle tests execute the actual sensor class with
HA doubles; they do not replace testing in HA 2026.10.
