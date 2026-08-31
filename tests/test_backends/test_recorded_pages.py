"""Parse the recorded live pages, so the backends stay honest about markup.

The fixtures next door are constructed and assert a story line by line. These
assert against `fixtures/recorded/`, captured verbatim from the two live
installations, and they deliberately assert shape rather than borrowing: how
many loans were out on the recording day is not interesting, but that Koha
renders no `#useraccount` on its checkouts tab, that aDIS renders no
`<section>` at all, and that a puzzle's EAN is not an ISBN, are exactly the
things that have broken before.

Every assertion here corresponds to a fix that was made blind against a
trimmed fixture and then found to be wrong against the real server.
"""

from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from freezegun import freeze_time

from custom_components.stadtbibliothek.backends.base import ParseError
from custom_components.stadtbibliothek.backends.remseck import RemseckBackend
from custom_components.stadtbibliothek.backends.stuttgart import StuttgartBackend

RECORDED = Path(__file__).parent / "fixtures" / "recorded"
REMSECK_URL = "https://mt-remseck.lmscloud.net"
STUTTGART_URL = "https://stadtbibliothek-stuttgart.de"

#: The day the pages in fixtures/recorded/ were captured. Renewal eligibility
#: is a comparison against today, so it only means anything frozen here.
RECORDED_ON = "2026-08-31"


def _recorded(name: str) -> str:
    return (RECORDED / name).read_text(encoding="utf-8")


@pytest.fixture
def checkouts() -> str:
    return _recorded("remseck_checkouts.html")


@pytest.fixture
def account() -> str:
    return _recorded("remseck_account.html")


@pytest.fixture
def logged_out() -> str:
    return _recorded("remseck_logged_out.html")


@pytest.fixture
def ausleihen() -> str:
    return _recorded("stuttgart_ausleihen.html")


