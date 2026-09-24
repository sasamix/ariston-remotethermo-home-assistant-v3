# Changelog

## 0.20.0 — 2026-09-24

- Added full Home Assistant entity localization for English and Russian.
- Entity names now follow the language selected in Home Assistant while legacy unique IDs remain unchanged.
- Added localized heating and DHW scenario controls, scenario option labels, gas type/unit labels, and active-program states.
- Added per-zone heating scenario read/write support with custom scenario naming.
- Added DHW scenario naming and custom scenario persistence.
- Added live R2 gas energy counters for heating and DHW.
- Added resilient Ariston state refresh when the vendor menuItems endpoint temporarily returns HTTP 500.
- Preserved existing entity IDs and Recorder history across the fork migration and localization changes.
