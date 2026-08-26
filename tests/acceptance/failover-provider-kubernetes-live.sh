#!/bin/sh
set -eu
export BUILDX_GIT_INFO=false

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."

cluster=zkdeal-provider-kubernetes-live
namespace=failover-live
node_image=kindest/node:v1.32.2@sha256:f226345927d7e348497136874b6d207e0b32cc52154ad8323129352923a3142f
postgres_source=${POSTGRES_CANDIDATE_IMAGE:-postgres@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94}
provider_source=${FAILOVER_PROVIDER_CANDIDATE_IMAGE:-}
postgres_image=zkdeal-provider-kind-postgres:17.6
provider_image=zkdeal-failover-provider:kubernetes-live
fixture_image=zkdeal-failover-platform-fixture:kubernetes-live
provider_port=18443
port_forward_pid=
candidate_mode=false

require_digest_image() {
  image_label=$1
  image_reference=$2
  PYTHONPATH="$script_dir/../../scripts" IMAGE_LABEL="$image_label" IMAGE_REFERENCE="$image_reference" python -c \
    'import os; from production_compose import validate_reference; validate_reference(os.environ["IMAGE_REFERENCE"], os.environ["IMAGE_LABEL"])'
}

cleanup() {
  if [ -n "$port_forward_pid" ]; then kill "$port_forward_pid" >/dev/null 2>&1 || true; fi
  kind delete cluster --name "$cluster" >/dev/null 2>&1 || true
}

diagnose() {
  echo '--- Kubernetes failover-provider diagnostics ---' >&2
  docker info --format 'docker-memory-bytes={{.MemTotal}} driver={{.Driver}}' >&2 || true
  docker ps -a --filter "name=${cluster}-control-plane" --format '{{json .}}' >&2 || true
  node_id=$(docker ps -aq --filter "name=${cluster}-control-plane" | head -n 1)
  if [ -n "$node_id" ]; then
    docker inspect "$node_id" --format 'state={{json .State}} image={{.Image}}' >&2 || true
    docker logs --tail 200 "$node_id" >&2 || true
  fi
  kubectl get nodes,pods -A -o wide >&2 || true
  kubectl get events -A --sort-by=.lastTimestamp >&2 || true
  kubectl -n "$namespace" describe pods >&2 || true
  for pod in $(kubectl -n "$namespace" get pods -o name 2>/dev/null || true); do
    echo "--- logs: $pod ---" >&2
    kubectl -n "$namespace" logs "$pod" --all-containers --tail=200 >&2 || true
    kubectl -n "$namespace" logs "$pod" --all-containers --previous --tail=200 >&2 || true
  done
}

on_exit() {
  result=$?
  trap - EXIT INT TERM
  if [ "$result" -ne 0 ]; then diagnose; fi
  cleanup
  exit "$result"
}
trap on_exit EXIT INT TERM

wait_api() {
  attempts=0
  while [ "$attempts" -lt 90 ]; do
    if kubectl get --raw=/readyz >/dev/null 2>&1; then return 0; fi
    attempts=$((attempts + 1))
    sleep 2
  done
  return 1
}

configure_container_api_route() {
  api_port=$(docker inspect "${cluster}-control-plane" --format '{{(index (index .NetworkSettings.Ports "6443/tcp") 0).HostPort}}')
  case "$api_port" in ''|*[!0-9]*) echo "invalid kind API port: $api_port" >&2; return 1 ;; esac
  kubectl config set-cluster "kind-$cluster" \
    --server="https://host.docker.internal:${api_port}" \
    --tls-server-name=127.0.0.1 >/dev/null
  wait_api
}

load_image() {
  timeout 300 kind load docker-image --name "$cluster" "$1"
  wait_api
}

start_port_forward() {
  if [ -n "$port_forward_pid" ]; then kill "$port_forward_pid" >/dev/null 2>&1 || true; fi
  kubectl -n "$namespace" port-forward service/failover-provider "${provider_port}:8443" >/tmp/zkdeal-provider-port-forward.log 2>&1 &
  port_forward_pid=$!
  export FAILOVER_PROVIDER_ACCEPTANCE_URL="http://127.0.0.1:${provider_port}"
  attempts=0
  while [ "$attempts" -lt 60 ]; do
    if python -c "import urllib.request; urllib.request.urlopen('${FAILOVER_PROVIDER_ACCEPTANCE_URL}/ready',timeout=2).read()" >/dev/null 2>&1; then return 0; fi
    kill -0 "$port_forward_pid" >/dev/null 2>&1 || {
      cat /tmp/zkdeal-provider-port-forward.log >&2 || true
      return 1
    }
    attempts=$((attempts + 1))
    sleep 1
  done
  return 1
}

