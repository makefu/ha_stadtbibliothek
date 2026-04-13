"""Tests for the Stuttgart aDIS/BMS library backend."""

from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from custom_components.stadtbibliothek.backends.base import AuthenticationError, RenewalError
from custom_components.stadtbibliothek.backends.stuttgart import StuttgartBackend

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://stadtbibliothek-stuttgart.de"
SESSION_URL = f"{BASE_URL}/aDISWeb/app;jsessionid=TESTsession123ABC"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _mock_login_flow(router: respx.MockRouter) -> None:
    """Set up respx mocks for the full 3-step login flow."""
    # Step 1: GET start page
    router.get(f"{BASE_URL}?service=direct/0/Home/$DirectLink&sp=SOPAC").mock(
        return_value=httpx.Response(200, text=_read_fixture("stuttgart_home.html")),
    )
    # Steps 1+2: POSTs to same jsessionid URL, return login form then logged-in page
    router.post(SESSION_URL).mock(
        side_effect=[
            httpx.Response(200, text=_read_fixture("stuttgart_login_form.html")),
            httpx.Response(200, text=_read_fixture("stuttgart_logged_in.html")),
        ],
    )


@respx.mock
async def test_login_step1_extracts_session() -> None:
    """Step 1 GET extracts jsessionid from form action."""
    _mock_login_flow(respx)

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
    finally:
        await backend.close()

    assert backend._login_url is not None
    assert "jsessionid=TESTsession123ABC" in backend._login_url


@respx.mock
async def test_login_step2_sends_credentials() -> None:
    """Step 2 POST includes username and password in form data."""
    _mock_login_flow(respx)

    backend = StuttgartBackend()
    try:
        await backend.login("5980610", "secret123")
    finally:
        await backend.close()

    # The second POST (index 1) is the credential submission
    post_route = respx.routes[1]
    assert post_route.call_count == 2
    body = post_route.calls[1].request.content.decode()
    assert "%24Textfield=5980610" in body
    assert "%24Textfield%240=secret123" in body


@respx.mock
async def test_login_success() -> None:
    """Full 3-step login completes without error."""
    _mock_login_flow(respx)

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
    finally:
        await backend.close()

    assert backend._ausleihen_url is not None
    assert "SBK00000001" in backend._ausleihen_url


@respx.mock
async def test_login_failure() -> None:
    """Login raises AuthenticationError when konto-services is missing."""
    respx.get(f"{BASE_URL}?service=direct/0/Home/$DirectLink&sp=SOPAC").mock(
        return_value=httpx.Response(200, text=_read_fixture("stuttgart_home.html")),
    )
    # Return login form, then a failure page (no konto-services)
    respx.post(SESSION_URL).mock(
        side_effect=[
            httpx.Response(200, text=_read_fixture("stuttgart_login_form.html")),
            httpx.Response(
                200,
                text="<html><body><div>Falsche Lesernummer</div></body></html>",
            ),
        ],
    )

    backend = StuttgartBackend()
    with pytest.raises(AuthenticationError, match="konto-services"):
        try:
            await backend.login("wrong", "creds")
        finally:
            await backend.close()


@respx.mock
async def test_get_loans() -> None:
    """Parses loan table into LoanItem objects with correct fields."""
    _mock_login_flow(respx)
    respx.get(url__regex=r".*SBK00000001.*").mock(
        return_value=httpx.Response(200, text=_read_fixture("stuttgart_ausleihen.html")),
    )

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        loans = await backend.get_loans()
    finally:
        await backend.close()

    assert len(loans) == 4

    # Row 1: CD with media type prefix
    assert loans[0].title == "Die drei Fragezeichen - Folge 220"
    assert loans[0].media_type == "CD"
    assert loans[0].due_date == date(2026, 5, 15)
    assert loans[0].library_branch == "Zentralbücherei"
    assert loans[0].times_renewed == 3
    assert loans[0].can_be_renewed is True
    assert loans[0].max_renewals == 8
    assert loans[0].item_id == "M-CD-K DRE"

    # Row 2: Book with ¬ sort indicators stripped, author from " / " split
    assert loans[1].title == "Der kleine Prinz"
    assert loans[1].author == "Saint-Exupéry, Antoine de"
    assert loans[1].item_id == "12345678"
    assert loans[1].media_type is None
    assert loans[1].times_renewed == 0
    assert loans[1].can_be_renewed is True

    # Row 3: Game with [Konventionelles Spiel] prefix
    assert loans[2].title == "Catan - Das Spiel"
    assert loans[2].media_type == "Konventionelles Spiel"
    assert loans[2].times_renewed == 7
    assert loans[2].can_be_renewed is True

    # Row 4: Not renewable, 8 renewals used, author from " / " split
    assert loans[3].title == "Python Crashkurs"
    assert loans[3].author == "Matthes, Eric"
    assert loans[3].item_id == "87654321"
    assert loans[3].can_be_renewed is False
    assert loans[3].times_renewed == 8
    assert loans[3].renewals_left == 0
    assert loans[3].is_overdue is True  # 10.04.2026 is in the past


