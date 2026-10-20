"""Button entity for saving the current Ariston DHW schedule."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from ariston import SystemType

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.components import persistent_notification
from homeassistant.helpers import entity_registry as er
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory

from .const import COORDINATOR, DOMAIN, ENERGY_COORDINATOR
from .coordinator import DeviceDataUpdateCoordinator
from .dhw_scenarios import DHW_SCENARIO_MANAGER
from .heating_scenarios import HEATING_SCENARIO_MANAGERS
from .entity import AristonEntity
from .sensor import _metering_device_info


@dataclass(kw_only=True, frozen=True)
class AristonButtonEntityDescription(ButtonEntityDescription):
    """Button description compatible with AristonEntity."""

    extra_states: list | None = None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up DHW and heating save-current-scenario buttons."""
    data = hass.data[DOMAIN][entry.unique_id]
    coordinator: DeviceDataUpdateCoordinator = data[COORDINATOR]
    entities = []

    manager = data.get(DHW_SCENARIO_MANAGER)
    if manager is not None:
        entities.append(AristonSaveCurrentDhwScenario(coordinator, manager))

    for zone, heating_manager in data.get(
        HEATING_SCENARIO_MANAGERS, {}
    ).items():
        entities.append(
            AristonSaveCurrentHeatingScenario(
                coordinator,
                heating_manager,
                zone,
            )
        )

    # The virtual energy device has a useful read-only diagnostic export,
    # rather than the old device-registry Delete action.
    meter = data.get(ENERGY_COORDINATOR)
    if (
        meter is not None
        and meter.device is not None
        and meter.device.system_type == SystemType.GALEVO
    ):
        entities.append(AristonExportR2Diagnostics(meter, entry))

    async_add_entities(entities)


def _write_diagnostics(path: Path, report: dict) -> None:
    """Keep R2 diagnostic details in private /config, never in public /local."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


class AristonExportR2Diagnostics(AristonEntity, ButtonEntity):
    """Save a credential-free R2 report and all Ariston entity statuses."""

    entity_description = AristonButtonEntityDescription(
        key="ExportR2Diagnostics",
        name="Сохранить диагностику R2",
        icon="mdi:file-export-outline",
    )

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, self.entity_description)
        self._entry_id = entry.entry_id

    @property
    def unique_id(self) -> str:
        """Stable diagnostic action, unrelated to metering sensor identities."""
        return f"{self.device.gateway}-Ariston export R2 diagnostics"

    @property
    def device_info(self):
        """Put the export action on the linked Energy device."""
        return _metering_device_info(super().device_info)

    async def async_press(self) -> None:
        """Write source reports and registry/status diagnostics without secrets."""
        registry = er.async_get(self.hass)
        entities = []
        for item in er.async_entries_for_config_entry(registry, self._entry_id):
            if item.platform != DOMAIN:
                continue
            state = self.hass.states.get(item.entity_id)
            entities.append({
                "entity_id": item.entity_id,
                "unique_id": item.unique_id,
                "device_id": item.device_id,
                "category": str(item.entity_category) if item.entity_category else None,
                "disabled_by": str(item.disabled_by) if item.disabled_by else None,
                "state": state.state if state is not None else None,
                "last_updated": (
                    state.last_updated.isoformat() if state is not None else None
                ),
                "unit": (
                    state.attributes.get("unit_of_measurement")
                    if state is not None else None
                ),
                "source_status": (
                    state.attributes.get("source_sync_status")
                    if state is not None else None
                ),
                "r2_source_total_m3": (
                    state.attributes.get("r2_source_total_m3")
                    if state is not None else None
                ),
                "r2_source_gap_m3": (
                    state.attributes.get("r2_source_gap_m3")
                    if state is not None else None
                ),
            })
        now = datetime.now().astimezone()
        device = self.device
        report = {
            "generated_at": now.isoformat(),
            "gateway": str(getattr(device, "gw", getattr(device, "gateway", ""))),
            "system_type": str(getattr(getattr(device, "system_type", None), "name", "")),
            "r2_gas_snapshot_time": getattr(device, "r2_gas_snapshot_time", None),
            "r2_kwh_snapshot_time": getattr(device, "r2_kwh_snapshot_time", None),
            "api_v2_snapshot_time": getattr(device, "energy_sequences_time", None),
            "entities": sorted(entities, key=lambda item: item["entity_id"]),
            "r2_physical_unit": getattr(device, "gas_physical_unit_data", None),
            "r2_kwh": getattr(device, "r2_kwh_data", None),
            "r2_kwh_raw": getattr(device, "r2_kwh_raw", None),
        }
        path = Path(self.hass.config.path(
            "ariston_diagnostics",
            f"r2_{now.strftime('%Y%m%d_%H%M%S_%f')}.json",
        ))
        await self.hass.async_add_executor_job(_write_diagnostics, path, report)
        persistent_notification.async_create(
            self.hass,
            (
                "Диагностика R2 сохранена без паролей и токенов.\n\n"
                f"Файл: `{path}`\n\n"
                f"Проверено сущностей Ariston: **{len(entities)}**. "
                "Файл доступен через File Editor или терминал HA."
            ),
            title="Ariston — диагностика R2 сохранена",
            notification_id="ariston_r2_diagnostics",
        )


class AristonSaveCurrentDhwScenario(AristonEntity, ButtonEntity):
    """Save the currently loaded Ariston schedule under the entered HA name."""

    entity_description = AristonButtonEntityDescription(
        key="SaveCurrentDhwScenario",
        name=None,
        translation_key="dhw_scenario_save",
        icon="mdi:content-save",
        entity_category=EntityCategory.CONFIG,
    )

    def __init__(self, coordinator, manager) -> None:
        super().__init__(coordinator, self.entity_description)
        self.manager = manager

    @property
    def unique_id(self) -> str:
        """Keep the original unique id after moving the button into the DHW group."""
        return f"{self.device.gateway}-Ariston save current DHW scenario"

    async def async_press(self) -> None:
        """Save the current cloud schedule under the text entity's draft name."""
        await self.manager.async_save_current(self.manager.draft_name)


class AristonSaveCurrentHeatingScenario(AristonEntity, ButtonEntity):
    """Save the current heating-zone schedule under the entered HA name."""

    def __init__(self, coordinator, manager, zone: int) -> None:
        description = AristonButtonEntityDescription(
            key=f"SaveCurrentHeatingScenarioZone{zone}",
            name=None,
            translation_key="heating_scenario_save",
            translation_placeholders={"zone": str(zone)},
            icon="mdi:content-save",
            entity_category=EntityCategory.CONFIG,
        )
        super().__init__(coordinator, description, zone)
        self.manager = manager

    @property
    def unique_id(self) -> str:
        """Keep the original unique id while changing display order/name."""
        return (
            f"{self.device.gateway}-"
            f"Ariston save current heating scenario zone {self.zone}-{self.zone}"
        )

    async def async_press(self) -> None:
        """Save the current heating schedule under the text entity draft."""
        await self.manager.async_save_current(self.manager.draft_name)
