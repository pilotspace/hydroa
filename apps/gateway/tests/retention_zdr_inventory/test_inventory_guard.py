"""RED structural guard for `zdr-retention-inventory-extension` (§CHECKS row 2).

RED at the current tree, and WHY: `_sweep_zdr_purge_pass`
(usage/application/retention_sweep.py L992+) enumerates NINE table names by hand and
declares no inventory tuple at all. A Base.metadata walk therefore finds five
payload-bearing tenant tables that are neither purged nor exempted —
vector_store_chunks, eval_cases, eval_case_results, finetune_jobs, finetune_job_events
— so this guard fails naming exactly those five (M3 / E2).

Build turns it green by adding ONE `ZDR_PURGE_TABLES` tuple in
tenants/application/retention_policy.py that the sweeper CONSUMES (M2), containing the
existing nine plus the five victims. Nothing here should be edited to reach green.

DO NOT weaken these tests to pass — that is Build's job.
"""

from __future__ import annotations

from tests.retention_zdr_inventory._inventory import (
    EXPECTED_PRE_FIX_VICTIMS,
    INVENTORY_TUPLE_POINTER,
    consumed_inventory,
    payload_shaped_census,
)

# ---------------------------------------------------------------------------
# The NAMED exemption list (A12 / R:SILENT_EXEMPTION).
#
# Every row is a 3-tuple (table, reason, task-citation). A bare table name is ITSELF a
# violation: the shape check below fails any row missing either half, so a future
# engineer cannot silence this guard by appending a name. Nor can the five victims be
# exempted — the anti-gaming self-check further down refuses that outright.
#
# These are the tables the population rule flags (tenant_id + Text/JSONB/Vector) that
# are deliberately NOT ZDR payload: metadata containers whose payload CHILD is purged,
# tenant configuration/credentials whose deletion would break the tenant, and financial
# or compliance evidence retained by design.
# ---------------------------------------------------------------------------
_CITE = "zdr-retention-inventory-extension TASK.md"

