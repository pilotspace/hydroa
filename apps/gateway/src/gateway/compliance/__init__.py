"""compliance/ — read-only cross-context evidence composition (Art. 12 record-keeping).

FIRST module in this bounded context (art12-record-keeping-preset TASK.md §3, FROZEN @ v1).
Owns NO domain state of its own — pure read composition over `audit/`, `logs/`, and `usage/`.
No `domain/`/`application/`/`infrastructure/` layers exist here by design (see that TASK.md's
Strategy step 3): the router calls the 3 existing/new repositories directly, mirroring
`audit/api/router.py`'s own precedent for a simple, non-mutating, no-cross-aggregate-invariant
read.
"""
