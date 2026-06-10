"""Catalog domain errors — no framework imports."""

from __future__ import annotations


class CatalogError(Exception):
    """Base for all catalog domain failures."""


class CatalogSourceUnavailableError(CatalogError):
    """Raised when the upstream catalog source cannot be reached."""


class CatalogEmptyError(CatalogError):
    """Raised when no active models exist in the catalog."""
