"""Tests for the Stuttgart aDIS/BMS library backend."""

from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from custom_components.stadtbibliothek.backends.base import (
    AuthenticationError,
    LoanItem,
    ParseError,
    RenewalError,
)
from custom_components.stadtbibliothek.backends.stuttgart import StuttgartBackend

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = FIXTURES / "recorded"
BASE_URL = "https://stadtbibliothek-stuttgart.de"
#: Newer aDIS puts the session in the URL path instead of a jsessionid. The
#: constructed fixtures all carry this fixed stand-in for it.
SESSION_URL = f"{BASE_URL}/aDISWeb/_0123456789abcdef0123456789abcdef/app"


def _fields(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode(), keep_blank_values=True).items()}


def _page(name: str, *, recorded: bool = False) -> httpx.Response:
    path = (RECORDED if recorded else FIXTURES) / name
    return httpx.Response(200, text=path.read_text(encoding="utf-8"))


def _mock_session(
    *,
    base_url: str = BASE_URL,
    logged_in: str = "stuttgart_logged_in.html",
    listing: str = "stuttgart_ausleihen.html",
    renewal: str | None = None,
    search: dict[str, str] | None = None,
    unknown_search: str = "stuttgart_home.html",
) -> None:
    """Serve a whole session off one route per URL, dispatching each POST on
    the fields it carries -- the same signals the real server routes on.
    respx consults routes in registration order, so a second POST route for
    the same URL is never reached once the first one answers.

    The login POSTs, the JS-nav POST (selected=ZTEXT *SZA), the renewal POST
    (the checked row checkbox) and the cover searches all go to the same
    session URL on modern aDIS.
    """
    respx.get(f"{base_url}?service=direct/0/Home/$DirectLink&sp=SOPAC").mock(return_value=_page("stuttgart_home.html"))

    def route(request: httpx.Request) -> httpx.Response:
        fields = _fields(request)
        query = fields.get("$Autosuggest", "")
        if query:
            if search is None:
                return httpx.Response(404)
            return _page(search[query], recorded=True) if query in search else _page(unknown_search, recorded=True)
        if fields.get("selected", "").strip().endswith("*SZA"):
            return _page(listing)
        if "$RTable_checkbox[]" in fields:
            return _page(renewal or listing)
        if "$Textfield$0" in fields:
            return _page(logged_in)
        if "SUO1_AUTHFU_1_hidden" in fields:
            return _page("stuttgart_login_form.html")
        return httpx.Response(404)

    respx.post(f"{base_url}/aDISWeb/_0123456789abcdef0123456789abcdef/app").mock(side_effect=route)


@respx.mock
async def test_login_step1_extracts_the_session_url() -> None:
    """The session rides in the start-page form's action path."""
    _mock_session()

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
    finally:
        await backend.close()

    assert backend._login_url == SESSION_URL


@respx.mock
async def test_login_step2_sends_credentials_and_one_button() -> None:
    """Regression: aDIS renamed the submit fields (textButton* -> $Button*)
    while the code still popped the old names, so every button value of the
    form -- Anmelden, Abbrechen, Passwort vergessen? -- went out together and
    the server read the click as something else than "Anmelden". Only the
    clicked button may be posted."""
    _mock_session()

    backend = StuttgartBackend()
    try:
        await backend.login("5980610", "secret123")
    finally:
        await backend.close()

    posts = [_fields(call.request) for call in respx.calls if call.request.method == "POST"]
    assert len(posts) == 2

    step1 = posts[0]
    assert step1["selected"] == "ZTEXT       *SBK"
    # The Anmelden control is a type=button; a browser never posts it.
    assert "$Button" not in step1

    creds = posts[1]
    assert creds["$Textfield"] == "5980610"
    assert creds["$Textfield$0"] == "secret123"
    assert creds["$Button"] == "Anmelden"
    # The Abbrechen / Passwort vergessen? / Bibliothekskunde buttons of the
    # same form must not ride along.
    assert "$Button$0" not in creds
    assert "$Button$1" not in creds
    assert "$Button$2" not in creds


@respx.mock
async def test_login_success() -> None:
    """Newer aDIS renders the service links href="#" and posts the account
    page's form from a page script; login() has to resolve that route."""
    _mock_session()

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
    finally:
        await backend.close()

    assert backend._ausleihen_url is None
    assert backend._ausleihen_nav is not None
    url, fields = backend._ausleihen_nav
    assert url == SESSION_URL
    assert fields["selected"] == "ZTEXT       *SZA"


