"""Catalog domain errors — no framework imports."""

from __future__ import annotations


class CatalogError(Exception):
    """Base for all catalog domain failures."""


class CatalogSourceUnavailableError(CatalogError):
    """Raised when the upstream catalog source cannot be reached."""


class CatalogEmptyError(CatalogError):
    """Raised when no active models exist in the catalog."""


# ---------------------------------------------------------------------------
# Input-modality validation errors (model-input-capabilities TASK.md §3)
# ---------------------------------------------------------------------------


class InvalidInputModalityError(CatalogError):
    """Raised when an unknown input-modality token is encountered.

    ``code`` is the machine-readable error key surfaced in API problem+json
    responses by the guard task (unsupported-input-guard).  Mirror the existing
    error pattern in this file.
    """

    code: str = "invalid_input_modality"

    def __init__(self, message: str = "Unknown input modality token") -> None:
        super().__init__(message)


class EmptyInputModalitiesError(CatalogError):
    """Raised when an empty (or all-blank) input-modalities set is provided.

    A model must accept at least one input type — the empty set is invalid at
    every entry point (normalize, seed, admin edit).
    """

    code: str = "empty_input_modalities"

    def __init__(self, message: str = "input_modalities must be non-empty") -> None:
        super().__init__(message)
