"""Domain errors for the evals module (eval-set-store §3).

Named errors the application layer raises and the router translates to the wire — never a
bare Exception or an inlined HTTP status in the use-case (backend-architect lens).
"""

from __future__ import annotations


class EvalSetNotFound(Exception):
    """The named parent set is absent OR owned by another tenant — indistinguishable (M5).

    The router maps this to a uniform 404 ERR_EVAL_SET_NOT_FOUND, identical for both causes,
    so there is no enumeration oracle.
    """
