"""Checkout error envelope (self-serve-checkout TASK.md §3, FROZEN @ v1).

The §3 endpoint contract returns checkout-specific rejects as ``{ "error": "<token>" }`` at
the appropriate HTTP status — a DIFFERENT envelope from the RFC 9457 ``{ "code": ... }``
problem shape used elsewhere. (The dependency-level 403 ERR_AUTH_FORBIDDEN from
require_permission still uses the problem envelope; that is the frozen contract's own mixed
403 line.) `CheckoutError` carries the (status, token) pair; a single app-level exception
handler renders it (see gateway.payments.api.error_handler).
"""

from __future__ import annotations


class CheckoutError(Exception):
    """A checkout-specific reject rendered as ``{ "error": <error> }`` with ``status``."""

    def __init__(self, status: int, error: str) -> None:
        super().__init__(error)
        self.status = status
        self.error = error

    # -- Reject constructors (one per §3 reject token) -----------------------
    @classmethod
    def checkout_disabled(cls) -> CheckoutError:
        return cls(403, "checkout_disabled")

    @classmethod
    def checkout_not_found(cls) -> CheckoutError:
        # 404 — covers unknown id AND cross-tenant session (existence never revealed, M1).
        return cls(404, "checkout_not_found")

    @classmethod
    def checkout_not_confirmable(cls) -> CheckoutError:
        return cls(409, "checkout_not_confirmable")

    @classmethod
    def plan_not_found(cls) -> CheckoutError:
        return cls(404, "plan_not_found")

    @classmethod
    def plan_not_self_serve(cls) -> CheckoutError:
        return cls(422, "plan_not_self_serve")

    @classmethod
    def plan_account_type_mismatch(cls) -> CheckoutError:
        return cls(422, "plan_account_type_mismatch")

    @classmethod
    def plan_downgrade_not_self_serve(cls) -> CheckoutError:
        return cls(422, "plan_downgrade_not_self_serve")

    @classmethod
    def plan_unchanged(cls) -> CheckoutError:
        return cls(422, "plan_unchanged")

    @classmethod
    def amount_invalid(cls) -> CheckoutError:
        return cls(422, "amount_invalid")

    @classmethod
    def amount_exceeds_max(cls) -> CheckoutError:
        return cls(422, "amount_exceeds_max")

    @classmethod
    def idempotency_key_required(cls) -> CheckoutError:
        return cls(400, "idempotency_key_required")

    @classmethod
    def idempotency_key_conflict(cls) -> CheckoutError:
        return cls(409, "idempotency_key_conflict")

    @classmethod
    def payment_provider_unavailable(cls) -> CheckoutError:
        return cls(503, "payment_provider_unavailable")


class PaymentProviderUnavailable(Exception):
    """Raised by a PaymentProvider adapter when outbound IO fails / breaker open (M12).

    The CheckoutService maps it to CheckoutError.payment_provider_unavailable (503) with NO
    partial mutation.
    """
