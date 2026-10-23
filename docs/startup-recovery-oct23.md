# Startup and polling recovery (2026-10-23)

A bus-error HTTP 500 previously raised `ConfigEntryNotReady` before platforms
were forwarded. Climate and all energy entities consequently failed to load.
An initial energy-report failure had the same effect. These two optional
coordinators now retain their failed state, allow platform setup, and retry
through their registered entity listeners. Essential device state still has to
load; authentication failures still trigger reauthentication.

A separate timer defect prevented recovery: each two-minute R2 response called
`async_set_updated_data` on the ten-minute API coordinator. Home Assistant resets
that coordinator's polling deadline on every such call. R2 now notifies listeners
without modifying the API polling deadline or its success flag. Program updates
use the same notification-only behavior. Concurrent R2 requests share one lock.

The complete Ariston source includes the previously distributed dated-counter
modules and concrete sensor publication fixes. Existing IDs, version 0.20.2,
SDK dependency and restore stores are retained. No tariff configuration or
personal accounting seed is shipped in this repository. Dated accounting remains
opt-in through its existing configuration and imports no personal entity IDs.

The dated-statistics writer is now bounded by the report's confirmed `through`
time, not the current wall clock. It neither replaces nor inserts later rows
using a flat extrapolation of an older report. Older successful source data is
not evidence that later hours have zero consumption.

## Installation

Use `scripts/install_energy_counter_fix.py` from the pinned follow-up commit.
`--check` validates without changes. `--restart` checks HA configuration and
restarts Core after the code files are replaced. The installer recognizes the
previous GitHub build, both October 21 packages and the supplied bus-error hotfix.
It backs up the integration, preserves the existing heating-number migration,
refuses unknown edits and rolls back code if configuration checking fails.
The installer does not run a separate historical statistics repair or modify
Energy preferences, tariff files, SQL rows, or counter stores. The existing
opt-in accounting task continues its configured operation after restart.

## Validation and limits

Regression tests execute the real setup function with independent bus/API
failures and authentication failures, exercise periodic R2 notifications and
serialized requests, and check all eight concrete meter properties including
zero and restored readings. Transaction tests verify that rows after the
confirmed report boundary remain untouched. Installer tests check recognized
updates, idempotence, unknown-edit refusal and existing migration preservation.

The HA coordinator behavior was checked against Home Assistant Core 2026.10.2
`homeassistant/helpers/update_coordinator.py`. Tests use isolated HA doubles and
SQLAlchemy/SQLite; they do not substitute for a full HA/MariaDB/device test.
Missing statistics since October 21 require a separate comparison of actual
Recorder rows and dated Ariston reports. A successful setup alone does not
establish that those days are complete.
