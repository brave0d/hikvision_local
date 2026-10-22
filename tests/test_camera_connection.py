"""Tests for the connection status of IP cameras connected to an NVR."""

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_local.const import CAMERAS_STATUS, DOMAIN, EVENTS_COORDINATOR


def connection_entity_ids(hass: HomeAssistant, entry: MockConfigEntry) -> dict[int, str]:
    """Return connection sensor entity IDs by camera ID."""
    registry = er.async_get(hass)
    serial_no = entry.runtime_data.device_info.serial_no.lower()
    return {
        camera.id: registry.async_get_entity_id(
            BINARY_SENSOR_DOMAIN, DOMAIN, slugify(f"{serial_no}_{camera.id}_online")
        )
        for camera in entry.runtime_data.cameras
    }


@pytest.mark.parametrize("init_integration", ["DS-7604NXI-K1"], indirect=True)
async def test_camera_connection_sensors(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Each IP camera on the NVR gets a diagnostic connection sensor."""

    entity_ids = connection_entity_ids(hass, init_integration)
    assert list(entity_ids) == [1, 2, 3, 4]

    registry = er.async_get(hass)
    for camera_id, entity_id in entity_ids.items():
        assert entity_id
        assert registry.async_get(entity_id).entity_category == EntityCategory.DIAGNOSTIC
        assert (sensor := hass.states.get(entity_id))
        assert sensor.state == STATE_ON
        assert sensor.attributes["device_class"] == "connectivity"
        assert sensor.attributes["detect_result"] == "connect"
        camera = init_integration.runtime_data.get_camera_by_id(camera_id)
        assert sensor.attributes["ip_address"] == camera.ip_addr


@pytest.mark.parametrize("init_integration", ["DS-7604NXI-K1"], indirect=True)
async def test_camera_connection_sensor_updates(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """The sensor follows the NVR's status, and is unavailable without one."""

    entity_ids = connection_entity_ids(hass, init_integration)
    coordinator = init_integration.runtime_data.coordinators[EVENTS_COORDINATOR]
    status = {
        1: {"online": True, "detect_result": "connect"},
        2: {"online": False, "detect_result": "netUnreachable"},
        3: {"online": True, "detect_result": "connect"},
    }
    coordinator.async_set_updated_data({**coordinator.data, CAMERAS_STATUS: status})
    await hass.async_block_till_done()

    assert hass.states.get(entity_ids[1]).state == STATE_ON
    assert (sensor := hass.states.get(entity_ids[2]))
    assert sensor.state == STATE_OFF
    assert sensor.attributes["detect_result"] == "netUnreachable"
    assert hass.states.get(entity_ids[4]).state == STATE_UNAVAILABLE


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_no_connection_sensor_on_camera(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """A camera added directly has no connection sensor."""

    assert init_integration.runtime_data.capabilities.support_channel_status is False
    assert not any(entity_id for entity_id in connection_entity_ids(hass, init_integration).values())
