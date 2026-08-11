import json

from tests.cost_attribution_tags.test_cost_attribution_tags import (
    signup_and_login,
    create_key,
    auth_key,
    chat_payload,
    _flush,
    _tags_for,
    COMPLETIONS,
)


async def test_verify_duplicate_header_instances(
    client, app, db_session, redis_client, wired_recorder, fake_upstream, active_model
):
    """Adversarial probe: what happens when X-Gateway-Tags is sent TWICE (raw
    httpx doesn't support duplicate header names via a dict, so use raw
    httpx.Headers with a list of tuples to force it)."""
    import httpx

    jwt, _t = await signup_and_login(client, tenant_name="DupHeader", email="dupheader@cat.io")
    key_body = await create_key(client, jwt, name="dup-header-key")
    key = key_body["key"]

    headers = httpx.Headers(
        [
            ("Authorization", f"Bearer {key}"),
            ("X-Gateway-Tags", json.dumps({"team": "first"})),
            ("X-Gateway-Tags", json.dumps({"team": "second"})),
            ("content-type", "application/json"),
        ]
    )
    resp = await client.post(
        COMPLETIONS,
        content=json.dumps(chat_payload(active_model)),
        headers=headers,
    )
    print("STATUS:", resp.status_code, resp.text[:300])
    if resp.status_code == 200:
        await _flush(redis_client, app, expect=1)
        rows = await _tags_for(db_session, model_id=active_model)
        print("STORED TAGS:", rows)