cleanup
if [ -n "$provider_source" ]; then
  : "${POSTGRES_CANDIDATE_IMAGE:?candidate provider mode requires POSTGRES_CANDIDATE_IMAGE}"
  require_digest_image FAILOVER_PROVIDER_CANDIDATE_IMAGE "$provider_source"
  require_digest_image POSTGRES_CANDIDATE_IMAGE "$postgres_source"
  docker pull "$provider_source" >/dev/null
  docker tag "$provider_source" "$provider_image"
  candidate_mode=true
else
  docker build --pull=false -f failover-provider/Dockerfile -t "$provider_image" .
  provider_source=$provider_image
fi
docker build --pull=false -f tests/fixtures/FailoverPlatform.Dockerfile -t "$fixture_image" .
docker pull "$postgres_source" >/dev/null
docker tag "$postgres_source" "$postgres_image"
[ "$(docker image inspect "$postgres_source" --format '{{.Id}}')" = "$(docker image inspect "$postgres_image" --format '{{.Id}}')" ]
[ "$(docker image inspect "$provider_source" --format '{{.Id}}')" = "$(docker image inspect "$provider_image" --format '{{.Id}}')" ]

kind create cluster --name "$cluster" --image "$node_image" --wait 180s
configure_container_api_route
for image in "$postgres_image" "$fixture_image" "$provider_image"; do load_image "$image"; done

kubectl create namespace "$namespace"
kubectl -n "$namespace" create secret generic failover-secrets \
  --from-literal=replication-password=kind-replication-password-32-chars \
  --from-literal=provider-token=acceptance-provider-token-32-characters \
  --from-literal=approval-token=acceptance-approval-token-32-characters
kubectl -n "$namespace" create configmap primary-init \
  --from-file=10-replication.sh=failover-provider/postgres/primary-init.sh
kubectl -n "$namespace" create configmap standby-entrypoint \
  --from-file=standby-entrypoint.sh=failover-provider/postgres/standby-entrypoint.sh

kubectl -n "$namespace" apply -f - <<EOF
apiVersion: v1
kind: Service
metadata: { name: postgresql-primary }
spec:
  selector: { app.kubernetes.io/instance: failover-live, zkdeal.io/failover-target: active }
  ports: [{ name: postgres, port: 5432, targetPort: 5432 }]
---
apiVersion: v1
kind: Service
metadata: { name: postgresql-standby }
spec:
  selector: { app.kubernetes.io/instance: failover-live, zkdeal.io/failover-target: standby }
  ports: [{ name: postgres, port: 5432, targetPort: 5432 }]
---
apiVersion: v1
kind: Service
metadata: { name: postgresql-writer }
spec:
  selector: { app.kubernetes.io/instance: failover-live, zkdeal.io/failover-target: active }
  ports: [{ name: postgres, port: 5432, targetPort: 5432 }]
