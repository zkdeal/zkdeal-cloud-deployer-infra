SCENARIOS = [
    "reorg",
    "rpc-disagreement",
    "split-brain-promotion",
    "database-object-restore",
    "sponsorship",
    "queue-congestion",
    "headless-restart",
    "blob",
    "partial-aggregate",
    "renewal",
    "withdrawal",
    "rpc-load-shadow",
    "sse-load-shadow",
    "indexer-load-shadow",
    "admission-load-shadow",
    "scheduler-load-shadow",
    "projection-parity-shadow",
    "prover-agent-trace-join",
]

ASSERTIONS = {
    "reorg": ["--assert-rollback-and-retraction"],
    "rpc-disagreement": ["--assert-fail-closed"],
    "split-brain-promotion": [
        "--kill-active",
        "--assert-fence",
        "--assert-rto-seconds",
        "300",
    ],
    "database-object-restore": [
        "--fresh-database",
        "--fresh-object-store",
        "--assert-hashes",
    ],
    "sponsorship": ["--assert-tenant-isolation", "--assert-idempotency"],
    "queue-congestion": ["--assert-fairness", "--assert-backoff"],
    "headless-restart": ["--assert-state-continuity", "--assert-single-writer"],
    "blob": ["--assert-hash-binding", "--assert-restart-resume"],
    "partial-aggregate": ["--assert-member-outcomes"],
    "renewal": ["--assert-deadline-and-metering"],
    "withdrawal": ["--assert-finality", "--assert-replay-denial"],
    "rpc-load-shadow": [
        "--assert-latency-budget-ms",
        "500",
        "--assert-mismatch-rate",
        "0",
    ],
    "sse-load-shadow": [
        "--assert-reconnect",
        "--assert-backpressure",
        "--assert-subscriber-cap",
    ],
    "indexer-load-shadow": ["--assert-rollback", "--assert-lag-budget-blocks", "8"],
    "admission-load-shadow": [
        "--assert-wal-durable",
        "--assert-backpressure",
        "--assert-cap",
    ],
    "scheduler-load-shadow": [
        "--assert-mixed-tenant-fairness",
        "--assert-deadline-budget",
    ],
    "projection-parity-shadow": [
        "--assert-projection-parity",
        "--assert-mismatch-rate",
        "0",
    ],
    "prover-agent-trace-join": ["--assert-coordinator-agent-prover-heartbeat"],
}

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


HEX = "0123456789abcdef"


def _require_digest_image(label, reference):
    if not reference:
        fail("%s is required" % label)
    pieces = reference.split("@sha256:")
    if len(pieces) != 2 or len(reference.split("@")) != 2:
        fail("%s must be exact repository@sha256:<64 lowercase hex>" % label)
    repository = pieces[0]
    digest = pieces[1]
    if not repository or len(digest) != 64:
        fail("%s must be exact repository@sha256:<64 lowercase hex>" % label)
    if ":" in repository.split("/")[-1]:
        fail("%s must not include a tag before the digest" % label)
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


def _require_assertion_command(scenario, command):
    if not command:
        fail("missing executable owner acceptance command for %s" % scenario)
    for character in command:
        if (
            character
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./,:=- "
        ):
            fail(
                "acceptance command for %s contains an unsafe shell character"
                % scenario
            )
    argv = [value for value in command.split(" ") if value]
    if len(argv) < 2 or argv[0] != "/opt/zkdeal-acceptance" or argv[1] != scenario:
        fail(
            "acceptance command for %s must use the exact owner entrypoint and scenario"
            % scenario
        )
    for marker in ASSERTIONS[scenario]:
        if marker not in argv:
            fail(
                "acceptance command for %s lacks required assertion %s"
                % (scenario, marker)
            )
    return command


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
    if alias not in TOKEN_FILE_ENV:
        fail("unknown acceptance auth alias %s" % alias)
    if not value or not value.startswith("eph_") or len(value) < 32 or len(value) > 124:
        fail("acceptance auth %s must be an enclave-scoped ephemeral token" % alias)
    for character in value:
        if (
            character
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        ):
            fail("acceptance auth %s contains an unsafe character" % alias)
    return value


