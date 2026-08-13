"""Deterministic scorers for the evals regression gate (deterministic-scorers §3, FROZEN @ v1).

PURE library — no IO, no tenant identity, no persistence. A scorer maps an assertion
(kind + expected) and a model's output text to a boolean ``ScoreResult``, identically on a
re-run. See ``ports.py`` for the port + result shape and ``scorers.py`` for the four kinds.
"""