NAMED_EXEMPTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "audit_events",
        "compliance evidence retained by design — no field and no code path may ever "
        "touch it (retention_policy.py module docstring, R4)",
        f"{_CITE} A11",
    ),
    (
        "usage_records",
        "billing/metering evidence; swept by its OWN operator window "
        "(EXISTING_SWEPT_TABLES) and a ZDR tenant must still be billed "
        "(tenant-retention-zdr M8), so an unconditional purge would destroy revenue data",
        f"{_CITE} A11",
    ),
    (
        "alert_events",
        "operator-scoped alert signal swept by its own retention window "
        "(EXISTING_SWEPT_TABLES); carries event_type/dedupe_key, not tenant payload",
        f"{_CITE} A11",
    ),
    (
        "vector_stores",
        "metadata CONTAINER — name/status/embedding_model survive the purge; its "
        "payload child vector_store_chunks is purged explicitly",
        f"{_CITE} A2",
    ),
    (
        "vector_store_files",
        "attach-record CONTAINER — status/last_error survive; its payload child "
        "vector_store_chunks is purged explicitly, never via ON DELETE CASCADE",
        f"{_CITE} A2, R:CASCADE_RELIANCE",
    ),
    (
        "eval_sets",
        "metadata CONTAINER — name/description survive; its payload child eval_cases "
        "is purged explicitly",
        f"{_CITE} A2",
    ),
    (
        "eval_runs",
        "metadata CONTAINER — model/status survive; its payload child "
        "eval_case_results is purged explicitly",
        f"{_CITE} A2",
    ),
    (
        "batch_jobs",
        "job CONTAINER carrying status/error only; its payload child batch_job_items "
        "is already in the purge inventory",
        f"{_CITE} A2",
    ),
    (
        "api_keys",
        "tenant CONFIGURATION (model allowlist, guardrail policy, tier) — purging it "
        "would revoke the tenant's own credentials, not protect its payload",
        f"{_CITE} A11",
    ),
    (
        "tenant_provider_keys",
        "BYOK credential configuration (Fernet ciphertext at rest) — purging it would "
        "destroy the tenant's provider access; ZDR governs payload, not credentials",
        f"{_CITE} A11",
    ),
    (
        "saml_provider_configs",
        "SSO configuration (IdP certificate) — purging it would lock every member of "
        "the tenant out of the console",
        f"{_CITE} A11",
    ),
    (
        "scim_tokens",
        "directory-provisioning credential configuration — purging it would silently "
        "break SCIM sync",
        f"{_CITE} A11",
    ),
    (
        "teams",
        "organisational structure configuration (team name), not tenant AI payload",
        f"{_CITE} A11",
    ),
    (
        "tenant_domain_claims",
        "domain-ownership proof (verification token/status) — purging it would "
        "un-verify the tenant's domain and break auto-join",
        f"{_CITE} A11",
    ),
    (
        "domain_invite_links",
        "onboarding/identity configuration (token hash, status), not payload",
        f"{_CITE} A11",
    ),
    (
        "tenant_model_overrides",
        "routing configuration — a model_id reference, no request or response payload",
        f"{_CITE} A11",
    ),
    (
        "tenant_model_presets",
        "routing/alias configuration (preset name -> target model), no payload",
        f"{_CITE} A11",
    ),
    (
        "tenant_rate_card_entries",
        "pricing configuration — a model_id reference plus rates, no payload",
        f"{_CITE} A11",
    ),
    (
        "tenant_region_multiplier_overrides",
        "pricing/residency configuration (region code), no payload",
        f"{_CITE} A11",
    ),
    (
        "tenant_report_schedules",
        "compliance-report schedule configuration (cadence, delivery target); the "
        "generated payload lives in compliance_report_runs, which IS purged",
        f"{_CITE} A11",
    ),
    (
        "models",
        "model CATALOG rows — capability/pricing metadata, tenant_id scopes a "
        "tenant-visible catalog entry, never request or response payload",
        f"{_CITE} A11",
    ),
    (
        "invoices",
        "financial record (status, currency) — billing evidence, retained by design",
        f"{_CITE} A11",
    ),
    (
        "credit_ledger",
        "financial ledger — immutable billing evidence, retained by design",
        f"{_CITE} A11",
    ),
    (
        "checkout_sessions",
        "commerce record (payment intent, idempotency key) — financial evidence, not "
        "tenant AI payload",
        f"{_CITE} A11",
    ),
    (
        "seat_membership_events",
        "entitlement/billing audit trail (event_type only), retained by design",
        f"{_CITE} A11",
    ),
    (
        "guardrail_verdict_events",
        "policy-decision telemetry (guardrail name, action, policy source) — carries "
        "the verdict, never the inspected payload",
        f"{_CITE} A11",
    ),
)


def _exempt_names() -> frozenset[str]:
    return frozenset(row[0] for row in NAMED_EXEMPTIONS)