---
apiVersion: apps/v1
kind: StatefulSet
metadata: { name: postgresql-primary }
spec:
  serviceName: postgresql-primary
  replicas: 1
  selector: { matchLabels: { app.kubernetes.io/instance: failover-live, zkdeal.io/failover-target: active } }
  template:
    metadata: { labels: { app.kubernetes.io/instance: failover-live, zkdeal.io/failover-target: active } }
    spec:
      automountServiceAccountToken: false
      securityContext: { runAsNonRoot: true, runAsUser: 70, runAsGroup: 70, fsGroup: 70, seccompProfile: { type: RuntimeDefault } }
      containers:
        - name: postgres
          image: $postgres_image
          imagePullPolicy: Never
          args: ["-c", "wal_level=replica", "-c", "max_wal_senders=10", "-c", "max_replication_slots=10", "-c", "synchronous_commit=remote_apply", "-c", "wal_keep_size=128MB"]
          env:
            - { name: POSTGRES_DB, value: zkdeal }
            - { name: POSTGRES_USER, value: zkdeal }
            - { name: POSTGRES_PASSWORD, value: kind-postgres-password-32-chars }
            - { name: PGDATA, value: /var/lib/postgresql/data/pgdata }
            - { name: REPLICATION_PASSWORD_FILE, value: /run/secrets/replication-password }
          ports: [{ name: postgres, containerPort: 5432 }]
          readinessProbe: { exec: { command: [pg_isready, -U, zkdeal, -d, zkdeal] }, periodSeconds: 2, timeoutSeconds: 2, failureThreshold: 90 }
          securityContext: { allowPrivilegeEscalation: false, capabilities: { drop: [ALL] } }
          volumeMounts:
            - { name: data, mountPath: /var/lib/postgresql/data }
            - { name: replication-secret, mountPath: /run/secrets, readOnly: true }
            - { name: primary-init, mountPath: /docker-entrypoint-initdb.d/10-replication.sh, subPath: 10-replication.sh, readOnly: true }
      volumes:
        - { name: data, emptyDir: { sizeLimit: 512Mi } }
        - { name: replication-secret, secret: { secretName: failover-secrets, defaultMode: 0440, items: [{ key: replication-password, path: replication-password }] } }
        - { name: primary-init, configMap: { name: primary-init, defaultMode: 0555 } }
---
apiVersion: apps/v1
kind: StatefulSet
metadata: { name: postgresql-standby }
spec:
  serviceName: postgresql-standby
  replicas: 1
  selector: { matchLabels: { app.kubernetes.io/instance: failover-live, zkdeal.io/failover-target: standby } }
  template:
    metadata: { labels: { app.kubernetes.io/instance: failover-live, zkdeal.io/failover-target: standby } }
    spec:
      automountServiceAccountToken: false
      securityContext: { runAsNonRoot: true, runAsUser: 70, runAsGroup: 70, fsGroup: 70, seccompProfile: { type: RuntimeDefault } }
      containers:
        - name: postgres
          image: $postgres_image
          imagePullPolicy: Never
          command: [/opt/zkdeal/standby-entrypoint.sh]
          env:
            - { name: PRIMARY_HOST, value: postgresql-primary }
            - { name: REPLICATION_SLOT, value: kind_standby }
            - { name: REPLICATION_PASSWORD_FILE, value: /run/secrets/replication-password }
            - { name: POSTGRES_DB, value: zkdeal }
            - { name: POSTGRES_USER, value: zkdeal }
            - { name: POSTGRES_PASSWORD, value: kind-postgres-password-32-chars }
            - { name: PGAPPNAME, value: kind_standby }
            - { name: PGDATA, value: /var/lib/postgresql/data/pgdata }
          ports: [{ name: postgres, containerPort: 5432 }]
          readinessProbe: { exec: { command: [pg_isready, -U, zkdeal, -d, zkdeal] }, periodSeconds: 2, timeoutSeconds: 2, failureThreshold: 90 }
          securityContext: { allowPrivilegeEscalation: false, capabilities: { drop: [ALL] } }
          volumeMounts:
            - { name: data, mountPath: /var/lib/postgresql/data }
            - { name: replication-secret, mountPath: /run/secrets, readOnly: true }
            - { name: standby-entrypoint, mountPath: /opt/zkdeal/standby-entrypoint.sh, subPath: standby-entrypoint.sh, readOnly: true }
      volumes:
        - { name: data, emptyDir: { sizeLimit: 512Mi } }
        - { name: replication-secret, secret: { secretName: failover-secrets, defaultMode: 0440, items: [{ key: replication-password, path: replication-password }] } }
        - { name: standby-entrypoint, configMap: { name: standby-entrypoint, defaultMode: 0555 } }
EOF

kubectl -n "$namespace" rollout status statefulset/postgresql-primary --timeout=180s
kubectl -n "$namespace" rollout status statefulset/postgresql-standby --timeout=180s
kubectl -n "$namespace" exec postgresql-primary-0 -c postgres -- \
  psql -U zkdeal -d zkdeal --set ON_ERROR_STOP=1 -c "ALTER SYSTEM SET synchronous_standby_names='*';" -c 'SELECT pg_reload_conf();'
