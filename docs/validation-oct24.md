# Complete package verification — 2026-10-24

Branch: `fix/persistent-energy-counters`. Integration version remains 0.20.2; SDK remains ariston==0.19.9. Diagnostics build: `2026-10-24-background-accounting`.

## Fixes

Live readings no longer await tariff recovery, cost entity registration or Recorder reconciliation. A delayed reconciliation cannot overwrite newer readings. Statistics reconciliation is a Home Assistant background task and does not hold its startup/drain barrier. A per-gateway Store lock serializes bootstrap, publication and replacement runtime loading, including unload during a pending save. Unloaded runtimes do not start new work or publish status errors.

## Executed validation

- Full existing suite: 124 test executions passed on Python 3.14.8.
- Seven additional tests with actual Home Assistant 2026.10.2 and ariston 0.19.9: import every integration module; actual Coordinator recovery after HTTP 500; actual Store restore; live refresh with held tariff lock and unblocked HA task drain; unload/reload during a pending Store write; preserved legacy temperature/offset unique IDs; real Recorder SQL models, full transaction and idempotent repeat.
- Compile and JSON validation for all 35 integration files; package archive contents compared byte-for-byte with source.
- Existing tests cover daily conservation, tariff boundary splitting, invalid metadata rollback, missing/mismatched reports, zero readings, restarts, source revisions and preserving unconfirmed future statistics.

## Reproduction

Use Python 3.14 and install `homeassistant==2026.10.2` plus `ariston==0.19.9` in an isolated environment. Run:

```
python -m unittest discover -s tests -v
python -m unittest discover -s tests_ha -v
python scripts/build_integration_package.py
```

The build produces `dist/ariston.zip`, containing the complete `custom_components/ariston` directory. Install it as one package, never by applying individual-file overlays. The preceding partial-file installer commands are superseded for this deployment. Keep the HA config entry, entity registry, Store and Recorder data.

## Limits

Network failures are simulated; no authenticated boiler/cloud test was performed. SQL execution used SQLite with HA's actual schema, not the user's MariaDB server. Tests do not establish that every possible fault is eliminated. Repository CI results must be checked separately; local results are not a claim that GitHub Actions ran. The separate tariff manager and dashboard were not replaced by this Ariston package.

Automatic statistics reconciliation remains enabled and backs up changed rows. First-time bootstrap still requires valid statistics baselines and cost metadata. A pending/error statistics status must not be presented as verified history.

## Independent export comparison

The supplied 2026-10-24 08:59 gas export and diagnostic source data agree through 08:00: native counters 1347.52/987.37 m³; latest completed interval 1.60/0.30 m³. October completed-day totals are 127.74/33.77 m³, excluding today's 2.00/0.60 m³. The export's prior-day interval sums (6.80/3.00) differ from its completed daily totals (5.07/2.32); regression coverage preserves the authoritative daily totals and scales only the within-day allocation. The export contains gas volumes, not independent electricity readings.
