"""Tests for actions."""

from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import httpx
import pytest
from pytest_homeassistant.common import MockConfigEntry
import respx

from custom_components.hikvision_local.const import (
    ACTION_ISAPI_REQUEST,
    ACTION_REBOOT,
    ATTR_CONFIG_ENTRY_ID,
    DOMAIN,
)
from tests.conftest import TEST_HOST


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_reboot_action(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test sending reboot request on reboot action."""

    mock_config_entry = init_integration

    url = f"{TEST_HOST}/ISAPI/System/reboot"
    endpoint = respx.put(url).respond()

    await hass.services.async_call(
        DOMAIN,
        ACTION_REBOOT,
        {ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id},
        blocking=True,
    )

    assert endpoint.called


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
@pytest.mark.parametrize(
    ("action", "data"),
    [(ACTION_REBOOT, {}), (ACTION_ISAPI_REQUEST, {"method": "GET", "path": "System/deviceInfo"})],
)
async def test_action_unknown_entry(hass: HomeAssistant, init_integration: MockConfigEntry, action, data) -> None:
    """An unknown config entry ID gives a readable error, not an exception."""

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            action,
            {ATTR_CONFIG_ENTRY_ID: "missing", **data},
            blocking=True,
            return_response=action == ACTION_ISAPI_REQUEST,
        )
    assert err.value.translation_key == "entry_not_found"


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_isapi_request_entry_not_loaded(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """isapi_request on an entry that isn't loaded gives a readable error."""

    await hass.config_entries.async_unload(init_integration.entry_id)
    assert init_integration.state is ConfigEntryState.NOT_LOADED

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            ACTION_ISAPI_REQUEST,
            {ATTR_CONFIG_ENTRY_ID: init_integration.entry_id, "method": "GET", "path": "System/deviceInfo"},
            blocking=True,
            return_response=True,
        )
    assert err.value.translation_key == "entry_not_loaded"
    assert err.value.translation_placeholders["state"] == "not_loaded"


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_reboot_entry_not_loaded(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """reboot works on an entry that isn't loaded, using its saved settings."""

    await hass.config_entries.async_unload(init_integration.entry_id)
    endpoint = respx.put(f"{TEST_HOST}/ISAPI/System/reboot").respond()

    await hass.services.async_call(
        DOMAIN, ACTION_REBOOT, {ATTR_CONFIG_ENTRY_ID: init_integration.entry_id}, blocking=True
    )

    assert endpoint.called


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_reboot_cannot_connect(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """reboot reports a device it can't reach as a readable error."""

    respx.put(f"{TEST_HOST}/ISAPI/System/reboot").mock(side_effect=httpx.ConnectTimeout("timed out"))

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN, ACTION_REBOOT, {ATTR_CONFIG_ENTRY_ID: init_integration.entry_id}, blocking=True
        )
    assert err.value.translation_key == "cannot_connect"


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_reboot_entry_setup_retry(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """reboot reaches a device whose setup keeps retrying."""

    entry = MockConfigEntry(domain=DOMAIN, data=init_integration.data, state=ConfigEntryState.SETUP_RETRY)
    entry.add_to_hass(hass)
    endpoint = respx.put(f"{TEST_HOST}/ISAPI/System/reboot").respond()

    await hass.services.async_call(DOMAIN, ACTION_REBOOT, {ATTR_CONFIG_ENTRY_ID: entry.entry_id}, blocking=True)

    assert endpoint.called


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
@pytest.mark.parametrize(
    ("state", "disabled_by"),
    [(ConfigEntryState.MIGRATION_ERROR, None), (ConfigEntryState.NOT_LOADED, ConfigEntryDisabler.USER)],
)
async def test_reboot_refuses_entries(
    hass: HomeAssistant, init_integration: MockConfigEntry, state, disabled_by
) -> None:
    """reboot doesn't connect for disabled entries or ones whose settings failed to migrate."""

    entry = MockConfigEntry(domain=DOMAIN, data={}, state=state, disabled_by=disabled_by)
    entry.add_to_hass(hass)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, ACTION_REBOOT, {ATTR_CONFIG_ENTRY_ID: entry.entry_id}, blocking=True)
    assert err.value.translation_key == "entry_not_loaded"


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_action_entry_of_another_integration(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """A config entry of another integration is reported as not found."""

    entry = MockConfigEntry(domain="other")
    entry.add_to_hass(hass)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, ACTION_REBOOT, {ATTR_CONFIG_ENTRY_ID: entry.entry_id}, blocking=True)
    assert err.value.translation_key == "entry_not_found"
