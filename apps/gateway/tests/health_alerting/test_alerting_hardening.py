"""Failing-first (RED) suite for the alerting audit-remediation package.

Verified findings (audit-remediation registry, HIGH alerting):
  H1 — dispatcher._fetch_undelivered has no FOR UPDATE SKIP LOCKED → two replicas can
       double-deliver the same alert_events row. Fix: claim rows with SKIP LOCKED so a
       row already claimed (locked, uncommitted) by one dispatcher is invisible to a
       concurrent claim by another.
  H2 — dispatcher retries forever across cycles with no dead-letter. Fix: cap total
       cross-cycle delivery attempts; once exhausted, move the row to a dead-letter
       state (excluded from future claims) and surface a counter.
  H3 — httpx_webhook_sink dials the configured webhook_url with NO SSRF/egress check.
       Fix: reuse core.egress_policy (DenyPrivateAndMetadataEgressPolicy) to fail CLOSED
       on internal/link-local/metadata targets, checked fresh before every dial.

Each test is written to FAIL against the current (unfixed) code for the stated reason,
then must PASS once the corresponding fix lands — no existing test is weakened.

Database: real Postgres — GATEWAY_TEST_DATABASE_URL (see tests/_redis_env.py), run with
`-p no:xdist -x -q` against a package-private test DB per REMEDIATION-CONTEXT.md.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tests import _redis_env

_TEST_DB_URL = _redis_env.TEST_DATABASE_URL


# ---------------------------------------------------------------------------
# DB fixtures — same pattern as tests/health_alerting/test_health_alerting.py
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_engine():  # type: ignore[return]
    from gateway.core.db import Base

    engine = create_async_engine(_TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(db_engine):  # type: ignore[return]
    yield async_sessionmaker(db_engine, expire_on_commit=False)


async def _insert_alert_row(
    session_factory: Any,
    *,
    event_type: str = "soft_budget_exceeded",
    dedupe_key: str | None = None,
    payload: dict[str, Any] | None = None,
) -> uuid.UUID:
    import json

    row_id = uuid.uuid4()
    if dedupe_key is None:
        dedupe_key = f"test:{row_id}"
    if payload is None:
        payload = {"test": "true"}
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO alert_events"
                " (id, tenant_id, key_id, event_type, payload, dedupe_key)"
                " VALUES (:id, NULL, NULL, :event_type, :payload::jsonb, :dedupe_key)"
            ),
            {
                "id": str(row_id),
                "event_type": event_type,
                "payload": json.dumps(payload),
                "dedupe_key": dedupe_key,
            },
        )
        await session.commit()
    return row_id


async def _get_alert_row(session_factory: Any, row_id: uuid.UUID) -> dict[str, Any] | None:
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT * FROM alert_events WHERE id = :id"),
            {"id": str(row_id)},
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class AlwaysFailSink:
    """Always returns 500 — never delivers."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def post_json(self, url: str, payload: dict[str, object]) -> int:
        self.calls.append(url)
        return 500


