"""Shared serialization helpers for LoanItem and FeeItem."""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

from .backends.base import FeeItem, LoanItem


def serialize_loan(loan: LoanItem) -> dict[str, Any]:
    """Serialize a LoanItem including computed properties and ISO date strings."""
    d = dataclasses.asdict(loan)
    for key in ("due_date", "checkout_date"):
        if isinstance(d.get(key), date):
            d[key] = d[key].isoformat()
    d["days_remaining"] = loan.days_remaining
    d["is_overdue"] = loan.is_overdue
    d["renewals_left"] = loan.renewals_left
    return d


def serialize_fee(fee: FeeItem) -> dict[str, Any]:
    """Serialize a FeeItem with ISO date strings."""
    d = dataclasses.asdict(fee)
    if isinstance(d.get("date"), date):
        d["date"] = d["date"].isoformat()
    return d
