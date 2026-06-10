# Playbook 5 — Build

**Role:** Execution agent. The human commands; you implement and report.

**Read first:** `./SPEC.md`, `./contracts/*`, `./tests/*`, `./CONVENTIONS.md`

**Task:** Make EVERY failing test pass, one small task at a time.

**Steps:**
1. Pick ONE task; restate the tests it must satisfy before coding
2. Implement; run tests; iterate to green WITHOUT weakening any test
3. Honor feature-specific safety rule
4. Run security and allow-list checks; attach evidence bundle

**Exit:** All green; coverage held; no test/contract changed; no unlisted packages.

**Never:** Change test or contract; add unlisted dependency; guess when unclear.
