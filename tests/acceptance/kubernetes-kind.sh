#!/bin/sh
set -eu
export BUILDX_GIT_INFO=false

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."
cluster=zkdeal-chart-acceptance
namespace=zkdeal-acceptance
node_image=kindest/node:v1.32.2@sha256:f226345927d7e348497136874b6d207e0b32cc52154ad8323129352923a3142f
postgres_image=postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94
minio_image=minio/minio:RELEASE.2025-04-22T22-12-26Z@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e
minio_client_image=minio/mc:RELEASE.2025-04-16T18-13-26Z@sha256:aead63c77f9db9107f1696fb08ecb0faeda23729cde94b0f663edf4fe09728e3
# kind's Docker-image importer does not retain a usable repository name for a
# `repository:tag@digest` reference. Pull by immutable digest, prove an exact
# image-ID match, and expose a deterministic cluster-local alias instead.
postgres_kind_image=zkdeal-kind-postgres:17.6
minio_kind_image=zkdeal-kind-minio:2025-04-22
minio_client_kind_image=zkdeal-kind-minio-client:2025-04-16
owner_kind_image=zkdeal-coordinator:kind
headless_kind_image=zkdeal-headless-room-node:kind
docs_kind_image=zkdeal-operator-docs:kind
backup_kind_image=zkdeal-backup-tools:kind
promotion_kind_image=zkdeal-promotion-controller:kind
provider_kind_image=zkdeal-failover-provider:kind
KIND_CANDIDATE_MODE=${KIND_CANDIDATE_MODE:-0}
KIND_HELM_VALUES_FILE=${KIND_HELM_VALUES_FILE:-tests/fixtures/helm-kind-live.yaml}
OWNER_IMAGE_MAX_BYTES=${OWNER_IMAGE_MAX_BYTES:-1180000000}
KIND_IMAGE_LOAD_TIMEOUT_SECONDS=${KIND_IMAGE_LOAD_TIMEOUT_SECONDS:-1200}
export OWNER_IMAGE_MAX_BYTES

cleanup() {
  kind delete cluster --name "$cluster" >/dev/null 2>&1 || true
}

diagnose() {
  echo "--- kind failure diagnostics ---" >&2
  docker info --format 'docker-memory-bytes={{.MemTotal}} driver={{.Driver}}' >&2 || true
  docker ps -a --filter "name=${cluster}-control-plane" --format '{{json .}}' >&2 || true
  node_id=$(docker ps -aq --filter "name=${cluster}-control-plane" | head -n 1)
  if [ -n "$node_id" ]; then
    docker inspect "$node_id" --format 'state={{json .State}} image={{.Image}}' >&2 || true
    docker logs --tail 300 "$node_id" >&2 || true
    docker exec "$node_id" kubectl --kubeconfig=/etc/kubernetes/admin.conf get --raw='/readyz?verbose' >&2 || true
  fi
  kubectl cluster-info --context "kind-$cluster" >&2 || true
  kubectl get --raw='/readyz?verbose' >&2 || true
  kubectl get nodes,pods -A -o wide >&2 || true
  kubectl get events -A --sort-by=.lastTimestamp >&2 || true
  if kubectl get namespace "$namespace" >/dev/null 2>&1; then
    kubectl -n "$namespace" describe pods >&2 || true
    workload_pods=$(kubectl -n "$namespace" get pods -o name 2>/dev/null || true)
    for workload_pod in $workload_pods; do
      echo "--- logs: ${namespace}/${workload_pod} ---" >&2
      kubectl -n "$namespace" logs "$workload_pod" --all-containers --tail=200 >&2 || true
      echo "--- previous logs: ${namespace}/${workload_pod} ---" >&2
      kubectl -n "$namespace" logs "$workload_pod" --all-containers --previous --tail=200 >&2 || true
    done
  fi
}

configure_container_api_route() {
  api_port=$(docker inspect "${cluster}-control-plane" --format '{{(index (index .NetworkSettings.Ports "6443/tcp") 0).HostPort}}')
  case "$api_port" in
    ''|*[!0-9]*) echo "ERROR: kind API host port is invalid: $api_port" >&2; return 1 ;;
  esac
  # kind writes 127.0.0.1 because the API port is published on the Docker
  # host. Inside this socket-bearing orchestrator, loopback is the orchestrator
  # itself. Route through Docker Desktop while retaining the certificate's
  # 127.0.0.1 SAN as the explicit TLS server name.
  kubectl config set-cluster "kind-$cluster" \
    --server="https://host.docker.internal:${api_port}" \
    --tls-server-name=127.0.0.1 >/dev/null
  wait_api
}

