# Agent Development Guide

Home Assistant custom component for Stuttgart (aDIS/BMS) and Remseck (Koha/LMSCloud) public library accounts.

## Project Structure

```
custom_components/stadtbibliothek/
  __init__.py          # Integration setup, service registration
  config_flow.py       # HA config flow (UI setup)
  const.py             # Domain, config keys
  coordinator.py       # DataUpdateCoordinator (data fetching, renewal logic)
  sensor.py            # Sensor entities (Loans, Warning, Fees)
  serializers.py       # Shared LoanItem/FeeItem serialization
  cli_remseck.py       # Standalone CLI for Remseck
  cli_stuttgart.py     # Standalone CLI for Stuttgart
  services.yaml        # HA service definitions
  manifest.json        # HA component metadata (version is source of truth)
  backends/
    base.py            # Abstract backend, dataclasses (LoanItem, FeeItem, AccountInfo)
    remseck.py         # Koha/LMSCloud scraping backend
    stuttgart.py        # aDIS/BMS scraping backend
tests/
  conftest.py          # HA module stubs and shared fixtures
  test_sensor.py       # Sensor entity tests
  test_coordinator.py  # Coordinator tests
  test_services.py     # Service resolver tests (entity_id -> config_entry_id)
  test_serializers.py  # Serialization tests
  test_cli_remseck.py  # Remseck CLI tests
  test_cli_stuttgart.py # Stuttgart CLI tests
  test_config_flow.py  # Config flow tests
  test_backends/
    test_base.py       # LoanItem/FeeItem dataclass tests
    test_remseck.py    # Remseck backend parsing + renewal logic tests
    test_stuttgart.py   # Stuttgart backend parsing tests
    test_recorded_pages.py # Parsing asserted against the live recordings
    fixtures/          # Constructed HTML fixtures for backend tests
      recorded/        # Verbatim captures of the live OPACs (see its README)
  integration/         # Live integration tests (require credentials)
    record_fixtures.py # Re-records fixtures/recorded/ and anonymises them
```

## Development Environment

All development uses Nix. Enter the dev shell before running any commands:

```sh
nix develop
```

This provides Python 3.13 with all dependencies: httpx, beautifulsoup4, html5lib, lxml, pytest, pytest-asyncio, respx, ruff, mypy, voluptuous, freezegun.

## Testing

### Unit Tests

```sh
nix develop -c pytest tests/ --ignore=tests/integration -v
```

Tests use `conftest.py` stubs for Home Assistant modules (the real HA packages are not available). Backend tests use HTML fixtures in `tests/test_backends/fixtures/` and `respx` for HTTP mocking. Time-dependent tests use `freezegun`.

Use realistic inputs/outputs. Prefer testing against actual HTML structures over mocked responses. When fixing a bug, start with a failing regression test.

### Live Integration Tests

Require credentials in a YAML secrets file:

```sh
nix run .#integration-remseck -- /path/to/.secrets.yml
SECRETS_FILE=/path/to/.secrets.yml nix run .#integration-stuttgart
```

### Recorded Fixtures

`tests/test_backends/fixtures/recorded/` holds verbatim captures of both live
OPACs, and `test_recorded_pages.py` asserts the parsers against them. They
exist because the constructed fixtures have twice described markup the real
servers never sent, and both times a backend was "fixed" to match the fixture
and broke against the library.

Prefer adding a case there over inventing markup. To refresh the whole set:

```sh
nix run .#record-fixtures -- /path/to/.secrets.yml
```

That reads both accounts, never renews, and refuses to write a page it can
still find the account holder in. Anything derived from a recording -- an
empty account, a renewal response -- belongs in the constructed fixtures
one directory up, with a comment saying what it was derived from.

### NixOS VM Test

```sh
pueue add -- 'nix build .#checks.x86_64-linux.vm-test 2>&1'
pueue follow <task-id> | tail -n 20
```

## Linting and Formatting

All code must pass before committing:

```sh
nix develop -c ruff check
nix develop -c ruff format --check
```

Rules: line-length=120, target Python 3.13. Fix root causes of lint errors — never silence them.

## Building

The package is built with `buildPythonApplication`. Version is read from `manifest.json` (single source of truth). The build includes an install check that verifies both CLI entry points respond to `--version`.

```sh
pueue add -- 'nix build 2>&1'
pueue follow <task-id> | tail -n 20
```

Use `pueue` for any nix build — they can take longer than shell timeouts.

After building, verify CLIs:

```sh
./result/bin/stadtbibliothek-remseck --version
./result/bin/stadtbibliothek-stuttgart --version
```

## Versioning

Version lives in two places — both must be updated together:
- `custom_components/stadtbibliothek/manifest.json` (source of truth, read by flake.nix)
- `pyproject.toml`

## Adding New Files

Always track new files for Nix flakes before building:

```sh
git add -AN
```

Non-Python files in the package (e.g. `manifest.json`, `services.yaml`) must be listed in `pyproject.toml` under `[tool.setuptools.package-data]`.

## Architecture Notes

- **Backends** are standalone async classes using `httpx.AsyncClient`. They work without Home Assistant and are used by both the HA integration and the CLI tools.
- **Coordinator** wraps backends with HA's `DataUpdateCoordinator` pattern. It handles login, data fetching, and renewal orchestration.
- **Services** accept `entity_id` (resolved via entity registry) or `config_entry_id`. Renewal services return structured response data via `SupportsResponse.OPTIONAL`.
- **Serializers** are shared between `sensor.py` and both CLI tools to avoid duplication. The `library` field is injected at the sensor level, not in the serializer.
- **CLI tools** have identical interfaces. Changes to one must be mirrored in the other.

## Commit Messages

Use kernel-mailing-list style. Focus on WHY, not WHAT. Always test/lint/format before committing.
