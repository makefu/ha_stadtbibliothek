"""Tests for Stadtbibliothek sensor entities."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from custom_components.stadtbibliothek.backends.base import AccountInfo, LibraryType, LoanItem
from custom_components.stadtbibliothek.const import DOMAIN
from custom_components.stadtbibliothek.sensor import FeesSensor, LoansSensor, WarningSensor


def _make_sensor(sensor_cls, account: AccountInfo | None):
    coordinator = MagicMock()
    coordinator.data = account
    coordinator.refresh_required = False
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    return sensor_cls(coordinator, config_entry, "remseck", "testuser")


class TestLoansSensor:
    def test_state_is_loan_count(self, sample_account):
        sensor = _make_sensor(LoansSensor, sample_account)
        assert sensor.native_value == 2

    def test_state_none_when_no_data(self):
        sensor = _make_sensor(LoansSensor, None)
        assert sensor.native_value is None

    def test_attributes_contain_loans(self, sample_account):
        sensor = _make_sensor(LoansSensor, sample_account)
        attrs = sensor.extra_state_attributes
        assert len(attrs["loans"]) == 2
        assert attrs["loans"][0]["title"] == "Python Crash Course"

    def test_attributes_include_library(self, sample_account):
        sensor = _make_sensor(LoansSensor, sample_account)
        attrs = sensor.extra_state_attributes
        assert attrs["loans"][0]["library"] == "Remseck"

    def test_attributes_include_computed_properties(self, sample_account):
        """Loan attributes should include days_remaining, is_overdue, renewals_left."""
        sensor = _make_sensor(LoansSensor, sample_account)
        attrs = sensor.extra_state_attributes
        loan = attrs["loans"][0]
        assert "days_remaining" in loan
        assert "is_overdue" in loan
        assert "renewals_left" in loan
        assert isinstance(loan["days_remaining"], int)
        assert isinstance(loan["is_overdue"], bool)

    def test_attributes_dates_are_iso_strings(self, sample_account):
        """Date fields should be serialized as ISO format strings, not date objects."""
        sensor = _make_sensor(LoansSensor, sample_account)
        attrs = sensor.extra_state_attributes
        loan = attrs["loans"][0]
        assert isinstance(loan["due_date"], str)
        # The overdue loan has a checkout_date of None, first loan has date.today()
        # so check the due_date format
        date.fromisoformat(loan["due_date"])  # should not raise

    def test_name(self, sample_account):
        sensor = _make_sensor(LoansSensor, sample_account)
        assert sensor._attr_name == "Loans"

    def test_icon(self, sample_account):
        sensor = _make_sensor(LoansSensor, sample_account)
        assert sensor._attr_icon == "mdi:bookshelf"

    def test_unique_id(self, sample_account):
        sensor = _make_sensor(LoansSensor, sample_account)
        assert sensor._attr_unique_id == f"{DOMAIN}_remseck_testuser_loans"


class TestWarningSensor:
    def test_state_is_min_days_remaining(self, sample_account):
        sensor = _make_sensor(WarningSensor, sample_account)
        # One loan due today (0 days), one overdue (large negative)
        value = sensor.native_value
        assert isinstance(value, int)
        assert value < 0  # the overdue one from 2020-01-01

    def test_state_none_when_no_loans(self):
        account = AccountInfo(username="u", library_type=LibraryType.REMSECK, loans=[], fees=[])
        sensor = _make_sensor(WarningSensor, account)
        assert sensor.native_value == "none"

    def test_state_none_when_no_data(self):
        sensor = _make_sensor(WarningSensor, None)
        assert sensor.native_value == "none"

    def test_overdue_icon(self, sample_account):
        sensor = _make_sensor(WarningSensor, sample_account)
        assert sensor.icon == "mdi:alert-circle"

    def test_no_overdue_icon(self):
        loans = [
            LoanItem(title="Book", item_id="1", due_date=date(2099, 12, 31)),
        ]
        account = AccountInfo(username="u", library_type=LibraryType.REMSECK, loans=loans, fees=[])
        sensor = _make_sensor(WarningSensor, account)
        assert sensor.icon == "mdi:clock-outline"

    def test_attributes_overdue_count(self, sample_account):
        sensor = _make_sensor(WarningSensor, sample_account)
        attrs = sensor.extra_state_attributes
        assert attrs["overdue_count"] == 1
        assert "earliest_due_date" in attrs

    def test_refresh_required_default_false(self, sample_account):
        sensor = _make_sensor(WarningSensor, sample_account)
        attrs = sensor.extra_state_attributes
        assert attrs["refresh_required"] is False

    def test_refresh_required_true_from_coordinator(self, sample_account):
        sensor = _make_sensor(WarningSensor, sample_account)
        sensor.coordinator.refresh_required = True
        attrs = sensor.extra_state_attributes
        assert attrs["refresh_required"] is True

    def test_name(self, sample_account):
        sensor = _make_sensor(WarningSensor, sample_account)
        assert sensor._attr_name == "Warning"

    def test_items_due_soon(self):
        loans = [
            LoanItem(title="A", item_id="1", due_date=date.today()),  # 0 days = due soon
            LoanItem(title="B", item_id="2", due_date=date(2099, 12, 31)),  # far future
        ]
        account = AccountInfo(username="u", library_type=LibraryType.REMSECK, loans=loans, fees=[])
        sensor = _make_sensor(WarningSensor, account)
        attrs = sensor.extra_state_attributes
        assert attrs["items_due_soon"] == 1


class TestFeesSensor:
    def test_state_is_total_fees(self, sample_account):
        sensor = _make_sensor(FeesSensor, sample_account)
        assert sensor.native_value == 11.50

    def test_state_none_when_no_data(self):
        sensor = _make_sensor(FeesSensor, None)
        assert sensor.native_value is None

    def test_zero_fees(self):
        account = AccountInfo(username="u", library_type=LibraryType.REMSECK, loans=[], fees=[], total_fees=0.0)
        sensor = _make_sensor(FeesSensor, account)
        assert sensor.native_value == 0.0

    def test_attributes_contain_fee_items(self, sample_account):
        sensor = _make_sensor(FeesSensor, sample_account)
        attrs = sensor.extra_state_attributes
        assert len(attrs["fee_items"]) == 2
        assert attrs["fee_items"][0]["description"] == "Late fee"
        assert attrs["fee_items"][0]["amount"] == 1.50

    def test_fee_dates_are_iso_strings(self, sample_account):
        """Fee date fields should be serialized as ISO format strings."""
        sensor = _make_sensor(FeesSensor, sample_account)
        attrs = sensor.extra_state_attributes
        fee = attrs["fee_items"][0]
        assert isinstance(fee["date"], str)
        date.fromisoformat(fee["date"])

    def test_name(self, sample_account):
        sensor = _make_sensor(FeesSensor, sample_account)
        assert sensor._attr_name == "Fees"

    def test_icon(self, sample_account):
        sensor = _make_sensor(FeesSensor, sample_account)
        assert sensor._attr_icon == "mdi:currency-eur"

    def test_unique_id(self, sample_account):
        sensor = _make_sensor(FeesSensor, sample_account)
        assert sensor._attr_unique_id == f"{DOMAIN}_remseck_testuser_fees"
