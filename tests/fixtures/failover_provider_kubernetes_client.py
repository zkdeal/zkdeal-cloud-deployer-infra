#!/usr/bin/env python3
"""HTTP assertions for the Kubernetes failover-provider live acceptance."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


BASE = os.environ.get("FAILOVER_PROVIDER_ACCEPTANCE_URL", "http://127.0.0.1:18443")
TOKEN = "acceptance-provider-token-32-characters"
APPROVAL = "acceptance-approval-token-32-characters"
CANDIDATE = "kind-provider-candidate-20260821"
PREPARE_KEY = "kind-provider-prepare-key-20260821"
COMMIT_KEY = "kind-provider-commit-key-20260821"
OWNER_HASH = "a" * 64
STATE_DIR = Path(os.environ.get("FAILOVER_PROVIDER_ACCEPTANCE_STATE", "/tmp/zkdeal-provider-kind"))
PREPARE_REQUEST = {
    "candidateId": CANDIDATE,
    "activeCoordinatorId": "kind-active-coordinator",
    "standbyCoordinatorId": "kind-standby-coordinator",
    "failedWitnessCount": 2,
}


def call(
    path: str,
    value: dict[str, object],
    key: str,
    *,
    token: str = TOKEN,
    approval: str = APPROVAL,
) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "X-Zkdeal-Failover-Approval": approval,
            "Idempotency-Key": key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=20)
        payload = response.read()
        status = response.status
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        status = exc.code
    value = json.loads(payload)
    assert isinstance(value, dict)
    return status, value


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def expect_error(status: int, value: dict[str, object], expected_status: int, code: str) -> None:
    assert status == expected_status, (status, value)
    assert value.get("code") == code, value


def mode_preflight() -> None:
    status, value = call("/v1/failovers", PREPARE_REQUEST, PREPARE_KEY, token="wrong-provider-token-32-characters")
    expect_error(status, value, 401, "unauthorized")
    status, value = call("/v1/failovers", PREPARE_REQUEST, PREPARE_KEY, approval="wrong-approval-token-32-characters")
    expect_error(status, value, 403, "approval-required")
    status, value = call(
        f"/v1/failovers/{CANDIDATE}/commit",
        {"ownerResponseSha256": OWNER_HASH},
        COMMIT_KEY,
    )
    expect_error(status, value, 409, "not-prepared")
    status, value = call("/v1/failovers", PREPARE_REQUEST, PREPARE_KEY)
    expect_error(status, value, 409, "active-witness-veto")


def mode_prepare() -> None:
    status, value = call("/v1/failovers", PREPARE_REQUEST, PREPARE_KEY)
    assert status == 200, (status, value)
    expected = {
        "operationId": CANDIDATE,
        "status": "READY_FOR_APPLICATION_PROMOTION",
        "activeFenced": True,
        "oldWriterTerminated": True,
        "targetCapturedByProvider": True,
        "databasePromoted": True,
        "standbyReplayAtOrAfterTarget": True,
        "indexerHeadMatchesL1": True,
        "stableDatabaseEndpointRouted": True,
        "standbySignerAuthorityActive": False,
        "primaryTargetSource": "durable-fenced-wal-checkpoint",
    }
    for key, expected_value in expected.items():
        assert value.get(key) == expected_value, (key, value)
    assert "/" in str(value.get("primaryTargetLsn", ""))
    assert "/" in str(value.get("standbyReplayLsn", ""))
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "prepare.json").write_bytes(canonical(value))


def mode_commit() -> None:
    status, value = call(
        f"/v1/failovers/{CANDIDATE}/commit",
        {"ownerResponseSha256": OWNER_HASH},
        COMMIT_KEY,
    )
    assert status == 200, (status, value)
    assert value == {
        "operationId": CANDIDATE,
        "writerRouteCommitted": True,
        "writerCoordinatorId": "kind-standby-coordinator",
        "oldWriterRouteRemoved": True,
        "stableDatabaseEndpointRouted": True,
        "signerAuthorityActivatedAfterFence": True,
    }, value
    (STATE_DIR / "commit.json").write_bytes(canonical(value))


def mode_replay() -> None:
    status, prepare = call("/v1/failovers", PREPARE_REQUEST, PREPARE_KEY)
    assert status == 200, (status, prepare)
    assert canonical(prepare) == (STATE_DIR / "prepare.json").read_bytes()
    status, commit = call(
        f"/v1/failovers/{CANDIDATE}/commit",
        {"ownerResponseSha256": OWNER_HASH},
        COMMIT_KEY,
    )
    assert status == 200, (status, commit)
    assert canonical(commit) == (STATE_DIR / "commit.json").read_bytes()
    changed = dict(PREPARE_REQUEST, failedWitnessCount=3)
    status, value = call("/v1/failovers", changed, PREPARE_KEY)
    expect_error(status, value, 409, "idempotency-conflict")


MODES = {
    "preflight": mode_preflight,
    "prepare": mode_prepare,
    "commit": mode_commit,
    "replay": mode_replay,
}


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in MODES:
        raise SystemExit(f"usage: {sys.argv[0]} {'|'.join(MODES)}")
    MODES[sys.argv[1]]()
    print(json.dumps({"mode": sys.argv[1], "passed": True}, sort_keys=True, separators=(",", ":")))
