"""Tests for Stadtbibliothek coordinator."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.stadtbibliothek.backends.base import (
    AccountInfo,
    AuthenticationError,
    LibraryType,
)
from custom_components.stadtbibliothek.const import (
    CONF_LIBRARY_TYPE,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
)
from custom_components.stadtbibliothek.coordinator import StadtbibliothekCoordinator

# The conftest stubs out homeassistant modules, so UpdateFailed is available:
from homeassistant.helpers.update_coordinator import UpdateFailed


def _make_coordinator(library_type="remseck", username="testuser", password="testpass"):
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {
        CONF_LIBRARY_TYPE: library_type,
        CONF_USERNAME: username,
        CONF_PASSWORD: password,
    }
    return StadtbibliothekCoordinator(hass, entry)


@patch("custom_components.stadtbibliothek.coordinator.get_async_client")
async def test_update_data_returns_account_info(mock_get_client, mock_backend, sample_loans, sample_fees):
    coordinator = _make_coordinator()

    with patch.object(coordinator, "_create_backend", return_value=mock_backend):
        data = await coordinator._async_update_data()

    assert isinstance(data, AccountInfo)
    assert data.username == "testuser"
    assert data.library_type == LibraryType.REMSECK
    assert data.loans == sample_loans
    assert data.fees == sample_fees
    assert data.total_fees == 11.50
    mock_backend.login.assert_awaited_once_with("testuser", "testpass")


@patch("custom_components.stadtbibliothek.coordinator.get_async_client")
async def test_update_data_auth_error_raises_update_failed(mock_get_client):
    coordinator = _make_coordinator()
    backend = AsyncMock()
    backend.login = AsyncMock(side_effect=AuthenticationError("bad"))

    with patch.object(coordinator, "_create_backend", return_value=backend):
        with pytest.raises(UpdateFailed, match="Authentication failed"):
            await coordinator._async_update_data()


@patch("custom_components.stadtbibliothek.coordinator.get_async_client")
async def test_update_data_generic_error_raises_update_failed(mock_get_client):
    coordinator = _make_coordinator()
    backend = AsyncMock()
    backend.login = AsyncMock(side_effect=RuntimeError("network down"))

    with patch.object(coordinator, "_create_backend", return_value=backend):
        with pytest.raises(UpdateFailed, match="Error fetching data"):
            await coordinator._async_update_data()


@patch("custom_components.stadtbibliothek.coordinator.get_async_client")
async def test_renew_loan(mock_get_client, mock_backend):
    coordinator = _make_coordinator()

    with patch.object(coordinator, "_create_backend", return_value=mock_backend):
        # Prevent the refresh from running a real update
        with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
            result = await coordinator.renew_loan("12345")

    assert result is True
    mock_backend.renew_loan.assert_awaited_once_with("12345")


@patch("custom_components.stadtbibliothek.coordinator.get_async_client")
async def test_renew_all(mock_get_client, mock_backend):
    coordinator = _make_coordinator()

    with patch.object(coordinator, "_create_backend", return_value=mock_backend):
        with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
            count = await coordinator.renew_all()

    assert count == 2
    mock_backend.renew_all.assert_awaited_once()


def test_update_interval():
    coordinator = _make_coordinator()
    assert coordinator.update_interval == timedelta(minutes=DEFAULT_SCAN_INTERVAL)
