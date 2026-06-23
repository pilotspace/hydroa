"""Offline unit tests (RED → GREEN) for scripts/live_helios_smoke.py pure helpers.

Layer (a) of the v34 helios-live-smoke test plan (TASK.md §4):
  These tests run in CI without a live stack, without network access, and without
  any API key material.  They assert the BEHAVIOR of the four safety-critical pure
  helpers that the script exposes:

    1. resolve_gemini_key()   — absent/empty HELIOS_GEMINI_KEY → SystemExit(2)
    2. _redact(line, *secrets) — replaces each secret substring with a fixed mask
    3. exit_code(results)      — any FAIL → 1; all PASS/SKIP → 0
    4. applicable(criterion, env) — C6 SKIPped (not FAILed) when cap is unset/0

Contract ref: FROZEN § 3 + TASK.md § 4 test_plan.
No network, no subprocess, no file I/O — fast and hermetic.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Import the script under test via importlib (it is NOT a package)
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[4] / "scripts" / "live_helios_smoke.py"
)

# Importing the module must NOT execute main() — the guard is tested implicitly
# by the fact that this import succeeds without network access.
_spec = importlib.util.spec_from_file_location("live_helios_smoke", _SCRIPT_PATH)
assert _spec is not None, f"Could not load spec from {_SCRIPT_PATH}"
assert _spec.loader is not None
_smoke = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_smoke)  # type: ignore[union-attr]
except SystemExit as _exc:
    # If the module raises SystemExit on import (e.g. missing key guard runs at
    # module level), the tests below will still fail with a clear ImportError-like
    # message rather than a confusing collection error.
    raise ImportError(
        f"live_helios_smoke.py raised SystemExit({_exc.code}) during import — "
        "ensure main() is guarded by `if __name__ == '__main__':` and "
        "resolve_gemini_key() is only called inside main()."
    ) from _exc


# ---------------------------------------------------------------------------
# Helper — resolve the helpers from the loaded module (fail fast if missing)
# ---------------------------------------------------------------------------

def _get(name: str) -> Any:
    """Return the named attribute from the smoke module; fail with AttributeError if absent."""
    return getattr(_smoke, name)


# ---------------------------------------------------------------------------
# Test 1: resolve_gemini_key() → SystemExit(2) when key is absent
# ---------------------------------------------------------------------------

class TestResolveGeminiKey:
    """resolve_gemini_key() must exit 2 when HELIOS_GEMINI_KEY is absent or empty."""

    def _call(self, env_val: str | None) -> Any:
        resolve_gemini_key = _get("resolve_gemini_key")
        old = os.environ.pop("HELIOS_GEMINI_KEY", None)
        try:
            if env_val is not None:
                os.environ["HELIOS_GEMINI_KEY"] = env_val
            elif "HELIOS_GEMINI_KEY" in os.environ:
                del os.environ["HELIOS_GEMINI_KEY"]
            return resolve_gemini_key()
        finally:
            # Restore
            if old is not None:
                os.environ["HELIOS_GEMINI_KEY"] = old
            elif "HELIOS_GEMINI_KEY" in os.environ:
                del os.environ["HELIOS_GEMINI_KEY"]

    def test_missing_key_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HELIOS_GEMINI_KEY unset → SystemExit(2), no provisioning or network call."""
        resolve_gemini_key = _get("resolve_gemini_key")
        monkeypatch.delenv("HELIOS_GEMINI_KEY", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            resolve_gemini_key()

        assert exc_info.value.code == 2, (
            f"Expected SystemExit(2) for missing key, got SystemExit({exc_info.value.code})"
        )

    def test_empty_key_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HELIOS_GEMINI_KEY='' (empty string) → SystemExit(2), treated as absent."""
        resolve_gemini_key = _get("resolve_gemini_key")
        monkeypatch.setenv("HELIOS_GEMINI_KEY", "")

        with pytest.raises(SystemExit) as exc_info:
            resolve_gemini_key()

        assert exc_info.value.code == 2, (
            f"Expected SystemExit(2) for empty key, got SystemExit({exc_info.value.code})"
        )

    def test_present_key_returns_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HELIOS_GEMINI_KEY='test-key' → returns the key string, no SystemExit."""
        resolve_gemini_key = _get("resolve_gemini_key")
        monkeypatch.setenv("HELIOS_GEMINI_KEY", "test-api-key-abc123")

        result = resolve_gemini_key()

        assert result == "test-api-key-abc123"


# ---------------------------------------------------------------------------
# Test 2: _redact(line, *secrets) — never emits the secret
# ---------------------------------------------------------------------------

class TestRedact:
    """_redact() must replace every occurrence of each secret with a fixed mask."""

    def test_redact_never_emits_secret(self) -> None:
        """A secret substring in a log line is fully replaced; line does not contain it."""
        _redact = _get("_redact")
        secret = "super-secret-gemini-key-xyz"
        line = f"PUT body: {{secret: {secret}}} sent to upstream"

        result = _redact(line, secret)

        assert secret not in result, (
            f"Secret STILL PRESENT in output: {result!r}"
        )
        # The mask must be a non-empty replacement (not just deletion)
        assert len(result) > 0

    def test_redact_uses_fixed_mask(self) -> None:
        """The replacement must be a consistent, non-empty mask string."""
        _redact = _get("_redact")
        secret = "my-secret"
        line = f"key={secret} logged"

        result = _redact(line, secret)

        # Must not contain secret
        assert secret not in result
        # The mask should be some fixed token (e.g. "***REDACTED***")
        # We don't hard-code the exact mask string, but it must be non-empty
        # and present in place of the secret.
        mask_present = len(result) > 0 and result != line
        assert mask_present, f"Expected mask in output but got: {result!r}"

    def test_redact_multiple_secrets(self) -> None:
        """Multiple secrets passed as positional args are ALL redacted."""
        _redact = _get("_redact")
        secret_a = "key-aaa"
        secret_b = "key-bbb"
        line = f"first={secret_a} second={secret_b}"

        result = _redact(line, secret_a, secret_b)

        assert secret_a not in result, f"secret_a still in result: {result!r}"
        assert secret_b not in result, f"secret_b still in result: {result!r}"

    def test_redact_no_secrets_is_noop(self) -> None:
        """_redact with no secrets returns the line unchanged."""
        _redact = _get("_redact")
        line = "nothing sensitive here"

        result = _redact(line)

        assert result == line

    def test_redact_empty_secret_does_not_crash(self) -> None:
        """An empty-string secret does not crash; line is returned as-is or masked."""
        _redact = _get("_redact")
        # Should not raise regardless of implementation choice
        result = _redact("some line", "")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Test 3: exit_code(results) — any FAIL → 1; all PASS/SKIP → 0
# ---------------------------------------------------------------------------

class TestExitCode:
    """exit_code() must return 1 on any FAIL; 0 when all results are PASS or SKIP."""

    def test_criteria_table_all_pass_returns_0(self) -> None:
        """All PASS results → exit_code returns 0."""
        exit_code = _get("exit_code")
        results = [
            ("C1", "PASS", "200 ok"),
            ("C2", "PASS", "stream ok"),
            ("C3", "PASS", "reasoning ok"),
        ]
        assert exit_code(results) == 0

    def test_criteria_table_fail_sets_exit_1(self) -> None:
        """Any FAIL result → exit_code returns 1 (regardless of other PASS)."""
        exit_code = _get("exit_code")
        results = [
            ("C1", "PASS", "200 ok"),
            ("C2", "FAIL", "got 500"),
            ("C3", "PASS", "ok"),
        ]
        assert exit_code(results) == 1

    def test_all_fail_returns_1(self) -> None:
        """All FAIL → exit_code returns 1."""
        exit_code = _get("exit_code")
        results = [("C1", "FAIL", "err"), ("C2", "FAIL", "err")]
        assert exit_code(results) == 1

    def test_skip_does_not_force_nonzero(self) -> None:
        """SKIP results (e.g. C6 when cap disabled) do NOT force a non-zero exit code."""
        exit_code = _get("exit_code")
        results = [
            ("C1", "PASS", "ok"),
            ("C6", "SKIP", "cap not set"),
        ]
        assert exit_code(results) == 0, (
            "A SKIP criterion must not force exit_code to 1"
        )

    def test_empty_results_returns_0(self) -> None:
        """Empty results list → 0 (vacuous pass; no FAILs)."""
        exit_code = _get("exit_code")
        assert exit_code([]) == 0

    def test_skip_and_fail_returns_1(self) -> None:
        """SKIP + FAIL together → exit_code returns 1 (the FAIL dominates)."""
        exit_code = _get("exit_code")
        results = [
            ("C6", "SKIP", "cap not set"),
            ("C1", "FAIL", "got 500"),
        ]
        assert exit_code(results) == 1


# ---------------------------------------------------------------------------
# Test 4: applicable(criterion, env) — C6 SKIPped when cap is unset/0
# ---------------------------------------------------------------------------

class TestApplicable:
    """applicable() must return SKIP for C6 when GATEWAY_MAX_CONCURRENT_REQUESTS is unset or 0."""

    def test_c6_skipped_when_cap_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """C6 is SKIPPED (not FAIL) when GATEWAY_MAX_CONCURRENT_REQUESTS is unset."""
        applicable = _get("applicable")
        monkeypatch.delenv("GATEWAY_MAX_CONCURRENT_REQUESTS", raising=False)
        env: dict[str, str] = {}  # empty env — cap absent

        result = applicable("C6", env)

        assert result == "SKIP", (
            f"Expected 'SKIP' for C6 with cap unset, got {result!r}"
        )

    def test_c6_skipped_when_cap_is_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """C6 is SKIPPED when GATEWAY_MAX_CONCURRENT_REQUESTS='0'."""
        applicable = _get("applicable")
        env = {"GATEWAY_MAX_CONCURRENT_REQUESTS": "0"}

        result = applicable("C6", env)

        assert result == "SKIP", (
            f"Expected 'SKIP' for C6 with cap=0, got {result!r}"
        )

    def test_c6_applicable_when_cap_positive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """C6 is applicable (not SKIP) when GATEWAY_MAX_CONCURRENT_REQUESTS > 0."""
        applicable = _get("applicable")
        env = {"GATEWAY_MAX_CONCURRENT_REQUESTS": "5"}

        result = applicable("C6", env)

        assert result != "SKIP", (
            f"Expected C6 to be applicable with cap=5, got {result!r}"
        )

    def test_other_criteria_always_applicable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """C1–C5 and C7 are always applicable (never SKIP)."""
        applicable = _get("applicable")
        env: dict[str, str] = {}  # cap absent — only C6 is conditional

        for criterion in ("C1", "C2", "C3", "C4", "C5", "C7"):
            result = applicable(criterion, env)
            assert result != "SKIP", (
                f"Expected {criterion} to be applicable (not SKIP), got {result!r}"
            )
