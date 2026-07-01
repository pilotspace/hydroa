════════════════════════════════════════════════════════════════════════
 openrouter-embeddings · OpenRouter embeddings routing
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     1/1 done           CRITERIA  3/3 met
 GATES     1 PASS             WAIVERS   none

 goal  A client can call POST /v1/embeddings with an OpenRouter-hosted
       embedding model (e.g. google/gemini-embedding-2) and get a real
       embedding back, billed correctly — the facade forwards
       /embeddings instead of hardcoding /chat/completions, and catalog
       sync classifies OpenRouter embedding models correctly instead of
       defaulting every row to modality=chat.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 openrouter-embeddings-rout… done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 GATED BY
   openrouter-embeddings-r… PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 3/3 met

 LEARNINGS (2 carried)
   • ADD · open · a Protocol-port change
     (`CatalogSource`/`CatalogRepository` gaining a new method/ kwarg)
     silently breaks any structural test double that isn't grepped for —
     the §1 "only one implementer" ground-phase claim was about
     PRODUCTION code only; two test fixtures (`FakeCatalogSource` in two
     files) were an unaccounted second/third "implementer" that broke at
     BUILD time, caught only by actually running the full suite, not by
     the ground-phase grep (evidence:
     `tests/catalog/test_model_catalog.py` +
     `tests/catalog_sync_trigger/conftest.py` both needed a
     `list_embedding_models()` stub + `modality` field added). Future
     Protocol-port changes should grep test doubles too, not just
     `src/`.
   • TDD · open · an async-generator method's exception only surfaces on
     first iteration, not at call time — useful for red-suite authors
     testing `CatalogSourceUnavailableError`-raising scenarios: `with
     pytest.raises(...): [x async for x in obj.method()]`, not `with
     pytest.raises(...): obj.method()` (evidence: OER6b test design,
     confirmed correct by the refute-read's passing mutation test).

 SPEC DELTAS    214 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              openrouter-embeddings
════════════════════════════════════════════════════════════════════════