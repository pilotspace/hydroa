"""The `/v1/` Envoy route must disable the default 15s response timeout (SSE truncation).

hydroa-envoy-top3 #1 — Envoy's default RouteAction.timeout is 15s and applies to the
whole response, including streaming SSE bodies (`text/event-stream`) that are NOT upgraded
connections. Long / reasoning chat completions on `/v1/chat/completions` can exceed 15s and
would be truncated mid-stream. The `/v1/` route now sets `timeout: 0s` (disable the overall
deadline) plus a per-route `idle_timeout` safety net that reaps only genuinely stalled
streams (it resets on activity, so a slow-but-progressing generation survives).

This guards all three edge configs (dev, prod, Helm) so the fix can't silently drift out of
one. The realtime WS route is intentionally NOT asserted here — Envoy does not apply the
route timeout to upgraded WebSocket connections, so it never had this problem.
"""

from __future__ import annotations

import pathlib
import re

import pytest

def _find_repo_root() -> pathlib.Path:
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / "infra/envoy/envoy.yaml").exists():
            return parent
    raise RuntimeError("could not locate repo root (infra/envoy/envoy.yaml)")


_REPO_ROOT = _find_repo_root()
_CONFIGS = [
    _REPO_ROOT / "infra/envoy/envoy.yaml",
    _REPO_ROOT / "infra/envoy/envoy-prod.yaml",
    _REPO_ROOT / "charts/ai-proxy/templates/envoy-configmap.yaml",
]


def _v1_route_block(text: str) -> str | None:
    """Return the text of the `/v1/` (non-realtime) route block, up to the next `- match:`."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        # exactly /v1/ (block `prefix: "/v1/"` or flow `{ prefix: "/v1/" }`), never
        # /v1/realtime/ — require /v1/ immediately followed by a closing quote.
        if re.search(r'prefix:\s*"/v1/"', line) and "realtime" not in line:
            block = [line]
            for nxt in lines[i + 1 :]:
                if re.search(r"^\s*(- )?match:", nxt) or re.search(r"-\s*match:\s*\{", nxt):
                    break
                block.append(nxt)
            return "\n".join(block)
    return None


@pytest.mark.parametrize("config_path", _CONFIGS, ids=lambda p: p.name)
def test_v1_route_disables_response_timeout(config_path: pathlib.Path) -> None:
    assert config_path.exists(), f"missing envoy config: {config_path}"
    block = _v1_route_block(config_path.read_text())
    assert block is not None, f'no `/v1/` route match found in {config_path.name}'
    assert re.search(r"timeout:\s*0s\b", block), (
        f"{config_path.name}: the /v1/ route must set `timeout: 0s` so long/streaming chat "
        f"completions aren't truncated at Envoy's 15s default. Route block:\n{block}"
    )
    assert re.search(r"idle_timeout:\s*\d+s\b", block), (
        f"{config_path.name}: the /v1/ route must set a per-route `idle_timeout` as the safety "
        f"net that reaps only genuinely stalled streams. Route block:\n{block}"
    )
