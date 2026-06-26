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
