"""Tests for the entity_id -> config_entry_id resolver."""

from __future__ import annotations

import pytest
import voluptuous as vol
from unittest.mock import MagicMock

from tests.conftest import _entity_registry_instance
from custom_components.stadtbibliothek import _resolve_config_entry_id


@pytest.fixture(autouse=True)
def _clean_registry():
    """Clear the entity registry between tests."""
    _entity_registry_instance._entries.clear()
    yield
    _entity_registry_instance._entries.clear()


@pytest.fixture
def hass():
    return MagicMock()


def test_resolve_with_config_entry_id(hass):
    result = _resolve_config_entry_id(hass, {"config_entry_id": "abc123"})
    assert result == "abc123"


def test_resolve_with_entity_id(hass):
    _entity_registry_instance.add(
        "sensor.stadtbibliothek_remseck_12345_loans",
        config_entry_id="abc123",
    )
    result = _resolve_config_entry_id(hass, {"entity_id": "sensor.stadtbibliothek_remseck_12345_loans"})
    assert result == "abc123"


def test_resolve_both_raises(hass):
    with pytest.raises(vol.Invalid, match="not both"):
        _resolve_config_entry_id(
            hass,
            {
                "config_entry_id": "abc123",
                "entity_id": "sensor.stadtbibliothek_remseck_12345_loans",
            },
        )


def test_resolve_neither_raises(hass):
    with pytest.raises(vol.Invalid, match="config_entry_id or entity_id"):
        _resolve_config_entry_id(hass, {})


def test_resolve_entity_not_found(hass):
    with pytest.raises(ValueError, match="not found"):
        _resolve_config_entry_id(hass, {"entity_id": "sensor.nonexistent"})


def test_resolve_entity_no_config_entry(hass):
    _entity_registry_instance.add("sensor.orphan", config_entry_id=None)
    with pytest.raises(ValueError, match="has no config entry"):
        _resolve_config_entry_id(hass, {"entity_id": "sensor.orphan"})
