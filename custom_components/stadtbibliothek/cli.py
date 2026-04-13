"""Common CLI logic for querying library account status and renewing loans."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from custom_components.stadtbibliothek.backends.base import AuthenticationError, FeeItem, LibraryBackend, LoanItem
from custom_components.stadtbibliothek.serializers import serialize_fee, serialize_loan


def _get_version() -> str:
    manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
    return manifest["version"]


def _format_status(loans: list[LoanItem], fees: list[FeeItem]) -> str:
    lines: list[str] = []
    if loans:
        lines.append(
            f"{'Title':<40} {'Author':<25} {'Item ID':<12} {'Due Date':<12} {'Days Left':>9}  {'Overdue':<7}  {'Renewable'}"
        )
        lines.append("-" * 125)
        for loan in loans:
            overdue = "YES" if loan.is_overdue else "no"
            renewable = "yes" if loan.can_be_renewed else "no"
            title = (loan.title[:37] + "...") if len(loan.title) > 40 else loan.title
            author = ((loan.author[:22] + "...") if len(loan.author) > 25 else loan.author) if loan.author else ""
            item_id = (loan.item_id[:9] + "...") if len(loan.item_id) > 12 else loan.item_id
            lines.append(
                f"{title:<40} {author:<25} {item_id:<12} {loan.due_date.isoformat():<12} {loan.days_remaining:>9}  {overdue:<7}  {renewable}"
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


async def _run_status(backend: LibraryBackend, args: argparse.Namespace) -> int:
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
            "loans": [serialize_loan(loan) for loan in loans],
            "fees": [serialize_fee(fee) for fee in fees],
            "total_fees": total_fees,
        }
        print(json.dumps(output, indent=2))
    else:
        print(_format_status(loans, fees))

    return 0


async def _run_renew(backend: LibraryBackend, args: argparse.Namespace) -> int:
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


def build_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--version", action="version", version=f"{prog} {_get_version()}")

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


def run_cli(prog: str, description: str, backend_factory: type[LibraryBackend]) -> None:
    parser = build_parser(prog, description)
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    backend = backend_factory()

    if args.command == "status":
        code = asyncio.run(_run_status(backend, args))
    elif args.command == "renew":
        code = asyncio.run(_run_renew(backend, args))
    else:
        parser.print_help()
        code = 0

    sys.exit(code)
