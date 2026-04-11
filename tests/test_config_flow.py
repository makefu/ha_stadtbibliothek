"""Tests for Stadtbibliothek config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.stadtbibliothek.backends.base import AuthenticationError
from custom_components.stadtbibliothek.config_flow import StadtbibliothekConfigFlow
from custom_components.stadtbibliothek.const import CONF_LIBRARY_TYPE, CONF_PASSWORD, CONF_USERNAME


def _user_input(library_type="remseck", username="testuser", password="testpass"):
    return {
        CONF_LIBRARY_TYPE: library_type,
        CONF_USERNAME: username,
        CONF_PASSWORD: password,
    }


def _mock_backend_cls(login_side_effect=None):
    """Return a mock backend class whose instances have a mock login()."""
    mock_instance = AsyncMock()
    if login_side_effect:
        mock_instance.login = AsyncMock(side_effect=login_side_effect)
    else:
        mock_instance.login = AsyncMock()
    cls = MagicMock(return_value=mock_instance)
    return cls, mock_instance


@pytest.fixture
def flow():
    return StadtbibliothekConfigFlow()


async def test_show_form_on_first_step(flow):
    result = await flow.async_step_user()
    assert result["type"] == "form"
    assert result["step_id"] == "user"


async def test_successful_login_remseck(flow):
    cls, inst = _mock_backend_cls()
    with patch.dict("custom_components.stadtbibliothek.config_flow.BACKEND_MAP", {"remseck": cls}):
        result = await flow.async_step_user(_user_input())
    assert result["type"] == "create_entry"
    assert result["title"] == "Remseck (Koha) - testuser"
    assert result["data"][CONF_LIBRARY_TYPE] == "remseck"
    inst.login.assert_awaited_once_with("testuser", "testpass")


async def test_successful_login_stuttgart(flow):
    cls, inst = _mock_backend_cls()
    with patch.dict("custom_components.stadtbibliothek.config_flow.BACKEND_MAP", {"stuttgart": cls}):
        result = await flow.async_step_user(_user_input(library_type="stuttgart"))
    assert result["type"] == "create_entry"
    assert result["title"] == "Stuttgart (aDIS) - testuser"


async def test_invalid_auth_shows_error(flow):
    cls, _ = _mock_backend_cls(login_side_effect=AuthenticationError("bad creds"))
    with patch.dict("custom_components.stadtbibliothek.config_flow.BACKEND_MAP", {"remseck": cls}):
        result = await flow.async_step_user(_user_input())
    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"


async def test_connection_error_shows_error(flow):
    cls, _ = _mock_backend_cls(login_side_effect=ConnectionError("timeout"))
    with patch.dict("custom_components.stadtbibliothek.config_flow.BACKEND_MAP", {"remseck": cls}):
        result = await flow.async_step_user(_user_input())
    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"
