"""Component providing support for Hikvision IP cameras."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from . import HikvisionConfigEntry
from .hikvision_device import HikvisionDevice
from .isapi import AnalogCamera, CameraStreamInfo, IPCamera

try:
    from homeassistant.components.camera import CameraEntityCapabilities
except ImportError:  # Home Assistant 2026.10
    CameraEntityCapabilities = None

# deviceType of cameras; recorders report NVR, DVR or IPC
CAMERA_DEVICE_TYPES = ("IPCamera", "IPDome")


async def async_setup_entry(
    hass: HomeAssistant, entry: HikvisionConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up a Hikvision IP Camera."""

    device = entry.runtime_data

    entities = []
    for camera in device.cameras:
        for stream in camera.streams:
            entities.append(HikvisionCamera(device, camera, stream))

    async_add_entities(entities)


class HikvisionCamera(Camera):
    """An implementation of a Hikvision IP camera."""

    _attr_supported_features: CameraEntityFeature = CameraEntityFeature.STREAM

    def __init__(
        self,
        device: HikvisionDevice,
        camera: AnalogCamera | IPCamera,
        stream_info: CameraStreamInfo,
    ) -> None:
        """Initialize Hikvision camera stream."""
        Camera.__init__(self)

        self._attr_device_info = device.hass_device_info(camera.id)
        self._attr_unique_id = slugify(f"{device.device_info.serial_no.lower()}_{stream_info.id}")
        if stream_info.type_id > 1:
            self._attr_has_entity_name = True
            self._attr_translation_key = f"stream{stream_info.type_id}"
            self._attr_entity_registry_enabled_default = False
        else:
            # for the main stream use just its name
            self._attr_name = camera.name
        self.device = device
        self.stream_info = stream_info

        # go2rtc sends the talk audio over the RTSP backchannel. Cameras with a speaker offer it. NVRs
        # don't offer it for their channels, only ISAPI talk, which go2rtc in Home Assistant can't use.
        if (
            CameraEntityCapabilities
            and device.device_info.device_type in CAMERA_DEVICE_TYPES
            and device.capabilities.audio_outputs
        ):
            self._attr_camera_entity_capabilities = CameraEntityCapabilities(two_way_audio=True)

    async def stream_source(self) -> str | None:
        """Return the source of the stream."""
        return self.device.get_stream_source(self.stream_info)

    @property
    def use_stream_for_stills(self) -> bool:
        """Generate stills from the stream when the device does not support snapshots."""
        return self.stream_info.snapshot_not_supported

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        """Return a still image response from the camera."""
        image = await self.device.get_camera_image(self.stream_info, width, height)
        if not image or image.startswith(b"<?xml"):
            # Request failed, or an ISAPI error document instead of an image
            return None
        return image
