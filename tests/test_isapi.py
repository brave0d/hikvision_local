"""Tests for specific ISAPI responses."""

from contextlib import suppress
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from custom_components.hikvision_local.const import ALARM_SERVER_PATH
from custom_components.hikvision_local.isapi import ISAPIClient, StorageInfo
from custom_components.hikvision_local.isapi.isapi import clean_int
from tests.conftest import load_fixture, mock_endpoint


@respx.mock
async def test_storage(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("ContentMgmt/Storage", "hdd1")
    storage_list = await isapi.get_storage_devices()
    assert len(storage_list) == 1
    assert storage_list[0] == StorageInfo(
        id=1,
        name="hdd1",
        type="SATA",
        status="ok",
        capacity=1907729,
        freespace=0,
        property="RW",
        ip="",
    )

    mock_endpoint("ContentMgmt/Storage", "hdd1_nas1")
    storage_list = await isapi.get_storage_devices()
    assert len(storage_list) == 2
    assert storage_list[0].type == "SATA"
    assert storage_list[1].type == "NFS"
    assert storage_list[1].ip != ""

    mock_endpoint("ContentMgmt/Storage", status_code=500)
    with suppress(Exception):
        storage_list = await isapi.get_storage_devices()
        assert len(storage_list) == 0


@respx.mock
async def test_notification_hosts(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    host_nvr = await isapi.get_alarm_server()

    mock_endpoint("Event/notification/httpHosts", "ipc_list")
    host_ipc = await isapi.get_alarm_server()

    assert host_nvr == host_ipc


@respx.mock
async def test_update_notification_hosts(mock_isapi):
    isapi = mock_isapi

    def update_side_effect(request, route):
        payload = load_fixture("ISAPI/Event.notification.httpHosts", "set_alarm_server_payload")
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    url = f"{isapi.host}/ISAPI/Event/notification/httpHosts"
    endpoint = respx.put(url).mock(side_effect=update_side_effect)
    await isapi.set_alarm_server("http://1.0.0.11:8123", ALARM_SERVER_PATH)

    assert endpoint.called


@respx.mock
async def test_update_notification_hosts_from_ipaddress_to_hostname(mock_isapi):
    isapi = mock_isapi

    def update_side_effect(request, route):
        payload = load_fixture("ISAPI/Event.notification.httpHosts", "set_alarm_server_outside_network_payload")
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    url = f"{isapi.host}/ISAPI/Event/notification/httpHosts"
    endpoint = respx.put(url).mock(side_effect=update_side_effect)
    await isapi.set_alarm_server("https://ha.hostname.domain", ALARM_SERVER_PATH)

    assert endpoint.called


@pytest.mark.parametrize(
    ("value", "expected"), [("1", 1), (3, 3), ("I-1", 1), ("D12", 12), (None, 0), ("", 0), ("abc", 0)]
)
def test_clean_int_reads_alphanumeric_channel_ids(value, expected):
    """Hybrid DVRs report channel IDs like "I-1"; the digits are the channel number."""
    assert clean_int(value) == expected


def test_parse_event_notification_with_alphanumeric_channel_id():
    """Event alerts from hybrid DVRs carry channel IDs like "I-2"."""
    xml = """<EventNotificationAlert version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">
    <ipAddress>1.0.0.10</ipAddress>
    <channelID>I-2</channelID>
    <eventType>VMD</eventType>
    <eventState>active</eventState>
    </EventNotificationAlert>"""

    alert = ISAPIClient.parse_event_notification(xml)

    assert alert.channel_id == 2
    assert alert.event_id == "motiondetection"


async def test_analog_inputs_count_only_physical_inputs(mock_isapi):
    """Only video inputs with an input port are counted as analog inputs."""
    mock_isapi.request = AsyncMock(
        return_value={
            "VideoInputChannelList": {
                "VideoInputChannel": [
                    {"id": "1", "inputPort": "1"},
                    {"id": "2", "inputPort": "2"},
                    {"id": "33"},
                ]
            }
        }
    )
    assert await mock_isapi.get_analog_inputs_count() == 2

    mock_isapi.request = AsyncMock(side_effect=httpx.ConnectError("unreachable"))
    assert await mock_isapi.get_analog_inputs_count() == 0


async def test_single_channel_event_fallback(mock_isapi):
    """Test event discovery when a single-channel camera omits Event/triggers."""
    responses = {
        "Event/triggers": {},
        "Event/channels/capabilities": {
            "ChannelEventCapList": {
                "ChannelEventCap": {
                    "eventType": {"@opt": "fielddetection"},
                    "channelID": "1",
                }
            }
        },
        "Event/triggers/fielddetection-1": {
            "EventTrigger": {
                "eventType": "fielddetection",
                "videoInputChannelID": "1",
                "EventTriggerNotificationList": {
                    "EventTriggerNotification": {
                        "notificationMethod": "center",
                    }
                },
            }
        },
    }
    mock_isapi.request = AsyncMock(side_effect=lambda _, endpoint: responses[endpoint])

    events = await mock_isapi.get_supported_events({})

    assert mock_isapi.capabilities.is_multi_channel is False
    assert [(event.id, event.channel_id, event.notifications) for event in events] == [
        ("fielddetection", 1, ["center"])
    ]
    assert [awaited.args[1] for awaited in mock_isapi.request.await_args_list] == [
        "Event/triggers",
        "Event/channels/capabilities",
        "Event/triggers/fielddetection-1",
    ]


async def test_event_fallback_ignores_capabilities_without_event_type(mock_isapi):
    """A capability entry without eventType doesn't stop event discovery."""
    responses = {
        "Event/triggers": {},
        "Event/channels/capabilities": {"ChannelEventCapList": {"ChannelEventCap": [{"channelID": "1"}]}},
    }
    mock_isapi.request = AsyncMock(side_effect=lambda _, endpoint: responses.get(endpoint, {}))

    assert await mock_isapi.get_supported_events({}) == []
