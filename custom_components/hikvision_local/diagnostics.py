"""Diagnostics support for Wiser."""

from __future__ import annotations

import inspect
import ipaddress
import json
import random
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntry
from httpx import HTTPStatusError

from . import HikvisionConfigEntry
from .isapi import ISAPIForbiddenError, ISAPIUnauthorizedError
from .isapi.const import GET, STREAM_TYPE


def anonymise_mac(orignal: str):
    """Anonymise MAC address with a random, locally administered unicast address."""

    mac = [0x02, 0x00, 0x00] + [random.randint(0x00, 0xFF) for _ in range(3)]
    return ":".join(f"{x:02x}" for x in mac)


def anonymise_ip(orignal: str):
    """Anonymise IPv4 and IPv6 addresses; a host name in an address field becomes a placeholder."""
    if not orignal or orignal[0] == "0" or orignal == "::":
        return orignal
    try:
        address = ipaddress.ip_address(orignal)
    except ValueError:
        return "hostname"
    if address.version == 6:
        # Link-local IPv6 addresses embed the MAC address, so every IPv6 address is replaced.
        return "fd00::1"
    return f"1.0.0.{random.randint(0x00, 0xFF)}"


def anonymise_url(orignal: str):
    """Anonymise the host of an absolute URL, dropping credentials and the query; relative URLs stay."""
    parts = urlsplit(orignal)
    if not parts.hostname:
        return orignal
    # Same replacement as the device's ipAddress field, so the two still match.
    host = anon_map.setdefault((anonymise_ip, parts.hostname), anonymise_ip(parts.hostname))
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def anonymise_serial(orignal: str):
    """Anonymise serial number."""

    if len(orignal) > 32:
        return orignal[:12] + "".join("0" if c.isdigit() else c for c in orignal[12:])
    return "".join("0" if c.isdigit() else c for c in orignal)


def anonymise_uuid(orignal: str):
    """Anonymise a device or channel UUID."""
    return "00000000-0000-0000-0000-000000000000"


def anonymise_digits(orignal: str):
    """Replace every digit, keeping the format."""
    return "".join("0" if c.isdigit() else c for c in orignal)


def anonymise_secret(orignal: str):
    """Replace a per-device secret such as a password salt."""
    return "0" * len(orignal)


def anonymise_text(replacement: str):
    """Replace user-entered text, keeping empty values empty."""
    return lambda orignal: replacement if orignal else orignal


ANON_KEYS = {
    "ip_address": anonymise_ip,
    "ip_addr": anonymise_ip,
    "ipAddress": anonymise_ip,
    "mac_address": anonymise_mac,
    "macAddress": anonymise_mac,
    "serial_no": anonymise_serial,
    "serialNumber": anonymise_serial,
    "subSerialNumber": anonymise_serial,
    "unique_id": anonymise_serial,
    "deviceID": anonymise_uuid,
    "devIndex": anonymise_uuid,
    "shortSerialNumber": anonymise_digits,
    "salt": anonymise_secret,
    "deviceName": anonymise_text("Device"),
    "deviceLocation": anonymise_text("location"),
    "hddName": anonymise_text("hdd1"),
    "hostName": anonymise_text("hostname"),
    "ipv6Address": anonymise_ip,
    "ipV6Address": anonymise_ip,
    "destIPAddress": anonymise_ip,
    "userName": anonymise_text("user"),
    "nasName": anonymise_text("nas1"),
    "path": anonymise_text("/share"),
    "hddSerialNumber": anonymise_digits,
    # A custom audio alarm URL can contain a host and credentials.
    "customAudioURL": anonymise_text("https://example.com/audio.wav"),
    "customAudioName": anonymise_text("custom"),
    # Channel status reports each camera's address as a URL.
    "url": anonymise_url,
}

# Channel names are user-entered, so they are replaced inside these elements only.
CHANNEL_NAME_KEYS = ("name", "channelName")
CHANNEL_ELEMENTS = ("StreamingChannel", "VideoInputChannel", "InputProxyChannel")

anon_map = {}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: HikvisionConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return await _async_get_diagnostics(hass, entry)