def test_inventory_guard_red_names_victims() -> None:
    """covers: M3, A5, A11, A12, A17, E2, R:SECOND_INVENTORY, R:SILENT_EXEMPTION

    Walk Base.metadata; any table with `tenant_id` AND a Text/JSONB/Vector column must
    sit either in the inventory THE SWEEPER CONSUMES or on the named exemption list.

    RED today: the sweeper consumes no declared tuple at all, so the walk names
    vector_store_chunks, eval_cases, eval_case_results, finetune_jobs and
    finetune_job_events as unpurged, unexempted payload at rest for a ZDR tenant.
    """
    census = payload_shaped_census()
    inventory, inventory_label = consumed_inventory()
    consumed = frozenset(inventory)
    exempt = _exempt_names()

    # --- R:SILENT_EXEMPTION: every exemption is a (table, reason, citation) row -----
    for index, row in enumerate(NAMED_EXEMPTIONS):
        assert isinstance(row, tuple) and len(row) == 3, (
            f"R:SILENT_EXEMPTION — NAMED_EXEMPTIONS[{index}] is not a "
            f"(table, reason, task-citation) 3-tuple: {row!r}. A bare table name is a "
            "violation; say WHY and cite the decision."
        )
        table, reason, citation = row
        assert table and reason and citation, (
            f"R:SILENT_EXEMPTION — NAMED_EXEMPTIONS[{index}] has an empty field: {row!r}"
        )
        assert len(reason.strip()) >= 20, (
            f"R:SILENT_EXEMPTION — exemption for {table!r} has no real reason: {reason!r}"
        )
        assert _CITE in citation, (
            f"R:SILENT_EXEMPTION — exemption for {table!r} cites no task decision: "
            f"{citation!r} (expected a {_CITE} A-line reference)"
        )
    assert len(exempt) == len(NAMED_EXEMPTIONS), (
        "duplicate table in NAMED_EXEMPTIONS — two rows for one table means one of the "
        "two reasons is dead text nobody will notice going stale"
    )

    # --- Anti-vacuity self-checks: a guard that walks nothing proves nothing --------
    assert len(census) >= 35, (
        f"anti-vacuity: the payload-shaped census collapsed to {len(census)} tables "
        "(expected 40 on this tree). Either the ORM side-effect import stopped "
        "registering tables or the population rule silently stopped matching — either "
        "way this guard is no longer guarding anything."
    )
    sentinel = "memories"
    assert sentinel in census, (
        f"anti-vacuity: the known payload table {sentinel!r} fell out of the census — "
        "the tenant_id + Text/JSONB/Vector population rule is broken"
    )
    assert sentinel in consumed, (
        f"anti-vacuity: the known-purged table {sentinel!r} is not in the inventory "
        f"the sweeper consumes ({inventory_label}) — the guard is reading the wrong object"
    )
    unqualified = sorted(EXPECTED_PRE_FIX_VICTIMS - set(census))
    assert not unqualified, (
        f"anti-vacuity: {unqualified} no longer carry tenant_id + a Text/JSONB/Vector "
        "column, so the population rule would not flag them even unpurged — the "
        "deep-review P0 premise changed and this task needs re-grounding"
    )
    stale = sorted(exempt - set(census))
    assert not stale, (
        f"stale exemption rows for tables that are no longer payload-shaped (or no "
        f"longer exist): {stale}. Delete the row rather than leaving a decision "
        "attached to nothing."
    )
    overlap = sorted(exempt & consumed)
    assert not overlap, (
        f"a table is BOTH purged and exempted: {overlap}. One of the two statements is "
        "a lie; resolve it rather than letting the guard pick."
    )

    # --- Anti-gaming: the five victims may never be exempted into silence -----------
    gamed = sorted(EXPECTED_PRE_FIX_VICTIMS & exempt)
    assert not gamed, (
        f"the deep-review P0 victims were exempted instead of purged: {gamed}. These "
        "five ARE tenant payload at rest (chunk text + embeddings, eval request bodies, "
        "model response text, finetune hyperparameters/error text, event payloads) — "
        "exempting them re-opens the exact defect this task closes."
    )

    # --- M3 / E2 / A17: the verdict --------------------------------------------------
    missing = sorted(set(census) - consumed - exempt)
    detail = "\n".join(f"    - {t}  (payload columns: {', '.join(census[t])})" for t in missing)
    assert not missing, (
        f"{len(missing)} payload-bearing tenant table(s) are neither in the ZDR purge "
        f"inventory the sweeper consumes ({inventory_label}) nor on the named exemption "
        f"list — a ZDR tenant's payload survives every sweep tick:\n{detail}\n"
        f"  Fix by adding each table to: {INVENTORY_TUPLE_POINTER}\n"
        "  OR, if the table is deliberately kept, add a "
        "(table, reason, task-citation) row to NAMED_EXEMPTIONS in this file — a bare "
        "table name is itself a violation (R:SILENT_EXEMPTION)."
    )
