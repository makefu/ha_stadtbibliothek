"""Stadtbibliothek integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN
from .coordinator import StadtbibliothekCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

SERVICE_RENEW_LOAN = "renew_loan"
SERVICE_RENEW_ALL = "renew_all"
SERVICE_FORCE_UPDATE = "force_update"

RENEW_LOAN_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Required("item_id"): str,
    }
)

RENEW_ALL_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
        vol.Optional("days_remaining_threshold", default=14): vol.All(int, vol.Range(min=0, max=90)),
    }
)

FORCE_UPDATE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): str,
    }
)


def _get_coordinator(hass: HomeAssistant, config_entry_id: str) -> StadtbibliothekCoordinator:
    if config_entry_id not in hass.data.get(DOMAIN, {}):
        raise ValueError(f"Config entry {config_entry_id} not found")
    return hass.data[DOMAIN][config_entry_id]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Stadtbibliothek from a config entry."""
    coordinator = StadtbibliothekCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


def _register_services(hass: HomeAssistant) -> None:
    """Register services (idempotent — safe to call per entry)."""
    if hass.services.has_service(DOMAIN, SERVICE_RENEW_LOAN):
        return

    async def handle_renew_loan(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        await coordinator.renew_loan(call.data["item_id"])

    async def handle_renew_all(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        days_remaining_threshold = call.data.get("days_remaining_threshold", 14)
        await coordinator.renew_all(days_remaining_threshold=days_remaining_threshold)

    async def handle_force_update(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_RENEW_LOAN, handle_renew_loan, schema=RENEW_LOAN_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RENEW_ALL, handle_renew_all, schema=RENEW_ALL_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_FORCE_UPDATE, handle_force_update, schema=FORCE_UPDATE_SCHEMA)
