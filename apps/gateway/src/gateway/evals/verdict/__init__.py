"""baseline-and-verdict (R7 L2) — pin a baseline run, verdict a candidate against it.

A run's SCORE is the exact pair ``(pass_count, total)`` computed ON DEMAND (never persisted,
R:STALE_SCORE) by re-scoring each completed case through the PURE deterministic scorer. A
VERDICT compares a candidate run to its eval_set's pinned baseline by EXACT integer
cross-multiplication — never float division (R:FLOAT_TIE). No pinned baseline is the explicit
``no_baseline`` state, never a silent pass (R:SILENT_PASS_NO_BASELINE).
"""
