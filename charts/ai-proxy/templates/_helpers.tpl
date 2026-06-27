{{/*
Shared naming + label helpers. Sibling templates (envoy→gateway, gateway→datastores,
dashboard→gateway) consume these for stable, deterministic names + the in-cluster
Service DNS. This is the M8 contract.
*/}}

{{- define "ai-proxy.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ai-proxy.gateway.fullname" -}}
{{- printf "%s-gateway" (include "ai-proxy.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Common labels stamped on every object. */}}
{{- define "ai-proxy.labels" -}}
app.kubernetes.io/name: {{ include "ai-proxy.fullname" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* Selector labels for the gateway workload (immutable subset). */}}
{{- define "ai-proxy.gateway.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ai-proxy.fullname" . }}
app.kubernetes.io/component: gateway
{{- end -}}

{{/* In-cluster DNS for the gateway Service — consumed by envoy/dashboard siblings. */}}
{{- define "ai-proxy.gateway.serviceDNS" -}}
{{- printf "%s.%s.svc.cluster.local" (include "ai-proxy.gateway.fullname" .) .Release.Namespace -}}
{{- end -}}

{{/* Resolve the JWT secret name: explicit (trimmed) existingSecret, else a conventional name. */}}
{{- define "ai-proxy.gateway.jwtSecretName" -}}
{{- .Values.gateway.jwtSecret.existingSecret | trim | default (printf "%s-jwt" (include "ai-proxy.gateway.fullname" .)) -}}
{{- end -}}

{{/*
Fail-closed secret guard (R5 secret_ref_missing) — mirrors the gateway's own
_forbid_dev_secret_outside_dev: for ANY environment outside {dev, test}, a JWT secret
MUST be referenced (existingSecret) or chart-created (createSecret). `trim` so a
whitespace-only ref does not pass as set. Call from the gateway Deployment.
*/}}
{{- define "ai-proxy.gateway.validateSecret" -}}
{{- $devEnvs := list "dev" "test" -}}
{{- $ref := .Values.gateway.jwtSecret.existingSecret | trim -}}
{{- if and (not (has .Values.gateway.env.environment $devEnvs)) (not $ref) (not .Values.gateway.jwtSecret.createSecret) -}}
{{- fail "secret_ref_missing: set gateway.jwtSecret.existingSecret or gateway.jwtSecret.createSecret when gateway.env.environment is not dev/test" -}}
{{- end -}}
{{- end -}}

{{/* ── Datastore helpers (v53 datastore-statefulsets) — additive, frozen helpers above untouched ── */}}

{{- define "ai-proxy.postgres.fullname" -}}
{{- printf "%s-postgres" (include "ai-proxy.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "ai-proxy.postgres.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ai-proxy.fullname" . }}
app.kubernetes.io/component: postgres
{{- end -}}

{{- define "ai-proxy.redis.fullname" -}}
{{- printf "%s-redis" (include "ai-proxy.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "ai-proxy.redis.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ai-proxy.fullname" . }}
app.kubernetes.io/component: redis
{{- end -}}

{{- define "ai-proxy.minio.fullname" -}}
{{- printf "%s-minio" (include "ai-proxy.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "ai-proxy.minio.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ai-proxy.fullname" . }}
app.kubernetes.io/component: minio
{{- end -}}

{{/* The datastore credential Secret name — rendered when create=true, referenced otherwise. */}}
{{- define "ai-proxy.datastores.secretName" -}}
{{- .Values.datastores.secrets.name | trim -}}
{{- end -}}

{{/*
Fail-closed datastore secret guard (chart_invalid -> datastore_secret_ref_missing) —
mirrors the gateway's validateSecret. For ANY environment outside {dev, test}, when at
least one datastore is enabled, the credential Secret name MUST be set (rendered or
referenced). `trim` so whitespace does not pass as set. Evaluated from datastore-secret.yaml.
*/}}
{{- define "ai-proxy.datastores.validateSecret" -}}
{{- $devEnvs := list "dev" "test" -}}
{{- $name := .Values.datastores.secrets.name | trim -}}
{{- $anyEnabled := or .Values.datastores.postgres.enabled .Values.datastores.redis.enabled .Values.datastores.minio.enabled -}}
{{- if and (not (has .Values.gateway.env.environment $devEnvs)) $anyEnabled (not $name) -}}
{{- fail "datastore_secret_ref_missing: set datastores.secrets.name (or datastores.secrets.create=true) when a datastore is enabled and gateway.env.environment is not dev/test" -}}
{{- end -}}
{{- end -}}

{{/* ── Envoy edge helpers (v53 envoy-edge-manifests) — additive, frozen helpers above untouched ── */}}

{{- define "ai-proxy.envoy.fullname" -}}
{{- printf "%s-envoy" (include "ai-proxy.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "ai-proxy.envoy.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ai-proxy.fullname" . }}
app.kubernetes.io/component: envoy
{{- end -}}

{{/*
Fail-closed TLS guard (chart_invalid -> tls_secret_ref_missing) — mirrors the gateway/datastore
secret guards. For ANY environment outside {dev, test}, when Envoy is enabled, the TLS Secret
ref MUST be set. `trim` so whitespace does not pass as set. Evaluated from envoy-deployment.yaml.
*/}}
{{- define "ai-proxy.envoy.validateTLS" -}}
{{- $devEnvs := list "dev" "test" -}}
{{- $ref := .Values.envoy.tls.existingSecret | trim -}}
{{- if and .Values.envoy.enabled (not (has .Values.gateway.env.environment $devEnvs)) (not $ref) -}}
{{- fail "tls_secret_ref_missing: set envoy.tls.existingSecret (a kubernetes.io/tls Secret) when envoy.enabled and gateway.env.environment is not dev/test" -}}
{{- end -}}
{{- end -}}

{{/* ── Dashboard helpers (v53 dashboard-chart) — additive, frozen helpers above untouched ── */}}

{{- define "ai-proxy.dashboard.fullname" -}}
{{- printf "%s-dashboard" (include "ai-proxy.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "ai-proxy.dashboard.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ai-proxy.fullname" . }}
app.kubernetes.io/component: dashboard
{{- end -}}

{{/* The BFF→gateway base URL: explicit dashboard.env.gatewayUrl, else the in-cluster gateway Service. */}}
{{- define "ai-proxy.dashboard.gatewayUrl" -}}
{{- $explicit := .Values.dashboard.env.gatewayUrl | trim -}}
{{- if $explicit -}}{{ $explicit }}{{- else -}}{{ printf "http://%s:8000" (include "ai-proxy.gateway.fullname" .) }}{{- end -}}
{{- end -}}
