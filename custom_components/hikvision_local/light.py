"""Platform for supplement light entities."""

from __future__ import annotations

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikvisionConfigEntry
from .const import LIGHTS_COORDINATOR, SUPPLEMENT_LIGHT_OFF_MODE
from .isapi import SupplementLightState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add Hikvision supplement light entities from a config_entry."""

    device = entry.runtime_data
    coordinator = device.coordinators.get(LIGHTS_COORDINATOR)
    if not coordinator:
        return

    entities: list[HikvisionSupplementLight] = []
    for camera in device.cameras:
        if not camera.supplement_light:
            continue
        entity = HikvisionSupplementLight(device, coordinator, camera)
        if entity.is_supported:
            entities.append(entity)

    if entities:
        async_add_entities(entities)


class HikvisionSupplementLight(CoordinatorEntity, LightEntity):
    """Representation of a Hikvision supplement light."""

    _attr_has_entity_name = True
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_translation_key = "supplement_light"

    def __init__(self, device, coordinator, camera) -> None:
        """Initialize supplement light entity."""
        super().__init__(coordinator)
        self._device = device
        self._camera = camera
        self._light = camera.supplement_light
        self._attr_unique_id = device.build_camera_light_unique_id(camera)
        self.entity_id = f"light.{self.unique_id}"
        self._attr_device_info = device.hass_device_info(camera.id)
        self._on_mode = self._determine_on_mode()

    def _determine_on_mode(self) -> str | None:
        if not self._light:
            return None
        for mode in self._light.capabilities.supported_modes:
            if mode.lower() != SUPPLEMENT_LIGHT_OFF_MODE:
                return mode
        return None

    def _get_state(self) -> SupplementLightState | None:
        if not self._light:
            return None
        coordinator_state = self.coordinator.data.get(self.unique_id)
        return coordinator_state or self._light.state

    @property
    def is_supported(self) -> bool:
        return self._on_mode is not None

    @staticmethod
    def _clamp(value: int, min_value: int, max_value: int) -> int:
        return max(min_value, min(value, max_value))

    def _device_brightness_to_ha(self, value: int) -> int:
        caps = self._light.capabilities
        span = max(caps.brightness_max - caps.brightness_min, 1)
        adjusted = value - caps.brightness_min
        percentage = self._clamp(adjusted, 0, span) / span
        return int(round(percentage * 255))

    def _ha_brightness_to_device(self, value: int) -> int:
        caps = self._light.capabilities
        span = max(caps.brightness_max - caps.brightness_min, 1)
        percentage = value / 255
        device_value = caps.brightness_min + round(span * percentage)
        return self._clamp(device_value, caps.brightness_min, caps.brightness_max)

    @property
    def is_on(self) -> bool:
        state = self._get_state()
        if not state:
            return False
        return state.mode.lower() != SUPPLEMENT_LIGHT_OFF_MODE

    @property
    def brightness(self) -> int | None:
        state = self._get_state()
        if not state or not self.is_on:
            return None
        return self._device_brightness_to_ha(state.brightness)

    @property
    def color_mode(self) -> ColorMode:
        return ColorMode.BRIGHTNESS

    @property
    def available(self) -> bool:
        return self._light is not None and self.is_supported

    @property
    def extra_state_attributes(self) -> dict | None:
        state = self._get_state()
        if not state:
            return None
        attrs: dict[str, str] = {}
        if state.regulation_mode:
            attrs["regulation_mode"] = state.regulation_mode
        return attrs or None

    async def async_turn_on(self, **kwargs) -> None:
        if not self._light or not self._on_mode:
            return
        state = self._get_state()
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is not None:
            device_brightness = self._ha_brightness_to_device(brightness)
        elif state:
            device_brightness = state.brightness
        else:
            device_brightness = self._light.capabilities.brightness_max

        new_state = await self.coordinator.device.set_supplement_light_state(
            self._camera.id,
            self._on_mode,
            device_brightness,
            state.regulation_mode if state else None,
        )
        self._light.state = new_state
        self.coordinator.data[self.unique_id] = new_state
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        if not self._light:
            return
        state = self._get_state()
        device_brightness = state.brightness if state else self._light.capabilities.brightness_min
        regulation_mode = state.regulation_mode if state else None
        new_state = await self.coordinator.device.set_supplement_light_state(
            self._camera.id,
            SUPPLEMENT_LIGHT_OFF_MODE,
            device_brightness,
            regulation_mode,
        )
        self._light.state = new_state
        self.coordinator.data[self.unique_id] = new_state
        self.async_write_ha_state()
