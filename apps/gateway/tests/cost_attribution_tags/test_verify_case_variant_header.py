import json

from tests.cost_attribution_tags.test_cost_attribution_tags import (
    signup_and_login, create_key, auth_key, chat_payload,
    _flush, _tags_for, COMPLETIONS,
)

async def test_verify_mixed_case_header_name_still_parsed(client, app, db_session, redis_client, wired_recorder, fake_upstream, active_model):
    jwt, _t = await signup_and_login(client, tenant_name="CaseVariant", email="casevariant@cat.io")
    key_body = await create_key(client, jwt, name="case-variant-key")
    key = key_body["key"]
    resp = await client.post(
        COMPLETIONS, json=chat_payload(active_model),
        headers={**auth_key(key), "x-GATEWAY-tags": json.dumps({"team": "mixed"})},
    )
    assert resp.status_code == 200, resp.text
    await _flush(redis_client, app)
    rows = await _tags_for(db_session, model_id=active_model)
    assert rows == [{"team": "mixed"}], f"mixed-case header name must still be parsed, got {rows}"
