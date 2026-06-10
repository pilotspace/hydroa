# MODEL_REGISTRY.md

Pinned AI models for reproducibility (ADD setup requirement).

## Development (this repo's ADD workflow)

| Role | Model | Pinned ID | Notes |
|------|-------|-----------|-------|
| Primary coding agent | Claude Fable 5 | `claude-fable-5` | All playbook phases |
| Subagent executor (simple tasks) | Claude Sonnet 4.6 | `claude-sonnet-4-6` | Per user orchestration rules |
| Subagent executor (complex tasks) | Claude Opus 4.8 | `claude-opus-4-8` | Fallback for complex waves |

Re-pinning a model is a logged decision: update this file in its own commit
with rationale in the body.

## Runtime (models the platform proxies)

The platform does **not** pin runtime models — tenants choose any model from
the OpenRouter catalog (`GET /api/v1/models`). The model catalog is synced,
cached, and priced from OpenRouter metadata; pricing snapshots are stored with
each usage-ledger row so historical costs stay accurate when prices change.
