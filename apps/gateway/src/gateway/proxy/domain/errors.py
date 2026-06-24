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
