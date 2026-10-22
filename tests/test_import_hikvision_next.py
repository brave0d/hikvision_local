"""Tests for setting up devices from hikvision_next entries."""

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER, ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_local.const import CONF_ALARM_SERVER_HOST, CONF_SET_ALARM_SERVER, DOMAIN, OLD_DOMAIN
from custom_components.hikvision_local.isapi import ISAPIUnauthorizedError
from tests.conftest import TEST_CONFIG, TEST_HOST

NVR_SERIAL = "DS-7608NXI-I0/0P/S0000000000CCRRJ00000000WCVU"
HA_URL = "http://1.0.0.1:8123"


def old_entry(hass: HomeAssistant, **data) -> MockConfigEntry:
    """Add a hikvision_next entry for the NVR."""
    entry = MockConfigEntry(
        domain=OLD_DOMAIN,
        title="Old NVR",
        unique_id=NVR_SERIAL,
        data={
            **TEST_CONFIG,
            CONF_SET_ALARM_SERVER: True,
            CONF_ALARM_SERVER_HOST: HA_URL,
            "host": f"{TEST_HOST}/",
            **data,
        },
        version=3,
    )
    entry.add_to_hass(hass)
    return entry


async def test_no_menu_without_hikvision_next(hass: HomeAssistant) -> None:
    """Without hikvision_next entries the flow goes straight to the form."""

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_menu_offers_import_and_manual_setup(hass: HomeAssistant) -> None:
    """With a hikvision_next entry the flow asks whether to import it."""
    old_entry(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] == FlowResultType.MENU
    assert result["menu_options"] == ["import_hikvision_next", "manual"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "manual"})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


@pytest.mark.parametrize("mock_isapi_device", ["DS-7608NXI-I2"], indirect=True)
async def test_import_from_hikvision_next(hass: HomeAssistant, mock_isapi_device) -> None:
    """The chosen device is set up with its hikvision_next settings."""
    entry = old_entry(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "import_hikvision_next"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "import_hikvision_next"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"entry_id": entry.entry_id})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Old NVR"
    assert result["data"] == {**TEST_CONFIG, CONF_SET_ALARM_SERVER: True, CONF_ALARM_SERVER_HOST: HA_URL}
    assert result["result"].unique_id == NVR_SERIAL

    # Imported devices aren't offered again.
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] == FlowResultType.FORM


@patch("custom_components.hikvision_local.isapi.ISAPIClient.get_device_info")
async def test_import_shows_connection_errors(get_device_info_mock, hass: HomeAssistant, mock_isapi) -> None:
    """Credentials that no longer work show the usual error."""
    entry = old_entry(hass)
    get_device_info_mock.side_effect = ISAPIUnauthorizedError(
        type("Error", (), {"request": type("Request", (), {"url": TEST_HOST})(), "response": None})()
    )

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "import_hikvision_next"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"entry_id": entry.entry_id})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_issue_asks_to_remove_hikvision_next(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """A device set up in both integrations gets a repair issue until the old entry is removed."""
    entry = old_entry(hass)
    device = init_integration.runtime_data
    issue_id = f"remove_hikvision_next_{init_integration.entry_id}"

    device.check_old_integration()
    assert (issue := ir.async_get(hass).async_get_issue(DOMAIN, issue_id))
    assert issue.translation_key == "remove_hikvision_next"

    await hass.config_entries.async_remove(entry.entry_id)
    device.check_old_integration()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_reauth_shows_form_with_hikvision_next_installed(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Reauthentication isn't sent through the import menu."""
    MockConfigEntry(domain=OLD_DOMAIN, title="Camera", unique_id="other", data=TEST_CONFIG).add_to_hass(hass)

    result = await init_integration.start_reauth_flow(hass)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


@pytest.mark.parametrize("mock_isapi_device", ["DS-7608NXI-I2"], indirect=True)
async def test_import_without_alarm_server_address(hass: HomeAssistant, mock_isapi_device) -> None:
    """An entry that sets the notifications host without an address doesn't push an empty one."""
    entry = old_entry(hass, alarm_server="")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "import_hikvision_next"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"entry_id": entry.entry_id})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SET_ALARM_SERVER] is False


async def test_import_of_incomplete_entry_aborts(hass: HomeAssistant) -> None:
    """An entry without credentials can't be imported."""
    entry = old_entry(hass, password="")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "import_hikvision_next"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"entry_id": entry.entry_id})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_incomplete"


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_no_issue_for_disabled_hikvision_next_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A disabled hikvision_next entry doesn't poll the device, so it doesn't raise the issue."""
    entry = old_entry(hass)
    await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)

    init_integration.runtime_data.check_old_integration()

    issue_id = f"remove_hikvision_next_{init_integration.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
