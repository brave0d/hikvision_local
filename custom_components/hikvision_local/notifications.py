"""Events listener."""

from __future__ import annotations

from http import HTTPStatus
import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import CONTENT_TYPE_TEXT_PLAIN, STATE_OFF, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_registry import async_get
from homeassistant.util import slugify
from requests_toolbelt.multipart import MultipartDecoder

from .const import ALARM_SERVER_PATH, DOMAIN, HIKVISION_EVENT
from .hikvision_device import HikvisionDevice
from .isapi import AlertInfo, IPCamera, ISAPIClient, ISAPIUnsupportedEventError
from .isapi.const import EVENT_IO

_LOGGER = logging.getLogger(__name__)

CONTENT_TYPE = "Content-Type"
CONTENT_TYPE_XML = (
    "application/xml",
    'application/xml; charset="UTF-8"',
    "text/xml",
)
CONTENT_TYPE_TEXT_HTML = "text/html"
CONTENT_TYPE_IMAGE = "image/jpeg"


def _clean_id(value: str | None) -> str:
    """Return a MAC address or serial number in lower case, without separators."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower()) if value else ""


class EventNotificationsView(HomeAssistantView):
    """Event notifications listener."""

    def __init__(self, hass: HomeAssistant):
        """Initialize."""
        self.requires_auth = False
        self.url = ALARM_SERVER_PATH
        self.name = DOMAIN
        self.device: HikvisionDevice
        self.hass = hass
        self.unsupported_events: set[str] = set()

    async def post(self, request: web.Request):
        """Accept the POST request from NVR or IP Camera."""

        try:
            _LOGGER.debug("--- Incoming event notification ---")
            _LOGGER.debug("Source: %s", request.remote)
            xml = await self.parse_event_request(request)
            if not xml:
                _LOGGER.debug("Ignoring event notification without content")
                return web.Response(status=HTTPStatus.OK, content_type=CONTENT_TYPE_TEXT_PLAIN)
            _LOGGER.debug("alert info: %s", xml)
            alert = ISAPIClient.parse_event_notification(xml)
            self.device = self.get_isapi_device(request.remote, alert)
            self.update_alert_channel(alert)
            self.trigger_sensor(alert)
        except ISAPIUnsupportedEventError as ex:
            if ex.event_id not in self.unsupported_events:
                self.unsupported_events.add(ex.event_id)
                _LOGGER.debug("Ignoring %s events, the integration has no entities for them", ex.event_id)
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Cannot process incoming event %s", ex)

        response = web.Response(status=HTTPStatus.OK, content_type=CONTENT_TYPE_TEXT_PLAIN)
        return response

    def get_isapi_device(self, device_ip, alert: AlertInfo) -> HikvisionDevice:
        """Get integration instance for device sending alert."""
        devices: list[HikvisionDevice] = [
            entry.runtime_data
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if not entry.disabled_by and getattr(entry, "runtime_data", None) is not None
        ]
        if len(devices) == 1:
            return devices[0]

        # Search device by MAC address, ignoring case and separators
        if alert_mac := _clean_id(alert.mac):
            for device in devices:
                if _clean_id(device.device_info.mac_address) == alert_mac:
                    return device

        # Search device by serial number
        if alert_serial := _clean_id(alert.device_serial_no):
            for device in devices:
                if _clean_id(device.device_info.serial_no) == alert_serial:
                    return device

        # Search device by the request's address, or the address in the alert (behind NAT)
        addresses = {device_ip, alert.ip_address} - {None}
        for device in devices:
            if self.get_ip(urlparse(device.host).hostname) in addresses:
                return device

        instance_identifiers = [f"{device.device_info.mac_address} {device.host}" for device in devices]
        raise ValueError(f"Cannot find ISAPI instance for device {device_ip} in {instance_identifiers}")

    def get_ip(self, ip_string: str) -> str:
        """Return an IP if either hostname or IP is provided."""

        try:
            ipaddress.ip_address(ip_string)
            return ip_string
        except ValueError:
            resolved_hostname = socket.gethostbyname(ip_string)
            _LOGGER.debug("Resolve host %s resolves to IP %s", ip_string, resolved_hostname)

            return resolved_hostname

    async def parse_event_request(self, request: web.Request) -> str:
        """Extract XML content from multipart request or from simple request."""

        data = await request.read()
        if not data.strip():
            return ""

        content_type_header = request.headers.get(CONTENT_TYPE).strip()

        _LOGGER.debug("request headers: %s", request.headers)
        xml = None
        if content_type_header in CONTENT_TYPE_XML:
            xml = data.decode("utf-8")
        else:
            # "multipart/form-data; boundary=boundary"
            decoder = MultipartDecoder(data, content_type_header)
            for part in decoder.parts:
                headers = {}
                for key, value in part.headers.items():
                    assert isinstance(key, bytes)
                    headers[key.decode("ascii")] = value.decode("ascii")
                _LOGGER.debug("part headers: %s", headers)
                if headers.get(CONTENT_TYPE) in CONTENT_TYPE_XML:
                    xml = part.text
                if headers.get(CONTENT_TYPE) == CONTENT_TYPE_IMAGE:
                    _LOGGER.debug("image found")
                    # Use camera.snapshot service instead
                    # from datetime import datetime
                    # import aiofiles
                    # now = datetime.now()
                    # filename = f"/media/{DOMAIN}/snapshots/{now.strftime('%Y-%m-%d_%H-%M-%S_%f')}.jpg"
                    # async with aiofiles.open(filename, "wb") as image_file:
                    #     await image_file.write(part.content)
                    #     await image_file.flush()

        if not xml:
            raise ValueError(f"Unexpected event Content-Type {content_type_header}")
        return xml

    def update_alert_channel(self, alert: AlertInfo) -> AlertInfo:
        """Fix channel id for NVR/DVR alert."""

        if alert.channel_id > 32:
            # channel id above 32 is an IP camera
            # On DVRs that support analog cameras 33 may not be
            # camera 1 but camera 5 for example
            try:
                alert.channel_id = [
                    camera.id
                    for camera in self.device.cameras
                    if isinstance(camera, IPCamera) and camera.input_port == alert.channel_id - 32
                ][0]
            except IndexError:
                alert.channel_id = alert.channel_id - 32
        elif (
            not alert.channel_id
            and alert.event_id != EVENT_IO
            and not self.device.device_info.is_nvr
            and self.device.cameras
            and not any(event.id == alert.event_id for event in self.device.events_info)
        ):
            # Some cameras send video event alerts without a channel ID.
            alert.channel_id = self.device.cameras[0].id

    def trigger_sensor(self, alert: AlertInfo) -> None:
        """Determine entity and set binary sensor state."""

        _LOGGER.debug("Alert: %s", alert)

        if alert.channel_id and alert.event_id != EVENT_IO and not self.device.get_camera_by_id(alert.channel_id):
            # For example a video loss alert from an analog input that is disabled on the DVR.
            _LOGGER.debug("Ignoring %s alert for channel %s without a camera", alert.event_id, alert.channel_id)
            return

        serial_no = self.device.device_info.serial_no.lower()

        device_id_param = f"_{alert.channel_id}" if alert.channel_id != 0 and alert.event_id != EVENT_IO else ""
        io_port_id_param = f"_{alert.io_port_id}" if alert.io_port_id != 0 else ""
        unique_id = f"binary_sensor.{slugify(serial_no)}{device_id_param}{io_port_id_param}_{alert.event_id}"

        _LOGGER.debug("UNIQUE_ID: %s", unique_id)

        entity_registry = async_get(self.hass)
        entity_id = entity_registry.async_get_entity_id(Platform.BINARY_SENSOR, DOMAIN, unique_id)
        if entity_id:
            entity = self.hass.states.get(entity_id)
            if entity:
                attributes = dict(entity.attributes)
                attributes.pop("target_type", None)
                if alert.event_state == "inactive":
                    # The event has ended, or the device sends a heartbeat (videoloss).
                    self.hass.states.async_set(entity_id, STATE_OFF, attributes)
                    return
                if alert.target_type:
                    attributes["target_type"] = alert.target_type
                self.hass.states.async_set(entity_id, STATE_ON, attributes)
                self.fire_hass_event(alert)
            return
        raise ValueError(f"Entity not found {entity_id}")

    def fire_hass_event(self, alert: AlertInfo):
        """Fire HASS event."""
        camera_name = ""
        if camera := self.device.get_camera_by_id(alert.channel_id):
            camera_name = camera.name

        message = {
            "channel_id": alert.channel_id,
            "io_port_id": alert.io_port_id,
            "camera_name": camera_name,
            "event_id": alert.event_id,
        }
        if alert.detection_target:
            message["detection_target"] = alert.detection_target
            message["region_id"] = alert.region_id
        if alert.target_type:
            message["target_type"] = alert.target_type

        self.hass.bus.fire(
            HIKVISION_EVENT,
            message,
        )
