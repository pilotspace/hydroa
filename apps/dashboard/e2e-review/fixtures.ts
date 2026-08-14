/**
 * fixtures.ts — mocked BFF/gateway response bodies for the UI/UX review harness.
 *
 * `gwBody(gatewayPath, method)` returns the JSON body to fulfill a same-origin
 * /api/gw/<gatewayPath> request (query string already stripped by the caller).
 * Return `undefined` to fall through to an empty `{}`.
 *
 * This is a PURE data module: plain object literals only, no imports of app
 * code, so it can never break the app build. Every shape below was mined from
 * either the real TS interface a component declares next to its `bffGet<T>()`
 * call, or a msw mock in tests-bff/**.test.tsx|tests/**.test.tsx that already
 * validates against that interface — see the `// source:` comment above each
 * block. Dynamic tenant-id (and other id) path segments are matched with a
 * generic `[^/]+` capture — the harness may visit any tenant, so content is
 * echoed/consistent rather than keyed off a single hardcoded UUID.
 *
 * Signature is FROZEN — a separate spec (the Playwright screenshot harness)
 * imports it.
 */

// ─────────────────────────────────────────────────────────────────────────────
// Shared fixture data
// ─────────────────────────────────────────────────────────────────────────────

/** source: components/models/ModelCatalogTable.tsx (ModelEntry/ModelsData) */
const CATALOG_MODELS = [
  {
    id: "openai/gpt-4o",
    name: "GPT-4o",
    context_length: 128000,
    prompt_per_token: 0.0000025,
    completion_per_token: 0.00001,
    object: "model",
    input_modalities: ["text", "image"],
  },
  {
    id: "anthropic/claude-sonnet-5",
    name: "Claude Sonnet 5",
    context_length: 200000,
    prompt_per_token: 0.000003,
    completion_per_token: 0.000015,
    object: "model",
    input_modalities: ["text", "image"],
  },
  {
    id: "google/gemini-2.5-pro",
    name: "Gemini 2.5 Pro",
    context_length: 1048576,
    prompt_per_token: 0.00000125,
    completion_per_token: 0.000005,
    object: "model",
    input_modalities: ["text", "image", "audio"],
  },
  {
    id: "openrouter/meta-llama/llama-3.3-70b-instruct",
    name: "Llama 3.3 70B Instruct",
    context_length: 131072,
    prompt_per_token: 0.00000012,
    completion_per_token: 0.0000003,
    object: "model",
    input_modalities: ["text"],
  },
  {
    id: "minimax/abab6.5s-chat",
    name: "MiniMax abab6.5s Chat",
    context_length: 245760,
    prompt_per_token: 0.0000003,
    completion_per_token: 0.0000009,
    object: "model",
    input_modalities: ["text"],
  },
];

/** source: components/models/ModelsPage.tsx (AdminModelItem/AdminModelsListResponse) */
const ADMIN_MODELS = CATALOG_MODELS.map((m, i) => ({
  id: m.id,
  name: m.name,
  context_length: m.context_length,
  enabled: i !== 3, // one disabled model for visual variety
  input_modalities: m.input_modalities,
}));

/** source: components/platform/PlatformTenantDirectory.tsx (PlatformTenantSummary) */
const PLATFORM_TENANTS = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    name: "Acme Corp",
    kind: "customer",
    created_at: "2025-01-15T09:30:00Z",
  },
  {
    id: "22222222-2222-4222-8222-222222222222",
    name: "Globex Corporation",
    kind: "customer",
    created_at: "2025-03-22T14:10:00Z",
  },
  {
    id: "33333333-3333-4333-8333-333333333333",
    name: "Initech",
    kind: "customer",
    created_at: "2025-05-08T11:45:00Z",
  },
  {
    id: "44444444-4444-4444-8444-444444444444",
    name: "Umbrella Corporation",
    kind: "customer",
    created_at: "2025-07-19T08:00:00Z",
  },
  {
    id: "00000000-0000-0000-0000-000000000001",
    name: "Hydroa Platform",
    kind: "platform",
    created_at: "2024-11-01T00:00:00Z",
  },
];

