from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from .base import (
    AuthenticationError,
    FeeItem,
    LibraryBackend,
    LibraryType,
    LoanItem,
    ParseError,
)


# ISBN-13s are the only EANs in the Bookland prefixes.
_ISBN13_PATTERN = re.compile(r"^97[89]\d{10}$")


class RemseckBackend(LibraryBackend):
    """Backend for Mediathek Remseck (Koha/LMSCloud OPAC)."""

    library_type = LibraryType.REMSECK
    BASE_URL = "https://mt-remseck.lmscloud.net"
    #: Present on every logged-in OPAC page; its absence means the session
    #: is gone or the page layout changed.
    ACCOUNT_MARKER = "#useraccount"
    #: Koha renders this graphic when it has no jacket for a record.
    NO_COVER_MARKER = "no-image"
    supports_details = True

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
        )
        self._owns_client = client is None
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self._borrowernumber: str | None = None

    async def login(self, username: str, password: str) -> None:
        resp = await self._client.post(
            f"{self.base_url}/cgi-bin/koha/opac-user.pl",
            data={
                "userid": username,
                "password": password,
                "koha_login_context": "opac",
            },
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        # If the login form is still present, authentication failed
        if soup.find("form", id="auth"):
            raise AuthenticationError("Login failed: invalid credentials")

    async def get_loans(self) -> list[LoanItem]:
        resp = await self._client.get(
            f"{self.base_url}/cgi-bin/koha/opac-user.pl",
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        # Extract borrowernumber from the renewal form for later use
        renew_form = soup.find("form", id="renewselected")
        if renew_form and isinstance(renew_form, Tag):
            bn_input = renew_form.find("input", attrs={"name": "borrowernumber"})
            if bn_input and isinstance(bn_input, Tag):
                val = bn_input.get("value")
                if isinstance(val, str):
                    self._borrowernumber = val

        if soup.select_one(self.ACCOUNT_MARKER) is None:
            raise ParseError("Not a logged-in account page; the session may have expired")

        table = soup.find("table", id="checkoutst")
        if not table or not isinstance(table, Tag):
            # Account page without a checkout table: nothing is borrowed.
            return []

        return [self._parse_loan_row(row) for row in table.select("tbody tr")]

    async def get_fees(self) -> list[FeeItem]:
        resp = await self._client.get(
            f"{self.base_url}/cgi-bin/koha/opac-account.pl",
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        if soup.select_one(self.ACCOUNT_MARKER) is None:
            raise ParseError("Not a logged-in account page; the session may have expired")

        table = soup.find("table", id="finestable")
        if not table or not isinstance(table, Tag):
            # Account page without a fees table: no outstanding fees.
            return []

        fees: list[FeeItem] = []
        for row in table.select("tbody tr"):
            fees.append(self._parse_fee_row(row))
        return fees

    async def renew_loan(self, item_id: str) -> bool:
        if not self._borrowernumber:
            # Need to fetch loans first to obtain borrowernumber
            await self.get_loans()
        if not self._borrowernumber:
            return False

        resp = await self._client.post(
            f"{self.base_url}/cgi-bin/koha/opac-renew.pl",
            data={
                "item": item_id,
                "borrowernumber": self._borrowernumber,
                "from": "opac_user",
            },
        )
        if resp.status_code != 200:
            return False
        # The server redirects to opac-user.pl with renewed=<item> on success
        url = str(resp.url)
        return f"renewed={item_id}" in url

    async def renew_all(self, days_remaining_threshold: int = 14) -> int:
        loans = await self.get_loans()
        renewed = 0
        for loan in loans:
            if loan.can_be_renewed and loan.days_remaining <= days_remaining_threshold:
                if await self.renew_loan(loan.item_id):
                    renewed += 1
        return renewed

    async def fetch_details(self, loan: LoanItem) -> LoanItem:
        """Fill in ISBN and a better cover from the catalogue detail page."""
        if not loan.detail_url:
            return loan

        resp = await self._client.get(loan.detail_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        isbn = _first_text(soup, 'span[property="isbn"]')
        if isbn is None:
            # Non-book media carry a product GTIN here, not a book number.
            ean = _first_text(soup, 'span[property="ean"]')
            if ean and _ISBN13_PATTERN.match(ean):
                isbn = ean
        if isbn:
            loan.isbn = isbn

        cover = self._absolute(_attr(soup.select_one("div.bookcover div.cover-image img"), "src"))
        if cover and self.NO_COVER_MARKER not in cover:
            loan.cover_url = cover

        return loan

    def _absolute(self, url: str | None) -> str | None:
        """Resolve an OPAC-relative URL against the configured base URL."""
        if not url:
            return None
        return urljoin(f"{self.base_url}/", url)

    async def close(self) -> None:
        # Only close a client this backend created; an injected one
        # belongs to the caller and may be shared with other backends.
        if self._owns_client:
            await self._client.aclose()

    def _parse_loan_row(self, row: Tag) -> LoanItem:
        title_tag = row.select_one("td.title a.title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        author = _cell_text(row, "td.author")
        item_id = _extract_itemnumber(row) or _extract_biblionumber(title_tag)
        media_type = _cell_text(row, "td.itype")
        call_number = _cell_text(row, "td.call_no")
        library_branch = _cell_text(row, "td.branch")

        due_date = _parse_data_order_date(row, "td.date_due")
        checkout_date = _parse_data_order_date(row, "td.checkout_date")

        detail_url = self._absolute(_attr(title_tag, "href"))
        cover_url = self._absolute(_attr(row.select_one("td.jacketcell img"), "src"))
        if cover_url and self.NO_COVER_MARKER in cover_url:
            cover_url = None

        times_renewed, max_renewals = _parse_renewals(row)
        no_renewal_before = _parse_no_renewal_before(row)
        renewals_disabled = _is_renewals_disabled(row)

        can_be_renewed = (
            times_renewed < max_renewals
            and not renewals_disabled
            and (no_renewal_before is None or date.today() >= no_renewal_before)
        )

        return LoanItem(
            title=title,
            item_id=item_id,
            due_date=due_date or date.today(),
            checkout_date=checkout_date,
            author=author or None,
            media_type=media_type or None,
            library_branch=library_branch or None,
            call_number=call_number or None,
            can_be_renewed=can_be_renewed,
            times_renewed=times_renewed,
            max_renewals=max_renewals,
            cover_url=cover_url,
            detail_url=detail_url,
        )

    @staticmethod
    def _parse_fee_row(row: Tag) -> FeeItem:
        cells = row.find_all("td")
        if len(cells) < 6:
            return FeeItem(description="", amount=0.0)

        created_str = cells[0].get_text(strip=True)
        description = cells[3].get_text(strip=True)
        amount_str = cells[5].get_text(strip=True)

        return FeeItem(
            description=description,
            amount=_parse_german_decimal(amount_str),
            date=_parse_german_date(created_str),
        )


def _extract_itemnumber(row: Tag) -> str:
    """Extract itemnumber from the renew checkbox or link in the renew cell.

    The Koha OPAC uses itemnumber (not barcode/biblionumber) for renewal.
    It appears as: <input type="checkbox" name="item" value="678885"/>
    or: <a href="...opac-renew.pl?...item=678885...">
    """
    checkbox = row.select_one('td.renew input[name="item"]')
    if checkbox and isinstance(checkbox, Tag):
        val = checkbox.get("value")
        if isinstance(val, str):
            return val
    # Fallback: extract from renewal link
    link = row.select_one("td.renew a[href*='opac-renew.pl']")
    if link and isinstance(link, Tag):
        href = link.get("href", "")
        if isinstance(href, str):
            m = re.search(r"item=(\d+)", href)
            if m:
                return m.group(1)
    return ""


def _extract_biblionumber(title_tag: Tag | None) -> str:
    """Extract biblionumber from the title link href (e.g. biblionumber=12345)."""
    if not title_tag:
        return ""
    href = title_tag.get("href", "")
    if isinstance(href, str):
        m = re.search(r"biblionumber=(\d+)", href)
        if m:
            return m.group(1)
    return ""


def _attr(tag: Tag | None, name: str) -> str | None:
    if tag is None:
        return None
    value = tag.get(name)
    return value if isinstance(value, str) else None


def _first_text(soup: BeautifulSoup, selector: str) -> str | None:
    tag = soup.select_one(selector)
    if tag is None:
        return None
    text = tag.get_text(strip=True)
    return text or None


def _cell_text(row: Tag, selector: str) -> str:
    cell = row.select_one(selector)
    if not cell:
        return ""
    # Remove tdlabel spans before extracting text (Koha adds hidden labels)
    for label in cell.select("span.tdlabel"):
        label.decompose()
    return cell.get_text(strip=True)


def _parse_data_order_date(row: Tag, selector: str) -> date | None:
    cell = row.select_one(selector)
    if not cell:
        return None
    data_order = cell.get("data-order")
    if data_order and isinstance(data_order, str):
        try:
            # data-order may contain datetime "2026-04-25 23:59:00"; take date part only
            date_str = data_order.split()[0]
            return date.fromisoformat(date_str)
        except (ValueError, IndexError):
            pass
    # Fallback: parse German date from text
    return _parse_german_date(cell.get_text(strip=True))


def _parse_german_date(text: str) -> date | None:
    """Parse DD.MM.YYYY format."""
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def _parse_german_decimal(text: str) -> float:
    """Parse German decimal format (comma as separator)."""
    cleaned = text.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_renewals(row: Tag) -> tuple[int, int]:
    """Extract (times_renewed, max_renewals) from renewal cell.

    Real format: "( X von Y Verlängerungen verbleiben )" where X = remaining, Y = total.
    """
    renew_cell = row.select_one("td.renew")
    if not renew_cell:
        return 0, 0
    text = renew_cell.get_text()
    m = re.search(r"(\d+)\s+von\s+(\d+)\s+Verlängerungen\s+verbleiben", text)
    if m:
        remaining = int(m.group(1))
        total = int(m.group(2))
        return total - remaining, total
    # Legacy fallback: "Verlängerungen: X von Y" or "X von Y"
    m = re.search(r"(\d+)\s+von\s+(\d+)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


def _parse_no_renewal_before(row: Tag) -> date | None:
    """Extract the earliest renewal date from 'Keine Verlängerung vor DD.MM.YYYY HH:MM'."""
    span = row.select_one("span.no-renewal-before")
    if not span:
        return None
    text = span.get_text(strip=True)
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def _is_renewals_disabled(row: Tag) -> bool:
    """Check if renewals are explicitly disabled via 'Keine Verlängerung möglich'."""
    span = row.select_one("span.renewals-disabled")
    return span is not None
