"""Domain errors for eval-run execution (eval-run-executor §3).

Named errors the application/repository raise and the router translates to the wire — never
a bare Exception or an inlined HTTP status in the use-case (appsec-engineer lens).
"""

from __future__ import annotations


class EvalRunNotFound(Exception):
    """The named run (or its parent set) is absent OR owned by another tenant — indistinguishable.

    The router maps this to a uniform 404 ERR_EVAL_RUN_NOT_FOUND / ERR_EVAL_SET_NOT_FOUND,
    identical for both causes, so there is no enumeration oracle (M6, R:RUN_NOT_FOUND).
    """
