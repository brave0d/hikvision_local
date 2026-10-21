"""Tests for the entity IDs Home Assistant generates for the integration."""

from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

DEVICES = [
    "DS-7608NXI-I2",
    "DS-2CD2386G2-IU",
    "DS-2CD2146G2-ISU",
    "DS-2CD2443G0-IW",
    "DS-2CD2532F-IWS",
    "DS-2TD1228-2-QA",
    "DS-2CD2346G2-ISU",
    "DS-2CD2T46G2-ISU",
    "DS-2CD2T86G2-ISU",
    "DS-2SE4C425MWG-E-26",
    "DS-7616NI-K2",
    "DS-7616NI-Q2",
    "DS-7732NI-M4",
    "iDS-7204HUHI-M1",
    "iDS-7208HQHI-M1",
    "iDS-7208HUHI-M1",
    "DS-2CD2387G2-LSU",
    "DS-7604NXI-K1",
]


@pytest.mark.generated_entity_ids
@pytest.mark.parametrize("init_integration", DEVICES, indirect=True)
async def test_generated_entity_ids_are_valid(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Every entity gets a valid ID in the domain of its platform."""

    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, init_integration.entry_id)
    assert entries

    for entry in entries:
        assert valid_entity_id(entry.entity_id), entry.entity_id
        assert entry.entity_id.startswith(f"{entry.domain}."), entry.entity_id
        assert hass.states.get(entry.entity_id) or entry.disabled_by, entry.entity_id


@pytest.mark.generated_entity_ids
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_snapshots_are_image_entities(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Snapshot entities are created in the image domain."""

    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, init_integration.entry_id)
    snapshots = [entry for entry in entries if entry.unique_id.endswith("_snapshot")]

    assert snapshots
    assert all(entry.entity_id.startswith("image.") for entry in snapshots)


@pytest.mark.generated_entity_ids
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_generated_entity_ids_for_an_nvr(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """NVR entities get unique IDs named after the camera or the NVR."""

    entity_registry = er.async_get(hass)
    entity_ids = [
        entry.entity_id for entry in er.async_entries_for_config_entry(entity_registry, init_integration.entry_id)
    ]

    assert len(entity_ids) == len(set(entity_ids))
    for expected in (
        "camera.garden",
        "camera.garden_sub_stream",
        "image.garden_snapshot",
        "binary_sensor.garden_motion",
        "switch.garden_motion_detection",
        "switch.nvr_alarm_output_1",
        "switch.nvr_holiday_mode",
        "sensor.nvr_sata_hdd1",
    ):
        assert expected in entity_ids, expected
