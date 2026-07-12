"""Adversarial verify-time probe (NOT part of the frozen §4 suite): does a cache
HIT bill with the REQUESTING call's own tags, or does it silently reuse/drop the
tags of the ORIGINAL (cache-populating) request?

§2/§4 has no scenario/test exercising tags + the response-cache exact-hit path at
all — this is a genuine coverage gap the static code read (use_cases.py: `_tags`
is parsed at line ~1892, BEFORE `_try_cache_lookup` is called at ~2110, and
threaded into `_fire_record_cached(..., tags=tags)`) strongly suggests is correct,
but nothing exercises it at runtime. This probe confirms it live.

Left uncommitted per verify instructions.
"""

import json

from tests.cost_attribution_tags.test_cost_attribution_tags import (
    signup_and_login,
    auth_key,
    chat_payload,
    _flush,
    COMPLETIONS,
    ADMIN_KEYS,
    auth_jwt,
)


async def test_verify_cache_hit_bills_with_requesting_calls_own_tags(
    client, app, db_session, redis_client, wired_recorder, fake_upstream, active_model
):
    from sqlalchemy import text

    jwt, _t = await signup_and_login(
        client, tenant_name="CacheTagVerify", email="cachetagverify@cat.io"
    )
    key_resp = await client.post(
        ADMIN_KEYS,
        json={"name": "cache-tag-key", "cache_enabled": True},
        headers=auth_jwt(jwt),
    )
    assert key_resp.status_code == 201, key_resp.text
    key_body = key_resp.json()
    key = key_body["key"]

    payload = chat_payload(active_model)

    # Request 1: MISS, no tags at all — populates the exact-match cache.
    resp1 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key))
    assert resp1.status_code == 200, resp1.text
    assert resp1.headers.get("x-cache") == "miss", resp1.headers.get("x-cache")

    # Request 2: SAME payload (byte-identical cache key) but THIS request carries
    # its OWN tags — a real client tagging a request that happens to hit cache.
    resp2 = await client.post(
        COMPLETIONS,
        json=payload,
        headers={**auth_key(key), "X-Gateway-Tags": json.dumps({"team": "cache-hit-caller"})},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.headers.get("x-cache") == "hit", resp2.headers.get("x-cache")

    await _flush(redis_client, app)

    rows = (
        await db_session.execute(
            text(
                "SELECT cost_usd, tags FROM usage_records"
                " WHERE model_id = :m ORDER BY created_at ASC"
            ),
            {"m": active_model},
        )
    ).fetchall()
    assert len(rows) == 2, f"expected 2 usage rows (miss + hit), got {len(rows)}"

    first_cost, first_tags = rows[0]
    second_cost, second_tags = rows[1]

    assert dict(first_tags) == {}, f"first (miss, untagged) row should have tags={{}}, got {first_tags!r}"
    assert float(second_cost) == 0, f"cache-hit row should be cost_usd=0, got {second_cost}"
    assert dict(second_tags) == {"team": "cache-hit-caller"}, (
        f"cache-HIT row must carry the REQUESTING call's own tags, not the "
        f"original (untagged) populating request's tags — got {second_tags!r}"
    )
