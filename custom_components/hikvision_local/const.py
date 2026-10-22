"""hikvision integration constants."""

from typing import Final

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from .isapi.const import EVENTS as ISAPI_EVENTS

DOMAIN: Final = "hikvision_local"
OLD_DOMAIN: Final = "hikvision_next"  # the integration this one continues

RTSP_PORT_FORCED: Final = "rtsp_port_forced"
CONF_SET_ALARM_SERVER: Final = "set_alarm_server"
CONF_ALARM_SERVER_HOST: Final = "alarm_server"
ALARM_SERVER_PATH = "/api/hikvision_local"

EVENTS_COORDINATOR: Final = "events"
SECONDARY_COORDINATOR: Final = "secondary"
LIGHTS_COORDINATOR: Final = "lights"
AUDIO_ALARM_COORDINATOR: Final = "audio_alarm"
HOLIDAY_MODE = "holiday_mode"
CAMERAS_STATUS = "cameras_status"
SIGNAL_ALERT_RECEIVED = f"{DOMAIN}_alert_received_{{}}"  # formatted with the config entry ID

SUPPLEMENT_LIGHT_OFF_MODE: Final = "close"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ACTION_REBOOT = "reboot"
ACTION_ISAPI_REQUEST = "isapi_request"
ACTION_UPDATE_SNAPSHOT = "update_snapshot"
ACTION_PTZ_MOVE = "ptz_move"
ACTION_PTZ_STOP = "ptz_stop"
ACTION_PTZ = "ptz"

HIKVISION_EVENT = f"{DOMAIN}_event"

EVENTS = {
    "motiondetection": {
        **ISAPI_EVENTS["motiondetection"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "tamperdetection": {
        **ISAPI_EVENTS["tamperdetection"],
        "device_class": BinarySensorDeviceClass.TAMPER,
    },
    "videoloss": {
        **ISAPI_EVENTS["videoloss"],
        "device_class": BinarySensorDeviceClass.PROBLEM,
    },
    "scenechangedetection": {
        **ISAPI_EVENTS["scenechangedetection"],
        "device_class": BinarySensorDeviceClass.TAMPER,
    },
    "fielddetection": {
        **ISAPI_EVENTS["fielddetection"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "linedetection": {
        **ISAPI_EVENTS["linedetection"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "regionentrance": {
        **ISAPI_EVENTS["regionentrance"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "regionexiting": {
        **ISAPI_EVENTS["regionexiting"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "io": {
        **ISAPI_EVENTS["io"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "pir": {
        **ISAPI_EVENTS["pir"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
}
