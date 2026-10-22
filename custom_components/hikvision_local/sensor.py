"""Platform for sensor integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import HikvisionConfigEntry
from .const import CONF_ALARM_SERVER_HOST, SECONDARY_COORDINATOR, SIGNAL_ALERT_RECEIVED
from .isapi import AlertInfo, StorageInfo

NOTIFICATION_HOST_KEYS = [
    "protocol_type",
    "address",  # ip_address or host_name
    "port_no",
    "path",
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add diagnostic sensors for hikvision alarm server settings and storage items."""

    device = entry.runtime_data
    coordinator = device.coordinators.get(SECONDARY_COORDINATOR)

    entities = []
    if coordinator:
        for key in NOTIFICATION_HOST_KEYS:
            entities.append(AlarmServerSensor(coordinator, key))

        for item in list(device.storage):
            entities.append(StorageSensor(coordinator, item))

    if device.capabilities.support_alarm_server:
        entities.append(LastAlertSensor(entry))

    async_add_entities(entities, True)


class AlarmServerSensor(CoordinatorEntity, SensorEntity):
    """Alarm Server settings sensor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:ip-network"

    def __init__(self, coordinator, key: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = f"{device.device_info.serial_no}_{CONF_ALARM_SERVER_HOST}_{key}"
        self._attr_device_info = device.hass_device_info()
        self._attr_translation_key = f"notifications_host_{key}"
        self.key = key

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        host = (self.coordinator.data or {}).get(CONF_ALARM_SERVER_HOST)
        return host.get(self.key) if host else None  # device unreachable: no data


class StorageSensor(CoordinatorEntity, SensorEntity):
    """HDD, NAS status sensor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:harddisk"

    def __init__(self, coordinator, hdd: StorageInfo) -> None:
        """Initialize."""
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = f"{device.device_info.serial_no}_{hdd.id}_{hdd.name}"
        self._attr_device_info = device.hass_device_info()
        self._attr_name = f"{hdd.type} {hdd.name}"
        self.hdd = hdd

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        hdd = self.coordinator.device.get_storage_device_by_id(self.hdd.id)
        return str(hdd.status).upper() if hdd else None

    @property
    def extra_state_attributes(self):
        """Return extra attributes."""
        attrs = {}
        attrs["type"] = self.hdd.type
        attrs["capacity"] = self.hdd.capacity
        attrs["freespace"] = self.hdd.freespace
        if self.hdd.ip:
            attrs["ip"] = self.hdd.ip
        return attrs


class LastAlertSensor(RestoreSensor):
    """When the device last sent an event alert to Home Assistant."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_should_poll = False
    _attr_translation_key = "last_alert"

    def __init__(self, entry: HikvisionConfigEntry) -> None:
        """Initialize."""
        device = entry.runtime_data
        self._attr_unique_id = f"{device.device_info.serial_no}_last_alert"
        self._attr_device_info = device.hass_device_info()
        self._attr_native_value: datetime | None = None
        self._attr_extra_state_attributes: dict[str, Any] = {}
        self.entry_id = entry.entry_id

    async def async_added_to_hass(self) -> None:
        """Restore the last alert time and listen for new alerts."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) and isinstance(last.native_value, datetime):
            self._attr_native_value = last.native_value
        if (state := await self.async_get_last_state()) is not None:
            self._attr_extra_state_attributes = {
                key: value for key, value in state.attributes.items() if key in ("event_id", "channel_id")
            }
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_ALERT_RECEIVED.format(self.entry_id), self._alert_received)
        )

    @callback
    def _alert_received(self, alert: AlertInfo) -> None:
        self._attr_native_value = dt_util.utcnow()
        self._attr_extra_state_attributes = {"event_id": alert.event_id, "channel_id": alert.channel_id}
        self.async_write_ha_state()
