"""Stuttgart aDIS/BMS library backend."""

import re
import unicodedata
from datetime import datetime
from html import unescape
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag
from .base import AuthenticationError, FeeItem, LibraryBackend, LibraryType, LoanItem, ParseError, RenewalError

# Fields that indicate media type prefixes in title column
_MEDIA_TYPE_PATTERN = re.compile(r"^\[.+\]$")
# Exemplar barcodes are all-digit and long; call numbers never are.
_BARCODE_PATTERN = re.compile(r"^\d{6,}$")


class StuttgartBackend(LibraryBackend):
    library_type = LibraryType.STUTTGART
    supports_fees = False
    BASE_URL = "https://stadtbibliothek-stuttgart.de"
    START_PATH = "?service=direct/0/Home/$DirectLink&sp=SOPAC"
    MAX_RENEWALS = 8
    #: The loan listing itself. aDIS renders it only on a result page, so its
    #: absence means we were sent somewhere else entirely -- typically back to
    #: the search mask after the session timed out.
    RESULTS_MARKER = "table.rTable_table"

    #: Catalogue covers are not on the loan listing at all: aDIS keeps them on
    #: the search-result page (``div.rList_img img``) and on the single-hit
    #: detail page (``div.show-full-basics img``), and serves the image itself
    #: from the Deutsche Nationalbibliothek's cover API, keyed by the record's
    #: ISBN with the OPAC's own client token baked into the URL.
    COVER_API_MARKER = "api.vlb.de"
    #: aDIS writes this into ``src`` and puts the real cover in ``data-src``,
    #: loaded lazily by its JS. Treat it as "no cover".
    PLACEHOLDER_MARKER = "placeholder"

    supports_details = True

    CLIENT_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:129.0) Gecko/20100101 Firefox/129.0"}

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        super().__init__(client, base_url=base_url)
        self._login_url: str | None = None
        self._ausleihen_url: str | None = None

    async def login(self, username: str, password: str) -> None:
        # Step 1: GET start page, extract form with jsessionid
        resp = await self._client.get(f"{self.base_url}{self.START_PATH}")
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, features="html.parser")

        form = soup.find("form")
        if not isinstance(form, Tag):
            raise AuthenticationError("No form found on start page")

        action_path = form.attrs["action"]
        self._login_url = f"{self.base_url}{action_path}"

        data = self._extract_hidden_inputs(form)
        data["SUO1_AUTHFU_1_hidden"] = ""
        data["select"] = "- Alle -"
        data["selected"] = "ZTEXT       *SBK"
        data["Form0"] = (
            "focus,keyCode,stz,source,selected,requestCount,scriptEnabled,"
            "scrollPos,scrDim,winDim,imgDim,SUO1_AUTHFU_1,$Autosuggest,"
            "select,$FormConditional,textButton,$FormConditional$0,"
            "textButton$0,$FormConditional$1,$FormConditional$2,"
            "$FormConditional$3,$FormConditional$4"
        )
        data.pop("textButton$0", None)

        # Step 2: POST to get login form, then fill credentials
        resp = await self._client.post(
            self._login_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, features="html.parser")

        form = soup.find("form")
        if not isinstance(form, Tag):
            raise AuthenticationError("No login form found")

        data = self._extract_hidden_inputs(form)
        data["$Textfield"] = username
        data["$Textfield$0"] = password
        data["focus"] = "$$GFBO_2"
        data["scriptEnabled"] = "true"
        data.pop("textButton$0", None)
        data.pop("textButton$1", None)
        data.pop("textButton$2", None)
        data["Form0"] = (
            "focus,keyCode,stz,source,select,selected,requestCount,"
            "scriptEnabled,scrollPos,scrDim,winDim,imgDim,$Textfield,"
            "$Textfield$0,$FormConditional,textButton,$FormConditional$0,"
            "textButton$0,$FormConditional$1,textButton$1,"
            "$FormConditional$2,textButton$2"
        )

        # Step 3: POST credentials, find Ausleihen link
        resp = await self._client.post(
            self._login_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, features="html.parser")

        for link in soup.select("div#konto-services li a"):
            if "Ausleihen" in link.text:
                self._ausleihen_url = f"{self.base_url}{link.attrs['href']}"
                return

        raise AuthenticationError("Login failed: konto-services with Ausleihen link not found")

    async def get_loans(self) -> list[LoanItem]:
        if not self._ausleihen_url:
            raise RuntimeError("Must call login() before get_loans()")

        resp = await self._client.get(self._ausleihen_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, features="html.parser")

        listing = soup.select_one(self.RESULTS_MARKER)
        if listing is None:
            # Erring towards an error rather than an empty list is deliberate:
            # a wrong "nothing borrowed" silently erases a borrowing history,
            # whereas a wrong error is merely noisy and self-correcting.
            raise ParseError("Not the Ausleihen page; the session may have expired")

        table = listing.select_one("tbody")
        if table is None:
            # The listing is there but has no body: nothing is borrowed.
            return []

        loans: list[LoanItem] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            due_date = datetime.strptime(cells[1].text.strip(), "%d.%m.%Y").date()
            branch = cells[2].text.strip()

            title_parts = self._split_br(cells[3])
            media_type, title, author, publisher, call_number, barcode = self._parse_title_cell(title_parts)
            # Books carry a barcode, media only a call number; renew_loan()
            # matches whichever one on the title cell text.
            item_id = barcode or call_number or ""

            # Parse extension column
            ext_parts = self._split_br(cells[4])
            ext_text = ext_parts[0] if ext_parts else ""
            can_be_renewed = ext_text.startswith("verlängerbar") or ext_text.startswith("Heute verlängert")

            times_renewed = 0
            if len(ext_parts) > 1:
                m = re.match(r"(\d+)\s+Verlängerungen?", ext_parts[1].strip())
                if m:
                    times_renewed = int(m.group(1))

            loans.append(
                LoanItem(
                    title=title,
                    item_id=item_id,
                    due_date=due_date,
                    library_branch=branch,
                    media_type=media_type,
                    author=author,
                    can_be_renewed=can_be_renewed,
                    times_renewed=times_renewed,
                    max_renewals=self.MAX_RENEWALS,
                    call_number=call_number,
                    barcode=barcode,
                    publisher=publisher,
                )
            )

        return loans

    async def fetch_details(self, loan: LoanItem) -> LoanItem:
        """Fill in a cover by looking the title up in the catalogue.

        The loan listing is plain text. The only route to a cover is the
        search the OPAC itself offers: query the loan's title and read the
        cover off the result row, or off the detail page when the search
        resolves to a single record. It costs a request per loan, which is why
        it lives here and not in get_loans().

        A lookup that fails -- no hit, no image, a network error -- leaves
        cover_url None. A title the catalogue has no image for must not fail
        the whole refresh.
        """
        try:
            cover = await self._search_cover(loan.title)
            if cover is None:
                # Retry once on a looser key: the OPAC token can expire
                # between two calls, and a decorated title (subtitle,
                # bracketed junk) deserves one second chance.
                cover = await self._search_cover(self._bare_title(loan.title))
        except httpx.HTTPError:
            return loan
        if cover:
            loan.cover_url = cover
        return loan

    async def _search_cover(self, query: str) -> str | None:
        """Run one catalogue query and return the cover of its best match."""
        # A fresh search mask per query. aDIS keeps the previous result set in
        # the session: reusing one mask for several queries hands back the
        # earlier query's results, unchanged, as often as not.
        mask = await self._client.get(f"{self.base_url}{self.START_PATH}")
        mask.raise_for_status()
        form = self._find_search_form(BeautifulSoup(mask.text, features="html.parser"))
        if form is None:
            return None

        data = self._extract_hidden_inputs(form)
        data["$Autosuggest"] = query
        # A radio group with nothing checked posts nothing; "Katalog" is the
        # difference between cover-carrying hits and "Treffer in Infoseiten".
        data["SRCHAW"] = "Katalog"
        data["textButton"] = "Suchen"
        data.pop("textButton$0", None)

        action = form.attrs["action"]
        resp = await self._client.post(
            urljoin(f"{self.base_url}/", str(action)),
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, features="html.parser")

        cover = self._best_match_cover(soup, query)
        if cover is not None:
            return cover
        # The search jumps straight to Vollanzeige when it finds exactly one
        # record -- how most loans come back, given the barcode printed on the
        # listing. Its cover sits in the "Weitere Infos" block.
        return self._detail_page_cover(soup, query)

    def _find_search_form(self, soup: BeautifulSoup) -> Tag | None:
        """The first form carrying the free-text search field."""
        for form in soup.find_all("form"):
            if form.find("input", {"name": "$Autosuggest"}) is not None:
                return form
        return None

    def _best_match_cover(self, soup: BeautifulSoup, query: str) -> str | None:
        """The cover of the result row whose title is the query's title."""
        want = self._norm_title(query)
        if not want:
            return None
        for item in soup.select("li.rList_li"):
            link = item.select_one("div.rList_titel a")
            if link is None:
                continue
            have = self._norm_title(link.get_text())
            if have and (have == want or have.startswith(want) or want.startswith(have)):
                return self._cover_from_img(item.select_one("div.rList_img img"))
        return None

    def _detail_page_cover(self, soup: BeautifulSoup, query: str) -> str | None:
        """The cover on a single-hit Vollanzeige (no result rows are rendered).

        On a result list the row scan above has already had its say: grabbing
        a cover from an unmatched page here would illustrate this loan with
        some other hit's jacket.
        """
        if soup.select_one("li.rList_li") is not None:
            return None
        # Detail pages echo the query ("Gesucht wurde mit: ..."). If the page
        # the session served is not about this query, it is somebody else's
        # record and its cover is the wrong image.
        want = self._norm_title(query)
        info = soup.select_one("p.info")
        if want and info is not None and want not in self._norm_title(info.get_text()):
            return None
        for img in soup.find_all("img"):
            cover = self._cover_from_img(img)
            if cover:
                return cover
        return None

    def _cover_from_img(self, img: Tag | None) -> str | None:
        if img is None:
            return None
        # Lazily-loaded covers sit in data-src; src holds the placeholder.
        raw = img.get("data-src") or img.get("src")
        url = raw if isinstance(raw, str) else ""
        if self.COVER_API_MARKER not in url or self.PLACEHOLDER_MARKER in url:
            return None
        return urljoin(f"{self.base_url}/", url)

    async def get_fees(self) -> list[FeeItem]:
        # aDIS exposes fees behind a separate flow that is not implemented;
        # supports_fees advertises that so callers do not read this as "no fees".
        return []

    async def renew_loan(self, item_id: str) -> bool:
        if not self._ausleihen_url:
            raise RuntimeError("Must call login() before renew_loan()")

        resp = await self._client.get(self._ausleihen_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, features="html.parser")

        form = soup.find("form")
        if not isinstance(form, Tag):
            raise RuntimeError("No form found on Ausleihen page")

        action_url = f"{self.base_url}{form.attrs['action']}"
        data = self._extract_hidden_inputs(form)

        # Remove all submit button values — only the clicked button should be sent
        data = {k: v for k, v in data.items() if not k.startswith("textButton")}

        # Find the checkbox whose row contains the target item_id
        table = soup.select_one("table.rTable_table tbody")
        if table is None:
            return False

        checkbox_name = None
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            title_text = cells[3].get_text()
            if item_id in title_text:
                checkbox = cells[0].find("input", {"type": "checkbox"})
                if checkbox:
                    name_attr = checkbox.get("name")
                    if isinstance(name_attr, str):
                        checkbox_name = name_attr
                        # Browsers send "on" for checked checkboxes without a value attribute
                        data[checkbox_name] = str(checkbox.get("value")) if checkbox.get("value") else "on"
                break

        if checkbox_name is None:
            raise RenewalError(f"Item {item_id} not found in loan table")

        # "Markierte Medien verlängern" = renew selected items
        data["textButton$1"] = "Markierte Medien verlängern"

        resp = await self._client.post(
            action_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()

        # Check for error messages in the response
        result_soup = BeautifulSoup(resp.text, features="html.parser")
        error_div = result_soup.select_one("div.aDISError")
        if error_div:
            raise RenewalError(error_div.get_text(strip=True))

        return True

    async def renew_all(self, days_remaining_threshold: int = 7) -> int:
        loans = await self.get_loans()
        renewed = 0
        for loan in loans:
            if loan.can_be_renewed and loan.days_remaining <= days_remaining_threshold:
                try:
                    if await self.renew_loan(loan.item_id):
                        renewed += 1
                except RenewalError:
                    pass
        return renewed

    @staticmethod
    def _parse_title_cell(
        parts: list[str],
    ) -> tuple[str | None, str, str | None, str | None, str | None, str | None]:
        """Classify the <br>-separated segments of the title cell.

        aDIS uses two shapes:

            book   "Titel / Autor" | Signatur | Exemplarnummer
            media  "[Typ]" | Titel | Verlag | Signatur

        Media rows carry no barcode, so the trailing segment is only an
        exemplar number when it looks like one. Deciding by position instead
        made every CD's call number its item_id and its label its author.
        """
        parts = list(parts)

        media_type = None
        if parts and _MEDIA_TYPE_PATTERN.match(parts[0]):
            media_type = parts.pop(0).strip("[]")

        barcode = None
        if len(parts) > 1 and _BARCODE_PATTERN.match(parts[-1]):
            barcode = parts.pop()

        call_number = parts.pop() if len(parts) > 1 else None

        raw_title = parts[0].replace("¬", "") if parts else ""
        title, author = raw_title, None
        if " / " in raw_title:
            title, author = raw_title.split(" / ", 1)

        # Whatever is left between title and call number is the publisher.
        publisher = parts[1] if len(parts) > 1 else None

        return media_type, title, author, publisher, call_number, barcode

    @staticmethod
    def _extract_hidden_inputs(form: Tag) -> dict[str, str]:
        data: dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name and isinstance(name, str):
                value = inp.get("value", "")
                data[name] = str(value)
        return data

    @staticmethod
    def _split_br(cell: Tag) -> list[str]:
        """Split cell innerHTML on <br> tags, return stripped text parts."""
        parts: list[str] = []
        for content in cell.decode_contents().split("<br"):
            # strip the closing > or /> from the br tag remnant
            text = re.sub(r"^[^>]*>", "", content) if not content.startswith("<") else content
            text = unescape(re.sub(r"<[^>]+>", "", text)).strip()
            if text:
                parts.append(text)
        return parts

    @staticmethod
    def _bare_title(title: str) -> str:
        """Title without subtitle or media-type bracket, for a second search."""
        title = re.split(r"\s+:\s+", title)[0]
        return re.sub(r"\[.+?\]", "", title).strip()

    #: Word characters only; everything else -- spacing, punctuation, media
    #: brackets -- is noise that differs between the two pages.
    _TITLE_WORD = re.compile(r"[a-z0-9]+")

    @classmethod
    def _norm_title(cls, text: str) -> str:
        """Join-key form of a title, for comparing a loan against a result.

        The listing decorates titles with non-filing marks ("¬Der¬") and
        shelf noise; the result page prefixes them with media brackets
        ("[CD] ...") and has its own spacing, and may spell "ss" where the
        listing has "ß". Compare letters and digits, with ß folded into ss,
        and nothing else.
        """
        folded = unicodedata.normalize("NFKC", text.replace("¬", " ")).lower()
        folded = re.sub(r"\[[^\]]*\]", " ", folded)
        return " ".join(cls._TITLE_WORD.findall(folded.replace("ß", "ss")))
