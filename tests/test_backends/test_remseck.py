"""Tests for the Remseck (Koha/LMSCloud) library backend."""

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from freezegun import freeze_time

from custom_components.stadtbibliothek.backends.base import AuthenticationError, LoanItem
from custom_components.stadtbibliothek.backends.remseck import RemseckBackend

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://mt-remseck.lmscloud.net"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def login_html() -> str:
    return _read_fixture("remseck_login.html")


@pytest.fixture
def checkouts_html() -> str:
    return _read_fixture("remseck_checkouts.html")


@pytest.fixture
def fees_html() -> str:
    return _read_fixture("remseck_fees.html")


@pytest.fixture
def no_checkouts_html() -> str:
    return _read_fixture("remseck_no_checkouts.html")


@pytest.fixture
def no_fees_html() -> str:
    return _read_fixture("remseck_no_fees.html")


@respx.mock
async def test_login_success(checkouts_html: str) -> None:
    """Successful login returns the account page (no login form)."""
    respx.post(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts_html))
    backend = RemseckBackend()
    try:
        await backend.login("12345", "01.01.1990")
        # No exception means success
    finally:
        await backend.close()


@respx.mock
async def test_login_failure(login_html: str) -> None:
    """Failed login returns the login page again -> AuthenticationError."""
    respx.post(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=login_html))
    backend = RemseckBackend()
    try:
        with pytest.raises(AuthenticationError):
            await backend.login("wrong", "wrong")
    finally:
        await backend.close()


@respx.mock
@freeze_time("2026-04-11")
async def test_get_loans(checkouts_html: str) -> None:
    """Parse the checkouts table into LoanItem objects."""
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts_html))
    backend = RemseckBackend()
    try:
        loans = await backend.get_loans()
    finally:
        await backend.close()

    assert len(loans) == 4

    # First item: Die unendliche Geschichte
    # no-renewal-before 08.04 is in the past -> can renew
    loan = loans[0]
    assert loan.title == "Die unendliche Geschichte"
    assert loan.author == "Ende, Michael"
    assert loan.item_id == "12345"
    assert loan.due_date == date(2026, 4, 15)
    assert loan.checkout_date is None
    assert loan.media_type == "Buch"
    assert loan.library_branch == "Mediathek im KUBUS"
    assert loan.call_number == "End"
    assert loan.times_renewed == 1
    assert loan.max_renewals == 3
    assert loan.renewals_left == 2
    assert loan.can_be_renewed is True

    # Second item: Momo (overdue, no renewals left, renewals-disabled)
    overdue = loans[1]
    assert overdue.title == "Momo"
    assert overdue.item_id == "12346"
    assert overdue.due_date == date(2026, 3, 1)
    assert overdue.times_renewed == 3
    assert overdue.max_renewals == 3
    assert overdue.renewals_left == 0
    assert overdue.can_be_renewed is False

    # Third item: Tschick (Hörbuch)
    # no-renewal-before 13.04 is AFTER test date 2026-04-11 -> cannot renew yet
    assert loans[2].title == "Tschick"
    assert loans[2].due_date == date(2026, 4, 20)
    assert loans[2].media_type == "Hörbuch"
    assert loans[2].times_renewed == 0
    assert loans[2].max_renewals == 2
    assert loans[2].renewals_left == 2
    assert loans[2].can_be_renewed is False

    # Fourth item: Krabat
    # no-renewal-before 24.04 is AFTER test date -> cannot renew yet
    assert loans[3].title == "Krabat"
    assert loans[3].author == "Preußler, Otfried"
    assert loans[3].due_date == date(2026, 5, 1)
    assert loans[3].times_renewed == 0
    assert loans[3].max_renewals == 3
    assert loans[3].can_be_renewed is False


@respx.mock
async def test_get_loans_empty(no_checkouts_html: str) -> None:
    """No checkouts table present -> empty list."""
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=no_checkouts_html))
    backend = RemseckBackend()
    try:
        loans = await backend.get_loans()
    finally:
        await backend.close()

    assert loans == []


@respx.mock
async def test_get_fees(fees_html: str) -> None:
    """Parse the fees table into FeeItem objects."""
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-account.pl").mock(return_value=httpx.Response(200, html=fees_html))
    backend = RemseckBackend()
    try:
        fees = await backend.get_fees()
    finally:
        await backend.close()

    assert len(fees) == 2

    assert fees[0].description == "Momo (T00012346)"
    assert fees[0].amount == 2.50
    assert fees[0].date == date(2026, 3, 10)

    assert fees[1].description == "Momo (T00012346) - 1. Mahnung"
    assert fees[1].amount == 1.00
    assert fees[1].date == date(2026, 4, 1)


