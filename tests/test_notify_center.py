"""Tests for the 'notify Home Assistant' (Notify Surveillance Center) switches."""

import json

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import respx
import xmltodict

from custom_components.hikvision_local.const import DOMAIN
from custom_components.hikvision_local.coordinator import notify_switch_unique_id
from tests.conftest import TEST_HOST

MODEL = "DS-2CD2346G2-ISU"


def camera_event(entry: MockConfigEntry, event_id: str):
    """Return the camera's event info."""
    return next(event for event in entry.runtime_data.cameras[0].events_info if event.id == event_id)


def notify_switch_id(hass: HomeAssistant, entry: MockConfigEntry, event_id: str) -> str:
    """Return the entity ID of an event's notify switch."""
    unique_id = notify_switch_unique_id(camera_event(entry, event_id))
    return er.async_get(hass).async_get_entity_id(SWITCH_DOMAIN, DOMAIN, unique_id)


def trigger_response(trigger_id: str) -> str:
    """Return the device's Event/triggers/<id> answer, taken from its Event/triggers list."""
    with open(f"tests/fixtures/devices/{MODEL}.json") as f:
        triggers = json.load(f)["data"]["ISAPI"]["Event/triggers"]["response"]
    triggers = (triggers.get("EventNotification") or triggers)["EventTriggerList"]["EventTrigger"]
    trigger = next(trigger for trigger in triggers if trigger["id"] == trigger_id)
    return xmltodict.unparse({"EventTrigger": trigger})


def sent_methods(request: httpx.Request) -> list[str]:
    """Return the notification methods in a PUT Event/triggers/<id> body."""
    trigger = xmltodict.parse(request.content)["EventTrigger"]
    notifications = (trigger.get("EventTriggerNotificationList") or {}).get("EventTriggerNotification", [])
    if isinstance(notifications, dict):
        notifications = [notifications]
    return [notify["notificationMethod"] for notify in notifications]


@pytest.mark.parametrize("init_integration", [MODEL], indirect=True)
async def test_notify_switches_state(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """The switch shows whether the event trigger has the 'Notify Surveillance Center' action."""

    motion_id = notify_switch_id(hass, init_integration, "motiondetection")
    intrusion_id = notify_switch_id(hass, init_integration, "fielddetection")

    assert er.async_get(hass).async_get(motion_id).entity_category == EntityCategory.CONFIG
    assert hass.states.get(motion_id).state == STATE_OFF
    assert hass.states.get(intrusion_id).state == STATE_ON


@pytest.mark.parametrize("init_integration", [MODEL], indirect=True)
async def test_notify_switch_turn_on_adds_center_and_enables_entities(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Turning the switch on adds the action, keeps the other actions and enables the event's entities."""

    registry = er.async_get(hass)
    event = camera_event(init_integration, "motiondetection")
    sensor_id = registry.async_get_entity_id("binary_sensor", DOMAIN, f"binary_sensor.{event.unique_id}")
    assert registry.async_get(sensor_id).disabled_by == er.RegistryEntryDisabler.INTEGRATION

    url = f"{TEST_HOST}/ISAPI/Event/triggers/VMD-1"
    respx.get(url).respond(text=trigger_response("VMD-1"))
    endpoint = respx.put(url).respond()

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: notify_switch_id(hass, init_integration, "motiondetection")},
        blocking=True,
    )

    assert endpoint.call_count == 1
    request = endpoint.calls.last.request
    assert sent_methods(request) == ["IO", "record", "center"]
    assert xmltodict.parse(request.content)["EventTrigger"]["eventDescription"] == "VMD Event trigger Information"
    assert "center" in event.notifications
    assert event.disabled is False
    assert registry.async_get(sensor_id).disabled_by is None


@pytest.mark.parametrize("init_integration", [MODEL], indirect=True)
async def test_notify_switch_turn_off_removes_center(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Turning the switch off removes only the 'Notify Surveillance Center' action."""

    url = f"{TEST_HOST}/ISAPI/Event/triggers/fielddetection-1"
    respx.get(url).respond(text=trigger_response("fielddetection-1"))
    endpoint = respx.put(url).respond()

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: notify_switch_id(hass, init_integration, "fielddetection")},
        blocking=True,
    )

    assert endpoint.call_count == 1
    assert sent_methods(endpoint.calls.last.request) == ["IO", "record", "FTP"]
    assert "center" not in camera_event(init_integration, "fielddetection").notifications


@pytest.mark.parametrize("init_integration", [MODEL], indirect=True)
async def test_notify_switch_does_not_write_unchanged_trigger(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """No write is sent when the trigger already has the requested action."""

    url = f"{TEST_HOST}/ISAPI/Event/triggers/fielddetection-1"
    respx.get(url).respond(text=trigger_response("fielddetection-1"))
    endpoint = respx.put(url).respond()

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: notify_switch_id(hass, init_integration, "fielddetection")},
        blocking=True,
    )

    assert not endpoint.called