@respx.mock
async def test_get_loans_parses_extension_info() -> None:
    """Extension column parsing: verlängerbar vs nicht verlängerbar, renewal counts."""
    _mock_login_flow(respx)
    respx.get(url__regex=r".*SBK00000001.*").mock(
        return_value=httpx.Response(200, text=_read_fixture("stuttgart_ausleihen.html")),
    )

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        loans = await backend.get_loans()
    finally:
        await backend.close()

    # "verlängerbar - Stand ..." with "3 Verlängerungen"
    assert loans[0].can_be_renewed is True
    assert loans[0].times_renewed == 3
    assert loans[0].renewals_left == 5

    # "verlängerbar - Stand ..." with "0 Verlängerungen"
    assert loans[1].can_be_renewed is True
    assert loans[1].times_renewed == 0
    assert loans[1].renewals_left == 8

    # "Heute verlängert" with "7 Verlängerungen"
    assert loans[2].can_be_renewed is True
    assert loans[2].times_renewed == 7
    assert loans[2].renewals_left == 1

    # "nicht verlängerbar" with "8 Verlängerungen"
    assert loans[3].can_be_renewed is False
    assert loans[3].times_renewed == 8
    assert loans[3].renewals_left == 0


@respx.mock
async def test_get_loans_skips_media_type_prefix() -> None:
    """Media type like [CD] or [Konventionelles Spiel] is extracted, not in title."""
    _mock_login_flow(respx)
    respx.get(url__regex=r".*SBK00000001.*").mock(
        return_value=httpx.Response(200, text=_read_fixture("stuttgart_ausleihen.html")),
    )

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        loans = await backend.get_loans()
    finally:
        await backend.close()

    # [CD] extracted as media_type, not in title
    assert not loans[0].title.startswith("[")
    assert loans[0].media_type == "CD"

    # No prefix -> media_type is None
    assert loans[1].media_type is None

    # [Konventionelles Spiel] extracted
    assert not loans[2].title.startswith("[")
    assert loans[2].media_type == "Konventionelles Spiel"

    # No prefix
    assert loans[3].media_type is None


@respx.mock
async def test_get_loans_strips_sort_indicators() -> None:
    """Non-sort indicator characters (¬) are stripped from titles."""
    _mock_login_flow(respx)
    respx.get(url__regex=r".*SBK00000001.*").mock(
        return_value=httpx.Response(200, text=_read_fixture("stuttgart_ausleihen.html")),
    )

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        loans = await backend.get_loans()
    finally:
        await backend.close()

    # Row 2 fixture has ¬Der¬ kleine Prinz — ¬ must be stripped
    assert "¬" not in loans[1].title
    assert loans[1].title == "Der kleine Prinz"


def _mock_login_and_ausleihen(router: respx.MockRouter) -> None:
    """Set up mocks for login + Ausleihen page load."""
    _mock_login_flow(router)
    router.get(url__regex=r".*SBK00000001.*").mock(
        return_value=httpx.Response(200, text=_read_fixture("stuttgart_ausleihen.html")),
    )


@respx.mock
async def test_renew_loan_success() -> None:
    """renew_loan posts the correct checkbox and returns True on success."""
    _mock_login_and_ausleihen(respx)
    # After checking the checkbox and POSTing, server returns the renewed page
    respx.post(url__regex=r".*jsessionid=TESTLOGIN456DEF.*").mock(
        return_value=httpx.Response(200, text=_read_fixture("stuttgart_renewed.html")),
    )

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        result = await backend.renew_loan("12345678")
    finally:
        await backend.close()

    assert result is True


@respx.mock
async def test_renew_loan_sends_correct_checkbox() -> None:
    """renew_loan selects the checkbox matching the item_id."""
    _mock_login_and_ausleihen(respx)
    respx.post(url__regex=r".*jsessionid=TESTLOGIN456DEF.*").mock(
        return_value=httpx.Response(200, text=_read_fixture("stuttgart_renewed.html")),
    )

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        await backend.renew_loan("12345678")
    finally:
        await backend.close()

    # The renewal POST is the last POST call (after the 2 login POSTs)
    # Find the POST that includes check_1 (item "12345678" is row index 1)
    last_post = respx.calls[-1]
    body = last_post.request.content.decode()
    assert "check_1=1" in body
    assert "textButton%241=Markierte" in body  # textButton$1=Markierte Medien verlängern


@respx.mock
async def test_renew_loan_item_not_found() -> None:
    """renew_loan raises RenewalError when item_id is not in the loan table."""
    _mock_login_and_ausleihen(respx)

    backend = StuttgartBackend()
    with pytest.raises(RenewalError, match="NONEXISTENT.*not found"):
        try:
            await backend.login("testuser", "testpass")
            await backend.renew_loan("NONEXISTENT")
        finally:
            await backend.close()


@respx.mock
async def test_renew_loan_server_error() -> None:
    """renew_loan raises RenewalError with server message on failure."""
    _mock_login_and_ausleihen(respx)
    respx.post(url__regex=r".*jsessionid=TESTLOGIN456DEF.*").mock(
        return_value=httpx.Response(200, text=_read_fixture("stuttgart_renew_failed.html")),
    )

    backend = StuttgartBackend()
    with pytest.raises(RenewalError, match="Maximale Anzahl"):
        try:
            await backend.login("testuser", "testpass")
            await backend.renew_loan("87654321")
        finally:
            await backend.close()


@respx.mock
async def test_renew_all_renews_eligible_loans() -> None:
    """renew_all renews loans within the days_remaining threshold."""
    _mock_login_and_ausleihen(respx)
    # Each individual renew_loan call will GET ausleihen then POST
    respx.get(url__regex=r".*jsessionid=TESTLOGIN456DEF.*").mock(
        return_value=httpx.Response(200, text=_read_fixture("stuttgart_ausleihen.html")),
    )
    respx.post(url__regex=r".*jsessionid=TESTLOGIN456DEF.*").mock(
        return_value=httpx.Response(200, text=_read_fixture("stuttgart_renewed.html")),
    )

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        # All loans have due dates within 42 days, but only 3 are renewable
        count = await backend.renew_all(days_remaining_threshold=42)
    finally:
        await backend.close()

    assert count == 3  # 3 renewable items (row 4 is "nicht verlängerbar")