attempts=0
while [ "$attempts" -lt 60 ]; do
  sync_state=$(kubectl -n "$namespace" exec postgresql-primary-0 -c postgres -- \
    psql -U zkdeal -d zkdeal -tAc "select coalesce(max(sync_state),'') from pg_stat_replication" | tr -d '\r')
  [ "$sync_state" = sync ] && break
  attempts=$((attempts + 1))
  sleep 1
done
[ "$sync_state" = sync ] || { echo "standby never became synchronous: $sync_state" >&2; exit 1; }

kubectl -n "$namespace" apply -f - <<EOF
apiVersion: v1
kind: Service
metadata: { name: witness-a }
spec: { selector: { app: witness-a }, ports: [{ name: http, port: 8080, targetPort: 8080 }] }
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: witness-a }
spec:
  replicas: 1
  selector: { matchLabels: { app: witness-a } }
  template:
    metadata: { labels: { app: witness-a } }
    spec:
      automountServiceAccountToken: false
      containers:
        - name: fixture
          image: $fixture_image
          imagePullPolicy: Never
          env: [{ name: FIXTURE_ROLE, value: witness }, { name: FIXTURE_COORDINATOR_ID, value: kind-active-coordinator }, { name: FIXTURE_HEALTHY, value: "false" }]
          readinessProbe: { httpGet: { path: /identity, port: 8080 }, periodSeconds: 2 }
          securityContext: { runAsNonRoot: true, runAsUser: 65532, runAsGroup: 65532, allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: [ALL] } }
---
apiVersion: v1
kind: Service
metadata: { name: witness-b }
spec: { selector: { app: witness-b }, ports: [{ name: http, port: 8080, targetPort: 8080 }] }
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: witness-b }
spec:
  replicas: 1
  selector: { matchLabels: { app: witness-b } }
  template:
    metadata: { labels: { app: witness-b } }
    spec:
      automountServiceAccountToken: false
      containers:
        - name: fixture
          image: $fixture_image
          imagePullPolicy: Never
          env: [{ name: FIXTURE_ROLE, value: witness }, { name: FIXTURE_COORDINATOR_ID, value: kind-active-coordinator }, { name: FIXTURE_HEALTHY, value: "true" }]
          readinessProbe: { httpGet: { path: /identity, port: 8080 }, periodSeconds: 2 }
          securityContext: { runAsNonRoot: true, runAsUser: 65532, runAsGroup: 65532, allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: [ALL] } }
---
apiVersion: v1
kind: Service
metadata: { name: coordinator-standby }
spec: { selector: { app: coordinator-standby }, ports: [{ name: http, port: 8080, targetPort: 8080 }] }
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: coordinator-standby }
spec:
  replicas: 1
  selector: { matchLabels: { app: coordinator-standby } }
  template:
    metadata: { labels: { app: coordinator-standby, app.kubernetes.io/instance: failover-live, zkdeal.io/failover-target: standby } }
    spec:
      automountServiceAccountToken: false
      containers:
        - name: fixture
          image: $fixture_image
          imagePullPolicy: Never
          env: [{ name: FIXTURE_ROLE, value: standby }, { name: FIXTURE_COORDINATOR_ID, value: kind-standby-coordinator }]
          readinessProbe: { httpGet: { path: /identity, port: 8080 }, periodSeconds: 2 }
          securityContext: { runAsNonRoot: true, runAsUser: 65532, runAsGroup: 65532, allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: [ALL] } }
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: coordinator-active }
spec:
  replicas: 1
  selector: { matchLabels: { app: coordinator-active } }
  template:
    metadata: { labels: { app: coordinator-active, app.kubernetes.io/instance: failover-live, zkdeal.io/failover-target: active } }
    spec:
      automountServiceAccountToken: false
      containers:
        - name: fixture
          image: $fixture_image
          imagePullPolicy: Never
          env: [{ name: FIXTURE_ROLE, value: active }, { name: FIXTURE_COORDINATOR_ID, value: kind-active-coordinator }, { name: FIXTURE_HEALTHY, value: "true" }]
          readinessProbe: { httpGet: { path: /identity, port: 8080 }, periodSeconds: 2 }
          securityContext: { runAsNonRoot: true, runAsUser: 65532, runAsGroup: 65532, allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: [ALL] } }
