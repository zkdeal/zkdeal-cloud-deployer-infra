{{- define "zkdeal.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "zkdeal.fullname" -}}
{{- if .Values.fullnameOverride }}{{ .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}{{ printf "%s-%s" .Release.Name (include "zkdeal.name" .) | trunc 63 | trimSuffix "-" }}{{- end }}
{{- end }}

{{- define "zkdeal.labels" -}}
app.kubernetes.io/name: {{ include "zkdeal.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "zkdeal.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}{{ default (include "zkdeal.fullname" .) .Values.serviceAccount.name }}{{ else }}{{ default "default" .Values.serviceAccount.name }}{{ end }}
{{- end }}

{{- define "zkdeal.image" -}}
{{- $image := . -}}
{{- if $image.digest -}}{{ printf "%s@%s" $image.repository $image.digest }}{{- else -}}{{ printf "%s:%s" $image.repository $image.tag }}{{- end -}}
{{- end }}

