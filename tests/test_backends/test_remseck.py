"""Tests for the Remseck (Koha/LMSCloud) library backend."""

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from freezegun import freeze_time

from custom_components.stadtbibliothek.backends.base import AuthenticationError, LoanItem, ParseError
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
    assert loan.item_id == "500001"
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
    assert overdue.item_id == "12346"  # no renew checkbox -> falls back to biblionumber
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


# --- renew_loan ---


@respx.mock
async def test_renew_loan_extracts_borrowernumber(checkouts_html: str) -> None:
    """get_loans extracts borrowernumber from the renewal form."""
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts_html))

    backend = RemseckBackend()
    try:
        await backend.get_loans()
        assert backend._borrowernumber == "99001234"
    finally:
        await backend.close()


@respx.mock
async def test_renew_loan_success(checkouts_html: str) -> None:
    """Renewal POSTs item + borrowernumber; success when redirect URL contains renewed=<item>."""
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts_html))
    # The real server does: POST -> 302 to opac-user.pl?renewed=500001 -> 200
    # With follow_redirects=True, resp.url is the final URL after redirect.
    # Mock the redirect chain: POST returns 302, GET returns 200.
    respx.post(f"{BASE_URL}/cgi-bin/koha/opac-renew.pl").mock(
        return_value=httpx.Response(
            302,
            headers={"location": f"{BASE_URL}/cgi-bin/koha/opac-user.pl?renewed=500001&renew_error="},
        )
    )
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl?renewed=500001&renew_error=").mock(
        return_value=httpx.Response(200, html=checkouts_html)
    )

    backend = RemseckBackend()
    try:
        await backend.get_loans()
        ok = await backend.renew_loan("500001")
        assert ok is True

        # Verify the POST was made with correct parameters
        renew_call = respx.calls[1]  # calls[0] is get_loans GET
        body = renew_call.request.content.decode()
        assert "item=500001" in body
        assert "borrowernumber=99001234" in body
        assert "from=opac_user" in body
    finally:
        await backend.close()


@respx.mock
async def test_renew_loan_failure(checkouts_html: str) -> None:
    """Renewal fails when the server redirect does not contain the item in renewed=."""
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts_html))
    respx.post(f"{BASE_URL}/cgi-bin/koha/opac-renew.pl").mock(
        return_value=httpx.Response(
            302,
            headers={"location": f"{BASE_URL}/cgi-bin/koha/opac-user.pl?renewed=&renew_error="},
        )
    )
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl?renewed=&renew_error=").mock(
        return_value=httpx.Response(200, html=checkouts_html)
    )

    backend = RemseckBackend()
    try:
        await backend.get_loans()
        ok = await backend.renew_loan("500001")
        assert ok is False
    finally:
        await backend.close()


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
    with (
        patch.object(backend, "get_loans", new=AsyncMock(return_value=loans)),
        patch.object(backend, "renew_loan", new=AsyncMock(return_value=True)) as mock_renew,
    ):
        count = await backend.renew_all()

        assert count == 1
        mock_renew.assert_awaited_once_with("A")


@freeze_time("2026-04-11")
async def test_renew_all_custom_threshold() -> None:
    """Custom threshold=5 only renews loans due within 5 days."""
    backend = RemseckBackend()
    loans = [
        _loan("A", date(2026, 4, 14), max_renewals=3, times_renewed=0),  # 3 days left -> renew
        _loan("B", date(2026, 4, 20), max_renewals=3, times_renewed=0),  # 9 days left -> skip
    ]
    with (
        patch.object(backend, "get_loans", new=AsyncMock(return_value=loans)),
        patch.object(backend, "renew_loan", new=AsyncMock(return_value=True)) as mock_renew,
    ):
        count = await backend.renew_all(days_remaining_threshold=5)

        assert count == 1
        mock_renew.assert_awaited_once_with("A")


@freeze_time("2026-04-11")
async def test_renew_all_renews_overdue_books() -> None:
    """Overdue books (negative days_remaining) are always renewed."""
    backend = RemseckBackend()
    loans = [
        _loan("A", date(2026, 4, 1), max_renewals=3, times_renewed=0),  # -10 days -> overdue -> renew
        _loan("B", date(2026, 4, 30), max_renewals=3, times_renewed=0),  # 19 days left -> skip
    ]
    with (
        patch.object(backend, "get_loans", new=AsyncMock(return_value=loans)),
        patch.object(backend, "renew_loan", new=AsyncMock(return_value=True)) as mock_renew,
    ):
        count = await backend.renew_all(days_remaining_threshold=5)

        assert count == 1
        mock_renew.assert_awaited_once_with("A")


