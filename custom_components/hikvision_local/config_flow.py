"""Config flow for hikvision_local integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
import httpx
import voluptuous as vol

from . import HikvisionConfigEntry
from .const import (
    CONF_ALARM_SERVER_HOST,
    CONF_SET_ALARM_SERVER,
    DOMAIN,
    OLD_DOMAIN,
    RTSP_PORT_FORCED,
)
from .hikvision_device import HikvisionDevice
from .isapi import ISAPIForbiddenError, ISAPIUnauthorizedError

_LOGGER = logging.getLogger(__name__)

# Settings taken over from a hikvision_next entry; both integrations store the same keys.
IMPORTED_KEYS = (
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_VERIFY_SSL,
    CONF_SET_ALARM_SERVER,
    CONF_ALARM_SERVER_HOST,
    RTSP_PORT_FORCED,
)


class HikvisionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for hikvision device."""

    VERSION = 3
    _entry: HikvisionConfigEntry
    _manual = False

    async def get_schema(self, user_input: dict[str, Any]):
        """Get schema with suggested values."""
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default="http://"): str,
                vol.Optional(CONF_VERIFY_SSL, default=True): bool,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_SET_ALARM_SERVER, default=True): bool,
                vol.Required(CONF_ALARM_SERVER_HOST): str,
                vol.Optional(RTSP_PORT_FORCED): vol.And(int, vol.Range(min=1)),
            }
        )
        if self.source in (SOURCE_RECONFIGURE, SOURCE_REAUTH):
            return self.add_suggested_values_to_schema(
                schema,
                {**self._entry.data, **(user_input or {})},
            )
        local_ip = await async_get_source_ip(self.hass)
        return self.add_suggested_values_to_schema(
            schema,
            {CONF_ALARM_SERVER_HOST: f"http://{local_ip}:8123", **(user_input or {})},
        )

    def importable_entries(self) -> list[ConfigEntry]:
        """Return hikvision_next entries of devices not set up in this integration yet."""
        configured = {entry.unique_id for entry in self._async_current_entries(include_ignore=False)}
        return [
            entry for entry in self.hass.config_entries.async_entries(OLD_DOMAIN) if entry.unique_id not in configured
        ]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""

        if user_input is None and self.source == SOURCE_USER and not self._manual and self.importable_entries():
            return self.async_show_menu(step_id="user", menu_options=["import_hikvision_next", "manual"])

        errors = {}

        if user_input is not None:
            try:
                host = user_input[CONF_HOST].rstrip("/")
                user_input_validated = {
                    **user_input,
                    CONF_HOST: host,
                }

                device = HikvisionDevice(self.hass, data=user_input_validated)
                await device.get_device_info()

            except ISAPIForbiddenError:
                errors["base"] = "insufficient_permission"
            except ISAPIUnauthorizedError:
                errors["base"] = "invalid_auth"
            except httpx.HTTPError as ex:
                _LOGGER.warning("Cannot connect to %s: %s", user_input[CONF_HOST], ex)
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error while connecting to %s", user_input[CONF_HOST])
                errors["base"] = "unknown"

            if not errors:
                if self.source == SOURCE_RECONFIGURE:
                    await self.async_set_unique_id(device.device_info.serial_no, raise_on_progress=False)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        self._entry,
                        data_updates=user_input_validated,
                    )
                if self.source == SOURCE_REAUTH:
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(entry=self._entry, data=user_input_validated)

                # add new device
                await self.async_set_unique_id(device.device_info.serial_no)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=device.device_info.name, data=user_input_validated)

        # show form
        schema = await self.get_schema(user_input)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Set up a device by entering its details."""
        self._manual = True
        return await self.async_step_user()

    async def async_step_import_hikvision_next(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Set up a device with the settings of its hikvision_next entry."""
        entries = {entry.entry_id: entry for entry in self.importable_entries()}
        if not entries:
            return self.async_abort(reason="nothing_to_import")
        errors = {}
        if user_input is not None and (old_entry := entries.get(user_input["entry_id"])):
            data = {
                CONF_VERIFY_SSL: True,
                CONF_SET_ALARM_SERVER: False,
                CONF_ALARM_SERVER_HOST: "",
                **{key: value for key, value in old_entry.data.items() if key in IMPORTED_KEYS},
            }
            if not all(data.get(key) for key in (CONF_HOST, CONF_USERNAME, CONF_PASSWORD)):
                return self.async_abort(reason="import_incomplete")
            data[CONF_HOST] = data[CONF_HOST].rstrip("/")
            if not data[CONF_ALARM_SERVER_HOST]:
                # Without an address there's nothing to point the device at.
                data[CONF_SET_ALARM_SERVER] = False
            try:
                device = HikvisionDevice(self.hass, data=data)
                await device.get_device_info()
            except ISAPIForbiddenError:
                errors["base"] = "insufficient_permission"
            except ISAPIUnauthorizedError:
                errors["base"] = "invalid_auth"
            except httpx.HTTPError as ex:
                _LOGGER.warning("Cannot connect to %s: %s", data[CONF_HOST], ex)
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error while connecting to %s", data[CONF_HOST])
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(device.device_info.serial_no)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=old_entry.title or device.device_info.name, data=data)

        schema = vol.Schema({vol.Required("entry_id"): vol.In({key: entry.title for key, entry in entries.items()})})
        return self.async_show_form(step_id="import_hikvision_next", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: Mapping[str, Any] | None = None) -> ConfigFlowResult:
        """Handle device re-configuration."""
        self._entry = self._get_reconfigure_entry()
        return await self.async_step_user()

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Perform reauth upon an authorization error."""
        self._entry = self._get_reauth_entry()
        return await self.async_step_user()
