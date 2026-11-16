from __future__ import annotations

import asyncio
import datetime
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, ClassVar

import httpx

_LOGGER = logging.getLogger(__name__)


class LibraryType(str, Enum):
    REMSECK = "remseck"
    STUTTGART = "stuttgart"


@dataclass
class LoanItem:
    title: str
    item_id: str
    due_date: date
    checkout_date: date | None = None
    author: str | None = None
    media_type: str | None = None
    library_branch: str | None = None
    can_be_renewed: bool = True
    times_renewed: int = 0
    max_renewals: int | None = None
    call_number: str | None = None
    #: Exemplar barcode, when the OPAC exposes one. Stuttgart prints it for
    #: books but not for CDs/DVDs/games; Koha does not show it at all.
    barcode: str | None = None
    publisher: str | None = None
    isbn: str | None = None
    #: Absolute URLs, when the OPAC links them from the loan listing.
    cover_url: str | None = None
    detail_url: str | None = None

    @property
    def days_remaining(self) -> int:
        return (self.due_date - date.today()).days

    @property
    def is_overdue(self) -> bool:
        return self.days_remaining < 0

    @property
    def renewals_left(self) -> int | None:
        if self.max_renewals is not None:
            return max(0, self.max_renewals - self.times_renewed)
        return None


@dataclass
class FeeItem:
    description: str
    amount: float  # EUR
    date: datetime.date | None = None


@dataclass
class AccountInfo:
    username: str
    library_type: LibraryType
    loans: list[LoanItem] = field(default_factory=list)
    fees: list[FeeItem] = field(default_factory=list)
    total_fees: float = 0.0


class AuthenticationError(Exception):
    pass


class RenewalError(Exception):
    pass


class ParseError(Exception):
    """The fetched page is not the account page we expected.

    Distinguishes "the OPAC changed, or the session expired" from "the account
    genuinely has nothing on it". Consumers that record history must never
    treat the former as an empty account.
    """


