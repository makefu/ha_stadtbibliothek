"""CLI for querying Stuttgart (aDIS) library account status and renewing loans."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import pathlib
import sys
from datetime import date
from typing import Any

from custom_components.stadtbibliothek.backends.base import AuthenticationError, FeeItem, LoanItem
from custom_components.stadtbibliothek.backends.stuttgart import StuttgartBackend


def _get_version() -> str:
    manifest = json.loads((pathlib.Path(__file__).parent / "manifest.json").read_text())
    return manifest["version"]


def _serialize_loan(loan: LoanItem) -> dict[str, Any]:
    d = dataclasses.asdict(loan)
    for key in ("due_date", "checkout_date"):
        if isinstance(d.get(key), date):
            d[key] = d[key].isoformat()
    d["days_remaining"] = loan.days_remaining
    d["is_overdue"] = loan.is_overdue
    d["renewals_left"] = loan.renewals_left
    return d


def _serialize_fee(fee: FeeItem) -> dict[str, Any]:
    d = dataclasses.asdict(fee)
    if isinstance(d.get("date"), date):
        d["date"] = d["date"].isoformat()
    return d


def _format_loans_table(loans: list[LoanItem]) -> str:
    if not loans:
        return "No loans."
    lines: list[str] = []
    for loan in loans:
        overdue = " [OVERDUE]" if loan.is_overdue else ""
        renewable = "yes" if loan.can_be_renewed else "no"
        author = f" by {loan.author}" if loan.author else ""
        lines.append(
            f"  {loan.title}{author}\n"
            f"    Due: {loan.due_date.isoformat()}  Days remaining: {loan.days_remaining}{overdue}\n"
            f"    Renewable: {renewable}  Item ID: {loan.item_id}"
        )
    return "\n".join(lines)


def _format_fees(fees: list[FeeItem], total: float) -> str:
    if not fees:
        return "No fees."
    lines: list[str] = []
    for fee in fees:
        date_str = f" ({fee.date.isoformat()})" if fee.date else ""
        lines.append(f"  {fee.description}: {fee.amount:.2f} EUR{date_str}")
    lines.append(f"  Total: {total:.2f} EUR")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stadtbibliothek-stuttgart", description="Stuttgart (aDIS) library account CLI"
    )
    parser.add_argument("--version", action="version", version=f"stadtbibliothek-stuttgart {_get_version()}")

    subparsers = parser.add_subparsers(dest="command", title="subcommands")

    for sub in ("status", "renew"):
        sp = subparsers.add_parser(sub, help="Show loan status" if sub == "status" else "Renew loans")
        sp.add_argument("--username", required=True, metavar="USER")
        sp.add_argument("--password", required=True, metavar="PASS")
        if sub == "status":
            sp.add_argument("--json", action="store_true", dest="json_output")
        else:
            sp.add_argument("--item-id", required=True, action="append", dest="item_ids", metavar="ID")

    return parser


async def _run(args: argparse.Namespace) -> int:
    backend = StuttgartBackend()
    try:
        await backend.login(args.username, args.password)

        if args.command == "status":
            loans = await backend.get_loans()
            fees = await backend.get_fees()
            total_fees = sum(f.amount for f in fees)

            if args.json_output:
                print(
                    json.dumps(
                        {
                            "loans": [_serialize_loan(loan) for loan in loans],
                            "fees": [_serialize_fee(f) for f in fees],
                            "total_fees": total_fees,
                        },
                        indent=2,
                    )
                )
            else:
                print("Loans:")
                print(_format_loans_table(loans))
                print()
                print("Fees:")
                print(_format_fees(fees, total_fees))
            return 0

        else:  # renew
            all_ok = True
            for item_id in args.item_ids:
                success = await backend.renew_loan(item_id)
                if success:
                    print(f"Renewed {item_id}: OK")
                else:
                    print(f"Renewed {item_id}: FAILED")
                    all_ok = False
            return 0 if all_ok else 2

    except AuthenticationError as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        await backend.close()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(2)
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
