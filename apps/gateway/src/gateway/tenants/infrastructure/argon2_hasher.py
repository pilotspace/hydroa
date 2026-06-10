from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import VerificationError


class Argon2PasswordHasher:
    """Implements domain port PasswordHasher (argon2id)."""

    def __init__(self) -> None:
        self._hasher = _Argon2()
        # Verified against when the user is unknown, so both login failure
        # paths cost the same time (no enumeration via timing).
        self._dummy_hash = self._hasher.hash("timing-equalization-dummy")

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str | None, password: str) -> bool:
        try:
            self._hasher.verify(
                password_hash if password_hash is not None else self._dummy_hash, password
            )
        except VerificationError:
            return False
        return password_hash is not None
