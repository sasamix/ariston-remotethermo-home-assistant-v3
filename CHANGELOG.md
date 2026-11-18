# Changelog

## 0.21.1 — 2026-11-18

- Governor: a block in the first hour after start (carried over from before a restart)
  pauses traffic but no longer lowers the budget — the rolling count is incomplete then.
  0.21.0 learned "limit 18/h" from such a block at 00:34 on 18 Nov and stayed at 40/h,
  skipping most schedule and quiet-time energy refreshes. A saved limit below 40/h is
  discarded on load and the budget returns to 120/h.
- The budget grows by 10/h after every hour without a block (was three hours) while it
  actually holds work back.

## 0.21.0 — 2026-11-18

Ariston answers `429 Requests are blocked` when one account sends too many requests.
Since 0.20.7 the integration sent about 200 requests per hour (energy every 2 minutes,
schedules every 3 minutes, menuItems with every state poll, SDK retries on 5xx), and
every poller backed off and probed on its own, so blocks kept coming back.

- Account-wide governor: every request of the account (API login, state, schedules,
  energy, bus errors, R2 web) passes one counter with a rolling one-hour budget.
- On a 429 *all* cloud traffic pauses (10 → 20 → 40 → 60 minutes), then one probe.
  The budget drops to 70 % of the rate that was blocked and grows by 10/h after three
  hours without a block only when it actually held work back, so it settles just under
  Ariston's limit. The pause and the learned budget survive reloads and restarts;
  a reload during a pause does not log in.
- While paused, entities keep their last values for up to 2 hours instead of becoming
  unavailable.
- Priorities: device state and energy right after a two-hour boundary may use the whole
  budget; menuItems 85 %; schedules, bus errors and quiet-time energy 60 %.
- Fewer requests for the same data: menuItems (signal strength, return temperature)
  every 15 minutes; energy every 15 minutes outside the post-boundary window; GALEVO
  energy settings/account every 6 hours (sequences only otherwise). The "properties
  only" retry no longer runs during a block.
- New state `sensor.ariston_cloud_status` (ok / paused / probing) with requests in the
  last hour, budget, per-category counts, skipped work and last block; the same data is
  in diagnostics.

## 0.20.8 — 2026-10-25

- Complete the 0.20.7 accounting/polling fixes with valid HA translation files.
  Remove invalid raw-state translation maps; preserve raw state values, select
  options, scenario names, names of entities and unique IDs. Static name labels
  remain localized. Dynamic/user-defined scenario names remain literal.
- Add original community-integration brand assets for HACS package validation.
- Scope HACS checks to custom-repository installation. Public-catalog admission
  checks for remote Issues/topics metadata are explicitly excluded; no package,
  manifest, branding, translation or runtime validation is disabled.
- Add regression checks for translation schemas and select-option compatibility.

## 0.20.7 — 2026-10-25

- Fix recurring accounting timer execution on Home Assistant's event loop. New
  energy responses now also trigger local accounting immediately, with no extra
  cloud requests. Keep last accepted readings on cloud failures.
- Set the default energy poll to two minutes; GALEVO effective cadence is bounded
  to 2–10 minutes, retaining slower configured polling up to that existing cap.
  Existing explicit options remain in place. Validate positive option ranges.
- Correct R2 HTTP 429/5xx and DNS backoff, cap prolonged-outage arithmetic, and
  bound SDK coordinator requests to 90 seconds. Preserve auth error handling.
- Repair the seam between reconstructed statistics and existing provisional
  rows; refresh Recorder's latest short-term row cache. Align gas cost baselines
  in both statistics resolutions, and skip superseded queued repairs.
- Expose accounting attempt/success timestamps in status and diagnostics.
- Preserve entity IDs, daily source totals, reset markers and tariff history.

## 0.20.6 — 2026-10-24

- Adaptive cloud pacing: every poller runs at its configured speed while the Ariston
  cloud answers; HTTP 429/5xx, timeouts and connection errors double the interval
  (up to 30 minutes) and the next successful answer restores it. Authorization errors
  are not treated as overload.
