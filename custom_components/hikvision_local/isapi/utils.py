import datetime
from functools import reduce
import json
from typing import Any

import xmltodict


def parse_isapi_response(response, present="dict"):
    """Parse Hikvision results."""
    if isinstance(response, (list,)):
        result = "".join(response)
    elif isinstance(response, str):
        result = response
    else:
        result = response.text

    if present is None or present == "dict":
        if isinstance(response, (list,)):
            events = []
            for event in response:
                e = json.loads(json.dumps(xmltodict.parse(event)))
                events.append(e)
            return events
        return json.loads(json.dumps(xmltodict.parse(result)))
    else:
        return result


def str_to_bool(value: str) -> bool:
    """Convert text to boolean."""
    if value:
        return value.lower() == "true"
    return False


def bool_to_str(value: bool) -> str:
    """Convert boolean to 'true' or 'false'."""
    return "true" if value else "false"


def get_stream_id(channel_id: str, stream_type: int = 1) -> int:
    """Get stream id."""
    return int(channel_id) * 100 + stream_type


def deep_get(dictionary: dict, path: str, default: Any = None) -> Any:
    """Get safely nested dictionary attribute."""
    result = reduce(
        lambda d, key: d.get(key, default) if isinstance(d, dict) else default,
        path.split("."),
        dictionary,
    )
    if default == [] and not isinstance(result, list):
        return [result]

    return result


def format_isapi_time(value: datetime.datetime, compact: bool = False) -> str:
    """Format a time as ISAPI UTC time, e.g. 2024-10-08T23:50:00Z (compact: 20241008T235000Z)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    value = value.astimezone(datetime.UTC)
    return value.strftime("%Y%m%dT%H%M%SZ" if compact else "%Y-%m-%dT%H:%M:%SZ")


def parse_isapi_time(value: str | None) -> datetime.datetime | None:
    """Parse ISAPI time to an aware datetime; times without offset are UTC."""
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC)


ALERT_START = b"<EventNotificationAlert"
ALERT_END = b"</EventNotificationAlert>"
MAX_ALERT_BYTES = 1024 * 1024


class AlertStreamSplitter:
    """Cut EventNotificationAlert documents out of the bytes of an alert stream.

    Alerts are found by their XML root element, not by the multipart boundaries, which firmware
    versions format differently. Anything between alerts, such as pictures, is dropped.
    """

    def __init__(self) -> None:
        """Initialize."""
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[str]:
        """Add a chunk of the stream and return the alerts it completes."""
        self._buffer += chunk
        alerts = []
        while True:
            start = self._buffer.find(ALERT_START)
            if start == -1:
                # Keep a start tag that is cut in two by the chunk boundary.
                del self._buffer[: max(0, len(self._buffer) - len(ALERT_START) + 1)]
                return alerts
            end = self._buffer.find(ALERT_END, start)
            if end == -1:
                del self._buffer[:start]
                if len(self._buffer) > MAX_ALERT_BYTES:
                    self._buffer.clear()
                return alerts
            end += len(ALERT_END)
            alerts.append(self._buffer[start:end].decode("utf-8", errors="replace"))
            del self._buffer[:end]
