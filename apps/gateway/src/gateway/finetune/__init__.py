"""finetune-broker (PLAN.md, FROZEN @ v1) — /v1/fine_tuning/jobs brokered to the
tenant's own BYOK provider credential.

Layout mirrors ``gateway.batches`` (job-store precedent):
  domain/          FinetuneProviderPort, FinetuneCompletionListener, provider derivation
  infrastructure/  ORM rows, repository, the real OpenAIFinetuneClient adapter
  application/      FinetuneBrokerService (the use-case / brokering flow)
  api/             FastAPI router (/v1/fine_tuning/jobs)
"""

from __future__ import annotations
