<!-- SASAMIX_FORK -->
> **sasamix fork.** This branch keeps the upstream Ariston integration and adds the GALEVO features tested on Home Assistant: 30-second DHW time-program refresh, Economy/Comfort active target handling, DHW scenario selection, native R2 gas metering in m³, stable Energy counters, and stale empty-device cleanup.
>
> The integration remains pinned to `ariston==0.19.9`. The Home Assistant domain stays `ariston`, and existing entity unique IDs are intentionally preserved so switching to this fork does not create a second integration or discard Recorder history. For HACS, add this repository as a custom **Integration** repository; the fork can be installed directly from the default branch.

[![CodeQL](https://github.com/fustom/ariston-remotethermo-home-assistant-v3/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/fustom/ariston-remotethermo-home-assistant-v3/actions/workflows/codeql.yml)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)
[![HACS Action](https://github.com/fustom/ariston-remotethermo-home-assistant-v3/actions/workflows/hacs.yml/badge.svg)](https://github.com/fustom/ariston-remotethermo-home-assistant-v3/actions/workflows/hacs.yml)
[![Validate with hassfest](https://github.com/fustom/ariston-remotethermo-home-assistant-v3/actions/workflows/hassfest.yml/badge.svg)](https://github.com/fustom/ariston-remotethermo-home-assistant-v3/actions/workflows/hassfest.yml)
# Ariston NET remotethermo integration for Home Assistant
This integration inspired by chomupashchuk fantastic work https://github.com/chomupashchuk/ariston-remotethermo-home-assistant-v2
But it does not use Ariston website. It uses Ariston API what I reversed engineered.


| [This integration](https://github.com/fustom/ariston-remotethermo-home-assistant-v3)  | [Chomupashchuk's v2 integration](https://github.com/chomupashchuk/ariston-remotethermo-home-assistant-v2) |
| ------------- | ------------- |
| Uses real API  | Uses Ariston website  |
| Faster set/get data  | Sometimes needs minutes to set/get data |
| Easy to setup with UI | Not so easy to setup (only with configuration.yaml) |
| Integration & devices & entites | Only entites |
| Proper asynchronous integration, clean code | Hard to understand and maintain (ariston.py has more than 4000 lines) |
| Less sensors, switches, etc |  More sensors, switches, etc |
| New code, may contains lot of bugs | Old, tested code |

## TODO
- Localization. Avaliable in english, catalan, italian, russian and ukranian.
- More sensors, switches, binary sersors, selectors, services.
- Exception handling.
- More logs.
- Unit tests.
- Fun.

## Integration was tested on and works with:
- Ariston Alteas One 24
- Ariston Velis Evo
- Ariston Velis Lux
- Ariston Lydos Hybrid

Feel free to test something else and create new issue / pull request if something goes wrong.

## Installation
[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=sasamix&repository=ariston-remotethermo-home-assistant-v3&category=integration)

For a new installation, add this repository to HACS as a custom **Integration** repository and download it.

### Migrating an existing Ariston installation without losing data

Do **not** delete the Ariston config entry from **Settings → Devices & services**. The fork deliberately keeps the same Home Assistant domain (`ariston`) and the same entity unique IDs, including the custom R2 gas Energy sensors. Existing entity registry entries, entity IDs, Recorder statistics and Energy Dashboard history therefore remain associated with the same entities.

Before changing the HACS source, create a normal Home Assistant backup. Then switch only the integration files/repository source. Do not delete Recorder statistics or remove/re-add the Ariston integration. See [MIGRATION.md](MIGRATION.md) for the safe procedure.

| ![Kazam_screenshot_00003](https://user-images.githubusercontent.com/6751243/146653448-ff7b6f9d-cbf1-4555-9a75-61bf68bc9d3e.png) | ![Kazam_screenshot_00004](https://user-images.githubusercontent.com/6751243/146653484-52e39d78-7c6f-44ae-888d-acf246147290.png) | ![Kazam_screenshot_00010](https://user-images.githubusercontent.com/6751243/147890590-6c4ebf38-16d9-421f-9b81-8f43298ec62f.png) |
:-------------------------:|:-------------------------:|:-------------------------:

![Kazam_screenshot_00011](https://user-images.githubusercontent.com/6751243/147890611-54ae2d28-bf5a-45f8-ba92-e7a00a22615c.png)

![Kazam_screenshot_00012](https://user-images.githubusercontent.com/6751243/147989103-cdac510f-e6f6-461f-a88e-b8ff0204c34f.png)

| ![Kazam_screenshot_00013](https://user-images.githubusercontent.com/6751243/148247717-5211c01c-561f-4a4e-b4b5-47a680e04a68.png) | ![Kazam_screenshot_00009](https://user-images.githubusercontent.com/6751243/146657797-ed14b741-595a-48a6-9126-1acca3beb69f.png) |
:-------------------------:|:-------------------------:

<h1 align="center">Peace Love Freedom</h1>


## Home Assistant-managed DHW scenario names

The Ariston mobile app can keep user-defined scenario names locally on the phone while the
cloud API exposes only the DHW weekly schedule. This fork can therefore store a local
Home Assistant name for the currently active schedule.

For GALEVO systems the integration adds:

- `text.*ariston_dhw_scenario_name` — enter a name for the schedule currently read from Ariston;
  submitting the value saves the current schedule under that name in Home Assistant.
- `button.*ariston_save_current_dhw_scenario` — optional explicit save control.
- `select.*ariston_dhw_scenario` — after saving, the custom name is recognized automatically
  and can be selected again to re-apply the stored weekly schedule.

Only the schedule payload and the user-supplied name are stored in Home Assistant.
Credentials, gateway identifiers and account data are not stored in this scenario mapping.


## Heating scenarios (GALEVO)

The fork now manages heating-zone schedules in the same way as DHW schedules.
For every heating zone reported by Ariston, Home Assistant gets:

- `select.*ariston_heating_scenario_zone_N` — current/selected heating scenario.
- `text.*ariston_heating_scenario_name_zone_N` — enter a name and submit it to save
  the currently loaded Ariston weekly schedule under that name.
- `button.*ariston_save_current_heating_scenario_zone_N` — optional explicit save action.
- `sensor.*ariston_heating_active_program_zone_N` — current Comfort/Economy/Manual slot.
- `sensor.*ariston_heating_active_target_temperature_zone_N` — effective target now.
- `sensor.*ariston_heating_comfort_temperature_zone_N` — zone Comfort temperature.
- `sensor.*ariston_heating_economy_temperature_zone_N` — zone Economy temperature.

Heating schedules are refreshed independently every 30 seconds using the Ariston API v2
`ChZnN` time-program resource. Standard schedules are recognized by their weekly-plan
signature, while mobile-app user scenarios can be learned in HA by giving the currently
loaded schedule a name. Selecting a scenario writes its complete weekly plan back to
Ariston and switches the zone to time-program mode.

Only scenario names and schedule payloads are persisted by this feature. Credentials,
account data and gateway identifiers are not stored in the scenario mapping.
