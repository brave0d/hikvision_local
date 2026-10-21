"ISAPI client for Home Assistant integration."

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util import slugify
import httpx

from .const import (
    ALARM_SERVER_PATH,
    AUDIO_ALARM_COORDINATOR,
    CONF_ALARM_SERVER_HOST,
    CONF_SET_ALARM_SERVER,
    DOMAIN,
    EVENTS,
    EVENTS_COORDINATOR,
    LIGHTS_COORDINATOR,
    RTSP_PORT_FORCED,
    SECONDARY_COORDINATOR,
)
from .coordinator import AudioAlarmCoordinator, EventsCoordinator, SecondaryCoordinator, SupplementLightCoordinator
from .isapi import (
    AnalogCamera,
    EventInfo,
    IPCamera,
    ISAPIClient,
    ISAPIForbiddenError,
    ISAPIUnauthorizedError,
)
from .isapi.const import EVENT_IO

_LOGGER = logging.getLogger(__name__)


class HikvisionDevice(ISAPIClient):
    """Hikvision device for Home Assistant integration."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize device."""

        config = entry.data if entry else data
        self.entry = entry
        self.hass = hass
        self.auth_token_expired = False
        self.control_alarm_server_host = config[CONF_SET_ALARM_SERVER]
        self.alarm_server_host = config[CONF_ALARM_SERVER_HOST]

        # init ISAPI client
        host = config[CONF_HOST]
        username = config[CONF_USERNAME]
        password = config[CONF_PASSWORD]
        verify_ssl = config.get(CONF_VERIFY_SSL, True)
        rtsp_port_forced = config.get(RTSP_PORT_FORCED, None)
        session = get_async_client(hass, verify_ssl)
        super().__init__(host, username, password, verify_ssl, rtsp_port_forced, session)

        self.events_info: list[EventInfo] = []

    async def init_coordinators(self):
        """Initialize coordinators."""

        # init events supported by integration
        self.events_info = self.get_device_event_capabilities()
        for camera in self.cameras:
            camera.events_info = self.get_device_event_capabilities(camera.id)

        # create coordinators
        self.coordinators = {}
        self.coordinators[EVENTS_COORDINATOR] = EventsCoordinator(self.hass, self)
        if (
            self.capabilities.support_holiday_mode
            or self.capabilities.support_alarm_server
            or self.capabilities.storage
        ):
            self.coordinators[SECONDARY_COORDINATOR] = SecondaryCoordinator(self.hass, self)

        if any(camera.supplement_light for camera in self.cameras):
            self.coordinators[LIGHTS_COORDINATOR] = SupplementLightCoordinator(self.hass, self)

        if self.audio_alarm:
            self.coordinators[AUDIO_ALARM_COORDINATOR] = AudioAlarmCoordinator(self.hass, self)

        if self.control_alarm_server_host and self.capabilities.support_alarm_server:
            await self.set_alarm_server(self.alarm_server_host, ALARM_SERVER_PATH)

        # first data fetch
        for coordinator in self.coordinators.values():
            await coordinator.async_config_entry_first_refresh()

    def hass_device_info(self, camera_id: int = 0) -> DeviceInfo:
        """Return Home Assistant entity device information."""
        if camera_id == 0:
            return DeviceInfo(
                manufacturer=self.device_info.manufacturer,
                identifiers={(DOMAIN, self.device_info.serial_no)},
                connections={(dr.CONNECTION_NETWORK_MAC, self.device_info.mac_address)},
                model=self.device_info.model,
                name=self.device_info.name,
                sw_version=self.device_info.firmware,
            )
        else:
            camera_info = self.get_camera_by_id(camera_id)
            is_ip_camera = isinstance(camera_info, IPCamera)

            device_info = DeviceInfo(
                manufacturer=self.device_info.manufacturer,
                identifiers={(DOMAIN, camera_info.serial_no)},
                model=camera_info.model,
                name=camera_info.name,
                sw_version=camera_info.firmware if is_ip_camera else "Unknown",
            )
            if self.device_info.is_nvr:
                device_info.update(self._via_nvr_device_info())
            return device_info

    def _via_nvr_device_info(self) -> DeviceInfo:
        """Return device info linking a camera to its parent NVR device."""
        nvr_identifier = (DOMAIN, self.device_info.serial_no)
        device_registry = dr.async_get(self.hass)
        nvr_device = device_registry.async_get_device_by_identifier(nvr_identifier, self.entry.entry_id)
        if nvr_device is None:
            _LOGGER.warning("NVR device %s is not registered yet, camera is added without a parent link", self.host)
            return DeviceInfo()
        return DeviceInfo(via_device_id=nvr_device.id)

    def get_device_event_capabilities(
        self,
        camera_id: int | None = None,
    ) -> list[EventInfo]:
        """Get events info handled by integration (camera id:  NVR = None, camera > 0)."""
        events = []

        if camera_id is None:  # NVR
            integration_supported_events = [
                s for s in self.supported_events if (s.id in EVENTS and EVENTS[s.id].get("type") == EVENT_IO)
            ]
        else:  # Camera
            integration_supported_events = [
                s for s in self.supported_events if (s.channel_id == int(camera_id) and s.id in EVENTS)
            ]

        for event in integration_supported_events:
            # Build unique_id
            device_id_param = f"_{camera_id}" if camera_id else ""
            io_port_id_param = f"_{event.io_port_id}" if event.io_port_id != 0 else ""
            unique_id = f"{slugify(self.device_info.serial_no.lower())}{device_id_param}{io_port_id_param}_{event.id}"

            if EVENTS.get(event.id):
                event.unique_id = unique_id
                event.disabled = "center" not in event.notifications  # Disable if not set Notify Surveillance Center
                events.append(event)
        return events

    def handle_exception(self, ex: Exception, details: str = ""):
        """Handle common exceptions."""

        error = "Unexpected exception"

        if isinstance(ex, ISAPIUnauthorizedError):
            if not self.auth_token_expired:
                # after device reboot, authorization token may have expired
                self.auth_token_expired = True
                self._auth_method = None
                self._session = get_async_client(self.hass, self.verify_ssl)
                _LOGGER.warning("Unauthorized access to %s, started checking if token expired", self.host)
                return
            self.auth_token_expired = False
            self.entry.async_start_reauth(self.hass)
            error = "Unauthorized access"
        elif isinstance(ex, ISAPIForbiddenError):
            error = "Forbidden access"
        elif isinstance(ex, (httpx.TimeoutException, httpx.ConnectTimeout)):
            error = "Timeout"
        elif isinstance(ex, (httpx.ConnectError, httpx.NetworkError)):
            error = "Connection error"

        _LOGGER.warning("%s | %s | %s | %s", error, self.host, details, ex)

    def build_camera_light_unique_id(self, camera: AnalogCamera) -> str:
        """Build unique_id for supplement light entity."""

        return slugify(f"{camera.serial_no.lower()}_{camera.id}_supplement_light")

    def build_audio_alarm_unique_id(self, key: str) -> str:
        """Build unique_id for an audio alarm entity."""

        return slugify(f"{self.device_info.serial_no.lower()}_audio_alarm_{key}")
