"""Fail-closed redaction for post-call output masking (audit Issue 2).

Single source of truth shared by the masking evaluator (infrastructure) and the proxy
use-cases (application). When the post-call PII masker cannot be trusted to have scrubbed
the model output — the evaluator's own masking raised, or a call-site caught an evaluator
exception — the output text is WITHHELD rather than returned raw. The request still
succeeds (the caller returns a well-formed 200 body): fail-CLOSED but NON-BLOCKING.

`redact_response_body` REBUILDS each choice from scratch (a redacted assistant message),
so every output-text vector is dropped, not just `message.content`: tool_call arguments,
list-shaped content, refusals, etc. all disappear. id / model / object / usage are
preserved so downstream billing/metering/capture keep working, and the caller's body is
never mutated in place.
"""

from __future__ import annotations

from typing import Any

# The withheld-content placeholder shown to the client on a masker failure.
POST_MASK_REDACTION = "[content withheld: output safety check unavailable]"


def redact_response_body(body: Any) -> Any:
    """Return a copy of a chat-completion `body` with every choice reduced to a redacted
    assistant message, preserving id/model/object/usage. Never mutates `body`.

    A body with no `choices` list carries no masker-scoped assistant text, so it is
    returned unchanged (the masker would have found nothing to scrub there either).
    """
    if not isinstance(body, dict):
        return {
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": POST_MASK_REDACTION}}
            ]
        }
    choices = body.get("choices")
    if not isinstance(choices, list):
        return body
    new_choices: list[Any] = []
    for i, choice in enumerate(choices):
        idx = choice.get("index", i) if isinstance(choice, dict) else i
        redacted: dict[str, Any] = {
            "index": idx,
            "message": {"role": "assistant", "content": POST_MASK_REDACTION},
        }
        # Preserve finish_reason if the original carried one — downstream consumers may
        # key on it; the assistant text itself is the only thing being withheld.
        if isinstance(choice, dict) and "finish_reason" in choice:
            redacted["finish_reason"] = choice["finish_reason"]
        new_choices.append(redacted)
    new_body = dict(body)
    new_body["choices"] = new_choices
    return new_body
