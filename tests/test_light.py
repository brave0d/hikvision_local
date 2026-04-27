"""Tests for light platform."""

import httpx
import pytest
import respx
import xmltodict
from homeassistant.components.light import ATTR_BRIGHTNESS, DOMAIN as LIGHT_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.conftest import TEST_HOST


ENTITY_ID = "light.ds_2cd2386g2_iu00000000aawrj00000000_1_supplement_light"


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_supplement_light_state(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Verify initial supplement light state."""

    assert (state := hass.states.get(ENTITY_ID))
    assert state.state == STATE_ON
    assert state.attributes["brightness"] == 64
    assert state.attributes.get("regulation_mode") == "auto"


@pytest.mark.parametrize("init_integration", ["DS-2CD2386G2-IU"], indirect=True)
async def test_supplement_light_turn_on_off(
    hass: HomeAssistant,
    respx_mock: respx.Router,
    init_integration: MockConfigEntry,
) -> None:
    """Test turning the supplement light on and off with brightness."""

    url = f"{TEST_HOST}/ISAPI/Image/channels/1/supplementLight"
    calls = {"count": 0}

    def _assert_payload(request, route):
        body = request.content.decode("utf-8")
        payload = xmltodict.parse(body)["SupplementLight"]
        if calls["count"] == 0:
            assert payload["supplementLightMode"] == "close"
            assert payload["whiteLightBrightness"] == "25"
        else:
            assert payload["supplementLightMode"] == "colorVuWhiteLight"
            assert payload["whiteLightBrightness"] == "60"
        assert payload["mixedLightBrightnessRegulatMode"] == "auto"
        calls["count"] += 1
        return httpx.Response(200)

    respx_mock.put(url).mock(side_effect=_assert_payload)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: ENTITY_ID},
        blocking=True,
    )

    state = hass.states.get(ENTITY_ID)
    assert state
    assert state.state == STATE_OFF

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_BRIGHTNESS: 153},
        blocking=True,
    )

    state = hass.states.get(ENTITY_ID)
    assert state
    assert state.state == STATE_ON
    assert state.attributes["brightness"] == 153
    assert calls["count"] == 2
