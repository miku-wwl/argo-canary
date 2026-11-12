{{- define "demo.name" -}}
{{- .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}

{{- define "demo.labels" -}}
app.kubernetes.io/name: demo-app
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "demo.selector" -}}
app: {{ include "demo.name" . }}
{{- end -}}

{{- define "demo.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- end -}}

{{- define "demo.analysis" -}}
templates:
  - templateName: {{ include "demo.name" . }}-health
args:
  - name: namespace
    value: {{ .Release.Namespace }}
  - name: latest-hash
    valueFrom:
      podTemplateHashValue: Latest
{{- end -}}
