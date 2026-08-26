#!/usr/bin/env python3
"""Validate rendered Helm object semantics from standard input."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from common import DeploymentError, require_container


def fail(message: str) -> None:
    raise DeploymentError(message)


def main() -> int:
    try:
        require_container()
        documents = [item for item in yaml.safe_load_all(sys.stdin.read()) if item]
        if not documents:
            fail("Helm render produced no objects")
        deployments = {
            item["metadata"]["name"]: item
            for item in documents
            if item.get("kind") == "Deployment"
        }
        required_suffixes = (
            "coordinator-active", "coordinator-standby", "indexer",
            "reconciler", "publisher", "headless-node", "prover", "prover-agent",
            "capacity-controller",
            "auto-claimer", "promotion-controller", "failover-provider",
            "standby-signer-authority", "docs",
        )
        for suffix in required_suffixes:
            if not any(name.endswith(suffix) for name in deployments):
                fail(f"missing production deployment: {suffix}")
        # Consolidated topology: tenant/entitlement/sponsorship APIs are
        # coordinator routes; finality and sponsor mutations are owner managed
        # L1 operations. No placeholder Deployment may return.
        for retired_suffix in ("tenant-api", "-sponsorship", "finality-oracle", "provider-payout"):
            if any(name.endswith(retired_suffix) for name in deployments):
                fail(f"retired placeholder deployment rendered: {retired_suffix}")
        for name, deployment in deployments.items():
            pod = deployment["spec"]["template"]["spec"]
            if not pod["securityContext"].get("runAsNonRoot"):
                fail(f"{name} is not runAsNonRoot")
            for field in ("runAsUser", "runAsGroup", "fsGroup"):
                if not isinstance(pod["securityContext"].get(field), int) or pod["securityContext"][field] < 1:
                    fail(f"{name} has no explicit numeric {field}")
            if not pod.get("topologySpreadConstraints") or not pod.get("affinity"):
                fail(f"{name} lacks topology/anti-affinity policy")
            for container in pod["containers"]:
                image = container["image"]
                if "@sha256:" not in image:
                    fail(f"{name} is not digest-pinned: {image}")
                security = container["securityContext"]
                if security.get("allowPrivilegeEscalation") is not False or security.get("readOnlyRootFilesystem") is not True:
                    fail(f"{name} container security policy is incomplete")
                if security.get("runAsUser") != pod["securityContext"]["runAsUser"] or security.get("runAsGroup") != pod["securityContext"]["runAsGroup"]:
                    fail(f"{name} container identity differs from its pod identity")
                if not container.get("livenessProbe") or not container.get("readinessProbe"):
                    fail(f"{name} lacks health gates")
        fenced = {
            suffix: next(value for name, value in deployments.items() if name.endswith(suffix))
            for suffix in ("coordinator-active", "coordinator-standby", "indexer", "reconciler", "publisher", "auto-claimer", "capacity-controller")
        }
        coordinator_security = fenced["coordinator-active"]["spec"]["template"]["spec"]["securityContext"]
        if coordinator_security.get("runAsUser") != 10001 or coordinator_security.get("runAsGroup") != 999:
            fail("owner coordinator must run as the published numeric 10001:999 identity")
        docs = next(value for name, value in deployments.items() if name.endswith("docs"))
        docs_security = docs["spec"]["template"]["spec"]["securityContext"]
        if docs_security.get("runAsUser") != 101 or docs_security.get("runAsGroup") != 101:
            fail("docs must run as the numeric nginx 101:101 identity")
        for suffix in ("coordinator-active", "indexer", "reconciler", "publisher", "auto-claimer", "capacity-controller"):
            if fenced[suffix]["spec"].get("strategy", {}).get("type") != "Recreate":
                fail(f"{suffix} rollout may overlap a fenced runtime identity")
        fenced_env = {
            suffix: {
                item["name"]: item
                for item in deployment["spec"]["template"]["spec"]["containers"][0].get("env", [])
            }
            for suffix, deployment in fenced.items()
        }
        if fenced_env["coordinator-active"]["COORDINATOR_ID"].get("value") != "zkdeal-render-primary":
            fail("active coordinator identity is not the rendered active-region identity")
        if fenced_env["coordinator-standby"]["COORDINATOR_ID"].get("value") != "zkdeal-render-standby":
            fail("standby coordinator identity is not distinct from the active identity")
        for suffix in ("indexer", "reconciler", "publisher", "auto-claimer", "capacity-controller"):
            if fenced_env[suffix]["COORDINATOR_ID"].get("value") != "zkdeal-render-primary":
                fail(f"{suffix} is not delegated to the active coordinator identity")
            worker_id = fenced_env[suffix].get("HOSTED_WORKER_ID", {})
            field_path = worker_id.get("valueFrom", {}).get("fieldRef", {}).get("fieldPath")
            if field_path != "metadata.uid":
                fail(f"{suffix} lacks a unique immutable pod worker identity")
        for suffix, environment in fenced_env.items():
            if environment.get("L1_RPC_URLS", {}).get("value") != "https://rpc-a.render.invalid,https://rpc-b.render.invalid":
                fail(f"{suffix} lacks two exact L1 RPC endpoints")
            if environment.get("L1_RPC_PROVIDER_IDS", {}).get("value") != "render-independent-a,render-independent-b":
                fail(f"{suffix} lacks one independent identity per L1 RPC")
        hosted_secret_names = {
            "DATABASE_URL", "API_KEY_PEPPER", "OBJECT_STORE_ENDPOINT",
            "OBJECT_STORE_ACCESS_KEY_ID", "OBJECT_STORE_SECRET_ACCESS_KEY",
        }
        for suffix in fenced:
            present = {
                name for name, item in fenced_env[suffix].items()
                if "secretKeyRef" in str(item.get("valueFrom", {}))
            }
            missing = hosted_secret_names - present
            if missing:
                fail(f"{suffix} lacks hosted runtime secret refs: {sorted(missing)}")
        docs = next(value for name, value in deployments.items() if name.endswith("-docs"))
        docs_env = docs["spec"]["template"]["spec"]["containers"][0].get("env", [])
        if any("secretKeyRef" in str(item) for item in docs_env):
            fail("docs deployment received a secret")
        kinds = {item.get("kind") for item in documents}
        for required in ("HorizontalPodAutoscaler", "CronJob", "PodDisruptionBudget", "NetworkPolicy", "Ingress"):
            if required not in kinds:
                fail(f"render lacks {required}")
        prometheus_rule = next(item for item in documents if item.get("kind") == "PrometheusRule")
        rendered_alerts = {
            rule["alert"]
            for group in prometheus_rule["spec"].get("groups", [])
            for rule in group.get("rules", [])
            if "alert" in rule
        }
        required_alerts = {
            "ZkdealCoordinatorMetricsAbsent", "ZkdealHostedIndexerAbsent",
            "ZkdealHostedReconcilerAbsent", "ZkdealHostedWorkerStale",
            "ZkdealHostedPublisherAbsent", "ZkdealBlobPublisherStale",
            "ZkdealBlobPublisherErrors", "ZkdealPostFinalitySurprise",
            "ZkdealWithdrawalAutoClaimerAbsent", "ZkdealWithdrawalAutoClaimerStale",
            "ZkdealWithdrawalClaimErrors", "ZkdealWithdrawalRecoveryRequired",
            "ZkdealCapacityControllerAbsent", "ZkdealCapacityControllerStale",
            "ZkdealCapacityOperationErrors", "ZkdealCapacityDeadlineRisk",
            "ZkdealRoomNodeAbsent", "ZkdealRoomNodeNotReady",
            "ZkdealRoomNodeControlErrors", "ZkdealRoomNodeFinalityUnavailable",
            "ZkdealL1FinalityMetricContractMissing", "ZkdealIndexerMetricContractMissing",
            "ZkdealAdmissionWalMetricContractMissing", "ZkdealRoomMetricContractMissing",
            "ZkdealHostedQueueMetricContractMissing", "ZkdealTenantBillingMetricContractMissing",
            "ZkdealStorageMetricContractMissing", "ZkdealSponsorshipMetricContractMissing",
            "ZkdealBlobMetricContractMissing", "ZkdealAggregateMetricContractMissing",
        }
        if not required_alerts <= rendered_alerts:
            fail(f"render lacks required owner/contract alerts: {sorted(required_alerts - rendered_alerts)}")
        for group in prometheus_rule["spec"].get("groups", []):
            for rule in group.get("rules", []):
                if "alert" not in rule:
                    continue
                labels = rule.get("labels", {})
                annotations = rule.get("annotations", {})
                if labels.get("severity") not in {"warning", "critical"} or not labels.get("unit"):
                    fail(f"alert {rule['alert']} lacks severity/unit")
                if not annotations.get("runbook_url"):
                    fail(f"alert {rule['alert']} lacks a runbook link")
        policies = [item for item in documents if item.get("kind") == "NetworkPolicy"]
        for policy in policies:
            rendered = policy.get("spec", {})
            for direction in ("ingress", "egress"):
                for rule in rendered.get(direction, []) or []:
                    for peer in rule.get("from", []) or []:
                        if peer.get("namespaceSelector") == {}:
                            fail(f"{policy['metadata']['name']} has a cluster-wide empty namespace selector")
                    for peer in rule.get("to", []) or []:
                        if peer.get("namespaceSelector") == {}:
                            fail(f"{policy['metadata']['name']} has a cluster-wide empty namespace selector")
                        if peer.get("ipBlock", {}).get("cidr") in {"0.0.0.0/0", "::/0"}:
                            fail(f"{policy['metadata']['name']} has unbounded external egress")
        backup = next(item for item in documents if item.get("kind") == "CronJob")
        backup_pod = backup["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        if backup_pod.get("securityContext", {}).get("fsGroup") != 70:
            fail("backup pod lacks the writable non-root fsGroup")
        backup_container = backup_pod["containers"][0]
        if backup_container.get("command") != ["/usr/local/bin/live_backup.sh"]:
            fail("backup is not using the transactional object-store workflow")
        backup_secrets = {
            item["name"] for item in backup_container.get("env", []) if "valueFrom" in item
        }
        required_backup_secrets = {
            "DATABASE_URL", "SOURCE_OBJECT_STORE_ENDPOINT",
            "SOURCE_OBJECT_STORE_ACCESS_KEY", "SOURCE_OBJECT_STORE_SECRET_KEY",
            "BACKUP_OBJECT_STORE_ENDPOINT", "BACKUP_OBJECT_STORE_ACCESS_KEY",
            "BACKUP_OBJECT_STORE_SECRET_KEY", "BACKUP_ENCRYPTION_KEY",
        }
        if backup_secrets != required_backup_secrets:
            fail(f"backup secret scope is wrong: {sorted(backup_secrets)}")
        policy_names = {item["metadata"]["name"] for item in policies}
        for suffix in (
            "backup-egress", "promotion-egress", "promotion-to-coordinator",
            "standby-from-promotion", "promotion-controller-egress",
            "promotion-controller-to-standby", "standby-from-promotion-controller",
            "promotion-controller-to-failover-provider",
            "failover-provider-from-promotion-controller",
            "failover-provider-external-egress", "failover-provider-to-standby",
            "standby-from-failover-provider", "indexer-from-failover-provider",
            "failover-provider-to-signer-authority",
            "signer-authority-from-failover-provider", "signer-authority-egress",
            "front-door-to-standby-coordinator",
        ):
            if not any(name.endswith(suffix) for name in policy_names):
                fail(f"render lacks operational network policy: {suffix}")
        signer_policy_suffixes = (
            "coordinator-active-to-signer-operations-settlement",
            "auto-claimer-to-signer-withdrawal-relayer",
            "publisher-to-signer-blob-publisher",
        )
        for suffix in signer_policy_suffixes:
            if not any(name.endswith(suffix) for name in policy_names):
                fail(f"render lacks signer isolation policy: {suffix}")
        promotion = next(item for item in documents if item.get("kind") == "Job" and item["metadata"]["name"].endswith("promote-standby"))
        promotion_container = promotion["spec"]["template"]["spec"]["containers"][0]
        promotion_names = {item["name"] for item in promotion_container.get("env", [])}
        required_promotion_names = {
            "PROMOTION_ENDPOINT", "PROMOTION_REQUIRED_REPLAY_LSN",
            "PROMOTION_STANDBY_COORDINATOR_ID", "PROMOTION_DATABASE_URL",
            "PROMOTION_IDEMPOTENCY_KEY", "PROMOTION_PRINCIPAL_TOKEN",
        }
        if promotion_names != required_promotion_names:
            fail(f"promotion secret scope is wrong: {sorted(promotion_names)}")
        if promotion_container.get("command") != ["/bin/sh", "/opt/zkdeal/promotion_gate.sh"]:
            fail("promotion job does not execute the reviewed replay/idempotency gate")
        secret_keys = {
            item["name"]: item.get("valueFrom", {}).get("secretKeyRef", {}).get("key")
            for item in promotion_container.get("env", []) if item.get("valueFrom")
        }
        if secret_keys != {
            "PROMOTION_DATABASE_URL": "promotion-database-url",
            "PROMOTION_IDEMPOTENCY_KEY": "promotion-idempotency-key",
            "PROMOTION_PRINCIPAL_TOKEN": "promotion-principal-token",
        }:
            fail(f"promotion secret refs are wrong: {secret_keys}")
        promotion_config = next(
            item for item in documents
            if item.get("kind") == "ConfigMap" and item["metadata"]["name"].endswith("promotion-gate")
        )
        promotion_script = promotion_config.get("data", {}).get("promotion_gate.sh", "")
        for marker in (
            '"fencing":"monotonic-transactional"', '"freshnessGateBlocks":8',
            '"promoted":true', '"effectiveRole":"active"',
            '"indexerHeadMatchesL1":true', 'Idempotency-Key:',
            "pg_last_wal_replay_lsn()", "pg_wal_lsn_diff",
            "hosted_idempotency_records", "duplicate or replayed",
            '"primaryTarget":"durable-fenced-wal-checkpoint"',
            '"standbyReplay":"pg_last_wal_replay_lsn"',
            '"atomicWithFenceTransfer":true', '"promotionReplication":{',
        ):
            if marker not in promotion_script:
                fail(f"promotion gate is missing owner assertion: {marker}")
        controller = next(
            value for name, value in deployments.items()
            if name.endswith("promotion-controller")
        )
        if controller["spec"].get("strategy", {}).get("type") != "Recreate":
            fail("promotion controller rollout may overlap one incident candidate")
        controller_pod = controller["spec"]["template"]["spec"]
        if controller_pod.get("automountServiceAccountToken") is not False:
            fail("promotion controller received a Kubernetes API token")
        controller_env = {
            item["name"]: item
            for item in controller_pod["containers"][0].get("env", [])
        }
        controller_secret_keys = {
            name: item.get("valueFrom", {}).get("secretKeyRef", {}).get("key")
            for name, item in controller_env.items() if item.get("valueFrom")
        }
        if controller_secret_keys != {
            "FAILOVER_PROVIDER_TOKEN": "failover-provider-token",
            "FAILOVER_APPROVAL_TOKEN": "failover-approval-token",
            "PROMOTION_PRINCIPAL_TOKEN": "promotion-principal-token",
        }:
            fail(f"promotion controller secret scope is wrong: {controller_secret_keys}")
        if any(name in controller_env for name in (
            "PROMOTION_DATABASE_URL", "SIGNER_URL", "SIGNER_ROLE", "HOSTING_ADMIN_TOKEN",
        )):
            fail("promotion controller received database or signer authority")
        witnesses = controller_env.get("ACTIVE_HEALTH_URLS", {}).get("value", "").split(",")
        if len(witnesses) != 2 or len(set(witnesses)) != 2 or not all(
            item.startswith("https://") for item in witnesses
        ):
            fail("promotion controller lacks two independent HTTPS witnesses")
        if controller_env.get("PROMOTION_CONTROLLER_ARMED", {}).get("value") != "true":
            fail("promotion controller is not explicitly armed in the structural fixture")
        if controller_env.get("ZKDEAL_VERIFIED_CONTAINER", {}).get("value") != "1":
            fail("promotion controller lacks its Kubernetes container execution marker")
        provider = next(
            value for name, value in deployments.items()
            if name.endswith("failover-provider")
        )
        if provider["spec"].get("strategy", {}).get("type") != "Recreate":
            fail("failover provider rollout may overlap a durable platform operation")
        provider_pod = provider["spec"]["template"]["spec"]
        if not provider_pod.get("automountServiceAccountToken"):
            fail("Kubernetes failover provider lacks its scoped platform token")
        if not provider_pod.get("serviceAccountName", "").endswith("failover-provider"):
            fail("failover provider does not use its dedicated ServiceAccount")
        provider_container = provider_pod["containers"][0]
        provider_env = {item["name"]: item for item in provider_container.get("env", [])}
        required_provider_env = {
            "FAILOVER_PLATFORM", "ACTIVE_HEALTH_URLS", "STANDBY_HEALTH_URL",
            "INDEXER_FRESHNESS_URL", "SIGNER_AUTHORITY_HEALTH_URLS",
            "ACTIVE_COORDINATOR_ID", "STANDBY_COORDINATOR_ID",
            "FAILOVER_PROVIDER_TOKEN_FILE", "FAILOVER_APPROVAL_TOKEN_FILE",
            "FAILOVER_PROVIDER_TLS_CERT_FILE", "FAILOVER_PROVIDER_TLS_KEY_FILE",
            "FAILOVER_PROVIDER_STATE_PATH", "K8S_FAILOVER_NAMESPACE",
            "K8S_ACTIVE_DEPLOYMENT", "K8S_STANDBY_DEPLOYMENT",
            "K8S_PRIMARY_DB_STATEFULSET", "K8S_PRIMARY_DB_POD",
            "K8S_STANDBY_DB_POD", "K8S_POSTGRES_CONTAINER",
            "K8S_DATABASE_SERVICE", "K8S_APPLICATION_SERVICE",
            "K8S_SIGNER_DEPLOYMENT", "K8S_ROUTE_SCOPE_LABEL_KEY",
            "K8S_ROUTE_SCOPE_LABEL_VALUE", "K8S_ROUTE_LABEL_KEY",
            "K8S_STANDBY_ROUTE_VALUE", "FAILOVER_PGUSER",
            "FAILOVER_PGDATABASE", "FAILOVER_PGDATA", "ZKDEAL_VERIFIED_CONTAINER",
        }
        if not required_provider_env <= set(provider_env):
            fail(f"failover provider lacks fixed topology: {sorted(required_provider_env - set(provider_env))}")
        if any("secretKeyRef" in str(item) for item in provider_env.values()):
            fail("failover provider received environment-injected credentials")
        if any(name in provider_env for name in (
            "PROMOTION_PRINCIPAL_TOKEN", "DATABASE_URL", "SIGNER_URL", "SIGNER_ROLE",
            "HOSTING_ADMIN_TOKEN", "KUBECONFIG",
        )):
            fail("failover provider received owner, database, signer, or broad cluster credentials")
        if provider_env["FAILOVER_PLATFORM"].get("value") != "kubernetes":
            fail("Helm failover provider is not using the Kubernetes adapter")
        if provider_env["ZKDEAL_VERIFIED_CONTAINER"].get("value") != "1":
            fail("failover provider lacks its Kubernetes container execution marker")
        if provider_env["K8S_APPLICATION_SERVICE"].get("value") != "zkdeal-coordinator-writer":
            fail("failover provider does not own the stable application writer route")
        if provider_env["K8S_ROUTE_SCOPE_LABEL_KEY"].get("value") != "app.kubernetes.io/instance" or provider_env["K8S_ROUTE_SCOPE_LABEL_VALUE"].get("value") != "zkdeal":
            fail("failover provider route mutation is not release-scoped")
        if provider_env["K8S_ROUTE_LABEL_KEY"].get("value") != "zkdeal.io/failover-target" or provider_env["K8S_STANDBY_ROUTE_VALUE"].get("value") != "standby":
            fail("failover provider route mutation differs from the chart-owned target label")
        provider_volumes = {item["name"]: item for item in provider_pod.get("volumes", [])}
        if set(provider_volumes) != {"state", "runtime-secrets", "tls", "tmp"}:
            fail(f"failover provider volume scope is wrong: {sorted(provider_volumes)}")
        projected = provider_volumes["runtime-secrets"]["projected"]["sources"]
        provider_secret_keys = {
            item["key"]
            for source in projected
            for item in source.get("secret", {}).get("items", [])
        }
        if provider_secret_keys != {"failover-provider-token", "failover-approval-token"}:
            fail(f"failover provider secret scope is wrong: {sorted(provider_secret_keys)}")
        role = next(
            item for item in documents
            if item.get("kind") == "Role" and item["metadata"]["name"].endswith("failover-provider")
        )
        allowed_rbac_resources = {
            "deployments", "deployments/scale", "statefulsets", "statefulsets/scale",
            "pods", "pods/exec", "services",
        }
        actual_rbac_resources = {
            resource for rule in role.get("rules", []) for resource in rule.get("resources", [])
        }
        if actual_rbac_resources != allowed_rbac_resources:
            fail(f"failover provider RBAC scope is wrong: {sorted(actual_rbac_resources)}")
        for rule in role.get("rules", []):
            if not rule.get("resourceNames"):
                fail("failover provider RBAC contains an unscoped resource rule")
            if set(rule.get("verbs", [])) - {"get", "patch", "create"}:
                fail("failover provider RBAC contains an excessive verb")
        signer_authority = next(
            value for name, value in deployments.items()
            if name.endswith("standby-signer-authority")
        )
        if signer_authority["spec"].get("replicas") != 0:
            fail("standby signer authority is active before fence promotion")
        signer_pod = signer_authority["spec"]["template"]["spec"]
        if signer_pod.get("automountServiceAccountToken") is not False:
            fail("standby signer authority received a Kubernetes API token")
        if any("secret" in volume for volume in signer_pod.get("volumes", [])):
            fail("standby signer authority received credentials")
        signer_config = next(
            item for item in documents
            if item.get("kind") == "ConfigMap" and item["metadata"]["name"].endswith("standby-signer-authority")
        ).get("data", {}).get("nginx.conf", "")
        for route in ("operations", "payout", "finality", "sponsor", "withdrawal", "blob"):
            if f"location /{route}/" not in signer_config or f"location = /health/{route}" not in signer_config:
                fail(f"standby signer authority lacks scoped route {route}")
        if "/liveness/" in signer_config:
            fail("standby signer authority can activate the independent liveness role")
        writer_route = next(
            item for item in documents
            if item.get("kind") == "Service" and item["metadata"]["name"].endswith("coordinator-writer")
        )
        if writer_route.get("spec", {}).get("selector") != {
            "app.kubernetes.io/instance": "zkdeal",
            "zkdeal.io/failover-target": "active",
        }:
            fail("stable writer Service does not start release-scoped on the active target")
        ingress = next(item for item in documents if item.get("kind") == "Ingress")
        ingress_service = ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"]
        if ingress_service != writer_route["metadata"]["name"]:
            fail("front door bypasses the stable promoted-writer Service")
        for deployment in deployments.values():
            env_names = {
                item["name"]
                for item in deployment["spec"]["template"]["spec"]["containers"][0].get("env", [])
            }
            if "HOSTING_ADMIN_TOKEN" in env_names:
                fail("production deployment received the dev-only static admin token")
        for denied_suffix in ("coordinator-standby", "indexer", "reconciler"):
            denied = next(value for name, value in deployments.items() if name.endswith(denied_suffix))
            env_names = {
                item["name"]
                for item in denied["spec"]["template"]["spec"]["containers"][0].get("env", [])
            }
            if {"SIGNER_URL", "SIGNER_ROLE"} & env_names:
                fail(f"read-only component {denied_suffix} received signer authority")
        prover_agent = next(value for name, value in deployments.items() if name.endswith("-prover-agent"))
        agent_container = prover_agent["spec"]["template"]["spec"]["containers"][0]
        if agent_container.get("ports"):
            fail("prover agent exposes a fictitious inbound HTTP port")
        agent_env_names = {item["name"] for item in agent_container.get("env", [])}
        if {"PORT", "HOST"} & agent_env_names:
            fail("prover agent receives fictitious inbound HTTP configuration")
        required_agent_env = {
            "QUEUE_URL", "ZKDEAL_QUEUE_NODE_TOKEN", "NODE_ID", "PROVER_URL",
            "ZKDEAL_PROVER_TOKEN", "ROOM_POOL", "NODE_LIVENESS_COORDINATOR_URL",
            "NODE_LIVENESS_COORDINATOR_AUTH_TOKEN", "NODE_LIVENESS_ACCOUNT",
            "L1_CHAIN_ID",
        }
        missing_agent_env = required_agent_env - agent_env_names
        if missing_agent_env:
            fail(f"prover agent lacks owner durable queue/liveness fields: {sorted(missing_agent_env)}")
        forbidden_agent_env = {
            "NODE_SERVICE_KEY", "NODE_LIVENESS_SIGNER_URL",
            "NODE_LIVENESS_SIGNER_AUTH_TOKEN", "NODE_LIVENESS_DEV_MODE",
            "NODE_LIVENESS_DEV_PRIVATE_KEY", "L1_RPC_URL", "L1_RPC_URLS",
        }
        if forbidden_agent_env & agent_env_names:
            fail("prover agent received a direct sender or L1 RPC boundary")
        if agent_container.get("command") != ["node", "/app/agent/agent.js"]:
            fail("prover agent does not use the packaged owner entrypoint")
        if agent_container.get("workingDir") != "/app":
            fail("prover agent working directory differs from the owner image")
        for probe_name in ("livenessProbe", "readinessProbe"):
            if "exec" not in agent_container.get(probe_name, {}):
                fail(f"prover agent {probe_name} does not use its real outbound boundary")
        readiness = " ".join(agent_container["readinessProbe"]["exec"]["command"])
        for marker in (
            "QUEUE_URL", "PROVER_URL", "NODE_LIVENESS_COORDINATOR_URL",
            "NODE_LIVENESS_COORDINATOR_AUTH_TOKEN", "nodeHeartbeat",
        ):
            if marker not in readiness:
                fail(f"prover agent readiness lacks {marker}")
        if "eth_accounts" in readiness or "NODE_LIVENESS_SIGNER" in readiness:
            fail("prover agent readiness still calls Web3Signer directly")
        active_container = fenced["coordinator-active"]["spec"]["template"]["spec"]["containers"][0]
        active_env_names = {item["name"] for item in active_container.get("env", [])}
        for required in (
            "NODE_LIVENESS_SIGNER_URL", "NODE_LIVENESS_SIGNER_ADDRESS",
            "NODE_LIVENESS_SIGNER_AUTH_TOKEN",
        ):
            if required not in active_env_names:
                fail(f"active coordinator lacks {required}")
        services = {
            item["metadata"]["name"]
            for item in documents
            if item.get("kind") == "Service"
        }
        if any(name.endswith("-prover-agent") for name in services):
            fail("prover agent has an unused inbound Service")
        if any(policy["metadata"]["name"].endswith("-monitor-prover-agent") for policy in policies):
            fail("prover agent has a fictitious monitoring ingress policy")
        policy_names = {policy["metadata"]["name"] for policy in policies}
        if "zkdeal-prover-agent-to-coordinator-active" not in policy_names:
            fail("prover agent lacks least-privilege coordinator egress")
        if any("prover-agent-to-signer" in name for name in policy_names):
            fail("prover agent retains a direct signer NetworkPolicy")
        publisher = fenced["publisher"]
        publisher_env = {
            item["name"]: item
            for item in publisher["spec"]["template"]["spec"]["containers"][0].get("env", [])
        }
        for required in ("L1_SIGNER_URL", "L1_SIGNER_ADDRESS", "L1_SIGNER_AUTH_TOKEN", "BLOB_PUBLISHER_ENABLED"):
            if required not in publisher_env:
                fail(f"publisher lacks {required}")
        if "SIGNER_ROLE" in publisher_env or "SIGNER_URL" in publisher_env:
            fail("publisher received the generic settlement signer boundary")
        secret_ref = publisher_env["L1_SIGNER_AUTH_TOKEN"].get("valueFrom", {}).get("secretKeyRef", {})
        if secret_ref.get("key") != "blob-publisher-auth-token":
            fail("publisher signer token is not a scoped secret ref")
        auto_claimer = fenced["auto-claimer"]
        auto_container = auto_claimer["spec"]["template"]["spec"]["containers"][0]
        if auto_container.get("command") != ["node", "dist/hosted-withdrawal-worker.js", "run"]:
            fail("auto-claimer does not use the owner-published entrypoint")
        auto_env = {item["name"]: item for item in auto_container.get("env", [])}
        for required in (
            "WITHDRAWAL_CLAIMER_ENABLED", "WITHDRAWAL_SIGNER_URL",
            "WITHDRAWAL_SIGNER_ADDRESS", "WITHDRAWAL_SIGNER_AUTH_TOKEN",
            "COORDINATOR_ID", "HOSTED_WORKER_ID", "DATABASE_URL",
        ):
            if required not in auto_env:
                fail(f"auto-claimer lacks {required}")
        if auto_env["COORDINATOR_ID"].get("value") != "zkdeal-render-primary":
            fail("auto-claimer is not fenced to the active coordinator identity")
        if {"SIGNER_ROLE", "SIGNER_URL", "L1_SIGNER_URL", "L1_SIGNER_ADDRESS"} & set(auto_env):
            fail("auto-claimer received a generic, settlement, or blob-publisher signer boundary")
        withdrawal_secret = auto_env["WITHDRAWAL_SIGNER_AUTH_TOKEN"].get("valueFrom", {}).get("secretKeyRef", {})
        if withdrawal_secret.get("key") != "withdrawal-signer-auth-token":
            fail("auto-claimer signer token is not a scoped secret ref")
        capacity = fenced["capacity-controller"]
        capacity_container = capacity["spec"]["template"]["spec"]["containers"][0]
        if capacity_container.get("command") != ["node", "dist/hosted-capacity-worker.js", "run"]:
            fail("capacity-controller does not use the owner-published entrypoint")
        capacity_env = {item["name"]: item for item in capacity_container.get("env", [])}
        for required in (
            "CAPACITY_CONTROLLER_ENABLED", "CAPACITY_PROVIDER_URL",
            "CAPACITY_PROVIDER_AUTH_TOKEN", "COORDINATOR_ID", "HOSTED_WORKER_ID",
            "DATABASE_URL",
        ):
            if required not in capacity_env:
                fail(f"capacity-controller lacks {required}")
        if capacity_env["COORDINATOR_ID"].get("value") != "zkdeal-render-primary":
            fail("capacity-controller is not fenced to the active coordinator identity")
        if not capacity_env["CAPACITY_PROVIDER_URL"].get("value", "").startswith("https://"):
            fail("capacity-controller provider boundary is not HTTPS")
        if {"SIGNER_ROLE", "SIGNER_URL", "L1_SIGNER_URL", "WITHDRAWAL_SIGNER_URL"} & set(capacity_env):
            fail("capacity-controller received an unrelated signer boundary")
        capacity_secret = capacity_env["CAPACITY_PROVIDER_AUTH_TOKEN"].get("valueFrom", {}).get("secretKeyRef", {})
        if capacity_secret.get("key") != "capacity-provider-auth-token":
            fail("capacity-controller provider token is not a scoped secret ref")
        headless = next(value for name, value in deployments.items() if name.endswith("-headless-node"))
        headless_pod = headless["spec"]["template"]["spec"]
        headless_container = headless_pod["containers"][0]
        if headless_container.get("command") != ["node", "dist/cli.js", "run"]:
            fail("headless room-node does not use the owner-published entrypoint")
        if headless_container.get("workingDir") != "/app/app-node/packages/room-node":
            fail("headless room-node working directory differs from its owner image")
        headless_env = {item["name"]: item for item in headless_container.get("env", [])}
        if headless_env.get("ROOM_NODE_CONFIG_PATH", {}).get("value") != "/run/zkdeal/room-node.json":
            fail("headless room-node lacks its file-backed config path")
        if any("secretKeyRef" in str(item) for item in headless_env.values()):
            fail("headless room-node received an environment secret instead of locked files")
        if {"SIGNER_URL", "SIGNER_ROLE", "NODE_SERVICE_KEY"} & set(headless_env):
            fail("headless room-node received an unpublished signer/environment-key boundary")
        if headless_pod.get("securityContext", {}).get("fsGroup") != 10001:
            fail("headless room-node lacks its non-root writable state group")
        init = next((item for item in headless_pod.get("initContainers", []) if item.get("name") == "prepare-room-node-secrets"), None)
        if not init or "chmod 0600" not in "\n".join(init.get("args", [])):
            fail("headless room-node does not prepare owner-required mode-0600 secret files")
        volumes = {item["name"]: item for item in headless_pod.get("volumes", [])}
        for volume in ("room-node-config", "room-node-secret-source", "room-node-secrets", "room-node-artifacts", "data"):
            if volume not in volumes:
                fail(f"headless room-node lacks volume {volume}")
        if volumes["room-node-secret-source"]["secret"].get("optional") is True:
            fail("headless room-node file secret is optional")
        mounts = {item["name"]: item for item in headless_container.get("volumeMounts", [])}
        if mounts.get("data", {}).get("mountPath") != "/var/lib/zkdeal-room-node":
            fail("headless room-node durable state is not mounted at the owner path")
        if mounts.get("room-node-artifacts", {}).get("readOnly") is not True:
            fail("headless proving artifacts are not immutable")
        print(f"Helm semantic gate passed: {len(documents)} objects, {len(deployments)} deployments")
        return 0
    except (DeploymentError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
