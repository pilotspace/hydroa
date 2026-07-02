"""RED suite for cache-alias-billing (B6 revenue leak — TASK.md §4).

A non-streaming chat request through a model-group ALIAS that HITS the response
cache (exact / semantic / vector layer) currently records usage keyed on the ALIAS
string — ``CompletionUseCase.complete`` fires ``_fire_record_cached(..., model=model_id,
...)`` at all three hit sites (:1143 exact, :1199 semantic, :1257 vector), where
``model_id`` is the alias itself (e.g. "fast"), never the served catalog candidate. An
alias has no ``pricing_snapshots`` row → cost resolves to $0, silently, on every
repeat/cached request.

The MISS path already bills the served candidate (frozen F7, :1463) — this suite
extends that invariant to the three cache-HIT paths, per TASK.md §3 CONTRACT:

  WRITE (status==200 store): stored = {**response_body, STAMP: served_model_id}
  READ  (status==200 hit):   served = cached.pop(STAMP, None) or cached.get("model") or model_id
                              bill on `served`, never `model_id` (the alias)
  INVARIANT: the client-returned body never contains STAMP (popped on every hit path)

RED at write time (no fix exists yet):
  - tests 1/2/3/5: the "billed == CAND_A, != ALIAS" assertions fail because the
    unfixed hit sites unconditionally bill `model=model_id` (the alias) regardless
    of what is stored in the cache.
  - tests 1/2/3: the "no STAMP in the returned client body" assertions ALSO fail,
    because nothing pops the stamp today — a pre-stamped cached entry leaks the
    reserved key straight through to the client (both are the SAME right reason:
    the stamp read/pop mechanic does not exist yet).
  - test 4: the "stored value carries the stamp" assertion fails because the MISS
    write path does not stamp the cached value yet (billing itself is unaffected —
    F7 already bills served_model_id on MISS, so that half of test 4 passes today).

Run ONLY this suite (fakes — no DB/Redis):
  cd apps/gateway && uv run pytest tests/cache_alias_billing/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

from gateway.proxy.infrastructure.response_cache import build_cache_key, build_semantic_cache_key

from .conftest import (
    ALIAS,
    CAND_A,
    STAMP,
    TENANT_A,
    FakeCompletionUpstream,
    FakeResponseCache,
    FakeVectorCache,
    MarkerSpyRecorder,
    billed_records,
    copy,
    legacy_body,
    make_payload,
    make_router,
    make_use_case,
    run_complete,
    settle,
    stamped_body,
)

# ---------------------------------------------------------------------------
# Scenario 1 — exact cache HIT through an alias bills the served candidate
# ---------------------------------------------------------------------------


async def test_exact_hit_bills_served_not_alias() -> None:
    """RED: warm the exact cache for alias->CAND_A; a repeat alias request HITS it.

    The billed usage record must key on CAND_A (the served candidate), never the
    alias "fast". The client-returned body must not leak the internal STAMP key.
    """
    body = make_payload(ALIAS)
    exact_key = build_cache_key(str(TENANT_A), body)
    cached = stamped_body(served=CAND_A)  # model field deliberately != CAND_A (":free" variant)
    rc = FakeResponseCache(exact={exact_key: cached})

    up = FakeCompletionUpstream()
    rec = MarkerSpyRecorder()
    uc = make_use_case(response_cache=rc)

    status, body_out, x_cache = await run_complete(uc, up, rec, body, router=make_router())
    await settle()

    assert x_cache == "hit", f"expected exact cache HIT, got x_cache={x_cache!r}"
    assert status == 200
    assert up.complete_calls == [], "upstream must NEVER be called on a cache hit"

    billed = billed_records(rec)
    assert billed, "expected exactly one billed (status=200, cached=true) usage record"
    assert billed[-1]["model"] == CAND_A, (
        f"billed on {billed[-1]['model']!r}; must be the SERVED candidate {CAND_A!r}, "
        f"never the alias {ALIAS!r} (alias has no pricing snapshot -> $0 leak on every "
        f"cache-hit request through this alias)"
    )
    assert billed[-1]["model"] != ALIAS

    assert STAMP not in body_out, (
        f"the response body returned to the CLIENT must never contain the reserved "
        f"stamp key {STAMP!r} — it must be popped on every hit path before return"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — semantic cache HIT through an alias bills the served candidate
# ---------------------------------------------------------------------------


async def test_semantic_hit_bills_served_not_alias() -> None:
    """RED: a normalization-equivalent alias request hits the semantic layer.

    body_warm and body_req normalize to the SAME semantic key (whitespace +
    trailing punctuation differ only) but hash to DIFFERENT exact keys, so the
    request genuinely traverses exact-MISS -> semantic-pointer-dereference ->
    semantic-HIT (not the exact-hit shortcut exercised by scenario 1).
    """
    body_warm = make_payload(ALIAS)
    body_warm["messages"] = [{"role": "user", "content": "Hello   World."}]
    body_req = make_payload(ALIAS)
    body_req["messages"] = [{"role": "user", "content": "hello world"}]

    exact_key = build_cache_key(str(TENANT_A), body_warm)
    sem_key = build_semantic_cache_key(str(TENANT_A), body_warm)
    assert sem_key == build_semantic_cache_key(str(TENANT_A), body_req), (
        "harness bug: body_warm/body_req must normalize to the SAME semantic key"
    )
    assert exact_key != build_cache_key(str(TENANT_A), body_req), (
        "harness bug: body_warm/body_req must hash to DIFFERENT exact keys "
        "(else this would exercise the exact-hit path, not semantic)"
    )

    cached = stamped_body(served=CAND_A)
    rc = FakeResponseCache(exact={exact_key: cached}, pointers={sem_key: exact_key})

    up = FakeCompletionUpstream()
    rec = MarkerSpyRecorder()
    uc = make_use_case(response_cache=rc, semantic_cache_enabled=True)

    status, body_out, x_cache = await run_complete(uc, up, rec, body_req, router=make_router())
    await settle()

    assert x_cache == "semantic_hit", f"expected a SEMANTIC cache HIT, got x_cache={x_cache!r}"
    assert status == 200
    assert up.complete_calls == [], "upstream must NEVER be called on a cache hit"

    billed = billed_records(rec)
    assert billed, "expected exactly one billed (status=200, cached=true) usage record"
    assert billed[-1]["model"] == CAND_A, (
        f"billed on {billed[-1]['model']!r}; must be the SERVED candidate {CAND_A!r}, "
        f"never the alias {ALIAS!r} (alias has no pricing snapshot -> $0 leak)"
    )
    assert billed[-1]["model"] != ALIAS

    assert STAMP not in body_out, (
        f"the response body returned to the CLIENT must never contain the reserved "
        f"stamp key {STAMP!r} — it must be popped on every hit path before return"
    )


# ---------------------------------------------------------------------------
# Scenario 3 — vector (embedding-similarity) cache HIT bills the served candidate
# ---------------------------------------------------------------------------


async def test_vector_hit_bills_served_not_alias() -> None:
    """RED: a near-duplicate alias request hits the vector (embedding-similarity) layer.

    The real RedisVectorCache dereferences a pointer to the SAME exact-cache body a
    fixed write would have stamped; the fake vector cache stands in for that
    dereference by returning an independent (deep-copied) stamped body directly —
    exact + semantic both cold/disabled so the request genuinely falls through to
    the vector layer.
    """
    body = make_payload(ALIAS)
    vec_hit_body = copy.deepcopy(stamped_body(served=CAND_A, resp_id="resp-vector"))

    rc = FakeResponseCache()  # cold: exact + semantic both miss
    vec = FakeVectorCache(hit_body=vec_hit_body)

    up = FakeCompletionUpstream()
    rec = MarkerSpyRecorder()
    uc = make_use_case(response_cache=rc, vector_cache=vec)

    status, body_out, x_cache = await run_complete(uc, up, rec, body, router=make_router())
    await settle()

    assert x_cache == "vector_hit", f"expected a VECTOR cache HIT, got x_cache={x_cache!r}"
    assert status == 200
    assert up.complete_calls == [], "upstream must NEVER be called on a cache hit"
    assert len(vec.lookup_calls) == 1

    billed = billed_records(rec)
    assert billed, "expected exactly one billed (status=200, cached=true) usage record"
    assert billed[-1]["model"] == CAND_A, (
        f"billed on {billed[-1]['model']!r}; must be the SERVED candidate {CAND_A!r}, "
        f"never the alias {ALIAS!r} (alias has no pricing snapshot -> $0 leak)"
    )
    assert billed[-1]["model"] != ALIAS

    assert STAMP not in body_out, (
        f"the response body returned to the CLIENT must never contain the reserved "
        f"stamp key {STAMP!r} — it must be popped on every hit path before return"
    )


# ---------------------------------------------------------------------------
# Scenario 4 — cache MISS is unchanged (F7) and leaks no stamp; the STORED value
#              carries the stamp (write-side, RED)
# ---------------------------------------------------------------------------


async def test_miss_unchanged_and_no_stamp_leak() -> None:
    """RED (write-side only): a cold-cache alias MISS still routes+bills CAND_A (F7,
    already true today) and the client body is unstamped (already true today — no
    write path exists to add it). The NEW assertion is that the value actually
    STORED in the cache carries STAMP == CAND_A — false today (write path does not
    stamp yet), so this is the one RED assertion in this test.
    """
    body = make_payload(ALIAS)
    ck = build_cache_key(str(TENANT_A), body)

    upstream_body = {
        "id": "resp-miss",
        "model": "cand-a:free",
        "choices": [{"message": {"role": "assistant", "content": "fresh"}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
    }
    rc = FakeResponseCache()  # cold
    up = FakeCompletionUpstream(body=upstream_body, status=200)
    rec = MarkerSpyRecorder()
    uc = make_use_case(response_cache=rc)

    status, body_out, x_cache = await run_complete(uc, up, rec, body, router=make_router())
    await settle()

    assert x_cache == "miss", f"expected a cold-cache MISS, got x_cache={x_cache!r}"
    assert status == 200
    assert up.complete_calls == [CAND_A], (
        f"expected the alias to route to {CAND_A!r}, got {up.complete_calls!r}"
    )

    # F7 (unchanged, already GREEN today): the MISS path bills the served candidate.
    billed = billed_records(rec)
    assert billed, "expected exactly one billed (status=200) usage record"
    assert billed[-1]["model"] == CAND_A, (
        f"MISS-path billing regressed: billed on {billed[-1]['model']!r}, expected the "
        f"served candidate {CAND_A!r} (F7 must stay unchanged by this fix)"
    )

    # Client body must never carry the internal stamp (already GREEN today: nothing writes it).
    assert STAMP not in body_out, (
        f"the response body returned to the CLIENT must never contain the reserved "
        f"stamp key {STAMP!r}"
    )

    # RED: the value actually persisted to the cache must carry the served-model stamp
    # so a FUTURE cache hit can bill correctly. Fails today — the write path does not
    # stamp the stored value yet.
    stored = rc.store.get(ck)
    assert stored is not None, "expected the MISS path to store a value at the exact-cache key"
    assert stored.get(STAMP) == CAND_A, (
        f"the value STORED in the cache must carry {STAMP!r} == {CAND_A!r} so a future "
        f"cache HIT can bill the served candidate instead of the alias; got "
        f"{stored.get(STAMP)!r} (write-side stamp not yet implemented)"
    )


# ---------------------------------------------------------------------------
# Scenario 5 — legacy cached entry without a stamp falls back safely to cached["model"]
# ---------------------------------------------------------------------------


async def test_legacy_entry_without_stamp_falls_back_to_cached_model() -> None:
    """RED: a warmed exact entry stored PRE-FIX (model=CAND_A, no STAMP key at all).

    The billed usage record must still key on CAND_A — read from cached["model"],
    the legacy fallback — never the alias, even though no stamp is present.
    """
    body = make_payload(ALIAS)
    exact_key = build_cache_key(str(TENANT_A), body)
    cached = legacy_body(served=CAND_A)
    assert STAMP not in cached, "harness bug: this fixture must simulate a PRE-FIX entry"
    rc = FakeResponseCache(exact={exact_key: cached})

    up = FakeCompletionUpstream()
    rec = MarkerSpyRecorder()
    uc = make_use_case(response_cache=rc)

    status, _body_out, x_cache = await run_complete(uc, up, rec, body, router=make_router())
    await settle()

    assert x_cache == "hit", f"expected exact cache HIT, got x_cache={x_cache!r}"
    assert status == 200
    assert up.complete_calls == [], "upstream must NEVER be called on a cache hit"

    billed = billed_records(rec)
    assert billed, "expected exactly one billed (status=200, cached=true) usage record"
    assert billed[-1]["model"] == CAND_A, (
        f"billed on {billed[-1]['model']!r}; a legacy (pre-fix, no-stamp) cache entry must "
        f"fall back to cached['model']={CAND_A!r}, never the alias {ALIAS!r} "
        f"(alias has no pricing snapshot -> $0 leak)"
    )
    assert billed[-1]["model"] != ALIAS
