"""Constants for the Ariston integration."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import sys
from typing import Any, Final

from ariston.const import (
    ARISTON_BUS_ERRORS,
    ConsumptionProperties,
    ConsumptionType,
    ConsumptionTimeInterval,
    CustomDeviceFeatures,
    DeviceAttribute,
    DeviceFeatures,
    DeviceProperties,
    EvoDeviceProperties,
    EvoLydosDeviceProperties,
    EvoOneDeviceProperties,
    MedDeviceSettings,
    MenuItemNames,
    NuosSplitProperties,
    SeDeviceSettings,
    SlpDeviceSettings,
    SystemType,
    ThermostatProperties,
    VelisDeviceProperties,
    WheType,
)
from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.components.climate import ClimateEntityDescription
from homeassistant.components.number import NumberEntityDescription
from homeassistant.components.select import SelectEntityDescription
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchEntityDescription
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTemperature, UnitOfTime, UnitOfVolume, UnitOfVolumeFlowRate
from homeassistant.helpers.entity import EntityCategory, EntityDescription
from homeassistant.util import dt as dt_util

try:
    from homeassistant.components.water_heater import WaterHeaterEntityDescription
except ImportError:
    # compatibility code for HA < 2025.1
    from homeassistant.components.water_heater import WaterHeaterEntityEntityDescription

    WaterHeaterEntityDescription = WaterHeaterEntityEntityDescription

import datetime as dt

DOMAIN: Final[str] = "ariston"
NAME: Final[str] = "Ariston"
COORDINATOR: Final[str] = "coordinator"
ENERGY_COORDINATOR: Final[str] = "energy_coordinator"
ENERGY_SCAN_INTERVAL: Final[str] = "energy_scan_interval"
BUS_ERRORS_COORDINATOR: Final[str] = "bus_errors_coordinator"
BUS_ERRORS_SCAN_INTERVAL: Final[str] = "bus_errors_scan_interval"
API_URL_SETTING: Final[str] = "api_url_setting"
API_USER_AGENT: Final[str] = "api_user_agent"

DEFAULT_SCAN_INTERVAL_SECONDS: Final[int] = 180
DEFAULT_ENERGY_SCAN_INTERVAL_MINUTES: Final[int] = 60
DEFAULT_BUS_ERRORS_SCAN_INTERVAL_SECONDS: Final[int] = 600

ATTR_TARGET_TEMP_STEP: Final[str] = "target_temp_step"
ATTR_HEAT_REQUEST: Final[str] = "heat_request"
ATTR_ECONOMY_TEMP: Final[str] = "economy_temp"
ATTR_HOLIDAY: Final[str] = "holiday"
ATTR_ZONE: Final[str] = "zone_number"
ATTR_ERRORS: Final[str] = "errors"

EXTRA_STATE_ATTRIBUTE: Final[str] = "Attribute"
EXTRA_STATE_DEVICE_METHOD: Final[str] = "DeviceMethod"


@dataclass(kw_only=True, frozen=True)
class AristonBaseEntityDescription(EntityDescription):
    """An abstract class that describes Ariston entites."""

    device_features: list[str] | None = None
    coordinator: str = COORDINATOR
    extra_states: list[dict[str, Any]] | None = None
    system_types: list[SystemType] | None = None
    whe_types: list[WheType] | None = None
    zone: bool = False
    # Original English/legacy display name used only to keep entity unique IDs
    # stable after names moved to Home Assistant's translation system.
    legacy_name: str | None = None


@dataclass(kw_only=True, frozen=True)
class AristonClimateEntityDescription(
    ClimateEntityDescription, AristonBaseEntityDescription
):
    """A class that describes climate entities."""


@dataclass(kw_only=True, frozen=True)
class AristonWaterHeaterEntityDescription(
    WaterHeaterEntityDescription, AristonBaseEntityDescription
):
    """A class that describes climate entities."""


@dataclass(kw_only=True, frozen=True)
class AristonBinarySensorEntityDescription(
    BinarySensorEntityDescription, AristonBaseEntityDescription
):
    """A class that describes binary sensor entities."""

    get_is_on: Callable[[Any], bool]


@dataclass(kw_only=True, frozen=True)
class AristonSwitchEntityDescription(
    SwitchEntityDescription, AristonBaseEntityDescription
):
    """A class that describes switch entities."""

    set_value: Callable[[Any, bool], Coroutine]
    get_is_on: Callable[[Any], bool]


@dataclass(kw_only=True, frozen=True)
class AristonNumberEntityDescription(
    NumberEntityDescription, AristonBaseEntityDescription
):
    """A class that describes switch entities."""

    set_native_value: Callable[[Any, float], Coroutine]
    get_native_value: Callable[[Any], Coroutine]
    get_native_min_value: Callable[[Any], float] | None = None
    get_native_max_value: Callable[[Any], float | None] | None = None
    get_native_step: Callable[[Any], Coroutine] | None = None


@dataclass(kw_only=True, frozen=True)
class AristonSensorEntityDescription(
    SensorEntityDescription, AristonBaseEntityDescription
):
    """A class that describes sensor entities."""

    get_native_unit_of_measurement: Callable[[Any], str] | None = None
    get_device_class: Callable[[Any], SensorDeviceClass | None] | None = None
    get_last_reset: Callable[[Any], dt.datetime] | None = None
    get_native_value: Callable[[Any], Any]


@dataclass(kw_only=True, frozen=True)
class AristonSelectEntityDescription(
    SelectEntityDescription, AristonBaseEntityDescription
):
    """A class that describes select entities."""

    get_current_option: Callable[[Any], str]
    get_options: Callable[[Any], list[str]]
    select_option: Callable[[Any, str], Coroutine]




DHW_SCENARIOS = {
    "Всегда Comfort": {
        "plans": [
            {"days": [1, 2, 3, 4, 5, 6, 0], "slices": [{"from": 0, "temp": 1}]}
        ]
    },
    "Семья": {
        "plans": [
            {"days": [1, 2, 3, 4], "slices": [{"from": 0, "temp": 0}, {"from": 330, "temp": 1}, {"from": 1320, "temp": 0}]},
            {"days": [5], "slices": [{"from": 0, "temp": 0}, {"from": 330, "temp": 1}, {"from": 1380, "temp": 0}]},
            {"days": [6], "slices": [{"from": 0, "temp": 0}, {"from": 390, "temp": 1}, {"from": 1380, "temp": 0}]},
            {"days": [0], "slices": [{"from": 0, "temp": 0}, {"from": 420, "temp": 1}, {"from": 1320, "temp": 0}]},
        ]
    },
    "Без обеда": {
        "plans": [
            {"days": [1, 2, 3, 4], "slices": [{"from": 0, "temp": 0}, {"from": 360, "temp": 1}, {"from": 480, "temp": 0}, {"from": 960, "temp": 1}, {"from": 1320, "temp": 0}]},
            {"days": [5], "slices": [{"from": 0, "temp": 0}, {"from": 360, "temp": 1}, {"from": 480, "temp": 0}, {"from": 900, "temp": 1}, {"from": 1380, "temp": 0}]},
            {"days": [6], "slices": [{"from": 0, "temp": 0}, {"from": 420, "temp": 1}, {"from": 1410, "temp": 0}]},
            {"days": [0], "slices": [{"from": 0, "temp": 0}, {"from": 480, "temp": 1}, {"from": 1320, "temp": 0}]},
        ]
    },
    "Дома днём": {
        "plans": [
            {"days": [1, 2, 3, 4], "slices": [{"from": 0, "temp": 0}, {"from": 360, "temp": 1}, {"from": 480, "temp": 0}, {"from": 690, "temp": 1}, {"from": 780, "temp": 0}, {"from": 1020, "temp": 1}, {"from": 1320, "temp": 0}]},
            {"days": [5], "slices": [{"from": 0, "temp": 0}, {"from": 360, "temp": 1}, {"from": 480, "temp": 0}, {"from": 690, "temp": 1}, {"from": 1380, "temp": 0}]},
            {"days": [6], "slices": [{"from": 0, "temp": 0}, {"from": 360, "temp": 1}, {"from": 1380, "temp": 0}]},
            {"days": [0], "slices": [{"from": 0, "temp": 0}, {"from": 420, "temp": 1}, {"from": 1320, "temp": 0}]},
        ]
    },
}


def _dhw_plan_signature(plan):
    """Return a semantic per-day signature for comparing DHW weekly plans."""
    if not isinstance(plan, dict):
        return None

    plans = plan.get("plans", [])
    if not isinstance(plans, list) or not plans:
        return None

    def normalize_temp(value):
        if value in (0, 0.0, "0"):
            return 0
        if value in (1, 1.0, "1"):
            return 1
        try:
            return float(value)
        except (TypeError, ValueError):
            return value

    default_temp = normalize_temp(plan.get("defaultTemp", 0))
    per_day = {}

    for day_plan in plans:
        if not isinstance(day_plan, dict):
            continue

        events = [(0, default_temp)]
        for item in day_plan.get("slices", []):
            if not isinstance(item, dict):
                continue
            try:
                minute = int(item.get("from", 0))
            except (TypeError, ValueError):
                minute = 0
            events.append(
                (minute, normalize_temp(item.get("temp", default_temp)))
            )

        by_minute = {}
        for minute, temp in sorted(events, key=lambda item: item[0]):
            by_minute[minute] = temp

        compact = []
        for minute, temp in sorted(by_minute.items()):
            if compact and compact[-1][1] == temp:
                continue
            compact.append((minute, temp))

        signature = tuple(compact)
        for day in day_plan.get("days", []):
            try:
                per_day[int(day)] = signature
            except (TypeError, ValueError):
                continue

    if not per_day:
        return None

    default_day = ((0, default_temp),)
    return tuple((day, per_day.get(day, default_day)) for day in range(7))


def get_dhw_scenario(device):
    """Return the matching built-in Ariston DHW scenario name."""
    program_data = getattr(device, "dhw_time_program", None) or {}
    current = program_data.get("Dhw", {})
    current_signature = _dhw_plan_signature(current)
    if current_signature is None:
        return None
    for name, scenario in DHW_SCENARIOS.items():
        if current_signature == _dhw_plan_signature(scenario):
            return name
    return None


async def async_set_dhw_scenario(entity, option):
    """Apply one of Ariston's built-in DHW scenarios through API v2."""
    if option not in DHW_SCENARIOS:
        raise ValueError(f"Unknown DHW scenario: {option}")

    device = entity.device
    api = device.api
    base_url = getattr(api, "_AristonAPI__api_url", None)
    if not base_url:
        raise RuntimeError("Ariston API base URL unavailable")

    umsys = getattr(device, "umsys", None)
    if umsys is None:
        umsys = getattr(device, "_umsys", None)
    suffix = f"?umsys={umsys}" if umsys is not None else ""

    # Use the same authenticated API-v2 resource that is used to read the
    # DHW program.  The Ariston API accepts the weekly-plan object on POST.
    url = f"{base_url}remote/timeProgs/{device.gw}/Dhw{suffix}"

    scenario = DHW_SCENARIOS[option]
    weekly_plan = {
        "ext": False,
        "plans": scenario["plans"],
        "allowedTemp": [0, 1],
        "defaultTemp": 0,
        "baseTemp": 0,
        "tick": 0,
        "maxSwitches": 0,
        "pilot": None,
    }

    await api._async_post(url, weekly_plan)

    # Do not fake the new state locally. Read it back from Ariston so HA only
    # reports a scenario after the cloud has actually accepted the change.
    new_program = await api._async_get(url)
    if not isinstance(new_program, dict) or "Dhw" not in new_program:
        raise RuntimeError("Ariston did not return the DHW program after write")

    device.dhw_time_program = new_program

    # Force entities using the state coordinator to refresh immediately.
    coordinator = getattr(entity, "coordinator", None)
    if coordinator is not None:
        coordinator.async_set_updated_data(coordinator.data)


