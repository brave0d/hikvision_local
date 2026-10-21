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
    ACTION_PTZ,
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
# Same fields as the onvif.ptz action, which Advanced Camera Card and other cards call.
ACTION_PTZ_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional("pan"): vol.In(["LEFT", "RIGHT"]),
        vol.Optional("tilt"): vol.In(["UP", "DOWN"]),
        vol.Optional("zoom"): vol.In(["ZOOM_IN", "ZOOM_OUT"]),
        vol.Optional("distance"): vol.Coerce(float),
        vol.Optional("speed", default=0.5): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Optional("continuous_duration", default=0.5): vol.All(vol.Coerce(float), vol.Range(min=0, max=60)),
        vol.Optional("move_mode", default="ContinuousMove"): vol.In(["ContinuousMove", "Stop"]),
    }
)

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


# Pending automatic stops per (config entry, channel), so a newer command cancels an older stop.
_PTZ_STOPS: dict[tuple[str, int], asyncio.Task] = {}


async def _ptz_move(hass: HomeAssistant, entity_ids: list[str], pan: int, tilt: int, zoom: int, duration: int):
    """Move the cameras, then stop them after duration milliseconds (0 keeps them moving)."""
    for entity_id in entity_ids:
        device, channel_id = _ptz_camera(hass, entity_id)
        key = (device.entry.entry_id, channel_id)
        if (pending := _PTZ_STOPS.pop(key, None)) is not None:
            pending.cancel()
        try:
            await device.ptz_continuous(channel_id, pan, tilt, zoom)
        except (HTTPStatusError, ISAPIForbiddenError, ISAPIUnauthorizedError) as ex:
            raise HomeAssistantError(f"PTZ control failed for {entity_id}: {ex}") from ex
        if duration and (pan or tilt or zoom):

            async def stop_later(device=device, channel_id=channel_id, entity_id=entity_id, key=key):
                try:
                    await asyncio.sleep(duration / 1000)
                    await device.ptz_continuous(channel_id, 0, 0, 0)
                except (HTTPStatusError, ISAPIForbiddenError, ISAPIUnauthorizedError) as ex:
                    _LOGGER.warning("Stopping PTZ movement of %s failed: %s", entity_id, ex)
                finally:
                    if _PTZ_STOPS.get(key) is asyncio.current_task():
                        del _PTZ_STOPS[key]

            _PTZ_STOPS[key] = device.entry.async_create_background_task(
                hass, stop_later(), f"{DOMAIN} PTZ stop {entity_id}"
            )


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

    async def handle_ptz(call: ServiceCall):
        """Handle the ONVIF-style PTZ action call. distance is accepted and ignored."""
        entity_ids = call.data[ATTR_ENTITY_ID]
        if call.data["move_mode"] == "Stop":
            await _ptz_move(hass, entity_ids, 0, 0, 0, 0)
            return
        speed = round(call.data["speed"] * 100)
        pan = {"LEFT": -speed, "RIGHT": speed}.get(call.data.get("pan"), 0)
        tilt = {"UP": speed, "DOWN": -speed}.get(call.data.get("tilt"), 0)
        zoom = {"ZOOM_IN": speed, "ZOOM_OUT": -speed}.get(call.data.get("zoom"), 0)
        await _ptz_move(hass, entity_ids, pan, tilt, zoom, int(call.data["continuous_duration"] * 1000))

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
    hass.services.async_register(DOMAIN, ACTION_PTZ, handle_ptz, schema=ACTION_PTZ_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        ACTION_ISAPI_REQUEST,
        handle_isapi_request,
        schema=ACTION_ISAPI_REQUEST_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
