"""Expose recordings stored on Hikvision device storage (SD card / HDD) as media sources."""

from __future__ import annotations

import datetime
import logging

from homeassistant.components.camera import DynamicStreamSettings
from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.components.stream import create_stream
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util, slugify

from .const import DOMAIN
from .hikvision_device import HikvisionDevice
from .isapi import AnalogCamera, IPCamera, LoggedEvent, RecordingSegment
from .isapi.utils import get_stream_id

_LOGGER = logging.getLogger(__name__)

DAYS_FALLBACK = 14
CLIP_STEP = datetime.timedelta(minutes=10)
PLAYBACK_LENGTH = datetime.timedelta(hours=1)
EVENT_PRE_ROLL = datetime.timedelta(seconds=10)
EVENT_POST_ROLL = datetime.timedelta(seconds=20)
EVENT_MIN_LENGTH = datetime.timedelta(seconds=30)
STREAM_IDLE_TIMEOUT = 600

LABELS = {
    "en": {
        "events": "Events",
        "motion": "Motion",
        "fieldDetection": "Intrusion",
        "lineDetection": "Line crossing",
        "regionEntrance": "Region entrance",
        "regionExiting": "Region exiting",
        "sceneChangeDetection": "Scene change",
        "shelteralarm": "Tamper",
        "tamperDetection": "Tamper",
    },
    "pl": {
        "events": "Zdarzenia",
        "motion": "Ruch",
        "fieldDetection": "Wtargnięcie",
        "lineDetection": "Przekroczenie linii",
        "regionEntrance": "Wejście w obszar",
        "regionExiting": "Wyjście z obszaru",
        "sceneChangeDetection": "Zmiana sceny",
        "shelteralarm": "Sabotaż",
        "tamperDetection": "Sabotaż",
    },
}


async def async_get_media_source(hass: HomeAssistant) -> HikvisionMediaSource:
    """Set up Hikvision recordings media source."""
    return HikvisionMediaSource(hass)


def overlaps(segments: list[RecordingSegment], start: datetime.datetime, end: datetime.datetime) -> bool:
    """Return True if any recorded segment overlaps the [start, end) span."""
    return any(segment.start < end and segment.end > start for segment in segments)


def events_in(events: list[LoggedEvent], start: datetime.datetime, end: datetime.datetime) -> list[LoggedEvent]:
    """Return events active within the [start, end) span."""
    return [event for event in events if event.start < end and (event.end or event.start) >= start]


