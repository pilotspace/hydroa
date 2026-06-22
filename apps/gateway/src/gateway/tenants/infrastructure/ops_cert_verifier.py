"""Operator client-cert verifier for the ops-auth surface (operator-wide-reconciliation §3).

The platform operator authenticates with mTLS: Envoy terminates TLS, validates the client
cert against the ops CA, and forwards the verified identity to the app via the
``x-forwarded-client-cert`` (XFCC) header. This verifier matches the forwarded leaf-cert
SHA-256 fingerprint (the XFCC ``Hash`` field) against a configured allow-list.

SECURITY (TASK.md §3 frozen):
  - DEFAULT-OFF / fail-closed: an EMPTY allow-list authorizes no one (``identify`` → None).
  - The app trusts XFCC ONLY because Envoy is configured to STRIP any client-supplied XFCC
    and ``/ops/*`` is reachable solely through that Envoy listener (the frozen edge control;
    this verifier is the app-side half — it cannot, and does not, prove the strip).
  - Fingerprints are public (cert hashes), not secrets — set membership is the right check;
    the auth secret is the operator's private key, validated upstream by Envoy's mTLS.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpsIdentity:
    """A verified platform-operator identity (the matched leaf-cert fingerprint)."""

    fingerprint: str  # lowercase hex SHA-256 of the operator client cert


class OpsCertVerifier:
    """Match an Envoy-forwarded XFCC operator cert fingerprint against an allow-list."""

    def __init__(self, fingerprints: frozenset[str]) -> None:
        # Normalize to lowercase hex; drop blanks. An empty set = OFF / fail-closed.
        self._fingerprints = frozenset(fp.strip().lower() for fp in fingerprints if fp.strip())

    @classmethod
    def from_settings(cls, settings: object) -> OpsCertVerifier:
        """Build from the CSV ``ops_cert_fingerprints`` setting (`` `` = OFF)."""
        raw = str(getattr(settings, "ops_cert_fingerprints", "") or "")
        return cls(frozenset(part for part in raw.split(",")))

    def identify(self, xfcc_header: str | None) -> OpsIdentity | None:
        """Return an OpsIdentity iff the XFCC carries a recognised operator fingerprint.

        Fail-closed on every other path: empty allow-list, missing/empty header, no Hash
        field (malformed), or an unrecognised fingerprint → None.
        """
        if not self._fingerprints:  # default-OFF
            return None
        if not xfcc_header:
            return None
        fingerprint = self._extract_hash(xfcc_header)
        if fingerprint is None:
            return None
        fingerprint = fingerprint.lower()
        if fingerprint in self._fingerprints:
            return OpsIdentity(fingerprint=fingerprint)
        return None

    @staticmethod
    def _extract_hash(xfcc_header: str) -> str | None:
        """Extract the leaf cert's ``Hash`` from an Envoy XFCC header, or None.

        XFCC is a comma-separated list of certs (leaf first), each a ``;``-separated list of
        ``Key=Value`` pairs (values may be quoted). We read ONLY the leaf (first) cert's Hash;
        matching an intermediate/chain entry would be a verification bypass.
        """
        leaf = xfcc_header.split(",", 1)[0]
        for pair in leaf.split(";"):
            key, sep, value = pair.partition("=")
            if sep and key.strip().lower() == "hash":
                return value.strip().strip('"')
        return None
