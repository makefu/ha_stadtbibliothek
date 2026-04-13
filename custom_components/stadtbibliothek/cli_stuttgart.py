"""CLI for querying Stuttgart (aDIS) library account status and renewing loans."""

from __future__ import annotations

from custom_components.stadtbibliothek.backends.stuttgart import StuttgartBackend
from custom_components.stadtbibliothek.cli import run_cli


def main() -> None:
    run_cli(
        prog="stadtbibliothek-stuttgart",
        description="Stuttgart (aDIS) library account CLI",
        backend_factory=StuttgartBackend,
    )


if __name__ == "__main__":
    main()
