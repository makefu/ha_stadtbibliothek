"""Tests for the Stuttgart CLI."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.stadtbibliothek.backends.base import AuthenticationError
from custom_components.stadtbibliothek.cli_stuttgart import main


@pytest.fixture
def mock_backend_instance(sample_loans, sample_fees):
    backend = AsyncMock()
    backend.login = AsyncMock()
    backend.get_loans = AsyncMock(return_value=sample_loans)
    backend.get_fees = AsyncMock(return_value=sample_fees)
    backend.renew_loan = AsyncMock(return_value=True)
    backend.close = AsyncMock()
    return backend


@pytest.fixture
def _patch_backend(mock_backend_instance):
    with patch(
        "custom_components.stadtbibliothek.cli_stuttgart.StuttgartBackend",
        return_value=mock_backend_instance,
    ):
        yield mock_backend_instance


class TestStatusHumanReadable:
    def test_output_contains_titles_and_dates(self, _patch_backend, sample_loans, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", ["stadtbibliothek-stuttgart", "status", "--username", "u", "--password", "p"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Python Crash Course" in out
        assert "Clean Code" in out
        for loan in sample_loans:
            assert loan.due_date.isoformat() in out
        assert "Late fee" in out
        assert "Lost item" in out


class TestStatusJson:
    def test_json_output_parses_and_has_fields(self, _patch_backend, capsys, monkeypatch):
        monkeypatch.setattr(
            "sys.argv", ["stadtbibliothek-stuttgart", "status", "--username", "u", "--password", "p", "--json"]
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        data = json.loads(capsys.readouterr().out)
        assert "loans" in data
        assert "fees" in data
        assert "total_fees" in data
        assert len(data["loans"]) == 2
        assert data["loans"][0]["title"] == "Python Crash Course"
        assert data["loans"][0]["due_date"] == date.today().isoformat()
        assert "days_remaining" in data["loans"][0]
        assert "is_overdue" in data["loans"][0]
        assert "renewals_left" in data["loans"][0]
        assert data["total_fees"] == 11.50


class TestRenewSuccess:
    def test_exit_code_zero(self, _patch_backend, capsys, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["stadtbibliothek-stuttgart", "renew", "--username", "u", "--password", "p", "--item-id", "12345"],
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        assert "success" in capsys.readouterr().out


class TestRenewFailure:
    def test_exit_code_two(self, _patch_backend, mock_backend_instance, capsys, monkeypatch):
        mock_backend_instance.renew_loan = AsyncMock(return_value=False)
        monkeypatch.setattr(
            "sys.argv",
            ["stadtbibliothek-stuttgart", "renew", "--username", "u", "--password", "p", "--item-id", "99999"],
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
        assert "FAILED" in capsys.readouterr().out


class TestAuthFailure:
    def test_exit_code_one(self, _patch_backend, mock_backend_instance, capsys, monkeypatch):
        mock_backend_instance.login = AsyncMock(side_effect=AuthenticationError("bad credentials"))
        monkeypatch.setattr("sys.argv", ["stadtbibliothek-stuttgart", "status", "--username", "u", "--password", "p"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        assert "Authentication failed" in capsys.readouterr().err


class TestVersion:
    def test_version_output(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", ["stadtbibliothek-stuttgart", "--version"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "stadtbibliothek-stuttgart" in out
        expected_version = json.loads(
            (Path(__file__).parent.parent / "custom_components/stadtbibliothek/manifest.json").read_text()
        )["version"]
        assert expected_version in out


class TestNoSubcommand:
    def test_prints_help_and_exits_zero(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", ["stadtbibliothek-stuttgart"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "usage:" in out.lower() or "subcommands" in out.lower()
