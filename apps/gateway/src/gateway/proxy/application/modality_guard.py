"""Pure guard helpers for the capability-aware input-modality guard.

unsupported-input-guard TASK.md §3 — three composable, side-effect-free helpers:

  required_input_modalities_for_chat(messages) -> frozenset[str]
      Derive the set of input modalities a chat request actually requires, by
      inspecting each message's content field.

  resolve_allowed(model_id, lookup, model_groups) -> frozenset[str] | None
      Return the effective allowed modality set for model_id.  Plain ids go to
      lookup directly; alias ids (present in model_groups) yield the UNION of
      their candidates' allowed sets (allow if ANY candidate can serve).
      Returns None when data is absent → caller FAILS OPEN.

  enforce(required, allowed, model_id) -> None
      Raise UNSUPPORTED_INPUT_MODALITY when ``required - allowed - {"video"}``
      is non-empty.  allowed=None → return silently (fail-open).  video modality
      is deliberately deferred from v55 and is never enforced here.

All three helpers are pure (no I/O) except resolve_allowed which awaits lookup.get().
Imported into CompletionUseCase._check_input_modalities and TranscriptionUseCase.execute.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gateway.proxy.domain.ports import InputModalityLookup

_log = logging.getLogger(__name__)


def required_input_modalities_for_chat(
    messages: list[dict[str, Any]],
) -> frozenset[str]:
    """Derive the required input modality set from a chat messages list.

    Rules (§3 CONTRACT):
      - str content → {"text"}
      - list content:
          {"text"} if any part has type=="text"
          union {"image"} if any part has type=="image_url"
          video_url parts are IGNORED (video deferred from v55)
          unknown/malformed parts are IGNORED (ERR_UNSUPPORTED_CONTENT_PART owns them)
      - Non-list, non-str content → treated as text (no validation here)
      - The function never raises; unknown shapes degrade to the empty contribution.
    """
    required: set[str] = set()
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            # Bare string → text modality required
            required.add("text")
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type == "text":
                    required.add("text")
                elif part_type == "image_url":
                    required.add("image")
                # video_url → deliberately skipped (deferred from v55)
                # unknown types → ignored (adapter handles ERR_UNSUPPORTED_CONTENT_PART)
    return frozenset(required)


async def resolve_allowed(
    model_id: str,
    lookup: InputModalityLookup,
    model_groups: dict[str, list[str]] | None,
) -> frozenset[str] | None:
    """Return the effective allowed-modality set for model_id.

    Resolution logic (§3 CONTRACT):
      - If model_id is a key in model_groups (alias group):
          Union the allowed sets of every candidate that has data.
          Return None iff EVERY candidate has no active catalog row.
      - Otherwise (plain model id):
          Return lookup.get(model_id) directly.
          None iff no active catalog row exists.

    None in both cases signals the caller to FAIL OPEN (no rejection on absent data).
    Union resolution: allow if SOME candidate supports the required type; reject only
    when NO candidate does (Tin-confirmed § ASSUMPTIONS ✓ RESOLVED).
    """
    if model_groups is not None and model_id in model_groups:
        candidates = model_groups[model_id]
        union: set[str] = set()
        any_data: bool = False
        for candidate_id in candidates:
            candidate_allowed = await lookup.get(candidate_id)
            if candidate_allowed is not None:
                any_data = True
                union.update(candidate_allowed)
        # None only when every candidate had no data — caller fails open.
        return frozenset(union) if any_data else None

    # Plain model id — direct lookup.
    return await lookup.get(model_id)


def enforce(
    required: frozenset[str],
    allowed: frozenset[str] | None,
    model_id: str,
) -> None:
    """Enforce that required modalities are a subset of allowed (minus the deferred video set).

    Raises UNSUPPORTED_INPUT_MODALITY (ProblemError 400) when a required modality
    is not in the allowed set (after excluding "video" which is deferred from v55).

    allowed=None → silently return (fail-open: no rejection on absent capability data).
    missing = required - allowed - {"video"} — video is NEVER enforced in v55.

    audit-remediation package C1 (MED modality_guard fail-open): fail-open here is a
    DELIBERATE, Tin-approved, FROZEN @ v1 contract (unsupported-input-guard TASK.md
    §1/§2/§3 — "an under-seeded catalog never 4xx's real traffic"), not an oversight;
    flipping it to a 400 would break that frozen contract and its own scenario tests.
    What WAS missing is observability: a genuinely un-checkable non-text/video
    modality (e.g. "image") silently passing through an unknown-capability model was
    completely invisible. Now logged at WARNING — gated on a non-text/video modality
    being present so plain-text fail-open (the near-universal, low-risk case) stays
    silent and production log volume doesn't explode on every un-seeded model.
    """
    if allowed is None:
        # Fail-open: unknown / no capability data → never 4xx (frozen contract).
        risky = required - frozenset({"text", "video"})
        if risky:
            _log.warning(
                "modality_guard_fail_open_unknown_capability",
                extra={"model_id": model_id, "required_modalities": sorted(required)},
            )
        return

    missing = required - allowed - frozenset({"video"})
    if not missing:
        return

    # Pick the first offending modality deterministically (sorted for stability).
    input_type = sorted(missing)[0]

    from gateway.core.error_catalog import UNSUPPORTED_INPUT_MODALITY

    raise UNSUPPORTED_INPUT_MODALITY.exc(
        detail=(
            f"{input_type} input not supported by model '{model_id}' (supports: {sorted(allowed)})"
        ),
        input_type=input_type,
    )