def get_dhw_active_program(device):
    """Return the active DHW time-program slot: Economy or Comfort."""

    if device.system_type != SystemType.GALEVO:
        return None

    dhw_mode = device._get_item_by_id(DeviceProperties.DHW_MODE, "value")
    if dhw_mode not in (1, 1.0):
        return "Manual"

    program_data = getattr(device, "dhw_time_program", None)
    if not program_data:
        return None

    dhw_program = program_data.get("Dhw", {})
    plans = dhw_program.get("plans", [])
    if not plans:
        return None

    now = dt_util.now()

    # Ariston: Sunday=0, Monday=1, ... Saturday=6.
    ariston_day = (now.weekday() + 1) % 7
    minutes_now = now.hour * 60 + now.minute

    selected_temp = dhw_program.get("defaultTemp", 0)

    for plan in plans:
        if ariston_day not in plan.get("days", []):
            continue

        for time_slice in sorted(
            plan.get("slices", []),
            key=lambda item: item.get("from", 0),
        ):
            if time_slice.get("from", 0) <= minutes_now:
                selected_temp = time_slice.get("temp", selected_temp)
            else:
                break
        break

    return "Comfort" if selected_temp == 1 else "Economy"


def get_dhw_active_target_temperature(device):
    """Return the active DHW target temperature for the current time program."""

    if device.system_type != SystemType.GALEVO:
        return device.water_heater_target_temperature

    active_program = get_dhw_active_program(device)

    # Outside TIME_BASED mode, use the API's standard target.
    if active_program in (None, "Manual"):
        return device.water_heater_target_temperature

    if active_program == "Comfort":
        active_temperature = device._get_item_by_id(
            DeviceProperties.DHW_TIMEPROG_COMFORT_TEMP,
            "value",
        )
    else:
        active_temperature = device._get_item_by_id(
            DeviceProperties.DHW_TIMEPROG_ECONOMY_TEMP,
            "value",
        )

    if active_temperature is None:
        return device.water_heater_target_temperature

    return active_temperature


ARISTON_CLIMATE_TYPES: list[AristonClimateEntityDescription] = [
    AristonClimateEntityDescription(
        key="AristonClimate",
        extra_states=[
            {
                EXTRA_STATE_ATTRIBUTE: ATTR_HEAT_REQUEST,
                EXTRA_STATE_DEVICE_METHOD: lambda entity: entity.device.get_zone_heat_request_value(
                    entity.zone
                ),
            },
            {
                EXTRA_STATE_ATTRIBUTE: ATTR_ECONOMY_TEMP,
                EXTRA_STATE_DEVICE_METHOD: lambda entity: entity.device.get_zone_economy_temp_value(
                    entity.zone
                ),
            },
            {
                EXTRA_STATE_ATTRIBUTE: ATTR_ZONE,
                EXTRA_STATE_DEVICE_METHOD: lambda entity: entity.zone,
            },
        ],
        system_types=[SystemType.GALEVO],
    ),
    AristonClimateEntityDescription(
        key="AristonClimate",
        system_types=[SystemType.BSB],
    ),
]

