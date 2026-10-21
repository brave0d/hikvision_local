"""Tests for the diagnostics output."""

import json
import re

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_local.diagnostics import anonymise_data, async_get_config_entry_diagnostics


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-2CD2386G2-IU"], indirect=True)
async def test_diagnostics_are_anonymised(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Device identifiers and user-entered names are replaced in the diagnostics."""

    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)
    text = json.dumps(diagnostics["ISAPI"], default=str)
    device_info = diagnostics["ISAPI"]["System/deviceInfo"]["response"]["DeviceInfo"]

    assert device_info["deviceID"] == "00000000-0000-0000-0000-000000000000"
    assert device_info["deviceName"] == "Device"
    assert device_info["macAddress"].startswith("02:00:00:")
    for mac in re.findall(r'"macAddress": "([^"]+)"', text):
        assert int(mac[:2], 16) & 0b11 == 0b10, mac


def test_anonymise_data_replaces_secrets_and_channel_names() -> None:
    """Salts, channel UUIDs and channel names are replaced; other names are kept."""

    data = {
        "DeviceCap": {"SecurityCap": {"salt": "44a95dfe"}},
        "InputProxyChannel": {"id": "1", "name": "Front door", "devIndex": "27689955-a9e3-4b5b-97d3-16ae441a7e42"},
        "StreamingChannel": {"id": "101", "channelName": "101"},
        "EventTrigger": {"eventDescription": "Motion Detection"},
        "DeviceInfo": {"shortSerialNumber": "K54308652", "deviceLocation": "garden"},
    }

    result = anonymise_data(data)

    assert result["DeviceCap"]["SecurityCap"]["salt"] == "00000000"
    assert result["InputProxyChannel"]["name"] == "Camera"
    assert result["InputProxyChannel"]["devIndex"] == "00000000-0000-0000-0000-000000000000"
    assert result["StreamingChannel"]["channelName"] == "101"
    assert result["EventTrigger"]["eventDescription"] == "Motion Detection"
    assert result["DeviceInfo"]["shortSerialNumber"] == "K00000000"
    assert result["DeviceInfo"]["deviceLocation"] == "location"