def run(plan, args={}):
    images = args.get("images", {})
    runner = _require_digest_image(
        "args.images.acceptance_runner", images.get("acceptance_runner")
    )
    if len(images) != 1:
        fail(
            "acceptance matrix targets the shared candidate stack and starts only its runner"
        )
    if args.get("input_contract") != "candidate-topology-expanded-v1":
        fail(
            "acceptance matrix requires the containerized candidate-topology launcher contract"
        )
    candidate_id = args.get("candidate_id")
    if not candidate_id or len(candidate_id) < 8 or len(candidate_id) > 80:
        fail("args.candidate_id is missing or malformed")
    hosted_token = _require_sha256(
        "args.hosted_integration_token",
        args.get("hosted_integration_token"),
        "sha256:",
    )
    topology_sha256 = _require_sha256(
        "args.topology_verification_sha256",
        args.get("topology_verification_sha256"),
    )
    plan_json = args.get("plan_json")
    if not plan_json or len(plan_json) > 4194304:
        fail("args.plan_json is missing or exceeds 4 MiB")
    plan_sha256 = _require_sha256("args.plan_sha256", args.get("plan_sha256"))
    commands = args.get("commands", {})
    endpoints = args.get("endpoints", {})
    required_endpoints = [
        "coordinator",
        "indexer",
        "queue",
        "headless",
        "l1_rpc_a",
        "l1_rpc_b",
        "fault",
        "backup",
        "failover",
        "prover",
        "logs",
    ]
    for endpoint in required_endpoints:
        if not endpoints.get(endpoint):
            fail("missing args.endpoints.%s" % endpoint)
    if endpoints.get("queue") != endpoints.get("coordinator"):
        fail(
            "acceptance requires the hosted coordinator queue, never a standalone file queue"
        )
    auth = args.get("auth", {})
    scenario_auth = args.get("scenario_auth", {})
    for alias, value in auth.items():
        _require_ephemeral_token(alias, value)
    for scenario in scenario_auth.keys():
        if scenario not in SCENARIOS:
            fail("args.scenario_auth contains unknown scenario %s" % scenario)
    evidence_artifacts = {}
    for scenario in SCENARIOS:
        command = _require_assertion_command(scenario, commands.get(scenario))
        if scenario not in scenario_auth:
            fail("args.scenario_auth omits %s" % scenario)
        aliases = scenario_auth[scenario]
        if type(aliases) != "list":
            fail("args.scenario_auth.%s must be a list" % scenario)
        input_config = {
            "/plan.json": struct(template="{{.Value}}", data={"Value": plan_json}),
        }
        token_env = {}
        token_copy = ""
        seen_aliases = []
        for alias in aliases:
            if alias in seen_aliases:
                fail("args.scenario_auth.%s repeats %s" % (scenario, alias))
            seen_aliases.append(alias)
            value = _require_ephemeral_token(alias, auth.get(alias))
            input_config["/tokens/%s.token" % alias] = struct(
                template="{{.Value}}", data={"Value": value}
            )
            token_env[TOKEN_FILE_ENV[alias]] = "/tmp/zkdeal-acceptance/%s.token" % alias
            token_copy = (
                token_copy
                + "cp /run/zkdeal-acceptance/tokens/%s.token /tmp/zkdeal-acceptance/%s.token; "
                % (alias, alias)
            )
        input_artifact = plan.render_templates(
            config=input_config,
            name="acceptance-%s-input" % scenario,
            description="render candidate-bound inputs for %s" % scenario,
        )
        env_vars = {
            "ACCEPTANCE_PLAN_FILE": "/run/zkdeal-acceptance/plan.json",
            "ACCEPTANCE_PLAN_SHA256": plan_sha256,
            "ACCEPTANCE_CANDIDATE_ID": candidate_id,
            "HOSTED_INTEGRATION_TOKEN": hosted_token,
            "CANDIDATE_TOPOLOGY_VERIFICATION_SHA256": topology_sha256,
            "ACCEPTANCE_EVIDENCE_DIR": "/evidence",
            "ACCEPTANCE_HTTP_TIMEOUT_SECONDS": "30",
        }
        for endpoint, env_name in ENDPOINT_ENV.items():
            env_vars[env_name] = endpoints[endpoint]
        for env_name, path in token_env.items():
            env_vars[env_name] = path
        result = plan.run_sh(
            name="accept-" + scenario,
            image=runner,
            env_vars=env_vars,
            files={"/run/zkdeal-acceptance": input_artifact},
            run="set -eu; umask 077; mkdir -p /tmp/zkdeal-acceptance /evidence; %schmod 0600 /tmp/zkdeal-acceptance/*.token 2>/dev/null || true; %s"
            % (token_copy, command),
            store=[
                StoreSpec(src="/evidence", name="acceptance-%s-evidence" % scenario)
            ],
            wait="2h",
        )
        evidence_artifacts[scenario] = result.files_artifacts
    return {
        "status": "ALL_OWNER_ASSERTIONS_PASSED",
        "scenarios": SCENARIOS,
        "runner": runner,
        "candidate_id": candidate_id,
        "plan_sha256": plan_sha256,
        "topology_verification_sha256": topology_sha256,
        "evidence_artifacts": evidence_artifacts,
        "health_polling_only": False,
        "candidate_stack_started_by_package": False,
        "standalone_queue_accepted": False,
    }
