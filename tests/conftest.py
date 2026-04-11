"""Shared test fixtures for HA integration tests."""

from __future__ import annotations

import sys
from datetime import date
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub out homeassistant modules so we can import our integration code
# without a full HA installation.
# ---------------------------------------------------------------------------


def _make_module(name: str, **attrs) -> ModuleType:
    mod = ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# --- homeassistant.const ---
class Platform:
    SENSOR = "sensor"


ha_const = _make_module(
    "homeassistant.const",
    Platform=Platform,
)

# --- homeassistant.core ---
HomeAssistant = MagicMock
ServiceCall = MagicMock
ServiceResponse = dict | None


class SupportsResponse:
    NONE = "none"
    ONLY = "only"
    OPTIONAL = "optional"


ha_core = _make_module(
    "homeassistant.core",
    HomeAssistant=HomeAssistant,
    ServiceCall=ServiceCall,
    ServiceResponse=ServiceResponse,
    SupportsResponse=SupportsResponse,
)

# --- homeassistant.config_entries ---


class ConfigFlowResult(dict):
    pass


class _ConfigFlowMeta(type):
    """Metaclass that accepts domain= keyword."""

    def __new__(mcs, name, bases, namespace, domain=None, **kwargs):
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)
        if domain is not None:
            cls.DOMAIN = domain
        return cls


class ConfigFlow(metaclass=_ConfigFlowMeta):
    hass = MagicMock()

    def async_show_form(self, *, step_id, data_schema, errors=None):
        return ConfigFlowResult(type="form", step_id=step_id, data_schema=data_schema, errors=errors or {})

    def async_create_entry(self, *, title, data):
        return ConfigFlowResult(type="create_entry", title=title, data=data)


class ConfigEntry:
    def __init__(self, entry_id="test_entry_id", data=None):
        self.entry_id = entry_id
        self.data = data or {}


ha_config_entries = _make_module(
    "homeassistant.config_entries",
    ConfigEntry=ConfigEntry,
    ConfigFlow=ConfigFlow,
    ConfigFlowResult=ConfigFlowResult,
)

# --- homeassistant.helpers ---
_make_module("homeassistant.helpers")
_make_module("homeassistant.helpers.typing", ConfigType=dict)


def _get_async_client(hass):
    return MagicMock()


_make_module("homeassistant.helpers.httpx_client", get_async_client=_get_async_client)

# --- homeassistant.helpers.entity ---


class DeviceInfo(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for k, v in kwargs.items():
            setattr(self, k, v)


_make_module("homeassistant.helpers.entity", DeviceInfo=DeviceInfo)

# --- homeassistant.helpers.device_registry ---


class DeviceEntryType:
    SERVICE = "service"


_make_module("homeassistant.helpers.device_registry", DeviceEntryType=DeviceEntryType)

# --- homeassistant.helpers.entity_platform ---
_make_module("homeassistant.helpers.entity_platform", AddEntitiesCallback=list)

# --- homeassistant.helpers.update_coordinator ---


class UpdateFailed(Exception):
    pass


class DataUpdateCoordinator:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, *, name, update_interval, config_entry=None):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.config_entry = config_entry
        self.data = None

    async def async_config_entry_first_refresh(self):
        self.data = await self._async_update_data()

    async def async_request_refresh(self):
        self.data = await self._async_update_data()

    async def _async_update_data(self):
        raise NotImplementedError


class CoordinatorEntity:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator


_make_module(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=DataUpdateCoordinator,
    UpdateFailed=UpdateFailed,
    CoordinatorEntity=CoordinatorEntity,
)

# --- homeassistant.components.sensor ---


class SensorEntity:
    pass


class SensorDeviceClass:
    MONETARY = "monetary"


_make_module(
    "homeassistant.components.sensor",
    SensorEntity=SensorEntity,
    SensorDeviceClass=SensorDeviceClass,
)

# --- homeassistant.exceptions ---
_make_module("homeassistant.exceptions", HomeAssistantError=Exception)

# ---------------------------------------------------------------------------
# Now we can safely import our integration modules.
# ---------------------------------------------------------------------------

from custom_components.stadtbibliothek.backends.base import (  # noqa: E402
    AccountInfo,
    FeeItem,
    LibraryType,
    LoanItem,
)


@pytest.fixture
def sample_loans() -> list[LoanItem]:
    return [
        LoanItem(
            title="Python Crash Course",
            item_id="12345",
            due_date=date.today(),
            author="Eric Matthes",
            media_type="Book",
            can_be_renewed=True,
            times_renewed=1,
            max_renewals=3,
        ),
        LoanItem(
            title="Clean Code",
            item_id="67890",
            due_date=date(2020, 1, 1),  # overdue
            author="Robert C. Martin",
            media_type="Book",
            can_be_renewed=False,
            times_renewed=3,
            max_renewals=3,
        ),
    ]


@pytest.fixture
def sample_fees() -> list[FeeItem]:
    return [
        FeeItem(description="Late fee", amount=1.50, date=date(2024, 1, 15)),
        FeeItem(description="Lost item", amount=10.00, date=date(2024, 2, 1)),
    ]


@pytest.fixture
def sample_account(sample_loans, sample_fees) -> AccountInfo:
    return AccountInfo(
        username="testuser",
        library_type=LibraryType.REMSECK,
        loans=sample_loans,
        fees=sample_fees,
        total_fees=11.50,
    )


@pytest.fixture
def mock_backend(sample_loans, sample_fees):
    backend = AsyncMock()
    backend.login = AsyncMock()
    backend.get_loans = AsyncMock(return_value=sample_loans)
    backend.get_fees = AsyncMock(return_value=sample_fees)
    backend.renew_loan = AsyncMock(return_value=True)
    backend.renew_all = AsyncMock(return_value=2)
    backend.close = AsyncMock()
    return backend
