"""Tests for the checks that events reach Home Assistant."""

from http import HTTPStatus

from homeassistant.const import STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_local.const import ALARM_SERVER_PATH, DOMAIN
from custom_components.hikvision_local.isapi import AlarmServer
from custom_components.hikvision_local.notifications import EventNotificationsView
from tests.test_notifications import mock_event_notification


def alarm_server_issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    """Return the alarm server repair issue of the entry."""
    return ir.async_get(hass).async_get_issue(DOMAIN, f"alarm_server_{entry.entry_id}")


@pytest.mark.parametrize(
    ("init_integration", "address", "path"),
    [("DS-7604NXI-K1", "0.0.0.0", "/"), ("DS-2CD2386G2-IU", "1.0.0.159", "/api/hikvision")],
    indirect=["init_integration"],
)
async def test_issue_when_alarm_server_does_not_point_here(
    hass: HomeAssistant, init_integration: MockConfigEntry, address: str, path: str
) -> None:
    """An unset alarm server or another path (such as hikvision_next's) raises a repair issue."""

    assert (issue := alarm_server_issue(hass, init_integration))
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_key == "alarm_server_not_set"
    assert issue.translation_placeholders["address"] == address
    assert issue.translation_placeholders["path"] == path
    assert issue.translation_placeholders["expected_path"] == ALARM_SERVER_PATH


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_issue_cleared_when_alarm_server_is_fixed(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """The issue goes away when the device points at Home Assistant, and on unload."""

    device = init_integration.runtime_data
    device.check_alarm_server(AlarmServer("1.0.0.159", None, 8123, f"{ALARM_SERVER_PATH}/", "HTTP"))
    assert alarm_server_issue(hass, init_integration) is None

    device.check_alarm_server(AlarmServer("0.0.0.0", None, 80, "/", "HTTP"))
    assert alarm_server_issue(hass, init_integration)

    await hass.config_entries.async_unload(init_integration.entry_id)
    assert alarm_server_issue(hass, init_integration) is None


@pytest.mark.parametrize("init_integration", ["DS-7604NXI-K1"], indirect=True)
async def test_no_issue_when_integration_sets_alarm_server(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """When the integration manages the alarm server, it doesn't ask the user to set it."""

    device = init_integration.runtime_data
    device.control_alarm_server_host = True
    device.check_alarm_server(AlarmServer("0.0.0.0", None, 80, "/", "HTTP"))

    assert alarm_server_issue(hass, init_integration) is None


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_last_event_sensor(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """The sensor shows when the device last sent an alert, and which one."""

    registry = er.async_get(hass)
    serial_no = init_integration.runtime_data.device_info.serial_no
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{serial_no}_last_alert")
    assert registry.async_get(entity_id).entity_category == EntityCategory.DIAGNOSTIC
    assert hass.states.get(entity_id).state == STATE_UNKNOWN

    view = EventNotificationsView(hass)
    assert (await view.post(mock_event_notification("vmd"))).status == HTTPStatus.OK
    await hass.async_block_till_done()

    assert (state := hass.states.get(entity_id))
    assert dt_util.parse_datetime(state.state) is not None
    assert state.attributes["device_class"] == "timestamp"
    assert state.attributes["event_id"] == "motiondetection"
    assert state.attributes["channel_id"] == 1
