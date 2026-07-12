"""Adversarial verify-time probe: insert_usage_row's old-event-safe tags default
(flusher.py ~line 126-131) has an `except (json.JSONDecodeError, ValueError):
tags_dict = {}` branch that coverage shows as NEVER executed by the frozen §4
suite (that suite only tests a MISSING "tags" field, not a PRESENT-but-corrupted
one). Confirm the code degrades safely rather than raising/poisoning the batch.

Left uncommitted per verify instructions.
"""

import uuid

from sqlalchemy import text


async def test_verify_corrupted_tags_field_degrades_to_empty_not_raise(app, db_session):
    from gateway.usage.application.flusher import insert_usage_row

    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name) ON CONFLICT DO NOTHING"),
        {"id": tenant_id, "name": "CorruptedTagsTenant"},
    )
    await db_session.commit()

    corrupted_fields: dict[str, str] = {
        "id": str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "key_id": str(uuid.uuid4()),
        "model_id": "corrupted-tags-model",
        "prompt_tokens": "10",
        "completion_tokens": "5",
        "cost_usd": "0.001",
        "status": "200",
        "raw": "{}",
        "tags": "{not valid json at all",  # present but corrupted
    }
    record_id = uuid.uuid4()

    # Must NOT raise — tags is best-effort attribution metadata (R9).
    await insert_usage_row(app.state.sessionmaker, record_id=record_id, fields=corrupted_fields)

    row = (
        await db_session.execute(
            text("SELECT tags FROM usage_records WHERE id = :id"), {"id": record_id}
        )
    ).fetchone()
    assert row is not None, "row must still be inserted despite corrupted tags field"
    assert dict(row[0]) == {}, f"corrupted tags JSON must degrade to {{}}, got {row[0]!r}"
