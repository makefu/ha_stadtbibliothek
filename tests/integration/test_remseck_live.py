"""Live integration test for Remseck (Koha/LMSCloud) backend.

Runs against mt-remseck.lmscloud.net with real credentials from .secrets.yml.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import yaml

# Allow running from repo root — add custom_components to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from custom_components.stadtbibliothek.backends.base import FeeItem, LoanItem
from custom_components.stadtbibliothek.backends.remseck import RemseckBackend


def load_credentials() -> tuple[str, str]:
    # Accept secrets path as CLI argument or SECRETS_FILE env var
    if len(sys.argv) > 1:
        secrets_path = Path(sys.argv[1])
    elif os.environ.get("SECRETS_FILE"):
        secrets_path = Path(os.environ["SECRETS_FILE"])
    else:
        print("Usage: test_remseck_live.py <path-to-secrets.yml>")
        print("  or set SECRETS_FILE environment variable")
        sys.exit(1)
    if not secrets_path.exists():
        print(f"ERROR: secrets file not found at {secrets_path}")
        sys.exit(1)

    with open(secrets_path) as f:
        secrets = yaml.safe_load(f)

    username = str(secrets["remseck_username"])
    password = str(secrets["remseck_password"])
    return username, password


def print_loan(idx: int, loan: LoanItem) -> None:
    print(f"\n  --- Loan #{idx} ---")
    print(f"  title:          {loan.title}")
    print(f"  author:         {loan.author}")
    print(f"  item_id:        {loan.item_id}")
    print(f"  media_type:     {loan.media_type}")
    print(f"  call_number:    {loan.call_number}")
    print(f"  library_branch: {loan.library_branch}")
    print(f"  checkout_date:  {loan.checkout_date}")
    print(f"  due_date:       {loan.due_date}")
    print(f"  days_remaining: {loan.days_remaining}")
    print(f"  is_overdue:     {loan.is_overdue}")
    print(f"  can_be_renewed: {loan.can_be_renewed}")
    print(f"  times_renewed:  {loan.times_renewed}")
    print(f"  max_renewals:   {loan.max_renewals}")
    print(f"  renewals_left:  {loan.renewals_left}")


def print_fee(idx: int, fee: FeeItem) -> None:
    print(f"\n  --- Fee #{idx} ---")
    print(f"  description: {fee.description}")
    print(f"  amount:      {fee.amount:.2f} EUR")
    print(f"  date:        {fee.date}")


async def main() -> None:
    username, password = load_credentials()
    print("Remseck live integration test")
    print(f"Target: {RemseckBackend.BASE_URL}")
    print(f"User:   {username}")
    print("=" * 60)

    backend = RemseckBackend()

    # Login
    print("\n[1/4] Logging in...")
    try:
        await backend.login(username, password)
    except Exception as exc:
        print(f"LOGIN FAILED: {exc}")
        await backend.close()
        sys.exit(1)
    print("Login successful.")

    # Loans
    print("\n[2/4] Fetching loans...")
    try:
        loans = await backend.get_loans()
    except Exception as exc:
        print(f"WARNING: get_loans() failed: {exc}")
        loans = []

    print(f"Found {len(loans)} loan(s):")
    for idx, loan in enumerate(loans, 1):
        print_loan(idx, loan)

    # Fees
    print("\n[3/4] Fetching fees...")
    try:
        fees = await backend.get_fees()
    except Exception as exc:
        print(f"WARNING: get_fees() failed: {exc}")
        fees = []

    print(f"Found {len(fees)} fee(s):")
    for idx, fee in enumerate(fees, 1):
        print_fee(idx, fee)

    # Close
    print("\n[4/4] Closing session...")
    await backend.close()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total loans:   {len(loans)}")
    print(f"Total fees:    {len(fees)}")

    overdue = [loan for loan in loans if loan.is_overdue]
    print(f"Overdue items: {len(overdue)}")
    for item in overdue:
        print(f"  - {item.title} (due {item.due_date}, {abs(item.days_remaining)} days overdue)")

    soon = [loan for loan in loans if 0 <= loan.days_remaining <= 7]
    print(f"Due within 7 days: {len(soon)}")
    for item in soon:
        print(f"  - {item.title} (due {item.due_date}, {item.days_remaining} days left)")

    total_fees = sum(fee.amount for fee in fees)
    print(f"Total fees:    {total_fees:.2f} EUR")
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
