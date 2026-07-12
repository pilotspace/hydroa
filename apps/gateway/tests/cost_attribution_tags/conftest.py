"""Directory conftest — re-exports the main suite's wired fixtures so the
verify-time probes (test_verify_*.py) can use them. pytest forbids
`pytest_plugins` in non-root test modules, so this is the sanctioned seam."""

from tests.cost_attribution_tags.test_cost_attribution_tags import (  # noqa: F401
    active_model,
    fake_upstream,
    redis_client,
    wired_recorder,
)