@freeze_time("2026-04-11")
async def test_renew_all_skips_no_renewals_left() -> None:
    """Loans with no renewals left are skipped even if within threshold."""
    backend = RemseckBackend()
    loans = [
        _loan("A", date(2026, 4, 14), max_renewals=3, times_renewed=3),  # 3 days but 0 renewals left
    ]
    with (
        patch.object(backend, "get_loans", new=AsyncMock(return_value=loans)),
        patch.object(backend, "renew_loan", new=AsyncMock(return_value=True)) as mock_renew,
    ):
        count = await backend.renew_all()

        assert count == 0
        mock_renew.assert_not_awaited()


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
    with (
        patch.object(backend, "get_loans", new=AsyncMock(return_value=[loan])),
        patch.object(backend, "renew_loan", new=AsyncMock(return_value=True)) as mock_renew,
    ):
        count = await backend.renew_all()

        assert count == 1
        mock_renew.assert_awaited_once_with("U1")


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
    with (
        patch.object(backend, "get_loans", new=AsyncMock(return_value=[loan])),
        patch.object(backend, "renew_loan", new=AsyncMock(return_value=True)) as mock_renew,
    ):
        count = await backend.renew_all()

        assert count == 0
        mock_renew.assert_not_awaited()


@respx.mock
async def test_base_url_override_redirects_all_requests(checkouts_html: str) -> None:
    """A custom base_url points every request at the given host.

    Needed to run against a local test server (and against other LMSCloud
    installations, which all share the same Koha layout).
    """
    fake = "http://fake.local"
    login = respx.post(f"{fake}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts_html))
    loans = respx.get(f"{fake}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts_html))
    backend = RemseckBackend(base_url=fake)
    try:
        assert backend.base_url == fake
        await backend.login("12345", "01.01.1990")
        await backend.get_loans()
    finally:
        await backend.close()

    assert login.called
    assert loans.called


async def test_base_url_defaults_to_the_class_constant() -> None:
    backend = RemseckBackend()
    try:
        assert backend.base_url == RemseckBackend.BASE_URL
    finally:
        await backend.close()


async def test_close_does_not_close_an_injected_client() -> None:
    """A caller that supplies its own client keeps ownership of it.

    bib-tracker shares one rate-limited client across all accounts; closing it
    from the first backend that finishes would break every other account.
    """
    client = httpx.AsyncClient()
    try:
        backend = RemseckBackend(client)
        await backend.close()
        assert client.is_closed is False
    finally:
        await client.aclose()


async def test_close_closes_a_client_it_created() -> None:
    backend = RemseckBackend()
    await backend.close()
    assert backend._client.is_closed is True


@respx.mock
async def test_get_loans_on_a_logged_out_page_raises_parse_error(login_html: str) -> None:
    """A session that silently expired must not look like "nothing borrowed"."""
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=login_html))
    backend = RemseckBackend()
    try:
        with pytest.raises(ParseError):
            await backend.get_loans()
    finally:
        await backend.close()


@respx.mock
async def test_get_loans_on_an_empty_account_returns_empty(no_checkouts_html: str) -> None:
    """An account page without a checkout table legitimately has no loans."""
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=no_checkouts_html))
    backend = RemseckBackend()
    try:
        assert await backend.get_loans() == []
    finally:
        await backend.close()


@respx.mock
async def test_get_fees_on_a_logged_out_page_raises_parse_error(login_html: str) -> None:
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-account.pl").mock(return_value=httpx.Response(200, html=login_html))
    backend = RemseckBackend()
    try:
        with pytest.raises(ParseError):
            await backend.get_fees()
    finally:
        await backend.close()


@respx.mock
async def test_get_fees_on_an_empty_account_returns_empty(no_fees_html: str) -> None:
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-account.pl").mock(return_value=httpx.Response(200, html=no_fees_html))
    backend = RemseckBackend()
    try:
        assert await backend.get_fees() == []
    finally:
        await backend.close()


async def test_remseck_reports_fee_support() -> None:
    assert RemseckBackend.supports_fees is True


@pytest.fixture
def detail_html() -> str:
    return _read_fixture("remseck_detail.html")


@pytest.fixture
def detail_no_isbn_html() -> str:
    return _read_fixture("remseck_detail_no_isbn.html")


