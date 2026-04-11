"""Stadtbibliothek integration."""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

try:
    import voluptuous as vol
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import Platform
    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
    from homeassistant.helpers import entity_registry as er

    from .const import DOMAIN
    from .coordinator import StadtbibliothekCoordinator

    _HAS_HOMEASSISTANT = True
except ImportError:
    _HAS_HOMEASSISTANT = False

if _HAS_HOMEASSISTANT:
    PLATFORMS = [Platform.SENSOR]

    SERVICE_RENEW_LOAN = "renew_loan"
    SERVICE_RENEW_ALL = "renew_all"
    SERVICE_FORCE_UPDATE = "force_update"

    RENEW_LOAN_SCHEMA = vol.Schema(
        {
            vol.Optional("config_entry_id"): str,
            vol.Optional("entity_id"): str,
            vol.Required("item_id"): str,
        }
    )

    RENEW_ALL_SCHEMA = vol.Schema(
        {
            vol.Optional("config_entry_id"): str,
            vol.Optional("entity_id"): str,
            vol.Optional("days_remaining_threshold", default=14): vol.All(int, vol.Range(min=0, max=90)),
        }
    )

    FORCE_UPDATE_SCHEMA = vol.Schema(
        {
            vol.Optional("config_entry_id"): str,
            vol.Optional("entity_id"): str,
        }
    )


def _resolve_config_entry_id(hass: HomeAssistant, call_data: dict) -> str:
    config_entry_id = call_data.get("config_entry_id")
    entity_id = call_data.get("entity_id")
    if config_entry_id and entity_id:
        raise vol.Invalid("Provide either config_entry_id or entity_id, not both")
    if config_entry_id:
        return config_entry_id
    if entity_id:
        registry = er.async_get(hass)
        entry = registry.async_get(entity_id)
        if entry is None:
            raise ValueError(f"Entity {entity_id} not found")
        if entry.config_entry_id is None:
            raise ValueError(f"Entity {entity_id} has no config entry")
        return entry.config_entry_id
    raise vol.Invalid("Provide either config_entry_id or entity_id")


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

    async def handle_renew_loan(call: ServiceCall) -> ServiceResponse:
        config_entry_id = _resolve_config_entry_id(hass, call.data)
        coordinator = _get_coordinator(hass, config_entry_id)
        item_id = call.data["item_id"]
        success = await coordinator.renew_loan(item_id)
        return {
            "item_id": item_id,
            "success": success,
            "error": None if success else f"Renewal failed for {item_id}",
        }

    async def handle_renew_all(call: ServiceCall) -> ServiceResponse:
        config_entry_id = _resolve_config_entry_id(hass, call.data)
        coordinator = _get_coordinator(hass, config_entry_id)
        days_remaining_threshold = call.data.get("days_remaining_threshold", 14)
        return await coordinator.renew_all(days_remaining_threshold=days_remaining_threshold)

    async def handle_force_update(call: ServiceCall) -> None:
        config_entry_id = _resolve_config_entry_id(hass, call.data)
        coordinator = _get_coordinator(hass, config_entry_id)
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_RENEW_LOAN,
        handle_renew_loan,
        schema=RENEW_LOAN_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RENEW_ALL,
        handle_renew_all,
        schema=RENEW_ALL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_FORCE_UPDATE, handle_force_update, schema=FORCE_UPDATE_SCHEMA)
