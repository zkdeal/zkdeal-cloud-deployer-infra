# Fault control

`zkdeal-fault-control` is the first-party acceptance authority for deliberate
local faults and bounded load/shadow probes. It is not an owner administrator:
it accepts only the dedicated `fault_control` bearer token.

Every mutating request carries the exact candidate ID, acceptance-plan SHA-256
and hosted-integration token. Startup repeats those bindings and verifies the
SHA-256 of the complete topology file. URLs, JSON-RPC methods, Docker container
names, load paths, HTTP methods and load bodies are fixed by that topology;
requests cannot supply a URL, command, raw JSON-RPC method, Docker name or
target body.

The exact control contract exposes:

- `POST /v1/faults` with the fixed enum `l1-reorg`, `rpc-disagreement`,
  `rpc-provider-control`, `coordinator-terminate`, `service-pause`,
  `headless-restart`, `prover-restart`, `indexer-rollback`,
  `object-store-restart`, `database-restart`, `network-partition`, and
  `sse-disconnect`;
- `rpc-provider-control` stops or starts exactly one of the two allowlisted L1
  RPC provider containers (`rpc-a` or `rpc-b`), and `service-pause` pauses or
  unpauses one allowlisted service container. Every container verb is one of
  `restart`, `stop`, `start`, `pause`, or `unpause` on an allowlisted target;
  the raw Docker socket is never otherwise exposed;
- authenticated `GET /v1/faults/{operationId}`;
- `POST /v1/load-runs` with fixed profiles `rpc`, `sse`, `indexer`,
  `admission`, `scheduler`, and `projection`, plus authenticated status GET;
- public bounded health, readiness and capability endpoints.

Every POST also requires `X-Correlation-Id`. Capabilities and receipts bind the
candidate descriptor, topology receipt, platform, exact adapter image digest,
and adapter source SHA-256. The service never accepts an owner admin token.

Each mutation requires `Idempotency-Key`. A write-once intent is fsynced before
the external action and a separate write-once closure is fsynced afterward. A
crash between them fails closed and never silently repeats a fault. Only hashes
of idempotency keys are persisted.

The image runs as UID/GID 65532 and supports a read-only root filesystem. Mount
only a dedicated writable journal at `/journal`, the reviewed topology and
token as read-only files, and (only when restart tests are authorized) the
Docker socket with the narrow group access needed by UID 65532. The example topology
is explicitly a non-release fixture; release evidence requires an exact
candidate topology and digest-pinned image.