class HikvisionMediaSource(MediaSource):
    """Provide recordings from Hikvision device storage as media sources."""

    name: str = "Hikvision"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize HikvisionMediaSource."""
        super().__init__(DOMAIN)
        self.hass = hass

    def _label(self, key: str) -> str:
        """Return a label in the configured language."""
        labels = LABELS["pl"] if (self.hass.config.language or "").startswith("pl") else LABELS["en"]
        return labels.get(key, key)

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a recording to a playable HLS stream."""
        parts = (item.identifier or "").split("|")
        if len(parts) not in (4, 5) or parts[0] != "PLAY":
            raise Unresolvable(f"Unknown media item '{item.identifier}'.")

        device, camera = self._get_camera(parts[1], parts[2])
        start = dt_util.utc_from_timestamp(int(parts[3]))
        end = dt_util.utc_from_timestamp(int(parts[4])) if len(parts) == 5 else start + PLAYBACK_LENGTH
        end = min(end, dt_util.utcnow())
        if end <= start:
            raise Unresolvable("Recording start time is in the future.")

        url = device.get_playback_source(get_stream_id(camera.id), start, end)
        _LOGGER.debug("Playing back %s from %s to %s", camera.name, start, end)

        stream = create_stream(self.hass, url, {}, DynamicStreamSettings(), f"{camera.name} playback")
        stream.add_provider("hls", timeout=STREAM_IDLE_TIMEOUT)
        stream_url: str = stream.endpoint_url("hls").replace("master_", "")
        return PlayMedia(stream_url, "application/x-mpegURL")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse cameras, days, hours, events and recordings."""
        parts = item.identifier.split("|") if item.identifier else []

        if not parts:
            return self._browse_cameras()
        if parts[0] == "CAM" and len(parts) == 3:
            return await self._browse_days(parts[1], parts[2])
        if parts[0] == "DAY" and len(parts) == 4:
            return await self._browse_hours(parts[1], parts[2], parts[3])
        if parts[0] == "EVT" and len(parts) == 4:
            return await self._browse_events(parts[1], parts[2], parts[3])
        if parts[0] == "HOUR" and len(parts) == 5:
            return await self._browse_clips(parts[1], parts[2], parts[3], int(parts[4]))

        raise Unresolvable(f"Unknown media item '{item.identifier}' during browsing.")

    def _get_devices(self) -> list[tuple[str, HikvisionDevice]]:
        """Return loaded config entries with their devices."""
        return [
            (entry.entry_id, entry.runtime_data)
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
        ]

    def _get_camera(self, entry_id: str, camera_id: str) -> tuple[HikvisionDevice, AnalogCamera | IPCamera]:
        """Return device and camera for identifiers."""
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if not entry or entry.state is not ConfigEntryState.LOADED:
            raise Unresolvable(f"Hikvision device '{entry_id}' is not loaded.")
        device: HikvisionDevice = entry.runtime_data
        camera = device.get_camera_by_id(int(camera_id))
        if not camera:
            raise Unresolvable(f"Camera '{camera_id}' not found.")
        return device, camera

    async def _get_events(
        self,
        device: HikvisionDevice,
        camera: AnalogCamera | IPCamera,
        start: datetime.datetime,
        end: datetime.datetime,
    ) -> list[LoggedEvent]:
        """Return logged alarm events of the camera, or none if the device log is not available."""
        try:
            events = await device.search_alarm_log(start, end)
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.debug("Alarm log not available for %s: %s", camera.name, ex)
            return []
        return [event for event in events if event.channel_id in (None, camera.id)]

    def _camera_thumbnail(self, device: HikvisionDevice, camera: AnalogCamera | IPCamera) -> str | None:
        """Return a still image URL of the camera main stream entity."""
        unique_id = slugify(f"{device.device_info.serial_no.lower()}_{get_stream_id(camera.id)}")
        entity_id = er.async_get(self.hass).async_get_entity_id("camera", DOMAIN, unique_id)
        return f"/api/camera_proxy/{entity_id}" if entity_id else None

    def _camera_name(self, device: HikvisionDevice, camera: AnalogCamera | IPCamera) -> str:
        """Return the camera name as shown in Home Assistant, falling back to the device channel name."""
        registry = dr.async_get(self.hass)
        for serial_no in (camera.serial_no, device.device_info.serial_no):
            if serial_no and (device_entry := registry.async_get_device(identifiers={(DOMAIN, serial_no)})):
                return device_entry.name_by_user or device_entry.name or camera.name
        return camera.name

    def _event_title(self, events: list[LoggedEvent]) -> str:
        """Return comma separated event type labels."""
        types = list(dict.fromkeys(self._label(event.event_type) for event in events))
        return ", ".join(types)

    def _browse_cameras(self) -> BrowseMediaSource:
        """List cameras of all devices."""
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"CAM|{entry_id}|{camera.id}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.PLAYLIST,
                title=self._camera_name(device, camera),
                thumbnail=self._camera_thumbnail(device, camera),
                can_play=False,
                can_expand=True,
            )
            for entry_id, device in self._get_devices()
            for camera in device.cameras
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PLAYLIST,
            title=self.name,
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    async def _browse_days(self, entry_id: str, camera_id: str) -> BrowseMediaSource:
        """List days with recordings, newest first."""
        device, camera = self._get_camera(entry_id, camera_id)
        track_id = get_stream_id(camera.id)
        today = dt_util.now().date()

        this_month = today.replace(day=1)
        previous_month = (this_month - datetime.timedelta(days=1)).replace(day=1)
        days: list[datetime.date] = []
        try:
            for month_start in (this_month, previous_month):
                days.extend(
                    month_start.replace(day=day)
                    for day in await device.get_recording_days(track_id, month_start.year, month_start.month)
                )
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.debug("Daily distribution not available for %s: %s", camera.name, ex)
        if not days:
            days = [today - datetime.timedelta(days=offset) for offset in range(DAYS_FALLBACK)]

        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"DAY|{entry_id}|{camera_id}|{day.isoformat()}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.PLAYLIST,
                title=day.isoformat(),
                can_play=False,
                can_expand=True,
            )
            for day in sorted(set(days), reverse=True)
            if day <= today
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"CAM|{entry_id}|{camera_id}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PLAYLIST,
            title=self._camera_name(device, camera),
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    async def _browse_hours(self, entry_id: str, camera_id: str, day_str: str) -> BrowseMediaSource:
        """List the day's events folder and the hours covered by recordings."""
        device, camera = self._get_camera(entry_id, camera_id)
        day_start = dt_util.start_of_local_day(datetime.date.fromisoformat(day_str))
        day_end = day_start + datetime.timedelta(days=1)
        segments = await device.search_recordings(get_stream_id(camera.id), day_start, day_end)
        events = await self._get_events(device, camera, day_start, day_end)

        children = []
        if events:
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"EVT|{entry_id}|{camera_id}|{day_str}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.PLAYLIST,
                    title=f"{self._label('events')} ({len(events)})",
                    can_play=False,
                    can_expand=True,
                )
            )
        for hour in range(24):
            hour_start = day_start + datetime.timedelta(hours=hour)
            hour_end = hour_start + datetime.timedelta(hours=1)
            if not overlaps(segments, hour_start, hour_end):
                continue
            title = f"{hour_start:%H:%M} - {hour_end:%H:%M}"
            if hour_events := events_in(events, hour_start, hour_end):
                title += f" · {self._label('events').lower()}: {len(hour_events)}"
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"HOUR|{entry_id}|{camera_id}|{day_str}|{hour}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.PLAYLIST,
                    title=title,
                    can_play=False,
                    can_expand=True,
                )
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"DAY|{entry_id}|{camera_id}|{day_str}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PLAYLIST,
            title=f"{self._camera_name(device, camera)} {day_str}",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    async def _browse_events(self, entry_id: str, camera_id: str, day_str: str) -> BrowseMediaSource:
        """List the day's logged events, newest first, each playable from just before it started."""
        device, camera = self._get_camera(entry_id, camera_id)
        day_start = dt_util.start_of_local_day(datetime.date.fromisoformat(day_str))
        events = await self._get_events(device, camera, day_start, day_start + datetime.timedelta(days=1))

        children = []
        for event in sorted(events, key=lambda event: event.start, reverse=True):
            play_start = event.start - EVENT_PRE_ROLL
            play_end = max((event.end or event.start) + EVENT_POST_ROLL, play_start + EVENT_MIN_LENGTH)
            title = f"{dt_util.as_local(event.start):%H:%M:%S} {self._label(event.event_type)}"
            if event.end and event.end > event.start:
                title += f" ({int((event.end - event.start).total_seconds())} s)"
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=(
                        f"PLAY|{entry_id}|{camera_id}|{int(play_start.timestamp())}|{int(play_end.timestamp())}"
                    ),
                    media_class=MediaClass.VIDEO,
                    media_content_type=MediaType.VIDEO,
                    title=title,
                    can_play=True,
                    can_expand=False,
                )
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"EVT|{entry_id}|{camera_id}|{day_str}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PLAYLIST,
            title=f"{self._camera_name(device, camera)} {day_str} {self._label('events')}",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.VIDEO,
        )

    async def _browse_clips(self, entry_id: str, camera_id: str, day_str: str, hour: int) -> BrowseMediaSource:
        """List playback start points within an hour, marking those with events."""
        device, camera = self._get_camera(entry_id, camera_id)
        hour_start = dt_util.start_of_local_day(datetime.date.fromisoformat(day_str)) + datetime.timedelta(hours=hour)
        hour_end = hour_start + datetime.timedelta(hours=1)
        segments = await device.search_recordings(get_stream_id(camera.id), hour_start, hour_end)
        events = await self._get_events(device, camera, hour_start, hour_end)

        children = []
        clip_start = hour_start
        while clip_start < hour_end:
            clip_end = clip_start + CLIP_STEP
            if overlaps(segments, clip_start, clip_end):
                title = f"{self._camera_name(device, camera)} {day_str} {clip_start:%H:%M}"
                if clip_events := events_in(events, clip_start, clip_end):
                    title += f" · {self._event_title(clip_events)}"
                children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=f"PLAY|{entry_id}|{camera_id}|{int(clip_start.timestamp())}",
                        media_class=MediaClass.VIDEO,
                        media_content_type=MediaType.VIDEO,
                        title=title,
                        can_play=True,
                        can_expand=False,
                    )
                )
            clip_start = clip_end
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"HOUR|{entry_id}|{camera_id}|{day_str}|{hour}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PLAYLIST,
            title=f"{self._camera_name(device, camera)} {day_str} {hour_start:%H:%M} - {hour_end:%H:%M}",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.VIDEO,
        )