@callback
async def _async_get_diagnostics(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    device: DeviceEntry | None = None,
) -> dict[str, Any]:
    device = entry.runtime_data
    # Keep replacements consistent within one diagnostics file only, and don't hold raw values afterwards.
    anon_map.clear()

    # Get info set
    info = {}

    # Add camera info
    # info.update({"Cameras": [to_json(camera) for camera in isapi.cameras]})

    # ISAPI responses
    responses = {}
    endpoints = [
        "System/deviceInfo",
        "System/capabilities",
        "System/IO/inputs/1/status",
        "System/IO/outputs/1/status",
        "System/Holidays",
        "System/Video/inputs/channels",
        "ContentMgmt/InputProxy/channels",
        "ContentMgmt/InputProxy/channels/status",
        "ContentMgmt/Storage",
        "Security/adminAccesses",
        "Event/triggers",
        "Event/channels/capabilities",
        "Event/triggers/scenechangedetection-1",
        "Event/notification/httpHosts",
        "Streaming/channels",
        "Event/triggers/notifications/AudioAlarm/capabilities?format=json",
        "Event/triggers/notifications/AudioAlarm?format=json",
    ]

    for endpoint in endpoints:
        responses[endpoint] = await get_isapi_data(device, endpoint)

    # channels
    for camera in device.cameras:
        for stream_type_id in STREAM_TYPE:
            endpoint = f"Streaming/channels/{camera.id}0{stream_type_id}"
            responses[endpoint] = await get_isapi_data(device, endpoint)

    # event states
    for camera in device.cameras:
        for event in camera.events_info:
            responses[event.url] = await get_isapi_data(device, event.url)

    info["ISAPI"] = responses
    return info


async def get_isapi_data(isapi, endpoint: str) -> dict:
    """Get data from ISAPI."""
    entry = {}
    try:
        if "format=json" in endpoint:
            response = json.loads(await isapi.request(GET, endpoint, present="json"))
        else:
            response = await isapi.request(GET, endpoint)
        entry["response"] = anonymise_data(response)
    except (HTTPStatusError, ISAPIUnauthorizedError, ISAPIForbiddenError) as ex:
        entry["status_code"] = ex.response.status_code
    except Exception as ex:  # noqa: BLE001
        entry["error"] = ex
    return entry


def to_json(obj):
    """Convert object to json."""
    result = json.dumps(obj, cls=ObjectEncoder, sort_keys=True, indent=2)
    result = json.loads(result)
    return anonymise_data(result)


def anonymise_data(data, parent: str = ""):
    """Anonymise sensitive data."""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key in ANON_KEYS and value is not None:
                anon_fn = ANON_KEYS[key]
                # Keyed by anonymiser too: the same text can be a name in one field and a host in another.
                cache_key = (anon_fn, value)
                if cache_key not in anon_map:
                    anon_map[cache_key] = anon_fn(value)
                result[key] = anon_map[cache_key]
            elif key in CHANNEL_NAME_KEYS and parent in CHANNEL_ELEMENTS and isinstance(value, str):
                result[key] = value if value.isdigit() else "Camera"
            else:
                result[key] = anonymise_data(value, key)
        return result
    if isinstance(data, list):
        result = []
        for item in data:
            result.append(anonymise_data(item, parent))
        return result
    return data


class ObjectEncoder(json.JSONEncoder):
    """Class to encode object to json."""

    def default(self, o):
        """Implement encoding logic."""
        if hasattr(o, "to_json"):
            return self.default(o.to_json())

        if hasattr(o, "__dict__"):
            data = {
                key: value
                for key, value in inspect.getmembers(o)
                if not key.startswith("__")
                and not inspect.isabstract(value)
                and not inspect.isbuiltin(value)
                and not inspect.isfunction(value)
                and not inspect.isgenerator(value)
                and not inspect.isgeneratorfunction(value)
                and not inspect.ismethod(value)
                and not inspect.ismethoddescriptor(value)
                and not inspect.isroutine(value)
            }
            return self.default(data)
        return o
