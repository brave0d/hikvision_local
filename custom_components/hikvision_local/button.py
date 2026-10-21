"""Platform for button integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikvisionConfigEntry
from .const import AUDIO_ALARM_COORDINATOR


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add hikvision_local entities from a config_entry."""

    device = entry.runtime_data
    coordinator = device.coordinators.get(AUDIO_ALARM_COORDINATOR)
    if not coordinator or not device.audio_alarm.capabilities.support_test:
        return

    async_add_entities([AudioAlarmTestButton(coordinator)])


class AudioAlarmTestButton(CoordinatorEntity, ButtonEntity):
    """Play the configured audio alarm sound once."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:bullhorn"
    _attr_translation_key = "audio_alarm_test"

    def __init__(self, coordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = device.build_audio_alarm_unique_id("test")
        self._attr_device_info = device.hass_device_info()

    async def async_press(self) -> None:
        """Play the sound."""
        audio_alarm = self.coordinator.device.audio_alarm
        sound_id = self.coordinator.data.sound_id if self.coordinator.data else None
        if sound_id is None:
            # the device is set to a prompt or custom sound, which has no test ID here
            sound_id = audio_alarm.capabilities.sounds[0].id
        await self.coordinator.device.test_audio_alarm(sound_id)
