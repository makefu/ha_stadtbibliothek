"""Tests for Stadtbibliothek config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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


def _mock_create_backend(login_side_effect=None):
    """Return a create_backend replacement plus the backend it hands out."""
    backend = AsyncMock()
    if login_side_effect:
        backend.login = AsyncMock(side_effect=login_side_effect)
    else:
        backend.login = AsyncMock()
    return AsyncMock(return_value=backend), backend


@pytest.fixture
def flow():
    return StadtbibliothekConfigFlow()


async def test_show_form_on_first_step(flow):
    result = await flow.async_step_user()
    assert result["type"] == "form"
    assert result["step_id"] == "user"


async def test_successful_login_remseck(flow):
    factory, inst = _mock_create_backend()
    with patch("custom_components.stadtbibliothek.config_flow.create_backend", factory):
        result = await flow.async_step_user(_user_input())
    assert result["type"] == "create_entry"
    assert result["title"] == "Remseck (Koha) - testuser"
    assert result["data"][CONF_LIBRARY_TYPE] == "remseck"
    inst.login.assert_awaited_once_with("testuser", "testpass")


async def test_successful_login_stuttgart(flow):
    factory, inst = _mock_create_backend()
    with patch("custom_components.stadtbibliothek.config_flow.create_backend", factory):
        result = await flow.async_step_user(_user_input(library_type="stuttgart"))
    assert result["type"] == "create_entry"
    assert result["title"] == "Stuttgart (aDIS) - testuser"


async def test_invalid_auth_shows_error(flow):
    factory, _ = _mock_create_backend(login_side_effect=AuthenticationError("bad creds"))
    with patch("custom_components.stadtbibliothek.config_flow.create_backend", factory):
        result = await flow.async_step_user(_user_input())
    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"


async def test_connection_error_shows_error(flow):
    factory, _ = _mock_create_backend(login_side_effect=ConnectionError("timeout"))
    with patch("custom_components.stadtbibliothek.config_flow.create_backend", factory):
        result = await flow.async_step_user(_user_input())
    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"
