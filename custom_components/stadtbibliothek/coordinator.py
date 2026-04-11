"""DataUpdateCoordinator for Stadtbibliothek."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
        self.refresh_required: bool = False

    def _create_backend(self) -> RemseckBackend | StuttgartBackend:
        backend_cls = BACKEND_MAP[self._library_type]
        return backend_cls()

    async def _async_update_data(self) -> AccountInfo:
        self.refresh_required = False
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
            self.refresh_required = True
            await self.async_request_refresh()
            return result
        except Exception as err:
            raise UpdateFailed(f"Error renewing loan: {err}") from err

    async def renew_all(self, days_remaining_threshold: int = 14) -> dict:
        """Renew all renewable loans, returning per-item results."""
        if self.data is None:
            raise UpdateFailed("No loan data available — run a refresh first")

        eligible = [
            loan for loan in self.data.loans if loan.can_be_renewed and loan.days_remaining <= days_remaining_threshold
        ]

        backend = self._create_backend()
        try:
            await backend.login(self._username, self._password)
        except Exception as err:
            raise UpdateFailed(f"Error renewing loans: {err}") from err

        results: list[dict] = []
        renewed = 0
        for loan in eligible:
            try:
                ok = await backend.renew_loan(loan.item_id)
            except Exception as exc:
                _LOGGER.warning("Renewal failed for %s: %s", loan.item_id, exc)
                ok = False

            results.append(
                {
                    "item_id": loan.item_id,
                    "title": loan.title,
                    "success": ok,
                    "error": None if ok else f"Renewal failed for {loan.item_id}",
                }
            )
            if ok:
                renewed += 1

        self.refresh_required = True
        await self.async_request_refresh()
        return {
            "renewed": renewed,
            "total_attempted": len(eligible),
            "results": results,
        }
