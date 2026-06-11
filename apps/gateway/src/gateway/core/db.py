"""Core database utilities: Base ORM class, session dependency, and asyncpg compat patch.

asyncpg compatibility patch (applied once at import time):
  SQLAlchemy's BIND_PARAMS regex uses a negative lookahead `(?!:)` to skip PostgreSQL
  type-cast operators (`::`) in text() literals. This intentionally avoids treating `::type`
  as a second bind parameter. However, the side-effect is that `:name::type` patterns
  (e.g. `:payload::jsonb`) are left completely un-substituted — SA does NOT convert
  `:payload` to `$N` — so asyncpg receives the SQL verbatim and PostgreSQL rejects it
  because it only understands `$N`-style positional parameters, not `:name` style.

  Fix: patch TextClause.__init__ to insert a space before `::type` casts that follow a
  bind parameter name. `:name::type` becomes `:name ::type`. The space is harmless to
  PostgreSQL (` ::jsonb` is the same as `::jsonb`), and SA's BIND_PARAMS regex now
  matches `:name` correctly (because the negative lookahead `(?!:)` passes — the next
  character is a space, not a colon). SA then substitutes it to `$N ::type` as expected.

  This patch is minimal, idempotent, and scoped to SQL text literals only. It does NOT
  change any compiled ORM queries. The only side-effect is a single extra space before
  `::type` casts that follow a bind param in raw text() SQL — invisible to PostgreSQL.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

# Matches :name immediately followed by a PostgreSQL type cast (::type or ::type[]).
# Used to insert a space so SQLAlchemy's BIND_PARAMS regex can match :name.
_PARAM_CAST_RE = re.compile(r"(:([a-zA-Z_]\w*))(::(?:[a-zA-Z_][\w\[\]]*)+)")


def _patch_textclause_for_asyncpg() -> None:
    """Patch TextClause.__init__ to normalise :name::type -> :name ::type.

    SQLAlchemy's BIND_PARAMS regex has a negative lookahead `(?!:)` that intentionally
    skips :name when the next character is `:` (to avoid treating `::type` as a param
    name). This breaks asyncpg because SA then sends the SQL un-substituted and
    PostgreSQL sees `:payload::jsonb` instead of `$1 ::jsonb`.

    Adding a space before `::type` after a bind param is semantically neutral in
    PostgreSQL and lets SA's regex correctly identify :name as a bound parameter.

    Idempotent: checks the pattern before patching; safe to import multiple times.
    """
    import sqlalchemy.sql.elements as _elements

    if getattr(_elements.TextClause.__init__, "_asyncpg_cast_patched", False):
        return  # already patched

    _orig_init: Any = _elements.TextClause.__init__

    def _patched_init(self: Any, text_str: str, **kw: Any) -> None:
        # Rewrite :name::type -> :name ::type (space before cast operator)
        text_str = _PARAM_CAST_RE.sub(r"\1 \3", text_str)
        _orig_init(self, text_str, **kw)

    _patched_init._asyncpg_cast_patched = True  # type: ignore[attr-defined]
    _elements.TextClause.__init__ = _patched_init  # type: ignore[assignment]


_patch_textclause_for_asyncpg()


class Base(DeclarativeBase):
    pass


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session
