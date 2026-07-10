"""SCIM 2.0 wire-translation layer — RFC 7644 shapes <-> the domain `User` entity.

Kept OUT of the domain/application layers (TASK.md §5 strategy item 3, the
ChatTranslator precedent): translation lives at the boundary, business rules don't know
about SCIM's wire shape.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from gateway.scim.api.errors import scim_invalid_value, scim_mutability
from gateway.tenants.domain.entities import User

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
LIST_RESPONSE_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"

# Attribute paths that are never client-mutable via PATCH/PUT (Reject: "PATCH targets an
# immutable path").
_IMMUTABLE_PATHS = {"id", "meta"}
# Privilege-shaped attribute paths (core or enterprise-extension schema) — silently
# ignored, NEVER a 400 (M3/Reject: role is never SCIM-controlled, full stop).
_PRIVILEGE_PATHS = {"role", "roles"}

_DEFAULT_COUNT = 100


def user_to_scim(user: User) -> dict[str, Any]:
    """Project a domain User onto the RFC 7644 core User schema."""
    return {
        "schemas": [USER_SCHEMA],
        "id": str(user.id),
        "userName": user.email,
        "active": user.deactivated_at is None,
        "meta": {"resourceType": "User"},
    }


def list_response(users: list[User], *, total: int, start_index: int, count: int) -> dict[str, Any]:
    return {
        "schemas": [LIST_RESPONSE_SCHEMA],
        "totalResults": total,
        "startIndex": start_index,
        "itemsPerPage": len(users),
        "Resources": [user_to_scim(u) for u in users],
    }


def empty_list_response() -> dict[str, Any]:
    """M10 — Groups probe: always an honest, spec-shaped empty collection."""
    return {
        "schemas": [LIST_RESPONSE_SCHEMA],
        "totalResults": 0,
        "startIndex": 1,
        "itemsPerPage": 0,
        "Resources": [],
    }


def service_provider_config() -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True},
        "filter": {"supported": True, "maxResults": 200},
        "bulk": {"supported": False},
        "sort": {"supported": False},
        "changePassword": {"supported": False},
        "etag": {"supported": False},
        "group": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "OAuth Bearer Token",
                "description": "Authentication scheme using a per-tenant SCIM bearer token",
            }
        ],
    }


def resource_types() -> list[dict[str, Any]]:
    """Groups intentionally omitted from the discoverable resource-type list (M9)."""
    return [
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": "User",
            "name": "User",
            "endpoint": "/Users",
            "schema": USER_SCHEMA,
        }
    ]


def schemas_list() -> list[dict[str, Any]]:
    return [
        {
            "id": USER_SCHEMA,
            "name": "User",
            "description": "SCIM core User schema",
        }
    ]


def parse_username_filter(filter_expr: str | None) -> str | None:
    """Parse `filter=userName eq "<value>"` — the ONLY supported filter expression
    (M4). Any other filter expression raises scim_invalid_value (400 invalidFilter-class
    payload)."""
    if filter_expr is None:
        return None
    expr = filter_expr.strip()
    lowered = expr.lower()
    if not lowered.startswith("username eq "):
        raise scim_invalid_value(f"Unsupported filter expression: {filter_expr!r}")
    raw_value = expr[len("userName eq ") :].strip()
    if len(raw_value) >= 2 and raw_value[0] == '"' and raw_value[-1] == '"':
        raw_value = raw_value[1:-1]
    if not raw_value:
        raise scim_invalid_value(f"Unsupported filter expression: {filter_expr!r}")
    return raw_value.lower()


def parse_create_user(body: dict[str, Any]) -> str:
    """Returns the lowercased userName, or raises scim_invalid_value (400).

    body is already guaranteed to be a JSON object by FastAPI's own `dict[str, Any]`
    parameter typing (a non-object JSON body is rejected by FastAPI's own request
    validation before this function is ever called) — no redundant isinstance guard.
    """
    schemas = body.get("schemas")
    if not isinstance(schemas, list) or not schemas:
        raise scim_invalid_value("schemas is required")
    username = body.get("userName")
    if not isinstance(username, str) or not username.strip():
        raise scim_invalid_value("userName is required")
    return username.strip().lower()


@dataclass(frozen=True, slots=True)
class ParsedPatch:
    """Resolved intent from a SCIM PatchOp body — the LAST value wins for a given field
    if the Operations array repeats a path (RFC 7644 application order)."""

    set_active: bool | None = None
    set_username: str | None = None


def _apply_field(
    key: str, value: Any, active: bool | None, username: str | None
) -> tuple[bool | None, str | None]:
    key_l = key.strip().lower()
    if key_l == "active":
        if not isinstance(value, bool):
            raise scim_invalid_value("active must be a boolean")
        return value, username
    if key_l == "username":
        if not isinstance(value, str) or not value.strip():
            raise scim_invalid_value("userName must be a non-empty string")
        return active, value.strip().lower()
    # Unknown/unsupported field paths are spec-tolerantly ignored, not an error.
    return active, username


def _normalize_path(path: str) -> str:
    # Strip an enterprise-extension URN prefix (`urn:...:User:role` -> `role`) and any
    # sub-attribute filter (`emails[type eq "work"].value` -> `emails`).
    return path.split(":")[-1].split(".")[0].split("[")[0].strip().lower()


def parse_patch_operations(body: dict[str, Any]) -> ParsedPatch:
    """Parse a SCIM PatchOp body's Operations array into resolved field intents.

    Raises scim_invalid_value (400) for a malformed body (missing/empty Operations,
    missing/unsupported op).
    Raises scim_mutability (400) for an op targeting an immutable path (id, meta).
    Silently ignores any privilege-shaped path (role/roles) — never an error (M3).

    body is already guaranteed to be a JSON object by FastAPI's own `dict[str, Any]`
    parameter typing — no redundant isinstance guard (mirrors parse_create_user).
    """
    ops = body.get("Operations")
    if not isinstance(ops, list) or not ops:
        raise scim_invalid_value("Operations must be a non-empty list")

    set_active: bool | None = None
    set_username: str | None = None

    for op_entry in ops:
        if not isinstance(op_entry, dict):
            raise scim_invalid_value("Each operation must be a JSON object")
        op = op_entry.get("op")
        if not isinstance(op, str) or op.lower() not in {"replace", "add", "remove"}:
            raise scim_invalid_value(f"Unsupported PATCH op: {op!r}")

        path = op_entry.get("path")
        if isinstance(path, str) and path:
            normalized = _normalize_path(path)
            if normalized in _IMMUTABLE_PATHS:
                raise scim_mutability(f"Path {path!r} is immutable")
            if normalized in _PRIVILEGE_PATHS:
                continue  # silently ignored — never a 400 (M3)
            set_active, set_username = _apply_field(
                normalized, op_entry.get("value"), set_active, set_username
            )
            continue

        # Pathless replace carrying a dict of {attr: value} pairs is also spec-legal.
        value = op_entry.get("value")
        if isinstance(value, dict):
            for k, v in value.items():
                normalized = _normalize_path(str(k))
                if normalized in _IMMUTABLE_PATHS:
                    raise scim_mutability(f"Path {k!r} is immutable")
                if normalized in _PRIVILEGE_PATHS:
                    continue
                set_active, set_username = _apply_field(normalized, v, set_active, set_username)
            continue

        raise scim_invalid_value("Operation missing 'path' (or a dict 'value')")

    return ParsedPatch(set_active=set_active, set_username=set_username)


def parse_put_user(body: dict[str, Any]) -> ParsedPatch:
    """PUT body is a full SCIM User resource (not a PatchOp) — extract the mutable
    top-level fields this task supports (userName, active). 'id'/'meta' present in the
    body are silently ignored (they are never client-settable regardless), and any
    privilege-shaped field (role/roles) is likewise ignored, never a 400 (M3)."""
    username = parse_create_user(body)  # reuses the same required-field validation
    active = body.get("active")
    if active is not None and not isinstance(active, bool):
        raise scim_invalid_value("active must be a boolean")
    return ParsedPatch(set_active=active, set_username=username)


def parse_token_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise scim_invalid_value(f"Invalid resource id: {raw!r}") from None
