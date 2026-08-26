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


REQUIRED_AUTH = [
    "tenant_a",
    "tenant_b",
    "node_a",
    "node_b",
    "l1_liveness",
    "l1_room",
    "l1_aggregate",
    "l1_sponsor",
    "withdrawal",
    "fault_control",
    "backup_restore",
    "failover_control",
    "failover_approval",
]


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
        fail("soak auth %s must be an independently revocable eph_ token" % alias)
    for character in value:
        if (
            character
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        ):
            fail("soak auth %s contains an unsafe character" % alias)
    return value


def _require_soak_command(command):
    if not command:
        fail("soak_command is required; read-only health polling is not a release soak")
    for character in command:
        if (
            character
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./,:=- "
        ):
            fail("soak_command contains an unsafe shell character")
    argv = [value for value in command.split(" ") if value]
    required = [
        "--submit-real-proof-jobs",
        "--restart",
        "--assert-durable-results,cursors,nonces,charges,sealed-output,safety,claims,fairness,deadlines",
        "--bounded-backoff",
        "--emit-evidence-closure",
    ]
    if not argv or argv[0] != "/opt/zkdeal-soak":
        fail("soak_command must use the exact first-party soak entrypoint")
    for marker in required:
        if marker not in argv:
            fail("soak_command lacks required assertion %s" % marker)
    return command


