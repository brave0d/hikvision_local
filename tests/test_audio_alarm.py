"""Tests for audio alarm button, select and number entities."""

import json

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.number import ATTR_VALUE, DOMAIN as NUMBER_DOMAIN, SERVICE_SET_VALUE
from homeassistant.components.select import ATTR_OPTION, DOMAIN as SELECT_DOMAIN, SERVICE_SELECT_OPTION
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import respx

from tests.conftest import TEST_HOST

PREFIX = "ds_2cd2346g2_isu_sl00000000aawrj00000000_audio_alarm"
BUTTON_ID = f"button.{PREFIX}_test"
SELECT_ID = f"select.{PREFIX}_sound"
VOLUME_ID = f"number.{PREFIX}_volume"
ALARM_TIMES_ID = f"number.{PREFIX}_alarm_times"
SETTINGS_URL = f"{TEST_HOST}/ISAPI/Event/triggers/notifications/AudioAlarm?format=json"


@pytest.mark.parametrize("init_integration", ["DS-2CD2346G2-ISU"], indirect=True)
async def test_audio_alarm_entities_state(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Read sound, volume and alarm times from the device."""

    assert hass.states.get(BUTTON_ID)

    assert (sound := hass.states.get(SELECT_ID))
    assert sound.state == "Siren"
    assert len(sound.attributes["options"]) == 11
    assert "Audio Warning" in sound.attributes["options"]

    assert (volume := hass.states.get(VOLUME_ID))
    assert volume.state == "98"
    assert volume.attributes["min"] == 1
    assert volume.attributes["max"] == 100

    assert (alarm_times := hass.states.get(ALARM_TIMES_ID))
    assert alarm_times.state == "5"
    assert alarm_times.attributes["max"] == 50


@pytest.mark.parametrize("init_integration", ["DS-2CD2146G2-ISU"], indirect=True)
async def test_audio_alarm_not_supported(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Add no audio alarm entities when the device has no audio alarm."""

    assert not hass.states.async_entity_ids(BUTTON_DOMAIN)
    assert not hass.states.async_entity_ids(SELECT_DOMAIN)
    assert not hass.states.async_entity_ids(NUMBER_DOMAIN)


@pytest.mark.parametrize("init_integration", ["DS-2CD2346G2-ISU"], indirect=True)
async def test_audio_alarm_test_button(
    hass: HomeAssistant,
    respx_mock: respx.Router,
    init_integration: MockConfigEntry,
) -> None:
    """Play the selected sound."""

    url = f"{TEST_HOST}/ISAPI/Event/triggers/notifications/AudioAlarm/1/test?format=json"
    route = respx_mock.put(url).respond(json={"statusCode": 1, "statusString": "OK"})

    await hass.services.async_call(BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: BUTTON_ID}, blocking=True)

    assert route.call_count == 1
    assert route.calls.last.request.content == b""


@pytest.mark.parametrize("init_integration", ["DS-2CD2346G2-ISU"], indirect=True)
async def test_audio_alarm_select_sound(
    hass: HomeAssistant,
    respx_mock: respx.Router,
    init_integration: MockConfigEntry,
) -> None:
    """Change the sound and keep the rest of the settings."""

    route = respx_mock.put(SETTINGS_URL).respond(json={"statusCode": 1, "statusString": "OK"})

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: SELECT_ID, ATTR_OPTION: "Audio Warning"},
        blocking=True,
    )

    payload = json.loads(route.calls.last.request.content)["AudioAlarm"]
    assert payload["audioClass"] == "alertAudio"
    assert payload["alertAudioID"] == 11
    assert payload["audioID"] == 11
    assert payload["audioVolume"] == 98
    assert payload["alarmTimes"] == 5
    assert len(payload["TimeRangeList"]) == 7
    assert hass.states.get(SELECT_ID).state == "Audio Warning"


@pytest.mark.parametrize("init_integration", ["DS-2CD2346G2-ISU"], indirect=True)
@pytest.mark.parametrize(
    ("entity_id", "value", "field"),
    [(VOLUME_ID, 40, "audioVolume"), (ALARM_TIMES_ID, 1, "alarmTimes")],
)
async def test_audio_alarm_set_number(
    hass: HomeAssistant,
    respx_mock: respx.Router,
    init_integration: MockConfigEntry,
    entity_id: str,
    value: int,
    field: str,
) -> None:
    """Change volume or alarm times and keep the rest of the settings."""

    route = respx_mock.put(SETTINGS_URL).respond(json={"statusCode": 1, "statusString": "OK"})

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: value},
        blocking=True,
    )

    payload = json.loads(route.calls.last.request.content)["AudioAlarm"]
    assert payload[field] == value
    assert payload["alertAudioID"] == 1
    assert len(payload["TimeRangeList"]) == 7
    assert hass.states.get(entity_id).state == str(value)


@pytest.mark.parametrize("init_integration", ["DS-2CD2346G2-ISU"], indirect=True)
async def test_audio_alarm_set_fails(
    hass: HomeAssistant,
    respx_mock: respx.Router,
    init_integration: MockConfigEntry,
) -> None:
    """Keep the old value when the device rejects the change."""

    respx_mock.put(SETTINGS_URL).respond(status_code=400)

    with pytest.raises(httpx.HTTPStatusError):
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: VOLUME_ID, ATTR_VALUE: 40},
            blocking=True,
        )

    assert hass.states.get(VOLUME_ID).state == "98"