class BlockingThenSucceedSink:
    """First call blocks until released (simulates an in-flight, slow delivery held
    inside a DB transaction); every call eventually returns 200.

    `started` fires the instant the sink is entered so the test can deterministically
    interleave a second concurrent claim while the first delivery is still in-flight
    (i.e. while its DB row lock — if the fix is correct — is still held).
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self._release = asyncio.Event()

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def release(self) -> None:
        self._release.set()

    async def post_json(self, url: str, payload: dict[str, object]) -> int:
        self.calls.append(url)
        self.started.set()
        await self._release.wait()
        return 200


# ============================================================================
# H1 — concurrent claims of the same undelivered alert: only ONE is delivered
# ============================================================================


async def test_h1_concurrent_claims_only_one_dispatcher_delivers(
    session_factory: Any,
) -> None:
    """RED reason: `_fetch_undelivered` has no `FOR UPDATE SKIP LOCKED` — it is a plain
    SELECT with no row-level claim, so a SECOND dispatcher's concurrent `run_once()` sees
    the SAME undelivered row while the first is still mid-delivery and dispatches it too.

    Target behavior: dispatcher A claims + starts delivering the row (holding the DB
    claim across the in-flight POST). While A is still in-flight, dispatcher B's
    `run_once()` runs concurrently against the SAME table and must find ZERO claimable
    rows (SKIP LOCKED skips the locked-but-uncommitted row) — i.e. B never calls the
    webhook sink for this row at all. Only A delivers.
    """
    from gateway.alerting.application.dispatcher import AlertDispatcher

    row_id = await _insert_alert_row(session_factory, dedupe_key=f"h1-{uuid.uuid4()}")

    sink_a = BlockingThenSucceedSink()
    sink_b = AlwaysFailSink()

    dispatcher_a = AlertDispatcher(
        session_factory=session_factory,
        webhook_sink=sink_a,
        webhook_url="https://hooks.example.com/alerts",
        retry_max=3,
    )
    dispatcher_b = AlertDispatcher(
        session_factory=session_factory,
        webhook_sink=sink_b,
        webhook_url="https://hooks.example.com/alerts",
        retry_max=3,
    )

    task_a = asyncio.create_task(dispatcher_a.run_once())
    # Wait until A has actually claimed the row and is mid-delivery (blocked in the sink).
    await asyncio.wait_for(sink_a.started.wait(), timeout=5.0)

    # B runs its own full claim pass concurrently, while A's claim (if the fix holds the
    # row's lock across delivery) is still open/uncommitted.
    await dispatcher_b.run_once()

    # B must NOT have delivered the row that A is currently (still) delivering.
    assert sink_b.call_count == 0, (
        f"a second concurrent dispatcher must claim ZERO rows while another dispatcher "
        f"is mid-delivery of the only undelivered row (SKIP LOCKED); got "
        f"{sink_b.call_count} call(s) — double-delivery"
    )

    sink_a.release()
    await asyncio.wait_for(task_a, timeout=5.0)

    assert sink_a.call_count == 1, f"dispatcher A must deliver exactly once, got {sink_a.call_count}"
    row = await _get_alert_row(session_factory, row_id)
    assert row is not None
    assert row["delivered_at"] is not None, "row must be marked delivered after A's success"


# ============================================================================
# H2 — an alert that fails past the retry cap lands in a dead-letter state
# ============================================================================


async def test_h2_alert_exhausted_past_cap_is_dead_lettered_not_retried_forever(
    session_factory: Any,
) -> None:
    """RED reason: today the dispatcher retries a permanently-failing row FOREVER —
    every `run_once()` cycle re-fetches it and re-attempts delivery with no upper bound
    and no dead-letter state, so `AlertDispatcher` currently has no
    `dead_letter_max_cycles` parameter at all (TypeError on construction).

    Target behavior: after `dead_letter_max_cycles` cycles of exhausted retries, the row
    is moved to a dead-letter state and is no longer picked up by future `run_once()`
    calls — bounded, not infinite.
    """
    from gateway.alerting.application.dispatcher import AlertDispatcher

    row_id = await _insert_alert_row(session_factory, dedupe_key=f"h2-{uuid.uuid4()}")
    sink = AlwaysFailSink()

    dispatcher = AlertDispatcher(
        session_factory=session_factory,
        webhook_sink=sink,
        webhook_url="https://hooks.example.com/alerts",
        retry_max=2,
        dead_letter_max_cycles=2,  # does not exist yet on current AlertDispatcher — TypeError
    )

    # Cycle 1 and 2: exhausts the dead-letter cap.
    await dispatcher.run_once()
    await dispatcher.run_once()
    calls_after_cap = sink.call_count

    # Cycle 3: row must now be dead-lettered — no further POST attempts for it.
    await dispatcher.run_once()

    assert sink.call_count == calls_after_cap, (
        "a dead-lettered row must not be retried again — dispatcher made "
        f"{sink.call_count - calls_after_cap} additional POST attempt(s) past the cap"
    )

    row = await _get_alert_row(session_factory, row_id)
    assert row is not None, "dead-lettered row must still exist (never dropped)"
    assert row["delivered_at"] is None, "a dead-lettered row was never actually delivered"

    dead_letter_meta = row["payload"].get("__alert_dispatch", {})
    assert dead_letter_meta.get("dead_letter") is True, (
        f"row payload must record dead-letter state after exhausting "
        f"dead_letter_max_cycles; got {row['payload']!r}"
    )

    # The failure/backlog must be observable, not just a silent DB flag.
    assert dispatcher.dead_letter_events_total >= 1, (
        "dispatcher must surface a dead-letter counter for observability; got "
        f"{dispatcher.dead_letter_events_total!r}"
    )


# ============================================================================
# H3 — a webhook target pointing at an internal/metadata address is REFUSED
#      before any HTTP dial
# ============================================================================


async def test_h3_webhook_sink_refuses_metadata_target_before_dial() -> None:
    """RED reason: `HttpxWebhookSink.post_json` dials `self._client.post(url, ...)`
    unconditionally — there is no egress/SSRF check at all, so a webhook_url pointed at
    the cloud-metadata address would be dialed directly. This test wires a transport
    that RAISES if it is ever invoked, so any actual dial attempt fails the test loudly
    (in addition to asserting the expected deny exception).

    Target behavior: the sink validates the target via `core.egress_policy` (reused, not
    forked) and refuses — fail CLOSED — before ever touching the network.
    """
    from gateway.alerting.infrastructure.httpx_webhook_sink import HttpxWebhookSink
    from gateway.core.egress_policy import EgressDeniedError

    def _must_not_dial(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            f"webhook sink must NOT dial a denied target — attempted dial to {request.url}"
        )

    transport = httpx.MockTransport(_must_not_dial)
    client = httpx.AsyncClient(transport=transport)
    sink = HttpxWebhookSink(client=client)

    with pytest.raises(EgressDeniedError):
        await sink.post_json("http://169.254.169.254/latest/meta-data/", {"a": 1})

    await client.aclose()


async def test_h3_webhook_sink_refuses_loopback_target_before_dial() -> None:
    """Same as above for a loopback target (also always denied)."""
    from gateway.alerting.infrastructure.httpx_webhook_sink import HttpxWebhookSink
    from gateway.core.egress_policy import EgressDeniedError

    def _must_not_dial(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            f"webhook sink must NOT dial a denied target — attempted dial to {request.url}"
        )

    transport = httpx.MockTransport(_must_not_dial)
    client = httpx.AsyncClient(transport=transport)
    sink = HttpxWebhookSink(client=client)

    with pytest.raises(EgressDeniedError):
        await sink.post_json("http://127.0.0.1:8080/admin", {"a": 1})

    await client.aclose()


class _FakeResolver:
    """Injectable DnsResolver (mirrors tests/edge_input_hardening/test_s3_egress_policy.py's
    `_FakeResolver`) — avoids any real network DNS lookup in this sandboxed test run."""

    def __init__(self, answers: dict[str, list[str]]) -> None:
        self._answers = answers

    async def resolve(self, host: str) -> list[str]:
        return self._answers.get(host, [])


async def test_h4_event_emitter_db_error_surfaced_not_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MED finding — `event_emitter.emit_system_event` swallows ALL DB errors with only a
    `_log.warning(...)` — a broken emitter (bad DSN, pool exhaustion, permissions) silently
    drops every system event with no counter and no ERROR-level signal.

    RED reason: today there is no `event_emitter_failures_total()` accessor at all
    (AttributeError/ImportError) and the failure is logged at WARNING, not ERROR — so
    this fails on the missing accessor.

    Target behavior (per the fire-and-forget CONTRACT, which we do NOT change — the
    caller must never see a raised exception): the failure is still swallowed, but is
    now observable — logged at ERROR with the event_type + dedupe_key, AND a
    process-local failure counter is incremented so the emitter's health can be
    monitored/alerted on rather than silently going dark.
    """
    from gateway.alerting.application import event_emitter as event_emitter_module

    class _BrokenSessionFactory:
        """Simulates a broken DB (e.g. pool exhaustion) — every session.execute raises."""

        def __call__(self) -> Any:
            return self

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def execute(self, *args: object, **kwargs: object) -> None:
            raise ConnectionError("simulated DB outage")

        async def commit(self) -> None:
            raise ConnectionError("simulated DB outage")

    before = event_emitter_module.emit_system_event_failures_total()

    with caplog.at_level("ERROR", logger="gateway.alerting.application.event_emitter"):
        # Must NOT raise — the fire-and-forget contract is preserved.
        await event_emitter_module.emit_system_event(
            _BrokenSessionFactory(),  # type: ignore[arg-type]
            event_type="circuit_breaker_open",
            dedupe_key="h4-broken-emitter-test",
            payload={"state": "open"},
        )

    after = event_emitter_module.emit_system_event_failures_total()
    assert after == before + 1, (
        f"emit_system_event must increment its failure counter on a swallowed DB error; "
        f"before={before} after={after}"
    )

    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert error_records, "a swallowed DB error must be logged at ERROR level, not WARNING"
    joined = " ".join(r.getMessage() for r in error_records)
    assert "circuit_breaker_open" in joined, "ERROR log must name the event_type"
    assert "h4-broken-emitter-test" in joined, "ERROR log must name the dedupe_key"


