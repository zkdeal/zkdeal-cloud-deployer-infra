HEX = "0123456789abcdef"

POSTGRES_PORT = 5432
MINIO_PORT = 9000
SERVER_PORT = 3000
INDEXER_PORT = 3001
HEADLESS_PORT = 3100
PROVER_PORT = 8080

REQUIRED_IMAGES = [
    "server",
    "headless",
    "prover",
    "agent",
    "postgres",
    "minio",
    "minio_client",
]

PROVER_MODES = ["gpu-prover", "declared-fixture"]


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


def _require_text(label, value, minimum, maximum):
    if type(value) != "string" or len(value) < minimum or len(value) > maximum:
        fail("%s must be a string of %d..%d characters" % (label, minimum, maximum))
    return value


def _require_address(label, value):
    value = _require_text(label, value, 42, 42)
    if not value.startswith("0x"):
        fail("%s must be a 0x-prefixed 20-byte address" % label)
    for character in value[2:].lower():
        if character not in HEX:
            fail("%s must be a 0x-prefixed 20-byte address" % label)
    return value


def _require_url(label, value):
    value = _require_text(label, value, 10, 300)
    if not value.startswith("http://") and not value.startswith("https://"):
        fail("%s must be an HTTP(S) URL" % label)
    return value


def _wait_http(plan, name, endpoint, port_id="http"):
    plan.wait(
        service_name=name,
        recipe=GetHttpRequestRecipe(port_id=port_id, endpoint=endpoint),
        field="code",
        assertion="==",
        target_value=200,
        timeout="5m",
    )