/** source: components/platform/PlatformPlanCatalog.tsx (PlanResponse/PlansListResponse) */
const PLATFORM_PLANS = [
  {
    id: "plan-free",
    name: "free",
    display_name: "Free",
    seat_cap: 3,
    budget_usd_monthly_default: "0.00",
    rpm_limit_default: 20,
    tpm_limit_default: 20000,
  },
  {
    id: "plan-pro",
    name: "pro",
    display_name: "Pro",
    seat_cap: 25,
    budget_usd_monthly_default: "500.00",
    rpm_limit_default: 120,
    tpm_limit_default: 200000,
  },
  {
    id: "plan-enterprise",
    name: "enterprise",
    display_name: "Enterprise",
    seat_cap: null,
    budget_usd_monthly_default: null,
    rpm_limit_default: null,
    tpm_limit_default: null,
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// Route table
// ─────────────────────────────────────────────────────────────────────────────

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "ANY";

interface Route {
  method: Method;
  pattern: RegExp;
  build: (match: RegExpMatchArray) => unknown;
}

const ROUTES: Route[] = [
  // ── Tenant-facing GET reads ────────────────────────────────────────────────

  // source: components/usage/UsageStatsCards.tsx (UsageData/UsageRecord) +
  // tests-bff/mocks/handlers.ts (base shape)
  {
    method: "GET",
    pattern: /^admin\/usage$/,
    build: () => ({
      total_cost_usd: "184.32",
      total_requests: 15234,
      total_prompt_tokens: 3840211,
      total_completion_tokens: 1920442,
      records: [
        {
          id: "9f1b2c3d-0001-4a11-8a11-000000000001",
          model_id: "openai/gpt-4o",
          prompt_tokens: 1240,
          completion_tokens: 512,
          cost_usd: "0.0134",
          status: 200,
          created_at: "2026-07-06T14:32:10Z",
        },
        {
          id: "9f1b2c3d-0001-4a11-8a11-000000000002",
          model_id: "anthropic/claude-sonnet-5",
          prompt_tokens: 3400,
          completion_tokens: 980,
          cost_usd: "0.0249",
          status: 200,
          created_at: "2026-07-06T13:58:44Z",
        },
        {
          id: "9f1b2c3d-0001-4a11-8a11-000000000003",
          model_id: "google/gemini-2.5-pro",
          prompt_tokens: 890,
          completion_tokens: 210,
          cost_usd: "0.0022",
          status: 429,
          created_at: "2026-07-06T12:11:02Z",
        },
        {
          id: "9f1b2c3d-0001-4a11-8a11-000000000004",
          model_id: "openrouter/meta-llama/llama-3.3-70b-instruct",
          prompt_tokens: 5200,
          completion_tokens: 1780,
          cost_usd: "0.0011",
          status: 200,
          created_at: "2026-07-06T09:47:19Z",
        },
        {
          id: "9f1b2c3d-0001-4a11-8a11-000000000005",
          model_id: "openai/gpt-4o",
          prompt_tokens: 610,
          completion_tokens: 88,
          cost_usd: "0.0031",
          status: 500,
          created_at: "2026-07-05T22:05:51Z",
        },
      ],
    }),
  },

  // source: components/usage/BudgetWidget.tsx (BudgetData)
  {
    method: "GET",
    pattern: /^admin\/budget$/,
    build: () => ({ budget_usd_monthly: "500.00", spent_usd_month: "184.32" }),
  },

  // source: components/spend/SpendPage.tsx + components/overview/OverviewPage.tsx
  // (SpendWindowResponse — superset of both components' local interfaces)
  {
    method: "GET",
    pattern: /^admin\/spend$/,
    build: () => {
      const buckets = [
        { bucket_start: "2026-07-01", requests: 1820, prompt_tokens: 412000, completion_tokens: 198000, cost_usd: "22.40" },
        { bucket_start: "2026-07-02", requests: 2105, prompt_tokens: 468000, completion_tokens: 224000, cost_usd: "25.10" },
        { bucket_start: "2026-07-03", requests: 1990, prompt_tokens: 441000, completion_tokens: 209000, cost_usd: "23.75" },
        { bucket_start: "2026-07-04", requests: 2310, prompt_tokens: 512000, completion_tokens: 246000, cost_usd: "27.90" },
        { bucket_start: "2026-07-05", requests: 2480, prompt_tokens: 549000, completion_tokens: 261000, cost_usd: "29.35" },
        { bucket_start: "2026-07-06", requests: 2529, prompt_tokens: 561000, completion_tokens: 267000, cost_usd: "30.02" },
      ];
      return {
        window: "month",
        bucket_size: "month",
        totals: {
          bucket_start: "2026-07-01",
          bucket_end: "2026-07-06",
          requests: 15234,
          prompt_tokens: 3840211,
          completion_tokens: 1920442,
          cost_usd: "184.32",
        },
        buckets,
        breakdown: [
          { key_id: "kid-prod-1a2b3c", requests: 8210, prompt_tokens: 2100000, completion_tokens: 980000, cost_usd: "98.10" },
          { key_id: "kid-staging-4d5e6f", requests: 4120, prompt_tokens: 980000, completion_tokens: 512000, cost_usd: "52.05" },
          { key_id: "kid-ci-7g8h9i", requests: 2904, prompt_tokens: 760211, completion_tokens: 428442, cost_usd: "34.17" },
        ],
      };
    },
  },

  // source: components/keys/KeysPage.tsx (ApiKey — includes v15 governance fields)
  {
    method: "GET",
    pattern: /^admin\/keys$/,
    build: () => [
      {
        key_id: "kid-a1b2c3d4",
        name: "production",
        prefix: "sk-prod-a1b2",
        created_at: "2026-01-10T00:00:00Z",
        revoked_at: null,
        monthly_budget_usd: "250.00",
        soft_budget_usd: "200.00",
        expires_at: null,
        model_allowlist: ["openai/gpt-4o", "anthropic/claude-sonnet-5"],
        rpm_limit: 120,
        tpm_limit: 200000,
        team_id: "team-eng-0001",
        cache_enabled: true,
      },
      {
        key_id: "kid-e5f6g7h8",
        name: "staging",
        prefix: "sk-stg-e5f6",
        created_at: "2026-02-14T00:00:00Z",
        revoked_at: null,
        monthly_budget_usd: null,
        soft_budget_usd: null,
        expires_at: "2026-12-31T23:59:59Z",
        model_allowlist: null,
        rpm_limit: null,
        tpm_limit: null,
        team_id: null,
        cache_enabled: false,
      },
      {
        key_id: "kid-i9j0k1l2",
        name: "ci-pipeline",
        prefix: "sk-ci-i9j0",
        created_at: "2026-03-02T00:00:00Z",
        revoked_at: null,
        monthly_budget_usd: "50.00",
        soft_budget_usd: null,
        expires_at: null,
        model_allowlist: null,
        rpm_limit: 60,
        tpm_limit: 60000,
        team_id: "team-eng-0001",
        cache_enabled: false,
      },
      {
        key_id: "kid-m3n4o5p6",
        name: "legacy-mobile",
        prefix: "sk-mob-m3n4",
        created_at: "2025-09-18T00:00:00Z",
        revoked_at: "2026-06-01T00:00:00Z",
        monthly_budget_usd: null,
        soft_budget_usd: null,
        expires_at: null,
        model_allowlist: null,
        rpm_limit: null,
        tpm_limit: null,
        team_id: null,
        cache_enabled: false,
      },
    ],
  },

  // source: components/models/ModelCatalogTable.tsx (ModelsData)
  {
    method: "GET",
    pattern: /^admin\/catalog\/models$/,
    build: () => ({ object: "list", data: CATALOG_MODELS }),
  },

  // source: components/settings/CacheSettings.tsx (CacheConfig)
  {
    method: "GET",
    pattern: /^admin\/cache$/,
    build: () => ({ enabled: true, semantic_enabled: false }),
  },

  // source: components/models/ModelsPage.tsx (AdminModelsListResponse)
  {
    method: "GET",
    pattern: /^admin\/models$/,
    build: () => ({ object: "list", data: ADMIN_MODELS }),
  },

  // source: components/teams/types.ts (TeamResponse — bare array, no envelope)
  {
    method: "GET",
    pattern: /^admin\/teams$/,
    build: () => [
      {
        id: "team-eng-0001",
        name: "Engineering",
        tenant_id: "00000000-0000-0000-0000-000000000099",
        created_at: "2026-01-20T00:00:00Z",
        member_count: 5,
        key_count: 3,
        team_budget_usd: "250.00",
      },
      {
        id: "team-ds-0002",
        name: "Data Science",
        tenant_id: "00000000-0000-0000-0000-000000000099",
        created_at: "2026-02-11T00:00:00Z",
        member_count: 3,
        key_count: 2,
        team_budget_usd: null,
      },
      {
        id: "team-growth-0003",
        name: "Growth",
        tenant_id: "00000000-0000-0000-0000-000000000099",
        created_at: "2026-03-05T00:00:00Z",
        member_count: 2,
        key_count: 1,
        team_budget_usd: "100.00",
      },
    ],
  },

  // source: components/teams/types.ts (TeamDetailResponse = TeamResponse + members)
  {
    method: "GET",
    pattern: /^admin\/teams\/([^/]+)$/,
    build: ([, id]) => ({
      id,
      name: "Engineering",
      tenant_id: "00000000-0000-0000-0000-000000000099",
      created_at: "2026-01-20T00:00:00Z",
      member_count: 3,
      key_count: 3,
      team_budget_usd: "250.00",
      members: [
        { user_id: "00000000-0000-0000-0000-000000000001", role: "lead", added_at: "2026-01-20T00:00:00Z" },
        { user_id: "00000000-0000-0000-0000-000000000010", role: "member", added_at: "2026-01-22T00:00:00Z" },
        { user_id: "00000000-0000-0000-0000-000000000011", role: "member", added_at: "2026-02-01T00:00:00Z" },
      ],
    }),
  },

  // source: components/members/MembersPage.tsx (UsersListResponse/TenantUser)
  {
    method: "GET",
    pattern: /^admin\/users$/,
    build: () => ({
      users: [
        { id: "00000000-0000-0000-0000-000000000001", email: "ada@acme.io", role: "owner" },
        { id: "00000000-0000-0000-0000-000000000010", email: "grace@acme.io", role: "admin" },
        { id: "00000000-0000-0000-0000-000000000011", email: "alan@acme.io", role: "operator" },
        { id: "00000000-0000-0000-0000-000000000012", email: "margaret@acme.io", role: "viewer" },
        { id: "00000000-0000-0000-0000-000000000013", email: "dennis@acme.io", role: "member" },
      ],
    }),
  },

  // source: components/routing/RoutingPage.tsx (RoutingConf)
  {
    method: "GET",
    pattern: /^admin\/routing$/,
    build: () => ({
      routing_strategy: "least-busy",
      retry_policy: { max_retries: 3, backoff_base_s: 0.5 },
      cooldown: { enabled: true, threshold: 5, ttl_s: 60, window_s: 120 },
      model_groups: {
        fast: ["openai/gpt-4o-mini", "anthropic/claude-haiku-4"],
        smart: ["openai/gpt-4o", "anthropic/claude-sonnet-5"],
      },
      deployments: {
        fast: [
          { model_id: "openai/gpt-4o-mini", weight: 2, rpm_limit: 500, tpm_limit: 500000 },
          { model_id: "anthropic/claude-haiku-4", weight: 1, rpm_limit: 300, tpm_limit: 300000 },
        ],
        smart: [
          { model_id: "openai/gpt-4o", weight: 1, rpm_limit: 120, tpm_limit: 200000 },
          { model_id: "anthropic/claude-sonnet-5", weight: 1, rpm_limit: 120, tpm_limit: 200000 },
        ],
      },
      candidates: [
        { model_id: "openai/gpt-4o-mini", alias: "fast", state: "closed" },
        { model_id: "anthropic/claude-haiku-4", alias: "fast", state: "half_open" },
        { model_id: "openai/gpt-4o", alias: "smart", state: "closed" },
        { model_id: "anthropic/claude-sonnet-5", alias: "smart", state: "open" },
      ],
    }),
  },

  // source: components/alerts/AlertsTable.tsx (AlertsData/AlertRow)
  {
    method: "GET",
    pattern: /^admin\/alerts$/,
    build: () => ({
      items: [
        {
          id: "alert-0001",
          event_type: "soft-budget",
          payload: { threshold_pct: 80, spent_usd_month: "184.32" },
          created_at: "2026-07-06T10:00:00Z",
          delivered: true,
          delivered_at: "2026-07-06T10:00:05Z",
        },
        {
          id: "alert-0002",
          event_type: "circuit-open",
          payload: { model_id: "anthropic/claude-sonnet-5", alias: "smart" },
          created_at: "2026-07-05T22:14:00Z",
          delivered: true,
          delivered_at: "2026-07-05T22:14:02Z",
        },
        {
          id: "alert-0003",
          event_type: "upstream-health",
          payload: { upstream: "google", status: "down" },
          created_at: "2026-07-05T18:47:00Z",
          delivered: false,
          delivered_at: null,
        },
        {
          id: "alert-0004",
          event_type: "drift",
          payload: { metric: "cost_variance_pct", value: 4.2 },
          created_at: "2026-07-04T09:12:00Z",
          delivered: true,
          delivered_at: "2026-07-04T09:12:01Z",
        },
      ],
      total: 4,
    }),
  },

  // source: components/audit/AuditTable.tsx (AuditData/AuditRow)
  {
    method: "GET",
    pattern: /^admin\/audit$/,
    build: () => ({
      items: [
        {
          id: "audit-0001",
          actor_email: "ada@acme.io",
          action: "create_key",
          target_type: "api_key",
          target_id: "kid-a1b2c3d4",
          result: "success",
          metadata: { name: "production" },
          created_at: "2026-07-06T09:00:00Z",
        },
        {
          id: "audit-0002",
          actor_email: "grace@acme.io",
          action: "update_budget",
          target_type: "tenant",
          target_id: "00000000-0000-0000-0000-000000000099",
          result: "success",
          metadata: { budget_usd_monthly: "500.00" },
          created_at: "2026-07-05T16:20:00Z",
        },
        {
          id: "audit-0003",
          actor_email: "alan@acme.io",
          action: "assign_role",
          target_type: "user",
          target_id: "00000000-0000-0000-0000-000000000013",
          result: "denied",
          metadata: { attempted_role: "owner" },
          created_at: "2026-07-05T11:05:00Z",
        },
        {
          id: "audit-0004",
          actor_email: null,
          action: "revoke_key",
          target_type: "api_key",
          target_id: "kid-m3n4o5p6",
          result: "success",
          metadata: {},
          created_at: "2026-06-01T00:00:00Z",
        },
        {
          id: "audit-0005",
          actor_email: "ada@acme.io",
          action: "update_guardrails",
          target_type: "tenant",
          target_id: "00000000-0000-0000-0000-000000000099",
          result: "success",
          metadata: { prompt_injection: true },
          created_at: "2026-05-28T14:40:00Z",
        },
      ],
      total: 5,
    }),
  },

  // source: components/health/UpstreamsTable.tsx (UpstreamHealthData/UpstreamRow)
  {
    method: "GET",
    pattern: /^admin\/health\/upstreams$/,
    build: () => ({
      checked_at: "2026-07-06T15:00:00Z",
      upstreams: [
        { name: "openai", status: "up", last_event_at: null, last_event_type: null },
        { name: "anthropic", status: "up", last_event_at: null, last_event_type: null },
        { name: "google", status: "down", last_event_at: "2026-07-05T18:47:00Z", last_event_type: "timeout" },
        { name: "bedrock", status: "up", last_event_at: "2026-06-30T02:11:00Z", last_event_type: "recovered" },
      ],
    }),
  },

  // source: components/slo/SloPage.tsx (SloData)
  {
    method: "GET",
    pattern: /^admin\/slo$/,
    build: () => ({
      window_hours: 24,
      total_requests: 15234,
      success_count: 15029,
      client_error_count: 150,
      server_error_count: 55,
      availability: 0.9829,
      error_rate: 0.0171,
      latency_ms: null,
    }),
  },

  // source: components/presets/PresetsPage.tsx (PresetsResponse/Preset)
  {
    method: "GET",
    pattern: /^admin\/presets$/,
    build: () => ({
      presets: [
        { preset_name: "default", alias_key: "fast", target_model: "openai/gpt-4o-mini", updated_at: "2026-06-01T00:00:00Z" },
        { preset_name: "default", alias_key: "smart", target_model: "anthropic/claude-sonnet-5", updated_at: "2026-06-01T00:00:00Z" },
        { preset_name: "cheap", alias_key: "fast", target_model: "openrouter/meta-llama/llama-3.3-70b-instruct", updated_at: "2026-06-15T00:00:00Z" },
      ],
    }),
  },

  // source: components/keys/BandwidthPanel.tsx (BandwidthData/BandwidthRow)
  {
    method: "GET",
    pattern: /^admin\/bandwidth$/,
    build: () => ({
      enabled: true,
      rate_per_sec: 5000,
      burst: 20000,
      keys: [
        { key_id: "kid-a1b2c3d4", name: "production", level: 12400 },
        { key_id: "kid-e5f6g7h8", name: "staging", level: 2100 },
        { key_id: "kid-i9j0k1l2", name: "ci-pipeline", level: null },
      ],
    }),
  },

  // source: components/keys/RatelimitsPanel.tsx (RatelimitsData/RatelimitRow)
  {
    method: "GET",
    pattern: /^admin\/ratelimits$/,
    build: () => ({
      keys: [
        { key_id: "kid-a1b2c3d4", name: "production", rpm_limit: 120, tpm_limit: 200000, rpm_current: 84, tpm_current: 142300 },
        { key_id: "kid-e5f6g7h8", name: "staging", rpm_limit: null, tpm_limit: null, rpm_current: 6, tpm_current: 9800 },
        { key_id: "kid-i9j0k1l2", name: "ci-pipeline", rpm_limit: 60, tpm_limit: 60000, rpm_current: null, tpm_current: null },
      ],
    }),
  },

  // source: components/settings/ProviderKeysSettings.tsx (ProviderKeyStatus)
  {
    method: "GET",
    pattern: /^admin\/provider-keys$/,
    build: () => ({
      keys: [
        { provider: "openrouter", configured: true, enabled: true, auth_mode: "api_key", updated_at: "2026-05-01T00:00:00Z" },
        { provider: "openai", configured: true, enabled: true, auth_mode: "api_key", updated_at: "2026-04-11T00:00:00Z" },
        { provider: "anthropic", configured: true, enabled: false, auth_mode: "api_key", updated_at: "2026-03-22T00:00:00Z" },
        { provider: "google", configured: false, enabled: false, auth_mode: null, updated_at: "2026-01-01T00:00:00Z" },
        { provider: "bedrock", configured: true, enabled: true, auth_mode: "aws_sigv4", updated_at: "2026-06-02T00:00:00Z" },
        { provider: "azure", configured: false, enabled: false, auth_mode: null, updated_at: "2026-01-01T00:00:00Z" },
        { provider: "minimax", configured: true, enabled: true, auth_mode: "api_key", updated_at: "2026-06-20T00:00:00Z" },
      ],
    }),
  },

  // source: components/settings/OidcSettings.tsx (OidcConf)
  {
    method: "GET",
    pattern: /^admin\/oidc$/,
    build: () => ({
      tenant_id: "00000000-0000-0000-0000-000000000099",
      issuer: "https://idp.acme.io",
      client_id: "hydroa-dashboard",
      client_secret: "<stored>",
      authorize_url: "https://idp.acme.io/auth",
      token_url: "https://idp.acme.io/token",
      jwks_url: "https://idp.acme.io/jwks",
      email_domains: ["acme.io"],
      enabled: true,
      created_at: "2026-02-01T00:00:00Z",
      updated_at: "2026-05-15T00:00:00Z",
    }),
  },

  // source: components/settings/GuardrailSettings.tsx (GuardrailConfig)
  {
    method: "GET",
    pattern: /^admin\/guardrails$/,
    build: () => ({
      prompt_injection: { enabled: true, mode: "block" },
      pii_mask: {
        enabled: true,
        mode: "mask",
        pii_custom_patterns: [{ name: "SSN", pattern: "\\d{3}-\\d{2}-\\d{4}" }],
      },
    }),
  },

  // source: lib/conversations.ts (ConversationSummary)
  {
    method: "GET",
    pattern: /^v1\/conversations$/,
    build: () => ({
      data: [
        { id: "conv-0001", title: "Q3 pricing analysis", updated_at: "2026-07-06T13:00:00Z", message_count: 12 },
        { id: "conv-0002", title: "Debugging the SSE stream", updated_at: "2026-07-05T21:40:00Z", message_count: 34 },
        { id: "conv-0003", title: null, updated_at: "2026-07-04T08:15:00Z", message_count: 2 },
        { id: "conv-0004", title: "Onboarding draft copy", updated_at: "2026-07-01T10:05:00Z", message_count: 8 },
      ],
    }),
  },

  // source: lib/memories.ts (MemoryItem)
  {
    method: "GET",
    pattern: /^v1\/memories$/,
    build: () => ({
      data: [
        {
          id: "mem-0001",
          content: "Prefers concise responses with bullet points.",
          created_at: "2026-06-01T00:00:00Z",
          has_embedding: true,
          metadata: { source: "chat" },
        },
        {
          id: "mem-0002",
          content: "Primary use case is customer-support ticket triage.",
          created_at: "2026-06-10T00:00:00Z",
          has_embedding: true,
          metadata: { source: "onboarding" },
        },
        {
          id: "mem-0003",
          content: "Timezone is America/New_York.",
          created_at: "2026-06-15T00:00:00Z",
          has_embedding: false,
          metadata: {},
        },
        {
          id: "mem-0004",
          content: "Company name is Acme Corp; billing contact is ada@acme.io.",
          created_at: "2026-06-20T00:00:00Z",
          has_embedding: true,
          metadata: { source: "settings" },
        },
      ],
    }),
  },

  // source: lib/artifacts.ts (ArtifactItem)
  {
    method: "GET",
    pattern: /^v1\/artifacts$/,
    build: () => ({
      data: [
        { id: "art-0001", name: "q3-report.pdf", content_type: "application/pdf", size_bytes: 245678, created_at: "2026-07-01T00:00:00Z" },
        { id: "art-0002", name: "architecture-diagram.png", content_type: "image/png", size_bytes: 98213, created_at: "2026-07-03T00:00:00Z" },
        { id: "art-0003", name: "notes.md", content_type: "text/markdown", size_bytes: 4021, created_at: "2026-07-05T00:00:00Z" },
      ],
      limit: 50,
      offset: 0,
    }),
  },

  // source: components/evals/EvalsListPage.tsx (EvalSetsListResponse/EvalSetSummary)
  {
    method: "GET",
    pattern: /^admin\/evals\/sets$/,
    build: () => ({
      object: "list",
      data: [
        {
          id: "es_a1b2c3d4",
          object: "eval.set",
          created_at: 1751328000,
          name: "Support Tone Regression",
          description: "Checks tone drift on refusal edge cases",
          case_count: 2,
        },
        {
          id: "es_e5f6a7b8",
          object: "eval.set",
          created_at: 1751500800,
          name: "Pricing Q&A Accuracy",
          description: null,
          case_count: 1,
        },
      ],
    }),
  },

  // source: components/evals/SetDetailPage.tsx (EvalSetDetail)
  {
    method: "GET",
    pattern: /^admin\/evals\/sets\/([^/]+)$/,
    build: ([, id]) => ({
      id,
      object: "eval.set",
      created_at: 1751328000,
      name: "Support Tone Regression",
      description: "Checks tone drift on refusal edge cases",
      cases: [
        {
          id: "ec_cccc1111",
          object: "eval.case",
          created_at: 1751328100,
          eval_set_id: id,
          assertion: { kind: "exact_match", expected: "Sure thing!" },
        },
        {
          id: "ec_cccc2222",
          object: "eval.case",
          created_at: 1751328200,
          eval_set_id: id,
          assertion: { kind: "exact_match", expected: "Absolutely!" },
        },
      ],
      runs: [
        {
          id: "er_e5f6a7b8",
          object: "eval.run",
          created_at: 1751328300,
          eval_set_id: id,
          model: "openai/gpt-4o",
          status: "completed",
          case_count: 2,
        },
      ],
      baseline_run_id: null,
    }),
  },

  // source: components/evals/RunVerdictPage.tsx (EvalVerdict)
  {
    method: "GET",
    pattern: /^admin\/evals\/runs\/([^/]+)\/verdict$/,
    build: ([, runId]) => ({
      object: "eval.verdict",
      run_id: runId,
      score: { passed: 1, total: 2 },
      baseline: { run_id: "er_00000000", score: { passed: 2, total: 2 } },
      verdict: "fail",
    }),
  },

  // source: components/evals/RunVerdictPage.tsx (EvalCaseResultsResponse)
  {
    method: "GET",
    pattern: /^admin\/evals\/runs\/([^/]+)\/cases$/,
    build: () => ({
      object: "list",
      data: [
        {
          eval_case_id: "ec_cccc1111",
          assertion: { kind: "exact_match", expected: "Sure thing!" },
          status: "completed",
          response_text: "Sure thing!",
          passed: true,
        },
        {
          eval_case_id: "ec_cccc2222",
          assertion: { kind: "exact_match", expected: "Absolutely!" },
          status: "completed",
          response_text: "Nope, can't do that.",
          reason: "Assertion exact_match failed: response did not match expected value",
          passed: false,
        },
      ],
    }),
  },

  // ── Platform (superadmin) GET reads ────────────────────────────────────────

  // source: components/platform/PlatformTenantDirectory.tsx (TenantDirectoryListResponse)
  // also serves components/platform/PlatformCommandPalette.tsx (TenantSearchResponse — same shape)
  {
    method: "GET",
    pattern: /^admin\/platform\/tenants$/,
    build: () => ({ tenants: PLATFORM_TENANTS, total: PLATFORM_TENANTS.length }),
  },

  // source: components/platform/PlatformPlanCatalog.tsx (PlansListResponse)
  {
    method: "GET",
    pattern: /^admin\/platform\/plans$/,
    build: () => ({ plans: PLATFORM_PLANS }),
  },

  // source: components/platform/PlatformPlanTab.tsx (TenantPlanResponse)
  {
    method: "GET",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/plan$/,
    build: ([, tenantId]) => ({
      tenant_id: tenantId,
      plan: PLATFORM_PLANS[1],
      seat_cap: 25,
    }),
  },

  // source: components/platform/PlatformBudgetTab.tsx (BudgetData)
  {
    method: "GET",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/budget$/,
    build: () => ({ budget_usd_monthly: "500.00", spent_usd_month: "184.32" }),
  },

  // source: components/platform/PlatformMembersTab.tsx (UsersListResponse/PlatformTenantUser)
  {
    method: "GET",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/users$/,
    build: () => ({
      users: [
        { id: "00000000-0000-0000-0000-000000000001", email: "ada@acme.io", role: "owner" },
        { id: "00000000-0000-0000-0000-000000000010", email: "grace@acme.io", role: "admin" },
        { id: "00000000-0000-0000-0000-000000000011", email: "alan@acme.io", role: "member" },
      ],
    }),
  },

  // source: components/platform/PlatformKeysTab.tsx (PlatformApiKey — bare array)
  {
    method: "GET",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/keys$/,
    build: () => [
      { key_id: "kid-plat-a1b2", name: "production", prefix: "sk-prod-a1b2", created_at: "2026-01-10T00:00:00Z", revoked_at: null },
      { key_id: "kid-plat-c3d4", name: "staging", prefix: "sk-stg-c3d4", created_at: "2026-02-14T00:00:00Z", revoked_at: null },
      { key_id: "kid-plat-e5f6", name: "old-integration", prefix: "sk-old-e5f6", created_at: "2025-08-01T00:00:00Z", revoked_at: "2026-04-01T00:00:00Z" },
    ],
  },

  // source: components/platform/PlatformConfigTab.tsx (CacheConfig)
  {
    method: "GET",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/cache$/,
    build: () => ({ enabled: true, semantic_enabled: true }),
  },

  // source: components/platform/PlatformConfigTab.tsx (GuardrailConfig)
  {
    method: "GET",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/guardrails$/,
    build: () => ({
      prompt_injection: { enabled: true, mode: "audit" },
      pii_mask: {
        enabled: false,
        mode: "mask",
        pii_custom_patterns: [],
      },
    }),
  },

  // source: components/platform/PlatformActivityTab.tsx (ActivityListResponse/ActivityRow)
  {
    method: "GET",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/audit$/,
    build: () => ({
      items: [
        {
          id: "activity-0001",
          actor_email: "ada@acme.io",
          action: "create_key",
          target_type: "api_key",
          target_id: "kid-plat-a1b2",
          result: "success",
          metadata: { name: "production" },
          created_at: "2026-07-06T09:00:00Z",
        },
        {
          id: "activity-0002",
          actor_email: "superadmin@hydroa.dev",
          action: "assign_plan",
          target_type: "tenant",
          target_id: "11111111-1111-4111-8111-111111111111",
          result: "success",
          metadata: { plan_id: "plan-pro" },
          created_at: "2026-07-04T12:00:00Z",
        },
        {
          id: "activity-0003",
          actor_email: null,
          action: "revoke_key",
          target_type: "api_key",
          target_id: "kid-plat-e5f6",
          result: "success",
          metadata: {},
          created_at: "2026-04-01T00:00:00Z",
        },
      ],
      total: 3,
    }),
  },

  // source: components/platform/PlatformTenantDetail.tsx (PlatformTenantSummary) —
  // deliberately placed AFTER the more specific /tenants/{id}/xxx routes above;
  // the `$` anchor already prevents overlap, this ordering is just for readability.
  // Looks the id up in PLATFORM_TENANTS so navigating from the directory (Screen 1)
  // into a specific row (e.g. the "Hydroa Platform" kind:"platform" row) shows a
  // coherent name/kind on the detail page instead of always "Acme Corp" — an id that
  // isn't in the fixture directory (e.g. a hand-typed URL) falls back to Acme Corp.
  {
    method: "GET",
    pattern: /^admin\/platform\/tenants\/([^/]+)$/,
    build: ([, id]) => ({
      ...(PLATFORM_TENANTS.find((t) => t.id === id) ?? {
        name: "Acme Corp",
        kind: "customer",
        created_at: "2025-01-15T09:30:00Z",
      }),
      id,
    }),
  },

  // ── Tenant-facing mutations (plausible success bodies) ─────────────────────

  // source: components/keys/KeysPage.tsx (CreateKeyResponse)
  {
    method: "POST",
    pattern: /^admin\/keys$/,
    build: () => ({ key_id: "kid-newkey01", name: "new-key", key: "sk-new.a1b2c3d4e5f6g7h8" }),
  },
  // source: components/keys/KeyGovernanceEditor.tsx (ApiKeyGovernance echo)
  {
    method: "PATCH",
    pattern: /^admin\/keys\/([^/]+)$/,
    build: ([, key_id]) => ({
      key_id,
      name: "production",
      prefix: "sk-prod-a1b2",
      created_at: "2026-01-10T00:00:00Z",
      revoked_at: null,
      monthly_budget_usd: "250.00",
      soft_budget_usd: "200.00",
      expires_at: null,
      model_allowlist: ["openai/gpt-4o"],
      rpm_limit: 120,
      tpm_limit: 200000,
      team_id: "team-eng-0001",
      cache_enabled: true,
    }),
  },
  // source: components/keys/KeyGovernanceEditor.tsx (RotateResponse)
  {
    method: "POST",
    pattern: /^admin\/keys\/([^/]+)\/rotate$/,
    build: ([, key_id]) => ({
      new_key_id: "kid-rotated01",
      superseded_key_id: key_id,
      key: "sk-rot.a1b2c3d4e5f6g7h8",
      name: "production",
      monthly_budget_usd: "250.00",
      soft_budget_usd: "200.00",
      expires_at: null,
      model_allowlist: ["openai/gpt-4o"],
    }),
  },
  { method: "DELETE", pattern: /^admin\/keys\/([^/]+)$/, build: () => null },

  // source: components/usage/BudgetEditForm.tsx (BudgetPutResponse)
  { method: "PUT", pattern: /^admin\/budget$/, build: () => ({ budget_usd_monthly: "500.00" }) },

  // source: components/settings/CacheSettings.tsx (CacheConfig echo)
  { method: "PUT", pattern: /^admin\/cache$/, build: () => ({ enabled: true, semantic_enabled: false }) },

  // source: components/settings/GuardrailSettings.tsx (GuardrailConfig echo)
  {
    method: "PUT",
    pattern: /^admin\/guardrails$/,
    build: () => ({
      prompt_injection: { enabled: true, mode: "block" },
      pii_mask: { enabled: true, mode: "mask", pii_custom_patterns: [] },
    }),
  },

  // source: components/settings/OidcSettings.tsx (OidcConf echo)
  {
    method: "PUT",
    pattern: /^admin\/oidc$/,
    build: () => ({
      tenant_id: "00000000-0000-0000-0000-000000000099",
      issuer: "https://idp.acme.io",
      client_id: "hydroa-dashboard",
      client_secret: "<stored>",
      authorize_url: "https://idp.acme.io/auth",
      token_url: "https://idp.acme.io/token",
      jwks_url: "https://idp.acme.io/jwks",
      email_domains: ["acme.io"],
      enabled: true,
      created_at: "2026-02-01T00:00:00Z",
      updated_at: "2026-07-06T00:00:00Z",
    }),
  },

  // source: components/settings/ProviderKeysSettings.tsx (ProviderKeyStatus echo)
  {
    method: "PUT",
    pattern: /^admin\/provider-keys\/([^/]+)$/,
    build: ([, provider]) => ({
      provider,
      configured: true,
      enabled: true,
      auth_mode: "api_key",
      updated_at: "2026-07-06T00:00:00Z",
    }),
  },
  { method: "DELETE", pattern: /^admin\/provider-keys\/([^/]+)$/, build: () => null },

  // source: components/teams/TeamsPage.tsx (TeamResponse)
  {
    method: "POST",
    pattern: /^admin\/teams$/,
    build: () => ({
      id: "team-new0001",
      name: "New Team",
      tenant_id: "00000000-0000-0000-0000-000000000099",
      created_at: "2026-07-06T00:00:00Z",
      member_count: 0,
      key_count: 0,
      team_budget_usd: null,
    }),
  },
  {
    method: "PATCH",
    pattern: /^admin\/teams\/([^/]+)$/,
    build: ([, id]) => ({
      id,
      name: "Engineering",
      tenant_id: "00000000-0000-0000-0000-000000000099",
      created_at: "2026-01-20T00:00:00Z",
      member_count: 3,
      key_count: 3,
      team_budget_usd: "300.00",
    }),
  },
  { method: "DELETE", pattern: /^admin\/teams\/([^/]+)$/, build: () => null },
  {
    method: "POST",
    pattern: /^admin\/teams\/([^/]+)\/members$/,
    build: ([, team_id]) => ({
      team_id,
      user_id: "00000000-0000-0000-0000-000000000020",
      role: "member",
      added_at: "2026-07-06T00:00:00Z",
    }),
  },
  { method: "DELETE", pattern: /^admin\/teams\/([^/]+)\/members\/([^/]+)$/, build: () => null },

  // source: components/members/MembersPage.tsx (TenantUser echo)
  {
    method: "PUT",
    pattern: /^admin\/users\/([^/]+)\/role$/,
    build: ([, id]) => ({ id, email: "grace@acme.io", role: "admin" }),
  },

  // source: components/routing/RoutingEditor.tsx (PutRoutingBody echo)
  {
    method: "PUT",
    pattern: /^admin\/routing$/,
    build: () => ({
      routing_strategy: "least-busy",
      model_groups: {
        fast: [{ model_id: "openai/gpt-4o-mini", weight: 2, rpm_limit: 500, tpm_limit: 500000 }],
        smart: [{ model_id: "openai/gpt-4o", weight: 1, rpm_limit: 120, tpm_limit: 200000 }],
      },
    }),
  },

  // source: components/presets/PresetsPage.tsx (Preset echo)
  {
    method: "PUT",
    pattern: /^admin\/presets\/([^/]+)\/([^/]+)$/,
    build: ([, preset_name, alias_key]) => ({
      preset_name,
      alias_key,
      target_model: "openai/gpt-4o-mini",
      updated_at: "2026-07-06T00:00:00Z",
    }),
  },
  { method: "DELETE", pattern: /^admin\/presets\/([^/]+)\/([^/]+)$/, build: () => null },

  // source: components/models/ModelsPage.tsx (CatalogSyncResponse)
  {
    method: "POST",
    pattern: /^admin\/catalog\/sync$/,
    build: () => ({ synced: CATALOG_MODELS.length, synced_at: "2026-07-06T15:00:00Z" }),
  },
  // source: components/models/ModelsPage.tsx (AdminModelItem echo)
  {
    method: "PUT",
    pattern: /^admin\/models\/(.+)$/,
    build: ([, id]) => ({
      id: decodeURIComponent(id),
      name: "GPT-4o",
      context_length: 128000,
      enabled: true,
      input_modalities: ["text", "image"],
    }),
  },

  // source: components/evals/PinBaselineControl.tsx (EvalBaselinePutResponse)
  {
    method: "PUT",
    pattern: /^admin\/evals\/sets\/([^/]+)\/baseline$/,
    build: ([, setId]) => ({
      object: "eval.baseline",
      eval_set_id: setId,
      baseline_run_id: "er_e5f6a7b8",
      pinned_at: 1751587400,
    }),
  },

  // ── Platform mutations (plausible success bodies) ──────────────────────────

  // source: components/platform/PlatformPlanTab.tsx (TenantPlanResponse echo)
  {
    method: "PUT",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/plan$/,
    build: ([, tenantId]) => ({ tenant_id: tenantId, plan: PLATFORM_PLANS[1], seat_cap: 25 }),
  },
  // source: components/platform/PlatformBudgetTab.tsx (budget echo)
  {
    method: "PUT",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/budget$/,
    build: () => ({ budget_usd_monthly: "500.00" }),
  },
  // source: components/platform/PlatformConfigTab.tsx (CacheConfig echo)
  {
    method: "PUT",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/cache$/,
    build: () => ({ enabled: true, semantic_enabled: true }),
  },
  // source: components/platform/PlatformConfigTab.tsx (GuardrailConfig echo)
  {
    method: "PUT",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/guardrails$/,
    build: () => ({
      prompt_injection: { enabled: true, mode: "audit" },
      pii_mask: { enabled: false, mode: "mask", pii_custom_patterns: [] },
    }),
  },
  // source: components/platform/PlatformMembersTab.tsx (PlatformTenantUser echo)
  {
    method: "PUT",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/users\/([^/]+)\/role$/,
    build: ([, , userId]) => ({ id: userId, email: "alan@acme.io", role: "admin" }),
  },
  // source: components/platform/PlatformKeysTab.tsx (CreateKeyResult; `key` included
  // for gateway realism even though the frontend type deliberately omits it — R6)
  {
    method: "POST",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/keys$/,
    build: () => ({ key_id: "kid-plat-new01", name: "new-key", key: "sk-plat-new.a1b2c3d4" }),
  },
  {
    method: "POST",
    pattern: /^admin\/platform\/tenants\/([^/]+)\/keys\/([^/]+)\/rotate$/,
    build: ([, , keyId]) => ({
      new_key_id: "kid-plat-rot01",
      superseded_key_id: keyId,
      name: "production",
      key: "sk-plat-rot.a1b2c3d4",
    }),
  },
  { method: "DELETE", pattern: /^admin\/platform\/tenants\/([^/]+)\/keys\/([^/]+)$/, build: () => null },
];

// ─────────────────────────────────────────────────────────────────────────────
// Entry point
// ─────────────────────────────────────────────────────────────────────────────

export function gwBody(gatewayPath: string, method: string): unknown {
  // Defensive normalization: strip a leading slash and any query string, even
  // though the caller contract says gatewayPath already arrives bare.
  const path = gatewayPath.replace(/^\/+/, "").split("?")[0];
  const upperMethod = method.toUpperCase();

  // Same-origin, non-gw impersonation-status route (contract carve-out) —
  // handled before the gw route table since it never starts with "admin/".
  if (path.startsWith("platform/impersonation")) {
    return { active: false };
  }

  for (const route of ROUTES) {
    if (route.method !== "ANY" && route.method !== upperMethod) continue;
    const match = path.match(route.pattern);
    if (match) return route.build(match);
  }

  return undefined;
}
