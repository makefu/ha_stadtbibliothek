"""Registry of available library backends.

Importing this package must never pull in Home Assistant: the backends are
shared with the standalone CLI tools and with external consumers, and only
``coordinator.py`` / ``sensor.py`` / ``config_flow.py`` may depend on HA.
"""

from __future__ import annotations

import asyncio

import httpx

from .base import (
    AccountInfo,
    AuthenticationError,
    FeeItem,
    LibraryBackend,
    LibraryType,
    LoanItem,
    RenewalError,
)
from .remseck import RemseckBackend
from .stuttgart import StuttgartBackend

BACKENDS: dict[str, type[LibraryBackend]] = {
    LibraryType.REMSECK.value: RemseckBackend,
    LibraryType.STUTTGART.value: StuttgartBackend,
}


def get_backend_class(library_type: str) -> type[LibraryBackend]:
    """Return the backend class registered for ``library_type``."""
    try:
        return BACKENDS[library_type]
    except KeyError:
        raise KeyError(f"Unknown library type {library_type!r}; known: {available_backends()}") from None


def available_backends() -> list[str]:
    """Return the registered library type names, sorted."""
    return sorted(BACKENDS)


async def create_backend(
    library_type: str,
    *,
    client: httpx.AsyncClient | None = None,
    base_url: str | None = None,
) -> LibraryBackend:
    """Instantiate a backend without blocking the event loop.

    Constructing an ``httpx.AsyncClient`` loads the certifi CA bundle
    synchronously, so build the backend in a worker thread unless the caller
    supplies a client of its own.
    """
    backend_cls = get_backend_class(library_type)
    if client is not None:
        return backend_cls(client, base_url=base_url)
    return await asyncio.to_thread(backend_cls, base_url=base_url)


__all__ = [
    "BACKENDS",
    "AccountInfo",
    "AuthenticationError",
    "FeeItem",
    "LibraryBackend",
    "LibraryType",
    "LoanItem",
    "RemseckBackend",
    "RenewalError",
    "StuttgartBackend",
    "available_backends",
    "create_backend",
    "get_backend_class",
]