def run(plan, args={}):
    """Development-shape hosted plane: the same PostgreSQL/MinIO/coordinator/
    indexer/headless/prover topology as compose.hosted.yaml, never the
    standalone filesystem queue. Hosted proving is served by the coordinator's
    PostgreSQL/MinIO queue API, exactly as in the production Compose overlay.
    """
    images = args.get("images", {})
    if sorted(images.keys()) != sorted(REQUIRED_IMAGES):
        fail("args.images must contain exactly %s" % ", ".join(REQUIRED_IMAGES))
    resolved = {}
    for name in REQUIRED_IMAGES:
        resolved[name] = _require_digest_image(
            "args.images.%s" % name, images.get(name)
        )

    chain_id = _require_text("args.chain_id", args.get("chain_id", "31337"), 1, 20)
    rpc_a = _require_url("args.l1_rpc_url_a", args.get("l1_rpc_url_a"))
    rpc_b = _require_url("args.l1_rpc_url_b", args.get("l1_rpc_url_b"))
    if rpc_a == rpc_b:
        fail(
            "args.l1_rpc_url_a and args.l1_rpc_url_b must be two independent providers"
        )
    provider_ids = _require_text(
        "args.l1_rpc_provider_ids", args.get("l1_rpc_provider_ids"), 3, 200
    )
    if (
        len(provider_ids.split(",")) != 2
        or provider_ids.split(",")[0] == provider_ids.split(",")[1]
    ):
        fail("args.l1_rpc_provider_ids must name one distinct identity per provider")
    beacon_urls = _require_url(
        "args.beacon_sidecar_urls", args.get("beacon_sidecar_urls")
    )
    room_manager = _require_address("args.room_manager", args.get("room_manager"))
    room_pool = _require_address("args.room_pool", args.get("room_pool"))
    access_token = _require_address("args.access_token", args.get("access_token"))

    prover_mode = args.get("prover_mode")
    if prover_mode not in PROVER_MODES:
        fail("args.prover_mode must be one of %s" % ", ".join(PROVER_MODES))

    secrets = args.get("secrets", {})
    postgres_password = _require_text(
        "args.secrets.postgres_password", secrets.get("postgres_password"), 16, 128
    )
    minio_user = _require_text(
        "args.secrets.minio_root_user", secrets.get("minio_root_user"), 3, 128
    )
    minio_password = _require_text(
        "args.secrets.minio_root_password", secrets.get("minio_root_password"), 16, 128
    )
    api_key_pepper = _require_text(
        "args.secrets.api_key_pepper", secrets.get("api_key_pepper"), 32, 256
    )
    indexer_token = _require_text(
        "args.secrets.indexer_token", secrets.get("indexer_token"), 16, 256
    )
    admission_token = _require_text(
        "args.secrets.admission_token", secrets.get("admission_token"), 16, 256
    )
    queue_node_token = _require_text(
        "args.secrets.queue_node_token", secrets.get("queue_node_token"), 16, 256
    )
    prover_token = _require_text(
        "args.secrets.prover_token", secrets.get("prover_token"), 16, 256
    )

    headless = args.get("headless", {})
    headless_config = _require_text(
        "args.headless.config_json", headless.get("config_json"), 2, 262144
    )
    headless_keys = _require_text(
        "args.headless.keys_json", headless.get("keys_json"), 2, 65536
    )
    headless_control = _require_text(
        "args.headless.control_token", headless.get("control_token"), 16, 4096
    )

    # --- durable dependencies: PostgreSQL authority and MinIO object store ---
    plan.add_service(
        name="postgres",
        config=ServiceConfig(
            image=resolved["postgres"],
            env_vars={
                "POSTGRES_DB": "zkdeal",
                "POSTGRES_USER": "zkdeal",
                "POSTGRES_PASSWORD": postgres_password,
                "POSTGRES_INITDB_ARGS": "--data-checksums",
            },
            ports={
                "postgres": PortSpec(
                    number=POSTGRES_PORT, transport_protocol="TCP", wait="2m"
                )
            },
        ),
    )
    plan.wait(
        service_name="postgres",
        recipe=ExecRecipe(command=["pg_isready", "-U", "zkdeal", "-d", "zkdeal"]),
        field="code",
        assertion="==",
        target_value=0,
        timeout="2m",
    )

    plan.add_service(
        name="minio",
        config=ServiceConfig(
            image=resolved["minio"],
            cmd=["server", "/data"],
            env_vars={
                "MINIO_ROOT_USER": minio_user,
                "MINIO_ROOT_PASSWORD": minio_password,
            },
            ports={
                "http": PortSpec(
                    number=MINIO_PORT,
                    transport_protocol="TCP",
                    application_protocol="http",
                    wait="2m",
                )
            },
        ),
    )
    _wait_http(plan, "minio", "/minio/health/live")
    plan.run_sh(
        name="minio-init",
        image=resolved["minio_client"],
        env_vars={
            "MINIO_ROOT_USER": minio_user,
            "MINIO_ROOT_PASSWORD": minio_password,
            "MC_CONFIG_DIR": "/tmp/.mc",
        },
        run=(
            'mc alias set local http://minio:%d "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" && '
            "mc mb --ignore-existing local/zkdeal && "
            "mc anonymous set none local/zkdeal && "
            "mc mb --ignore-existing local/zkdeal-evidence && "
            "mc anonymous set none local/zkdeal-evidence"
        )
        % MINIO_PORT,
        wait="5m",
    )

    # --- hosted owner runtime environment, mirroring compose.hosted.yaml ---
    hosted_env = {
        "CHAIN_ID": chain_id,
        "L1_RPC_URL": rpc_a,
        "L1_RPC_URLS": "%s,%s" % (rpc_a, rpc_b),
        "L1_RPC_PROVIDER_IDS": provider_ids,
        "BEACON_SIDECAR_URLS": beacon_urls,
        "ROOM_MANAGER": room_manager,
        "ROOM_POOL": room_pool,
        "ACCESS_TOKEN": access_token,
        "DATABASE_URL": "postgresql://zkdeal:%s@postgres:%d/zkdeal"
        % (postgres_password, POSTGRES_PORT),
        "API_KEY_PEPPER": api_key_pepper,
        "OBJECT_STORE_ENDPOINT": "http://minio:%d" % MINIO_PORT,
        "OBJECT_STORE_BUCKET": "zkdeal",
        "OBJECT_STORE_REGION": "us-east-1",
        "OBJECT_STORE_PREFIX": "hosted",
        "OBJECT_STORE_ACCESS_KEY_ID": minio_user,
        "OBJECT_STORE_SECRET_ACCESS_KEY": minio_password,
        "MAX_ARCHIVE_LAG_BLOCKS": "8",
        "DEMO_ENABLED": "0",
        "QUEUE_ENABLED": "0",
    }

    coordinator_env = {}
    coordinator_env.update(hosted_env)
    coordinator_env.update(
        {
            "PORT": str(SERVER_PORT),
            "HOST": "0.0.0.0",
            "COORDINATOR_ROLE": "active",
            "COORDINATOR_ID": "kurtosis-local-active",
            "INDEXER_TOKEN": indexer_token,
            "ADMISSION_TOKEN": admission_token,
        }
    )
    plan.add_service(
        name="coordinator",
        config=ServiceConfig(
            image=resolved["server"],
            cmd=["node", "dist/index.js"],
            env_vars=coordinator_env,
            ports={
                "http": PortSpec(
                    number=SERVER_PORT,
                    transport_protocol="TCP",
                    application_protocol="http",
                    wait="5m",
                )
            },
        ),
    )
    _wait_http(plan, "coordinator", "/hosting/v1/ready")

    indexer_env = {}
    indexer_env.update(hosted_env)
    indexer_env.update(
        {
            "WORKER_HOST": "0.0.0.0",
            "WORKER_PORT": str(INDEXER_PORT),
            "HOSTED_WORKER_ROLE": "indexer",
            "HOSTED_WORKER_ID": "kurtosis-local-indexer-1",
            "COORDINATOR_ID": "kurtosis-local-active",
            "INDEXER_BOOTSTRAP_BLOCK": args.get("indexer_bootstrap_block", "finalized"),
        }
    )
    plan.add_service(
        name="indexer",
        config=ServiceConfig(
            image=resolved["server"],
            cmd=["node", "dist/hosted-worker.js", "indexer"],
            env_vars=indexer_env,
            ports={
                "http": PortSpec(
                    number=INDEXER_PORT,
                    transport_protocol="TCP",
                    application_protocol="http",
                    wait="5m",
                )
            },
        ),
    )
    _wait_http(plan, "indexer", "/ready")

    # --- production headless room node against the hosted plane ---
    headless_input = plan.render_templates(
        config={
            "/room-node.json": struct(
                template="{{.Value}}", data={"Value": headless_config}
            ),
            "/keys.json": struct(template="{{.Value}}", data={"Value": headless_keys}),
            "/control.token": struct(
                template="{{.Value}}", data={"Value": headless_control}
            ),
        },
        name="headless-room-node-input",
        description="render the hosted room-node config and owner-held secrets",
    )
    plan.add_service(
        name="headless-room-node",
        config=ServiceConfig(
            image=resolved["headless"],
            entrypoint=["/bin/sh", "-ec"],
            cmd=[
                "umask 077; mkdir -p /tmp/zkdeal-room-node; "
                + "cp /run/zkdeal-input/room-node.json /tmp/zkdeal-room-node/room-node.json; "
                + "cp /run/zkdeal-input/keys.json /tmp/zkdeal-room-node/keys.json; "
                + "cp /run/zkdeal-input/control.token /tmp/zkdeal-room-node/control.token; "
                + "chmod 0600 /tmp/zkdeal-room-node/room-node.json /tmp/zkdeal-room-node/keys.json /tmp/zkdeal-room-node/control.token; "
                + "exec node dist/cli.js run",
            ],
            env_vars={"ROOM_NODE_CONFIG_PATH": "/tmp/zkdeal-room-node/room-node.json"},
            files={"/run/zkdeal-input": headless_input},
            ports={
                "http": PortSpec(
                    number=HEADLESS_PORT,
                    transport_protocol="TCP",
                    application_protocol="http",
                    wait="5m",
                )
            },
        ),
    )
    _wait_http(plan, "headless-room-node", "/ready")

    # --- proving plane: hosted coordinator queue plus prover/agent ---
    coordinator_url = "http://coordinator:%d" % SERVER_PORT
    agent_env = {
        "QUEUE_URL": coordinator_url,
        "ZKDEAL_QUEUE_NODE_TOKEN": queue_node_token,
        "NODE_ID": args.get("node_id", "kurtosis-local-node-0"),
        "ROOM_POOL": room_pool,
        "L1_CHAIN_ID": chain_id,
    }
    if prover_mode == "gpu-prover":
        plan.add_service(
            name="prover",
            config=ServiceConfig(
                image=resolved["prover"],
                cmd=["serve", "--host", "0.0.0.0", "--port", str(PROVER_PORT)],
                env_vars={
                    "RISC0_DEV_MODE": "0",
                    "RISC0_PROVER": "local",
                    "RISC0_REQUIRE_CUDA": "1",
                    "CUDA_VISIBLE_DEVICES": "0",
                    "ZKDEAL_PROVER_TOKEN": prover_token,
                },
                ports={
                    "http": PortSpec(
                        number=PROVER_PORT,
                        transport_protocol="TCP",
                        application_protocol="http",
                        wait="10m",
                    )
                },
            ),
        )
        plan.wait(
            service_name="prover",
            recipe=ExecRecipe(command=["/usr/local/bin/zkdeal-r0", "health"]),
            field="code",
            assertion="==",
            target_value=0,
            timeout="10m",
        )
        agent_env.update(
            {
                "PROVER_URL": "http://prover:%d" % PROVER_PORT,
                "ZKDEAL_PROVER_TOKEN": prover_token,
                "ZKDEAL_AGENT_GPU": "1",
            }
        )
    else:
        # Declared fixture prover: exercises the durable queue lease/ack path
        # without CUDA. Never release evidence; declared in the return value.
        agent_env.update(
            {
                "ZKDEAL_AGENT_GPU": "0",
                "ZKDEAL_AGENT_STUB": "ok:50",
                "POLL_INTERVAL_MS": "250",
            }
        )
    plan.add_service(
        name="prover-agent",
        config=ServiceConfig(
            image=resolved["agent"],
            cmd=["node", "/app/agent/agent.js"],
            env_vars=agent_env,
        ),
    )

    return {
        "status": "HOSTED_PLANE_READY",
        "coordinator": coordinator_url,
        "queue": coordinator_url,
        "indexer": "http://indexer:%d" % INDEXER_PORT,
        "headless": "http://headless-room-node:%d" % HEADLESS_PORT,
        "object_store": "http://minio:%d" % MINIO_PORT,
        "database": "postgres:%d" % POSTGRES_PORT,
        "prover_mode": prover_mode,
        "fixture_prover": prover_mode == "declared-fixture",
        "standalone_queue_started": False,
        "release_evidence": False,
    }
