"""Hikvision ISAPI client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
import datetime
from http import HTTPStatus
import ipaddress
import json
import logging
import re
from typing import Any
from urllib.parse import quote, urljoin, urlparse
import uuid

import httpx
from httpx import HTTPStatusError
import xmltodict

from .const import (
    CONNECTION_TYPE_DIRECT,
    CONNECTION_TYPE_PROXIED,
    EVENT_BASIC,
    EVENT_IO,
    EVENT_MOTION,
    EVENT_PIR,
    EVENTS,
    EVENTS_ALTERNATE_ID,
    GET,
    MOTION_TARGETS,
    MUTEX_ALTERNATE_ID,
    POST,
    PUT,
    STREAM_TYPE,
)
from .models import (
    AlarmServer,
    AlertInfo,
    AnalogCamera,
    AudioAlarm,
    AudioAlarmCapabilities,
    AudioAlarmSound,
    AudioAlarmState,
    CameraStreamInfo,
    CapabilitiesInfo,
    EventInfo,
    IPCamera,
    ISAPIDeviceInfo,
    LoggedEvent,
    MutexIssue,
    ProtocolsInfo,
    RecordingSegment,
    StorageInfo,
    SupplementLight,
    SupplementLightCapabilities,
    SupplementLightState,
)
from .utils import bool_to_str, deep_get, format_isapi_time, parse_isapi_response, parse_isapi_time, str_to_bool


# Helper to sanitize channel IDs (e.g. converting "I-1" to 1)
def clean_int(value, default: int = 0) -> int:
    """Sanitize and convert value to int, handling alphanumeric strings.

    Returns default for values without digits, such as None or "".
    """
    try:
        return int(value)
    except ValueError, TypeError:
        # Extract digits from string (e.g. "I-1" -> "1")
        digits = "".join(filter(str.isdigit, str(value)))
        return int(digits) if digits else default


def normalize_event_id(event_type: str) -> str:
    """Return the event ID for an event type, without a channel suffix (vmd-1 -> motiondetection)."""
    event_id = event_type.lower().strip()
    if event_id not in EVENTS and event_id not in EVENTS_ALTERNATE_ID:
        event_id = re.sub(r"-\d+$", "", event_id)
    return EVENTS_ALTERNATE_ID.get(event_id, event_id)


def event_channel_suffix(*values: str | None) -> int:
    """Return the channel from the first value with a suffix such as facedetection-1, or 0."""
    for value in values:
        if value and (match := re.search(r"-(\d+)$", str(value))):
            return int(match.group(1))
    return 0


def split_targets(target_type: str | None) -> list[str]:
    """Return the targets in a targetType value such as "human,vehicle"."""
    return [target.strip().lower() for target in (target_type or "").split(",") if target.strip()]


def get_trigger_notifications(event_trigger: dict) -> list[dict]:
    """Return the notification method entries of an Event/triggers entry."""
    notifications = deep_get(event_trigger.get("EventTriggerNotificationList") or {}, "EventTriggerNotification", [])
    if isinstance(notifications, dict):
        notifications = [notifications]
    return [notify for notify in notifications if isinstance(notify, dict)]


def notifies_center(event_trigger: dict) -> bool:
    """Return True if an Event/triggers entry has the 'Notify Surveillance Center' action."""
    return any(notify.get("notificationMethod") == "center" for notify in get_trigger_notifications(event_trigger))


def get_event_triggers_list(event_triggers: dict) -> list[dict]:
    """Return the entries of an Event/triggers response."""
    if event_notification := event_triggers.get("EventNotification"):
        event_triggers = event_notification
    triggers = deep_get(event_triggers, "EventTriggerList.EventTrigger", [])
    if isinstance(triggers, dict):
        triggers = [triggers]
    return [trigger for trigger in triggers if isinstance(trigger, dict)]


Node = dict[str, Any]

SEARCH_PAGE_SIZE = 50
LOG_PAGE_SIZE = 100

_LOGGER = logging.getLogger(__name__)


class ISAPIClient:
    """Hikvision ISAPI client."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool = True,
        rtsp_port_forced: int = None,
        session: httpx.AsyncClient = None,
    ) -> None:
        """Initialize."""

        self.host = host
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = 20
        self.isapi_prefix = "ISAPI"
        self._session = session
        self._auth_method: httpx._auth.Auth = None

        self.rtsp_port_forced = rtsp_port_forced

        self.device_info = ISAPIDeviceInfo()
        self.capabilities = CapabilitiesInfo()
        self.cameras: list[IPCamera | AnalogCamera] = []
        self.supported_events: list[EventInfo] = []
        self.storage: list[StorageInfo] = []
        self.audio_alarm: AudioAlarm | None = None
        self.protocols = ProtocolsInfo()
        self.pending_initialization = False

    @staticmethod
    def _node_text(node: Any, default: str | None = None) -> str | None:
        if node is None:
            return default
        if isinstance(node, dict):
            return node.get("#text", default)
        return node

    @staticmethod
    def _node_opt_list(node: Any) -> list[str]:
        if node is None:
            return []
        value = (node.get("@opt") or node.get("#text")) if isinstance(node, dict) else node
        if not value:
            return []
        return [opt for opt in str(value).split(",") if opt]

    @staticmethod
    def _parse_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except TypeError, ValueError:
            return default

    async def get_device_info(self):
        """Get device info."""
        hw_info = (await self.request(GET, "System/deviceInfo")).get("DeviceInfo", {})
        self.device_info = ISAPIDeviceInfo(
            name=hw_info.get("deviceName"),
            manufacturer=str(hw_info.get("manufacturer", "Hikvision")).title(),
            model=hw_info.get("model"),
            serial_no=hw_info.get("serialNumber"),
            firmware=hw_info.get("firmwareVersion"),
            mac_address=hw_info.get("macAddress"),
            ip_address=urlparse(self.host).hostname,
            device_type=hw_info.get("deviceType"),
        )

    async def get_hardware_info(self):
        """Get device all data."""
        await self.get_device_info()
        capabilities = (await self.request(GET, "System/capabilities")).get("DeviceCap", {})

        self.capabilities.analog_cameras_inputs = int(deep_get(capabilities, "SysCap.VideoCap.videoInputPortNums", 0))
        self.capabilities.digital_cameras_inputs = int(deep_get(capabilities, "RacmCap.inputProxyNums", 0))
        if not self.capabilities.analog_cameras_inputs and self.capabilities.digital_cameras_inputs:
            self.capabilities.analog_cameras_inputs = await self.get_analog_inputs_count()
        self.capabilities.support_holiday_mode = str_to_bool(deep_get(capabilities, "SysCap.isSupportHolidy", "false"))
        self.capabilities.support_channel_zero = str_to_bool(
            deep_get(capabilities, "RacmCap.isSupportZeroChan", "false")
        )
        self.capabilities.support_event_mutex_checking = str_to_bool(
            capabilities.get("isSupportGetmutexFuncErrMsg", "false")
        )
        self.capabilities.input_ports = int(deep_get(capabilities, "SysCap.IOCap.IOInputPortNums", 0))
        self.capabilities.output_ports = int(deep_get(capabilities, "SysCap.IOCap.IOOutputPortNums", 0))
        self.capabilities.support_alarm_server = bool(await self.get_alarm_server())

        # Set if NVR based on whether more than 1 supported IP or analog cameras
        # Single IP camera will show 0 supported devices in total
        if self.capabilities.analog_cameras_inputs + self.capabilities.digital_cameras_inputs > 1:
            self.device_info.is_nvr = True

        await self.get_cameras()

        if any(camera.connection_type == CONNECTION_TYPE_PROXIED for camera in self.cameras):
            with suppress(Exception):
                self.capabilities.support_channel_status = bool(await self.get_cameras_online_status())

        # NVRs expose audio alarm per channel under a different URL
        if not self.device_info.is_nvr:
            self.audio_alarm = await self.get_audio_alarm()

        self.supported_events = await self.get_supported_events(capabilities)
        # One request per motion event; a few at a time, so large NVRs set up quicker without being flooded.
        motion_events = [event for event in self.supported_events if event.id == EVENT_MOTION and event.url]
        semaphore = asyncio.Semaphore(4)

        async def read_targets(event: EventInfo) -> list[str]:
            async with semaphore:
                return await self.get_motion_targets(event.url)

        targets = await asyncio.gather(*(read_targets(event) for event in motion_events), return_exceptions=True)
        for event, event_targets in zip(motion_events, targets, strict=True):
            if isinstance(event_targets, list):
                event.targets = event_targets

        await self.get_protocols()

        with suppress(Exception):
            self.storage = await self.get_storage_devices()

    async def get_analog_inputs_count(self) -> int:
        """Count analog inputs of a recorder from its video input channel list.

        Some hybrid DVR firmwares (e.g. HiWatch DVR-108P-G/N V3.4.892) report
        videoInputPortNums=0 in capabilities while having analog inputs.
        IP-only NVRs answer this endpoint with 403, so they keep 0.
        """
        try:
            response = await self.request(GET, "System/Video/inputs/channels")
        except Exception:  # noqa: BLE001
            # A count we can't read is no worse than the 0 the capabilities reported.
            return 0
        channels = deep_get(response, "VideoInputChannelList.VideoInputChannel", [])
        if isinstance(channels, dict):
            channels = [channels]
        # Analog inputs have a physical input port; anything else in the list isn't one.
        return len([channel for channel in channels if channel.get("inputPort")])

    async def get_cameras(self):
        """Get camera objects for all connected cameras."""

        if not self.device_info.is_nvr:
            # Fetch the list of streaming channels (cameras), can be multiple for thermal cameras for example
            streaming_channels = await self.request(GET, "Streaming/channels")
            streaming_channel_list = deep_get(streaming_channels, "StreamingChannelList.StreamingChannel", [])

            channel_ids = set()
            for streaming_channel in streaming_channel_list:
                channel_id = int(deep_get(streaming_channel, "Video.videoInputChannelID", 1))
                channel_ids.add(channel_id)

            self.capabilities.is_multi_channel = len(channel_ids) > 1
            for channel_id in sorted(channel_ids):
                # Determine camera name
                if len(channel_ids) > 1:
                    camera_name = f"{self.device_info.name} - Channel {channel_id}"
                else:
                    camera_name = self.device_info.name

                camera = IPCamera(
                    id=channel_id,
                    name=camera_name,
                    model=self.device_info.model,
                    serial_no=self.device_info.serial_no,
                    firmware=self.device_info.firmware,
                    input_port=channel_id,
                    connection_type=CONNECTION_TYPE_DIRECT,
                    ip_addr=self.device_info.ip_address,
                    streams=await self.get_camera_streams(channel_id),
                )
                self.cameras.append(camera)

                supplement_light = await self.get_camera_supplement_light(channel_id)
                if supplement_light:
                    camera.supplement_light = supplement_light
        else:
            # Get analog and digital cameras attached to NVR
            if self.capabilities.digital_cameras_inputs > 0:
                digital_cameras = deep_get(
                    (await self.request(GET, "ContentMgmt/InputProxy/channels")),
                    "InputProxyChannelList.InputProxyChannel",
                    [],
                )

                for digital_camera in digital_cameras:
                    camera_id = digital_camera.get("id")
                    source = digital_camera.get("sourceInputPortDescriptor")
                    if not source:
                        continue

                    serial_no = source.get("serialNumber")
                    if not serial_no or self.get_camera_by_serial_no(serial_no):
                        # serial no is not always recognized correcly by NVR
                        serial_no = f"{self.device_info.serial_no}_{source.get('proxyProtocol')}_{camera_id}"

                    camera = IPCamera(
                        id=int(camera_id),
                        name=digital_camera.get("name"),
                        model=source.get("model", "Unknown"),
                        serial_no=serial_no,
                        firmware=source.get("firmwareVersion"),
                        input_port=int(source.get("srcInputPort")),
                        connection_type=CONNECTION_TYPE_PROXIED,
                        ip_addr=source.get("ipAddress"),
                        ip_port=source.get("managePortNo"),
                        streams=await self.get_camera_streams(
                            camera_id, default_streams=source.get("proxyProtocol") != "HIKVISION"
                        ),
                    )
                    supplement_light = await self.get_camera_supplement_light(int(camera_id))
                    if supplement_light:
                        camera.supplement_light = supplement_light
                    self.cameras.append(camera)

            # Get analog cameras
            if self.capabilities.analog_cameras_inputs > 0:
                analog_cameras = deep_get(
                    (await self.request(GET, "System/Video/inputs/channels")),
                    "VideoInputChannelList.VideoInputChannel",
                    [],
                )

                for analog_camera in analog_cameras:
                    if not str_to_bool(analog_camera.get("videoInputEnabled", "true")):
                        # Disabled inputs have no camera; "NO VIDEO" on an enabled input can be an offline one.
                        continue
                    camera_id = clean_int(analog_camera.get("id"))
                    device_serial_no = f"{self.device_info.serial_no}-VI{camera_id}"
                    input_port = clean_int(analog_camera.get("inputPort"))

                    camera = AnalogCamera(
                        id=camera_id,
                        name=analog_camera.get("name"),
                        model=analog_camera.get("resDesc"),
                        serial_no=device_serial_no,
                        input_port=input_port,
                        connection_type=CONNECTION_TYPE_DIRECT,
                        streams=await self.get_camera_streams(camera_id),
                    )
                    supplement_light = await self.get_camera_supplement_light(int(camera_id))
                    if supplement_light:
                        camera.supplement_light = supplement_light
                    self.cameras.append(camera)

    async def get_protocols(self):
        """Get protocols and ports."""
        protocols = deep_get(
            await self.request(GET, "Security/adminAccesses"),
            "AdminAccessProtocolList.AdminAccessProtocol",
            [],
        )

        for item in protocols:
            if item.get("protocol") == "RTSP" and item.get("portNo"):
                if self.rtsp_port_forced:
                    self.protocols.rtsp_port = str(self.rtsp_port_forced)
                else:
                    self.protocols.rtsp_port = item.get("portNo")
                break

    async def get_supported_events(self, system_capabilities: dict) -> list[EventInfo]:
        """Get list of all supported events available."""

        def create_event_info(event_trigger: dict, trigger_id: str | None = None):
            event_type = event_trigger.get("eventType")
            if not event_type:
                return None
            event_id = normalize_event_id(event_type)

            if event_id == EVENT_PIR:
                is_supported = str_to_bool(deep_get(system_capabilities, "WLAlarmCap.isSupportPIR", False))
                if not is_supported:
                    return None

            channel_id = 0
            io_port = 0
            is_proxy = False

            if event_id == EVENT_IO:
                io_port = clean_int(event_trigger.get("inputIOPortID", 0))
                if not io_port:
                    io_port = clean_int(event_trigger.get("dynInputIOPortID", 0))
                    is_proxy = io_port > 0
            else:
                channel_id = clean_int(event_trigger.get("videoInputChannelID", 0))
                if not channel_id:
                    channel_id = clean_int(event_trigger.get("dynVideoInputChannelID", 0))
                    is_proxy = channel_id > 0
                if not channel_id:
                    channel_id = event_channel_suffix(event_type, event_trigger.get("id"))

            url = self.get_event_url(event_id, channel_id, io_port, is_proxy)

            return EventInfo(
                channel_id=channel_id,
                io_port_id=io_port,
                id=event_id,
                url=url,
                is_proxy=is_proxy,
                notifications=[notify.get("notificationMethod") for notify in get_trigger_notifications(event_trigger)],
                trigger_id=event_trigger.get("id") or trigger_id,
            )

        events = []

        # Get events from Event/triggers
        event_triggers = await self.request(GET, "Event/triggers")

        for event_trigger in get_event_triggers_list(event_triggers):
            if event := create_event_info(event_trigger):
                events.append(event)

        # some devices do not have scenechangedetection in Event/triggers
        if not [e for e in events if e.id == "scenechangedetection"]:
            is_supported = str_to_bool(deep_get(system_capabilities, "SmartCap.isSupportSceneChangeDetection", False))
            if is_supported:
                event_trigger = await self.request(GET, "Event/triggers/scenechangedetection-1")
                event_trigger = deep_get(event_trigger, "EventTrigger", {})
                if event := create_event_info(event_trigger, "scenechangedetection-1"):
                    events.append(event)

        # Multi-channel cameras need events fetched per channel. Some devices also answer
        # Event/triggers with an error or nothing at all; fall back to the channel capabilities.
        if self.capabilities.is_multi_channel or not events:
            channels_capabilities = await self.request(GET, "Event/channels/capabilities")
            channel_events = deep_get(channels_capabilities, "ChannelEventCapList.ChannelEventCap", [])
            if isinstance(channel_events, dict):
                channel_events = [channel_events]
            for event_cap in channel_events:
                event_types = deep_get(event_cap, "eventType", {}).get("@opt", "").split(",")
                channel_id = clean_int(event_cap.get("channelID", 1))
                for event_type in event_types:
                    event_id = event_type.lower()
                    if event_id in EVENTS_ALTERNATE_ID:
                        event_id = EVENTS_ALTERNATE_ID[event_id]
                    if event_id not in EVENTS:
                        continue
                    if not [e for e in events if (e.id == event_id and e.channel_id == channel_id)]:
                        trigger_id = f"{event_id}-{channel_id}"
                        event_trigger = await self.request(GET, f"Event/triggers/{trigger_id}")
                        event_trigger = deep_get(event_trigger, "EventTrigger", {})
                        if event := create_event_info(event_trigger, trigger_id):
                            events.append(event)

        return events

    async def get_camera_supplement_light(self, channel_id: int) -> SupplementLight | None:
        """Fetch camera supplement light details if supported."""

        capabilities = await self.get_supplement_light_capabilities(channel_id)
        if not capabilities or not capabilities.supported_modes:
            return None

        state = await self.get_supplement_light_state(channel_id)
        if not state:
            return None

        return SupplementLight(channel_id=channel_id, capabilities=capabilities, state=state)

    async def get_supplement_light_capabilities(self, channel_id: int) -> SupplementLightCapabilities | None:
        """Retrieve supplement light capabilities for a given channel."""

        try:
            response = await self.request(GET, f"Image/channels/{channel_id}/capabilities")
        except HTTPStatusError, httpx.HTTPError, ISAPIForbiddenError, ISAPIUnauthorizedError:
            return None

        supplement = deep_get(response, "ImageChannel.SupplementLight")
        if not supplement:
            return None

        brightness_node = supplement.get("whiteLightBrightness", {}) or {}
        brightness_min = self._parse_int(brightness_node.get("@min"), 0)
        brightness_max = self._parse_int(brightness_node.get("@max"), 100)
        auto_cfg = str_to_bool(str(self._node_text(supplement.get("isAutoModeBrightnessCfg"), "false")))

        return SupplementLightCapabilities(
            supported_modes=self._node_opt_list(supplement.get("supplementLightMode")),
            supported_regulation_modes=self._node_opt_list(supplement.get("mixedLightBrightnessRegulatMode")),
            brightness_min=brightness_min,
            brightness_max=brightness_max,
            auto_mode_brightness_configurable=auto_cfg,
        )

    async def get_supplement_light_state(self, channel_id: int) -> SupplementLightState | None:
        """Get supplement light state for a channel."""

        try:
            response = await self.request(GET, f"Image/channels/{channel_id}/supplementLight")
        except HTTPStatusError, httpx.HTTPError, ISAPIForbiddenError, ISAPIUnauthorizedError:
            return None

        supplement = response.get("SupplementLight") or deep_get(response, "ImageChannel.SupplementLight")
        if not supplement:
            return None

        mode = self._node_text(supplement.get("supplementLightMode"))
        if not mode:
            return None

        brightness_node = supplement.get("whiteLightBrightness")
        brightness = self._parse_int(self._node_text(brightness_node), 0)
        regulation_mode = self._node_text(supplement.get("mixedLightBrightnessRegulatMode"))

        return SupplementLightState(mode=mode, brightness=brightness, regulation_mode=regulation_mode)

    async def set_supplement_light_state(
        self,
        channel_id: int,
        mode: str,
        brightness: int,
        regulation_mode: str | None = None,
    ) -> SupplementLightState:
        """Set supplement light state.

        The current settings are read first and sent back with only these fields changed, so other
        light settings (IR brightness, smart light, schedules) stay as they are.
        """
        url = f"Image/channels/{channel_id}/supplementLight"
        payload = await self.request(GET, url)
        if not isinstance(payload, dict) or not isinstance(payload.get("SupplementLight"), dict):
            raise ValueError(f"Cannot read the supplement light settings of channel {channel_id}")
        settings = payload["SupplementLight"]
        settings["supplementLightMode"] = mode
        settings["whiteLightBrightness"] = str(brightness)
        if regulation_mode:
            settings["mixedLightBrightnessRegulatMode"] = regulation_mode

        xml = xmltodict.unparse(payload)
        await self.request(PUT, url, present="xml", data=xml)
        return SupplementLightState(mode=mode, brightness=brightness, regulation_mode=regulation_mode)

    async def get_audio_alarm(self) -> AudioAlarm | None:
        """Fetch audio alarm details if supported."""

        capabilities = await self.get_audio_alarm_capabilities()
        if not capabilities or not capabilities.sounds:
            return None

        state = await self.get_audio_alarm_state()
        if not state:
            return None

        return AudioAlarm(capabilities=capabilities, state=state)

    async def get_audio_alarm_capabilities(self) -> AudioAlarmCapabilities | None:
        """Retrieve the sounds, volume and alarm times range the device supports."""

        try:
            response = await self.request(
                GET, "Event/triggers/notifications/AudioAlarm/capabilities?format=json", present="json"
            )
        except HTTPStatusError, httpx.HTTPError, ISAPIForbiddenError, ISAPIUnauthorizedError:
            return None

        capabilities = self._parse_json(response).get("AudioAlarmCap")
        if not capabilities:
            return None

        # Newer firmware lists the sounds for the alertAudio class separately
        sounds = [
            AudioAlarmSound(id=int(sound["alertAudioID"]), name=sound["alertAudioDescription"])
            for sound in capabilities.get("AlertAudioTypeListCap", [])
        ] or [
            AudioAlarmSound(id=int(sound["audioID"]), name=sound["audioDescription"])
            for sound in capabilities.get("audioTypeListCap", [])
        ]
        volume = capabilities.get("audioVolume", {})
        alarm_times = capabilities.get("alarmTimes", {})

        return AudioAlarmCapabilities(
            sounds=sounds,
            volume_min=self._parse_int(volume.get("@min"), 1),
            volume_max=self._parse_int(volume.get("@max"), 100),
            alarm_times_min=self._parse_int(alarm_times.get("@min"), 1),
            alarm_times_max=self._parse_int(alarm_times.get("@max"), 50),
            support_test=bool(capabilities.get("isSupportAudioTest", False)),
        )

    async def get_audio_alarm_state(self) -> AudioAlarmState | None:
        """Get the configured audio alarm sound, volume and alarm times."""

        try:
            settings = await self._get_audio_alarm_settings()
        except HTTPStatusError, httpx.HTTPError, ISAPIForbiddenError, ISAPIUnauthorizedError:
            return None
        if not settings:
            return None

        return self._audio_alarm_state(settings)

    async def set_audio_alarm_state(
        self,
        sound_id: int | None = None,
        volume: int | None = None,
        alarm_times: int | None = None,
    ) -> AudioAlarmState:
        """Change audio alarm settings, leaving the schedule and other fields as they are."""

        settings = await self._get_audio_alarm_settings()
        if not settings:
            # Writing back an empty document would wipe the schedule and the other settings.
            raise ValueError("Cannot read the current audio alarm settings")
        if sound_id is not None:
            # The device plays audioID, alertAudioID only records the choice within the alertAudio class
            settings["audioID"] = sound_id
            if "alertAudioID" in settings:
                settings["audioClass"] = "alertAudio"
                settings["alertAudioID"] = sound_id
        if volume is not None:
            settings["audioVolume"] = volume
        if alarm_times is not None:
            settings["alarmTimes"] = alarm_times

        data = json.dumps({"AudioAlarm": settings})
        await self.request(PUT, "Event/triggers/notifications/AudioAlarm?format=json", present="json", data=data)
        return self._audio_alarm_state(settings)

    async def test_audio_alarm(self, sound_id: int) -> None:
        """Play a sound once through the device speaker."""

        await self.request(PUT, f"Event/triggers/notifications/AudioAlarm/{sound_id}/test?format=json", present="json")

    async def _get_audio_alarm_settings(self) -> dict:
        response = await self.request(GET, "Event/triggers/notifications/AudioAlarm?format=json", present="json")
        return self._parse_json(response).get("AudioAlarm", {})

    @staticmethod
    def _audio_alarm_state(settings: dict) -> AudioAlarmState:
        if "alertAudioID" in settings:
            sound_id = settings["alertAudioID"] if settings.get("audioClass") == "alertAudio" else None
        else:
            sound_id = settings.get("audioID")

        return AudioAlarmState(
            sound_id=sound_id,
            volume=settings.get("audioVolume", 0),
            alarm_times=settings.get("alarmTimes", 0),
        )

    @staticmethod
    def _parse_json(response: Any) -> dict:
        # request() returns {} instead of raising while the integration initializes
        if not response or not isinstance(response, str):
            return {}
        try:
            return json.loads(response)
        except ValueError:
            return {}

    async def get_motion_targets(self, url: str) -> list[str]:
        """Return the targets (human, vehicle) motion detection tells apart, from its targetType setting."""
        data = await self.request(GET, url)
        target_type = deep_get(data, "MotionDetection.MotionDetectionLayout.targetType", "")
        if not isinstance(target_type, str):
            return []
        return [target for target in MOTION_TARGETS if target in split_targets(target_type)]

    def get_event_url(self, event_id: str, channel_id: int, io_port_id: int, is_proxy: bool) -> str | None:
        """Get event ISAPI URL."""

        if not EVENTS.get(event_id):
            return None

        event_type = EVENTS[event_id]["type"]
        slug = EVENTS[event_id]["slug"]

        if event_type == EVENT_BASIC:
            if is_proxy:
                url = f"ContentMgmt/InputProxy/channels/{channel_id}/video/{slug}"
            else:
                url = f"System/Video/inputs/channels/{channel_id}/{slug}"

        elif event_type == EVENT_IO:
            url = f"ContentMgmt/IOProxy/{slug}/{io_port_id}" if is_proxy else f"System/IO/{slug}/{io_port_id}"
        elif event_type == EVENT_PIR:
            # ISAPI/WLAlarm/PIR
            url = slug
        else:
            url = f"Smart/{slug}/{channel_id}"
        return url

    async def get_camera_streams(self, channel_id: int, default_streams: bool = False) -> list[CameraStreamInfo]:
        """Get stream info for all cameras.

        default_streams: when the device hides the details of the main and sub stream, assume
        they exist (NVRs answer 403 for IP channels added with ONVIF or other protocols).
        """
        streams = []
        for stream_type_id, stream_type in STREAM_TYPE.items():
            stream_id = f"{channel_id}0{stream_type_id}"
            stream_info = (await self.request(GET, f"Streaming/channels/{stream_id}")).get("StreamingChannel")
            if not stream_info:
                continue
            try:
                streams.append(
                    CameraStreamInfo(
                        id=int(stream_info["id"]),
                        name=stream_info["channelName"],
                        type_id=stream_type_id,
                        type=stream_type,
                        enabled=stream_info["enabled"],
                        codec=deep_get(stream_info, "Video.videoCodecType"),
                        width=deep_get(stream_info, "Video.videoResolutionWidth", 0),
                        height=deep_get(stream_info, "Video.videoResolutionHeight", 0),
                        audio=str_to_bool(deep_get(stream_info, "Audio.enabled", "false")),
                    )
                )
            except (KeyError, TypeError, ValueError) as ex:
                # Incomplete stream details shouldn't stop the rest of the device from loading.
                _LOGGER.debug("Stream %s not available: %s", stream_id, ex)
        if default_streams and not streams:
            # RTSP still works for these channels; only the stream details are hidden. The size is
            # unknown (0), so snapshots are requested without a resolution.
            for stream_type_id in (1, 2):
                streams.append(
                    CameraStreamInfo(
                        id=int(f"{channel_id}0{stream_type_id}"),
                        name=f"{channel_id}0{stream_type_id}",
                        type_id=stream_type_id,
                        type=STREAM_TYPE[stream_type_id],
                        enabled=True,
                        codec=None,
                        width=0,
                        height=0,
                        audio=False,
                    )
                )
        return streams

    def get_camera_by_id(self, camera_id: int) -> IPCamera | AnalogCamera | None:
        """Get camera object by id."""
        try:
            if camera_id == 0:
                return None
            return [camera for camera in self.cameras if camera.id == camera_id][0]
        except IndexError:
            # Camera id does not exist
            return None

    def get_camera_by_serial_no(self, serial_no: str) -> IPCamera | AnalogCamera | None:
        """Get camera object by serial number."""
        for c in self.cameras:
            if c.serial_no == serial_no:
                return c
        return None

    async def get_storage_devices(self):
        """Get HDD and NAS storage devices."""
        storage_list = []
        storage_info = (await self.request(GET, "ContentMgmt/Storage")).get("storage", {})

        hdd_list = storage_info.get("hddList") or {}
        if "hdd" in hdd_list:
            if not isinstance(hdd_list, list):
                hdd_list = [hdd_list]
            for storage in hdd_list:
                storage = storage.get("hdd")
                if not isinstance(storage, list):
                    storage = [storage]
                if storage:
                    for item in storage:
                        storage_list.append(  # noqa: PERF401
                            StorageInfo(
                                id=int(item.get("id")),
                                name=item.get("hddName"),
                                type=item.get("hddType"),
                                status=item.get("status"),
                                capacity=int(item.get("capacity")),
                                freespace=int(item.get("freeSpace")),
                                property=item.get("property"),
                            )
                        )

        nas_list = storage_info.get("nasList") or {}
        if "nas" in nas_list:
            if not isinstance(nas_list, list):
                nas_list = [nas_list]
            for storage in nas_list:
                storage = storage.get("nas")
                if not isinstance(storage, list):
                    storage = [storage]
                if storage:
                    for item in storage:
                        storage_list.append(  # noqa: PERF401
                            StorageInfo(
                                id=int(item.get("id")),
                                name=item.get("path"),
                                type=item.get("nasType"),
                                status=item.get("status"),
                                capacity=int(item.get("capacity")),
                                freespace=int(item.get("freeSpace")),
                                property=item.get("property"),
                                ip=item.get("ipAddress"),
                            )
                        )

        return storage_list

    def get_storage_device_by_id(self, device_id: int) -> StorageInfo | None:
        """Get storage object by id."""
        try:
            return [storage_device for storage_device in self.storage if storage_device.id == device_id][0]
        except IndexError:
            # Storage id does not exist
            return None

    async def search_recordings(
        self,
        track_id: int,
        start: datetime.datetime,
        end: datetime.datetime,
        max_pages: int = 20,
    ) -> list[RecordingSegment]:
        """Search video segments recorded on the device storage within a time span."""
        segments = []
        position = 0
        search_id = str(uuid.uuid4()).upper()
        for _ in range(max_pages):
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<CMSearchDescription>"
                f"<searchID>{search_id}</searchID>"
                f"<trackList><trackID>{track_id}</trackID></trackList>"
                "<timeSpanList><timeSpan>"
                f"<startTime>{format_isapi_time(start)}</startTime>"
                f"<endTime>{format_isapi_time(end)}</endTime>"
                "</timeSpan></timeSpanList>"
                f"<maxResults>{SEARCH_PAGE_SIZE}</maxResults>"
                f"<searchResultPostion>{position}</searchResultPostion>"
                "<metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList>"
                "</CMSearchDescription>"
            )
            result = (await self.request(POST, "ContentMgmt/search", data=xml)).get("CMSearchResult", {})
            matches = deep_get(result, "matchList.searchMatchItem", [])
            for item in matches:
                seg_start = parse_isapi_time(deep_get(item, "timeSpan.startTime"))
                seg_end = parse_isapi_time(deep_get(item, "timeSpan.endTime"))
                if seg_start and seg_end:
                    segments.append(RecordingSegment(track_id=track_id, start=seg_start, end=seg_end))
            num_matches = int(result.get("numOfMatches") or 0)
            position += num_matches
            if result.get("responseStatusStrg") != "MORE" or num_matches == 0:
                break
        else:
            _LOGGER.warning(
                "Recording search of track %s stopped after %s pages; later recordings are missing", track_id, max_pages
            )
        return sorted(segments, key=lambda segment: segment.start)

    async def search_alarm_log(
        self,
        start: datetime.datetime,
        end: datetime.datetime,
        max_pages: int = 20,
    ) -> list[LoggedEvent]:
        """Search alarm events (motion, intrusion, ...) in the device log, pairing Start/Stop entries."""
        entries = []
        position = 0
        search_id = str(uuid.uuid4()).upper()
        for _ in range(max_pages):
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<CMSearchDescription>"
                f"<searchID>{search_id}</searchID>"
                "<timeSpanList><timeSpan>"
                f"<startTime>{format_isapi_time(start)}</startTime>"
                f"<endTime>{format_isapi_time(end)}</endTime>"
                "</timeSpan></timeSpanList>"
                f"<maxResults>{LOG_PAGE_SIZE}</maxResults>"
                f"<searchResultPostion>{position}</searchResultPostion>"
                "<metaId>log.std-cgi.com/Alarm</metaId>"
                "</CMSearchDescription>"
            )
            result = (await self.request(POST, "ContentMgmt/logSearch", data=xml)).get("CMSearchResult", {})
            matches = deep_get(result, "matchList.searchMatchItem", [])
            for item in matches:
                # metaId format: log.hikvision.com/Alarm/motionStart/1
                meta_id = str(deep_get(item, "logDescriptor.metaId") or "")
                time = parse_isapi_time(deep_get(item, "logDescriptor.StartDateTime"))
                parts = meta_id.split("/")
                if len(parts) < 3 or parts[1] != "Alarm" or not time:
                    continue
                channel_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
                entries.append((time, parts[2], channel_id))
            position += len(matches)
            if result.get("responseStatusStrg") != "MORE" or not matches:
                break
        else:
            _LOGGER.warning("Alarm log search stopped after %s pages; later events are missing", max_pages)

        events: list[LoggedEvent] = []
        open_events: dict[tuple[str, int | None], LoggedEvent] = {}
        for time, name, channel_id in sorted(entries, key=lambda entry: entry[0]):
            if name.endswith("Start"):
                event = LoggedEvent(event_type=name[:-5], channel_id=channel_id, start=time)
                open_events[(event.event_type, channel_id)] = event
                events.append(event)
            elif name.endswith("Stop"):
                event = open_events.pop((name[:-4], channel_id), None)
                if event:
                    event.end = time
            else:
                events.append(LoggedEvent(event_type=name, channel_id=channel_id, start=time, end=time))
        return events

    async def get_recording_days(self, track_id: int, year: int, month: int) -> list[int]:
        """Get days of the month that have recordings (device local time)."""
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<trackDailyParam><year>{year}</year><monthOfYear>{month}</monthOfYear></trackDailyParam>"
        )
        response = await self.request(POST, f"ContentMgmt/record/tracks/{track_id}/dailyDistribution", data=xml)
        days = deep_get(response, "trackDailyDistribution.dayList.day", [])
        return sorted(int(day["dayOfMonth"]) for day in days if str_to_bool(day.get("record")))

    def get_playback_source(self, track_id: int, start: datetime.datetime, end: datetime.datetime) -> str:
        """Get RTSP source playing back a recorded track from the given time."""
        u = quote(self.username, safe="")
        p = quote(self.password, safe="")
        url = f"{self.device_info.ip_address}:{self.protocols.rtsp_port}/Streaming/tracks/{track_id}/"
        times = f"starttime={format_isapi_time(start, compact=True)}&endtime={format_isapi_time(end, compact=True)}"
        return f"rtsp://{u}:{p}@{url}?{times}"

    def _get_event_state_node(self, event: EventInfo) -> str:
        """Get xml key for event state."""
        slug = EVENTS[event.id]["slug"]

        # Alternate node name for some event types
        if event.is_proxy and (proxied_node := EVENTS[event.id].get("proxied_node")):
            slug = proxied_node
        if not event.is_proxy and (direct_node := EVENTS[event.id].get("direct_node")):
            slug = direct_node

        return slug[0].upper() + slug[1:]

    async def get_event_enabled_state(self, event: EventInfo) -> bool:
        """Get event detection state."""
        if not event.url:
            _LOGGER.warning("Cannot fetch event enabled state. Unknown event URL %s", event.id)
            return False
        state = await self.request(GET, event.url)
        node = self._get_event_state_node(event)
        return str_to_bool(state[node].get("enabled", "false")) if state.get(node) else False

    @staticmethod
    def _motion_polygons(layout: Any) -> list[dict]:
        """Return the motion polygons of a layout that has a gridMap, or [] if none has 3+ points."""
        if not isinstance(layout, dict) or "gridMap" not in layout:
            return []
        region_list = layout.get("RegionList")
        if not isinstance(region_list, dict):
            return []
        regions = region_list.get("Region") or []
        if isinstance(regions, dict):
            regions = [regions]
        valid = []
        for region in regions:
            points = deep_get(region, "RegionCoordinatesList.RegionCoordinates", [])
            if isinstance(points, dict):
                points = [points]
            if len(points) >= 3:
                valid.append(region)
        # Rewrite only when every region is usable, so no part of the area is dropped.
        return valid if valid and len(valid) == len(regions) else []

    async def get_event_switch_mutex(self, event: EventInfo, channel_id: int) -> list[MutexIssue]:
        """Get if event is mutually exclusive with enabled events."""
        mutex_issues = []

        if not EVENTS[event.id].get("mutex"):
            return mutex_issues

        # Use alt event ID for mutex due to crap API!
        event_id = event.id
        if MUTEX_ALTERNATE_ID.get(event.id):
            event_id = MUTEX_ALTERNATE_ID[event.id]

        data = {"function": event_id, "channelID": int(channel_id)}
        url = "System/mutexFunction?format=json"
        response = await self.request(POST, url, present="json", data=json.dumps(data))
        if not response:
            return []
        response = json.loads(response)

        if mutex_list := response.get("MutexFunctionList"):
            for mutex_item in mutex_list:
                mutex_event_id = mutex_item.get("mutexFunction")
                if EVENTS_ALTERNATE_ID.get(mutex_event_id):
                    mutex_event_id = EVENTS_ALTERNATE_ID[mutex_event_id]

                mutex_issues.append(
                    MutexIssue(
                        event_id=mutex_event_id,
                        channels=mutex_item.get("channelID"),
                    )
                )
        return mutex_issues

    async def set_event_enabled_state(self, channel_id: int, event: EventInfo, is_enabled: bool) -> None:
        """Set event detection state."""
        if not event.url:
            _LOGGER.warning("Cannot set event enabled state. Unknown event URL %s", event.id)
            return False
        # Validate that this event switch is not mutually exclusive with another enabled one
        mutex_issues = []
        if channel_id != 0 and is_enabled and self.capabilities.support_event_mutex_checking:
            mutex_issues = await self.get_event_switch_mutex(event, channel_id)

        if not mutex_issues:
            data = await self.request(GET, event.url)
            node = self._get_event_state_node(event)
            new_state = bool_to_str(is_enabled)
            if new_state == data[node]["enabled"]:
                return
            data[node]["enabled"] = new_state
            # When motion detection has regionType="grid", the firmware recomputes
            # RegionCoordinatesList as the bounding box of gridMap on every PUT,
            # destroying the user's custom polygon. The Hikvision web UI works
            # around this by sending regionType="region" with a zeroed gridMap,
            # which makes the firmware treat RegionList as the source of truth
            # and re-derive gridMap from the polygon. Mirror what the UI does.
            # Only when a usable polygon exists: on grid-only devices a zeroed gridMap would
            # leave the camera with no motion area at all.
            if data[node].get("regionType") == "grid":
                layout = deep_get(data[node], "MotionDetectionLayout.layout")
                regions = self._motion_polygons(layout)
                if regions:
                    data[node]["regionType"] = "region"
                    layout["gridMap"] = "0" * len(layout["gridMap"])
                    layout["RegionList"]["@size"] = str(len(regions))
            xml = xmltodict.unparse(data)
            await self.request(PUT, event.url, present="xml", data=xml)
        else:
            raise ISAPISetEventStateMutexError(event, mutex_issues)

    async def get_cameras_online_status(self) -> dict[int, dict[str, Any]]:
        """Get the connection status of IP cameras connected to an NVR, by camera ID."""
        data = await self.request(GET, "ContentMgmt/InputProxy/channels/status")
        channels = deep_get(data, "InputProxyChannelStatusList.InputProxyChannelStatus", [])
        if isinstance(channels, dict):
            channels = [channels]
        return {
            clean_int(channel["id"]): {
                "online": str_to_bool(channel.get("online", "false")),
                "detect_result": channel.get("chanDetectResult"),
            }
            for channel in channels
            if isinstance(channel, dict) and channel.get("id")
        }

    async def get_events_notify_center(self, trigger_ids: list[str]) -> dict[str, bool]:
        """Return whether each event trigger notifies the surveillance center (Home Assistant)."""
        result = {
            trigger["id"]: notifies_center(trigger)
            for trigger in get_event_triggers_list(await self.request(GET, "Event/triggers"))
            if trigger.get("id") in trigger_ids
        }
        # Triggers missing from the list, on devices that only answer them one by one
        for trigger_id in trigger_ids:
            if trigger_id not in result:
                try:
                    response = await self.request(GET, f"Event/triggers/{trigger_id}")
                except (HTTPStatusError, ISAPIForbiddenError) as ex:
                    # One unreadable trigger leaves only its own switch unknown.
                    _LOGGER.debug("Cannot read Event/triggers/%s: %s", trigger_id, ex)
                    continue
                if trigger := deep_get(response, "EventTrigger", {}):
                    result[trigger_id] = notifies_center(trigger)
        return result

    async def set_event_notify_center(self, event: EventInfo, is_enabled: bool) -> None:
        """Add or remove the 'Notify Surveillance Center' action of an event trigger."""
        url = f"Event/triggers/{event.trigger_id}"
        data = await self.request(GET, url)
        event_trigger = data.get("EventTrigger") if data else None
        if not isinstance(event_trigger, dict):
            raise ValueError(f"Cannot read {url}")

        if notifies_center(event_trigger) != is_enabled:
            notifications = get_trigger_notifications(event_trigger)
            if is_enabled:
                notifications.append(
                    {"id": "center", "notificationMethod": "center", "notificationRecurrence": "beginning"}
                )
            else:
                notifications = [notify for notify in notifications if notify.get("notificationMethod") != "center"]

            # Only the notification methods change; the rest of the trigger is sent back as read.
            notification_list = event_trigger.get("EventTriggerNotificationList") or {}
            if notifications:
                notification_list["EventTriggerNotification"] = notifications
            else:
                notification_list.pop("EventTriggerNotification", None)
            event_trigger["EventTriggerNotificationList"] = notification_list or None

            await self.request(PUT, url, present="xml", data=xmltodict.unparse(data))

        methods = [method for method in event.notifications if method != "center"]
        event.notifications = [*methods, "center"] if is_enabled else methods

    async def get_io_port_status(self, port_type: str, port_no: int) -> str:
        """Get status of physical ports."""
        if port_type == "input":
            status = await self.request(GET, f"System/IO/inputs/{port_no}/status")
        else:
            status = await self.request(GET, f"System/IO/outputs/{port_no}/status")
        return deep_get(status, "IOPortStatus.ioState", "inactive") == "active"

    async def set_output_port_state(self, port_no: int, turn_on: bool):
        """Set status of output port."""
        data = {}
        if turn_on:
            data["IOPortData"] = {"outputState": "high"}
        else:
            data["IOPortData"] = {"outputState": "low"}

        xml = xmltodict.unparse(data)
        await self.request(PUT, f"System/IO/outputs/{port_no}/trigger", present="xml", data=xml)

    async def get_holiday_enabled_state(self, holiday_index=0) -> bool:
        """Get holiday state."""

        data = await self.request(GET, "System/Holidays")
        holiday = data["HolidayList"]["holiday"][holiday_index]
        return str_to_bool(holiday["enabled"]["#text"])

    async def set_holiday_enabled_state(self, is_enabled: bool, holiday_index=0) -> None:
        """Enable or disable holiday, by enable set time span to year starting from today."""

        data = await self.request(GET, "System/Holidays")
        holiday = data["HolidayList"]["holiday"][holiday_index]
        new_state = bool_to_str(is_enabled)
        if new_state == holiday["enabled"]["#text"]:
            return
        holiday["enabled"]["#text"] = new_state
        if is_enabled:
            today = datetime.date.today()
            holiday["holidayMode"]["#text"] = "date"
            holiday["holidayDate"] = {
                "startDate": today.strftime("%Y-%m-%d"),
                "endDate": today.replace(year=today.year + 1).strftime("%Y-%m-%d"),
            }
            holiday.pop("holidayWeek", None)
            holiday.pop("holidayMonth", None)
        xml = xmltodict.unparse(data)
        await self.request(PUT, "System/Holidays", present="xml", data=xml)

    def _get_event_notification_host(self, data: Node) -> Node:
        hosts = deep_get(data, "HttpHostNotificationList.HttpHostNotification", [])
        if hosts:
            return hosts[0]

    async def get_alarm_server(self) -> AlarmServer | None:
        """Get event notifications listener server URL."""

        data = await self.request(GET, "Event/notification/httpHosts")
        if not data:
            return None
        host = self._get_event_notification_host(data)

        return AlarmServer(
            ip_address=host.get("ipAddress"),
            port_no=int(host.get("portNo")),
            url=host.get("url"),
            protocol_type=host.get("protocolType"),
            host_name=host.get("hostName"),
        )

    async def set_alarm_server(self, base_url: str, path: str) -> None:
        """Set event notifications listener server."""

        address = urlparse(base_url)
        data = await self.request(GET, "Event/notification/httpHosts")
        if not data:
            return
        host = self._get_event_notification_host(data)

        old_address = host.get("ipAddress") if host.get("addressingFormatType") == "ipaddress" else host.get("hostname")

        if (
            host["protocolType"] == address.scheme.upper()
            and old_address == address.hostname
            and host.get("portNo") == str(address.port)
            and host["url"] == path
        ):
            return
        host["url"] = path
        host["protocolType"] = address.scheme.upper()
        host["parameterFormatType"] = "XML"

        try:
            ipaddress.ip_address(address.hostname)

            # if address.hostname is an ip
            host["addressingFormatType"] = "ipaddress"
            host["ipAddress"] = address.hostname
            host["hostName"] = None
            del host["hostName"]
        except ValueError:
            # if address.hostname is a domain
            host["addressingFormatType"] = "hostname"
            host["ipAddress"] = None
            del host["ipAddress"]
            host["hostName"] = address.hostname

        host["portNo"] = address.port or (443 if address.scheme == "https" else 80)
        host["httpAuthenticationMethod"] = "none"

        xml = xmltodict.unparse(data)
        await self.request(PUT, "Event/notification/httpHosts", present="xml", data=xml)

    async def ptz_continuous(self, channel_id: int, pan: int = 0, tilt: int = 0, zoom: int = 0) -> None:
        """Start or stop a continuous PTZ movement; all zeros stop it.

        Values go from -100 to 100. Positive pan turns right, positive tilt goes up and positive
        zoom zooms in.
        """
        data = {"PTZData": {"pan": pan, "tilt": tilt, "zoom": zoom}}
        xml = xmltodict.unparse(data, full_document=False)
        await self.request(PUT, f"PTZCtrl/channels/{channel_id}/continuous", present="xml", data=xml)

    async def ptz_goto_preset(self, channel_id: int, preset: int) -> None:
        """Move a PTZ camera to a stored preset."""
        await self.request(PUT, f"PTZCtrl/channels/{channel_id}/presets/{preset}/goto", present="xml")

    async def reboot(self):
        """Reboot device."""
        await self.request(PUT, "System/reboot", present="xml")

    @staticmethod
    def parse_event_notification(xml: str) -> AlertInfo:
        """Parse incoming EventNotificationAlert XML message."""

        # Fix for some cameras sending non html encoded data
        xml = xml.replace("&", "&amp;")

        data = xmltodict.parse(xml)
        alert = data["EventNotificationAlert"]

        event_id = alert.get("eventType")
        if not event_id or event_id == "duration":
            # <EventNotificationAlert version="2.0"
            event_id = deep_get(alert, "DurationList.Duration.relationEvent", "")
        raw_event_type = event_id
        event_id = normalize_event_id(event_id)

        channel_id = clean_int(alert.get("channelID", alert.get("dynChannelID", 0)))
        if not channel_id:
            channel_id = event_channel_suffix(raw_event_type)
        io_port_id = clean_int(alert.get("inputIOPortID", 0))
        # <EventNotificationAlert version="1.0"
        device_serial = deep_get(alert, "Extensions.serialNumber.#text")
        # <EventNotificationAlert version="2.0"
        mac = alert.get("macAddress")

        detection_target = deep_get(alert, "DetectionRegionList.DetectionRegionEntry.detectionTarget")
        region_id = int(deep_get(alert, "DetectionRegionList.DetectionRegionEntry.regionID", 0))

        target_type = alert.get("targetType")
        event_state = str(alert.get("eventState", "active")).lower()

        if event_id not in EVENTS:
            raise ISAPIUnsupportedEventError(event_id)

        return AlertInfo(
            channel_id,
            io_port_id,
            event_id,
            device_serial,
            mac,
            region_id,
            detection_target,
            target_type,
            event_state=event_state,
            ip_address=alert.get("ipAddress"),
        )

    async def get_camera_image(
        self,
        stream: CameraStreamInfo,
        width: int | None = None,
        height: int | None = None,
        attempt: int = 0,
    ):
        """Get camera snapshot."""
        params = {}
        if (not width or width > 100) and stream.width and stream.height:
            params = {
                "videoResolutionWidth": stream.width,
                "videoResolutionHeight": stream.height,
            }

        if stream.use_alternate_picture_url:
            url = f"ContentMgmt/StreamingProxy/channels/{stream.id}/picture"
            full_url = self.get_isapi_url(url)
            chunks = self.request_bytes(GET, full_url, params=params)
        else:
            url = f"Streaming/channels/{stream.id}/picture"
            full_url = self.get_isapi_url(url)
            chunks = self.request_bytes(GET, full_url, params=params)
        data = b"".join([chunk async for chunk in chunks])

        if data.startswith(b"<?xml "):
            error = xmltodict.parse(data)
            status_code = int(deep_get(error, "ResponseStatus.statusCode"))
            if status_code == 6 and not stream.use_alternate_picture_url:
                # handle 'Invalid XML Content' for some cameras, use alternate url for still image
                stream.use_alternate_picture_url = True
                return await self.get_camera_image(stream, width, height)
            if status_code == 3 and attempt < 2:
                # handle 'Device Error', try again
                return await self.get_camera_image(stream, width, height, attempt + 1)
            if status_code == 4 and deep_get(error, "ResponseStatus.subStatusCode") == "notSupport":
                # handle 'Invalid Operation / notSupport', e.g. PoE channels of DS-7104NI-Q1 NVRs
                if not stream.use_alternate_picture_url:
                    stream.use_alternate_picture_url = True
                    return await self.get_camera_image(stream, width, height)
                # no snapshot endpoint at all: stills will be generated from the stream
                stream.snapshot_not_supported = True

        return data

    def get_stream_source(self, stream: CameraStreamInfo) -> str:
        """Get stream source."""
        u = quote(self.username, safe="")
        p = quote(self.password, safe="")
        url = f"{self.device_info.ip_address}:{self.protocols.rtsp_port}/Streaming/channels/{stream.id}"
        return f"rtsp://{u}:{p}@{url}"

    async def _detect_auth_method(self):
        """Establish the connection with device."""
        if not self._session:
            self._session = httpx.AsyncClient(timeout=self.timeout, verify=self.verify_ssl)

        url = urljoin(self.host, self.isapi_prefix + "/System/deviceInfo")
        _LOGGER.debug("--- [WWW-Authenticate detection] %s", self.host)
        response = await self._session.get(url)
        if response.status_code == 401:
            www_authenticate = response.headers.get("WWW-Authenticate", "")
            _LOGGER.debug("WWW-Authenticate header: %s", www_authenticate)
            if "Basic" in www_authenticate:
                self._auth_method = httpx.BasicAuth(self.username, self.password)
            elif "Digest" in www_authenticate:
                self._auth_method = httpx.DigestAuth(self.username, self.password)

        if not self._auth_method:
            _LOGGER.error("Authentication method not detected, %s", response.status_code)
            if response.headers:
                _LOGGER.error("response.headers %s", response.headers)

    def get_isapi_url(self, relative_url: str) -> str:
        """Build full ISAPI URL."""
        return f"{self.host}/{self.isapi_prefix}/{relative_url}"

    async def request(
        self,
        method: str,
        url: str,
        present: str = "dict",
        data: str = None,
    ) -> Any:
        """Send ISAPI request and log response, returns {} if request fails."""
        full_url = self.get_isapi_url(url)
        try:
            if not self._auth_method:
                await self._detect_auth_method()

            request_kwargs = {
                "auth": self._auth_method,
                "timeout": self.timeout,
            }
            if data is not None:
                request_kwargs["content"] = data

            response = await self._session.request(
                method,
                full_url,
                **request_kwargs,
            )
            response.raise_for_status()
            result = parse_isapi_response(response, present)
            _LOGGER.debug("--- [%s] %s", method, full_url)
            if data:
                _LOGGER.debug(">>> payload:\n%s", data)
            _LOGGER.debug("\n%s", result)
        except HTTPStatusError as ex:
            _LOGGER.info("--- [%s] %s\n%s", method, full_url, ex)
            if ex.response.status_code == HTTPStatus.UNAUTHORIZED:
                raise ISAPIUnauthorizedError(ex) from ex
            if ex.response.status_code == HTTPStatus.FORBIDDEN and not self.pending_initialization:
                raise ISAPIForbiddenError(ex) from ex
            if self.pending_initialization:
                # supress http errors during initialization
                return {}
            raise
        else:
            return result

    async def request_bytes(
        self,
        method: str,
        full_url: str,
        **data,
    ) -> AsyncIterator[bytes]:
        """Send ISAPI request for binary data."""

        try:
            if not self._auth_method:
                await self._detect_auth_method()

            async with self._session.stream(method, full_url, auth=self._auth_method, **data) as response:
                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.HTTPError as ex:
            _LOGGER.warning("Failed request [%s] %s | %s", method, full_url, ex)


