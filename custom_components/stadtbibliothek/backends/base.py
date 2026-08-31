from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import ClassVar


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

    async def close(self) -> None:
        pass