async def test_h3_webhook_sink_still_delivers_to_a_public_target() -> None:
    """Sanity/no-regression: a normal public https webhook target must still be dialed
    (the SSRF check must not fail-closed on legitimate targets)."""
    from gateway.alerting.infrastructure.httpx_webhook_sink import HttpxWebhookSink
    from gateway.core.egress_policy import DenyPrivateAndMetadataEgressPolicy

    def _ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(_ok)
    client = httpx.AsyncClient(transport=transport)
    # Fixed, fake DNS answer for the public hostname — a real public IP, no live lookup.
    # audit-remediation Blocker 2: the SINK now resolves+pins, so the fake resolver is
    # injected into the sink (the policy sees a literal IP and does no lookup of its own).
    resolver = _FakeResolver({"hooks.example.com": ["93.184.216.34"]})
    sink = HttpxWebhookSink(
        client=client, egress_policy=DenyPrivateAndMetadataEgressPolicy(), resolver=resolver
    )

    status = await sink.post_json("https://hooks.example.com/alerts", {"a": 1})
    assert status == 200

    await client.aclose()


async def test_h3_webhook_sink_pins_dial_to_resolved_ip_no_rebind() -> None:
    """audit-remediation Blocker 2 (HIGH) — DNS-rebind close.

    RED reason: `post_json` resolves nothing and dials the raw HOSTNAME URL, so the
    egress `check()` and httpx's own connect-time resolution are two INDEPENDENT DNS
    lookups — a rebinding resolver can answer benign to the check and metadata/RFC1918
    to the dial. Fix (mirrors mcp_connector httpx_dialer): the sink resolves ONCE, pins
    the dialed URL's host to that literal IP (so check + dial share one IP), and keeps
    the Host header + TLS SNI as the original hostname.

    This asserts the transport actually received the PINNED literal-IP URL (not the
    hostname), with Host/SNI preserved — which fails today because the sink dials the
    hostname verbatim."""
    from gateway.alerting.infrastructure.httpx_webhook_sink import HttpxWebhookSink
    from gateway.core.egress_policy import DenyPrivateAndMetadataEgressPolicy

    seen: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["header_host"] = request.headers.get("Host")
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(_capture)
    client = httpx.AsyncClient(transport=transport)
    resolver = _FakeResolver({"hooks.example.com": ["93.184.216.34"]})
    sink = HttpxWebhookSink(
        client=client, egress_policy=DenyPrivateAndMetadataEgressPolicy(), resolver=resolver
    )

    status = await sink.post_json("https://hooks.example.com/alerts", {"a": 1})
    assert status == 200
    assert seen["host"] == "93.184.216.34", (
        f"dial must target the resolved+checked literal IP, not the hostname; got {seen['host']}"
    )
    assert seen["header_host"] == "hooks.example.com", "Host header must stay the original hostname"
    assert seen["sni"] == "hooks.example.com", "TLS SNI must stay the original hostname"

    await client.aclose()