async def _remseck_loans(html: str) -> list:
    with respx.mock:
        respx.get(f"{REMSECK_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=html))
        backend = RemseckBackend()
        try:
            return await backend.get_loans()
        finally:
            await backend.close()


async def _stuttgart_loans(html: str) -> list:
    backend = StuttgartBackend()
    backend._ausleihen_url = f"{STUTTGART_URL}/aDISWeb/app"
    with respx.mock:
        respx.get(backend._ausleihen_url).mock(return_value=httpx.Response(200, html=html))
        try:
            return await backend.get_loans()
        finally:
            await backend.close()


# --- Remseck: what the live Koha actually sends -------------------------


async def test_the_recorded_checkouts_page_parses_every_row(checkouts: str) -> None:
    """Whatever else changes, no row may be silently dropped."""
    loans = await _remseck_loans(checkouts)
    rows = checkouts.count('<td class="title">')
    assert len(loans) == rows
    assert all(loan.title for loan in loans)
    assert all(loan.due_date for loan in loans)


async def test_the_live_checkouts_tab_has_no_useraccount_wrapper(checkouts: str, account: str) -> None:
    """Koha wraps only some tabs in #useraccount: the fees page has it, the
    checkouts page does not. Requiring it made a working session look expired
    and every loan vanish."""
    assert 'id="useraccount"' not in checkouts
    assert 'id="useraccount"' in account

    assert await _remseck_loans(checkouts)


async def test_the_masthead_markers_the_live_pages_do_carry(checkouts: str) -> None:
    """What is left to detect a session by, once #useraccount is out."""
    for marker in ('id="logout"', 'class="loggedinusername"', 'id="loggedinuser-menu"'):
        assert marker in checkouts


async def test_a_logged_out_page_raises_rather_than_reporting_nothing_borrowed(logged_out: str) -> None:
    """The failure that ParseError exists for: an expired session must not
    read as an emptied account, which would look like every loan returned."""
    assert 'id="auth"' in logged_out
    with respx.mock:
        respx.get(f"{REMSECK_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=logged_out))
        backend = RemseckBackend()
        try:
            with pytest.raises(ParseError):
                await backend.get_loans()
        finally:
            await backend.close()


async def test_the_recorded_account_page_has_a_fees_table_with_no_rows(account: str) -> None:
    """ "No fees" is not the same page as "no fees table": this Koha renders
    the table either way, and an empty tbody has to mean zero, not an error."""
    assert 'id="finestable"' in account
    with respx.mock:
        respx.get(f"{REMSECK_URL}/cgi-bin/koha/opac-account.pl").mock(return_value=httpx.Response(200, html=account))
        backend = RemseckBackend()
        try:
            assert await backend.get_fees() == []
        finally:
            await backend.close()


async def test_the_borrowernumber_comes_off_the_renewal_form(checkouts: str) -> None:
    """Renewal needs it, and it is on the page even when nothing is
    renewable -- the form is rendered regardless."""
    with respx.mock:
        respx.get(f"{REMSECK_URL}/cgi-bin/koha/opac-user.pl").mock(return_value=httpx.Response(200, html=checkouts))
        backend = RemseckBackend()
        try:
            await backend.get_loans()
            assert backend._borrowernumber == "10000001"
        finally:
            await backend.close()


@freeze_time(RECORDED_ON)
async def test_a_future_no_renewal_before_blocks_renewal(checkouts: str) -> None:
    """Every recorded loan sat inside its "Keine Verlängerung vor" window.
    Reporting them renewable would have the coordinator POST renewals the
    server is certain to reject."""
    loans = await _remseck_loans(checkouts)
    assert "no-renewal-before" in checkouts
    assert not any(loan.can_be_renewed for loan in loans)


async def test_remaining_renewals_are_read_as_renewals_already_used(checkouts: str) -> None:
    """Koha counts down -- "( 0 von 3 Verlängerungen verbleiben )" means three
    used, not none. Reading it forwards inverted every count."""
    loans = await _remseck_loans(checkouts)
    assert "Verlängerungen verbleiben" in checkouts
    assert all(loan.max_renewals == 3 for loan in loans)
    exhausted = [loan for loan in loans if loan.renewals_left == 0]
    assert exhausted, "expected at least one loan with its renewals used up"
    assert all(loan.times_renewed == 3 for loan in exhausted)


async def test_covers_are_taken_from_the_jacket_cell_and_absent_when_there_is_none(checkouts: str) -> None:
    """Three of the recorded records have an empty jacket cell. Reporting a
    placeholder for those would give every unknown title the same image."""
    loans = await _remseck_loans(checkouts)
    with_cover = [loan for loan in loans if loan.cover_url]
    without = [loan for loan in loans if not loan.cover_url]
    assert with_cover and without
    assert all(loan.cover_url.startswith("https://") for loan in with_cover)
    assert not any(RemseckBackend.NO_COVER_MARKER in loan.cover_url for loan in with_cover)


async def test_every_row_links_its_catalogue_record(checkouts: str) -> None:
    loans = await _remseck_loans(checkouts)
    assert all(loan.detail_url.startswith(f"{REMSECK_URL}/cgi-bin/koha/opac-detail.pl") for loan in loans)


async def test_item_id_falls_back_to_the_biblionumber_without_a_renew_control(checkouts: str) -> None:
    """Koha renders the renew checkbox only for a loan it would accept a
    renewal for. On the recorded page nothing was renewable, so no row has
    one, and item_id is a biblionumber throughout -- which renew_loan() cannot
    use. Recorded so that the day the fallback starts mattering is a test
    failure rather than a silent renewal that never happens."""
    assert 'name="item"' not in checkouts
    loans = await _remseck_loans(checkouts)
    assert all(loan.item_id.isdigit() for loan in loans)
    assert all(f"biblionumber={loan.item_id}" in loan.detail_url for loan in loans)


async def test_this_koha_renders_no_checkout_date_column(checkouts: str) -> None:
    """The lending date is simply not on the page, so checkout_date is None
    for every loan. A consumer deriving a lending history has to reconstruct
    it from successive polls rather than trust this field."""
    assert 'class="checkout_date"' not in checkouts
    loans = await _remseck_loans(checkouts)
    assert all(loan.checkout_date is None for loan in loans)


async def test_due_dates_come_from_the_sortable_data_order_attribute(checkouts: str) -> None:
    loans = await _remseck_loans(checkouts)
    assert 'class="date_due" data-order=' in checkouts
    assert all(isinstance(loan.due_date, date) for loan in loans)


# --- Remseck: detail pages ---------------------------------------------


async def test_a_detail_page_yields_its_isbn() -> None:
    loan = (await _remseck_loans(_recorded("remseck_checkouts.html")))[0]
    with respx.mock:
        respx.get(loan.detail_url).mock(return_value=httpx.Response(200, html=_recorded("remseck_detail.html")))
        backend = RemseckBackend()
        try:
            enriched = await backend.fetch_details(loan)
        finally:
            await backend.close()
    assert enriched.isbn == "9783473460625"


async def test_a_product_ean_is_not_reported_as_an_isbn() -> None:
    """The recorded tiptoi puzzle carries EAN 4005556001385 and no ISBN. Only
    the Bookland prefixes 978 and 979 are ISBN-13s; a toy's GTIN in that field
    would otherwise be looked up as a book and match something unrelated."""
    ean_page = _recorded("remseck_detail_ean.html")
    assert 'property="ean"' in ean_page
    assert 'property="isbn"' not in ean_page

    loan = (await _remseck_loans(_recorded("remseck_checkouts.html")))[0]
    with respx.mock:
        respx.get(loan.detail_url).mock(return_value=httpx.Response(200, html=ean_page))
        backend = RemseckBackend()
        try:
            enriched = await backend.fetch_details(loan)
        finally:
            await backend.close()
    assert enriched.isbn is None


# --- Stuttgart: what the live aDIS actually sends -----------------------


async def test_the_loan_listing_is_a_table_not_a_section(ausleihen: str) -> None:
    """aDIS renders no <section> at all. Requiring section#results as proof
    of a logged-in page turned every poll into a ParseError."""
    assert "<section" not in ausleihen
    assert 'class="rTable_table"' in ausleihen
    assert 'class="rTable_div"' in ausleihen

    assert await _stuttgart_loans(ausleihen)


async def test_the_last_column_is_headed_hinweis(ausleihen: str) -> None:
    """It was assumed to be "Verlängerung"; the live one says "Hinweis"."""
    assert "Hinweis" in ausleihen


async def test_a_page_without_the_listing_raises_rather_than_reporting_no_loans() -> None:
    """The search mask is what aDIS sends back once the session has gone."""
    home = _recorded("stuttgart_home.html")
    assert 'class="rTable_table"' not in home

    backend = StuttgartBackend()
    backend._ausleihen_url = f"{STUTTGART_URL}/aDISWeb/app"
    with respx.mock:
        respx.get(backend._ausleihen_url).mock(return_value=httpx.Response(200, html=home))
        try:
            with pytest.raises(ParseError):
                await backend.get_loans()
        finally:
            await backend.close()


async def test_a_book_row_yields_a_barcode_and_a_call_number(ausleihen: str) -> None:
    """The title cell is <br>-separated: "Titel / Autor", Signatur,
    Exemplarnummer. The trailing all-digit run is the barcode; the segment
    before it is the call number, which used to be read as the author."""
    loans = await _stuttgart_loans(ausleihen)
    books = [loan for loan in loans if loan.media_type is None]
    assert books, "expected book rows in the recording"
    for loan in books:
        assert loan.barcode and loan.barcode.isdigit() and len(loan.barcode) >= 6
        assert loan.call_number
        assert loan.item_id == loan.barcode
        assert loan.author


async def test_a_short_numeric_shelf_mark_is_not_taken_for_a_barcode(ausleihen: str) -> None:
    """Stuttgart shelves its picture books under the call number "1". Only a
    run of six digits or more is an exemplar number -- classifying the cell's
    trailing segments by "is it a number" alone would swallow the shelf mark
    and leave the row with no identifier."""
    loans = await _stuttgart_loans(ausleihen)
    numeric = [loan for loan in loans if loan.call_number and loan.call_number.isdigit()]
    assert numeric, "the recording no longer exercises an all-digit call number"
    for loan in numeric:
        assert len(loan.call_number) < 6
        assert loan.barcode and loan.barcode != loan.call_number


async def test_a_media_row_keeps_its_type_out_of_the_title(ausleihen: str) -> None:
    """Non-book rows lead with "[Typ]" in its own segment. Taking it as part
    of the title, or the shelf mark as an identifier, is what reported a board
    game's item_id as "S-SPIEL CAT" and its author as its publisher."""
    loans = await _stuttgart_loans(ausleihen)
    typed = [loan for loan in loans if loan.media_type]
    assert typed, "expected rows carrying a media type"
    assert {"CD", "Konventionelles Spiel"} <= {loan.media_type for loan in typed}
    for loan in typed:
        assert not loan.title.startswith("[")
        assert loan.call_number != loan.item_id or loan.barcode is None


async def test_non_filing_indicators_are_stripped_from_titles(ausleihen: str) -> None:
    """The catalogue marks non-sort characters with "¬" ("¬Die¬ Reise").
    They belong in a sort key, not in a displayed title."""
    assert "¬" in ausleihen, "the recording no longer exercises non-filing marks"
    loans = await _stuttgart_loans(ausleihen)
    assert not any("¬" in loan.title for loan in loans)


async def test_renewal_state_is_read_from_the_hinweis_column(ausleihen: str) -> None:
    loans = await _stuttgart_loans(ausleihen)
    assert any(loan.can_be_renewed for loan in loans)
    assert any(loan.times_renewed for loan in loans)
    assert all(loan.max_renewals == StuttgartBackend.MAX_RENEWALS for loan in loans)


async def test_the_row_checkboxes_are_named_the_way_renewal_posts_them(ausleihen: str) -> None:
    """renew_loan() sends back the checkbox it finds in the row's first cell.
    aDIS names them cellCheck, cellCheck$1, cellCheck$2 ..., and a fixture
    that invents another name would let a rename through unnoticed."""
    assert 'name="cellCheck"' in ausleihen
    assert 'name="cellCheck$1"' in ausleihen


async def test_the_account_overview_links_the_loan_listing() -> None:
    """login() finds its way to the loans through div#konto-services; without
    that link there is nothing to fetch."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_recorded("stuttgart_account.html"), features="html.parser")
    links = [link for link in soup.select("div#konto-services li a") if "Ausleihen" in link.text]
    assert links, "no Ausleihen link on the recorded account page"
    assert links[0].attrs["href"].startswith("/aDISWeb/app")


async def test_the_start_page_carries_the_form_login_begins_with() -> None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_recorded("stuttgart_home.html"), features="html.parser")
    form = soup.find("form")
    assert form is not None
    assert form.attrs["action"].startswith("/aDISWeb/app")
    names = {inp.get("name") for inp in form.find_all("input")}
    assert {"service", "sp", "Form0", "requestCount", "scriptEnabled"} <= names


async def test_the_credentials_form_takes_the_fields_login_fills_in() -> None:
    """Step 2 of the aDIS flow. The field names are positional nonsense
    ($Textfield, $Textfield$0) and only a recording can vouch for them."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_recorded("stuttgart_login_form.html"), features="html.parser")
    form = soup.find("form")
    assert form is not None
    assert form.attrs["action"].startswith("/aDISWeb/app")


# --- the recordings themselves ------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "remseck_checkouts.html",
        "remseck_account.html",
        "remseck_logged_out.html",
        "remseck_detail.html",
        "remseck_detail_ean.html",
        "stuttgart_home.html",
        "stuttgart_login_form.html",
        "stuttgart_account.html",
        "stuttgart_ausleihen.html",
    ],
)
def test_no_recording_carries_a_live_session_or_a_patron(name: str) -> None:
    """The recordings are committed, so the anonymiser has to have run. A
    fixture that still holds a session id is a credential in the repository."""
    import re

    html = _recorded(name)
    for session in re.findall(r"jsessionid=([0-9A-Fa-f]{16,})", html):
        assert session == "0123456789ABCDEF0123456789ABCDEF"
    assert "11001558" not in html, "the real Koha borrowernumber is still in this fixture"
    for match in re.findall(r"Abholcode:\s*(\S+)", html):
        assert match == "Mu1234"
