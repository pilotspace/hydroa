"""Hydroa gateway: OpenAI-compatible data plane + admin control plane.

`__version__` is the ONE place the running artifact says what it is. It reads the installed
distribution's metadata, which comes from `pyproject.toml` — so the version served on
`/openapi.json`, the version declared to the packaging tools, and the version a deploy
pinned are the same string by construction rather than by anyone remembering.

Before this (release-provenance PLAN.md §3, todo #86) there were four answers and none of
them agreed: pyproject said 0.7.0, `/openapi.json` said 0.1.0 from a literal in main.py
that had never been touched, RELEASES.md stopped at 0.12.0, and `git tag` had nine tags
with four gaps. The one a client actually sees was the least correct of the four.
"""

from __future__ import annotations

import importlib.metadata

#: Fallback for running from a source tree with no installed distribution metadata (an
#: editable-install edge). It MUST equal pyproject's version — a fallback that disagrees is
#: just a fourth version to keep in sync — which is what the §4 guard enforces.
_FALLBACK_VERSION = "0.13.0"

try:
    __version__: str = importlib.metadata.version("hydroa-gateway")
except importlib.metadata.PackageNotFoundError:  # pragma: no cover - exercised via §4
    __version__ = _FALLBACK_VERSION

__all__ = ["__version__"]