async def test_h3_webhook_sink_rebind_to_private_ip_refused_before_dial() -> None:
    """audit-remediation Blocker 2 — a hostname that RESOLVES to a private/metadata IP is
    refused before any dial, because the sink resolves-then-checks the SAME IP it will
    dial. RED today: the sink never resolves, so it can't catch this at the sink layer."""
    from gateway.alerting.infrastructure.httpx_webhook_sink import HttpxWebhookSink
    from gateway.core.egress_policy import DenyPrivateAndMetadataEgressPolicy, EgressDeniedError

    def _must_not_dial(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"must not dial a rebound private target: {request.url}")

    transport = httpx.MockTransport(_must_not_dial)
    client = httpx.AsyncClient(transport=transport)
    resolver = _FakeResolver({"evil.example.com": ["169.254.169.254"]})
    sink = HttpxWebhookSink(
        client=client, egress_policy=DenyPrivateAndMetadataEgressPolicy(), resolver=resolver
    )

    with pytest.raises(EgressDeniedError):
        await sink.post_json("https://evil.example.com/hook", {"a": 1})

    await client.aclose()


async def test_h3_webhook_sink_rejects_redirect() -> None:
    """audit-remediation Blocker 2 — any 3xx is rejected (fail CLOSED), never followed to a
    Location the sink never egress-checked. RED today: the sink returns the 3xx status
    without raising, so a redirect to an internal Location is silently honored by any
    follow-redirects client."""
    from gateway.alerting.infrastructure.httpx_webhook_sink import HttpxWebhookSink
    from gateway.core.egress_policy import DenyPrivateAndMetadataEgressPolicy

    def _redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    transport = httpx.MockTransport(_redirect)
    client = httpx.AsyncClient(transport=transport)
    resolver = _FakeResolver({"hooks.example.com": ["93.184.216.34"]})
    sink = HttpxWebhookSink(
        client=client, egress_policy=DenyPrivateAndMetadataEgressPolicy(), resolver=resolver
    )

    with pytest.raises(Exception):  # noqa: B017 -- redirect must fail-closed (any raise)
        await sink.post_json("https://hooks.example.com/alerts", {"a": 1})

    await client.aclose()