on_exit() {
  result=$?
  trap - EXIT INT TERM
  if [ "$result" -ne 0 ]; then diagnose; fi
  cleanup
  exit "$result"
}

wait_api() {
  attempts=0
  while [ "$attempts" -lt 90 ]; do
    if kubectl get --raw=/readyz >/dev/null 2>&1; then return 0; fi
    node_running=$(docker inspect "${cluster}-control-plane" --format '{{.State.Running}}' 2>/dev/null || printf false)
    [ "$node_running" = true ] || return 1
    attempts=$((attempts + 1))
    sleep 2
  done
  return 1
}

load_image() {
  image=$1
  if ! timeout "$KIND_IMAGE_LOAD_TIMEOUT_SECONDS" kind load docker-image --name "$cluster" "$image"; then
    echo "ERROR: kind image transfer failed for $image within ${KIND_IMAGE_LOAD_TIMEOUT_SECONDS}s; owner size ceiling is ${OWNER_IMAGE_MAX_BYTES} bytes" >&2
    return 1
  fi
  if ! wait_api; then
    echo "ERROR: kind API did not recover after loading $image" >&2
    return 1
  fi
}

pin_kind_alias() {
  source_image=$1
  local_image=$2
  docker pull "$source_image" >/dev/null
  docker tag "$source_image" "$local_image"
  source_id=$(docker image inspect "$source_image" --format '{{.Id}}')
  local_id=$(docker image inspect "$local_image" --format '{{.Id}}')
  if [ -z "$source_id" ] || [ "$source_id" != "$local_id" ]; then
    echo "ERROR: kind-local alias $local_image does not match immutable source $source_image" >&2
    return 1
  fi
  printf 'kind-alias source=%s alias=%s image-id=%s\n' "$source_image" "$local_image" "$source_id"
}

require_digest_image() {
  label=$1
  reference=$2
  PYTHONPATH=scripts python -c \
    'import sys; from production_compose import validate_reference; validate_reference(sys.argv[2], sys.argv[1])' \
    "$label" "$reference"
}

trap on_exit EXIT INT TERM
cleanup

case "$KIND_CANDIDATE_MODE" in
  0)
    # Local acceptance images prove chart mechanics only. Their tags never
    # become release identities.
    OWNER_IMAGE_TAG="$owner_kind_image" sh tests/acceptance/owner-image-budget.sh
    docker build --pull=false -f docs/Dockerfile -t "$docs_kind_image" .
    docker build --pull=false -f tools/backup.Dockerfile -t "$backup_kind_image" .
    ;;
  1)
    # Candidate mode never builds. Each source must be a registry-verifiable
    # digest and the cluster-local alias must resolve to that exact image ID.
    : "${KIND_OWNER_IMAGE:?set exact owner repository@sha256 digest}"
    : "${KIND_HEADLESS_IMAGE:?set exact headless repository@sha256 digest}"
    : "${KIND_DOCS_IMAGE:?set exact docs repository@sha256 digest}"
    : "${KIND_BACKUP_IMAGE:?set exact backup repository@sha256 digest}"
    : "${KIND_PROMOTION_CONTROLLER_IMAGE:?set exact promotion-controller repository@sha256 digest}"
    : "${KIND_FAILOVER_PROVIDER_IMAGE:?set exact failover-provider repository@sha256 digest}"
    [ -f "$KIND_HELM_VALUES_FILE" ] || { echo "ERROR: candidate Helm values file is absent: $KIND_HELM_VALUES_FILE" >&2; exit 1; }
    require_digest_image KIND_OWNER_IMAGE "$KIND_OWNER_IMAGE"
    require_digest_image KIND_HEADLESS_IMAGE "$KIND_HEADLESS_IMAGE"
    require_digest_image KIND_DOCS_IMAGE "$KIND_DOCS_IMAGE"
    require_digest_image KIND_BACKUP_IMAGE "$KIND_BACKUP_IMAGE"
    require_digest_image KIND_PROMOTION_CONTROLLER_IMAGE "$KIND_PROMOTION_CONTROLLER_IMAGE"
    require_digest_image KIND_FAILOVER_PROVIDER_IMAGE "$KIND_FAILOVER_PROVIDER_IMAGE"
    pin_kind_alias "$KIND_OWNER_IMAGE" "$owner_kind_image"
    pin_kind_alias "$KIND_HEADLESS_IMAGE" "$headless_kind_image"
    pin_kind_alias "$KIND_DOCS_IMAGE" "$docs_kind_image"
    pin_kind_alias "$KIND_BACKUP_IMAGE" "$backup_kind_image"
    pin_kind_alias "$KIND_PROMOTION_CONTROLLER_IMAGE" "$promotion_kind_image"
    pin_kind_alias "$KIND_FAILOVER_PROVIDER_IMAGE" "$provider_kind_image"
    for alias in "$owner_kind_image" "$headless_kind_image" "$docs_kind_image" "$backup_kind_image" "$promotion_kind_image" "$provider_kind_image"; do
      grep -F "$alias" "$KIND_HELM_VALUES_FILE" >/dev/null || {
        echo "ERROR: candidate Helm values do not consume exact-ID alias $alias" >&2
        exit 1
      }
    done
    ;;
  *) echo "ERROR: KIND_CANDIDATE_MODE must be 0 or 1" >&2; exit 1 ;;
