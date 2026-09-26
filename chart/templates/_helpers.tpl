{{/*
Common labels for every resource.
*/}}
{{- define "assistant.labels" -}}
app.kubernetes.io/part-of: enterprise-voice-avatar-assistant
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/*
Labels for one component. Call with (dict "root" $ "name" "<component>").
*/}}
{{- define "assistant.componentLabels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/component: {{ .name }}
{{ include "assistant.labels" .root }}
{{- end -}}

{{/*
Selector labels for one component. Must stay stable across chart versions.
*/}}
{{- define "assistant.selector" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end -}}

{{/*
Container security context compatible with the restricted SCC.
*/}}
{{- define "assistant.securityContext" -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop:
    - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/*
Deterministic Route host when global.domain is set, else empty.
Call with (dict "root" $ "name" "<route name>").
*/}}
{{- define "assistant.host" -}}
{{- if .root.Values.global.domain -}}
{{ printf "%s-%s.%s" .name .root.Release.Namespace .root.Values.global.domain }}
{{- end -}}
{{- end -}}

{{/*
Public host: explicit override, else computed. Call with (dict "root" $ "name" "<route>" "override" "<value>").
*/}}
{{- define "assistant.publicHost" -}}
{{- if .override -}}
{{ .override }}
{{- else -}}
{{ include "assistant.host" . }}
{{- end -}}
{{- end -}}

{{/*
Image reference for an application component. Call with (dict "root" $ "name" "<component>" "override" "<image>").
*/}}
{{- define "assistant.image" -}}
{{- if .override -}}
{{ .override }}
{{- else -}}
{{ printf "%s/enterprise-voice-avatar-assistant-%s:%s" .root.Values.images.registry .name .root.Values.images.tag }}
{{- end -}}
{{- end -}}

{{/*
OpenAI-compatible base URL for a model. Call with (dict "root" $ "key" "llm|stt|embeddings|guardrails|tts").
In-cluster KServe raw deployments expose <name>-predictor on port 8080.
*/}}
{{- define "assistant.modelEndpoint" -}}
{{- $m := index .root.Values.models .key -}}
{{- if eq .key "tts" -}}
{{- if $m.deploy -}}http://tts:8880/v1{{- else -}}{{ $m.endpoint }}{{- end -}}
{{- else if $m.deploy -}}
http://{{ $m.name }}-predictor.{{ .root.Release.Namespace }}.svc.cluster.local:8080/v1
{{- else -}}
{{ $m.endpoint }}
{{- end -}}
{{- end -}}

{{/*
Environment shared by the n8n server and the workflow-import init container: the
same database, encryption key, public URL and service URLs. Pass the root context.
*/}}
{{- define "assistant.n8nEnv" -}}
{{- $host := include "assistant.publicHost" (dict "root" . "name" "n8n" "override" .Values.n8n.publicHost) -}}
# Runs under an OpenShift-assigned UID: keep all writable state on the volume.
- name: N8N_USER_FOLDER
  value: /data
- name: HOME
  value: /data
- name: N8N_PORT
  value: "5678"
- name: N8N_PROTOCOL
  value: https
{{- if $host }}
- name: N8N_HOST
  value: {{ $host | quote }}
- name: WEBHOOK_URL
  value: https://{{ $host }}/
- name: N8N_EDITOR_BASE_URL
  value: https://{{ $host }}/
{{- end }}
- name: N8N_PROXY_HOPS
  value: "1"
- name: N8N_ENCRYPTION_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.n8n }}
      key: N8N_ENCRYPTION_KEY
- name: DB_TYPE
  value: postgresdb
- name: DB_POSTGRESDB_HOST
  value: postgres
- name: DB_POSTGRESDB_PORT
  value: "5432"
- name: DB_POSTGRESDB_SCHEMA
  value: n8n
- name: DB_POSTGRESDB_DATABASE
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.postgres }}
      key: POSTGRESQL_DATABASE
- name: DB_POSTGRESDB_USER
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.postgres }}
      key: POSTGRESQL_USER
- name: DB_POSTGRESDB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.postgres }}
      key: POSTGRESQL_PASSWORD
- name: N8N_RUNNERS_ENABLED
  value: "true"
- name: N8N_BLOCK_ENV_ACCESS_IN_NODE
  value: "false"
- name: N8N_DIAGNOSTICS_ENABLED
  value: "false"
- name: N8N_TEMPLATES_ENABLED
  value: "true"
- name: N8N_SECURE_COOKIE
  value: "true"
- name: N8N_PAYLOAD_SIZE_MAX
  value: {{ .Values.n8n.payloadSizeMaxMb | quote }}
- name: GENERIC_TIMEZONE
  value: {{ .Values.n8n.timezone | quote }}
- name: TZ
  value: {{ .Values.n8n.timezone | quote }}
# Service URLs the exported workflows read through $env
- name: RAG_API_URL
  value: http://rag-api:8080
- name: INGESTION_URL
  value: http://ingestion:8080
# Bearer token the workflows send to the RAG API and the ingestion service
- name: INTERNAL_API_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.admin }}
      key: INTERNAL_API_TOKEN
# The workflows post to Slack only when it is on (an IF node in front of every Slack node)
- name: SLACK_ENABLED
  value: {{ .Values.integrations.slack.enabled | quote }}
# WF4 verifies the signature of Slack clicks (SLACK_SIGNING_SECRET) with node's crypto module
- name: NODE_FUNCTION_ALLOW_BUILTIN
  value: crypto
- name: S3_ENDPOINT_URL
  value: http://object-store:7070
# The buckets whose new objects WF2 ingests or classifies (objectStore.eventBuckets)
- name: EVENT_BUCKETS
  value: {{ join "," .Values.objectStore.eventBuckets | quote }}
- name: NODE_EXTRA_CA_CERTS
  value: /var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt
{{- range $k, $v := .Values.n8n.extraEnv }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}
