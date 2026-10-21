"""Tests for actions."""

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import respx

from custom_components.hikvision_local.const import (
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