esac
pin_kind_alias "$postgres_image" "$postgres_kind_image"
pin_kind_alias "$minio_image" "$minio_kind_image"
pin_kind_alias "$minio_client_image" "$minio_client_kind_image"

kind create cluster --name "$cluster" --image "$node_image" --wait 180s
configure_container_api_route
for image in \
  "$owner_kind_image" \
  "$docs_kind_image" \
  "$backup_kind_image" \
  "$postgres_kind_image" \
  "$minio_kind_image" \
  "$minio_client_kind_image"
do
  load_image "$image"
done
if [ "$KIND_CANDIDATE_MODE" = 1 ]; then
  for image in "$headless_kind_image" "$promotion_kind_image" "$provider_kind_image"; do
    load_image "$image"
  done
fi
kubectl create namespace "$namespace"
kubectl -n "$namespace" create secret generic zkdeal-runtime-secrets \
  --from-literal=queue-submit-token=kind-queue-submit-token-32-characters \
  --from-literal=queue-node-token=kind-queue-node-token-32-characters \
  --from-literal=node-service-key=kind-node-service-key-32-characters \
  --from-literal=database-url=postgresql://zkdeal:kind-postgres-password@postgres:5432/zkdeal \
  --from-literal=object-store-endpoint=http://minio:9000 \
  --from-literal=object-store-access-key=kind-minio-admin \
  --from-literal=object-store-secret-key=kind-minio-password \
  --from-literal=backup-object-store-endpoint=http://minio:9000 \
  --from-literal=backup-object-store-access-key=kind-minio-admin \
  --from-literal=backup-object-store-secret-key=kind-minio-password \
  --from-literal=backup-encryption-key=0123456789abcdef0123456789abcdef \
  --from-literal=api-key-pepper=kind-api-key-pepper-at-least-32-characters \
  --from-literal=postgres-password=kind-postgres-password \
  --from-literal=minio-root-user=kind-minio-admin \
  --from-literal=minio-root-password=kind-minio-password

