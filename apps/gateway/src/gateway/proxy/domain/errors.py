"""Domain errors for the proxy module — zero framework imports."""


class ProxyError(Exception):
    """Base for all proxy domain failures."""


class UpstreamUnavailableError(ProxyError):
    """Upstream returned 5xx, timed out, or network error occurred.

    The circuit breaker catches this to count consecutive failures.
    """


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
