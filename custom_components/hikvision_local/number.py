"""Platform for number integration."""

from __future__ import annotations

from homeassistant.components.number import ENTITY_ID_FORMAT, NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory
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

    async_add_entities([AudioAlarmVolumeNumber(coordinator), AudioAlarmTimesNumber(coordinator)])


class AudioAlarmNumber(CoordinatorEntity, NumberEntity):
    """Base class for audio alarm settings."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_step = 1
    _key: str

    def __init__(self, coordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = device.build_audio_alarm_unique_id(self._key)
        self.entity_id = ENTITY_ID_FORMAT.format(self.unique_id)
        self._attr_device_info = device.hass_device_info()

    @property
    def native_value(self) -> int | None:
        """Return the current value."""
        if not self.coordinator.data:
            return None
        return getattr(self.coordinator.data, self._key)

    async def async_set_native_value(self, value: float) -> None:
        """Change the value."""
        state = await self.coordinator.device.set_audio_alarm_state(**{self._key: int(value)})
        self.coordinator.async_set_updated_data(state)


class AudioAlarmVolumeNumber(AudioAlarmNumber):
    """Audio alarm volume."""

    _attr_icon = "mdi:volume-high"
    _attr_translation_key = "audio_alarm_volume"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _key = "volume"

    def __init__(self, coordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        capabilities = coordinator.device.audio_alarm.capabilities
        self._attr_native_min_value = capabilities.volume_min
        self._attr_native_max_value = capabilities.volume_max


class AudioAlarmTimesNumber(AudioAlarmNumber):
    """Number of times the audio alarm sound plays."""

    _attr_icon = "mdi:repeat"
    _attr_translation_key = "audio_alarm_times"
    _attr_mode = NumberMode.BOX
    _key = "alarm_times"

    def __init__(self, coordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        capabilities = coordinator.device.audio_alarm.capabilities
        self._attr_native_min_value = capabilities.alarm_times_min
        self._attr_native_max_value = capabilities.alarm_times_max
