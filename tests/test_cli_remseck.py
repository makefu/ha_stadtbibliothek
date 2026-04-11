"""Tests for the Remseck CLI script."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.stadtbibliothek.backends.base import AuthenticationError
from custom_components.stadtbibliothek.cli_remseck import main


@pytest.fixture
def mock_remseck_backend(sample_loans, sample_fees):
    backend = AsyncMock()
    backend.login = AsyncMock()
    backend.get_loans = AsyncMock(return_value=sample_loans)
    backend.get_fees = AsyncMock(return_value=sample_fees)
    backend.renew_loan = AsyncMock(return_value=True)
    backend.close = AsyncMock()
    return backend


@pytest.fixture
def _patch_backend(mock_remseck_backend):
    with patch("custom_components.stadtbibliothek.cli_remseck.RemseckBackend", return_value=mock_remseck_backend):
        yield mock_remseck_backend


def test_status_human_readable(_patch_backend, sample_loans, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["stadtbibliothek-remseck", "status", "--username", "user", "--password", "pass"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0

    output = capsys.readouterr().out
    assert "Python Crash Course" in output
    assert "Clean Code" in output
    for loan in sample_loans:
        assert loan.due_date.isoformat() in output
    assert "Late fee" in output
    assert "Lost item" in output


def test_status_json_output(_patch_backend, sample_loans, sample_fees, capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["stadtbibliothek-remseck", "status", "--username", "user", "--password", "pass", "--json"]
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0

    data = json.loads(capsys.readouterr().out)
    assert len(data["loans"]) == 2
    assert len(data["fees"]) == 2
    assert data["total_fees"] == 11.50

    loan_titles = {loan["title"] for loan in data["loans"]}
    assert "Python Crash Course" in loan_titles
    assert "Clean Code" in loan_titles

    for loan in data["loans"]:
        assert "days_remaining" in loan
        assert "is_overdue" in loan
        assert "renewals_left" in loan
        assert "due_date" in loan
        # due_date should be an ISO string
        assert isinstance(loan["due_date"], str)

    for fee in data["fees"]:
        assert "description" in fee
        assert "amount" in fee


def test_renew_success(_patch_backend, capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["stadtbibliothek-remseck", "renew", "--username", "user", "--password", "pass", "--item-id", "12345"],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0

    output = capsys.readouterr().out
    assert "12345" in output
    assert "success" in output


def test_renew_failure(_patch_backend, mock_remseck_backend, capsys, monkeypatch):
    mock_remseck_backend.renew_loan = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "sys.argv",
        ["stadtbibliothek-remseck", "renew", "--username", "user", "--password", "pass", "--item-id", "99999"],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2

    output = capsys.readouterr().out
    assert "FAILED" in output


def test_auth_failure(_patch_backend, mock_remseck_backend, capsys, monkeypatch):
    mock_remseck_backend.login = AsyncMock(side_effect=AuthenticationError("bad credentials"))
    monkeypatch.setattr("sys.argv", ["stadtbibliothek-remseck", "status", "--username", "user", "--password", "wrong"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1

    err = capsys.readouterr().err
    assert "Authentication failed" in err


def test_version(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["stadtbibliothek-remseck", "--version"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0

    output = capsys.readouterr().out
    assert "stadtbibliothek-remseck" in output
    assert "0.2.1" in output
