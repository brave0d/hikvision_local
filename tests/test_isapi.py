"""Tests for specific ISAPI responses."""

from contextlib import suppress

import httpx
import respx

from custom_components.hikvision_local.const import ALARM_SERVER_PATH
from custom_components.hikvision_local.isapi import StorageInfo
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


@respx.mock
async def test_request_retries_once_with_fresh_auth_after_401(mock_isapi):
    """An expired Digest nonce gives a 401; the request is retried once with new credentials state."""
    isapi = mock_isapi
    challenge = {"WWW-Authenticate": 'Digest realm="testrealm", qop="auth", nonce="abc", opaque="799d5"'}
    route = respx.get(f"{isapi.host}/ISAPI/System/time").mock(
        side_effect=[
            httpx.Response(401, headers=challenge),
            httpx.Response(401, headers=challenge),
            httpx.Response(401, headers=challenge),
            httpx.Response(200, text="<Time><timeMode>NTP</timeMode></Time>"),
        ]
    )

    result = await isapi.request("GET", "System/time")

    assert result == {"Time": {"timeMode": "NTP"}}
    assert route.call_count == 4
    assert isinstance(isapi._auth_method, httpx.DigestAuth)
