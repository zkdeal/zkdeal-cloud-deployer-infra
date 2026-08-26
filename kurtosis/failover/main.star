HEX = "0123456789abcdef"


ENDPOINT_ENV = {
    "coordinator": "COORDINATOR_URL",
    "indexer": "INDEXER_URL",
    "queue": "QUEUE_URL",
    "headless": "HEADLESS_URL",
    "l1_rpc_a": "L1_RPC_A",
    "l1_rpc_b": "L1_RPC_B",
    "fault": "ACCEPTANCE_FAULT_URL",
    "backup": "ACCEPTANCE_BACKUP_URL",
    "failover": "FAILOVER_PROVIDER_URL",
    "prover": "PROVER_URL",
    "logs": "LOG_QUERY_URL",
}


TOKEN_FILE_ENV = {
    "admin": "ACCEPTANCE_ADMIN_TOKEN_FILE",
    "tenant_a": "ACCEPTANCE_TENANT_A_TOKEN_FILE",
    "tenant_b": "ACCEPTANCE_TENANT_B_TOKEN_FILE",
    "node_a": "ACCEPTANCE_NODE_A_TOKEN_FILE",
    "node_b": "ACCEPTANCE_NODE_B_TOKEN_FILE",
    "l1_liveness": "ACCEPTANCE_L1_LIVENESS_TOKEN_FILE",
    "l1_room": "ACCEPTANCE_L1_ROOM_TOKEN_FILE",
    "l1_aggregate": "ACCEPTANCE_L1_AGGREGATE_TOKEN_FILE",
    "l1_sponsor": "ACCEPTANCE_L1_SPONSOR_TOKEN_FILE",
    "withdrawal": "ACCEPTANCE_WITHDRAWAL_TOKEN_FILE",
    "fault_control": "ACCEPTANCE_FAULT_CONTROL_TOKEN_FILE",
    "backup_restore": "ACCEPTANCE_BACKUP_RESTORE_TOKEN_FILE",
    "failover_control": "ACCEPTANCE_FAILOVER_CONTROL_TOKEN_FILE",
    "failover_approval": "ACCEPTANCE_FAILOVER_APPROVAL_TOKEN_FILE",
}


def _require_digest_image(label, reference):
    if not reference:
        fail("%s is required" % label)
    pieces = reference.split("@sha256:")
    if len(pieces) != 2 or len(reference.split("@")) != 2:
        fail("%s must be exact repository@sha256:<64 lowercase hex>" % label)
    repository = pieces[0]
    digest = pieces[1]
    if not repository or len(digest) != 64 or ":" in repository.split("/")[-1]:
        fail("%s must be exact repository@sha256:<64 lowercase hex>" % label)
    if (
        "registry.invalid" in repository
        or "registry.example" in repository
        or "REPLACE" in reference
    ):
        fail("%s must not use a placeholder registry or digest" % label)
    for character in digest:
        if character not in HEX:
            fail("%s digest must contain exactly 64 lowercase hex characters" % label)
    return reference


def _require_sha256(label, value, prefix=""):
    if not value or not value.startswith(prefix):
        fail("%s is missing or malformed" % label)
    digest = value[len(prefix) :]
    if len(digest) != 64:
        fail("%s must contain exactly 64 lowercase hex characters" % label)
    for character in digest:
        if character not in HEX:
            fail("%s must contain exactly 64 lowercase hex characters" % label)
    return value


def _require_ephemeral_token(alias, value):
    if not value or not value.startswith("eph_") or len(value) < 32 or len(value) > 124:
        fail("%s must be an enclave-scoped ephemeral token" % alias)
    for character in value:
        if (
            character
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        ):
            fail("%s contains an unsafe character" % alias)
    return value


def _require_failover_command(command):
    if not command:
        fail("failover_command is required; setup-only health is not failover evidence")
    for character in command:
        if (
            character
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./,:=- "
        ):
            fail("failover_command contains an unsafe shell character")
    argv = [value for value in command.split(" ") if value]
    required = [
        "--terminate-active",
        "--persist-primary-target-lsn",
        "--assert-standby-replay",
        "--promote",
        "--assert-stale-writer-denied",
        "--assert-rto-seconds",
        "300",
    ]
    if not argv or argv[0] != "/opt/zkdeal-failover":
        fail("failover_command must use the exact first-party failover entrypoint")
    for marker in required:
        if marker not in argv:
            fail("failover_command lacks required assertion %s" % marker)
    return command


