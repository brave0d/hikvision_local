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