kubectl -n "$namespace" apply -f - <<EOF
apiVersion: v1
kind: Service
metadata: { name: postgres, labels: { app.kubernetes.io/component: postgres } }
spec: { selector: { app.kubernetes.io/component: postgres }, ports: [{ name: postgres, port: 5432, targetPort: 5432 }] }
---
apiVersion: apps/v1
kind: StatefulSet
metadata: { name: postgres, labels: { app.kubernetes.io/component: postgres } }
spec:
  serviceName: postgres
  replicas: 1
  selector: { matchLabels: { app.kubernetes.io/component: postgres } }
  template:
    metadata: { labels: { app.kubernetes.io/component: postgres } }
    spec:
      automountServiceAccountToken: false
      securityContext: { runAsNonRoot: true, runAsUser: 70, runAsGroup: 70, fsGroup: 70, seccompProfile: { type: RuntimeDefault } }
      containers:
        - name: postgres
          image: $postgres_kind_image
          imagePullPolicy: Never
          env:
            - { name: POSTGRES_DB, value: zkdeal }
            - { name: POSTGRES_USER, value: zkdeal }
            - { name: POSTGRES_PASSWORD, valueFrom: { secretKeyRef: { name: zkdeal-runtime-secrets, key: postgres-password } } }
            - { name: PGDATA, value: /var/lib/postgresql/data/pgdata }
          ports: [{ containerPort: 5432 }]
          readinessProbe: { exec: { command: [pg_isready, -U, zkdeal, -d, zkdeal] }, periodSeconds: 3, timeoutSeconds: 2, failureThreshold: 30 }
          securityContext: { allowPrivilegeEscalation: false, capabilities: { drop: [ALL] } }
          volumeMounts: [{ name: data, mountPath: /var/lib/postgresql/data }]
      volumes: [{ name: data, emptyDir: { sizeLimit: 1Gi } }]
---
apiVersion: v1
kind: Service
metadata: { name: minio, labels: { app.kubernetes.io/component: minio } }
spec: { selector: { app.kubernetes.io/component: minio }, ports: [{ name: api, port: 9000, targetPort: 9000 }] }
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: minio, labels: { app.kubernetes.io/component: minio } }
spec:
  replicas: 1
  selector: { matchLabels: { app.kubernetes.io/component: minio } }
  template:
    metadata: { labels: { app.kubernetes.io/component: minio } }
    spec:
      automountServiceAccountToken: false
      securityContext: { runAsNonRoot: true, runAsUser: 1000, runAsGroup: 1000, fsGroup: 1000, seccompProfile: { type: RuntimeDefault } }
      containers:
        - name: minio
          image: $minio_kind_image
          imagePullPolicy: Never
          args: [server, /data]
          env:
            - { name: MINIO_ROOT_USER, valueFrom: { secretKeyRef: { name: zkdeal-runtime-secrets, key: minio-root-user } } }
            - { name: MINIO_ROOT_PASSWORD, valueFrom: { secretKeyRef: { name: zkdeal-runtime-secrets, key: minio-root-password } } }
          ports: [{ containerPort: 9000 }]
          readinessProbe: { httpGet: { path: /minio/health/ready, port: 9000 }, periodSeconds: 3, timeoutSeconds: 2, failureThreshold: 30 }
          securityContext: { allowPrivilegeEscalation: false, capabilities: { drop: [ALL] } }
          volumeMounts: [{ name: data, mountPath: /data }]
      volumes: [{ name: data, emptyDir: { sizeLimit: 1Gi } }]
EOF

kubectl -n "$namespace" rollout status statefulset/postgres --timeout=180s
kubectl -n "$namespace" rollout status deployment/minio --timeout=180s
kubectl -n "$namespace" apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata: { name: minio-init, labels: { app.kubernetes.io/component: minio-init } }
spec:
  backoffLimit: 2
  template:
    metadata: { labels: { app.kubernetes.io/component: minio-init } }
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext: { runAsNonRoot: true, runAsUser: 1000, runAsGroup: 1000, fsGroup: 1000, seccompProfile: { type: RuntimeDefault } }
      containers:
        - name: minio-init
          image: $minio_client_kind_image
          imagePullPolicy: Never
          command: [/bin/sh, -ec]
          env:
            - { name: HOME, value: /tmp }
            - { name: MC_CONFIG_DIR, value: /tmp/mc }
            - { name: MINIO_ROOT_USER, valueFrom: { secretKeyRef: { name: zkdeal-runtime-secrets, key: minio-root-user } } }
            - { name: MINIO_ROOT_PASSWORD, valueFrom: { secretKeyRef: { name: zkdeal-runtime-secrets, key: minio-root-password } } }
          args:
            - >-
              mc alias set local http://minio:9000 "\$MINIO_ROOT_USER" "\$MINIO_ROOT_PASSWORD";
              mc mb --ignore-existing local/zkdeal;
              mc mb --ignore-existing local/zkdeal-backups;
              mc anonymous set none local/zkdeal;
              mc anonymous set none local/zkdeal-backups
          securityContext: { readOnlyRootFilesystem: true, allowPrivilegeEscalation: false, capabilities: { drop: [ALL] } }
          volumeMounts: [{ name: mc-config, mountPath: /tmp }]
      volumes: [{ name: mc-config, emptyDir: { sizeLimit: 16Mi } }]
