"""Tests for the PTZ actions."""

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import respx
import xmltodict

from custom_components.hikvision_local.const import ACTION_PTZ, ACTION_PTZ_MOVE, ACTION_PTZ_STOP, DOMAIN
from tests.conftest import TEST_HOST

CAMERA = "camera.ds_2se4c425mwg_e_0000000000aawrfc0000000_101"
PTZ_URL = f"{TEST_HOST}/ISAPI/PTZCtrl/channels/1/continuous"


def sent_commands(route) -> list[dict]:
    """Return the PTZData of every request sent to the route."""
    return [xmltodict.parse(call.request.content)["PTZData"] for call in route.calls]


@pytest.mark.parametrize("init_integration", ["DS-2SE4C425MWG-E-26"], indirect=True)
async def test_ptz_move_direction_stops_after_duration(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """A direction moves at half speed and a stop follows after the duration."""
    route = respx.put(PTZ_URL).respond(200)

    await hass.services.async_call(
        DOMAIN, ACTION_PTZ_MOVE, {"entity_id": CAMERA, "direction": "up_left", "duration": 10}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert sent_commands(route) == [{"pan": "-50", "tilt": "50", "zoom": "0"}, {"pan": "0", "tilt": "0", "zoom": "0"}]


@pytest.mark.parametrize("init_integration", ["DS-2SE4C425MWG-E-26"], indirect=True)
async def test_ptz_zoom_and_stop(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Zoom moves are stopped too; duration 0 keeps moving until ptz_stop."""
    route = respx.put(PTZ_URL).respond(200)

    await hass.services.async_call(
        DOMAIN, ACTION_PTZ_MOVE, {"entity_id": CAMERA, "zoom": 30, "duration": 0}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert sent_commands(route) == [{"pan": "0", "tilt": "0", "zoom": "30"}]

    await hass.services.async_call(DOMAIN, ACTION_PTZ_STOP, {"entity_id": CAMERA}, blocking=True)
    assert sent_commands(route)[-1] == {"pan": "0", "tilt": "0", "zoom": "0"}


@pytest.mark.parametrize("init_integration", ["DS-2SE4C425MWG-E-26"], indirect=True)
async def test_ptz_move_needs_a_movement(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Without a direction or speeds the action fails."""
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, ACTION_PTZ_MOVE, {"entity_id": CAMERA}, blocking=True)


@pytest.mark.parametrize("init_integration", ["DS-2SE4C425MWG-E-26"], indirect=True)
async def test_ptz_rejects_other_entities(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Only cameras of this integration can be moved."""
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, ACTION_PTZ_MOVE, {"entity_id": "camera.unknown", "direction": "up"}, blocking=True
        )


@pytest.mark.parametrize("init_integration", ["DS-2SE4C425MWG-E-26"], indirect=True)
async def test_onvif_style_ptz(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """The ONVIF-style action maps directions and speed, and Stop stops."""
    route = respx.put(PTZ_URL).respond(200)

    await hass.services.async_call(
        DOMAIN,
        ACTION_PTZ,
        {"entity_id": CAMERA, "pan": "RIGHT", "tilt": "DOWN", "speed": 0.7, "continuous_duration": 0},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN, ACTION_PTZ, {"entity_id": CAMERA, "zoom": "ZOOM_IN", "continuous_duration": 0}, blocking=True
    )
    await hass.services.async_call(DOMAIN, ACTION_PTZ, {"entity_id": CAMERA, "move_mode": "Stop"}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert sent_commands(route) == [
        {"pan": "70", "tilt": "-70", "zoom": "0"},
        {"pan": "0", "tilt": "0", "zoom": "50"},
        {"pan": "0", "tilt": "0", "zoom": "0"},
    ]


@pytest.mark.parametrize("init_integration", ["DS-2SE4C425MWG-E-26"], indirect=True)
async def test_newer_move_cancels_the_pending_stop(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """A move without a duration isn't stopped by the timer of an earlier move."""
    route = respx.put(PTZ_URL).respond(200)

    await hass.services.async_call(
        DOMAIN, ACTION_PTZ_MOVE, {"entity_id": CAMERA, "direction": "left", "duration": 20}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN, ACTION_PTZ_MOVE, {"entity_id": CAMERA, "direction": "right", "duration": 0}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert sent_commands(route) == [{"pan": "-50", "tilt": "0", "zoom": "0"}, {"pan": "50", "tilt": "0", "zoom": "0"}]


@pytest.mark.parametrize("init_integration", ["DS-2SE4C425MWG-E-26"], indirect=True)
async def test_ptz_targets_a_device(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """A device target moves the camera entities of that device (both channels of this camera)."""
    route = respx.put(PTZ_URL).respond(200)
    second_channel = respx.put(f"{TEST_HOST}/ISAPI/PTZCtrl/channels/2/continuous").respond(200)
    device_id = er.async_get(hass).async_get(CAMERA).device_id

    await hass.services.async_call(DOMAIN, ACTION_PTZ_STOP, {"device_id": device_id}, blocking=True)

    assert route.called
    assert second_channel.called


@pytest.mark.parametrize("init_integration", ["DS-2SE4C425MWG-E-26"], indirect=True)
async def test_onvif_style_goto_preset(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """GotoPreset moves the camera to the preset; without a preset it fails."""
    route = respx.put(f"{TEST_HOST}/ISAPI/PTZCtrl/channels/1/presets/3/goto").respond(200)

    await hass.services.async_call(
        DOMAIN, ACTION_PTZ, {"entity_id": CAMERA, "move_mode": "GotoPreset", "preset": 3}, blocking=True
    )
    assert route.called

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, ACTION_PTZ, {"entity_id": CAMERA, "move_mode": "GotoPreset"}, blocking=True
        )
