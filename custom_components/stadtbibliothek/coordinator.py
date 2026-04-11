"""DataUpdateCoordinator for Stadtbibliothek."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .backends.base import AccountInfo, AuthenticationError, LibraryType
from .backends.remseck import RemseckBackend
from .backends.stuttgart import StuttgartBackend
from .const import CONF_LIBRARY_TYPE, CONF_PASSWORD, CONF_USERNAME, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

BACKEND_MAP = {
    LibraryType.REMSECK.value: RemseckBackend,
    LibraryType.STUTTGART.value: StuttgartBackend,
}


class StadtbibliothekCoordinator(DataUpdateCoordinator[AccountInfo]):
    """Coordinator that fetches library account data."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=DEFAULT_SCAN_INTERVAL),
            config_entry=config_entry,
        )
        self._library_type = config_entry.data[CONF_LIBRARY_TYPE]
        self._username = config_entry.data[CONF_USERNAME]
        self._password = config_entry.data[CONF_PASSWORD]

    def _create_backend(self) -> RemseckBackend | StuttgartBackend:
        client = get_async_client(self.hass)
        backend_cls = BACKEND_MAP[self._library_type]
        return backend_cls(client=client)

    async def _async_update_data(self) -> AccountInfo:
        backend = self._create_backend()
        try:
            await backend.login(self._username, self._password)
            loans = await backend.get_loans()
            fees = await backend.get_fees()
            total_fees = sum(f.amount for f in fees)
            return AccountInfo(
                username=self._username,
                library_type=LibraryType(self._library_type),
                loans=loans,
                fees=fees,
                total_fees=total_fees,
            )
        except AuthenticationError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Error fetching data: {err}") from err

    async def renew_loan(self, item_id: str) -> bool:
        """Renew a single loan by item ID."""
        backend = self._create_backend()
        try:
            await backend.login(self._username, self._password)
            result = await backend.renew_loan(item_id)
            await self.async_request_refresh()
            return result
        except Exception as err:
            raise UpdateFailed(f"Error renewing loan: {err}") from err

    async def renew_all(self, days_remaining_threshold: int = 14) -> int:
        """Renew all renewable loans."""
        backend = self._create_backend()
        try:
            await backend.login(self._username, self._password)
            count = await backend.renew_all(days_remaining_threshold=days_remaining_threshold)
            await self.async_request_refresh()
            return count
        except Exception as err:
            raise UpdateFailed(f"Error renewing loans: {err}") from err
