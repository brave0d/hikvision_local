"""Tests for 401 answers from a device whose credentials are valid."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant
import httpx
import pytest
from pytest_homeassistant.common import MockConfigEntry
import respx

from custom_components.hikvision_local.const import DOMAIN
from custom_components.hikvision_local.hikvision_device import REAUTH_AFTER_UNAUTHORIZED, HikvisionDevice
from custom_components.hikvision_local.isapi import ISAPIClient, ISAPIUnauthorizedError
from tests.conftest import TEST_HOST

STATUS_URL = f"{TEST_HOST}/ISAPI/System/status"


def unauthorized_error() -> ISAPIUnauthorizedError:
    request = httpx.Request("GET", STATUS_URL)
    response = httpx.Response(401, request=request)
    return ISAPIUnauthorizedError(httpx.HTTPStatusError("401", request=request, response=response))


def reauth_flows(hass: HomeAssistant) -> list:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == "reauth"
    ]


@respx.mock
async def test_request_success_clears_unauthorized(mock_isapi: ISAPIClient) -> None:
    """A successful request shows the credentials work."""
    respx.get(STATUS_URL).respond(text="<status/>")
    mock_isapi._session = httpx.AsyncClient()
    mock_isapi.unauthorized_since = 1.0

    await mock_isapi.request("GET", "System/status")

    assert mock_isapi.unauthorized_since is None


@respx.mock
async def test_request_401_is_not_retried(mock_isapi: ISAPIClient) -> None:
    """A 401 is raised after one attempt, so wrong credentials don't count twice toward a lockout."""
    route = respx.get(STATUS_URL).respond(status_code=401)
    mock_isapi._session = httpx.AsyncClient()

    with pytest.raises(ISAPIUnauthorizedError):
        await mock_isapi.request("GET", "System/status")
    assert route.call_count == 1


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_unauthorized_endpoints_dont_start_reauth(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Several 401s in one update, as from endpoints the device refuses, don't ask for the credentials."""
    device: HikvisionDevice = init_integration.runtime_data

    with patch("custom_components.hikvision_local.hikvision_device.time.monotonic", return_value=1000.0):
        device.handle_exception(unauthorized_error(), "holiday mode")
        device.handle_exception(unauthorized_error(), "notifications host")
    await hass.async_block_till_done()

    assert device.unauthorized_since == 1000.0
    assert not reauth_flows(hass)


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_success_clears_unauthorized(
    hass: HomeAssistant, init_integration: MockConfigEntry, respx_mock: respx.MockRouter
) -> None:
    """A successful request in between means the credentials work, so the wait starts over."""
    device: HikvisionDevice = init_integration.runtime_data
    respx_mock.get(STATUS_URL).respond(text="<status/>")

    with patch("custom_components.hikvision_local.hikvision_device.time.monotonic", return_value=1000.0):
        device.handle_exception(unauthorized_error())
    await device.request("GET", "System/status")
    with patch(
        "custom_components.hikvision_local.hikvision_device.time.monotonic",
        return_value=1000.0 + REAUTH_AFTER_UNAUTHORIZED + 1,
    ):
        device.handle_exception(unauthorized_error())
    await hass.async_block_till_done()

    assert not reauth_flows(hass)


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_persistent_unauthorized_starts_reauth(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """401s with no success for REAUTH_AFTER_UNAUTHORIZED seconds ask for the credentials again."""
    device: HikvisionDevice = init_integration.runtime_data

    with patch("custom_components.hikvision_local.hikvision_device.time.monotonic", return_value=1000.0):
        device.handle_exception(unauthorized_error())
    with patch(
        "custom_components.hikvision_local.hikvision_device.time.monotonic",
        return_value=1000.0 + REAUTH_AFTER_UNAUTHORIZED,
    ):
        device.handle_exception(unauthorized_error())
    await hass.async_block_till_done()

    assert len(reauth_flows(hass)) == 1
    assert device.unauthorized_since is None