EOF
kubectl -n "$namespace" wait --for=condition=complete job/minio-init --timeout=180s

kubectl -n "$namespace" apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: { name: zkdeal-coordinator-to-live-storage }
spec:
  podSelector: { matchLabels: { app.kubernetes.io/instance: zkdeal, app.kubernetes.io/component: coordinator-active } }
  policyTypes: [Egress]
  egress:
    - to: [{ podSelector: { matchLabels: { app.kubernetes.io/component: postgres } } }]
      ports: [{ protocol: TCP, port: 5432 }]
    - to: [{ podSelector: { matchLabels: { app.kubernetes.io/component: minio } } }]
      ports: [{ protocol: TCP, port: 9000 }]
EOF

helm install zkdeal helm/zkdeal --namespace "$namespace" \
  -f "$KIND_HELM_VALUES_FILE" \
  --wait --timeout 5m

required_deployments="coordinator-active docs"
current_owner_workers=false
if [ "$KIND_CANDIDATE_MODE" = 1 ]; then
  required_deployments="$required_deployments coordinator-standby indexer reconciler publisher headless-node capacity-controller auto-claimer promotion-controller failover-provider"
  current_owner_workers=true
fi
for deployment in $required_deployments; do
  kubectl -n "$namespace" get "deployment/zkdeal-$deployment" >/dev/null
  kubectl -n "$namespace" rollout status "deployment/zkdeal-$deployment" --timeout=180s
done
[ "$(kubectl -n "$namespace" get networkpolicy -o name | wc -l | tr -d ' ')" -ge 4 ] || { echo "expected least-privilege NetworkPolicies" >&2; exit 1; }
[ "$(kubectl -n "$namespace" get poddisruptionbudget -o name | wc -l | tr -d ' ')" -ge 1 ] || { echo "expected active-writer PDB" >&2; exit 1; }
[ "$(kubectl -n "$namespace" get horizontalpodautoscaler -o name | wc -l | tr -d ' ')" -ge 1 ] || { echo "expected HPA" >&2; exit 1; }
[ "$(kubectl -n "$namespace" get cronjob -o name | wc -l | tr -d ' ')" -ge 1 ] || { echo "expected backup CronJob" >&2; exit 1; }

kubectl -n "$namespace" get deployment,cronjob -o json | python -c '
import json,sys
doc=json.load(sys.stdin)
refs=[]
for item in doc["items"]:
    spec=item["spec"].get("template") or item["spec"]["jobTemplate"]["spec"]["template"]
    for container in spec["spec"].get("containers",[]):
        for env in container.get("env",[]):
            ref=env.get("valueFrom",{}).get("secretKeyRef")
            if ref: refs.append(ref)
assert refs and all(ref.get("name")=="zkdeal-runtime-secrets" and ref.get("key") for ref in refs)
'

kubectl -n "$namespace" patch deployment zkdeal-docs --type merge \
  -p '{"spec":{"template":{"metadata":{"annotations":{"zkdeal.io/acceptance-rollout":"1"}}}}}' >/dev/null
kubectl -n "$namespace" rollout status deployment/zkdeal-docs --timeout=180s
helm uninstall zkdeal --namespace "$namespace" --wait
[ -z "$(kubectl -n "$namespace" get all -l app.kubernetes.io/instance=zkdeal -o name)" ] || { echo "Helm uninstall left workload resources" >&2; exit 1; }

printf '{"kind":"passed","mode":"%s","kubernetes":"v1.32.2","nodeImage":"%s","ownerCoordinator":true,"currentOwnerWorkers":%s,"conformanceWorkloads":false,"postgres":true,"minio":true,"chartInstall":true,"networkPolicies":true,"pdb":true,"hpa":true,"backupCronJob":true,"secretRefs":true,"rollingUpdate":true,"uninstall":true}\n' "$KIND_CANDIDATE_MODE" "$node_image" "$current_owner_workers"
