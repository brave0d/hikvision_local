"""Tests for the diagnostics output."""

import json
import re

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_local.diagnostics import anonymise_data, async_get_config_entry_diagnostics


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-7616NI-Q2", "DS-2CD2386G2-IU"], indirect=True)
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
    assert set(re.findall(r'"userName": "([^"]*)"', text)) <= {"", "user"}
    assert set(re.findall(r'"path": "([^"]*)"', text)) <= {"", "/share"}
    assert set(re.findall(r'"deviceID": "([^"]*)"', text)) <= {"00000000-0000-0000-0000-000000000000"}


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


def test_anonymise_data_keeps_replacements_per_field() -> None:
    """The same text in different fields gets each field's own replacement."""

    result = anonymise_data({"deviceName": "nvr", "hostName": "nvr", "userName": "nvr"})

    assert result == {"deviceName": "Device", "hostName": "hostname", "userName": "user"}


def test_anonymise_data_handles_lists_and_addresses() -> None:
    """Channel lists, user names, NAS shares and IPv6 addresses are replaced."""

    data = {
        "InputProxyChannelList": {
            "InputProxyChannel": [
                {"id": "1", "name": "Garage", "sourceInputPortDescriptor": {"userName": "admin"}},
                {"id": "2", "name": "Garden", "sourceInputPortDescriptor": {"userName": ""}},
            ]
        },
        "nasList": {"nas": {"path": "/volume2/Cameras", "nasName": "home-nas", "hddSerialNumber": "ABC1D2E3"}},
        "IPAddress": {"ipv6Address": "fe80::4219:b6ff:fe12:3456", "ipAddress": "192.168.1.10"},
    }

    result = anonymise_data(data)

    channels = result["InputProxyChannelList"]["InputProxyChannel"]
    assert [channel["name"] for channel in channels] == ["Camera", "Camera"]
    assert channels[0]["sourceInputPortDescriptor"]["userName"] == "user"
    assert channels[1]["sourceInputPortDescriptor"]["userName"] == ""
    assert result["nasList"]["nas"] == {"path": "/share", "nasName": "nas1", "hddSerialNumber": "ABC0D0E0"}
    assert result["IPAddress"]["ipv6Address"] == "fd00::1"
    assert result["IPAddress"]["ipAddress"].startswith("1.0.0.")


def test_anonymise_data_replaces_custom_audio_urls() -> None:
    """A custom audio alarm URL can contain credentials, so it is replaced."""
    result = anonymise_data({"AudioAlarm": {"customAudioURL": "https://user:pass@nas.local/siren.wav", "audioID": 1}})

    assert result["AudioAlarm"] == {"customAudioURL": "https://example.com/audio.wav", "audioID": 1}
