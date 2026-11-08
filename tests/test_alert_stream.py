"""Test reading events from the device's alert stream."""

import asyncio
import logging
from unittest.mock import patch

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify
import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import respx

from custom_components.hikvision_local.const import (
    CONF_ALARM_SERVER_HOST,
    CONF_ALERT_STREAM,
    CONF_SET_ALARM_SERVER,
    DOMAIN,
)
from custom_components.hikvision_local.isapi import ISAPIClient, ISAPIUnauthorizedError
from custom_components.hikvision_local.isapi.utils import AlertStreamSplitter
from tests.conftest import TEST_CONFIG, TEST_HOST, load_fixture

ALERT_STREAM_URL = f"{TEST_HOST}/ISAPI/Event/notification/alertStream"
MOTION_SENSOR = "binary_sensor.ds_2cd2386g2_iu00000000aawrj00000000_1_motiondetection"
TEST_CONFIG_ALERT_STREAM = {
    **TEST_CONFIG,
    CONF_SET_ALARM_SERVER: True,
    CONF_ALARM_SERVER_HOST: "http://1.0.0.11:8123",
    CONF_ALERT_STREAM: True,
}
HEARTBEAT = """<?xml version="1.0" encoding="UTF-8"?>
<EventNotificationAlert version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
<ipAddress>1.0.0.255</ipAddress>
<channelID>1</channelID>
<dateTime>2026-11-07T18:00:00+01:00</dateTime>
<activePostCount>0</activePostCount>
<eventType>videoloss</eventType>
<eventState>inactive</eventState>
<eventDescription>videoloss alarm</eventDescription>
</EventNotificationAlert>"""


def multipart(*parts: tuple[str, bytes | str]) -> bytes:
    """Build an alert stream body the way devices send it."""
    body = b""
    for content_type, content in parts:
        data = content.encode() if isinstance(content, str) else content
        body += f"--boundary\r\nContent-Type: {content_type}\r\nContent-Length: {len(data)}\r\n\r\n".encode()
        body += data + b"\r\n"
    return body


def alert_stream_response(*alerts: str) -> httpx.Response:
    """Return an alert stream response with the given alerts."""
    return httpx.Response(
        200,
        headers={"Content-Type": "multipart/mixed; boundary=boundary"},
        content=multipart(*[('application/xml; charset="UTF-8"', xml) for xml in alerts]),
    )


async def wait_for(condition) -> None:
    """Let the alert stream task run until the condition holds."""
    for _ in range(1000):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not met")


def test_splitter_finds_alerts_across_chunks() -> None:
    """Alerts cut anywhere by chunk boundaries come out whole; pictures between them are dropped."""
    vmd = load_fixture("ISAPI/EventNotificationAlert", "vmd")
    body = multipart(
        ("application/xml", HEARTBEAT),
        ("image/jpeg", b"\xff\xd8 picture \xff\xd9"),
        ("application/xml", vmd),
    )
    for size in (1, 7, 64, len(body)):
        splitter = AlertStreamSplitter()
        alerts = []
        for start in range(0, len(body), size):
            alerts += splitter.feed(body[start : start + size])
        assert [alert.split("<eventType>")[1].split("<")[0] for alert in alerts] == ["videoloss", "VMD"]
        assert alerts[0].startswith("<EventNotificationAlert") and alerts[0].endswith("</EventNotificationAlert>")


def test_splitter_drops_oversized_alert() -> None:
    """A start tag without an end doesn't grow the buffer without limit."""
    splitter = AlertStreamSplitter()
    assert splitter.feed(b"<EventNotificationAlert>" + b"x" * (2 * 1024 * 1024)) == []
    assert splitter.feed(HEARTBEAT.encode()) != []


@respx.mock
async def test_client_alert_stream(mock_isapi: ISAPIClient) -> None:
    """The client yields the XML of each alert in the stream."""
    respx.get(ALERT_STREAM_URL).mock(return_value=alert_stream_response(HEARTBEAT, HEARTBEAT))
    mock_isapi._session = httpx.AsyncClient()

    alerts = [xml async for xml in mock_isapi.alert_stream()]

    assert len(alerts) == 2
    assert ISAPIClient.parse_event_notification(alerts[0]).event_state == "inactive"


@respx.mock
async def test_client_alert_stream_unauthorized(mock_isapi: ISAPIClient) -> None:
    """A 401 from the stream raises the client's unauthorized error."""
    respx.get(ALERT_STREAM_URL).respond(status_code=401)
    mock_isapi._session = httpx.AsyncClient()

    with pytest.raises(ISAPIUnauthorizedError):
        [xml async for xml in mock_isapi.alert_stream()]


