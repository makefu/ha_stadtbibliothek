from datetime import date

from freezegun import freeze_time

from custom_components.stadtbibliothek.backends.base import (
    AccountInfo,
    FeeItem,
    LibraryType,
    LoanItem,
)


def _make_loan(**kwargs) -> LoanItem:
    defaults = {
        "title": "Test Book",
        "item_id": "123",
        "due_date": date(2026, 5, 1),
    }
    defaults.update(kwargs)
    return LoanItem(**defaults)


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
