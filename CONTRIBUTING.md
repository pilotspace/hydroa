# Contributing

How work lands on `main`. For *how features are designed and built*, read `CLAUDE.md`
and `.add/PROJECT.md` — this file covers only the merge gate.

## The merge rule

**A change lands on `main` only when its required checks are green.**

Required checks on `main`:

| Check | Job | What it proves |
| --- | --- | --- |
| `gateway` | `.github/workflows/ci.yml` | lint · typecheck · dependency allow-lists · full gateway suite · Alembic migration parity |
| `dashboard` | `.github/workflows/ci.yml` | vitest suite · `next build` |

The rule above is the standard this repo holds itself to. Be precise about what is
currently **enforced by GitHub** versus what is enforced by convention, because the two
are not the same today:

> **NOT YET MECHANICALLY ENFORCED.** Branch protection is **not** configured on `main`,
> and cannot be until the plan changes. `pilotspace` is on the GitHub **free** plan and
> `hydroa` is **private**; the protection API is a paid-plan feature for private repos,
> so `.github/branch-protection.sh` currently fails with
> `403 — Upgrade to GitHub Pro or make this repository public`. Until that is resolved,
> every statement below about required checks and `enforce_admins` describes the
> **intended** gate, and the actual gate is reviewer discipline. Owner: Tin Dang.

Once the plan supports it, apply (and re-apply) the protection with
`.github/branch-protection.sh` — it sets `enforce_admins: true`, so repository admins
are **not** exempt. Verify with the `gh api` call in that script's header; treat the
gate as in force only when that call reports the required contexts back.

`kind-e2e` (`.github/workflows/kind-e2e.yml`) is deliberately **not** required. It is
path-filtered, so it reports no status at all on a PR that touches none of its paths —
a required path-filtered check deadlocks such a PR at "Expected — waiting for status"
forever. It is also a 45-minute job. Run it on demand (`workflow_dispatch`) and before
a release cut; it is not a per-PR gate.

### `--admin` is not a sanctioned path

`gh pr merge --admin` bypasses required checks. It is **not** an accepted way to land
a change, and "the suite passed on my machine" is **not** a substitute for a green
check. The distinction matters beyond hygiene: SOC 2 CC8.1 change management asks you
to evidence that tests ran **on the merged artifact**, and a local run cannot evidence
that. During the period when CI was blocked at the account level, merges did happen by
admin override on locally-run evidence — that gap is exactly what this rule closes, and
it is not a precedent.

If CI itself is broken, fix CI. That is a change like any other, and it goes through
the same gate.

### If you genuinely must override

Only a repository admin can, and only with all three of:

1. a written reason in the PR (what is broken, why waiting is worse than merging),
2. a follow-up issue that restores the green,
3. a note in the PR that the merge was an override — so the audit trail records it as
   an exception rather than as a normal merge.

An override with no logged reason is indistinguishable from a bypassed control. Do not
create one.

## Keeping CI honest

CI is a manifest and it drifts. Two guards run inside the gateway suite
(`apps/gateway/tests/migrations/test_ci_workflow_parity.py`):

- the Postgres image in `ci.yml` must equal the one in
  `infra/docker-compose.dev.yml` and `charts/ai-proxy/values.yaml` — CI must test
  against the Postgres the project actually deploys (this is how the pgvector bump in
  #89 silently left CI behind);
- every gate named by the `Makefile` `ci:` target must be invoked by the workflow — so
  a gate cannot be quietly dropped, and a red check cannot be "fixed" by deleting the
  step that produced it.

Making a check pass by removing a step or excluding a suite is a regression, not a fix.

## Before you open a PR

```bash
make ci          # lint · typecheck · allowlist · allowlist-node · full gateway suite
```

`make ci` is the same gate the `gateway` job runs. It needs the local Postgres and
Redis from `infra/docker-compose.dev.yml`; on a saturated machine run the gateway suite
in chunks rather than dropping to fewer tests.