@pytest.mark.parametrize("mock_config_entry", [TEST_CONFIG_ALERT_STREAM], indirect=True)
@pytest.mark.parametrize("init_integration", [("DS-2CD2386G2-IU", True)], indirect=True)
async def test_alerts_from_stream(
    hass: HomeAssistant, respx_mock, init_integration: MockConfigEntry, mock_config_entry: MockConfigEntry
) -> None:
    """With the alert stream on, alerts from it set sensors, and the notifications host isn't touched."""
    vmd = load_fixture("ISAPI/EventNotificationAlert", "vmd")
    route = respx.get(ALERT_STREAM_URL).mock(return_value=alert_stream_response(vmd, HEARTBEAT))

    with patch("custom_components.hikvision_local.notifications.ALERT_STREAM_RETRY_FIRST", 3600):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        serial_no = mock_config_entry.runtime_data.device_info.serial_no
        motion = registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"binary_sensor.{slugify(serial_no.lower())}_1_motiondetection"
        )
        last_event = registry.async_get_entity_id("sensor", DOMAIN, f"{serial_no}_last_alert")
        await wait_for(lambda: hass.states.get(motion).state == STATE_ON)
        await wait_for(lambda: route.called and route.calls.last.response.is_stream_consumed)

        # The heartbeat came after the motion alert, but doesn't count as the last event.
        assert hass.states.get(last_event).attributes["event_id"] == "motiondetection"
        # The integration doesn't set the notifications host, nor ask the user to.
        assert not [call for call in respx_mock.calls if call.request.method != "GET"]
        assert not mock_config_entry.runtime_data.control_alarm_server_host

        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.parametrize("mock_config_entry", [TEST_CONFIG_ALERT_STREAM], indirect=True)
@pytest.mark.parametrize("init_integration", [("DS-2CD2386G2-IU", True)], indirect=True)
async def test_alert_stream_reconnects(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unavailable stream is logged once, and the integration reconnects."""
    caplog.set_level(logging.INFO, logger="custom_components.hikvision_local")
    vmd = load_fixture("ISAPI/EventNotificationAlert", "vmd")
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise httpx.ConnectError("All connection attempts failed")
        return alert_stream_response(vmd)

    respx.get(ALERT_STREAM_URL).mock(side_effect=respond)

    with patch("custom_components.hikvision_local.notifications.ALERT_STREAM_RETRY_FIRST", 0):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        await wait_for(lambda: calls >= 4)

        unavailable = [r for r in caplog.records if "is unavailable" in r.getMessage()]
        assert len(unavailable) == 1
        assert unavailable[0].levelno == logging.WARNING
        assert any("is available again" in r.getMessage() for r in caplog.records)

        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_no_alert_stream_by_default(hass: HomeAssistant, respx_mock, init_integration: MockConfigEntry) -> None:
    """Without the option, the integration doesn't open the alert stream."""
    assert not init_integration.runtime_data.alert_stream_enabled
    assert not [call for call in respx_mock.calls if call.request.url.path.endswith("alertStream")]
    last_event = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{init_integration.runtime_data.device_info.serial_no}_last_alert"
    )
    assert hass.states.get(last_event).state == STATE_UNKNOWN
    assert hass.states.get(MOTION_SENSOR).state == STATE_OFF


@pytest.mark.parametrize("mock_config_entry", [TEST_CONFIG_ALERT_STREAM], indirect=True)
@pytest.mark.parametrize("init_integration", [("DS-2CD2386G2-IU", True)], indirect=True)
async def test_alert_stream_stops_for_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_config_entry: MockConfigEntry
) -> None:
    """Rejected credentials start reauthentication once and stop the stream, so the user isn't locked."""
    route = respx.get(ALERT_STREAM_URL).respond(status_code=401)

    with (
        patch("custom_components.hikvision_local.notifications.ALERT_STREAM_RETRY_FIRST", 0),
        patch("custom_components.hikvision_local.hikvision_device.REAUTH_AFTER_UNAUTHORIZED", 0),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        await wait_for(lambda: hass.config_entries.flow.async_progress_by_handler(DOMAIN))
        for _ in range(100):
            await asyncio.sleep(0)

        assert route.call_count == 2
        flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert [flow["context"]["source"] for flow in flows] == ["reauth"]

        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.parametrize("mock_config_entry", [TEST_CONFIG_ALERT_STREAM], indirect=True)
@pytest.mark.parametrize("init_integration", [("DS-2CD2386G2-IU", True)], indirect=True)
async def test_alert_stream_stops_when_reauth_is_open(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_config_entry: MockConfigEntry
) -> None:
    """The stream stops on a 401 when reauthentication was already started elsewhere."""
    route = respx.get(ALERT_STREAM_URL).respond(status_code=401)

    with patch("custom_components.hikvision_local.notifications.ALERT_STREAM_RETRY_FIRST", 0):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        mock_config_entry.async_start_reauth(hass)
        await hass.async_block_till_done()
        calls = route.call_count
        for _ in range(100):
            await asyncio.sleep(0)

        # At most the attempt in flight when reauth started, then no more.
        assert route.call_count <= calls + 1

        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.parametrize("mock_config_entry", [TEST_CONFIG_ALERT_STREAM], indirect=True)
@pytest.mark.parametrize("init_integration", [("DS-2CD2386G2-IU", True)], indirect=True)
async def test_alert_stream_alerts_clear_unauthorized(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_config_entry: MockConfigEntry
) -> None:
    """Alerts arriving on the stream show the credentials work."""

    async def body():
        # Set after the stream opened, which clears it too, so only the alert can clear it.
        mock_config_entry.runtime_data.unauthorized_since = 1.0
        yield multipart(('application/xml; charset="UTF-8"', HEARTBEAT))

    respx.get(ALERT_STREAM_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, headers={"Content-Type": "multipart/mixed; boundary=boundary"}, content=body()
        )
    )

    with patch("custom_components.hikvision_local.notifications.ALERT_STREAM_RETRY_FIRST", 3600):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        device = mock_config_entry.runtime_data
        await wait_for(lambda: device.unauthorized_since is None)

        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