@respx.mock
async def test_an_account_page_with_a_plain_href_is_still_followed() -> None:
    """Older aDIS renders the Ausleihen link with a real href; login() must
    take that route instead of parsing JS that is not there."""
    _mock_session(logged_in="stuttgart_logged_in_href.html")
    respx.get(url__regex=r".*sp=SBK00000001.*").mock(return_value=_page("stuttgart_ausleihen.html"))

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        assert backend._ausleihen_nav is None
        assert backend._ausleihen_url is not None
        assert backend._ausleihen_url.startswith(f"{BASE_URL}/aDISWeb/app;jsessionid=")
        loans = await backend.get_loans()
    finally:
        await backend.close()

    assert len(loans) == 5


@respx.mock
async def test_login_failure() -> None:
    """Login raises AuthenticationError when konto-services is missing."""
    respx.get(f"{BASE_URL}?service=direct/0/Home/$DirectLink&sp=SOPAC").mock(
        return_value=_page("stuttgart_home.html"),
    )
    # Return login form, then a failure page (no konto-services)
    respx.post(SESSION_URL).mock(
        side_effect=[
            _page("stuttgart_login_form.html"),
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
    _mock_session()

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        loans = await backend.get_loans()
    finally:
        await backend.close()

    assert len(loans) == 5

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
    _mock_session()

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
    _mock_session()

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
    _mock_session()

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        loans = await backend.get_loans()
    finally:
        await backend.close()

    # Row 2 fixture has ¬Der¬ kleine Prinz — ¬ must be stripped
    assert "¬" not in loans[1].title
    assert loans[1].title == "Der kleine Prinz"


@respx.mock
async def test_get_loans_off_the_account_page_raises_parse_error() -> None:
    """Landing back on the search page means the session died, not that
    nothing is borrowed. Reporting zero loans there would look like every
    item had been returned."""
    _mock_session(listing="stuttgart_home.html")
    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        with pytest.raises(ParseError):
            await backend.get_loans()
    finally:
        await backend.close()


@respx.mock
async def test_get_loans_with_an_empty_result_section_returns_empty() -> None:
    """A results section without a loan table means the account is empty."""
    _mock_session(listing="stuttgart_no_loans.html")
    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        assert await backend.get_loans() == []
    finally:
        await backend.close()


@respx.mock
async def test_renew_loan_success() -> None:
    """renew_loan posts the row checkbox and returns True on success."""
    _mock_session(renewal="stuttgart_renewed.html")

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        result = await backend.renew_loan("12345678")
    finally:
        await backend.close()

    assert result is True


@respx.mock
async def test_renew_loan_sends_the_row_checkbox_and_one_button() -> None:
    """The renewal posts the checked row's value under the shared
    $RTable_checkbox[] name plus exactly the clicked button -- a second
    button value and aDIS loses the action."""
    _mock_session(renewal="stuttgart_renewed.html")

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        await backend.renew_loan("12345678")
    finally:
        await backend.close()

    posts = [_fields(call.request) for call in respx.calls if call.request.method == "POST"]
    renewal = next(fields for fields in posts if "$RTable_checkbox[]" in fields)
    assert renewal["$RTable_checkbox[]"] == "CheckCell_1"
    assert renewal["$Button$1"] == "Markierte Medien verlängern"
    assert "$Button" not in renewal  # Druckversion
    assert "$Button$0" not in renewal  # Alle verlängern
    assert "$Button$2" not in renewal  # Zur Übersicht


@respx.mock
async def test_renew_loan_item_not_found() -> None:
    """renew_loan raises RenewalError when item_id is not in the loan table."""
    _mock_session()

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
    _mock_session(renewal="stuttgart_renew_failed.html")

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
    _mock_session(renewal="stuttgart_renewed.html")

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        # All loans have due dates within 42 days, but only 4 are renewable
        count = await backend.renew_all(days_remaining_threshold=42)
    finally:
        await backend.close()

    assert count == 4  # the Python row is "nicht verlängerbar"


@respx.mock
async def test_base_url_override_redirects_all_requests() -> None:
    """A custom base_url points every request at the given host."""
    fake = "http://fake.local"
    _mock_session(base_url=fake)
    backend = StuttgartBackend(base_url=fake)
    try:
        assert backend.base_url == fake
        await backend.login("testuser", "testpass")
    finally:
        await backend.close()

    assert backend._login_url is not None
    assert backend._login_url.startswith(fake)
    assert backend._ausleihen_nav is not None
    assert backend._ausleihen_nav[0].startswith(fake)


async def test_base_url_defaults_to_the_class_constant() -> None:
    backend = StuttgartBackend()
    try:
        assert backend.base_url == StuttgartBackend.BASE_URL
    finally:
        await backend.close()


async def test_close_does_not_close_an_injected_client() -> None:
    """A caller that supplies its own client keeps ownership of it.

    bib-tracker shares one rate-limited client across all accounts; closing it
    from the first backend that finishes would break every other account.
    """
    client = httpx.AsyncClient()
    try:
        backend = StuttgartBackend(client)
        await backend.close()
        assert client.is_closed is False
    finally:
        await client.aclose()


async def test_close_closes_a_client_it_created() -> None:
    backend = StuttgartBackend()
    await backend.close()
    assert backend._client.is_closed is True


async def _fetch_loans() -> list:
    _mock_session()
    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        return await backend.get_loans()
    finally:
        await backend.close()


@respx.mock
async def test_book_row_splits_call_number_from_barcode() -> None:
    """A book row is "Titel / Autor", call number, barcode."""
    loans = await _fetch_loans()
    book = next(loan for loan in loans if loan.title == "Der kleine Prinz")
    assert book.author == "Saint-Exupéry, Antoine de"
    assert book.call_number == "K-SL SAI"
    assert book.barcode == "12345678"
    assert book.item_id == "12345678"
    assert book.publisher is None


@respx.mock
async def test_media_row_author_is_not_publisher() -> None:
    """Regression: a media row is "[Typ]", Titel, Verlag, Signatur -- the
    third segment is the publisher, and reporting it as the author made
    every CD look like it was written by its label."""
    loans = await _fetch_loans()
    cd = next(loan for loan in loans if loan.media_type == "CD")
    assert cd.title == "Die drei Fragezeichen - Folge 220"
    assert cd.author is None
    assert cd.publisher == "Europa (Musik)"
    assert cd.call_number == "M-CD-K DRE"
    assert cd.barcode is None


@respx.mock
async def test_media_row_call_number_is_not_mistaken_for_a_barcode() -> None:
    """Media rows carry no barcode, so item_id falls back to the call number
    -- which is what renew_loan() matches on."""
    loans = await _fetch_loans()
    game = next(loan for loan in loans if loan.media_type == "Konventionelles Spiel")
    assert game.title == "Catan - Das Spiel"
    assert game.publisher == "Kosmos"
    assert game.call_number == "S-SPIEL CAT"
    assert game.barcode is None
    assert game.item_id == "S-SPIEL CAT"


@respx.mock
async def test_media_row_without_a_publisher() -> None:
    """Three segments after the media type prefix: title and call number only."""
    loans = await _fetch_loans()
    dvd = next(loan for loan in loans if loan.media_type == "DVD")
    assert dvd.title == "Das Leben der Anderen"
    assert dvd.author is None
    assert dvd.publisher is None
    assert dvd.call_number == "M-DVD-S LEB"
    assert dvd.item_id == "M-DVD-S LEB"


# --- catalogue-search covers (recorded pages) ----------------------------

#: The search response the live OPAC gave for each fixture title -- recorded
#: verbatim and anonymised like the other recordings. The keys are the titles
#: as the loan listing spells them, because the router above dispatches on
#: exactly what the backend put into the search box.
_SEARCH_PAGES = {
    "Die drei Fragezeichen - Folge 220": "stuttgart_search_00.html",
    "Der kleine Prinz": "stuttgart_search_01.html",
    "Catan - Das Spiel": "stuttgart_search_02.html",
    "Python Crashkurs": "stuttgart_search_03.html",
    "Das Leben der Anderen": "stuttgart_search_04.html",
    "Der Koboldmaki und der große Sturm": "stuttgart_search_05.html",
}


@respx.mock
async def test_fetch_details_fills_covers_from_the_catalogue_search() -> None:
    """The loan listing carries no image at all: a cover comes from looking
    the title up in the OPAC's catalogue search, whose result rows keep the
    jacket in a lazily-loaded data-src."""
    _mock_session(search=_SEARCH_PAGES)

    backend = StuttgartBackend()
    try:
        await backend.login("testuser", "testpass")
        loans = await backend.get_loans()
        for loan in loans:
            await backend.fetch_details(loan)
    finally:
        await backend.close()

    by_title = {loan.title: loan for loan in loans}
    token = "access_token=63a82a71-a875-4a48-a32d-560e9bc3ef3e"
    assert by_title["Python Crashkurs"].cover_url == f"https://api.vlb.de/api/v1/cover/9783864909894/s?{token}"
    # Matched the catalogue's spelling, "Catan : das Spiel", not the listing's
    # "Catan - Das Spiel".
    assert by_title["Catan - Das Spiel"].cover_url == f"https://api.vlb.de/api/v1/cover/4002051684655/s?{token}"
    assert by_title["Der kleine Prinz"].cover_url == f"https://api.vlb.de/api/v1/cover/4251192162243/s?{token}"

    # A title the search has no row for keeps no cover: taking the first
    # unrelated hit's jacket would illustrate every miss with the same book.
    assert by_title["Die drei Fragezeichen - Folge 220"].cover_url is None
    assert by_title["Das Leben der Anderen"].cover_url is None


@respx.mock
async def test_fetch_details_reads_a_single_hit_detail_page() -> None:
    """An exact-title search skips the result list: aDIS answers a single hit
    with its Vollanzeige, where the cover sits in the detail block."""
    _mock_session(search=_SEARCH_PAGES)
    loan = LoanItem(title="Der Koboldmaki und der große Sturm", item_id="1", due_date=date(2026, 10, 1))

    backend = StuttgartBackend()
    try:
        assert await backend.fetch_details(loan) is loan
    finally:
        await backend.close()

    token = "access_token=63a82a71-a875-4a48-a32d-560e9bc3ef3e"
    assert loan.cover_url == f"https://api.vlb.de/api/v1/cover/9783741527968/m/?{token}"


@respx.mock
async def test_the_cover_search_asks_the_catalogue_not_the_info_pages() -> None:
    """Without SRCHAW=Katalog the same form searches the website's Infoseiten
    and returns a page with neither rows nor covers."""
    _mock_session(search=_SEARCH_PAGES)
    backend = StuttgartBackend()
    try:
        await backend.fetch_details(LoanItem(title="Python Crashkurs", item_id="1", due_date=date(2026, 10, 1)))
    finally:
        await backend.close()

    searches = [_fields(call.request) for call in respx.calls if call.request.method == "POST"]
    searches = [fields for fields in searches if fields.get("$Autosuggest") == "Python Crashkurs"]
    assert searches, "fetch_details ran no catalogue search"
    for fields in searches:
        assert fields.get("SRCHAW") == "Katalog"


@respx.mock
async def test_a_search_page_about_another_query_yields_no_cover() -> None:
    """aDIS keeps the previous result set in the session and hands it back
    when queried too fast. A detail page echoing a different query is not
    about this loan, and its cover is the wrong image."""
    _mock_session(search=_SEARCH_PAGES, unknown_search="stuttgart_search_05.html")
    loan = LoanItem(title="Ein ganz anderes Buch", item_id="1", due_date=date(2026, 10, 1))

    backend = StuttgartBackend()
    try:
        await backend.fetch_details(loan)
    finally:
        await backend.close()

    assert loan.cover_url is None


@respx.mock
async def test_a_failing_cover_search_leaves_the_loan_untouched() -> None:
    """One title's lookup must never fail the whole refresh: the sensor still
    has a loan listing to show."""
    respx.get(f"{BASE_URL}?service=direct/0/Home/$DirectLink&sp=SOPAC").mock(
        return_value=_page("stuttgart_home.html"),
    )
    respx.post(SESSION_URL).mock(side_effect=httpx.ConnectError("connection refused"))

    loan = LoanItem(title="Irgendwas", item_id="1", due_date=date(2026, 10, 1))
    backend = StuttgartBackend()
    try:
        assert await backend.fetch_details(loan) is loan
    finally:
        await backend.close()

    assert loan.cover_url is None


def test_stuttgart_supports_details() -> None:
    """The lookup costs one request per loan, so the coordinator only pays for
    it when the backend advertises it."""
    assert StuttgartBackend.supports_details is True


def test_norm_title_folds_the_noise_the_two_pages_spell_differently() -> None:
    """Non-filing marks, media brackets, ß versus ss, punctuation: the listing
    and the result page write one title differently, and the join key has to
    make them compare equal."""
    assert StuttgartBackend._norm_title("¬Der¬ Koboldmaki und der große Sturm") == StuttgartBackend._norm_title(
        "Der Koboldmaki und der grosse Sturm"
    )
    assert StuttgartBackend._norm_title("[CD] An Teich und Fluss") == StuttgartBackend._norm_title("An Teich und Fluss")
    assert StuttgartBackend._norm_title("Catan - Das Spiel") == StuttgartBackend._norm_title("Catan : das Spiel")


def test_bare_title_drops_subtitle_and_brackets() -> None:
    assert StuttgartBackend._bare_title("Der Ernst des Lebens : eine Stunde voller Wörterwunder") == (
        "Der Ernst des Lebens"
    )
    assert StuttgartBackend._bare_title("An und aus, wie geht das? [CD]") == "An und aus, wie geht das?"
