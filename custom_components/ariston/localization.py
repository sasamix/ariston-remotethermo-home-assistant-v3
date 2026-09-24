"""Entity localization helpers for the Ariston integration.

Translation keys are intentionally mapped from the existing fallback names instead of
rewriting entity descriptions.  This keeps legacy names available as a fallback and,
more importantly, lets entity unique IDs remain compatible with existing installations.
"""

from __future__ import annotations

ENTITY_TRANSLATION_KEYS_BY_NAME: dict[str, str] = {
    # Sensors
    "Газ отопление — текущие 2 часа": "gas_heating_current_2h_volume",
    "Газ ГВС — текущие 2 часа": "gas_dhw_current_2h_volume",
    "Газ отопление — текущий месяц": "gas_heating_current_month",
    "Газ ГВС — текущий месяц": "gas_dhw_current_month",
    "Газ отопление — средний расход за текущие 2 часа": "gas_heating_current_2h_average_flow",
    "Газ ГВС — средний расход за текущие 2 часа": "gas_dhw_current_2h_average_flow",
    "Давление в контуре отопления": "heating_circuit_pressure",
    "Ariston CH flow setpoint temp": "ch_flow_setpoint_temperature",
    "Ariston CH flow temp": "ch_flow_temperature",
    "Уровень сигнала": "signal_level",
    "Ariston CH return temp": "ch_return_temperature",
    "Ariston Outside temp": "outside_temperature",
    "Ariston average showers": "average_showers",
    "Ariston electricity consumption for heating last month": "electricity_heating_last_month",
    "Ariston electricity consumption for cooling last month": "electricity_cooling_last_month",
    "Ariston electricity consumption for water last month": "electricity_water_last_month",
    "Отопление — общее энергопотребление": "heating_total_energy_consumption",
    "ГВС — общее энергопотребление": "dhw_total_energy_consumption",
    "Ariston central heating gas consumption": "central_heating_gas_consumption",
    "Ariston domestic hot water heating pump electricity consumption": "dhw_heating_pump_electricity_consumption",
    "Ariston domestic hot water resistor electricity consumption": "dhw_resistor_electricity_consumption",
    "Ariston domestic hot water gas consumption": "dhw_gas_consumption",
    "Газ отопление — средняя мощность за час": "gas_heating_hourly_average_power",
    "Газ ГВС — средняя мощность за час": "gas_dhw_hourly_average_power",
    "Ariston central heating electricity consumption": "central_heating_electricity_consumption",
    "Ariston domestic hot water electricity consumption": "dhw_electricity_consumption",
    "Ariston remaining time": "remaining_time",
    "Ariston heating rate": "heating_rate",
    "Количество ошибок": "bus_error_count",
    "Ariston current temperature": "current_temperature",
    "Ariston proc req temp": "process_requested_temperature",
    "Ariston DHW current temperature": "dhw_current_temperature",
    "Ariston DHW economy temperature": "dhw_economy_temperature",
    "Ariston DHW comfort temperature": "dhw_comfort_temperature",
    "Ariston DHW active program": "dhw_active_program",
    "Ariston DHW active target temperature": "dhw_active_target_temperature",

    # Binary sensors
    "Ariston is flame on": "flame_active",
    "Ariston is heating pump on": "heating_pump_active",
    "Ariston holiday mode": "holiday_mode",
    "Ariston is heating": "heating_active",
    "Ariston anti-legionella cycle": "anti_legionella_cycle",

    # Switches
    "Ariston automatic thermoregulation": "automatic_thermoregulation",
    "Ariston is quiet": "quiet_mode",
    "Ariston eco mode": "eco_mode",
    "Ariston power option": "power_option",
    "Ariston power": "power",
    "Ariston anti legionella": "anti_legionella",
    "Ariston preheating": "preheating",
    "Ariston boost": "boost",
    "Ariston permanent boost": "permanent_boost",
    "Ariston anti cooling": "anti_cooling",
    "Ariston night mode": "night_mode",

    # Numbers
    "Ariston Energy electricity cost": "energy_electricity_cost",
    "Ariston Energy gas cost": "energy_gas_cost",
    "Ariston max setpoint temperature": "max_setpoint_temperature",
    "Ariston min setpoint temperature": "min_setpoint_temperature",
    "Ariston reduced temperature": "reduced_temperature",
    "Ariston heating flow temperature": "heating_flow_temperature",
    "Ariston heating flow offset": "heating_flow_offset",
    "Ariston requested number of showers": "requested_showers",
    "Ariston anti cooling temperature": "anti_cooling_temperature",

    # Selects
    "Ariston DHW scenario": "dhw_scenario",
    "Ariston Energy currency": "energy_currency",
    "Ariston Energy gas type": "energy_gas_type",
    "Ariston Energy gas energy unit": "energy_gas_energy_unit",
    "Ariston hybrid mode": "hybrid_mode",
    "Ariston buffer control mode": "buffer_control_mode",
    "Ariston operation mode": "operation_mode",

    # Text/button entities added by this fork
    "Ariston DHW scenario name": "dhw_scenario_name",
    "Ariston DHW scenario save": "dhw_scenario_save",
}


def get_entity_translation_key(name: str | None) -> str | None:
    """Return the translation key for a legacy entity fallback name."""
    if not name:
        return None
    return ENTITY_TRANSLATION_KEYS_BY_NAME.get(name)