---
apiVersion: v1
kind: Service
metadata: { name: coordinator-writer }
spec:
  selector: { app.kubernetes.io/instance: failover-live, zkdeal.io/failover-target: active }
  ports: [{ name: http, port: 8080, targetPort: 8080 }]
---
apiVersion: v1
kind: Service
metadata: { name: indexer }
spec: { selector: { app: indexer }, ports: [{ name: http, port: 8080, targetPort: 8080 }] }
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: indexer }
spec:
  replicas: 1
  selector: { matchLabels: { app: indexer } }
  template:
    metadata: { labels: { app: indexer } }
    spec:
      automountServiceAccountToken: false
      containers:
        - name: fixture
          image: $fixture_image
          imagePullPolicy: Never
          env: [{ name: FIXTURE_ROLE, value: indexer }]
          readinessProbe: { httpGet: { path: /freshness, port: 8080 }, periodSeconds: 2 }
          securityContext: { runAsNonRoot: true, runAsUser: 65532, runAsGroup: 65532, allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: [ALL] } }
---
apiVersion: v1
kind: Service
metadata: { name: standby-signer-authority }
spec: { selector: { app: standby-signer-authority }, ports: [{ name: http, port: 8080, targetPort: 8080 }] }
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: standby-signer-authority }
spec:
  replicas: 0
  selector: { matchLabels: { app: standby-signer-authority } }
  template:
    metadata: { labels: { app: standby-signer-authority } }
    spec:
      automountServiceAccountToken: false
      containers:
        - name: fixture
          image: $fixture_image
          imagePullPolicy: Never
          env: [{ name: FIXTURE_ROLE, value: signer }]
          readinessProbe: { httpGet: { path: /health, port: 8080 }, periodSeconds: 2 }
          securityContext: { runAsNonRoot: true, runAsUser: 65532, runAsGroup: 65532, allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: [ALL] } }
EOF

for deployment in witness-a witness-b coordinator-active coordinator-standby indexer; do
  kubectl -n "$namespace" rollout status "deployment/$deployment" --timeout=120s
done

