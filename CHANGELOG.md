# Changelog

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
