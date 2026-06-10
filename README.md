# AI Proxy Platform

Multi-tenant AI proxy over [OpenRouter](https://openrouter.ai) with per-tenant
cost tracking and an admin dashboard. Built with the
[ADD methodology](https://github.com/pilotspace/ADD).

**Goal:** tenant setup → login → use any LLM model → cost tracked.

## Orientation

| Read | For |
|------|-----|
| `.add/PROJECT.md` | Domain, architecture, key decisions — read first |
| `SETUP-REVIEW.md` | Confidence-tagged setup decisions (human-locked 2026-06-10) |
| `.add/CONVENTIONS.md` | Layout, style, TDD and failure-design rules |
| `.add/GLOSSARY.md` | Canonical names used in contracts and code |
| `.claude/skills/add/phases/` | The ADD phase prompts (setup → observe) |

State-tracked workflow: `python3 .add/tooling/add.py status` shows the resume
point; in Claude Code, run `/add`.

## Develop

```sh
make install   # uv sync
make ci        # lint + typecheck + allowlist gate + tests
```

Stack: Python 3.12 / FastAPI gateway (`apps/gateway`), Envoy edge
(`infra/envoy`), Next.js dashboard (`apps/dashboard`), PostgreSQL + Redis.
