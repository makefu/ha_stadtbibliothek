"""Tests for Stadtbibliothek coordinator."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.stadtbibliothek.backends.base import (
    AccountInfo,
    AuthenticationError,
    LibraryType,
    ParseError,
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


async def test_update_data_returns_account_info(mock_backend, sample_loans, sample_fees):
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


async def test_update_data_auth_error_raises_update_failed():
    coordinator = _make_coordinator()
    backend = AsyncMock()
    backend.login = AsyncMock(side_effect=AuthenticationError("bad"))

    with patch.object(coordinator, "_create_backend", return_value=backend):
        with pytest.raises(UpdateFailed, match="Authentication failed"):
            await coordinator._async_update_data()


async def test_update_data_generic_error_raises_update_failed():
    coordinator = _make_coordinator()
    backend = AsyncMock()
    backend.login = AsyncMock(side_effect=RuntimeError("network down"))

    with patch.object(coordinator, "_create_backend", return_value=backend):
        with pytest.raises(UpdateFailed, match="Error fetching data"):
            await coordinator._async_update_data()


async def test_renew_loan(mock_backend):
    coordinator = _make_coordinator()

    with patch.object(coordinator, "_create_backend", return_value=mock_backend):
        # Prevent the refresh from running a real update
        with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
            result = await coordinator.renew_loan("12345")

    assert result is True
    mock_backend.renew_loan.assert_awaited_once_with("12345")


async def test_renew_all(mock_backend, sample_loans, sample_account):
    coordinator = _make_coordinator()
    coordinator.data = sample_account

    with patch.object(coordinator, "_create_backend", return_value=mock_backend):
        with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
            result = await coordinator.renew_all()

    assert isinstance(result, dict)
    assert "renewed" in result
    assert "total_attempted" in result
    assert "results" in result
    # Only the first loan (can_be_renewed=True, days_remaining<=14) is eligible
    assert result["total_attempted"] == 1
    assert result["renewed"] == 1
    assert len(result["results"]) == 1
    r = result["results"][0]
    assert r["item_id"] == "12345"
    assert r["title"] == "Python Crash Course"
    assert r["author"] == "Eric Matthes"
    assert r["due_date"] == date.today().isoformat()
    assert r["media_type"] == "Book"
    assert r["times_renewed"] == 1
    assert r["max_renewals"] == 3
    assert r["renewals_left"] == 2
    assert r["success"] is True
    assert r["error"] is None


async def test_renew_all_with_custom_threshold(mock_backend, sample_account):
    coordinator = _make_coordinator()
    coordinator.data = sample_account

    with patch.object(coordinator, "_create_backend", return_value=mock_backend):
        with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
            result = await coordinator.renew_all(days_remaining_threshold=7)

    # days_remaining for first loan is 0 (due today), so it qualifies for threshold=7
    assert result["total_attempted"] == 1
    assert result["renewed"] == 1


async def test_refresh_required_set_after_renew_all(mock_backend, sample_account):
    coordinator = _make_coordinator()
    coordinator.data = sample_account
    assert coordinator.refresh_required is False

    with patch.object(coordinator, "_create_backend", return_value=mock_backend):
        with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
            await coordinator.renew_all()

    assert coordinator.refresh_required is True


async def test_refresh_required_set_after_renew_loan(mock_backend):
    coordinator = _make_coordinator()
    assert coordinator.refresh_required is False

    with patch.object(coordinator, "_create_backend", return_value=mock_backend):
        with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
            await coordinator.renew_loan("12345")

    assert coordinator.refresh_required is True


async def test_refresh_required_cleared_after_update(mock_backend, sample_account):
    coordinator = _make_coordinator()
    coordinator.data = sample_account

    # First do a renewal to set the flag
    with patch.object(coordinator, "_create_backend", return_value=mock_backend):
        with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
            await coordinator.renew_all()

    assert coordinator.refresh_required is True

    # Now do a data update — flag should clear
    with patch.object(coordinator, "_create_backend", return_value=mock_backend):
        await coordinator._async_update_data()

    assert coordinator.refresh_required is False


async def test_refresh_required_not_set_on_failed_renewal(sample_account):
    coordinator = _make_coordinator()
    coordinator.data = sample_account
    backend = AsyncMock()
    backend.login = AsyncMock(side_effect=RuntimeError("network down"))

    with patch.object(coordinator, "_create_backend", return_value=backend):
        with pytest.raises(UpdateFailed):
            await coordinator.renew_all()

    assert coordinator.refresh_required is False


async def test_renew_all_failed_renewal_has_error(sample_account):
    coordinator = _make_coordinator()
    coordinator.data = sample_account
    backend = AsyncMock()
    backend.login = AsyncMock()
    backend.renew_loan = AsyncMock(return_value=False)

    with patch.object(coordinator, "_create_backend", return_value=backend):
        with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
            result = await coordinator.renew_all()

    assert result["renewed"] == 0
    assert result["total_attempted"] == 1
    r = result["results"][0]
    assert r["success"] is False
    assert "Renewal failed" in r["error"]
    assert r["item_id"] == "12345"
    assert r["title"] == "Python Crash Course"
    assert r["author"] == "Eric Matthes"
    assert r["due_date"] == date.today().isoformat()
    assert r["media_type"] == "Book"
    assert r["times_renewed"] == 1
    assert r["max_renewals"] == 3
    assert r["renewals_left"] == 2


async def test_renew_all_no_data_raises_update_failed():
    coordinator = _make_coordinator()
    # coordinator.data is None by default

    with pytest.raises(UpdateFailed, match="No loan data"):
        await coordinator.renew_all()


def test_update_interval():
    coordinator = _make_coordinator()
    assert coordinator.update_interval == timedelta(minutes=DEFAULT_SCAN_INTERVAL)


async def test_update_data_parse_error_raises_update_failed():
    """A changed page layout must make the entity unavailable, not report
    zero loans -- which would look like everything had been returned."""
    coordinator = _make_coordinator()
    backend = AsyncMock()
    backend.get_loans = AsyncMock(side_effect=ParseError("no account page"))

    with patch.object(coordinator, "_create_backend", return_value=backend):
        with pytest.raises(UpdateFailed, match="Unexpected page layout"):
            await coordinator._async_update_data()


async def test_update_data_enriches_loans_when_the_backend_offers_details(mock_backend, sample_loans):
    """Covers cost one request per loan, so the coordinator asks only when the
    backend advertises it -- and passes each loan through exactly once."""
    mock_backend.supports_details = True
    seen: list[str] = []

    async def enrich(loan):
        seen.append(loan.item_id)
        loan.cover_url = "https://cover.example/x.jpg"
        return loan

    mock_backend.fetch_details = AsyncMock(side_effect=enrich)
    coordinator = _make_coordinator()
    with patch.object(coordinator, "_create_backend", return_value=mock_backend):
        data = await coordinator._async_update_data()

    assert seen == [loan.item_id for loan in sample_loans]
    assert data.loans == sample_loans
    assert all(loan.cover_url == "https://cover.example/x.jpg" for loan in data.loans)


async def test_a_failed_detail_lookup_keeps_the_loan_but_not_the_cover(mock_backend, sample_loans):
    """One title's lookup timing out must not fail the refresh: the sensor
    still has its loan listing, just without that cover."""
    mock_backend.supports_details = True
    mock_backend.fetch_details = AsyncMock(side_effect=TimeoutError("search timed out"))
    coordinator = _make_coordinator()

    with patch.object(coordinator, "_create_backend", return_value=mock_backend):
        data = await coordinator._async_update_data()

    assert data.loans == sample_loans
    assert all(loan.cover_url is None for loan in data.loans)


async def test_update_data_skips_details_when_unadvertised(mock_backend):
    """Polling every few minutes: a backend that advertises nothing must not
    be billed for a request per loan."""
    mock_backend.supports_details = False
    mock_backend.fetch_details = AsyncMock()
    coordinator = _make_coordinator()

    with patch.object(coordinator, "_create_backend", return_value=mock_backend):
        await coordinator._async_update_data()

    mock_backend.fetch_details.assert_not_awaited()
