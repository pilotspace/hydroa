"""No-DB unit tests for the TTS input-length ceiling (tts-input-guardrails, v42 t2).

The cap rejects at ``SpeechUseCase.execute`` STEP 2.5 — BEFORE governance (Step 4),
provider-select (Step 6), and the single per_character bill (Step 7). So these tests
need no DB/Redis: a real ``SpeechUseCase`` with a SPY governance + ``session=None``
suffices, because the reject path never touches the session, registry, or recorder.

Scenarios (TASK.md §2):
  - over-cap input -> ProblemError 413 ERR_PAYLOAD_INPUT_TOO_LONG, governance NOT called
  - within-cap input -> cap does not raise; flow reaches governance (Step 4)
  - cap disabled (0) -> no cap check even for a huge input
  - default knob -> 4096 (default-ON)
"""

from __future__ import annotations

from typing import Any

import pytest

from gateway.core.config import Settings
from gateway.core.errors import ProblemError
from gateway.proxy.application.audio_use_case import SpeechUseCase


class _SentinelReached(Exception):
    """Raised by the spy governance to prove the flow passed the cap to Step 4."""


class _SpyGovernance:
    """Records whether ``authorize`` ran; raises a sentinel when reached.

    Mirrors the call site ``self._governance.authorize(raw_key, model_id,
    estimated_tokens=None)`` (audio_use_case.py:333).
    """

    def __init__(self) -> None:
        self.authorize_called = False

    async def authorize(
        self, raw_key: str | None, model_id: str, estimated_tokens: int | None = None
    ) -> Any:
        self.authorize_called = True
        raise _SentinelReached


def _speech_uc(governance: _SpyGovernance, max_input_characters: int) -> SpeechUseCase:
    # session=None is safe: the cap raises before Step 5's only session use.
    return SpeechUseCase(
        governance=governance,  # type: ignore[arg-type]
        session=None,  # type: ignore[arg-type]
        max_input_characters=max_input_characters,
    )


def _body(n_chars: int) -> dict[str, Any]:
    return {"model": "tts-1", "input": "x" * n_chars, "voice": "alloy"}


async def test_over_cap_rejects_before_bill() -> None:
    gov = _SpyGovernance()
    uc = _speech_uc(gov, max_input_characters=4096)

    with pytest.raises(ProblemError) as ei:
        await uc.execute(
            raw_key="k",
            body=_body(5000),
            registry={},
            usage_recorder=None,  # type: ignore[arg-type]
        )

    assert ei.value.status == 413
    assert ei.value.code == "ERR_PAYLOAD_INPUT_TOO_LONG"
    # Reject is BEFORE governance (Step 4) → before any upstream call or bill.
    assert gov.authorize_called is False


async def test_within_cap_reaches_governance() -> None:
    gov = _SpyGovernance()
    uc = _speech_uc(gov, max_input_characters=4096)

    # The cap does NOT raise; the flow proceeds to Step 4 (spy raises the sentinel).
    with pytest.raises(_SentinelReached):
        await uc.execute(
            raw_key="k",
            body=_body(100),
            registry={},
            usage_recorder=None,  # type: ignore[arg-type]
        )

    assert gov.authorize_called is True


async def test_cap_disabled_at_zero() -> None:
    gov = _SpyGovernance()
    uc = _speech_uc(gov, max_input_characters=0)

    # 0 disables the cap entirely — even a huge input reaches governance.
    with pytest.raises(_SentinelReached):
        await uc.execute(
            raw_key="k",
            body=_body(50_000),
            registry={},
            usage_recorder=None,  # type: ignore[arg-type]
        )

    assert gov.authorize_called is True


def test_default_knob_is_4096() -> None:
    assert Settings().tts_max_input_characters == 4096
