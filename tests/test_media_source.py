"""Tests for recordings media source."""

import datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from homeassistant.components.media_source import MediaSourceItem, Unresolvable
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_local.const import DOMAIN
from custom_components.hikvision_local.isapi import RecordingSegment
from custom_components.hikvision_local.isapi.utils import format_isapi_time, parse_isapi_time
from custom_components.hikvision_local.media_source import async_get_media_source, overlaps
from tests.conftest import TEST_HOST

UTC = datetime.UTC


def search_result(spans: list[tuple[str, str]], status: str = "OK") -> str:
    """Build CMSearchResult XML."""
    items = "".join(
        "<searchMatchItem><sourceID>{0}</sourceID><trackID>101</trackID>"
        f"<timeSpan><startTime>{start}</startTime><endTime>{end}</endTime></timeSpan>"
        "<mediaSegmentDescriptor><contentType>video</contentType><codecType>H.265</codecType>"
        "<playbackURI>rtsp://1.0.0.255/Streaming/tracks/101/</playbackURI></mediaSegmentDescriptor>"
        "</searchMatchItem>"
        for start, end in spans
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<CMSearchResult version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">'
        f"<searchID>X</searchID><responseStatus>true</responseStatus><responseStatusStrg>{status}</responseStatusStrg>"
        f"<numOfMatches>{len(spans)}</numOfMatches><matchList>{items}</matchList></CMSearchResult>"
    )


DAILY_DISTRIBUTION = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<trackDailyDistribution version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema"><dayList>'
    "<day><id>1</id><dayOfMonth>1</dayOfMonth><record>false</record></day>"
    "<day><id>2</id><dayOfMonth>2</dayOfMonth><record>true</record><recordType>time</recordType></day>"
    "<day><id>3</id><dayOfMonth>3</dayOfMonth><record>true</record><recordType>time</recordType></day>"
    "</dayList></trackDailyDistribution>"
)