class RateLimitedError(Exception):
    """The OPAC turned us away: 403 or 429.

    Distinct from a plain network error so that a poller can tell "back off
    and try much later" from "the cable is unplugged": a 403 from these sites
    is the front-end firewall rejecting our traffic pattern, and hammering it
    again on the normal schedule is what extends the block.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str,
        status_code: int,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code
        #: Seconds the server asked us to wait (from ``Retry-After``), when it
        #: said; None means it gave no hint.
        self.retry_after = retry_after


class LibraryBackend(ABC):
    library_type: LibraryType

    #: Default host of the OPAC. Overridable per instance via ``base_url`` so a
    #: backend can be pointed at a test server or another installation of the
    #: same OPAC software.
    BASE_URL: ClassVar[str]
    base_url: str

    #: Whether get_fees() actually queries the OPAC. When False it returns an
    #: empty list because fees are not implemented for this library, which is
    #: not the same as the account having none.
    supports_fees: ClassVar[bool] = True

    #: Whether fetch_details() actually retrieves anything. It costs one extra
    #: request per item, so callers that poll frequently should skip it.
    supports_details: ClassVar[bool] = False

    #: Extra headers for requests to this OPAC. Applied per request, not only
    #: to a client the backend opened itself: a caller may hand us a shared
    #: client whose headers are generic, and the site's firewall decides by
    #: what the request looks like, not by who owns the connection pool.
    CLIENT_HEADERS: ClassVar[dict[str, str]] = {}
    REQUEST_TIMEOUT: ClassVar[float] = 30.0

    #: Minimum seconds between two requests to this OPAC. These front ends
    #: rate-limit by burst shape: a poll that fires a dozen logins in twenty
    #: seconds gets the address blocked. Zero disables pacing; subclasses set
    #: a value that keeps a full poll comfortably under the sites' radar.
    REQUEST_MIN_INTERVAL: ClassVar[float] = 0.0

    #: Statuses that mean "you are being rate-limited or firewalled" rather
    #: than "this page is gone". Raised as RateLimitedError, never raise_for_status.
    RATE_LIMIT_STATUSES: ClassVar[tuple[int, ...]] = (403, 429)

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        # A client we were handed belongs to the caller and may be shared with
        # other backends, so close() must leave it alone.
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            headers=self.CLIENT_HEADERS or None,
            follow_redirects=True,
            timeout=self.REQUEST_TIMEOUT,
        )
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        # Pacing is per instance: each poll builds a fresh backend, so the
        # schedule resets between polls and only serialises within one.
        self._pace_lock = asyncio.Lock()
        self._next_request_at = 0.0

    async def _pace(self) -> None:
        """Wait our turn, then reserve the next slot.

        Reserve-then-wait, not wait-then-act: two coroutines sharing a client
        must still leave the site REQUEST_MIN_INTERVAL apart.
        """
        interval = self.REQUEST_MIN_INTERVAL
        if interval <= 0:
            return
        async with self._pace_lock:
            now = time.monotonic()
            wait = self._next_request_at - now
            self._next_request_at = max(now, self._next_request_at) + interval
        if wait > 0:
            await asyncio.sleep(wait)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        check: bool = True,
        headers: dict[str, str] | None = None,
        **kw: Any,
    ) -> httpx.Response:
        """One paced, identified, classified request.

        Every route to the OPAC goes through here so that pacing, the browser
        headers, the rate-limit raise, and the failure log cannot be forgotten
        at one call site.

        ``check`` reproduces ``raise_for_status()``; pass False where the
        caller inspects the status itself. A rate-limit status raises
        RateLimitedError either way: it must not be mistaken for a page.
        """
        await self._pace()
        resp = await self._client.request(
            method,
            url,
            headers={**self.CLIENT_HEADERS, **(headers or {})},
            **kw,
        )
        if resp.status_code >= 400:
            _LOGGER.warning(
                "%s: %s %s -> %d%s",
                self.library_type.value,
                method,
                url,
                resp.status_code,
                _body_snippet(resp),
            )
        if resp.status_code in self.RATE_LIMIT_STATUSES:
            raise RateLimitedError(
                f"{self.library_type.value}: {method} {url} rejected with {resp.status_code}{_body_snippet(resp)}",
                url=url,
                status_code=resp.status_code,
                retry_after=_retry_after_seconds(resp),
            )
        if check:
            resp.raise_for_status()
        return resp

    async def _get(self, url: str, *, check: bool = True, **kw: Any) -> httpx.Response:
        return await self._request("GET", url, check=check, **kw)

    async def _post(self, url: str, *, check: bool = True, **kw: Any) -> httpx.Response:
        return await self._request("POST", url, check=check, **kw)

    @abstractmethod
    async def login(self, username: str, password: str) -> None:
        """Authenticate. Raises AuthenticationError on failure."""

    @abstractmethod
    async def get_loans(self) -> list[LoanItem]:
        """Fetch current checkouts."""

    @abstractmethod
    async def get_fees(self) -> list[FeeItem]:
        """Fetch outstanding fees."""

    @abstractmethod
    async def renew_loan(self, item_id: str) -> bool:
        """Renew a single loan. Returns True on success."""

    @abstractmethod
    async def renew_all(self, days_remaining_threshold: int = 14) -> int:
        """Renew all renewable loans. Returns count renewed."""

    async def fetch_details(self, loan: LoanItem) -> LoanItem:
        """Enrich a loan with data that needs an extra request.

        Deliberately not part of get_loans(): it costs one request per item,
        which a frequent poller does not want to pay. Returns the loan
        unchanged when the backend has nothing more to offer.
        """
        return loan

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _body_snippet(resp: httpx.Response) -> str:
    """A short piece of the error page, enough to name the blocker.

    These sites answer a block with one identifying sentence ("Request
    forbidden by administrative rules"); logging the whole body buries it.
    """
    try:
        text = " ".join(resp.text.split())
    except (LookupError, ValueError):  # an unknown charset must not lose the log line
        return ""
    return f" | {text[:200]}" if text else ""


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """The Retry-After delay, in seconds, when the server sent a usable one."""
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = datetime.datetime.strptime(raw, "%a, %d %b %Y %H:%M:%S %Z")
    except ValueError:
        return None
    now = datetime.datetime.now(datetime.UTC)
    return max(0.0, (when.replace(tzinfo=datetime.UTC) - now).total_seconds())
