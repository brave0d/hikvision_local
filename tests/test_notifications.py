"""Test event notifications."""

from http import HTTPStatus
from unittest.mock import MagicMock

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.hikvision_local.const import HIKVISION_EVENT, RTSP_PORT_FORCED
from custom_components.hikvision_local.notifications import EventNotificationsView
from tests.conftest import TEST_CONFIG, TEST_CONFIG_OUTSIDE_NETWORK, TEST_HOST_IP, load_fixture


def mock_event_notification(file) -> MagicMock:
    """Mock incoming event notification request."""

    mock_request = MagicMock()
    mock_request.headers = {
        "Content-Type": 'application/xml; charset="UTF-8"',
    }
    mock_request.remote = TEST_HOST_IP

    async def read():
        payload = load_fixture("ISAPI/EventNotificationAlert", file)
        return payload.encode()

    mock_request.read = read
    return mock_request


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_nvr_intrusion_detection_alert(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test incoming intrusion detection event alert from nvr."""

    entity_id = "binary_sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_2_fielddetection"
    bus_events = async_capture_events(hass, HIKVISION_EVENT)

    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("nvr_2_fielddetection")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON

    await hass.async_block_till_done()
    assert len(bus_events) == 1
    data = bus_events[0].data
    assert data["channel_id"] == 2
    assert data["event_id"] == "fielddetection"
    assert data["camera_name"] == "home"


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_ipc_intrusion_detection_alert(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test incoming intrusion detection event alert from ip camera."""

    entity_id = "binary_sensor.ds_2cd2386g2_iu00000000aawrj00000000_1_fielddetection"

    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("ipc_1_fielddetection")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_ipc_vmd_detection_alert(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test incoming motion detection event alert from ip camera."""

    entity_id = "binary_sensor.ds_2cd2386g2_iu00000000aawrj00000000_1_motiondetection"
    bus_events = async_capture_events(hass, HIKVISION_EVENT)

    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("vmd")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON

    mock_request = mock_event_notification("vmd_human")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON
    assert sensor.attributes.get("target_type") == "human"

    await hass.async_block_till_done()
    assert len(bus_events) == 2
    assert "target_type" not in bus_events[0].data
    assert bus_events[1].data["target_type"] == "human"


@pytest.mark.parametrize("init_integration", ["DS-2TD1228-2-QA"], indirect=True)
async def test_ipc_motion_detection_on_thermometry_channel_alert(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test incoming motion detection event alert on thermometry channel from ip multi channel camera."""

    entity_id = "binary_sensor.ds_2td1228_2_qa_xxxxxxxxxxxxxxxxxx_2_motiondetection"

    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("ipc_thermometry_motiondetection")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON


@pytest.mark.parametrize("init_integration", ["DS-2TD1228-2-QA"], indirect=True)
async def test_motion_detection_human_alert(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test incoming motion detection event alert with target type from ip multi channel camera."""

    entity_id = "binary_sensor.ds_2td1228_2_qa_xxxxxxxxxxxxxxxxxx_2_motiondetection"

    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF
    assert sensor.attributes.get("target_type") is None

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("motiondetection_human")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON
    assert sensor.attributes.get("target_type") == "human"

    mock_request = mock_event_notification("ipc_thermometry_motiondetection")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON
    assert sensor.attributes.get("target_type") is None


@pytest.mark.parametrize("init_integration", ["DS-2CD2146G2-ISU"], indirect=True)
async def test_field_detection_alert(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test incoming field detection event with detection target."""

    entity_id = "binary_sensor.ds_2cd2146g2_isu00000000aawrg00000000_1_fielddetection"
    bus_events = async_capture_events(hass, HIKVISION_EVENT)

    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("fielddetection_human")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON

    await hass.async_block_till_done()
    assert len(bus_events) == 1
    data = bus_events[0].data
    assert data["channel_id"] == 1
    assert data["event_id"] == "fielddetection"
    assert data["detection_target"] == "human"
    assert data["region_id"] == 3

    mock_request = mock_event_notification("fielddetection_vehicle")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON

    await hass.async_block_till_done()
    assert len(bus_events) == 2
    data = bus_events[1].data
    assert data["channel_id"] == 1
    assert data["event_id"] == "fielddetection"
    assert data["detection_target"] == "vehicle"
    assert data["region_id"] == 2


@pytest.mark.parametrize(
    "init_multi_device_integration",
    [
        [
            {"model": "DS-7608NXI-I2", "config": TEST_CONFIG},
            {"model": "DS-2CD2T46G2-ISU", "config": TEST_CONFIG_OUTSIDE_NETWORK},
            {"model": "DS-2CD2346G2-ISU", "config": {**TEST_CONFIG_OUTSIDE_NETWORK, RTSP_PORT_FORCED: 5152}},
            {"model": "DS-2CD2T86G2-ISU", "config": {**TEST_CONFIG_OUTSIDE_NETWORK, RTSP_PORT_FORCED: 5153}},
        ]
    ],
    indirect=True,
)
async def test_nvr_and_cam_notification_alert(
    hass: HomeAssistant,
    init_multi_device_integration: list[MockConfigEntry],
) -> None:
    """Test incoming multiple notifications with 1 NVR in the same network et 3 cameras outside."""

    """A NVR IN THE SAME NETWORK without macAddress in notification"""
    entity_nvr_1_id = "binary_sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_2_fielddetection"

    """ANOTHER CAMERAS OUTSIDE THE NETWORK with macAddress in notification"""
    entity_cam_1_id = "binary_sensor.ds_2cd2t46g2_isu_sl00000000aawrg00000000_1_io"
    entity_cam_2_id = "binary_sensor.ds_2cd2346g2_isu_sl00000000aawrj00000000_1_io"
    entity_cam_3_id = "binary_sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_1_io"

    assert (sensor_cam_1 := hass.states.get(entity_cam_1_id))
    assert (sensor_cam_2 := hass.states.get(entity_cam_2_id))
    assert (sensor_cam_3 := hass.states.get(entity_cam_3_id))
    assert (sensor_nvr_1 := hass.states.get(entity_nvr_1_id))
    assert sensor_cam_1.state == STATE_OFF
    assert sensor_cam_2.state == STATE_OFF
    assert sensor_cam_3.state == STATE_OFF
    assert sensor_nvr_1.state == STATE_OFF

    """NOTIFICATION ON CAM 3 SENSOR"""
    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("cam3_DS-2CD2T86G2-ISU_io_notification")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK

    assert (sensor_cam_1 := hass.states.get(entity_cam_1_id))
    assert (sensor_cam_2 := hass.states.get(entity_cam_2_id))
    assert (sensor_cam_3 := hass.states.get(entity_cam_3_id))
    assert (sensor_nvr_1 := hass.states.get(entity_nvr_1_id))
    assert sensor_cam_1.state == STATE_OFF
    assert sensor_cam_2.state == STATE_OFF
    assert sensor_cam_3.state == STATE_ON
    assert sensor_nvr_1.state == STATE_OFF

    """NOTIFICATION ON CAM 1 SENSOR"""
    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("cam1_DS-2CD2T46G2-ISU_io_notification")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK

    assert (sensor_cam_1 := hass.states.get(entity_cam_1_id))
    assert (sensor_cam_2 := hass.states.get(entity_cam_2_id))
    assert (sensor_cam_3 := hass.states.get(entity_cam_3_id))
    assert (sensor_nvr_1 := hass.states.get(entity_nvr_1_id))
    assert sensor_cam_1.state == STATE_ON
    assert sensor_cam_2.state == STATE_OFF
    assert sensor_cam_3.state == STATE_ON
    assert sensor_nvr_1.state == STATE_OFF

    """NOTIFICATION WITHOUT MAC ADDRESS ON NVR"""
    view = EventNotificationsView(hass)
    mock_request = mock_event_notification("nvr_2_fielddetection")
    response = await view.post(mock_request)

    assert response.status == HTTPStatus.OK

    assert (sensor_cam_1 := hass.states.get(entity_cam_1_id))
    assert (sensor_cam_2 := hass.states.get(entity_cam_2_id))
    assert (sensor_cam_3 := hass.states.get(entity_cam_3_id))
    assert (sensor_nvr_1 := hass.states.get(entity_nvr_1_id))
    assert sensor_cam_1.state == STATE_ON
    assert sensor_cam_2.state == STATE_OFF
    assert sensor_cam_3.state == STATE_ON
    assert sensor_nvr_1.state == STATE_ON


@pytest.mark.parametrize("init_integration", ["iDS-7208HUHI-M1"], indirect=True)
async def test_alert_for_disabled_dvr_input_is_ignored(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    caplog,
) -> None:
    """A video loss alert from a disabled DVR input is ignored without a warning."""

    view = EventNotificationsView(hass)
    response = await view.post(mock_event_notification("dvr_5_videoloss"))

    assert response.status == HTTPStatus.OK
    assert "Cannot process incoming event" not in caplog.text


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_inactive_alert_turns_sensor_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """An inactive alert switches the sensor off and fires no event."""

    entity_id = "binary_sensor.ds_2cd2386g2_iu00000000aawrj00000000_1_motiondetection"
    bus_events = async_capture_events(hass, HIKVISION_EVENT)

    view = EventNotificationsView(hass)
    response = await view.post(mock_event_notification("vmd_human"))
    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_ON
    assert sensor.attributes.get("target_type") == "human"

    response = await view.post(mock_event_notification("vmd_inactive"))
    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF
    assert sensor.attributes.get("target_type") is None

    await hass.async_block_till_done()
    assert len(bus_events) == 1


@pytest.mark.parametrize("init_integration", ["iDS-7208HUHI-M1"], indirect=True)
async def test_video_loss_heartbeat_keeps_sensor_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """DVRs send inactive video loss alerts as a heartbeat; the sensor stays off."""

    entity_id = "binary_sensor.ids_7208huhi_m0_s_a0000000000ccwrj00000000wcvu_1_videoloss"

    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF

    view = EventNotificationsView(hass)
    response = await view.post(mock_event_notification("dvr_1_videoloss_inactive"))

    assert response.status == HTTPStatus.OK
    assert (sensor := hass.states.get(entity_id))
    assert sensor.state == STATE_OFF
