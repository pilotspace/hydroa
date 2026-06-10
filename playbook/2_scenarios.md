# Playbook 2 — Scenarios

**Role:** Specification tester.

**Read first:** `./SPEC.md`, `./GLOSSARY.md`

**Task:** Produce `features/<name>.feature`.

**Steps:**
1. For each Must and Reject rule, write a Given/When/Then scenario
2. For every rejection, add an And-clause asserting what must NOT change

**Exit:** Every rule has at least one scenario with observable result.

**Never:** Write vague results like "then it works."
