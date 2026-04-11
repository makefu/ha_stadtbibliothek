"""Tests for the shared serializers module."""

from __future__ import annotations

from datetime import date

from custom_components.stadtbibliothek.backends.base import FeeItem, LoanItem
from custom_components.stadtbibliothek.serializers import serialize_fee, serialize_loan


def test_serialize_loan_converts_dates_to_iso(sample_loans):
    loan = sample_loans[0]
    result = serialize_loan(loan)
    assert result["due_date"] == loan.due_date.isoformat()
    assert isinstance(result["due_date"], str)


def test_serialize_loan_includes_computed_properties(sample_loans):
    result = serialize_loan(sample_loans[0])
    assert "days_remaining" in result
    assert "is_overdue" in result
    assert "renewals_left" in result
    assert result["renewals_left"] == sample_loans[0].max_renewals - sample_loans[0].times_renewed


def test_serialize_loan_overdue_item(sample_loans):
    overdue_loan = sample_loans[1]  # due_date=2020-01-01
    result = serialize_loan(overdue_loan)
    assert result["is_overdue"] is True
    assert result["days_remaining"] < 0


def test_serialize_loan_none_checkout_date():
    loan = LoanItem(
        title="Test",
        item_id="1",
        due_date=date.today(),
        can_be_renewed=True,
    )
    result = serialize_loan(loan)
    assert result["checkout_date"] is None


def test_serialize_fee_converts_date(sample_fees):
    result = serialize_fee(sample_fees[0])
    assert result["date"] == "2024-01-15"
    assert result["description"] == "Late fee"
    assert result["amount"] == 1.50


def test_serialize_fee_none_date():
    fee = FeeItem(description="Misc", amount=5.00, date=None)
    result = serialize_fee(fee)
    assert result["date"] is None
