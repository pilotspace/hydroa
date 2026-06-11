"""Suite-local conftest for rename_hydroa.

This suite is pure file/app introspection — no DB, no Redis, no network.
No shared fixtures from the root conftest are used or overridden here.

Per CONVENTIONS.md: autouse fixtures that are suite-specific belong in the owning
suite's conftest, never the repo root — this conftest exists to satisfy that rule
and to document that this suite intentionally uses no infrastructure fixtures.
"""
