"""CLI for querying Remseck (Koha) library account status and renewing loans."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from custom_components.stadtbibliothek.backends.base import AuthenticationError, FeeItem, LoanItem
from custom_components.stadtbibliothek.backends.remseck import RemseckBackend


def _get_version() -> str:
    manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
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


def _format_status(loans: list[LoanItem], fees: list[FeeItem]) -> str:
    lines: list[str] = []
    if loans:
        lines.append(f"{'Title':<40} {'Author':<25} {'Due Date':<12} {'Days Left':>9}  {'Overdue':<7}  {'Renewable'}")
        lines.append("-" * 110)
        for loan in loans:
            overdue = "YES" if loan.is_overdue else "no"
            renewable = "yes" if loan.can_be_renewed else "no"
            title = (loan.title[:37] + "...") if len(loan.title) > 40 else loan.title
            author = ((loan.author[:22] + "...") if len(loan.author) > 25 else loan.author) if loan.author else ""
            lines.append(
                f"{title:<40} {author:<25} {loan.due_date.isoformat():<12} {loan.days_remaining:>9}  {overdue:<7}  {renewable}"
            )
    else:
        lines.append("No active loans.")

    lines.append("")
    if fees:
        total = sum(f.amount for f in fees)
        lines.append("Fees:")
        for fee in fees:
            date_str = fee.date.isoformat() if fee.date else ""
            lines.append(f"  {fee.description:<30} {fee.amount:>8.2f} EUR  {date_str}")
        lines.append(f"  {'Total':<30} {total:>8.2f} EUR")
    else:
        lines.append("No fees.")

    return "\n".join(lines)


async def _run_status(args: argparse.Namespace) -> int:
    backend = RemseckBackend()
    try:
        await backend.login(args.username, args.password)
        loans = await backend.get_loans()
        fees = await backend.get_fees()
    except AuthenticationError:
        print("Authentication failed.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        await backend.close()

    if args.json:
        total_fees = sum(f.amount for f in fees)
        output = {
            "loans": [_serialize_loan(loan) for loan in loans],
            "fees": [_serialize_fee(fee) for fee in fees],
            "total_fees": total_fees,
        }
        print(json.dumps(output, indent=2))
    else:
        print(_format_status(loans, fees))

    return 0


async def _run_renew(args: argparse.Namespace) -> int:
    backend = RemseckBackend()
    try:
        await backend.login(args.username, args.password)
    except AuthenticationError:
        print("Authentication failed.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    all_ok = True
    try:
        for item_id in args.item_id:
            try:
                ok = await backend.renew_loan(item_id)
            except Exception as exc:
                print(f"Error renewing {item_id}: {exc}", file=sys.stderr)
                ok = False
            if ok:
                print(f"Renewed {item_id}: success")
            else:
                print(f"Renewed {item_id}: FAILED")
                all_ok = False
    finally:
        await backend.close()

    return 0 if all_ok else 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stadtbibliothek-remseck", description="Remseck (Koha) library account CLI")
    parser.add_argument("--version", action="version", version=f"stadtbibliothek-remseck {_get_version()}")

    subparsers = parser.add_subparsers(dest="command", title="subcommands")

    status_parser = subparsers.add_parser("status", help="Show loan status")
    status_parser.add_argument("--username", required=True, help="Library account username")
    status_parser.add_argument("--password", required=True, help="Library account password")
    status_parser.add_argument("--json", action="store_true", help="Output as JSON")

    renew_parser = subparsers.add_parser("renew", help="Renew loans")
    renew_parser.add_argument("--username", required=True, help="Library account username")
    renew_parser.add_argument("--password", required=True, help="Library account password")
    renew_parser.add_argument("--item-id", required=True, action="append", help="Item ID to renew (can be repeated)")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "status":
        code = asyncio.run(_run_status(args))
    elif args.command == "renew":
        code = asyncio.run(_run_renew(args))
    else:
        parser.print_help()
        code = 0

    sys.exit(code)


if __name__ == "__main__":
    main()
