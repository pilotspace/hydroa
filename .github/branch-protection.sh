#!/usr/bin/env bash
# Apply the required-checks branch protection on `main`.
#
# The merge rule this encodes is documented in CONTRIBUTING.md: a change lands on
# `main` only when `gateway` and `dashboard` are green, and admins are NOT exempt.
#
# Needs repository-admin rights on pilotspace/hydroa. Idempotent — re-run it any time
# to re-assert the protection (e.g. after someone relaxes it).
#
# PREREQUISITE — A PAID PLAN. Verified 2026-07-30: this script currently FAILS with
#   403 — "Upgrade to GitHub Pro or make this repository public to enable this feature"
# because `pilotspace` is on the GitHub **free** plan and `hydroa` is **private**, and
# the branch-protection API is a paid-plan feature for private repositories.
#
# This is a SECOND, INDEPENDENT blocker from the Actions-runner billing fault (jobs
# ending at 0 steps). Fixing the runner billing does NOT make this script work; the org
# must move to Team/Pro, or the repo must become public. Both blockers have to clear
# before the merge gate in CONTRIBUTING.md is mechanically enforced rather than
# aspirational. Until then the script is the recorded intent, not the active control.
#
# `kind-e2e` is deliberately absent from the required set: it is path-filtered, so it
# reports no status at all on a PR touching none of its paths, and a required
# path-filtered check deadlocks such a PR permanently. See CONTRIBUTING.md.
#
# Verify afterwards:
#   gh api repos/pilotspace/hydroa/branches/main/protection \
#     --jq '{checks: .required_status_checks.contexts, admins: .enforce_admins.enabled}'

set -euo pipefail

REPO="${REPO:-pilotspace/hydroa}"
BRANCH="${BRANCH:-main}"

echo "applying branch protection to ${REPO}@${BRANCH} ..."

gh api -X PUT "repos/${REPO}/branches/${BRANCH}/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["gateway", "dashboard"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON

echo
echo "applied. current state:"
gh api "repos/${REPO}/branches/${BRANCH}/protection" \
  --jq '{checks: .required_status_checks.contexts, strict: .required_status_checks.strict, admins: .enforce_admins.enabled}'
