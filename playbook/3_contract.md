# Playbook 3 — Contract

**Role:** Interface/contract architect; contracts are immutable once frozen.

**Read first:** `./SPEC.md`, `./features/*.feature`, `./GLOSSARY.md`

**Task:** Produce `contracts/<name>.md`, mock server, and contract tests.
No business logic.

**Steps:**
1. Define interfaces, request/response shapes, schema — named from glossary
2. Define a response for every Reject error code
3. Generate mock returning contracted shapes; create contract tests pinning them
4. Mark contract FROZEN at a version

**Exit:** Contract tests pass against mock; every spec rejection has response.

**Never:** Change a frozen contract — changes reopen Specify phase.
