"""Platform for select integration."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
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
    if not coordinator:
        return

    async_add_entities([AudioAlarmSoundSelect(coordinator)])


class AudioAlarmSoundSelect(CoordinatorEntity, SelectEntity):
    """Sound the device plays as an audio alarm."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:music-note"
    _attr_translation_key = "audio_alarm_sound"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = device.build_audio_alarm_unique_id("sound")
        self._attr_device_info = device.hass_device_info()
        self._sounds = device.audio_alarm.capabilities.sounds
        self._attr_options = [sound.name for sound in self._sounds]

    @property
    def current_option(self) -> str | None:
        """Return the selected sound."""
        if not self.coordinator.data:
            return None
        sound_id = self.coordinator.data.sound_id
        return next((sound.name for sound in self._sounds if sound.id == sound_id), None)

    async def async_select_option(self, option: str) -> None:
        """Change the sound."""
        sound_id = next(sound.id for sound in self._sounds if sound.name == option)
        state = await self.coordinator.device.set_audio_alarm_state(sound_id=sound_id)
        self.coordinator.async_set_updated_data(state)