ARISTON_WATER_HEATER_TYPES: list[AristonWaterHeaterEntityDescription] = [
    AristonWaterHeaterEntityDescription(
        key="AristonWaterHeater",
        extra_states=[
            {
                EXTRA_STATE_ATTRIBUTE: ATTR_TARGET_TEMP_STEP,
                EXTRA_STATE_DEVICE_METHOD: lambda entity: entity.device.water_heater_temperature_step,
            }
        ],
        device_features=[CustomDeviceFeatures.HAS_DHW],
        system_types=[SystemType.GALEVO, SystemType.BSB],
    ),
    AristonWaterHeaterEntityDescription(
        key="AristonWaterHeater",
        extra_states=[
            {
                EXTRA_STATE_ATTRIBUTE: ATTR_TARGET_TEMP_STEP,
                EXTRA_STATE_DEVICE_METHOD: lambda entity: entity.device.water_heater_temperature_step,
            }
        ],
        device_features=[CustomDeviceFeatures.HAS_DHW],
        system_types=[SystemType.VELIS],
        whe_types=[
            WheType.Andris2,
            WheType.Evo2,
            WheType.Lux,
            WheType.Lux2,
            WheType.Lydos,
            WheType.LydosHybrid,
            WheType.NuosSplit,
        ],
    ),
]

def get_gas_consumption_unit(device):
    """Return the gas consumption unit selected in Ariston settings."""
    units = {
        "KWH": UnitOfEnergy.KILO_WATT_HOUR,
        "GIGA_JOULE": "GJ",
        "THERM": "therm",
        "MEGA_BTU": "MBtu",
        "SMC": "Smc",
        "CUBE_METER": "m³",
    }
    return units.get(device.gas_energy_unit, UnitOfEnergy.KILO_WATT_HOUR)


def get_gas_consumption_device_class(device):
    """Return the Home Assistant device class matching the selected gas unit."""
    if device.gas_energy_unit in ("CUBE_METER", "SMC"):
        return SensorDeviceClass.GAS
    if device.gas_energy_unit == "KWH":
        return SensorDeviceClass.ENERGY
    return None





