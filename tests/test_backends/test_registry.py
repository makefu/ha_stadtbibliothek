"""Tests for the backend registry in backends/__init__.py.

The registry is what lets non-Home-Assistant consumers resolve a backend by
name. Before it existed the mapping lived only in coordinator.py and
config_flow.py, both of which import homeassistant unconditionally.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from custom_components.stadtbibliothek.backends import (
    BACKENDS,
    available_backends,
    create_backend,
    get_backend_class,
)
from custom_components.stadtbibliothek.backends.base import LibraryBackend, LibraryType
from custom_components.stadtbibliothek.backends.remseck import RemseckBackend
from custom_components.stadtbibliothek.backends.stuttgart import StuttgartBackend

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_every_library_type_has_a_backend():
    assert set(BACKENDS) == {member.value for member in LibraryType}


def test_registered_classes_match_their_library_type():
    for name, cls in BACKENDS.items():
        assert issubclass(cls, LibraryBackend)
        assert cls.library_type.value == name


@pytest.mark.parametrize(
    ("name", "expected"),
    [("remseck", RemseckBackend), ("stuttgart", StuttgartBackend)],
)
def test_get_backend_class(name, expected):
    assert get_backend_class(name) is expected


def test_get_backend_class_rejects_unknown_name():
    with pytest.raises(KeyError, match="nirgendwo"):
        get_backend_class("nirgendwo")


def test_available_backends_is_sorted():
    assert available_backends() == ["remseck", "stuttgart"]


async def test_create_backend_returns_an_instance():
    backend = await create_backend("remseck")
    try:
        assert isinstance(backend, RemseckBackend)
    finally:
        await backend.close()


async def test_create_backend_passes_client_and_base_url():
    client = httpx.AsyncClient()
    try:
        backend = await create_backend("stuttgart", client=client, base_url="http://fake.local")
        assert backend._client is client
        assert backend.base_url == "http://fake.local"
    finally:
        await client.aclose()


def test_backends_import_without_homeassistant():
    """The backends package must never pull in homeassistant.

    conftest.py stubs homeassistant into sys.modules for the HA-only modules,
    so this has to run in a fresh interpreter with the import actively blocked.
    """
    code = (
        "import sys\n"
        "class Blocker:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name == 'homeassistant' or name.startswith('homeassistant.'):\n"
        "            raise ImportError('homeassistant is blocked')\n"
        "        return None\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        return self.find_module(name, path)\n"
        "sys.meta_path.insert(0, Blocker())\n"
        "from custom_components.stadtbibliothek.backends import BACKENDS, create_backend\n"
        "assert set(BACKENDS) == {'remseck', 'stuttgart'}\n"
        "assert 'homeassistant' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
