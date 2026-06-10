"""Domain errors for the proxy module — zero framework imports."""


class ProxyError(Exception):
    """Base for all proxy domain failures."""


class UpstreamUnavailableError(ProxyError):
    """Upstream returned 5xx, timed out, or network error occurred.

    The circuit breaker catches this to count consecutive failures.
    """


class CircuitOpenError(ProxyError):
    """Circuit breaker is open — no upstream call was made."""
