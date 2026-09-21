# Safe migration to the sasamix Ariston fork

This fork is designed to replace the upstream custom integration **in place**.

## Data that must be preserved

The fork keeps:

- Home Assistant domain: `ariston`
- the existing Ariston config entry
- standard Ariston entity unique IDs
- R2 live gas sensor unique IDs:
  - `*_r2_gas_energy_live_v3_heating`
  - `*_r2_gas_energy_live_v3_dhw`
- Recorder database and long-term statistics
- Energy Dashboard configuration/statistics
- restored external gas statistics such as `ariston_r2:gas_heating` and `ariston_r2:gas_dhw`

Changing Python files does not require deleting or recreating the Home Assistant integration.

## Safe procedure

1. Create a full Home Assistant backup first.
2. Leave **Settings → Devices & services → Ariston** configured exactly as it is.
3. Do not delete any Ariston entities or statistics.
4. In HACS, add:
   `https://github.com/sasamix/ariston-remotethermo-home-assistant-v3`
   as a custom repository of type **Integration**.
5. Download/re-download the fork so that it replaces `/config/custom_components/ariston`.
6. Restart Home Assistant once after the files have been replaced.
7. Verify the existing entities still have their previous entity IDs and history.

## Important

Do **not** use "Delete integration" / "Remove integration" in Home Assistant during the switch. That is unnecessary and can remove the config entry/device relationship.

If HACS refuses to install the fork because the upstream repository is still registered, stop before uninstalling anything from Home Assistant. Only the HACS repository registration needs to be changed; the Ariston config entry must remain.
