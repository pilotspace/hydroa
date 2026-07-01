"""Suite-local fixtures for catalog-sync-trigger (TASK.md §4).

POST /admin/catalog/sync is a thin owner/admin wrapper over the existing SyncCatalogUseCase.
A FakeCatalogSource is injected via `app.state.catalog_source` (the composition root reads it
through get_sync_use_case) so no real HTTP ever leaves the process — the proven pattern from the
catalog/model-catalog suite. Owner/admin tokens via the canonical signup+login; a member token is
minted directly (direct users insert + token_service.issue) — the model_mgmt pattern.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

SYNC = "/admin/catalog/sync"
PASSWORD = "correct horse battery"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass(frozen=True, slots=True)
class FakeCatalogModel:
    """Mirrors the CatalogModel value object the domain port returns."""

    id: str
    name: str
    context_length: int | None
    prompt_usd_per_token: float
    completion_usd_per_token: float
    # openrouter-embeddings-routing TASK.md §3: CatalogModel.modality is now read
    # by _upsert_model; mirror its "chat" default so this suite is unaffected.
    modality: str = "chat"


class FakeCatalogSource:
    """In-memory stub implementing the CatalogSource Protocol port."""

    def __init__(
        self,
        models: list[FakeCatalogModel] | None = None,
        *,
        raise_unavailable: bool = False,
    ) -> None:
        self.models = models or []
        self.raise_unavailable = raise_unavailable

    async def list_models(self) -> AsyncIterator[FakeCatalogModel]:
        if self.raise_unavailable:
            from gateway.catalog.domain.errors import CatalogSourceUnavailableError

            raise CatalogSourceUnavailableError("fake upstream unavailable")
        for model in self.models:
            yield model

    async def list_embedding_models(self) -> AsyncIterator[FakeCatalogModel]:
        # openrouter-embeddings-routing TASK.md §3: CatalogSource grew this sibling
        # method. This suite never exercises embeddings — always succeeds with zero
        # rows (SyncCatalogUseCase.execute() calls it unconditionally now).
        return
        yield  # pragma: no cover — makes this an async generator


OPUS = FakeCatalogModel(
    id="anthropic/claude-opus-4",
    name="Claude Opus 4",
    context_length=200_000,
    prompt_usd_per_token=15e-6,
    completion_usd_per_token=75e-6,
)
SONNET = FakeCatalogModel(
    id="anthropic/claude-sonnet-4",
    name="Claude Sonnet 4",
    context_length=200_000,
    prompt_usd_per_token=3e-6,
    completion_usd_per_token=15e-6,
)


def install_fake_source(app: Any, fake: FakeCatalogSource) -> None:
    """Override the catalog_source stored on app.state (the composition root wires it)."""
    app.state.catalog_source = fake


async def signup_and_login(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> tuple[str, str]:
    """Signup + login; return (owner_jwt, tenant_id)."""
    sr = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": PASSWORD},
    )
    assert sr.status_code == 201, sr.text
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post("/admin/auth/login", json={"email": email, "password": PASSWORD})
    assert lr.status_code == 200, lr.text
    return lr.json()["access_token"], tenant_id


async def mint_role_token(
    app: Any,
    session: AsyncSession,
    *,
    tenant_id: str,
    role: Role,
    email: str,
) -> str:
    """Insert a same-tenant user with `role` and mint its JWT (model_mgmt pattern)."""
    user_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'placeholder-not-a-real-hash', :role)"
        ),
        {"id": user_id, "tid": tenant_id, "email": email, "role": str(role)},
    )
    await session.commit()
    token, _ = app.state.token_service.issue(
        user_id=uuid.UUID(user_id),
        tenant_id=uuid.UUID(tenant_id),
        role=role,
        email=email,
    )
    return str(token)


async def active_model_count(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(text("SELECT COUNT(*) FROM models WHERE active = true"))
        ).scalar_one()
    )
