"""Curated NS-hostname -> registrar deep-link map (registrar-hint TASK.md §3 — FROZEN @ v1).

Pure, zero-IO (backend-architect discipline — mirrors domain_validation.py's own zero-
framework-import shape) — a hand-maintained static table + one pure lookup function.
`deep_link_url` values are ALWAYS byte-identical literals copied straight from this map
(M9); NEVER string-built from a resolved nameserver hostname — that is what closes the
SSRF/open-redirect pivot through the DNS answer (§0 GROUND).

Curation note (Build note, TASK.md §3 freeze): a provider whose exact DNS-record-editor
page needs an account/zone-specific ID this endpoint cannot supply is deliberately
EXCLUDED rather than shipped with a deep link that would 404 for most callers — those
nameservers simply fall through `infer_registrar` to the SAME graceful `fallback: true`
shape as an unrecognized/failed lookup (M6). Every entry below instead links to a
provider's DNS-management LANDING page (a login/dashboard the authenticated owner can
navigate onward from) — never a per-zone record editor.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegistrarHint:
    name: str
    deep_link_url: str


# Ordered (NS-hostname needle -> RegistrarHint). Matched by `needle in nameserver.lower()`
# — deliberately a substring check rather than a strict `str.endswith` suffix, so a
# numbered/regional NS host (e.g. AWS Route 53's "ns-123.awsdns-45.co.uk", where the
# numeric infix and TLD both vary) still matches on its one stable identifying substring.
# The ORDER of this tuple is irrelevant to M11's determinism — that comes from
# `infer_registrar` iterating the RESOLVER's own nameserver list in order, not this map.
REGISTRAR_SUFFIX_MAP: tuple[tuple[str, RegistrarHint], ...] = (
    ("cloudflare.com", RegistrarHint("Cloudflare", "https://dash.cloudflare.com/login")),
    ("domaincontrol.com", RegistrarHint("GoDaddy", "https://dcc.godaddy.com/manage/dns")),
    (
        "registrar-servers.com",
        RegistrarHint("Namecheap", "https://ap.www.namecheap.com/domains/list"),
    ),
    (
        "awsdns",
        RegistrarHint(
            "AWS Route 53", "https://console.aws.amazon.com/route53/v2/hostedzones"
        ),
    ),
    (
        "digitalocean.com",
        RegistrarHint("DigitalOcean", "https://cloud.digitalocean.com/networking/domains"),
    ),
    (
        "azure-dns.",
        RegistrarHint(
            "Azure DNS",
            "https://portal.azure.com/#view/HubsExtension/BrowseResource/"
            "resourceType/Microsoft.Network%2FdnsZones",
        ),
    ),
    (
        "googledomains.com",
        RegistrarHint("Google Domains", "https://domains.google.com/registrar"),
    ),
    ("namesilo.com", RegistrarHint("Namesilo", "https://www.namesilo.com/account_domains.php")),
)


def infer_registrar(nameservers: list[str]) -> RegistrarHint | None:
    """Return the FIRST curated-map match, scanning `nameservers` in the CALLER-supplied
    (resolver-returned) order (M11) — deterministic, no re-sorting. A pure, zero-IO scan;
    `None` when no nameserver matches any curated entry (M6)."""
    for ns in nameservers:
        needle = ns.strip().lower()
        for suffix, hint in REGISTRAR_SUFFIX_MAP:
            if suffix in needle:
                return hint
    return None
