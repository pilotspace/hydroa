"""SHA-256 secret hasher for API keys.

API key secrets are 32 bytes of CSPRNG output (256-bit entropy).  Dictionary
and brute-force attacks are computationally infeasible regardless of hash
speed.  Using SHA-256 instead of argon2 keeps /internal/authz sub-millisecond
(argon2 at sensible cost adds 50-200 ms per verify on the hot path).

Passwords (users.password_hash) REMAIN argon2 — see GLOSSARY amendment in §3.

Safety rule (task §5):
- hmac.compare_digest is used for constant-time comparison to prevent
  timing-based enumeration of valid key_ids.
"""

import hashlib
import hmac


class Sha256SecretHasher:
    """Implements domain port SecretHasher using stdlib hashlib + hmac."""

    def hash(self, secret: str) -> str:
        """Return the SHA-256 hex digest of the UTF-8 encoded secret."""
        return hashlib.sha256(secret.encode()).hexdigest()

    def verify(self, stored_hash: str, candidate_secret: str) -> bool:
        """Compare SHA-256(candidate_secret) == stored_hash in constant time.

        Uses hmac.compare_digest to prevent timing attacks regardless of
        whether the hashes differ early or late in the comparison.
        Returns False on any mismatch; NEVER raises.
        """
        candidate_hash = self.hash(candidate_secret)
        return hmac.compare_digest(stored_hash, candidate_hash)