def run(plan, args={}):
    images = args.get("images", {})
    runner = _require_digest_image(
        "args.images.failover_runner", images.get("failover_runner")
    )
    if len(images) != 1:
        fail(
            "failover package starts only the assertion runner; the candidate stack must already exist"
        )
    if args.get("input_contract") != "candidate-topology-expanded-v1":
        fail("failover requires the containerized candidate-topology launcher contract")
    candidate_id = args.get("candidate_id")
    if not candidate_id or len(candidate_id) < 8 or len(candidate_id) > 80:
        fail("candidate_id is missing or malformed")
    hosted_token = _require_sha256(
        "hosted_integration_token",
        args.get("hosted_integration_token"),
        "sha256:",
    )
    plan_json = args.get("plan_json")
    if not plan_json or len(plan_json) > 4194304:
        fail("plan_json is missing or exceeds 4 MiB")
    plan_sha256 = _require_sha256("plan_sha256", args.get("plan_sha256"))
    topology_sha256 = _require_sha256(
        "topology_verification_sha256", args.get("topology_verification_sha256")
    )
    command = _require_failover_command(args.get("failover_command"))
    endpoints = args.get("endpoints", {})
    for endpoint in ENDPOINT_ENV.keys():
        if not endpoints.get(endpoint):
            fail("candidate topology omits endpoint %s" % endpoint)
    if endpoints.get("queue") != endpoints.get("coordinator"):
        fail("failover acceptance requires the hosted coordinator queue")
    control = args.get("failover_control", {})
    witnesses = control.get("active_witness_urls", [])
    if type(witnesses) != "list" or len(witnesses) != 2 or witnesses[0] == witnesses[1]:
        fail("failover requires two distinct candidate-bound active witnesses")
    for name in [
        "standby_health_url",
        "promotion_endpoint",
        "provider_operation_url",
        "active_coordinator_id",
        "standby_coordinator_id",
        "failure_threshold",
        "max_rto_seconds",
    ]:
        if not control.get(name):
            fail("failover control omits %s" % name)
    if control.get("active_coordinator_id") == control.get("standby_coordinator_id"):
        fail("active and standby coordinator identities must differ")
    auth = args.get("auth", {})
    if not auth.get("failover_control") or not auth.get("failover_approval"):
        fail(
            "failover requires separate failover_control and failover_approval credentials"
        )
    promotion_principal = _require_ephemeral_token(
        "promotion_principal",
        args.get("promotion_principal"),
    )
    input_config = {
        "/plan.json": struct(template="{{.Value}}", data={"Value": plan_json}),
        "/tokens/promotion-principal.token": struct(
            template="{{.Value}}", data={"Value": promotion_principal}
        ),
    }
    token_env = {}
    token_copy = ""
    seen = []
    for alias, value in auth.items():
        if alias not in TOKEN_FILE_ENV or alias in seen:
            fail("failover auth contains an unknown or repeated alias %s" % alias)
        seen.append(alias)
        token = _require_ephemeral_token(alias, value)
        input_config["/tokens/%s.token" % alias] = struct(
            template="{{.Value}}", data={"Value": token}
        )
        token_env[TOKEN_FILE_ENV[alias]] = "/tmp/zkdeal-failover/%s.token" % alias
        token_copy = (
            token_copy
            + "cp /run/zkdeal-failover/tokens/%s.token /tmp/zkdeal-failover/%s.token; "
            % (alias, alias)
        )
    inputs = plan.render_templates(
        config=input_config,
        name="candidate-failover-input",
        description="render hash-bound plan and scoped failover credentials",
    )
    env_vars = {
        "PROMOTION_CANDIDATE_ID": candidate_id,
        "ACTIVE_COORDINATOR_ID": control["active_coordinator_id"],
        "STANDBY_COORDINATOR_ID": control["standby_coordinator_id"],
        "ACTIVE_HEALTH_URLS": ",".join(witnesses),
        "STANDBY_HEALTH_URL": control["standby_health_url"],
        "FAILOVER_PROVIDER_OPERATION_URL": control["provider_operation_url"],
        "PROMOTION_ENDPOINT": control["promotion_endpoint"],
        "FAILOVER_PROVIDER_TOKEN_FILE": "/tmp/zkdeal-failover/failover_control.token",
        "FAILOVER_APPROVAL_TOKEN_FILE": "/tmp/zkdeal-failover/failover_approval.token",
        "PROMOTION_PRINCIPAL_TOKEN_FILE": "/tmp/zkdeal-failover/promotion-principal.token",
        "PROMOTION_CONTROLLER_ARMED": "true",
        "PROMOTION_ALLOW_INSECURE_HTTP": "acceptance-only",
        "FAILURE_THRESHOLD": str(control["failure_threshold"]),
        "MAX_RTO_SECONDS": str(control["max_rto_seconds"]),
        "MAX_CHECKPOINT_AGE_SECONDS": "30",
        "POLL_SECONDS": "1",
        "REQUEST_TIMEOUT_SECONDS": "10",
        "PROMOTION_CONTROLLER_STATE_PATH": "/evidence/promotion-state.json",
        "FAILOVER_EVIDENCE_DIR": "/evidence",
        "ACCEPTANCE_EVIDENCE_DIR": "/evidence",
        "ACCEPTANCE_PLAN_FILE": "/run/zkdeal-failover/plan.json",
        "ACCEPTANCE_PLAN_SHA256": plan_sha256,
        "ACCEPTANCE_CANDIDATE_ID": candidate_id,
        "HOSTED_INTEGRATION_TOKEN": hosted_token,
        "CANDIDATE_TOPOLOGY_VERIFICATION_SHA256": topology_sha256,
        "ACCEPTANCE_HTTP_TIMEOUT_SECONDS": "30",
    }
    for endpoint, env_name in ENDPOINT_ENV.items():
        env_vars[env_name] = endpoints[endpoint]
    for env_name, path in token_env.items():
        env_vars[env_name] = path
    result = plan.run_sh(
        name="candidate-provider-failover",
        image=runner,
        env_vars=env_vars,
        files={"/run/zkdeal-failover": inputs},
        run="set -eu; umask 077; mkdir -p /tmp/zkdeal-failover /evidence; %scp /run/zkdeal-failover/tokens/promotion-principal.token /tmp/zkdeal-failover/promotion-principal.token; chmod 0600 /tmp/zkdeal-failover/*.token; %s"
        % (token_copy, command),
        store=[StoreSpec(src="/evidence", name="candidate-failover-evidence")],
        wait="30m",
    )
    return {
        "status": "ALL_FAILOVER_ASSERTIONS_PASSED",
        "candidate_id": candidate_id,
        "runner": runner,
        "topology_verification_sha256": topology_sha256,
        "plan_sha256": plan_sha256,
        "evidence_artifacts": result.files_artifacts,
        "candidate_stack_started_by_package": False,
        "standalone_queue_accepted": False,
        "shared_single_postgres_accepted": False,
    }