def test_isapi_time() -> None:
    """Test ISAPI time helpers."""
    value = datetime.datetime(2024, 10, 9, 0, 14, 22, tzinfo=UTC)
    assert format_isapi_time(value) == "2024-10-09T00:14:22Z"
    assert format_isapi_time(value, compact=True) == "20241009T001422Z"
    local = datetime.datetime(2024, 10, 9, 2, 14, 22, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    assert format_isapi_time(local) == "2024-10-09T00:14:22Z"
    assert parse_isapi_time("2024-10-09T00:14:22Z") == value
    assert parse_isapi_time("2024-10-09T00:14:22") == value
    assert parse_isapi_time("2024-10-09T02:14:22+02:00") == value
    assert parse_isapi_time("") is None
    assert parse_isapi_time("bad") is None


def test_overlaps() -> None:
    """Test segment overlap detection."""
    seg = RecordingSegment(
        track_id=101,
        start=datetime.datetime(2024, 10, 9, 10, 5, tzinfo=UTC),
        end=datetime.datetime(2024, 10, 9, 10, 25, tzinfo=UTC),
    )
    assert overlaps([seg], datetime.datetime(2024, 10, 9, 10, 0, tzinfo=UTC), datetime.datetime(2024, 10, 9, 10, 10, tzinfo=UTC))
    assert overlaps([seg], datetime.datetime(2024, 10, 9, 10, 20, tzinfo=UTC), datetime.datetime(2024, 10, 9, 10, 30, tzinfo=UTC))
    assert not overlaps([seg], datetime.datetime(2024, 10, 9, 10, 25, tzinfo=UTC), datetime.datetime(2024, 10, 9, 10, 35, tzinfo=UTC))
    assert not overlaps([], datetime.datetime(2024, 10, 9, 10, 0, tzinfo=UTC), datetime.datetime(2024, 10, 9, 11, 0, tzinfo=UTC))


@respx.mock
async def test_search_recordings_paginated(mock_isapi) -> None:
    """Test recordings search follows MORE pages and sorts segments."""
    isapi = mock_isapi
    route = respx.post(f"{TEST_HOST}/ISAPI/ContentMgmt/search").mock(
        side_effect=[
            httpx.Response(200, text=search_result([("2024-10-09T01:00:00Z", "2024-10-09T01:25:00Z")], "MORE")),
            httpx.Response(200, text=search_result([("2024-10-09T00:14:00Z", "2024-10-09T01:00:00Z")], "OK")),
        ]
    )
    segments = await isapi.search_recordings(
        101,
        datetime.datetime(2024, 10, 9, 0, 0, tzinfo=UTC),
        datetime.datetime(2024, 10, 9, 2, 0, tzinfo=UTC),
    )
    assert route.call_count == 2
    assert b"<searchResultPostion>1</searchResultPostion>" in route.calls[1].request.content
    assert b"<startTime>2024-10-09T00:00:00Z</startTime>" in route.calls[0].request.content
    assert [s.start for s in segments] == [
        datetime.datetime(2024, 10, 9, 0, 14, tzinfo=UTC),
        datetime.datetime(2024, 10, 9, 1, 0, tzinfo=UTC),
    ]
    assert segments[1].end == datetime.datetime(2024, 10, 9, 1, 25, tzinfo=UTC)


@respx.mock
async def test_search_recordings_empty(mock_isapi) -> None:
    """Test recordings search without matches."""
    isapi = mock_isapi
    respx.post(f"{TEST_HOST}/ISAPI/ContentMgmt/search").respond(text=search_result([], "NO MATCHES"))
    segments = await isapi.search_recordings(
        101, datetime.datetime(2024, 10, 9, tzinfo=UTC), datetime.datetime(2024, 10, 10, tzinfo=UTC)
    )
    assert segments == []


@respx.mock
async def test_recording_days(mock_isapi) -> None:
    """Test daily distribution parsing."""
    isapi = mock_isapi
    route = respx.post(f"{TEST_HOST}/ISAPI/ContentMgmt/record/tracks/101/dailyDistribution").respond(
        text=DAILY_DISTRIBUTION
    )
    assert await isapi.get_recording_days(101, 2024, 10) == [2, 3]
    assert b"<monthOfYear>10</monthOfYear>" in route.calls[0].request.content


def test_playback_source(mock_isapi) -> None:
    """Test RTSP playback URL."""
    isapi = mock_isapi
    isapi.device_info.ip_address = "1.0.0.255"
    url = isapi.get_playback_source(
        101,
        datetime.datetime(2024, 10, 9, 10, 0, tzinfo=UTC),
        datetime.datetime(2024, 10, 9, 11, 0, tzinfo=UTC),
    )
    assert url == (
        "rtsp://u1:%2A%2A%2A@1.0.0.255:554/Streaming/tracks/101/"
        "?starttime=20241009T100000Z&endtime=20241009T110000Z"
    )


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_browse_and_resolve(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test browsing cameras, days, hours and clips, and resolving playback."""
    entry = init_integration
    device = entry.runtime_data
    camera = device.cameras[0]
    source = await async_get_media_source(hass)

    root = await source.async_browse_media(MediaSourceItem(hass, DOMAIN, "", None))
    assert [child.title for child in root.children] == [camera.name]
    cam_id = root.children[0].identifier
    assert cam_id == f"CAM|{entry.entry_id}|{camera.id}"

    respx.post(f"{TEST_HOST}/ISAPI/ContentMgmt/record/tracks/101/dailyDistribution").respond(
        text=DAILY_DISTRIBUTION
    )
    today = dt_util.now().date()
    days = await source.async_browse_media(MediaSourceItem(hass, DOMAIN, cam_id, None))
    titles = [child.title for child in days.children]
    assert titles == sorted(titles, reverse=True)
    assert all(datetime.date.fromisoformat(title) <= today for title in titles)

    day = datetime.date(2024, 10, 9)
    day_start = dt_util.start_of_local_day(day)
    seg_start = day_start + datetime.timedelta(hours=10, minutes=5)
    seg_end = day_start + datetime.timedelta(hours=10, minutes=25)
    respx.post(f"{TEST_HOST}/ISAPI/ContentMgmt/search").respond(
        text=search_result([(format_isapi_time(seg_start), format_isapi_time(seg_end))])
    )
    hours = await source.async_browse_media(
        MediaSourceItem(hass, DOMAIN, f"DAY|{entry.entry_id}|{camera.id}|{day.isoformat()}", None)
    )
    assert [child.title for child in hours.children] == ["10:00 - 11:00"]

    clips = await source.async_browse_media(MediaSourceItem(hass, DOMAIN, hours.children[0].identifier, None))
    assert [child.title.split()[-1] for child in clips.children] == ["10:00", "10:10", "10:20"]
    assert all(child.can_play for child in clips.children)

    stream = MagicMock()
    stream.endpoint_url.return_value = "/api/hls/abc/master_playlist.m3u8"
    with patch("custom_components.hikvision_local.media_source.create_stream", return_value=stream) as create:
        play = await source.async_resolve_media(MediaSourceItem(hass, DOMAIN, clips.children[1].identifier, None))
    assert play.url == "/api/hls/abc/playlist.m3u8"
    assert play.mime_type == "application/x-mpegURL"
    rtsp_url = create.call_args.args[1]
    clip_start = day_start + datetime.timedelta(hours=10, minutes=10)
    assert f"starttime={format_isapi_time(clip_start, compact=True)}" in rtsp_url
    assert f"endtime={format_isapi_time(clip_start + datetime.timedelta(hours=1), compact=True)}" in rtsp_url
    stream.add_provider.assert_called_once_with("hls", timeout=600)

    with pytest.raises(Unresolvable):
        await source.async_resolve_media(MediaSourceItem(hass, DOMAIN, "FOO|bar", None))
    with pytest.raises(Unresolvable):
        await source.async_browse_media(MediaSourceItem(hass, DOMAIN, "CAM|missing|1", None))
