"""Azure OpenAI config resolution + deployment URL routing (azure-auth-routing §3 FROZEN @ v1).

A pure, IO-free seam consumed by the later Azure adapters (azure-chat / azure-embeddings /
azure-aad-auth). Azure OpenAI differs from every other provider in two ways this module owns:

  1. Deployment-based routing — the client's ``model`` maps to an Azure *deployment name*
     via a configured map (identity by default), and the deployment is a PATH segment.
  2. The required ``api-version`` query parameter on every operation.

The wire URL shape is:
    {endpoint}/openai/deployments/{deployment}/{op}?api-version={api_version}

Security: ``api_key`` is a SECRET — ``field(repr=False)`` keeps it out of repr/str, and it
NEVER appears in ``build_url`` output, any log field, metric label, span attribute, or
exception message (mirrors AwsCredentials.secret_access_key).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

#: GA-stable default Azure OpenAI API version when GATEWAY_AZURE_API_VERSION is unset.
DEFAULT_API_VERSION = "2024-10-21"


@dataclass(frozen=True)
class AzureConfig:
    """Immutable Azure OpenAI routing config.

    ``api_key`` is excluded from ``repr``/``str`` so it never leaks into logs or
    exception output. Field ordering mirrors AwsCredentials: the repr-hidden secret
    is required (no default), followed by the remaining required fields.
    """

    api_key: str = field(repr=False)
    endpoint: str
    api_version: str
    deployment_map: Mapping[str, str]

    def resolve_deployment(self, model: str) -> str:
        """Map a client model name to its Azure deployment (identity when unmapped)."""
        return self.deployment_map.get(model, model)

    def build_url(self, deployment: str, op: str) -> str:
        """Build the Azure OpenAI operation URL.

        ``op`` is the OpenAI-relative operation segment ("chat/completions", "embeddings").
        ``deployment`` is URL path-quoted. A trailing slash on the endpoint is stripped
        (idempotent). Raises ValueError("AZURE_DEPLOYMENT_REQUIRED") for an empty deployment
        so a malformed URL is never emitted.
        """
        if not deployment or not deployment.strip():
            raise ValueError("AZURE_DEPLOYMENT_REQUIRED")
        quoted = quote(deployment, safe="")
        base = self.endpoint.rstrip("/")
        return f"{base}/openai/deployments/{quoted}/{op}?api-version={self.api_version}"


def resolve_azure_config(settings: object) -> AzureConfig | None:
    """Return an AzureConfig iff both ``azure_api_key`` and ``azure_endpoint`` are truthy.

    Returns None otherwise (opt-in; partial config silently disables Azure — byte-identical
    to pre-Azure behavior, mirroring resolve_aws_credentials). ``api_version`` falls back to
    DEFAULT_API_VERSION; ``deployment_map`` defaults to {} (identity routing).
    """
    api_key: str = getattr(settings, "azure_api_key", "") or ""
    endpoint: str = getattr(settings, "azure_endpoint", "") or ""
    if not (api_key and endpoint):
        return None

    api_version: str = getattr(settings, "azure_api_version", "") or DEFAULT_API_VERSION
    raw_map = getattr(settings, "azure_deployment_map", None) or {}
    deployment_map: dict[str, str] = dict(raw_map)

    return AzureConfig(
        api_key=api_key,
        endpoint=endpoint,
        api_version=api_version,
        deployment_map=deployment_map,
    )


__all__ = ["DEFAULT_API_VERSION", "AzureConfig", "resolve_azure_config"]