def get_native_gas_two_hour_value(device, series):
    physical = getattr(device, "gas_physical_unit_data", None)
    if not isinstance(physical, dict):
        return None

    histogram = physical.get("histogramData", [])
    if not isinstance(histogram, list):
        return None

    now = dt_util.now()
    start_hour = (now.hour // 2) * 2
    end_hour = (start_hour + 2) % 24

    def _hour12(hour):
        value = hour % 12
        return 12 if value == 0 else value

    am_pm = "AM" if start_hour < 12 else "PM"
    bucket = f"{_hour12(start_hour):02d}-{_hour12(end_hour):02d} {am_pm}"

    for dataset in histogram:
        if (
            dataset.get("tab") == "ConsumedGas"
            and dataset.get("period") == "CurrentDay"
            and dataset.get("series") == series
        ):
            for item in dataset.get("items", []):
                if item.get("x") == bucket:
                    value = item.get("y")
                    return None if value is None else float(value)
    return None



def get_native_gas_current_year_total(device, series):
    """Return native R2 gas volume accumulated in the current year, m³."""
    physical = getattr(device, "gas_physical_unit_data", None)
    if not isinstance(physical, dict):
        return None
    histogram = physical.get("histogramData", [])
    if not isinstance(histogram, list):
        return None

    for dataset in histogram:
        if (
            dataset.get("tab") == "ConsumedGas"
            and dataset.get("period") == "CurrentYear"
            and dataset.get("series") == series
        ):
            total = 0.0
            found = False
            for item in dataset.get("items", []):
                value = item.get("y")
                if value is None:
                    continue
                try:
                    total += float(value)
                    found = True
                except (TypeError, ValueError):
                    continue
            return round(total, 2) if found else None
    return None



def get_native_gas_energy_live_total(device, series):
    """Return a finer current-year cumulative native R2 gas total, m³.

    Uses completed months from CurrentYear, completed days from CurrentMonth,
    and today's 2-hour CurrentDay buckets. This preserves a cumulative counter
    suitable for Home Assistant Energy while allowing intra-day updates.
    """
    physical = getattr(device, "gas_physical_unit_data", None)
    if not isinstance(physical, dict):
        return None

    histogram = physical.get("histogramData", [])
    if not isinstance(histogram, list):
        return None

    datasets = {}
    for dataset in histogram:
        if (
            isinstance(dataset, dict)
            and dataset.get("tab") == "ConsumedGas"
            and dataset.get("series") == series
        ):
            datasets[dataset.get("period")] = dataset.get("items", [])

    year_items = datasets.get("CurrentYear")
    month_items = datasets.get("CurrentMonth")
    day_items = datasets.get("CurrentDay")
    if not all(isinstance(items, list) for items in (year_items, month_items, day_items)):
        return None
    if len(year_items) < 12:
        return None

    now = dt_util.now()
    current_month_index = now.month - 1
    current_day = now.day

    def numeric_y(item):
        if not isinstance(item, dict):
            return None
        value = item.get("y")
        if value is None:
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    # Completed calendar months only.
    total = 0.0
    for index in range(current_month_index):
        value = numeric_y(year_items[index])
        if value is None:
            return None
        total += value

    # Completed days of the current month only.
    for item in month_items:
        try:
            day_number = int(str(item.get("x")))
        except (AttributeError, TypeError, ValueError):
            continue
        if day_number >= current_day:
            continue
        value = numeric_y(item)
        if value is not None:
            total += value

    # Today's 2-hour buckets. R2 supplies 12 physical-volume buckets.
    usable_day_values = 0
    for item in day_items:
        value = numeric_y(item)
        if value is None:
            continue
        total += value
        usable_day_values += 1

    if usable_day_values == 0:
        return None

    return round(total, 3)


def get_native_gas_current_month_total(device, series):
    """Return native R2 gas volume for the current month in m³."""
    physical = getattr(device, "gas_physical_unit_data", None)
    if not isinstance(physical, dict):
        return None
    histogram = physical.get("histogramData", [])
    if not isinstance(histogram, list):
        return None

    for dataset in histogram:
        if (
            dataset.get("tab") == "ConsumedGas"
            and dataset.get("period") == "CurrentMonth"
            and dataset.get("series") == series
        ):
            total = 0.0
            found = False
            for item in dataset.get("items", []):
                value = item.get("y")
                if value is None:
                    continue
                try:
                    total += float(value)
                    found = True
                except (TypeError, ValueError):
                    continue
            return round(total, 2) if found else None
    return None


def get_native_gas_two_hour_average_flow(device, series):
    """Return average m³/h for the current native Ariston 2-hour bucket."""
    value = get_native_gas_two_hour_value(device, series)
    if value is None:
        return None
    return round(value / 2.0, 3)


ARISTON_SENSOR_TYPES: list[AristonSensorEntityDescription] = [

    AristonSensorEntityDescription(
        key="Central heating gas current 2h volume",
        legacy_name="Газ отопление — текущие 2 часа",
        name=None,
        translation_key="gas_heating_current_2h_volume",
        icon="mdi:meter-gas",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=2,
        device_features=[DeviceFeatures.HAS_METERING],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: get_native_gas_two_hour_value(
            entity.device, "Heating"
        ),
    ),
    AristonSensorEntityDescription(
        key="Domestic hot water gas current 2h volume",
        legacy_name="Газ ГВС — текущие 2 часа",
        name=None,
        translation_key="gas_dhw_current_2h_volume",
        icon="mdi:meter-gas",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=2,
        device_features=[DeviceFeatures.HAS_METERING],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: get_native_gas_two_hour_value(
            entity.device, "Dhw"
        ),
    ),
    # Native R2 gas current month totals (server physical unit: m³)
    # Native R2 cumulative gas sensors for Home Assistant Energy Dashboard.
            AristonSensorEntityDescription(
        key="Central heating gas current month native",
        legacy_name="Газ отопление — текущий месяц",
        name=None,
        translation_key="gas_heating_current_month",
        icon="mdi:meter-gas",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL,
        device_class=SensorDeviceClass.GAS,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_features=[CustomDeviceFeatures.HAS_DHW],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: get_native_gas_current_month_total(
            entity.device, "Heating"
        ),
    ),
    AristonSensorEntityDescription(
        key="Domestic hot water gas current month native",
        legacy_name="Газ ГВС — текущий месяц",
        name=None,
        translation_key="gas_dhw_current_month",
        icon="mdi:meter-gas",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL,
        device_class=SensorDeviceClass.GAS,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_features=[CustomDeviceFeatures.HAS_DHW],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: get_native_gas_current_month_total(
            entity.device, "Dhw"
        ),
    ),
    AristonSensorEntityDescription(
        key="Central heating gas current 2h average flow",
        legacy_name="Газ отопление — средний расход за текущие 2 часа",
        name=None,
        translation_key="gas_heating_current_2h_average_flow",
        icon="mdi:meter-gas",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        suggested_display_precision=3,
        device_features=[DeviceFeatures.HAS_METERING],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: get_native_gas_two_hour_average_flow(
            entity.device, "Heating"
        ),
    ),
    AristonSensorEntityDescription(
        key="Domestic hot water gas current 2h average flow",
        legacy_name="Газ ГВС — средний расход за текущие 2 часа",
        name=None,
        translation_key="gas_dhw_current_2h_average_flow",
        icon="mdi:meter-gas",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        suggested_display_precision=3,
        device_features=[DeviceFeatures.HAS_METERING],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: get_native_gas_two_hour_average_flow(
            entity.device, "Dhw"
        ),
    ),

    AristonSensorEntityDescription(
        key=DeviceProperties.HEATING_CIRCUIT_PRESSURE,
        legacy_name="Давление в контуре отопления",
        name=None,
        translation_key="heating_circuit_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        get_native_value=lambda entity: entity.device.heating_circuit_pressure_value,
        get_native_unit_of_measurement=lambda entity: entity.device.heating_circuit_pressure_unit,
        system_types=[SystemType.GALEVO],
    ),
    AristonSensorEntityDescription(
        key=DeviceProperties.CH_FLOW_SETPOINT_TEMP,
        legacy_name=f"{NAME} CH flow setpoint temp",
        name=None,
        translation_key="ch_flow_setpoint_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        get_native_value=lambda entity: entity.device.ch_flow_setpoint_temp_value,
        get_native_unit_of_measurement=lambda entity: entity.device.ch_flow_setpoint_temp_unit,
        system_types=[SystemType.GALEVO],
    ),
    AristonSensorEntityDescription(
        key=DeviceProperties.CH_FLOW_TEMP,
        legacy_name=f"{NAME} CH flow temp",
        name=None,
        translation_key="ch_flow_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        get_native_value=lambda entity: entity.device.ch_flow_temp_value,
        get_native_unit_of_measurement=lambda entity: entity.device.ch_flow_temp_unit,
        device_features=[DeviceProperties.CH_FLOW_TEMP],
        system_types=[SystemType.GALEVO],
    ),
    AristonSensorEntityDescription(
        key=str(MenuItemNames.SIGNAL_STRENGTH),
        legacy_name="Уровень сигнала",
        name=None,
        translation_key="signal_level",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        get_native_value=lambda entity: entity.device.signal_strength_value,
        get_native_unit_of_measurement=lambda entity: entity.device.signal_strength_unit,
        system_types=[SystemType.GALEVO],
    ),
    AristonSensorEntityDescription(
        key=str(MenuItemNames.CH_RETURN_TEMP),
        legacy_name=f"{NAME} CH return temp",
        name=None,
        translation_key="ch_return_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        get_native_value=lambda entity: entity.device.ch_return_temp_value,
        get_native_unit_of_measurement=lambda entity: entity.device.ch_return_temp_unit,
        system_types=[SystemType.GALEVO],
    ),
    AristonSensorEntityDescription(
        key=DeviceProperties.OUTSIDE_TEMP,
        legacy_name=f"{NAME} Outside temp",
        name=None,
        translation_key="outside_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        device_features=[CustomDeviceFeatures.HAS_OUTSIDE_TEMP],
        get_native_value=lambda entity: entity.device.outside_temp_value,
        get_native_unit_of_measurement=lambda entity: entity.device.outside_temp_unit,
        system_types=[SystemType.GALEVO, SystemType.BSB],
    ),
    AristonSensorEntityDescription(
        key=EvoLydosDeviceProperties.AV_SHW,
        legacy_name=f"{NAME} average showers",
        name=None,
        translation_key="average_showers",
        icon="mdi:shower-head",
        state_class=SensorStateClass.MEASUREMENT,
        get_native_value=lambda entity: entity.device.av_shw_value,
        native_unit_of_measurement="",
        system_types=[SystemType.VELIS],
        whe_types=[
            WheType.Lux,
            WheType.Evo,
            WheType.Evo2,
            WheType.Lydos,
            WheType.LydosHybrid,
            WheType.Andris2,
            WheType.Lux2,
        ],
    ),
        AristonSensorEntityDescription(
        key="Electricity consumption for heating last month",
        legacy_name=f"{NAME} electricity consumption for heating last month",
        name=None,
        translation_key="electricity_heating_last_month",
        icon="mdi:cash",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_features=[DeviceFeatures.HAS_METERING],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.electricity_consumption_for_heating_last_month,
        system_types=[SystemType.GALEVO],
    ),
    AristonSensorEntityDescription(
        key="Electricity consumption for cooling last month",
        legacy_name=f"{NAME} electricity consumption for cooling last month",
        name=None,
        translation_key="electricity_cooling_last_month",
        icon="mdi:cash",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_features=[DeviceFeatures.HAS_METERING, DeviceAttribute.HPMP_SYS],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.electricity_consumption_for_cooling_last_month,
        system_types=[SystemType.GALEVO],
    ),
        AristonSensorEntityDescription(
        key="Electricity consumption for water last month",
        legacy_name=f"{NAME} electricity consumption for water last month",
        name=None,
        translation_key="electricity_water_last_month",
        icon="mdi:cash",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_features=[DeviceFeatures.HAS_METERING, CustomDeviceFeatures.HAS_DHW],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.electricity_consumption_for_water_last_month,
        system_types=[SystemType.GALEVO],
    ),
    AristonSensorEntityDescription(
        key="Central heating total energy consumption",
        legacy_name="Отопление — общее энергопотребление",
        name=None,
        translation_key="heating_total_energy_consumption",
        icon="mdi:cash",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_features=[
            DeviceFeatures.HAS_METERING,
            ConsumptionType.CENTRAL_HEATING_TOTAL_ENERGY.name,
        ],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.central_heating_total_energy_consumption,
        get_last_reset=lambda entity: entity.device.consumption_sequence_last_changed_utc,
    ),
    AristonSensorEntityDescription(
        key="Domestic hot water total energy consumption",
        legacy_name="ГВС — общее энергопотребление",
        name=None,
        translation_key="dhw_total_energy_consumption",
        icon="mdi:cash",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_features=[
            DeviceFeatures.HAS_METERING,
            ConsumptionType.DOMESTIC_HOT_WATER_TOTAL_ENERGY.name,
        ],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.domestic_hot_water_total_energy_consumption,
        get_last_reset=lambda entity: entity.device.consumption_sequence_last_changed_utc,
    ),
    AristonSensorEntityDescription(
        key="Central heating gas consumption",
        legacy_name=f"{NAME} central heating gas consumption",
        name=None,
        translation_key="central_heating_gas_consumption",
        icon="mdi:cash",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_features=[
            DeviceFeatures.HAS_METERING,
            ConsumptionType.CENTRAL_HEATING_GAS.name,
        ],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.central_heating_gas_consumption,
        get_last_reset=lambda entity: entity.device.consumption_sequence_last_changed_utc,
    ),
    AristonSensorEntityDescription(
        key="Domestic hot water heating pump electricity consumption",
        legacy_name=f"{NAME} domestic hot water heating pump electricity consumption",
        name=None,
        translation_key="dhw_heating_pump_electricity_consumption",
        icon="mdi:cash",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_features=[
            DeviceFeatures.HAS_METERING,
            ConsumptionType.DOMESTIC_HOT_WATER_HEATING_PUMP_ELECTRICITY.name,
        ],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.domestic_hot_water_heating_pump_electricity_consumption,
        get_last_reset=lambda entity: entity.device.consumption_sequence_last_changed_utc,
    ),
    AristonSensorEntityDescription(
        key="Domestic hot water resistor electricity consumption",
        legacy_name=f"{NAME} domestic hot water resistor electricity consumption",
        name=None,
        translation_key="dhw_resistor_electricity_consumption",
        icon="mdi:cash",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_features=[
            DeviceFeatures.HAS_METERING,
            ConsumptionType.DOMESTIC_HOT_WATER_RESISTOR_ELECTRICITY.name,
        ],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.domestic_hot_water_resistor_electricity_consumption,
        get_last_reset=lambda entity: entity.device.consumption_sequence_last_changed_utc,
    ),
    AristonSensorEntityDescription(
        key="Domestic hot water gas consumption",
        legacy_name=f"{NAME} domestic hot water gas consumption",
        name=None,
        translation_key="dhw_gas_consumption",
        icon="mdi:cash",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_features=[
            DeviceFeatures.HAS_METERING,
            ConsumptionType.DOMESTIC_HOT_WATER_GAS.name,
        ],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.domestic_hot_water_gas_consumption,
        get_last_reset=lambda entity: entity.device.consumption_sequence_last_changed_utc,
    ),
    AristonSensorEntityDescription(
        key="Central heating gas hourly average power",
        legacy_name="Газ отопление — средняя мощность за час",
        name=None,
        translation_key="gas_heating_hourly_average_power",
        icon="mdi:fire",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_features=[
            DeviceFeatures.HAS_METERING,
            ConsumptionType.CENTRAL_HEATING_GAS.name,
        ],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.central_heating_gas_consumption,
    ),
    AristonSensorEntityDescription(
        key="Domestic hot water gas hourly average power",
        legacy_name="Газ ГВС — средняя мощность за час",
        name=None,
        translation_key="gas_dhw_hourly_average_power",
        icon="mdi:fire",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_features=[
            DeviceFeatures.HAS_METERING,
            ConsumptionType.DOMESTIC_HOT_WATER_GAS.name,
        ],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.domestic_hot_water_gas_consumption,
    ),
    AristonSensorEntityDescription(
        key="Central heating electricity consumption",
        legacy_name=f"{NAME} central heating electricity consumption",
        name=None,
        translation_key="central_heating_electricity_consumption",
        icon="mdi:cash",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_features=[
            DeviceFeatures.HAS_METERING,
            ConsumptionType.CENTRAL_HEATING_ELECTRICITY.name,
        ],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.central_heating_electricity_consumption,
        get_last_reset=lambda entity: entity.device.consumption_sequence_last_changed_utc,
    ),
    AristonSensorEntityDescription(
        key="Domestic hot water electricity consumption",
        legacy_name=f"{NAME} domestic hot water electricity consumption",
        name=None,
        translation_key="dhw_electricity_consumption",
        icon="mdi:cash",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_features=[
            DeviceFeatures.HAS_METERING,
            ConsumptionType.DOMESTIC_HOT_WATER_ELECTRICITY.name,
        ],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.domestic_hot_water_electricity_consumption,
        get_last_reset=lambda entity: entity.device.consumption_sequence_last_changed_utc,
    ),
    AristonSensorEntityDescription(
        key=EvoDeviceProperties.RM_TM,
        legacy_name=f"{NAME} remaining time",
        name=None,
        translation_key="remaining_time",
        icon="mdi:timer",
        state_class=SensorStateClass.MEASUREMENT,
        get_native_value=lambda entity: entity.device.rm_tm_in_minutes,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        system_types=[SystemType.VELIS],
        whe_types=[
            WheType.Lux,
            WheType.Evo,
            WheType.Evo2,
            WheType.Lux2,
            WheType.Lydos,
        ],
    ),
    AristonSensorEntityDescription(
        key=SlpDeviceSettings.SLP_HEATING_RATE,
        legacy_name=f"{NAME} heating rate",
        name=None,
        translation_key="heating_rate",
        icon="mdi:chart-line",
        state_class=SensorStateClass.MEASUREMENT,
        get_native_value=lambda entity: entity.device.water_heater_heating_rate,
        native_unit_of_measurement="",        system_types=[SystemType.VELIS],
        whe_types=[WheType.NuosSplit],
    ),
    AristonSensorEntityDescription(
        key=ARISTON_BUS_ERRORS,
        legacy_name="Количество ошибок",
        name=None,
        translation_key="bus_error_count",
        icon="mdi:alert-outline",
        coordinator=BUS_ERRORS_COORDINATOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        get_native_value=lambda entity: len(entity.device.bus_errors),
        native_unit_of_measurement="",
        extra_states=[
            {
                EXTRA_STATE_ATTRIBUTE: ATTR_ERRORS,
                EXTRA_STATE_DEVICE_METHOD: lambda entity: entity.device.bus_errors,
            },
        ],
    ),
    AristonSensorEntityDescription(
        key=EvoOneDeviceProperties.TEMP,
        legacy_name=f"{NAME} current temperature",
        name=None,
        translation_key="current_temperature",
        icon="mdi:thermometer-auto",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        get_native_value=lambda entity: entity.device.water_heater_current_temperature,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        system_types=[SystemType.VELIS],
        whe_types=[
            WheType.Evo,
        ],
    ),
    AristonSensorEntityDescription(
        key=VelisDeviceProperties.PROC_REQ_TEMP,
        legacy_name=f"{NAME} proc req temp",
        name=None,
        translation_key="process_requested_temperature",
        icon="mdi:thermometer-auto",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        get_native_value=lambda entity: entity.device.proc_req_temp_value,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        system_types=[SystemType.VELIS],
        whe_types=[
            WheType.NuosSplit,
            WheType.Evo2,
            WheType.LydosHybrid,
            WheType.Lydos,
            WheType.Andris2,
            WheType.Lux,
            WheType.Lux2,
        ],
    ),
    AristonSensorEntityDescription(
        key=DeviceProperties.DHW_STORAGE_TEMPERATURE,
        legacy_name=f"{NAME} DHW current temperature",
        name=None,
        translation_key="dhw_current_temperature",
        icon="mdi:thermometer-water",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        get_native_value=lambda entity: entity.device.water_heater_current_temperature,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_features=[CustomDeviceFeatures.HAS_DHW],
        system_types=[SystemType.GALEVO, SystemType.BSB],
    ),
    AristonSensorEntityDescription(
    key=DeviceProperties.DHW_TIMEPROG_ECONOMY_TEMP,
    legacy_name=f"{NAME} DHW economy temperature",
    name=None,
    translation_key="dhw_economy_temperature",
    icon="mdi:thermometer-chevron-down",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    get_native_value=lambda entity: entity.device._get_item_by_id(
        DeviceProperties.DHW_TIMEPROG_ECONOMY_TEMP,
        "value",
	),
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    device_features=[CustomDeviceFeatures.HAS_DHW],
    system_types=[SystemType.GALEVO],
    ),
    AristonSensorEntityDescription(
    key=DeviceProperties.DHW_TIMEPROG_COMFORT_TEMP,
    legacy_name=f"{NAME} DHW comfort temperature",
    name=None,
    translation_key="dhw_comfort_temperature",
    icon="mdi:thermometer-chevron-up",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    get_native_value=lambda entity: entity.device._get_item_by_id(
        DeviceProperties.DHW_TIMEPROG_COMFORT_TEMP,
        "value",
	),
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    device_features=[CustomDeviceFeatures.HAS_DHW],
    system_types=[SystemType.GALEVO],
    ),
    AristonSensorEntityDescription(
        key="DhwActiveProgram",
        legacy_name=f"{NAME} DHW active program",
        name=None,
        translation_key="dhw_active_program",
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.ENUM,
        options=["Comfort", "Economy", "Manual"],
        get_native_value=lambda entity: get_dhw_active_program(entity.device),
        device_features=[CustomDeviceFeatures.HAS_DHW],
        system_types=[SystemType.GALEVO],
    ),
    AristonSensorEntityDescription(
        key="DhwActiveTargetTemperature",
        legacy_name=f"{NAME} DHW active target temperature",
        name=None,
        translation_key="dhw_active_target_temperature",
        icon="mdi:thermometer-check",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        get_native_value=lambda entity: get_dhw_active_target_temperature(entity.device),
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_features=[CustomDeviceFeatures.HAS_DHW],
        system_types=[SystemType.GALEVO],
    ),
]

ARISTON_BINARY_SENSOR_TYPES: list[AristonBinarySensorEntityDescription] = [
    AristonBinarySensorEntityDescription(
        key=DeviceProperties.IS_FLAME_ON,
        legacy_name=f"{NAME} is flame on",
        name=None,
        translation_key="flame_active",
        icon="mdi:fire",
        get_is_on=lambda entity: entity.device.is_flame_on_value,
        system_types=[SystemType.GALEVO, SystemType.BSB],
    ),
    AristonBinarySensorEntityDescription(
        key=DeviceProperties.IS_HEATING_PUMP_ON,
        legacy_name=f"{NAME} is heating pump on",
        name=None,
        translation_key="heating_pump_active",
        icon="mdi:heat-pump-outline",
        get_is_on=lambda entity: entity.device.is_heating_pump_on_value,
        device_features=[DeviceFeatures.HYBRID_SYS],
        system_types=[SystemType.GALEVO],
    ),
    AristonBinarySensorEntityDescription(
        key=DeviceProperties.HOLIDAY,
        legacy_name=f"{NAME} holiday mode",
        name=None,
        translation_key="holiday_mode",
        icon="mdi:island",
        extra_states=[
            {
                EXTRA_STATE_ATTRIBUTE: ATTR_HOLIDAY,
                EXTRA_STATE_DEVICE_METHOD: lambda entity: entity.device.holiday_expires_on,
            }
        ],
        get_is_on=lambda entity: entity.device.holiday_mode_value,
        system_types=[SystemType.GALEVO],
    ),
    AristonBinarySensorEntityDescription(
        key=EvoLydosDeviceProperties.HEAT_REQ,
        legacy_name=f"{NAME} is heating",
        name=None,
        translation_key="heating_active",
        icon="mdi:fire",
        get_is_on=lambda entity: entity.device.is_heating,
        system_types=[SystemType.VELIS],
        whe_types=[
            WheType.Lux,
            WheType.Evo,
            WheType.Evo2,
            WheType.Lydos,
            WheType.LydosHybrid,
            WheType.Andris2,
            WheType.Lux2,
        ],
    ),
    AristonBinarySensorEntityDescription(
        key=EvoLydosDeviceProperties.ANTI_LEG,
        legacy_name=f"{NAME} anti-legionella cycle",
        name=None,
        translation_key="anti_legionella_cycle",
        icon="mdi:bacteria",
        get_is_on=lambda entity: entity.device.is_antileg,
        system_types=[SystemType.VELIS],
        whe_types=[
            WheType.Evo2,
            WheType.Lydos,
            WheType.LydosHybrid,
            WheType.Andris2,
        ],
    ),
]

ARISTON_SWITCH_TYPES: list[AristonSwitchEntityDescription] = [
    AristonSwitchEntityDescription(
        key=DeviceProperties.AUTOMATIC_THERMOREGULATION,
        legacy_name=f"{NAME} automatic thermoregulation",
        name=None,
        translation_key="automatic_thermoregulation",
        icon="mdi:radiator",
        device_features=[DeviceFeatures.AUTO_THERMO_REG],
        set_value=lambda entity,
        value: entity.device.async_set_automatic_thermoregulation(value),
        get_is_on=lambda entity: entity.device.automatic_thermoregulation,
        system_types=[SystemType.GALEVO],
    ),
    AristonSwitchEntityDescription(
        key=DeviceProperties.IS_QUIET,
        legacy_name=f"{NAME} is quiet",
        name=None,
        translation_key="quiet_mode",
        icon="mdi:volume-off",
        entity_category=EntityCategory.CONFIG,
        device_features=[DeviceProperties.IS_QUIET],
        set_value=lambda entity, value: entity.device.async_set_is_quiet(value),
        get_is_on=lambda entity: entity.device.is_quiet_value,
        system_types=[SystemType.GALEVO],
    ),
    AristonSwitchEntityDescription(
        key=EvoDeviceProperties.ECO,
        legacy_name=f"{NAME} eco mode",
        name=None,
        translation_key="eco_mode",
        icon="mdi:leaf",
        set_value=lambda entity, value: entity.device.async_set_eco_mode(value),
        get_is_on=lambda entity: entity.device.water_heater_eco_value,
        system_types=[SystemType.VELIS],
        whe_types=[
            WheType.Lux,
            WheType.Evo,
            WheType.Evo2,
            WheType.Lydos,
            WheType.Andris2,
            WheType.Lux2,
        ],
    ),
    AristonSwitchEntityDescription(
        key=EvoDeviceProperties.PWR_OPT,
        legacy_name=f"{NAME} power option",
        name=None,
        translation_key="power_option",
        icon="mdi:leaf",
        set_value=lambda entity,
        value: entity.device.async_set_water_heater_power_option(value),
        get_is_on=lambda entity: entity.device.water_heater_power_option_value,
        system_types=[SystemType.VELIS],
        whe_types=[WheType.Lux2],
    ),
    AristonSwitchEntityDescription(
        key=VelisDeviceProperties.ON,
        legacy_name=f"{NAME} power",
        name=None,
        translation_key="power",
        icon="mdi:power",
        set_value=lambda entity, value: entity.device.async_set_power(value),
        get_is_on=lambda entity: entity.device.water_heater_power_value,
        system_types=[SystemType.VELIS],
    ),
    AristonSwitchEntityDescription(
        key=MedDeviceSettings.MED_ANTILEGIONELLA_ON_OFF,
        legacy_name=f"{NAME} anti legionella",
        name=None,
        translation_key="anti_legionella",
        icon="mdi:bacteria-outline",
        entity_category=EntityCategory.CONFIG,
        set_value=lambda entity, value: entity.device.async_set_antilegionella(value),
        get_is_on=lambda entity: entity.device.water_anti_leg_value,
        system_types=[SystemType.VELIS],
        whe_types=[
            WheType.Andris2,
            WheType.Evo2,
            WheType.Lux,
            WheType.Lux2,
            WheType.Lydos,
            WheType.LydosHybrid,
            WheType.NuosSplit,
        ],
    ),
    AristonSwitchEntityDescription(
        key=SlpDeviceSettings.SLP_PRE_HEATING_ON_OFF,
        legacy_name=f"{NAME} preheating",
        name=None,
        translation_key="preheating",
        icon="mdi:heat-wave",
        entity_category=EntityCategory.CONFIG,
        set_value=lambda entity, value: entity.device.async_set_preheating(value),
        get_is_on=lambda entity: entity.device.water_heater_preheating_on_off,
        system_types=[SystemType.VELIS],
        whe_types=[WheType.NuosSplit],
    ),
    AristonSwitchEntityDescription(
        key=NuosSplitProperties.BOOST_ON,
        legacy_name=f"{NAME} boost",
        name=None,
        translation_key="boost",
        icon="mdi:car-turbocharger",
        entity_category=EntityCategory.CONFIG,
        set_value=lambda entity, value: entity.device.async_set_water_heater_boost(
            value
        ),
        get_is_on=lambda entity: entity.device.water_heater_boost,
        system_types=[SystemType.VELIS],
        whe_types=[WheType.NuosSplit],
    ),
    AristonSwitchEntityDescription(
        key=SeDeviceSettings.SE_PERMANENT_BOOST_ON_OFF,
        legacy_name=f"{NAME} permanent boost",
        name=None,
        translation_key="permanent_boost",
        icon="mdi:car-turbocharger",
        entity_category=EntityCategory.CONFIG,
        set_value=lambda entity, value: entity.device.async_set_permanent_boost_value(
            value
        ),
        get_is_on=lambda entity: entity.device.permanent_boost_value,
        system_types=[SystemType.VELIS],
        whe_types=[WheType.LydosHybrid],
    ),
    AristonSwitchEntityDescription(
        key=SeDeviceSettings.SE_ANTI_COOLING_ON_OFF,
        legacy_name=f"{NAME} anti cooling",
        name=None,
        translation_key="anti_cooling",
        icon="mdi:snowflake-thermometer",
        entity_category=EntityCategory.CONFIG,
        set_value=lambda entity, value: entity.device.async_set_anti_cooling_value(
            value
        ),
        get_is_on=lambda entity: entity.device.anti_cooling_value,
        system_types=[SystemType.VELIS],
        whe_types=[WheType.LydosHybrid],
    ),
    AristonSwitchEntityDescription(
        key=SeDeviceSettings.SE_NIGHT_MODE_ON_OFF,
        legacy_name=f"{NAME} night mode",
        name=None,
        translation_key="night_mode",
        icon="mdi:weather-night",
        entity_category=EntityCategory.CONFIG,
        set_value=lambda entity, value: entity.device.async_set_night_mode_value(value),
        get_is_on=lambda entity: entity.device.night_mode_value,
        system_types=[SystemType.VELIS],
        whe_types=[WheType.LydosHybrid],
    ),
]

ARISTON_NUMBER_TYPES: list[AristonNumberEntityDescription] = [
    AristonNumberEntityDescription(
        key=ConsumptionProperties.ELEC_COST,
        legacy_name=f"{NAME} Energy electricity cost",
        name=None,
        translation_key="energy_electricity_cost",
        icon="mdi:currency-sign",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=sys.maxsize,
        native_step=0.01,
        device_features=[DeviceFeatures.HAS_METERING],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.elect_cost,
        set_native_value=lambda entity, value: entity.device.async_set_elect_cost(
            value
        ),
        system_types=[SystemType.GALEVO],
    ),
    AristonNumberEntityDescription(
        key=ConsumptionProperties.GAS_COST,
        legacy_name=f"{NAME} Energy gas cost",
        name=None,
        translation_key="energy_gas_cost",
        icon="mdi:currency-sign",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=sys.maxsize,
        native_step=0.01,
        device_features=[DeviceFeatures.HAS_METERING],
        coordinator=ENERGY_COORDINATOR,
        get_native_value=lambda entity: entity.device.gas_cost,
        set_native_value=lambda entity, value: entity.device.async_set_gas_cost(value),
        system_types=[SystemType.GALEVO],
    ),
    AristonNumberEntityDescription(
        key=MedDeviceSettings.MED_MAX_SETPOINT_TEMPERATURE,
        legacy_name=f"{NAME} max setpoint temperature",
        name=None,
        translation_key="max_setpoint_temperature",
        icon="mdi:thermometer-high",
        entity_category=EntityCategory.CONFIG,
        get_native_min_value=lambda entity: entity.device.water_heater_maximum_setpoint_temperature_minimum,
        get_native_max_value=lambda entity: entity.device.water_heater_maximum_setpoint_temperature_maximum,
        native_step=1,
        get_native_value=lambda entity: entity.device.water_heater_maximum_setpoint_temperature,
        set_native_value=lambda entity,
        value: entity.device.async_set_max_setpoint_temp(value),
        system_types=[SystemType.VELIS],
        whe_types=[
            WheType.Andris2,
            WheType.Evo2,
            WheType.Lux,
            WheType.Lux2,
            WheType.Lydos,
            WheType.LydosHybrid,
            WheType.NuosSplit,
        ],
    ),
    AristonNumberEntityDescription(
        key=SlpDeviceSettings.SLP_MIN_SETPOINT_TEMPERATURE,
        legacy_name=f"{NAME} min setpoint temperature",
        name=None,
        translation_key="min_setpoint_temperature",
        icon="mdi:thermometer-low",
        entity_category=EntityCategory.CONFIG,
        get_native_min_value=lambda entity: entity.device.water_heater_minimum_setpoint_temperature_minimum,
        get_native_max_value=lambda entity: entity.device.water_heater_minimum_setpoint_temperature_maximum,
        native_step=1,
        get_native_value=lambda entity: entity.device.water_heater_minimum_setpoint_temperature,
        set_native_value=lambda entity,
        value: entity.device.async_set_min_setpoint_temp(value),
        system_types=[SystemType.VELIS],
        whe_types=[WheType.NuosSplit],
    ),
    AristonNumberEntityDescription(
        key=NuosSplitProperties.REDUCED_TEMP,
        legacy_name=f"{NAME} reduced temperature",
        name=None,
        translation_key="reduced_temperature",
        icon="mdi:thermometer-chevron-down",
        entity_category=EntityCategory.CONFIG,
        get_native_min_value=lambda entity: entity.device.water_heater_minimum_temperature,
        get_native_max_value=lambda entity: entity.device.water_heater_maximum_temperature,
        native_step=1,
        get_native_value=lambda entity: entity.device.water_heater_reduced_temperature,
        set_native_value=lambda entity,
        value: entity.device.async_set_water_heater_reduced_temperature(value),
        system_types=[SystemType.VELIS],
        whe_types=[WheType.NuosSplit],
    ),
    AristonNumberEntityDescription(
        key=ThermostatProperties.HEATING_FLOW_TEMP,
        legacy_name=f"{NAME} heating flow temperature",
        name=None,
        translation_key="heating_flow_temperature",
        icon="mdi:thermometer",
        entity_category=EntityCategory.CONFIG,
        zone=True,
        get_native_min_value=lambda entity: entity.device.get_heating_flow_temp_min(
            entity.zone
        ),
        get_native_max_value=lambda entity: entity.device.get_heating_flow_temp_max(
            entity.zone
        ),
        get_native_step=lambda entity: entity.device.get_heating_flow_temp_step(
            entity.zone
        ),
        get_native_value=lambda entity: entity.device.get_heating_flow_temp_value(
            entity.zone
        ),
        set_native_value=lambda entity,
        value: entity.device.async_set_heating_flow_temp(value, entity.zone),
        system_types=[SystemType.GALEVO],
    ),
    AristonNumberEntityDescription(
        key=ThermostatProperties.HEATING_FLOW_OFFSET,
        legacy_name=f"{NAME} heating flow offset",
        name=None,
        translation_key="heating_flow_offset",
        icon="mdi:progress-wrench",
        entity_category=EntityCategory.CONFIG,
        zone=True,
        get_native_min_value=lambda entity: entity.device.get_heating_flow_offset_min(
            entity.zone
        ),
        get_native_max_value=lambda entity: entity.device.get_heating_flow_offset_max(
            entity.zone
        ),
        get_native_step=lambda entity: entity.device.get_heating_flow_offset_step(
            entity.zone
        ),
        get_native_value=lambda entity: entity.device.get_heating_flow_offset_value(
            entity.zone
        ),
        set_native_value=lambda entity,
        value: entity.device.async_set_heating_flow_offset(value, entity.zone),
        system_types=[SystemType.GALEVO],
    ),
    AristonNumberEntityDescription(
        key=EvoOneDeviceProperties.AV_SHW,
        legacy_name=f"{NAME} requested number of showers",
        name=None,
        translation_key="requested_showers",
        icon="mdi:shower-head",
        native_min_value=0,
        get_native_max_value=lambda entity: entity.device.max_req_shower,
        native_step=1,
        get_native_value=lambda entity: entity.device.req_shower,
        set_native_value=lambda entity,
        value: entity.device.async_set_water_heater_number_of_showers(int(value)),
        whe_types=[WheType.Evo],
    ),
    AristonNumberEntityDescription(
        key=SeDeviceSettings.SE_ANTI_COOLING_TEMPERATURE,
        legacy_name=f"{NAME} anti cooling temperature",
        name=None,
        translation_key="anti_cooling_temperature",
        icon="mdi:thermometer-alert",
        entity_category=EntityCategory.CONFIG,
        get_native_min_value=lambda entity: entity.device.anti_cooling_temperature_minimum,
        get_native_max_value=lambda entity: entity.device.anti_cooling_temperature_maximum,
        native_step=1,
        get_native_value=lambda entity: entity.device.anti_cooling_temperature_value,
        set_native_value=lambda entity,
        value: entity.device.async_set_cooling_temperature_value(int(value)),
        whe_types=[WheType.LydosHybrid],
    ),
]

ARISTON_SELECT_TYPES: list[AristonSelectEntityDescription] = [
    AristonSelectEntityDescription(
        key="DhwScenario",
        legacy_name=f"{NAME} DHW scenario",
        name=None,
        translation_key="dhw_scenario",
        icon="mdi:calendar-sync",
        entity_category=EntityCategory.CONFIG,
        device_features=[CustomDeviceFeatures.HAS_DHW],
        get_current_option=lambda entity: get_dhw_scenario(entity.device),
        get_options=lambda entity: list(DHW_SCENARIOS.keys()),
        select_option=async_set_dhw_scenario,
        system_types=[SystemType.GALEVO],
    ),
    AristonSelectEntityDescription(
        key=ConsumptionProperties.CURRENCY,
        legacy_name=f"{NAME} Energy currency",
        name=None,
        translation_key="energy_currency",
        icon="mdi:cash-100",
        device_class=SensorDeviceClass.MONETARY,
        entity_category=EntityCategory.CONFIG,
        device_features=[DeviceFeatures.HAS_METERING],
        coordinator=ENERGY_COORDINATOR,
        get_current_option=lambda entity: entity.device.currency,
        get_options=lambda entity: entity.device.get_currencies(),
        select_option=lambda entity, option: entity.device.async_set_currency(option),
        system_types=[SystemType.GALEVO],
    ),
    AristonSelectEntityDescription(
        key=ConsumptionProperties.GAS_TYPE,
        legacy_name=f"{NAME} Energy gas type",
        name=None,
        translation_key="energy_gas_type",
        icon="mdi:gas-cylinder",
        entity_category=EntityCategory.CONFIG,
        device_features=[DeviceFeatures.HAS_METERING],
        coordinator=ENERGY_COORDINATOR,
        get_current_option=lambda entity: entity.device.gas_type,
        get_options=lambda entity: entity.device.get_gas_types(),
        select_option=lambda entity, option: entity.device.async_set_gas_type(option),
        system_types=[SystemType.GALEVO],
    ),
    AristonSelectEntityDescription(
        key=ConsumptionProperties.GAS_ENERGY_UNIT,
        legacy_name=f"{NAME} Energy gas energy unit",
        name=None,
        translation_key="energy_gas_energy_unit",
        icon="mdi:cube-scan",
        entity_category=EntityCategory.CONFIG,
        device_features=[DeviceFeatures.HAS_METERING],
        coordinator=ENERGY_COORDINATOR,
        get_current_option=lambda entity: entity.device.gas_energy_unit,
        get_options=lambda entity: entity.device.get_gas_energy_units(),
        select_option=lambda entity, option: entity.device.async_set_gas_energy_unit(
            option
        ),
        system_types=[SystemType.GALEVO],
    ),
    AristonSelectEntityDescription(
        key=DeviceProperties.HYBRID_MODE,
        legacy_name=f"{NAME} hybrid mode",
        name=None,
        translation_key="hybrid_mode",
        icon="mdi:cog",
        entity_category=EntityCategory.CONFIG,
        device_features=[DeviceFeatures.HYBRID_SYS],
        get_current_option=lambda entity: entity.device.hybrid_mode,
        get_options=lambda entity: entity.device.hybrid_mode_opt_texts,
        select_option=lambda entity, option: entity.device.async_set_hybrid_mode(
            option
        ),
        system_types=[SystemType.GALEVO],
    ),
    AristonSelectEntityDescription(
        key=DeviceProperties.BUFFER_CONTROL_MODE,
        legacy_name=f"{NAME} buffer control mode",
        name=None,
        translation_key="buffer_control_mode",
        icon="mdi:cup-water",
        entity_category=EntityCategory.CONFIG,
        device_features=[DeviceFeatures.BUFFER_TIME_PROG_AVAILABLE],
        get_current_option=lambda entity: entity.device.buffer_control_mode,
        get_options=lambda entity: entity.device.buffer_control_mode_opt_texts,
        select_option=lambda entity,
        option: entity.device.async_set_buffer_control_mode(option),
        system_types=[SystemType.GALEVO],
    ),
    AristonSelectEntityDescription(
        key=EvoOneDeviceProperties.MODE,
        legacy_name=f"{NAME} operation mode",
        name=None,
        translation_key="operation_mode",
        icon="mdi:cog",
        get_current_option=lambda entity: entity.device.water_heater_current_mode_text,
        get_options=lambda entity: entity.device.water_heater_mode_operation_texts,
        select_option=lambda entity,
        option: entity.device.async_set_water_heater_operation_mode(option),
        system_types=[SystemType.VELIS],
        whe_types=[WheType.Evo],
    ),
]