kubectl -n "$namespace" apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata: { name: failover-provider }
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: { name: failover-provider }
rules:
  - { apiGroups: [apps], resources: [deployments], resourceNames: [coordinator-active, standby-signer-authority], verbs: [get, patch] }
  - { apiGroups: [apps], resources: [deployments/scale], resourceNames: [coordinator-active, standby-signer-authority], verbs: [get, patch] }
  - { apiGroups: [apps], resources: [statefulsets], resourceNames: [postgresql-primary], verbs: [get, patch] }
  - { apiGroups: [apps], resources: [statefulsets/scale], resourceNames: [postgresql-primary], verbs: [get, patch] }
  - { apiGroups: [""], resources: [pods], resourceNames: [postgresql-primary-0, postgresql-standby-0], verbs: [get] }
  - { apiGroups: [""], resources: [pods/exec], resourceNames: [postgresql-primary-0, postgresql-standby-0], verbs: [create] }
  - { apiGroups: [""], resources: [services], resourceNames: [postgresql-writer, coordinator-writer], verbs: [get, patch] }
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: { name: failover-provider }
subjects: [{ kind: ServiceAccount, name: failover-provider, namespace: $namespace }]
roleRef: { apiGroup: rbac.authorization.k8s.io, kind: Role, name: failover-provider }
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata: { name: failover-provider-state }
spec:
  accessModes: [ReadWriteOnce]
  resources: { requests: { storage: 32Mi } }
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: failover-provider }
spec:
  replicas: 1
  strategy: { type: Recreate }
  selector: { matchLabels: { app: failover-provider } }
  template:
    metadata: { labels: { app: failover-provider } }
    spec:
      serviceAccountName: failover-provider
      securityContext: { runAsNonRoot: true, runAsUser: 65532, runAsGroup: 65532, fsGroup: 65532, seccompProfile: { type: RuntimeDefault } }
      containers:
        - name: failover-provider
          image: $provider_image
          imagePullPolicy: Never
          env:
            - { name: ZKDEAL_VERIFIED_CONTAINER, value: "1" }
            - { name: FAILOVER_PLATFORM, value: kubernetes }
            - { name: FAILOVER_PROVIDER_ALLOW_INSECURE_HTTP, value: acceptance-only }
            - { name: ACTIVE_HEALTH_URLS, value: "http://witness-a:8080/health,http://witness-b:8080/health" }
            - { name: STANDBY_HEALTH_URL, value: http://coordinator-standby:8080/health }
            - { name: INDEXER_FRESHNESS_URL, value: http://indexer:8080/freshness }
            - { name: SIGNER_AUTHORITY_HEALTH_URLS, value: http://standby-signer-authority:8080/health }
            - { name: ACTIVE_COORDINATOR_ID, value: kind-active-coordinator }
            - { name: STANDBY_COORDINATOR_ID, value: kind-standby-coordinator }
            - { name: FAILOVER_PROVIDER_TOKEN_FILE, value: /run/zkdeal-provider/secrets/provider-token }
            - { name: FAILOVER_APPROVAL_TOKEN_FILE, value: /run/zkdeal-provider/secrets/approval-token }
            - { name: FAILOVER_PROVIDER_STATE_PATH, value: /state/state.json }
            - { name: PLATFORM_TIMEOUT_SECONDS, value: "120" }
            - { name: K8S_FAILOVER_NAMESPACE, value: $namespace }
            - { name: K8S_ACTIVE_DEPLOYMENT, value: coordinator-active }
            - { name: K8S_STANDBY_DEPLOYMENT, value: coordinator-standby }
            - { name: K8S_PRIMARY_DB_STATEFULSET, value: postgresql-primary }
            - { name: K8S_PRIMARY_DB_POD, value: postgresql-primary-0 }
            - { name: K8S_STANDBY_DB_POD, value: postgresql-standby-0 }
            - { name: K8S_POSTGRES_CONTAINER, value: postgres }
            - { name: K8S_DATABASE_SERVICE, value: postgresql-writer }
            - { name: K8S_APPLICATION_SERVICE, value: coordinator-writer }
            - { name: K8S_SIGNER_DEPLOYMENT, value: standby-signer-authority }
            - { name: K8S_ROUTE_SCOPE_LABEL_KEY, value: app.kubernetes.io/instance }
            - { name: K8S_ROUTE_SCOPE_LABEL_VALUE, value: failover-live }
            - { name: K8S_ROUTE_LABEL_KEY, value: zkdeal.io/failover-target }
            - { name: K8S_STANDBY_ROUTE_VALUE, value: standby }
            - { name: FAILOVER_PGUSER, value: zkdeal }
            - { name: FAILOVER_PGDATABASE, value: zkdeal }
            - { name: FAILOVER_PGDATA, value: /var/lib/postgresql/data/pgdata }
          ports: [{ name: http, containerPort: 8443 }]
          readinessProbe: { httpGet: { path: /ready, port: 8443 }, periodSeconds: 2, failureThreshold: 60 }
          livenessProbe: { httpGet: { path: /health, port: 8443 }, timeoutSeconds: 2, periodSeconds: 5, failureThreshold: 12 }
          securityContext: { runAsNonRoot: true, runAsUser: 65532, runAsGroup: 65532, allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: [ALL] } }
          volumeMounts: [{ name: state, mountPath: /state }, { name: secrets, mountPath: /run/zkdeal-provider/secrets, readOnly: true }, { name: tmp, mountPath: /tmp }]
      volumes:
        - { name: state, persistentVolumeClaim: { claimName: failover-provider-state } }
        - { name: secrets, secret: { secretName: failover-secrets, defaultMode: 0440, items: [{ key: provider-token, path: provider-token }, { key: approval-token, path: approval-token }] } }
        - { name: tmp, emptyDir: { sizeLimit: 16Mi } }
---
apiVersion: v1
kind: Service
metadata: { name: failover-provider }
spec: { selector: { app: failover-provider }, ports: [{ name: http, port: 8443, targetPort: 8443 }] }
EOF

if ! kubectl -n "$namespace" rollout status deployment/failover-provider --timeout=90s; then
  kubectl -n "$namespace" get pod -l app=failover-provider -o jsonpath='{range .items[*]}{.metadata.name}{" state="}{.status.containerStatuses[0].state}{" last="}{.status.containerStatuses[0].lastState}{" message="}{.status.containerStatuses[0].state.terminated.message}{"\n"}{end}' >&2 || true
  kubectl -n "$namespace" logs deployment/failover-provider --tail=100 >&2 || true
  kubectl -n "$namespace" logs deployment/failover-provider --previous --tail=100 >&2 || true
  exit 1
fi
start_port_forward
rm -rf /tmp/zkdeal-provider-kind
python tests/fixtures/failover_provider_kubernetes_client.py preflight
[ "$(kubectl -n "$namespace" get deployment coordinator-active -o jsonpath='{.spec.replicas}')" = 1 ]
[ "$(kubectl -n "$namespace" get statefulset postgresql-primary -o jsonpath='{.spec.replicas}')" = 1 ]

kubectl -n "$namespace" exec deployment/witness-b -- python -c \
  "import urllib.request; r=urllib.request.Request('http://127.0.0.1:8080/test/unhealthy',data=b'{}',headers={'X-Acceptance-Control':'set-unhealthy'},method='POST'); urllib.request.urlopen(r,timeout=5).read()"
python tests/fixtures/failover_provider_kubernetes_client.py prepare

[ "$(kubectl -n "$namespace" get deployment coordinator-active -o jsonpath='{.spec.replicas}')" = 0 ]
[ "$(kubectl -n "$namespace" get statefulset postgresql-primary -o jsonpath='{.spec.replicas}')" = 0 ]
[ "$(kubectl -n "$namespace" get deployment standby-signer-authority -o jsonpath='{.spec.replicas}')" = 0 ]
[ "$(kubectl -n "$namespace" get service postgresql-writer -o jsonpath='{.spec.selector.zkdeal\.io/failover-target}')" = standby ]
[ "$(kubectl -n "$namespace" exec postgresql-standby-0 -c postgres -- psql -U zkdeal -d zkdeal -tAc 'select pg_is_in_recovery()' | tr -d '\r')" = f ]

python tests/fixtures/failover_provider_kubernetes_client.py commit
[ "$(kubectl -n "$namespace" get deployment standby-signer-authority -o jsonpath='{.spec.replicas}')" = 1 ]
[ "$(kubectl -n "$namespace" get service coordinator-writer -o jsonpath='{.spec.selector.zkdeal\.io/failover-target}')" = standby ]
kubectl -n "$namespace" rollout status deployment/standby-signer-authority --timeout=120s

provider_uid_before=$(kubectl -n "$namespace" get pod -l app=failover-provider -o jsonpath='{.items[0].metadata.uid}')
kubectl -n "$namespace" rollout restart deployment/failover-provider
kubectl -n "$namespace" rollout status deployment/failover-provider --timeout=120s
provider_uid_after=$(kubectl -n "$namespace" get pod -l app=failover-provider -o jsonpath='{.items[0].metadata.uid}')
[ -n "$provider_uid_before" ] && [ -n "$provider_uid_after" ] && [ "$provider_uid_before" != "$provider_uid_after" ]
start_port_forward
python tests/fixtures/failover_provider_kubernetes_client.py replay

provider_id=$(docker image inspect "$provider_image" --format '{{.Id}}')
provider_source_id=$(docker image inspect "$provider_source" --format '{{.Id}}')
fixture_id=$(docker image inspect "$fixture_image" --format '{{.Id}}')
postgres_id=$(docker image inspect "$postgres_image" --format '{{.Id}}')
printf '{"kubernetesFailoverProvider":"passed","kindNode":"%s","candidateDigestMode":%s,"providerReference":"%s","providerSourceImageId":"%s","providerAliasImageId":"%s","fixtureImageId":"%s","postgresReference":"%s","postgresImageId":"%s","realPostgresqlSynchronousReplication":true,"independentWitnessVeto":true,"oldWriterScaledToZero":true,"primaryStatefulSetScaledToZero":true,"standbyPromoted":true,"databaseRouteSwitched":true,"applicationRouteSwitched":true,"signerActivatedOnlyAfterCommit":true,"scopedRbac":true,"podReplacementIdempotency":true,"persistentVolumeDurability":true,"tls":"acceptance-http-production-helm-requires-tls"}\n' \
  "$node_image" "$candidate_mode" "$provider_source" "$provider_source_id" "$provider_id" "$fixture_id" "$postgres_source" "$postgres_id"
