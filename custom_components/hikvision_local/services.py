"Integration actions."

import asyncio
import logging

from homeassistant.components.camera.helper import get_camera_from_entity_id
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from httpx import HTTPStatusError
import voluptuous as vol

from .const import (
    ACTION_ISAPI_REQUEST,
    ACTION_PTZ_MOVE,
    ACTION_PTZ_STOP,
    ACTION_REBOOT,
    ATTR_CONFIG_ENTRY_ID,
    DOMAIN,
)
from .isapi import ISAPIForbiddenError, ISAPIUnauthorizedError

_LOGGER = logging.getLogger(__name__)

# Direction to (pan, tilt, zoom), at half speed.
PTZ_DIRECTIONS = {
    "up": (0, 50, 0),
    "down": (0, -50, 0),
    "left": (-50, 0, 0),
    "right": (50, 0, 0),
    "up_left": (-50, 50, 0),
    "up_right": (50, 50, 0),
    "down_left": (-50, -50, 0),
    "down_right": (50, -50, 0),
    "zoom_in": (0, 0, 50),
    "zoom_out": (0, 0, -50),
}
PTZ_SPEED = vol.All(vol.Coerce(int), vol.Range(min=-100, max=100))

ACTION_PTZ_MOVE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional("direction"): vol.In(list(PTZ_DIRECTIONS)),
        vol.Optional("pan"): PTZ_SPEED,
        vol.Optional("tilt"): PTZ_SPEED,
        vol.Optional("zoom"): PTZ_SPEED,
        vol.Optional("duration", default=1000): vol.All(vol.Coerce(int), vol.Range(min=0, max=60000)),
    }
)
ACTION_PTZ_STOP_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_ids})

ACTION_ISAPI_REQUEST_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): str,
        vol.Required("method"): str,
        vol.Required("path"): str,
        vol.Optional("payload"): str,
    }
)


def _ptz_camera(hass: HomeAssistant, entity_id: str):
    """Return the device and channel behind a camera entity of this integration."""
    try:
        camera = get_camera_from_entity_id(hass, entity_id)
    except HomeAssistantError as ex:
        raise HomeAssistantError(f"{entity_id} is not a camera") from ex
    device = getattr(camera, "device", None)
    stream_info = getattr(camera, "stream_info", None)
    if device is None or stream_info is None or camera.platform.platform_name != DOMAIN:
        raise HomeAssistantError(f"{entity_id} is not a {DOMAIN} camera")
    # Stream IDs are channel * 100 + stream type, for example 101 for channel 1.
    return device, stream_info.id // 100


async def _ptz_move(hass: HomeAssistant, entity_ids: list[str], pan: int, tilt: int, zoom: int, duration: int):
    """Move the cameras, then stop them after duration milliseconds (0 keeps them moving)."""
    for entity_id in entity_ids:
        device, channel_id = _ptz_camera(hass, entity_id)
        try:
            await device.ptz_continuous(channel_id, pan, tilt, zoom)
        except (HTTPStatusError, ISAPIForbiddenError, ISAPIUnauthorizedError) as ex:
            raise HomeAssistantError(f"PTZ control failed for {entity_id}: {ex}") from ex
        if duration and (pan or tilt or zoom):

            async def stop_later(device=device, channel_id=channel_id, entity_id=entity_id):
                await asyncio.sleep(duration / 1000)
                try:
                    await device.ptz_continuous(channel_id, 0, 0, 0)
                except (HTTPStatusError, ISAPIForbiddenError, ISAPIUnauthorizedError) as ex:
                    _LOGGER.warning("Stopping PTZ movement of %s failed: %s", entity_id, ex)

            device.entry.async_create_background_task(hass, stop_later(), f"{DOMAIN} PTZ stop {entity_id}")


def setup_services(hass: HomeAssistant) -> None:
    """Set up the services for the Hikvision component."""

    async def handle_reboot(call: ServiceCall):
        """Handle the reboot action call."""
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        entry = hass.config_entries.async_get_entry(entry_id)
        device = entry.runtime_data
        try:
            await device.reboot()
        except (HTTPStatusError, ISAPIForbiddenError, ISAPIUnauthorizedError) as ex:
            raise HomeAssistantError(ex.response.content) from ex

    async def handle_isapi_request(call: ServiceCall) -> ServiceResponse:
        """Handle the custom ISAPI request action call."""
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        entry = hass.config_entries.async_get_entry(entry_id)
        device = entry.runtime_data
        method = call.data.get("method", "POST")
        path = call.data["path"].strip("/")
        payload = call.data.get("payload")
        try:
            response = await device.request(method, path, present="xml", data=payload)
        except (HTTPStatusError, ISAPIForbiddenError, ISAPIUnauthorizedError) as ex:
            if isinstance(ex.response.content, bytes):
                response = ex.response.content.decode("utf-8")
            else:
                response = ex.response.content
        return {"data": response.replace("\r", "")}

    async def handle_ptz_move(call: ServiceCall):
        """Handle the PTZ move action call."""
        if any(key in call.data for key in ("pan", "tilt", "zoom")):
            pan, tilt, zoom = (call.data.get(key, 0) for key in ("pan", "tilt", "zoom"))
        elif direction := call.data.get("direction"):
            pan, tilt, zoom = PTZ_DIRECTIONS[direction]
        else:
            raise HomeAssistantError("Set a direction, or pan, tilt or zoom")
        await _ptz_move(hass, call.data[ATTR_ENTITY_ID], pan, tilt, zoom, call.data["duration"])

    async def handle_ptz_stop(call: ServiceCall):
        """Handle the PTZ stop action call."""
        await _ptz_move(hass, call.data[ATTR_ENTITY_ID], 0, 0, 0, 0)

    hass.services.async_register(
        DOMAIN,
        ACTION_REBOOT,
        handle_reboot,
    )
    hass.services.async_register(DOMAIN, ACTION_PTZ_MOVE, handle_ptz_move, schema=ACTION_PTZ_MOVE_SCHEMA)
    hass.services.async_register(DOMAIN, ACTION_PTZ_STOP, handle_ptz_stop, schema=ACTION_PTZ_STOP_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        ACTION_ISAPI_REQUEST,
        handle_isapi_request,
        schema=ACTION_ISAPI_REQUEST_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
