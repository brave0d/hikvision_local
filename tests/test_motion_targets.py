"""Tests for the person and vehicle sensors of motion detection."""

from http import HTTPStatus

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_local.const import DOMAIN
from custom_components.hikvision_local.isapi.isapi import split_targets
from custom_components.hikvision_local.notifications import EventNotificationsView
from tests.test_notifications import mock_event_notification


def target_entity_ids(hass: HomeAssistant, entry: MockConfigEntry, camera_id: int = 1) -> dict[str, str | None]:
    """Return the camera's motion, person and vehicle sensor entity IDs."""
    camera = entry.runtime_data.get_camera_by_id(camera_id)
    event = next(event for event in camera.events_info if event.id == "motiondetection")
    registry = er.async_get(hass)
    return {
        name: registry.async_get_entity_id(BINARY_SENSOR_DOMAIN, DOMAIN, f"binary_sensor.{event.unique_id}{suffix}")
        for name, suffix in (("motion", ""), ("human", "_human"), ("vehicle", "_vehicle"))
    }


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_person_and_vehicle_sensors(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """A camera that tells humans and vehicles apart gets a sensor for each."""

    entity_ids = target_entity_ids(hass, init_integration)

    assert entity_ids["human"]
    assert entity_ids["vehicle"]
    assert hass.states.get(entity_ids["human"]).state == STATE_OFF
    assert hass.states.get(entity_ids["human"]).attributes["device_class"] == "motion"


@pytest.mark.parametrize("init_integration", ["DS-2CD2146G2-ISU"], indirect=True)
async def test_only_configured_targets_get_sensors(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """A camera set to detect humans only gets a person sensor only."""

    entity_ids = target_entity_ids(hass, init_integration)

    assert entity_ids["human"]
    assert entity_ids["vehicle"] is None


@pytest.mark.parametrize("init_integration", ["DS-2CD2346G2-ISU"], indirect=True)
async def test_no_target_sensors_without_target_type(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """A camera without target detection has no person or vehicle sensors."""

    entity_ids = target_entity_ids(hass, init_integration)

    assert entity_ids["human"] is None
    assert entity_ids["vehicle"] is None


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_target_sensors_follow_alerts(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """A human alert turns the person sensor on, a plain one leaves it, an inactive one turns all off."""

    entity_ids = target_entity_ids(hass, init_integration)
    view = EventNotificationsView(hass)

    def states() -> dict[str, str]:
        return {name: hass.states.get(entity_id).state for name, entity_id in entity_ids.items()}

    assert (await view.post(mock_event_notification("vmd_human"))).status == HTTPStatus.OK
    assert states() == {"motion": STATE_ON, "human": STATE_ON, "vehicle": STATE_OFF}

    assert (await view.post(mock_event_notification("vmd"))).status == HTTPStatus.OK
    assert states() == {"motion": STATE_ON, "human": STATE_ON, "vehicle": STATE_OFF}

    assert (await view.post(mock_event_notification("vmd_inactive"))).status == HTTPStatus.OK
    assert states() == {"motion": STATE_OFF, "human": STATE_OFF, "vehicle": STATE_OFF}


@pytest.mark.parametrize("init_integration", ["DS-7604NXI-K1"], indirect=True)
async def test_target_sensors_follow_motion_event_enabled_state(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """On an NVR, the sensors are disabled like the motion sensor when alerts don't reach Home Assistant."""

    registry = er.async_get(hass)
    for camera_id in (1, 2, 3, 4):
        entity_ids = target_entity_ids(hass, init_integration, camera_id)
        motion = registry.async_get(entity_ids["motion"])
        for target in ("human", "vehicle"):
            assert (entry := registry.async_get(entity_ids[target]))
            assert entry.disabled_by == motion.disabled_by


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_person_sensor_works_without_motion_sensor(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The person sensor follows alerts when the motion sensor is disabled."""

    entity_ids = target_entity_ids(hass, init_integration)
    er.async_get(hass).async_update_entity(entity_ids["motion"], disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    assert hass.states.get(entity_ids["motion"]) is None

    view = EventNotificationsView(hass)
    assert (await view.post(mock_event_notification("vmd_human"))).status == HTTPStatus.OK

    assert hass.states.get(entity_ids["human"]).state == STATE_ON


@pytest.mark.parametrize(
    ("target_type", "expected"),
    [("human,vehicle", ["human", "vehicle"]), ("Human, Vehicle", ["human", "vehicle"]), ("", []), (None, [])],
)
def test_split_targets(target_type, expected) -> None:
    """targetType values are split into lower-case targets."""
    assert split_targets(target_type) == expected
