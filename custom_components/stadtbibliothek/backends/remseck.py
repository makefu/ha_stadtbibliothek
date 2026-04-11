from __future__ import annotations

import re
from datetime import date

import httpx
from bs4 import BeautifulSoup, Tag

from .base import (
    AuthenticationError,
    FeeItem,
    LibraryBackend,
    LibraryType,
    LoanItem,
)


class RemseckBackend(LibraryBackend):
    """Backend for Mediathek Remseck (Koha/LMSCloud OPAC)."""

    library_type = LibraryType.REMSECK
    BASE_URL = "https://mt-remseck.lmscloud.net"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
        )

    async def login(self, username: str, password: str) -> None:
        resp = await self._client.post(
            f"{self.BASE_URL}/cgi-bin/koha/opac-user.pl",
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
            f"{self.BASE_URL}/cgi-bin/koha/opac-user.pl",
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", id="checkoutst")
        if not table or not isinstance(table, Tag):
            return []

        loans: list[LoanItem] = []
        for row in table.select("tbody tr"):
            loans.append(self._parse_loan_row(row))
        return loans

    async def get_fees(self) -> list[FeeItem]:
        resp = await self._client.get(
            f"{self.BASE_URL}/cgi-bin/koha/opac-account.pl",
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", id="finestable")
        if not table or not isinstance(table, Tag):
            return []

        fees: list[FeeItem] = []
        for row in table.select("tbody tr"):
            fees.append(self._parse_fee_row(row))
        return fees

    async def renew_loan(self, item_id: str) -> bool:
        resp = await self._client.post(
            f"{self.BASE_URL}/cgi-bin/koha/opac-renew.pl",
            data={"barcode": item_id},
        )
        return resp.status_code == 200

    async def renew_all(self) -> int:
        loans = await self.get_loans()
        renewed = 0
        for loan in loans:
            if loan.renewals_left > 0:
                if await self.renew_loan(loan.item_id):
                    renewed += 1
        return renewed

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _parse_loan_row(row: Tag) -> LoanItem:
        title_tag = row.select_one("td.title a.title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        author = _cell_text(row, "td.author")
        barcode = _cell_text(row, "td.barcode") or _extract_biblionumber(title_tag)
        media_type = _cell_text(row, "td.itype")
        call_number = _cell_text(row, "td.call_no")
        library_branch = _cell_text(row, "td.branch")

        due_date = _parse_data_order_date(row, "td.date_due")

        times_renewed, max_renewals = _parse_renewals(row)
        can_be_renewed = times_renewed < max_renewals

        return LoanItem(
            title=title,
            item_id=barcode,
            due_date=due_date or date.today(),
            author=author or None,
            media_type=media_type or None,
            library_branch=library_branch or None,
            call_number=call_number or None,
            can_be_renewed=can_be_renewed,
            times_renewed=times_renewed,
            max_renewals=max_renewals,
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


def _cell_text(row: Tag, selector: str) -> str:
    cell = row.select_one(selector)
    return cell.get_text(strip=True) if cell else ""


def _parse_data_order_date(row: Tag, selector: str) -> date | None:
    cell = row.select_one(selector)
    if not cell:
        return None
    data_order = cell.get("data-order")
    if data_order and isinstance(data_order, str):
        try:
            return date.fromisoformat(data_order)
        except ValueError:
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

    Looks for patterns like "Verlängerungen: 1 von 3" or "1 of 3".
    """
    renew_cell = row.select_one("td.renew")
    if not renew_cell:
        return 0, 0
    text = renew_cell.get_text()
    m = re.search(r"(\d+)\s+von\s+(\d+)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)\s+of\s+(\d+)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0