- New option "Weekly schedules scan interval" (seconds, default 180, minimum 60).
  Schedules refresh on their own timer instead of every state poll.
- Energy: after each two-hour boundary, API v2 + R2 are polled every 2 minutes until
  the finished interval appears (at most 40 minutes), then back to the normal energy
  interval; no faster polling when no new data is expected.
- R2 metering backs off on its own when it fails, without delaying API v2.

## 0.20.5 — 2026-10-24

- About 80 % fewer cloud requests: weekly DHW/heating programs are refreshed at most
  every 10 minutes together with the normal state poll (was every 30 seconds plus every
  poll); HA still re-reads a program right after writing it. The separate 2-minute R2
  timer is removed; R2 metering is read with API v2 on every energy refresh (≤10 min
  for GALEVO), so both snapshots stay in the same two-hour interval.
- Gas cost statistics no longer count the newest two-hour interval twice while it is
  waiting for the next reconciliation: the newest hourly cost row takes the cost
  entity's current state and reset marker as Recorder's baseline.
- "Gas — current month" sensors use `total_increasing`, so the monthly restart is a
  meter reset instead of negative consumption in long-term statistics.
- 0.20.4 was published automatically with the one-off `repair_history` service; that
  service is not part of the integration and is absent from 0.20.5.

## 0.20.3 — 2026-10-24

Release of the work merged to `main` on 2026-10-21 … 2026-10-24 (PR #5). Until this
release HACS kept offering v0.20.2 by default, so "Redownload" reinstalled the
September code that still aborted setup on a temporary HTTP 500.

- Setup no longer fails when the Ariston cloud returns HTTP 500 for the bus-error
  list or the initial energy report; those coordinators retry on their normal
  interval. Essential device state and authentication failures still behave as before.
- R2 metering and time-program refreshes no longer reset the main API polling deadline;
  concurrent R2 requests are serialized.
- GALEVO electricity counters use API-v2 daily values (k=20 heating, k=21 DHW)
  cross-checked against R2 monthly totals; unverified layouts are never counted as zero.
- Counters are persisted with per-day high-water marks: restarts, repeated polls and
  temporary outages neither add usage nor publish placeholder zeros.
- Live meter readings no longer wait for tariff recovery, cost entity registration or
  Recorder reconciliation; statistics reconciliation runs as a background task and
  cannot overwrite newer readings. Store writes are serialized per gateway, including
  unload/reload during a pending save.
- Fixed HA 2026.10 metering device registration (deprecated `via_device` removed).
- Entity unique IDs, entity IDs, units, `state_class`, the `ariston==0.19.9` SDK pin,
  restore stores and Recorder statistics are unchanged. Upgrade through HACS; do not
  remove the integration.

## 0.20.2 — 2026-09-24

- Restored the `Ariston System heating flow temperature 1` and `offset 1` registry IDs used before localization.
- Re-enabled those entries when version 0.20.1 disabled them, preserving user-disabled entries.

## 0.20.1 — 2026-09-24

- Attempted to restore the historical unique IDs of the heating flow temperature and offset controls; corrected in 0.20.2.
- Disabled existing System heating flow registry entries in error; corrected in 0.20.2.
- Separated scenario name editing from the save buttons for DHW and heating.
- Kept DHW and heating scenario names separate even when saved schedules are identical.
- Shortened English and Russian scenario labels to distinguish DHW and heating controls.

## 0.20.0 — 2026-09-24

- Added full Home Assistant entity localization for English and Russian.
- Entity names now follow the language selected in Home Assistant while legacy unique IDs remain unchanged.
- Added localized heating and DHW scenario controls, scenario option labels, gas type/unit labels, and active-program states.
- Added per-zone heating scenario read/write support with custom scenario naming.
- Added DHW scenario naming and custom scenario persistence.
- Added live R2 gas energy counters for heating and DHW.
- Added resilient Ariston state refresh when the vendor menuItems endpoint temporarily returns HTTP 500.
- Preserved existing entity IDs and Recorder history across the fork migration and localization changes.
