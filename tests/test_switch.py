"""Tests for switch platform."""

import json

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
import homeassistant.helpers.entity_registry as er
import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import respx
import xmltodict

from custom_components.hikvision_local.hikvision_device import HikvisionDevice
from custom_components.hikvision_local.isapi import EventInfo
from custom_components.hikvision_local.isapi.const import EVENT_IO
from tests.conftest import TEST_HOST


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_event_switch_state(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test switch state."""

    for entity_id, state in [
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_1_videoloss", STATE_ON),
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_1_fielddetection", STATE_OFF),
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_1_linedetection", STATE_ON),
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_1_scenechangedetection", STATE_OFF),
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_2_motiondetection", STATE_OFF),
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_2_videoloss", STATE_ON),
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_2_fielddetection", STATE_OFF),
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_2_linedetection", STATE_ON),
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_2_regionentrance", STATE_ON),
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_2_scenechangedetection", STATE_OFF),
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_3_videoloss", STATE_ON),
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_3_fielddetection", STATE_ON),
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_1_alarm_output", STATE_OFF),
        ("switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_holiday_mode", STATE_OFF),
    ]:
        assert (switch := hass.states.get(entity_id))
        assert switch.state == state

    entity_registry = er.async_get(hass)
    for entity_id in [
        "switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_2_regionexiting",
        "switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_3_motiondetection",
        "switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_3_linedetection",
    ]:
        switch_entity = entity_registry.async_get(entity_id)
        assert switch_entity.disabled


@pytest.mark.parametrize("init_integration", ["DS-2CD2T86G2-ISU"], indirect=True)
async def test_event_switch_state_of_a_camera(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test switch state of a camera."""

    for entity_id, state in [
        ("switch.ds_2cd2t86g2_isu_sl00000000aawrae0000000_1_fielddetection", STATE_ON),
        ("switch.ds_2cd2t86g2_isu_sl00000000aawrae0000000_1_scenechangedetection", STATE_ON),
        ("switch.ds_2cd2t86g2_isu_sl00000000aawrae0000000_1_io", STATE_OFF),
        ("switch.ds_2cd2t86g2_isu_sl00000000aawrae0000000_1_alarm_output", STATE_OFF),
    ]:
        assert (switch := hass.states.get(entity_id))
        assert switch.state == state


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_event_switch_payload(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test event switch."""

    entity_id = "switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_1_videoloss"
    assert (switch := hass.states.get(entity_id))
    assert switch.state == STATE_ON

    def update_side_effect(request, route):
        payload = '<?xml version="1.0" encoding="utf-8"?>\n<VideoLoss version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema"><enabled>false</enabled></VideoLoss>'
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    url = f"{TEST_HOST}/ISAPI/ContentMgmt/InputProxy/channels/1/video/videoLoss"
    endpoint = respx.put(url).mock(side_effect=update_side_effect)

    # do not call if already on
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert endpoint.called is False

    # switch to off
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert endpoint.called


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_alarm_output_switch(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test alarm output switch."""

    port_no = 1
    entity_id = f"switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_{port_no}_alarm_output"
    assert (switch := hass.states.get(entity_id))
    assert switch.state == STATE_OFF

    url = f"{TEST_HOST}/ISAPI/System/IO/outputs/{port_no}/trigger"
    endpoint = respx.put(url)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )

    assert endpoint.called


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-7616NI-K2", "DS-2CD2146G2-ISU"], indirect=True)
async def test_alarm_input_switch_state_url(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test alarm output switch."""

    device: HikvisionDevice = init_integration.runtime_data
    for e in device.events_info:
        if e.id == EVENT_IO:
            if e.io_port_id < 100:
                assert e.url == f"System/IO/inputs/{e.io_port_id}"
            else:
                assert e.is_proxy
                assert e.url == f"ContentMgmt/IOProxy/inputs/{e.io_port_id}"


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-7616NI-K2"], indirect=True)
async def test_nvr_event_switch_state_url(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test NVR event switch."""

    device: HikvisionDevice = init_integration.runtime_data
    for e in device.cameras[0].events_info:
        if e.id == "motiondetection":
            assert e.url == f"ContentMgmt/InputProxy/channels/{e.channel_id}/video/motionDetection"
        if e.id == "fielddetection":
            assert e.url == f"Smart/FieldDetection/{e.channel_id}"


@pytest.mark.parametrize("init_integration", ["iDS-7204HUHI-M1"], indirect=True)
async def test_dvr_event_switch_state_url(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test DVR event switch."""

    device: HikvisionDevice = init_integration.runtime_data
    for e in device.cameras[0].events_info:
        if e.id == "motiondetection":
            assert e.url == f"System/Video/inputs/channels/{e.channel_id}/motionDetection"
        if e.id == "fielddetection":
            assert e.url == f"Smart/FieldDetection/{e.channel_id}"


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_ipc_event_switch_state_url(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test IPC event switch."""

    device: HikvisionDevice = init_integration.runtime_data
    for e in device.cameras[0].events_info:
        if e.id == "motiondetection":
            assert e.url == f"System/Video/inputs/channels/{e.channel_id}/motionDetection"
        if e.id == "fielddetection":
            assert e.url == f"Smart/FieldDetection/{e.channel_id}"


@pytest.mark.parametrize("init_integration", ["DS-2SE4C425MWG-E-26"], indirect=True)
async def test_ipc_multichannel_event_switch(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test IPC two-channels events."""

    device: HikvisionDevice = init_integration.runtime_data

    assert device.capabilities.is_multi_channel
    assert len(device.cameras[0].events_info) == 2
    assert len(device.cameras[1].events_info) == 4

    switch_entities = [
        "switch.ds_2se4c425mwg_e_0000000000aawrfc0000000_1_motiondetection",
        "switch.ds_2se4c425mwg_e_0000000000aawrfc0000000_1_tamperdetection",
        "switch.ds_2se4c425mwg_e_0000000000aawrfc0000000_2_fielddetection",
        "switch.ds_2se4c425mwg_e_0000000000aawrfc0000000_2_linedetection",
        # 2 events are diabled
    ]
    for entity_id in switch_entities:
        assert hass.states.get(entity_id)


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_motion_detection_switch_preserves_polygon(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test that toggling motion detection preserves the user's drawn polygon.

    When regionType is "grid" the firmware recomputes RegionCoordinatesList as
    the bounding box of gridMap on every PUT, destroying the user's polygon.
    The Hikvision web UI works around this by sending regionType="region" with
    a zeroed gridMap, which makes the firmware treat RegionList as the source
    of truth. The integration must mirror that payload.
    """

    entity_id = "switch.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_2_motiondetection"
    assert (switch := hass.states.get(entity_id))
    assert switch.state == STATE_OFF

    def check_payload(request, route):
        payload = request.content.decode("utf-8")
        if "<regionType>region</regionType>" not in payload:
            raise AssertionError("PUT payload must use regionType=region")
        if "<regionType>grid</regionType>" in payload:
            raise AssertionError("PUT payload must not use regionType=grid")
        if "<gridMap>0" not in payload or any(
            c not in "0" for c in payload.split("<gridMap>")[1].split("</gridMap>")[0]
        ):
            raise AssertionError("PUT payload must contain a zeroed gridMap")
        if "RegionCoordinates" not in payload:
            raise AssertionError("PUT payload must contain RegionCoordinates")
        if "<enabled>true</enabled>" not in payload:
            raise AssertionError("PUT payload must contain enabled=true")
        return httpx.Response(200)

    url = f"{TEST_HOST}/ISAPI/ContentMgmt/InputProxy/channels/2/video/motionDetection"
    endpoint = respx.put(url).mock(side_effect=check_payload)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert endpoint.called


def load_device_response(model: str, endpoint: str) -> dict:
    """Return one recorded ISAPI response from a device fixture."""
    with open(f"tests/fixtures/devices/{model}.json") as f:
        return json.load(f)["data"]["ISAPI"][endpoint]["response"]


@respx.mock
@pytest.mark.parametrize(
    ("model", "endpoint"),
    [
        ("DS-7608NXI-I2", "ContentMgmt/InputProxy/channels/3/video/motionDetection"),
        ("DS-2CD2443G0-IW", "System/Video/inputs/channels/1/motionDetection"),
        ("DS-2CD2346G2-ISU", "System/Video/inputs/channels/1/motionDetection"),
    ],
)
async def test_motion_detection_toggle_keeps_grid_only_areas(mock_isapi, model: str, endpoint: str) -> None:
    """Without a usable polygon the grid is the motion area, so only enabled changes."""

    original = load_device_response(model, endpoint)
    current = original["MotionDetection"]["enabled"]
    new_state = "false" if current == "true" else "true"
    respx.get(f"{TEST_HOST}/ISAPI/{endpoint}").respond(text=xmltodict.unparse(original))
    put = respx.put(f"{TEST_HOST}/ISAPI/{endpoint}").respond(200)
    event = EventInfo(id="motiondetection", channel_id=1, io_port_id=0, url=endpoint)

    await mock_isapi.set_event_enabled_state(1, event, new_state == "true")

    sent = xmltodict.parse(put.calls.last.request.content)["MotionDetection"]
    expected = xmltodict.parse(xmltodict.unparse(original))["MotionDetection"]
    expected["enabled"] = new_state
    assert sent == expected
