"""Config flow for Stadtbibliothek integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .backends.base import AuthenticationError, LibraryType
from .backends.remseck import RemseckBackend
from .backends.stuttgart import StuttgartBackend
from .const import CONF_LIBRARY_TYPE, CONF_PASSWORD, CONF_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

LIBRARY_TYPE_OPTIONS = {
    LibraryType.REMSECK.value: "Remseck (Koha)",
    LibraryType.STUTTGART.value: "Stuttgart (aDIS)",
}

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LIBRARY_TYPE): vol.In(LIBRARY_TYPE_OPTIONS),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

BACKEND_MAP = {
    LibraryType.REMSECK.value: RemseckBackend,
    LibraryType.STUTTGART.value: StuttgartBackend,
}


class StadtbibliothekConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Stadtbibliothek."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            library_type = user_input[CONF_LIBRARY_TYPE]
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            backend_cls = BACKEND_MAP[library_type]
            backend = backend_cls()

            try:
                await backend.login(username, password)
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during login")
                errors["base"] = "cannot_connect"
            else:
                title = f"{LIBRARY_TYPE_OPTIONS[library_type]} - {username}"
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
