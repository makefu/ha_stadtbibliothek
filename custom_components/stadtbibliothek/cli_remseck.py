"""CLI for querying Remseck (Koha) library account status and renewing loans."""

from __future__ import annotations

from custom_components.stadtbibliothek.backends.remseck import RemseckBackend
from custom_components.stadtbibliothek.cli import run_cli


def main() -> None:
    run_cli(
        prog="stadtbibliothek-remseck",
        description="Remseck (Koha) library account CLI",
        backend_factory=RemseckBackend,
    )


if __name__ == "__main__":
    main()