@respx.mock
async def test_get_loans_extracts_detail_url(checkouts_html: str) -> None:
    """Every checkout row links its catalogue record; absolutise it so the
    URL stays usable outside the OPAC."""
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts_html))
    backend = RemseckBackend()
    try:
        loans = await backend.get_loans()
    finally:
        await backend.close()

    assert loans[0].detail_url == f"{BASE_URL}/cgi-bin/koha/opac-detail.pl?biblionumber=12345"


@respx.mock
async def test_get_loans_extracts_cover_url(checkouts_html: str) -> None:
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts_html))
    backend = RemseckBackend()
    try:
        loans = await backend.get_loans()
    finally:
        await backend.close()

    assert loans[1].cover_url == "https://cover.ekz.de/004251192118868.jpg"


@respx.mock
async def test_get_loans_ignores_the_no_image_placeholder(checkouts_html: str) -> None:
    """Koha renders a placeholder graphic when it has no jacket; reporting it
    as a cover would give every unknown title the same broken image."""
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts_html))
    backend = RemseckBackend()
    try:
        loans = await backend.get_loans()
    finally:
        await backend.close()

    assert loans[0].cover_url is None


@respx.mock
async def test_fetch_details_fills_isbn_and_cover(checkouts_html: str, detail_html: str) -> None:
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts_html))
    detail = respx.get(f"{BASE_URL}/cgi-bin/koha/opac-detail.pl", params={"biblionumber": "12345"}).mock(
        return_value=httpx.Response(200, html=detail_html)
    )
    backend = RemseckBackend()
    try:
        loans = await backend.get_loans()
        enriched = await backend.fetch_details(loans[0])
    finally:
        await backend.close()

    assert detail.called
    assert enriched.isbn == "9783522621885"
    assert enriched.cover_url == "https://static.onleihe.de/images/bonnier/20220429/9783522621885/tn9783522621885l.jpg"


@respx.mock
async def test_fetch_details_does_not_mistake_a_product_ean_for_an_isbn(
    checkouts_html: str, detail_no_isbn_html: str
) -> None:
    """Non-book media carry a GTIN in the EAN field. Only 978/979 prefixes are
    ISBN-13s; anything else must not be reported as one."""
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts_html))
    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-detail.pl", params={"biblionumber": "12345"}).mock(
        return_value=httpx.Response(200, html=detail_no_isbn_html)
    )
    backend = RemseckBackend()
    try:
        loans = await backend.get_loans()
        enriched = await backend.fetch_details(loans[0])
    finally:
        await backend.close()

    assert enriched.isbn is None
    assert enriched.cover_url == "https://cover.ekz.de/004251192118868.jpg"


async def test_fetch_details_without_a_detail_url_is_a_no_op() -> None:
    loan = LoanItem(title="Test", item_id="1", due_date=date.today())
    backend = RemseckBackend()
    try:
        assert await backend.fetch_details(loan) is loan
    finally:
        await backend.close()


async def test_remseck_supports_details() -> None:
    assert RemseckBackend.supports_details is True


async def test_stuttgart_fetch_details_is_a_no_op() -> None:
    """aDIS detail pages need a separate session-bound flow; the default
    implementation must leave the loan untouched rather than pretend."""
    from custom_components.stadtbibliothek.backends.stuttgart import StuttgartBackend

    loan = LoanItem(title="Test", item_id="1", due_date=date.today(), detail_url="https://example.invalid/x")
    backend = StuttgartBackend()
    try:
        assert await backend.fetch_details(loan) is loan
    finally:
        await backend.close()

    assert StuttgartBackend.supports_details is False


@respx.mock
async def test_the_checkouts_page_needs_no_useraccount_wrapper(checkouts_html: str) -> None:
    """A current Koha wraps only some tabs in #useraccount -- the fees page
    has it, the checkouts page does not. Depending on it made a perfectly good
    session look expired, and every loan vanish."""
    assert 'id="useraccount"' not in checkouts_html

    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts_html))
    backend = RemseckBackend()
    try:
        loans = await backend.get_loans()
    finally:
        await backend.close()

    assert len(loans) == 4


@respx.mock
async def test_any_masthead_login_marker_is_enough(no_checkouts_html: str) -> None:
    """Which marker a Koha renders varies by version, so any one will do."""
    for marker in ("logout", "loggedinusername", "loggedinuser-menu"):
        assert marker in no_checkouts_html

    respx.get(f"{BASE_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=no_checkouts_html))
    backend = RemseckBackend()
    try:
        assert await backend.get_loans() == []
    finally:
        await backend.close()