class ISAPISetEventStateMutexError(Exception):
    """Error setting event mutex."""

    def __init__(self, event: EventInfo, mutex_issues: []) -> None:
        """Initialize exception."""
        self.event = event
        self.mutex_issues = mutex_issues
        self.message = f"""You cannot enable {EVENTS[event.id]["label"]} events.
            Please disable {EVENTS[mutex_issues[0].event_id]["label"]}
            on channels {mutex_issues[0].channels} first"""


class ISAPIUnsupportedEventError(ValueError):
    """Event notification of a type the integration has no entities for."""

    def __init__(self, event_id: str) -> None:
        """Initialize exception."""
        super().__init__(f"Unsupported event {event_id}")
        self.event_id = event_id


class ISAPIUnauthorizedError(Exception):
    """HTTP Error 401."""

    def __init__(self, ex: HTTPStatusError, *args) -> None:
        """Initialize exception."""
        self.message = f"Unauthorized request {ex.request.url}, check username and password."
        self.response = ex.response


class ISAPIForbiddenError(Exception):
    """HTTP Error 403."""

    def __init__(self, ex: HTTPStatusError, *args) -> None:
        """Initialize exception."""
        self.message = f"Forbidden request {ex.request.url}, check user permissions."
        self.response = ex.response
        _LOGGER.warning(self.message)
