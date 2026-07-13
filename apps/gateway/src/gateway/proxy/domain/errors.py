"""Domain errors for the proxy module — zero framework imports."""


class ProxyError(Exception):
    """Base for all proxy domain failures."""


class UpstreamUnavailableError(ProxyError):
    """Upstream returned 5xx, timed out, or network error occurred.

    The circuit breaker catches this to count consecutive failures.
    """


class UpstreamRateLimitedError(UpstreamUnavailableError):
    """Upstream returned HTTP 429 after exhausting all retry attempts.

    IS-A UpstreamUnavailableError so the circuit breaker still counts it and
    the fallback router still falls over on it (no behavior change for those paths).
    Carries the parsed Retry-After value (seconds) when the upstream supplied one.
    """

    def __init__(self, message: str = "", *, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class CircuitOpenError(ProxyError):
    """Circuit breaker is open — no upstream call was made."""


class AllDeploymentsSaturatedError(Exception):
    """Every candidate of an alias group has exceeded its per-deployment RPM/TPM limit.

    Raised by FallbackModelRouter.complete() when the limit filter removes ALL
    candidates of the group. The use case maps this to 429 ERR_RATE_LIMITED.

    Additive extension @ deployment-limits TASK.md §3.
    """

    def __init__(self, alias: str) -> None:
        self.alias = alias
        super().__init__(f"All deployments for alias '{alias}' are saturated")


class AllCandidatesOutOfRegionError(Exception):
    """Every candidate of an alias group is outside the tenant's pinned residency region.

    Raised by FallbackModelRouter.complete() / .stream() / .stream_resilient() when the
    residency pre-loop filter (Tier 2) removes ALL candidates of the group. Distinct from
    AllDeploymentsSaturatedError so the use case maps THIS to 403
    ERR_RESIDENCY_NO_ELIGIBLE_REGION — never the generic 429/502 path (residency-policy
    TASK.md §3 M4). A deliberate fail-closed refusal, never a silent out-of-region reroute.

    `region` carries the tenant's pin (when known at raise time) so the catching use
    case can render the precise error message without a second DB round-trip; may be
    None when the raiser did not have it in scope (the use case falls back to an
    empty-string placeholder in that case — never a crash).
    """

    def __init__(self, alias: str, region: str | None = None) -> None:
        self.alias = alias
        self.region = region
        super().__init__(
            f"All candidates for alias '{alias}' are outside the tenant's pinned region"
        )
