# Playbook 4 — Tests

**Role:** Test author who writes tests before code.

**Read first:** `./features/*.feature`, `./contracts/*`

**Task:** Produce failing (red) test suite. Do NOT implement the feature.

**Steps:**
1. Turn each scenario into executable test
2. Add contract-conformance and edge-case tests
3. Run suite; confirm it fails for right reason
4. Record coverage target

**Exit:** One test per scenario; suite red for right reason; target recorded.

**Never:** Assert on internals; write the implementation.
