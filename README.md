# AI Proxy Platform

Multi-tenant AI proxy over [OpenRouter](https://openrouter.ai) with per-tenant
cost tracking and an admin dashboard. Built with the
[ADD methodology](https://github.com/pilotspace/ADD).

**Goal:** tenant setup → login → use any LLM model → cost tracked.

## Orientation

| Read | For |
|------|-----|
| `PROJECT.md` | Domain, architecture, key decisions — read first |
| `SETUP-REVIEW.md` | Confidence-tagged setup decisions awaiting human lock |
| `CONVENTIONS.md` | Layout, style, TDD and failure-design rules |
| `GLOSSARY.md` | Canonical names used in contracts and code |
| `playbook/` | The six ADD phase prompts (specify → observe) |

## Develop

```sh
make install   # uv sync
make ci        # lint + typecheck + allowlist gate + tests
```

Stack: Python 3.12 / FastAPI gateway (`apps/gateway`), Envoy edge
(`infra/envoy`), Next.js dashboard (`apps/dashboard`), PostgreSQL + Redis.
