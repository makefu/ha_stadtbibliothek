"""Live integration test for Stuttgart aDIS/BMS library backend."""

import asyncio
import os
import sys
from pathlib import Path

import httpx
import yaml

# Add project root to path so we can import the backend
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from custom_components.stadtbibliothek.backends.base import AuthenticationError, LoanItem, FeeItem
from custom_components.stadtbibliothek.backends.stuttgart import StuttgartBackend


def load_credentials() -> tuple[str, str]:
    env_path = os.environ.get("SECRETS_FILE")
    if env_path:
        secrets_path = Path(env_path)
    else:
        secrets_path = Path(__file__).resolve().parents[2] / ".secrets.yml"
    if not secrets_path.exists():
        print(f"ERROR: secrets file not found at {secrets_path}")
        sys.exit(1)

    with open(secrets_path) as f:
        secrets = yaml.safe_load(f)

    username = str(secrets["stuttgart_username"])
    password = str(secrets["stuttgart_password"])
    return username, password


def print_loan(i: int, loan: LoanItem) -> None:
    print(f"\n  --- Loan #{i} ---")
    print(f"  title:          {loan.title}")
    print(f"  author:         {loan.author}")
    print(f"  item_id:        {loan.item_id}")
    print(f"  due_date:       {loan.due_date}")
    print(f"  days_remaining: {loan.days_remaining}")
    print(f"  is_overdue:     {loan.is_overdue}")
    print(f"  checkout_date:  {loan.checkout_date}")
    print(f"  media_type:     {loan.media_type}")
    print(f"  library_branch: {loan.library_branch}")
    print(f"  can_be_renewed: {loan.can_be_renewed}")
    print(f"  times_renewed:  {loan.times_renewed}")
    print(f"  max_renewals:   {loan.max_renewals}")
    print(f"  renewals_left:  {loan.renewals_left}")
    print(f"  call_number:    {loan.call_number}")


def print_fee(i: int, fee: FeeItem) -> None:
    print(f"\n  --- Fee #{i} ---")
    print(f"  description: {fee.description}")
    print(f"  amount:      {fee.amount} EUR")
    print(f"  date:        {fee.date}")


async def main() -> None:
    print("=" * 60)
    print("Stuttgart aDIS/BMS Integration Test")
    print("=" * 60)

    username, password = load_credentials()
    print(f"\nCredentials loaded: username={username}")

    client = httpx.AsyncClient(
        headers={"User-Agent": StuttgartBackend._USER_AGENT},
        follow_redirects=True,
    )
    backend = StuttgartBackend(client=client)

    # --- Login ---
    print("\n--- LOGIN ---")
    try:
        await backend.login(username, password)
    except AuthenticationError as e:
        print(f"FAILED: {e}")
        # Try to dump some debug info
        print("\nAttempting debug: fetching start page HTML snippet...")
        try:
            resp = await client.get(f"{backend.BASE_URL}{backend.START_PATH}")
            print(resp.text[:2000])
        except Exception as e2:
            print(f"  Could not fetch debug page: {e2}")
        await backend.close()
        sys.exit(1)
    except Exception as e:
        print(f"UNEXPECTED ERROR during login: {type(e).__name__}: {e}")
        await backend.close()
        sys.exit(1)

    # Extract jsessionid from the login URL
    jsessionid = ""
    if backend._login_url:
        parts = backend._login_url.split(";")
        if len(parts) > 1:
            jsessionid = parts[1].split("?")[0]
    print("  Login successful!")
    print(f"  jsessionid:    {jsessionid}")
    print(f"  ausleihen_url: {backend._ausleihen_url}")

    # --- Loans ---
    print("\n--- LOANS ---")
    try:
        loans = await backend.get_loans()
    except Exception as e:
        print(f"ERROR fetching loans: {type(e).__name__}: {e}")
        await backend.close()
        sys.exit(1)

    print(f"Total loans: {len(loans)}")
    for i, loan in enumerate(loans, 1):
        print_loan(i, loan)

    # --- Fees ---
    print("\n--- FEES ---")
    try:
        fees = await backend.get_fees()
    except Exception as e:
        print(f"ERROR fetching fees: {type(e).__name__}: {e}")
        fees = []

    if fees:
        print(f"Total fees: {len(fees)}")
        for i, fee in enumerate(fees, 1):
            print_fee(i, fee)
    else:
        print("No fees found.")

    # --- Close ---
    await backend.close()

    # --- Summary ---
    overdue = [loan for loan in loans if loan.is_overdue]
    due_soon = [loan for loan in loans if 0 <= loan.days_remaining <= 7]
    not_renewable = [loan for loan in loans if not loan.can_be_renewed]

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Total loans:       {len(loans)}")
    print(f"  Overdue items:     {len(overdue)}")
    print(f"  Due within 7 days: {len(due_soon)}")
    print(f"  Not renewable:     {len(not_renewable)}")
    if fees:
        total = sum(f.amount for f in fees)
        print(f"  Total fees:        {total:.2f} EUR")
    print("\nIntegration test PASSED.")


if __name__ == "__main__":
    asyncio.run(main())
