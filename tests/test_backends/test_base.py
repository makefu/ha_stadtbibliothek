from datetime import date
from typing import Any, ClassVar

import httpx
import pytest
import respx
from freezegun import freeze_time

from custom_components.stadtbibliothek.backends.base import (
    AccountInfo,
    FeeItem,
    LibraryBackend,
    LibraryType,
    LoanItem,
    RateLimitedError,
)


class _Dummy(LibraryBackend):
    """The smallest backend that can issue requests."""

    library_type = LibraryType.STUTTGART
    BASE_URL = "https://dummy.example"

    async def login(self, username: str, password: str) -> None: ...

    async def get_loans(self) -> list[LoanItem]:
        return []

    async def get_fees(self) -> list[FeeItem]:
        return []

    async def renew_loan(self, item_id: str) -> bool:
        return False

    async def renew_all(self, days_remaining_threshold: int = 14) -> int:
        return 0


def _make_loan(
    title: str = "Test Book",
    item_id: str = "123",
    due_date: date | None = None,
    **kwargs: Any,
) -> LoanItem:
    return LoanItem(title=title, item_id=item_id, due_date=due_date or date(2026, 5, 1), **kwargs)


@freeze_time("2026-04-11")
def test_loan_item_days_remaining():
    loan = _make_loan(due_date=date(2026, 4, 21))
    assert loan.days_remaining == 10


@freeze_time("2026-04-11")
def test_loan_item_overdue():
    loan = _make_loan(due_date=date(2026, 4, 5))
    assert loan.is_overdue is True


@freeze_time("2026-04-11")
def test_loan_item_not_overdue():
    loan = _make_loan(due_date=date(2026, 4, 20))
    assert loan.is_overdue is False


def test_loan_item_renewals_left():
    loan = _make_loan(max_renewals=8, times_renewed=3)
    assert loan.renewals_left == 5


def test_loan_item_renewals_left_none():
    loan = _make_loan(max_renewals=None)
    assert loan.renewals_left is None


def test_loan_item_renewals_left_zero():
    loan = _make_loan(max_renewals=3, times_renewed=5)
    assert loan.renewals_left == 0


def test_fee_item_basic():
    fee = FeeItem(description="Overdue fee", amount=2.50, date=date(2026, 3, 15))
    assert fee.description == "Overdue fee"
    assert fee.amount == 2.50
    assert fee.date == date(2026, 3, 15)


def test_account_info_defaults():
    account = AccountInfo(username="testuser", library_type=LibraryType.STUTTGART)
    assert account.loans == []
    assert account.fees == []
    assert account.total_fees == 0.0


# --- paced requests ---------------------------------------------------------


@respx.mock
async def test_a_403_raises_rate_limited_not_http_status() -> None:
    """A firewall block must be tellable from a dead link: bib-tracker backs
    off on RateLimitedError, and every other network error on the poll."""
    respx.get("https://dummy.example/").mock(
        return_value=httpx.Response(403, text="<html>Request forbidden by administrative rules.</html>")
    )
    backend = _Dummy()
    try:
        with pytest.raises(RateLimitedError) as excinfo:
            await backend._get("https://dummy.example/")
    finally:
        await backend.close()
    assert excinfo.value.status_code == 403
    assert "administrative rules" in str(excinfo.value)


@respx.mock
async def test_a_429_reports_the_retry_after_hint() -> None:
    respx.get("https://dummy.example/").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "120"}, text="slow down")
    )
    backend = _Dummy()
    try:
        with pytest.raises(RateLimitedError) as excinfo:
            await backend._get("https://dummy.example/")
    finally:
        await backend.close()
    assert excinfo.value.retry_after == 120.0


@respx.mock
async def test_a_rate_limit_raises_even_when_the_caller_checks_status() -> None:
    """check=False means "I handle 4xx myself" -- a block is still not a page."""
    respx.post("https://dummy.example/").mock(return_value=httpx.Response(403, text="nope"))
    backend = _Dummy()
    try:
        with pytest.raises(RateLimitedError):
            await backend._post("https://dummy.example/", check=False, data={"a": "b"})
    finally:
        await backend.close()


@respx.mock
async def test_client_headers_apply_to_a_borrowed_client() -> None:
    """The firewall decides by request headers; a shared client's generic
    identity must not leak into requests to an OPAC that expects a browser."""

    class _Browserish(_Dummy):
        CLIENT_HEADERS: ClassVar[dict[str, str]] = {"User-Agent": "Mozilla/5.0 browserlike"}

    route = respx.get("https://dummy.example/").mock(return_value=httpx.Response(200, text="ok"))
    client = httpx.AsyncClient()
    backend = _Browserish(client=client)
    try:
        await backend._get("https://dummy.example/")
    finally:
        await backend.close()
        await client.aclose()
    assert route.calls[-1].request.headers["User-Agent"] == "Mozilla/5.0 browserlike"


@respx.mock
async def test_pacing_spaces_requests_of_one_backend() -> None:
    import time

    respx.get("https://dummy.example/").mock(return_value=httpx.Response(200, text="ok"))
    backend = _Dummy()
    backend.REQUEST_MIN_INTERVAL = 0.05
    try:
        started = time.monotonic()
        for _ in range(3):
            await backend._get("https://dummy.example/")
        elapsed = time.monotonic() - started
    finally:
        await backend.close()
    # Two gaps of at least the interval; slack for scheduling, not for absent
    # pacing (which would return near-instantly).
    assert elapsed >= 0.09


@respx.mock
async def test_a_plain_http_error_still_raises_for_status() -> None:
    """Only 403/429 are reclassified; a 500 keeps raise_for_status semantics."""
    respx.get("https://dummy.example/").mock(return_value=httpx.Response(500, text="boom"))
    backend = _Dummy()
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await backend._get("https://dummy.example/")
    finally:
        await backend.close()