@respx.mock
async def test_get_fees_none(no_fees_html: str) -> None:
    """No fees table present -> empty list."""
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-account.pl").mock(return_value=httpx.Response(200, html=no_fees_html))
    backend = RemseckBackend()
    try:
        fees = await backend.get_fees()
    finally:
        await backend.close()

    assert fees == []


# --- renew_all with days_remaining_threshold ---


def _loan(
    item_id: str,
    due_date: date,
    max_renewals: int = 3,
    times_renewed: int = 0,
    can_be_renewed: bool | None = None,
) -> LoanItem:
    if can_be_renewed is None:
        can_be_renewed = times_renewed < max_renewals
    return LoanItem(
        title=f"Book {item_id}",
        item_id=item_id,
        due_date=due_date,
        max_renewals=max_renewals,
        times_renewed=times_renewed,
        can_be_renewed=can_be_renewed,
    )


@freeze_time("2026-04-11")
async def test_renew_all_default_threshold_only_renews_due_within_14_days() -> None:
    """With default threshold=14, only loans due within 14 days are renewed."""
    backend = RemseckBackend()
    loans = [
        _loan("A", date(2026, 4, 20), max_renewals=3, times_renewed=0),  # 9 days left -> renew
        _loan("B", date(2026, 4, 30), max_renewals=3, times_renewed=0),  # 19 days left -> skip
    ]
    backend.get_loans = AsyncMock(return_value=loans)
    backend.renew_loan = AsyncMock(return_value=True)

    count = await backend.renew_all()

    assert count == 1
    backend.renew_loan.assert_awaited_once_with("A")


@freeze_time("2026-04-11")
async def test_renew_all_custom_threshold() -> None:
    """Custom threshold=5 only renews loans due within 5 days."""
    backend = RemseckBackend()
    loans = [
        _loan("A", date(2026, 4, 14), max_renewals=3, times_renewed=0),  # 3 days left -> renew
        _loan("B", date(2026, 4, 20), max_renewals=3, times_renewed=0),  # 9 days left -> skip
    ]
    backend.get_loans = AsyncMock(return_value=loans)
    backend.renew_loan = AsyncMock(return_value=True)

    count = await backend.renew_all(days_remaining_threshold=5)

    assert count == 1
    backend.renew_loan.assert_awaited_once_with("A")


@freeze_time("2026-04-11")
async def test_renew_all_renews_overdue_books() -> None:
    """Overdue books (negative days_remaining) are always renewed."""
    backend = RemseckBackend()
    loans = [
        _loan("A", date(2026, 4, 1), max_renewals=3, times_renewed=0),  # -10 days -> overdue -> renew
        _loan("B", date(2026, 4, 30), max_renewals=3, times_renewed=0),  # 19 days left -> skip
    ]
    backend.get_loans = AsyncMock(return_value=loans)
    backend.renew_loan = AsyncMock(return_value=True)

    count = await backend.renew_all(days_remaining_threshold=5)

    assert count == 1
    backend.renew_loan.assert_awaited_once_with("A")


@freeze_time("2026-04-11")
async def test_renew_all_skips_no_renewals_left() -> None:
    """Loans with no renewals left are skipped even if within threshold."""
    backend = RemseckBackend()
    loans = [
        _loan("A", date(2026, 4, 14), max_renewals=3, times_renewed=3),  # 3 days but 0 renewals left
    ]
    backend.get_loans = AsyncMock(return_value=loans)
    backend.renew_loan = AsyncMock(return_value=True)

    count = await backend.renew_all()

    assert count == 0
    backend.renew_loan.assert_not_awaited()


@freeze_time("2026-04-11")
async def test_renew_all_renews_unlimited_renewals() -> None:
    """Loans with max_renewals=None (unlimited) should still be renewed if can_be_renewed."""
    backend = RemseckBackend()
    loan = LoanItem(
        title="Unlimited Book",
        item_id="U1",
        due_date=date(2026, 4, 14),  # 3 days left
        max_renewals=None,
        times_renewed=0,
        can_be_renewed=True,
    )
    backend.get_loans = AsyncMock(return_value=[loan])
    backend.renew_loan = AsyncMock(return_value=True)

    count = await backend.renew_all()

    assert count == 1
    backend.renew_loan.assert_awaited_once_with("U1")


@freeze_time("2026-04-11")
async def test_renew_all_skips_not_renewable() -> None:
    """Loans with can_be_renewed=False are skipped regardless of renewals_left."""
    backend = RemseckBackend()
    loan = LoanItem(
        title="Blocked Book",
        item_id="B1",
        due_date=date(2026, 4, 14),  # 3 days left
        max_renewals=3,
        times_renewed=1,
        can_be_renewed=False,  # explicitly blocked
    )
    backend.get_loans = AsyncMock(return_value=[loan])
    backend.renew_loan = AsyncMock(return_value=True)

    count = await backend.renew_all()

    assert count == 0
    backend.renew_loan.assert_not_awaited()
