# Hikvision Local

![GitHub release (latest by date)](https://img.shields.io/github/v/release/brave0d/hikvision_local?style=flat-square) [![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

The Home Assistant integration for Hikvision NVRs and IP cameras. Receives and switches detection of alarm events. Provides video streams.

Hikvision Local continues [hikvision_next](https://github.com/maciej-or/hikvision_next) by Maciej ([@maciej-or](https://github.com/maciej-or)).
If you'd like to thank the original author too, you can [support Maciej on Ko-fi](https://ko-fi.com/maciejor).

## Moving from hikvision_next

Hikvision Local uses its own domain, `hikvision_local`, so it can be installed next to hikvision_next. Things to update when you switch:

- Add your devices again under Hikvision Local. Entity IDs are new.
- The Home Assistant event is now `hikvision_local_event` (was `hikvision_next_event`). Update automations that trigger on it.
- Event notifications now go to `/api/hikvision_local` (was `/api/hikvision`). If you set the device's notification host by hand, change its URL. With `Set Notifications Host` checked, the integration updates it for you.
- Actions are now `hikvision_local.reboot`, `hikvision_local.isapi_request` and `hikvision_local.update_snapshot`.

## Features

- Camera entities for main and sub streams
- Real-time Acusense events notifications through binary sensors and HA events (hikvision_local_event)
- Switches for Acusense events detection
- Switches for NVR Outputs and PIR sensor
- Holiday mode switch (allows to switch continuous recording with appropriate NVR setup)
- Image entities for the latest snapshots
- Audio alarm settings for cameras with a speaker: alarm sound, volume and how many times it plays, plus a button that plays the alarm once to test it
- Recordings browser: play recordings from the camera's SD card or the NVR's disks in the Home Assistant media browser, with alarm events marked
- PTZ actions: `ptz_move`, `ptz_stop`, and an ONVIF-style `ptz` action for cards such as Advanced Camera Card
- Supplement light (ColorVu white light, IR) as light entities, also for cameras connected to an NVR. Turning the light off sets the camera's supplement light mode to off (`close`) until it's turned on again, so the camera has no light at night meanwhile.
- Connection status of each IP camera connected to an NVR, as a diagnostic binary sensor
- Tracking HDD and NAS status
- Tracking Notifications Host settings for diagnostic purposes
- Remote reboot device
- Basic and digest authentication support

### Supported events

- Motion
- Video Tampering
- Video Loss
- Scene Change
- Intrusion (Field Detection)
- Line Crossing
- Region Entrance
- Region Exiting
- NVR Input Triggers
- PIR

**NOTE**
Events must be set to alert the surveillance center in Linkage Action for Home Assistant to be notified. Otherwise related binary sensors and switches will appear as disabled entities.

### Blueprints

#### Take Multiple Snapshots On Detection Event

Creates automation that allows to take snapshots from selected cameras when an event sensor is triggered.

[<img src="https://my.home-assistant.io/badges/blueprint_import.svg">](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/brave0d/hikvision_local/blob/main/blueprints/take_pictures_on_motion_detection.yaml)

#### Display Sensor State On Hikvision Video

Creates an automation that allows to display text overlay on a selected video stream with the state of a selected sensor. Refreshes every 15 minutes.

[<img src="https://my.home-assistant.io/badges/blueprint_import.svg">](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/brave0d/hikvision_local/blob/main/blueprints/display_sensor_state_on_hikvision_video.yaml)

## Preview

### IP Camera device view
![IP Camera](/assets/ipcam.jpg "IP Camera device view")

### NVR device view
![NVR](/assets/nvr.jpg "NVR device view")

The scope supported features depends on device model, setup and firmware version.

## Installation

[<img src="https://my.home-assistant.io/badges/hacs_repository.svg">](https://my.home-assistant.io/redirect/hacs_repository/?owner=brave0d&repository=hikvision_local&category=integration)

### With HACS

1. In HACS, open the three-dot menu, choose `Custom repositories`, add `https://github.com/brave0d/hikvision_local` with type `Integration`, then search for `Hikvision Local` and press `Download`
2. on `Settings / Devices & Services` page press `+ Add Integration`
3. Search for `Hikvision Local` and add your Hikvision device using config dialog, repeat the last 2 steps for more devices

### Manual

1. copy `custom_components/hikvision_local` folder into `config/custom_components`
2. restart Home Assistant
3. on `Settings / Devices & Services` page press `+ Add Integration`
4. search for `Hikvision Local` and add your Hikvision device using config dialog, repeat the last 2 steps for more devices

## Hikvision device setup checklist

- Network settings
  - enabled ISAPI access
- User Management - create user with permissions:
  - Remote: Parameters Settings
  - Remote: Log Search / Interrogate Working Status
  - Remote: Live View
  - Remote: Notify Surveillance Center / Trigger Alarm Output
- Events
  - Notify Surveillance Center
  - Regions if needed
  - Arming Schedule
- Storage Schedule Settings - set continuous recording in Holiday mode for desired cameras
- Notifications Host - IP address of Home Assistant instance for event notifications, with the URL `/api/hikvision_local`. Can be set manually or by this integration if checked `Set Notifications Host` checkbox in the configuration dialog. It will be reverted to `http://0.0.0.0:80/` on integration unload.

## Reporting issues

There are a lot of Hikvision devices with different firmwares in the world. In most cases logs are crucial to solve your problem, so please attach them to the report.
Keep in mind that logs include MAC addresses, serial numbers and local IP addresses of your devices. Consider using [pastebin.com](https://pastebin.com) or similar services for sharing logs.

You can also download a `diagnostic output` to provide essential information about your device. It is available on device info page. This redacts all sensitive data and can be provided in the github issue.

Setup log level to `debug` in configuration.yaml

```yaml
logger:
  logs:
    custom_components.hikvision_local: debug
```

Restart Home Assistant

Download logs from `Settings / System / Logs`

## Tested models

### NVR

- Annke N46PCK
- DS-7108NI-Q1/8P
- DS-7608NI-I2
- DS-7608NI-I2/8P
- DS-7608NXI-I2/8P/S
- DS-7608NXI-K1/8P
- DS-7616NI-E2/16P
- DS-7616NI-I2/16P
- DS-7616NI-Q2
- DS-7616NI-Q2/16P
- DS-7616NXI-I2/16P/S
- DS-7716NI-I4/16P
- DS-7732NI-M4
- ERI-K104-P4

### DVR

- iDS-7204HUHI-M1/FA/A
- iDS-7204HUHI-M1/P
- iDS-7208HQHI-M1(A)/S(C)

### IP Camera

- Annke C800 (I91BM)
- DS-2CD2047G2-LU/SL
- DS-2CD2047G2H-LIU
- DS-2CD2087G2-LU
- DS-2CD2146G2-ISU
- DS-2CD2155FWD-I
- DS-2CD2346G2-IU
- DS-2CD2386G2-IU
- DS-2CD2387G2-LU
- DS-2CD2387G2H-LISU/SL
- DS-2CD2425FWD-IW
- DS-2CD2443G0-IW
- DS-2CD2532F-IWS
- DS-2CD2546G2-IS
- DS-2CD2747G2-LZS
- DS-2CD2785G1-IZS
- DS-2CD2H46G2-IZS (C)
- DS-2CD2T46G2-ISU/SL
- DS-2CD2T87G2-L
- DS-2CD2T87G2P-LSU/SL
- DS-2DE4425IW-DE (PTZ)
- DS-2SE4C425MWG-E/26
