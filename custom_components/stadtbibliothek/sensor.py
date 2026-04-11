"""Sensor platform for Stadtbibliothek."""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .backends.base import AccountInfo, FeeItem, LoanItem
from .const import CONF_LIBRARY_TYPE, CONF_USERNAME, DOMAIN
from .coordinator import StadtbibliothekCoordinator


def _serialize_loan(loan: LoanItem) -> dict[str, Any]:
    """Serialize a LoanItem including computed properties and ISO date strings."""
    d = dataclasses.asdict(loan)
    # Convert date objects to ISO strings
    for key in ("due_date", "checkout_date"):
        if isinstance(d.get(key), date):
            d[key] = d[key].isoformat()
    # Add computed properties
    d["days_remaining"] = loan.days_remaining
    d["is_overdue"] = loan.is_overdue
    d["renewals_left"] = loan.renewals_left
    return d


def _serialize_fee(fee: FeeItem) -> dict[str, Any]:
    """Serialize a FeeItem with ISO date strings."""
    d = dataclasses.asdict(fee)
    if isinstance(d.get("date"), date):
        d["date"] = d["date"].isoformat()
    return d


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: StadtbibliothekCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    lib_type = config_entry.data[CONF_LIBRARY_TYPE]
    username = config_entry.data[CONF_USERNAME]

    async_add_entities(
        [
            LoansSensor(coordinator, config_entry, lib_type, username),
            WarningSensor(coordinator, config_entry, lib_type, username),
            FeesSensor(coordinator, config_entry, lib_type, username),
        ]
    )


def _slug(lib_type: str, username: str) -> str:
    return f"{lib_type}_{username}".lower().replace(" ", "_")


class StadtbibliothekEntity(CoordinatorEntity[StadtbibliothekCoordinator], SensorEntity):
    """Base entity for Stadtbibliothek sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: StadtbibliothekCoordinator,
        config_entry: ConfigEntry,
        lib_type: str,
        username: str,
    ) -> None:
        super().__init__(coordinator)
        self._slug = _slug(lib_type, username)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=f"Stadtbibliothek {lib_type} {username}",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _account(self) -> AccountInfo | None:
        return self.coordinator.data


class LoansSensor(StadtbibliothekEntity):
    """Number of active loans."""

    _attr_icon = "mdi:bookshelf"
    _attr_native_unit_of_measurement = "loans"

    def __init__(self, coordinator, config_entry, lib_type, username) -> None:
        super().__init__(coordinator, config_entry, lib_type, username)
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_loans"

    @property
    def native_value(self) -> int | None:
        if self._account is None:
            return None
        return len(self._account.loans)

    @property
    def extra_state_attributes(self) -> dict:
        if self._account is None:
            return {}
        return {
            "loans": [_serialize_loan(loan) for loan in self._account.loans],
        }


class WarningSensor(StadtbibliothekEntity):
    """Days until earliest due date."""

    _attr_native_unit_of_measurement = "days"

    def __init__(self, coordinator, config_entry, lib_type, username) -> None:
        super().__init__(coordinator, config_entry, lib_type, username)
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_warning"

    @property
    def native_value(self) -> int | str | None:
        if self._account is None or not self._account.loans:
            return "none"
        return min(loan.days_remaining for loan in self._account.loans)

    @property
    def icon(self) -> str:
        if self._account and self._account.loans:
            if any(loan.is_overdue for loan in self._account.loans):
                return "mdi:alert-circle"
        return "mdi:clock-outline"

    @property
    def extra_state_attributes(self) -> dict:
        if self._account is None or not self._account.loans:
            return {}
        overdue = [loan for loan in self._account.loans if loan.is_overdue]
        due_soon = [loan for loan in self._account.loans if 0 <= loan.days_remaining <= 7]
        earliest = min(loan.due_date for loan in self._account.loans)
        return {
            "overdue_count": len(overdue),
            "items_due_soon": len(due_soon),
            "earliest_due_date": earliest.isoformat(),
        }


class FeesSensor(StadtbibliothekEntity):
    """Total outstanding fees."""

    _attr_icon = "mdi:currency-eur"
    _attr_native_unit_of_measurement = "EUR"

    def __init__(self, coordinator, config_entry, lib_type, username) -> None:
        super().__init__(coordinator, config_entry, lib_type, username)
        self._attr_unique_id = f"{DOMAIN}_{self._slug}_fees"

    @property
    def native_value(self) -> float | None:
        if self._account is None:
            return None
        return self._account.total_fees

    @property
    def extra_state_attributes(self) -> dict:
        if self._account is None:
            return {}
        return {
            "fee_items": [_serialize_fee(fee) for fee in self._account.fees],
        }