def run(plan, args={}):
    images = args.get("images", {})
    runner = _require_digest_image("args.images.runner", images.get("runner"))
    if len(images) != 1:
        fail(
            "soak package targets the shared candidate stack and starts only its runner"
        )
    if args.get("input_contract") != "candidate-topology-expanded-v1":
        fail("soak requires the containerized candidate-topology launcher contract")
    candidate_id = args.get("candidate_id")
    if not candidate_id or len(candidate_id) < 8 or len(candidate_id) > 80:
        fail("candidate_id is missing or malformed")
    hosted_token = _require_sha256(
        "hosted_integration_token",
        args.get("hosted_integration_token"),
        "sha256:",
    )
    topology_sha = _require_sha256(
        "topology_verification_sha256",
        args.get("topology_verification_sha256"),
    )
    manifest_sha = _require_sha256("manifest_sha256", args.get("manifest_sha256"))
    command_sha = _require_sha256(
        "owner_command_sha256", args.get("owner_command_sha256")
    )
    owner_source = _require_sha256(
        "owner_driver_source_sha256", args.get("owner_driver_source_sha256")
    )
    if args.get("owner_driver_image_label_sha256") != owner_source:
        fail("owner soak source hash differs from its exact image label")
    manifest_json = args.get("manifest_json")
    owner_command_json = args.get("owner_command_json")
    if not manifest_json or len(manifest_json) > 4194304:
        fail("release soak manifest is absent or exceeds 4 MiB")
    if not owner_command_json or len(owner_command_json) > 65536:
        fail("owner soak command is absent or exceeds 64 KiB")
    duration = int(args.get("duration_seconds", "0"))
    if duration < 43200 or duration > 86400:
        fail("release soak duration must be 12 through 24 hours")
    active_id = args.get("active_coordinator_id")
    standby_id = args.get("standby_coordinator_id")
    if not active_id or not standby_id or active_id == standby_id:
        fail(
            "soak requires the distinct verified active/standby coordinator "
            "identities for the coordinator-promotion fault"
        )
    witness_count = int(args.get("failover_witness_count", "2"))
    if witness_count < 1 or witness_count > 8:
        fail("failover_witness_count is out of bounds")
    addresses = args.get("deployment_addresses", {})
    for name in ("roomManager", "operationsAccount", "roomPool", "sponsorAccount"):
        value = addresses.get(name, "")
        if len(value) != 42 or not value.startswith("0x"):
            fail("deployment_addresses.%s must be 0x plus 40 hex characters" % name)
        for character in value[2:]:
            if character not in HEX:
                fail(
                    "deployment_addresses.%s must be 0x plus 40 lowercase hex characters"
                    % name
                )
    command = _require_soak_command(args.get("soak_command"))
    endpoints = args.get("endpoints", {})
    for endpoint in ENDPOINT_ENV.keys():
        if not endpoints.get(endpoint):
            fail("candidate topology omits endpoint %s" % endpoint)
    if endpoints.get("queue") != endpoints.get("coordinator"):
        fail("release soak requires the hosted coordinator queue")
    auth = args.get("auth", {})
    if sorted(auth.keys()) != sorted(REQUIRED_AUTH):
        fail("release soak auth must exactly cover every scoped lifecycle/fault role")
    values = []
    input_config = {
        "/manifest.json": struct(template="{{.Value}}", data={"Value": manifest_json}),
        "/owner-command.json": struct(
            template="{{.Value}}", data={"Value": owner_command_json}
        ),
    }
    token_copy = ""
    for alias in REQUIRED_AUTH:
        token = _require_ephemeral_token(alias, auth[alias])
        if token in values:
            fail("every soak authority must have a distinct token")
        values.append(token)
        input_config["/tokens/%s.token" % alias] = struct(
            template="{{.Value}}", data={"Value": token}
        )
        token_copy = (
            token_copy
            + "cp /run/zkdeal-soak/tokens/%s.token /tmp/zkdeal-soak/%s.token; "
            % (alias, alias)
        )
    inputs = plan.render_templates(
        config=input_config,
        name="candidate-soak-input",
        description="render hash-bound physical soak manifest, owner command and scoped credentials",
    )
    env_vars = {
        "SOAK_DURATION_SECONDS": str(duration),
        "SOAK_MANIFEST_FILE": "/run/zkdeal-soak/manifest.json",
        "SOAK_MANIFEST_SHA256": manifest_sha,
        "SOAK_OWNER_COMMAND_FILE": "/run/zkdeal-soak/owner-command.json",
        "SOAK_OWNER_COMMAND_SHA256": command_sha,
        "OWNER_SOAK_DRIVER_SOURCE_SHA256": owner_source,
        "OWNER_SOAK_DRIVER_IMAGE_LABEL_SHA256": owner_source,
        "SOAK_EVIDENCE_DIR": "/evidence",
        "SOAK_JOURNAL_FILE": "/evidence/journal.ndjson",
        "SOAK_STATE_FILE": "/evidence/state.json",
        "SOAK_CLOSURE_FILE": "/evidence/closure.json",
        "SOAK_CANDIDATE_ID": candidate_id,
        "ACTIVE_COORDINATOR_ID": active_id,
        "STANDBY_COORDINATOR_ID": standby_id,
        "SOAK_FAILOVER_WITNESS_COUNT": str(witness_count),
        "ZKDEAL_ROOM_MANAGER": addresses["roomManager"],
        "ZKDEAL_OPERATIONS_ACCOUNT": addresses["operationsAccount"],
        "ZKDEAL_ROOM_POOL": addresses["roomPool"],
        "ZKDEAL_SPONSOR_ACCOUNT": addresses["sponsorAccount"],
        "HOSTED_INTEGRATION_TOKEN": hosted_token,
        "CANDIDATE_TOPOLOGY_VERIFICATION_SHA256": topology_sha,
        "REQUIRE_REAL_PROOF_JOBS": "1",
        "REQUIRE_FULL_LIFECYCLE": "room-create,submit,lease,live-prepare,prove,verify,blob-archive,aggregate-settle,sponsor,reorg,finalize,withdraw,reconcile",
        "REQUIRE_INDUCED_RESTARTS": "headless-restart,prover-restart,coordinator-promotion,indexer-rollback,rpc-split,object-store-restart,database-restart,docker-host-restart-resume",
        "REQUIRE_DURABLE_ASSERTIONS": "source,image,trust-root,chain-seed,room,job,nonce,tx,event,cursor,usage,charge,sealed-output,safety,claim,fairness,deadline",
        "REQUIRE_APPEND_ONLY_JOURNAL": "1",
        "REQUIRE_RESTART_RESUME": "1",
    }
    for endpoint, env_name in ENDPOINT_ENV.items():
        env_vars[env_name] = endpoints[endpoint]
    for alias in REQUIRED_AUTH:
        env_vars["SOAK_AUTH_%s_TOKEN_FILE" % alias.upper()] = (
            "/tmp/zkdeal-soak/%s.token" % alias
        )
    result = plan.run_sh(
        name="restart-safe-owner-soak",
        image=runner,
        env_vars=env_vars,
        files={"/run/zkdeal-soak": inputs},
        run="set -eu; umask 077; mkdir -p /tmp/zkdeal-soak /evidence; %schmod 0600 /tmp/zkdeal-soak/*.token; %s"
        % (token_copy, command),
        store=[StoreSpec(src="/evidence", name="candidate-release-soak-evidence")],
        # The runner allows duration (up to 24h) plus 7200s of owner-driver and
        # closure-verification overhead (93600s total); the Kurtosis wait must
        # exceed that ceiling with margin or a maximum-length soak is killed
        # before its closure is written.
        wait="27h",
    )
    return {
        "status": "OWNER_SOAK_ASSERTIONS_PASSED",
        "candidate_id": candidate_id,
        "duration_seconds": duration,
        "runner": runner,
        "manifest_sha256": manifest_sha,
        "topology_verification_sha256": topology_sha,
        "evidence_artifacts": result.files_artifacts,
        "candidate_stack_started_by_package": False,
        "health_polling_only": False,
        "standalone_queue_accepted": False,
    }
