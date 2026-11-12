"""Tests for camera platform."""

from dataclasses import dataclass

from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.components.camera.helper import get_camera_from_entity_id
from homeassistant.const import STATE_IDLE
from homeassistant.core import HomeAssistant
import homeassistant.helpers.entity_registry as er
import httpx
import pytest
from pytest_homeassistant.common import MockConfigEntry
import respx

from custom_components.hikvision_local import camera as camera_platform
from custom_components.hikvision_local.hikvision_device import HikvisionDevice
from tests.conftest import TEST_HOST, load_fixture


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera initialization."""

    assert len(hass.states.async_entity_ids(CAMERA_DOMAIN)) == 3

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101"
    assert hass.states.get(entity_id)

    camera_entity = get_camera_from_entity_id(hass, entity_id)
    assert camera_entity.state == STATE_IDLE
    assert camera_entity.name == "garden"

    stream_url = await camera_entity.stream_source()
    assert stream_url == "rtsp://u1:%2A%2A%2A@1.0.0.255:10554/Streaming/channels/101"

    entity_registry = er.async_get(hass)
    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_102"
    camera_entity = entity_registry.async_get(entity_id)
    assert camera_entity.disabled
    assert camera_entity.original_name == "Sub-Stream"

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_104"
    camera_entity = entity_registry.async_get(entity_id)
    assert camera_entity.disabled
    assert camera_entity.original_name == "Transcoded Stream"


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera_snapshot(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera snapshot."""

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101"
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    image_url = f"{TEST_HOST}/ISAPI/Streaming/channels/101/picture"
    respx.get(image_url).respond(content=b"binary image data")
    image = await camera_entity.async_camera_image()
    assert image == b"binary image data"


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera_snapshot_http_error_page(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """An HTTP error page from the device isn't returned as an image."""

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101"
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    image_url = f"{TEST_HOST}/ISAPI/Streaming/channels/101/picture"
    route = respx.get(image_url).respond(
        status_code=401, headers={"Content-Type": "text/html"}, content=b"<html>Unauthorized</html>"
    )

    assert await camera_entity.async_camera_image() is None
    assert route.calls.last.request.extensions["timeout"]["read"] == init_integration.runtime_data.timeout


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera_snapshot_device_error(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera snapshot with 2 attempts."""

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101"
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    image_url = f"{TEST_HOST}/ISAPI/Streaming/channels/101/picture"
    route = respx.get(image_url)
    error_response = load_fixture("ISAPI/Streaming.channels.x0y.picture", "deviceError")
    route.side_effect = [
        httpx.Response(200, content=error_response),
        httpx.Response(200, content=error_response),
        httpx.Response(200, content=b"binary image data"),
    ]
    image = await camera_entity.async_camera_image()
    assert image == b"binary image data"


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7616NI-Q2"], indirect=True)
async def test_camera_snapshot_alternate_url(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera snapshot with alternate url."""

    entity_id = "camera.ds_7616ni_q2_00p0000000000ccrre00000000wcvu_101"
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    error_response = load_fixture("ISAPI/Streaming.channels.x0y.picture", "badXmlContent")
    image_url = f"{TEST_HOST}/ISAPI/Streaming/channels/101/picture"
    respx.get(image_url).respond(content=error_response)
    image_url = f"{TEST_HOST}/ISAPI/ContentMgmt/StreamingProxy/channels/101/picture"
    respx.get(image_url).respond(content=b"binary image data")
    image = await camera_entity.async_camera_image()
    assert image == b"binary image data"


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7616NI-Q2"], indirect=True)
async def test_camera_snapshot_not_supported(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test fallback to stream stills when the device does not support snapshots."""

    entity_id = "camera.ds_7616ni_q2_00p0000000000ccrre00000000wcvu_101"
    camera_entity = get_camera_from_entity_id(hass, entity_id)
    assert not camera_entity.use_stream_for_stills

    error_response = load_fixture("ISAPI/Streaming.channels.x0y.picture", "notSupport")
    image_url = f"{TEST_HOST}/ISAPI/Streaming/channels/101/picture"
    respx.get(image_url).respond(status_code=403, content=error_response)
    image_url = f"{TEST_HOST}/ISAPI/ContentMgmt/StreamingProxy/channels/101/picture"
    respx.get(image_url).respond(status_code=403, content=error_response)
    image = await camera_entity.async_camera_image()
    assert image is None
    assert camera_entity.use_stream_for_stills


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7616NI-Q2"], indirect=True)
async def test_camera_snapshot_not_supported_alternate_url(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test alternate url is tried before falling back to stream stills."""

    entity_id = "camera.ds_7616ni_q2_00p0000000000ccrre00000000wcvu_101"
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    error_response = load_fixture("ISAPI/Streaming.channels.x0y.picture", "notSupport")
    image_url = f"{TEST_HOST}/ISAPI/Streaming/channels/101/picture"
    respx.get(image_url).respond(status_code=403, content=error_response)
    image_url = f"{TEST_HOST}/ISAPI/ContentMgmt/StreamingProxy/channels/101/picture"
    respx.get(image_url).respond(content=b"binary image data")
    image = await camera_entity.async_camera_image()
    assert image == b"binary image data"
    assert not camera_entity.use_stream_for_stills


device_data = {
    "DS-7608NXI-I2": {
        "entity_id": "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101",
        "codec": "H.264",
        "width": "3840",
        "height": "2160",
        "rtsp_port": 10554,
    },
    "DS-7616NI-Q2": {
        "entity_id": "camera.ds_7616ni_q2_00p0000000000ccrre00000000wcvu_101",
        "codec": "H.265",
        "width": "2560",
        "height": "1440",
        "rtsp_port": 554,
    },
}


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-7616NI-Q2"], indirect=True)
async def test_camera_stream_info(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera snapshot with alternate url."""

    data = device_data[init_integration.title]
    entity_id = data["entity_id"]
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    assert camera_entity.stream_info.codec == data["codec"]
    assert camera_entity.stream_info.width == data["width"]
    assert camera_entity.stream_info.height == data["height"]

    stream_url = await camera_entity.stream_source()
    assert stream_url == f"rtsp://u1:%2A%2A%2A@1.0.0.255:{data['rtsp_port']}/Streaming/channels/101"


@pytest.mark.parametrize("init_integration", ["DS-2TD1228-2-QA"], indirect=True)
async def test_camera_multichannel(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    entry = init_integration

    device: HikvisionDevice = entry.runtime_data
    assert len(device.cameras) == 2  # video channel + thermal channel
    assert device.cameras[0].input_port == 1
    assert device.cameras[1].input_port == 2


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-7732NI-M4"], indirect=True)
async def test_nvr_with_onvif_cameras(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test proxy cameras with repeated serial no."""

    entry = init_integration
    device: HikvisionDevice = entry.runtime_data

    unique_serial_no = set()
    for camera in device.cameras:
        unique_serial_no.add(camera.serial_no)

    assert len(device.cameras) == len(unique_serial_no)


try:
    from homeassistant.components.camera import CameraEntityCapabilities
except ImportError:  # Home Assistant 2026.10: stand-in with the same shape

    @dataclass(frozen=True, kw_only=True)
    class CameraEntityCapabilities:
        two_way_audio: bool = False


@pytest.mark.parametrize(
    ("init_integration", "two_way_audio"),
    [
        ("DS-2CD2387G2-LSU", True),  # camera with a speaker
        ("DS-2SE4C425MWG-E-26", True),  # PTZ dome with a speaker
        ("DS-2TD1228-2-QA", True),  # thermal camera with two channels
        ("DS-2CD2386G2-IU", False),  # camera without a speaker
        ("DS-7608NXI-I2", False),  # NVR: no RTSP backchannel for its channels
        ("DVR-108P-G", False),  # DVR with an audio output
    ],
    indirect=["init_integration"],
)
async def test_camera_two_way_audio(
    hass: HomeAssistant, init_integration: MockConfigEntry, monkeypatch: pytest.MonkeyPatch, two_way_audio: bool
) -> None:
    """Cameras with a speaker declare two-way audio."""

    monkeypatch.setattr(camera_platform, "CameraEntityCapabilities", CameraEntityCapabilities)
    assert await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()

    entity_ids = hass.states.async_entity_ids(CAMERA_DOMAIN)
    assert entity_ids
    for entity_id in entity_ids:
        capabilities = getattr(get_camera_from_entity_id(hass, entity_id), "_attr_camera_entity_capabilities", None)
        assert (capabilities == CameraEntityCapabilities(two_way_audio=True)) is two_way_audio


@pytest.mark.parametrize("init_integration", ["DS-2CD2387G2-LSU"], indirect=True)
async def test_camera_without_capabilities_support(
    hass: HomeAssistant, init_integration: MockConfigEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without CameraEntityCapabilities (Home Assistant 2026.10), cameras set up without two-way audio."""

    monkeypatch.setattr(camera_platform, "CameraEntityCapabilities", None)
    assert await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()

    entity_id = hass.states.async_entity_ids(CAMERA_DOMAIN)[0]
    camera_entity = get_camera_from_entity_id(hass, entity_id)
    assert "_attr_camera_entity_capabilities" not in vars(camera_entity)
