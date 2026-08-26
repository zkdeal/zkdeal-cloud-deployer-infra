from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import DeploymentError, sha256_file  # noqa: E402
from soak import REQUIRED_FAULTS, validate_manifest, verify_closure  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "zkdeal_owner_soak", ROOT / "owner-soak-driver/zkdeal_owner_soak.py",
)
assert SPEC and SPEC.loader
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


CANDIDATE_ID = "soak-candidate-0001"
PLAN_SHA = "e" * 64
HOSTED_TOKEN = "sha256:" + "f" * 64
ACTIVE_ID = "coordinator-active-1"
STANDBY_ID = "coordinator-standby-1"
ADDR_OPERATIONS = "0x" + "a1" * 20
ADDR_SPONSOR = "0x" + "b2" * 20
ADDR_ORACLE = "0x" + "c3" * 20
ADDR_WITHDRAW = "0x" + "d4" * 20
ROOM_MANAGER = "0x" + "e5" * 20
ROOM_POOL = "0x" + "f6" * 20

CHAIN_ID = 31337
# The anvil genesis account the rig deploys with; the driver funds deposits and
# signs the L2 room transaction with it, so every deposit beneficiary and every
# recovered admission sender must be this address.
DEPOSITOR_KEY = driver.ANVIL_GENESIS_KEY
DEPOSITOR_ADDRESS = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"
# The room's on-chain admissionSigner: roomState reports it and the receipt the
# stub returns must recover to it, exactly like the real coordinator key.
ADMISSION_SIGNER_KEY = "0x" + "3c" * 32
ADMISSION_SIGNER_ADDRESS = driver.private_key_address(driver.private_key_int(ADMISSION_SIGNER_KEY))
# roomState(uint64) values the stub reports for every soak room.
ROOM_MINIMUM_DEPOSIT_CONFIRMATIONS = 1
ROOM_MAXIMUM_ADMISSION_WINDOW = 256
ROOM_BATCH_INDEX = 3
ROOM_BOND_EPOCH = 7
# web2-api/server/src/admission.ts DEFAULT_MINIMUM_DEADLINE_LEAD_BLOCKS.
MINIMUM_DEADLINE_LEAD_BLOCKS = 8


def rlp_split(data: bytes):
    """Decode one RLP item; returns (value, remainder)."""
    prefix = data[0]
    if prefix < 0x80:
        return data[:1], data[1:]
    if prefix < 0xB8:
        length = prefix - 0x80
        return data[1:1 + length], data[1 + length:]
    if prefix < 0xC0:
        header = prefix - 0xB7
        length = int.from_bytes(data[1:1 + header], "big")
        return data[1 + header:1 + header + length], data[1 + header + length:]
    if prefix < 0xF8:
        length = prefix - 0xC0
        body, rest = data[1:1 + length], data[1 + length:]
    else:
        header = prefix - 0xF7
        length = int.from_bytes(data[1:1 + header], "big")
        body, rest = data[1 + header:1 + header + length], data[1 + header + length:]
    items = []
    while body:
        item, body = rlp_split(body)
        items.append(item)
    return items, rest


def decode_legacy_transaction(raw_hex: str) -> dict:
    """Decode and cryptographically verify one EIP-155 legacy transaction.

    The stub recovers the sender from the reconstructed signing pre-image
    rather than being told who signed, so a malformed nonce, chain id, v value
    or RLP body is caught here instead of being silently accepted.
    """
    raw = bytes.fromhex(raw_hex[2:])
    fields, rest = rlp_split(raw)
    if rest or not isinstance(fields, list) or len(fields) != 9:
        raise AssertionError("stub L1 got a transaction that is not 9-field legacy RLP")
    nonce, gas_price, gas, to, value, data, v_raw, r_raw, s_raw = fields
    v = int.from_bytes(v_raw, "big")
    chain_id, remainder = divmod(v - 35, 2)
    if chain_id < 1 or remainder not in (0, 1):
        raise AssertionError(f"stub L1 got a non-EIP-155 v value {v}")
    unsigned = driver.rlp_encode(
        [nonce, gas_price, gas, to, value, data, chain_id, 0, 0]
    )
    sender = driver.public_key_address(
        driver.recover_public_key(
            driver.keccak256(unsigned),
            int.from_bytes(r_raw, "big"),
            int.from_bytes(s_raw, "big"),
            remainder,
        )
    )
    return {
        "nonce": int.from_bytes(nonce, "big"),
        "to": "0x" + to.hex(),
        "value": int.from_bytes(value, "big"),
        "data": data,
        "chainId": chain_id,
        "sender": sender,
        "transactionHash": driver.keccak_hex(raw),
    }


def room_state_blob(batch_index: int, next_inbox_id: int) -> str:
    """The 44-word IRoomManager.Room tuple roomState(uint64) returns."""
    words = [0] * driver.ROOM_STATE_WORD_COUNT
    words[0] = 1  # RoomState.Open
    words[1] = 1  # AuthorizationMode.VALIDITY_ONLY
    words[18] = batch_index
    words[23] = next_inbox_id
    words[31] = ROOM_MINIMUM_DEPOSIT_CONFIRMATIONS
    words[35] = ROOM_MAXIMUM_ADMISSION_WINDOW
    words[36] = ROOM_BOND_EPOCH
    words[37] = int(ADMISSION_SIGNER_ADDRESS, 16)
    words[38] = 10 ** 18  # minimumServiceBond
    words[39] = 10 ** 15  # omissionPenalty
    words[40] = 10 ** 19  # serviceBond
    return "0x" + b"".join(word.to_bytes(32, "big") for word in words).hex()

# Deterministic stub billing: every pulse charges (1 unit, 3 wei); every
# aggregate cycle first charges the stale single-room pre-submission at
# (1, 3) and then the 7 applied members at (2 units, 5 wei) each; the
# sponsorship charges (3 units, 7 wei) once and the withdrawal claim
# (1 unit, 2 wei).
EXPECTED_USAGE = 36 * 1 + 3 * (1 + 7 * 2) + 3 + 1
EXPECTED_WEI = 36 * 3 + 3 * (3 + 7 * 5) + 7 + 2


def hex_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def b64_of(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def canonical(value) -> str:  # noqa: ANN001
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


# Shared zkVM identities served by the stub prover, mirroring the
# hosted-api-catalog fixture conventions.
DEPLOYMENT_DOMAIN = "0x" + hex_of("deployment-domain")
ROOM_PROGRAM_ID = "0x" + hex_of("room-program")
DA_PROGRAM_ID = "0x" + hex_of("da-program")
AGGREGATE_PROGRAM_ID = "0x" + hex_of("aggregate-program")


class FakeClock:
    """Virtual monotonic clock: sleeping advances time instantly."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))


class StubState:
    """Shared deterministic stack state behind the HTTP stub."""

    def __init__(self):
        self.lock = threading.Lock()
        self.idempotent: dict[str, tuple[int, dict]] = {}
        self.jobs: dict[str, dict] = {}
        self.job_reads: dict[str, int] = {}
        self.operations: dict[str, dict] = {}
        self.nonces: dict[str, int] = {}
        self.ledger: list[dict] = []
        self.claims: dict[str, dict] = {}
        self.claimed_withdrawals: set[str] = set()
        self.fault_operations: set[str] = set()
        self.failovers: dict[str, dict] = {}
        self.indexer_cursor = 0
        self.tamper_outputs = False
        # Rooms whose batch was already accepted through an independent
        # single-room operation; an unchanged aggregate member for such a room
        # is stale at settlement and fails without being charged.
        self.batch_rooms: set[str] = set()
        # Canonical indexer facts (AggregateMemberOutcome) surfaced through
        # the zkdeal_getBatches JSON-RPC catalog.
        self.facts: list[dict] = []
        # --- L1 devnet model -------------------------------------------
        # Blocks advance when a transaction is mined, exactly enough for the
        # deposit confirmation depth this room policy demands.
        self.block_number = 128
        self.l1_receipts: dict[str, dict] = {}
        self.next_inbox: dict[str, int] = {}
        self.deposits: dict[tuple[str, str], dict] = {}
        # --- admission WAL model ---------------------------------------
        # Rows keyed (roomId, admissionId) with the WAL's status vocabulary,
        # plus the transaction-hash index the hosted store enforces UNIQUE.
        self.admissions: dict[tuple[str, str], dict] = {}
        self.admission_by_hash: dict[str, tuple[str, str]] = {}
        self.reject_admissions = False
        # Refuse this many otherwise-valid admissions, modelling the request a
        # resumed worker replays after its deadline has already lapsed.
        self.admission_refusals = 0
        # Ordered route trace, so a test can prove an admission was created
        # before the lease that hands it out.
        self.calls: list[str] = []

    def trace(self, name: str) -> None:
        self.calls.append(name)

    def admissions_for(self, room_id: str) -> list[dict]:
        return [
            dict(record) for key, record in sorted(self.admissions.items(), key=lambda item: int(item[0][1]))
            if key[0] == room_id
        ]

    def mine(self) -> int:
        """Include a transaction in the current block and start the next one."""
        included = self.block_number
        self.block_number += 1
        return included

    def deposit(self, transaction: dict) -> dict:
        """Model RoomManagerIntakeFacet.queueDeposit and its DepositQueued log."""
        data = transaction["data"]
        selector = driver.function_selector(driver.QUEUE_DEPOSIT_SIGNATURE)
        if data[:4] != selector or len(data) != 4 + 4 * 32:
            raise AssertionError("stub L1 got calldata that is not queueDeposit(uint64,address,uint256,address)")
        room_id = str(int.from_bytes(data[4:36], "big"))
        asset = "0x" + data[36:68][12:].hex()
        amount = int.from_bytes(data[68:100], "big")
        beneficiary = "0x" + data[100:132][12:].hex()
        if transaction["to"] != ROOM_MANAGER.lower():
            raise AssertionError("stub L1 got a queueDeposit for something other than the RoomManager")
        if asset == driver.ZERO_ADDRESS and transaction["value"] != amount:
            raise AssertionError("stub L1 got an ETH queueDeposit whose value differs from its amount")
        inbox_id = self.next_inbox.get(room_id, 0) + 1
        self.next_inbox[room_id] = inbox_id
        block = self.mine()
        entry = {
            "roomId": room_id, "depositInboxId": str(inbox_id), "asset": asset,
            "amount": amount, "beneficiary": beneficiary,
            "depositor": transaction["sender"], "queuedAtBlock": block,
            "consumed": False, "refunded": False,
        }
        self.deposits[(room_id, str(inbox_id))] = entry
        receipt = {
            "transactionHash": transaction["transactionHash"],
            "status": "0x1",
            "blockNumber": hex(block),
            "blockHash": "0x" + hex_of("l1-block:" + str(block)),
            "from": transaction["sender"],
            "to": transaction["to"],
            "logs": [{
                "address": ROOM_MANAGER,
                "topics": [
                    driver.event_topic(driver.DEPOSIT_QUEUED_SIGNATURE),
                    "0x" + int(room_id).to_bytes(32, "big").hex(),
                    "0x" + inbox_id.to_bytes(32, "big").hex(),
                    "0x" + bytes.fromhex(beneficiary[2:]).rjust(32, b"\x00").hex(),
                ],
                "data": "0x" + (
                    bytes.fromhex(asset[2:]).rjust(32, b"\x00") + amount.to_bytes(32, "big")
                ).hex(),
                "blockNumber": hex(block),
            }],
        }
        self.l1_receipts[transaction["transactionHash"]] = receipt
        return receipt

    def admit(self, room_id: str, body: dict) -> tuple[int, dict]:
        """Model AdmissionService.submitSerial and the receipt it signs."""
        transaction = decode_legacy_transaction(str(body.get("rawSignedTransaction", "")))
        existing = self.admission_by_hash.get(transaction["transactionHash"])
        if existing is not None:
            # The hosted WAL is keyed by transaction hash; a replay of the same
            # bytes returns the receipt that was already committed.
            return 200, {
                "decision": "LOCALLY_ADMITTED",
                "guarantee": "The transaction will be succeeded, reverted, or rejected by the deadline.",
                "receipt": dict(self.admissions[existing]["receipt"]),
            }
        if self.reject_admissions:
            return 400, {
                "decision": "NOT_ADMITTED",
                "reason": "admission fee is below the operator minimum for this coordinator",
                "nextAction": "Correct the transaction or use the L1 forced-transaction path.",
            }

        def refused(reason: str) -> tuple[int, dict]:
            return 400, {"decision": "NOT_ADMITTED", "reason": reason, "nextAction": "Correct the transaction."}

        if self.admission_refusals > 0:
            self.admission_refusals -= 1
            return refused("admission deadline does not leave the minimum lead time for this room")
        inbox_id = str(body.get("depositInboxId", ""))
        deposit = self.deposits.get((room_id, inbox_id))
        if deposit is None or deposit["consumed"] or deposit["refunded"]:
            return refused("deposit inbox id does not name a pending deposit in this room")
        if deposit["beneficiary"] != transaction["sender"]:
            return refused("deposit inbox id belongs to another beneficiary")
        if self.block_number - deposit["queuedAtBlock"] < ROOM_MINIMUM_DEPOSIT_CONFIRMATIONS:
            return refused("deposit inbox id has not reached the room minimum confirmation depth")
        deadline = int(str(body.get("deadlineBlock", "0")))
        maximum_batch = int(str(body.get("maximumBatchIndex", "0")))
        if deadline < self.block_number + MINIMUM_DEADLINE_LEAD_BLOCKS:
            return refused("admission deadline does not leave the minimum lead time for this room")
        if deadline > self.block_number + ROOM_MAXIMUM_ADMISSION_WINDOW or maximum_batch <= ROOM_BATCH_INDEX:
            return refused("admission deadline or maximum batch is already exhausted")
        if int(str(body.get("admissionFee", "0"))) < 0:
            return refused("admission fee is below the operator minimum for this coordinator")
        if any(
            record["request"]["depositInboxId"] == inbox_id and record["status"] != "CANCELLED"
            for key, record in self.admissions.items() if key[0] == room_id
        ):
            return refused("deposit inbox id is already reserved by a pending admission")
        admission_id = str(max(
            [int(key[1]) for key in self.admissions if key[0] == room_id] + [0],
        ) + 1)
        receipt = {
            "roomId": room_id,
            "admissionId": admission_id,
            "transactionHash": transaction["transactionHash"],
            "depositInboxId": inbox_id,
            "depositContentHash": driver.deposit_content_hash(
                deposit["depositor"], deposit["beneficiary"], deposit["asset"], deposit["amount"],
            ),
            "deadlineBlock": str(deadline),
            "maximumBatchIndex": str(maximum_batch),
            "bondEpoch": str(ROOM_BOND_EPOCH),
            "admissionFee": str(int(str(body.get("admissionFee", "0")))),
        }
        digest = driver.admission_receipt_digest(CHAIN_ID, ROOM_MANAGER, receipt)
        r, s, recovery = driver.sign_digest(digest, driver.private_key_int(ADMISSION_SIGNER_KEY))
        receipt["signature"] = "0x" + (
            r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([27 + recovery])
        ).hex()
        self.admissions[(room_id, admission_id)] = {
            "roomId": room_id, "admissionId": admission_id, "status": "COMMITTED",
            "transactionHash": transaction["transactionHash"], "sender": transaction["sender"],
            "rawSignedTransaction": str(body["rawSignedTransaction"]),
            "request": dict(body), "receipt": receipt,
        }
        self.admission_by_hash[transaction["transactionHash"]] = (room_id, admission_id)
        return 200, {
            "decision": "LOCALLY_ADMITTED",
            "guarantee": "The transaction will be succeeded, reverted, or rejected by the deadline.",
            "receipt": dict(receipt),
        }

    def lease(self, room_id: str) -> list[dict]:
        """Contiguous committed head of the room's WAL, as the store leases it.

        Re-leasing an already leased row models an expired lease: the driver's
        virtual clock jumps whole soak minutes between cycles, so any lease
        taken by an earlier cycle has long since lapsed.
        """
        entries = []
        for record in self.admissions_for(room_id):
            if record["status"] not in {"COMMITTED", "LEASED"}:
                continue
            self.admissions[(room_id, record["admissionId"])]["status"] = "LEASED"
            entries.append({
                "roomId": room_id,
                "admissionId": record["admissionId"],
                "status": "LEASED",
                "transactionHash": record["transactionHash"],
                "rawSignedTransaction": record["rawSignedTransaction"],
                "receipt": record["receipt"],
            })
        return entries

    def acknowledge(self, room_id: str, admission_ids: list) -> int:
        acknowledged = 0
        for admission_id in admission_ids:
            record = self.admissions.get((room_id, str(admission_id)))
            if record is None:
                raise AssertionError(f"stub acked an admission it never issued: {room_id}:{admission_id}")
            deposit = self.deposits.get((room_id, record["request"]["depositInboxId"]))
            if deposit is not None:
                deposit["consumed"] = True
            record["status"] = "ACKED"
            acknowledged += 1
        return acknowledged

    def l1_call(self, method: str, params: list):
        if method == "eth_chainId":
            return hex(CHAIN_ID)
        if method == "eth_blockNumber":
            return hex(self.block_number)
        if method == "eth_gasPrice":
            return hex(10 ** 9)
        if method == "eth_getTransactionCount":
            return hex(len(self.l1_receipts))
        if method == "eth_call":
            call = params[0] if params and isinstance(params[0], dict) else {}
            if str(call.get("to", "")).lower() != ROOM_MANAGER.lower():
                raise AssertionError("stub L1 eth_call targeted something other than the RoomManager")
            data = bytes.fromhex(str(call.get("data", "0x"))[2:])
            if data[:4] != driver.function_selector(driver.ROOM_STATE_SIGNATURE):
                raise AssertionError("stub L1 eth_call is not roomState(uint64)")
            room_id = str(int.from_bytes(data[4:36], "big"))
            return room_state_blob(ROOM_BATCH_INDEX, self.next_inbox.get(room_id, 0))
        if method == "eth_sendRawTransaction":
            transaction = decode_legacy_transaction(str(params[0]))
            if transaction["sender"] != DEPOSITOR_ADDRESS:
                raise AssertionError(f"stub L1 got a deposit signed by {transaction['sender']}")
            if transaction["chainId"] != CHAIN_ID:
                raise AssertionError("stub L1 got a transaction for another chain")
            if transaction["transactionHash"] not in self.l1_receipts:
                self.deposit(transaction)
            return transaction["transactionHash"]
        if method == "eth_getTransactionReceipt":
            return self.l1_receipts.get(str(params[0]).lower())
        raise AssertionError(f"stub L1 got unknown JSON-RPC method {method}")

    def charge(self, usage: int, wei: int) -> None:
        entry_id = len(self.ledger) + 1
        self.ledger.append({
            "entryId": entry_id,
            "chargeId": f"charge-{entry_id}",
            "usageUnits": usage,
            "chargeWei": str(wei),
        })

    def operation(self, idem_key: str, kind: str, body: dict, correlation: str) -> dict:
        operation_id = "op-" + hex_of("op:" + idem_key)[:24]
        if operation_id in self.operations:
            return self.operations[operation_id]
        accounts = {
            "room-batch": ADDR_OPERATIONS,
            "room-aggregate": ADDR_OPERATIONS,
            "sponsor-reserve": ADDR_SPONSOR,
            "sponsor-renew": ADDR_SPONSOR,
            "pool-checkpoint": ADDR_ORACLE,
            "pool-disposal": ADDR_SPONSOR,
            "withdrawal-claim": ADDR_WITHDRAW,
        }
        selectors = {
            "room-batch": "0x11223344",
            "room-aggregate": "0x5e8b37ac",
            "sponsor-reserve": "0x827ac259",
            "sponsor-renew": "0xf180fe5d",
            "pool-checkpoint": "0xe19bc67e",
            "pool-disposal": "0xed97f11a",
            "withdrawal-claim": "0xb051a9f8",
        }
        sender = accounts[kind]
        nonce = self.nonces.get(sender, 0)
        self.nonces[sender] = nonce + 1
        transaction_hash = "0x" + hex_of("tx:" + operation_id)
        # Selector and transactionType live only under the immutable
        # operationBinding, exactly like ManagedL1OperationResult.
        binding: dict = {
            "selector": selectors[kind],
            "transactionType": 3 if kind == "room-aggregate" else 2,
        }
        if kind == "room-batch":
            binding["roomId"] = str(body.get("roomId", ""))
            # An independently accepted batch makes any later unchanged
            # aggregate member for the same room stale at settlement.
            self.batch_rooms.add(str(body.get("roomId", "")))
        aggregate_applied = 0
        if kind == "room-aggregate":
            artifacts = body.get("artifacts") or {}
            agg_members = artifacts.get("members") or []
            da_entries = artifacts.get("dataAvailability") or []
            prove_job_id = str(((artifacts.get("aggregate") or {}).get("prove") or {}).get("jobId", ""))
            prove_job = self.jobs.get(prove_job_id)
            prove_result = json.loads(prove_job["resultBytes"]) if prove_job else {}
            statement = str(prove_result.get("statement", ""))
            block_hash = "0x" + hex_of("block:" + operation_id)
            member_bindings = []
            for member_index, member in enumerate(agg_members):
                room = str(member.get("roomId", ""))
                applied = room not in self.batch_rooms
                aggregate_applied += 1 if applied else 0
                member_bindings.append({
                    "memberIndex": member_index, "roomId": room, "batchIndex": "1",
                    "prepare": member.get("prepare"), "prove": member.get("prove"),
                    "verify": member.get("verify"),
                })
                self.facts.append({
                    "factId": str(len(self.facts) + 1),
                    "factKind": "aggregate",
                    "roomId": room,
                    "tenantId": "tenant-a",
                    "blockNumber": "12",
                    "blockHash": block_hash,
                    "payload": {
                        "args": {
                            "aggregateHash": statement, "memberIndex": member_index,
                            "roomId": room, "batchIndex": "1", "applied": applied,
                            "failureSelector": "0x00000000" if applied else "0xdeadbeef",
                        },
                        "provenance": {
                            "eventName": "AggregateMemberOutcome",
                            "transactionHash": transaction_hash,
                            "blockNumber": "12",
                            "blockHash": block_hash,
                        },
                    },
                })
            binding.update({
                "kind": "ROOM_AGGREGATE",
                "aggregateStatement": statement,
                "aggregateProgramId": str(prove_result.get("programId", "")),
                "zkdealBlobCount": len(da_entries),
                "members": member_bindings,
                "confirmationPolicy": body.get("confirmationPolicy"),
            })
        operation = {
            "operationId": operation_id,
            "status": "FINALIZED",
            "finalized": True,
            "confirmations": 64,
            "chainId": 31337,
            "from": sender,
            "to": ROOM_MANAGER if kind in {"room-batch", "room-aggregate"} else ROOM_POOL,
            "nonce": str(nonce),
            "transactionHash": transaction_hash,
            "correlationId": correlation,
            "castBroadcast": False,
            "receiptSource": {
                "canonical": True,
                "providerIds": ["rpc-a", "rpc-b"],
                "observedAt": "2026-08-22T00:00:00.000Z",
            },
            "binding": binding,
        }
        if kind == "sponsor-reserve":
            operation["allocationId"] = "0x" + hex_of("alloc:" + operation_id)
            operation["payer"] = ADDR_SPONSOR
            operation["beneficiary"] = str(body.get("beneficiary", "")).lower()
            operation["refundRecipient"] = ADDR_SPONSOR
        self.operations[operation_id] = operation
        if kind == "room-aggregate":
            # Success-only charging: exactly one charge per applied member.
            for _index in range(aggregate_applied):
                self.charge(2, 5)
        charges = {
            "room-batch": (1, 1, 3),
            "sponsor-reserve": (1, 3, 7),
            "withdrawal-claim": (1, 1, 2),
        }
        if kind in charges:
            count, usage, wei = charges[kind]
            for _index in range(count):
                self.charge(usage, wei)
        return operation

    def queue_job(self, idem_key: str, request_body: dict) -> dict:
        job_id = "pj-" + hex_of("job:" + idem_key)[:20]
        if job_id in self.jobs:
            return self.jobs[job_id]
        endpoint = str(request_body.get("endpoint", ""))
        request = request_body.get("request") or {}
        room_id = str(request_body.get("roomId", ""))
        if endpoint == "/hosting/v1/rooms/prepare-batch":
            digest = hex_of("prep:" + job_id)
            journal_hash = "0x" + hex_of("journal:" + job_id)
            guest_journal = {
                "protocol_version": 6, "room_id": room_id,
                "cycle": str(request.get("cycle", "")),
            }
            result = {
                "schemaVersion": 1,
                "fixture": False,
                "preparedFrom": "live-room-engine-state",
                "batchInput": "BatchInputV5",
                "programId": ROOM_PROGRAM_ID,
                "journal": guest_journal,
                "journalHash": journal_hash,
                "prepareArtifactDigest": digest,
                "contentAddress": digest,
                "proofRequest": {
                    "production": True,
                    "proofMode": "groth16",
                    "inputDigest": "0x" + digest,
                    "journal": guest_journal,
                    "journalHash": journal_hash,
                    "programId": ROOM_PROGRAM_ID,
                },
                "provisionalSubmission": {
                    "journal": {
                        "protocolVersion": "6",
                        "deploymentDomain": DEPLOYMENT_DOMAIN,
                        "roomId": room_id,
                        "batchIndex": "1",
                        "admissionCursorBefore": "0",
                        "admissionCursorAfter": "0",
                        "l1InclusionDeadline": "1000",
                    },
                    "seal": "0x",
                    "canonicalBatchData": "0x" + hex_of("canonical:" + job_id)[:32],
                },
            }
        elif endpoint == "/v5/rooms/prove":
            journal_hash = str(request.get("journalHash", "0x" + hex_of("journal:" + job_id)))
            result = {
                "proofMode": "groth16",
                "realCuda": True,
                "gpuUuid": "GPU-stub-4090",
                "backendId": "risc0",
                "programId": str(request.get("programId", ROOM_PROGRAM_ID)),
                "inputDigest": request.get("inputDigest", "0x" + hex_of("in:" + job_id)),
                "journal": request.get("journal", {}),
                "journalHash": journal_hash,
                "receiptB64": b64_of("room-receipt:" + journal_hash),
                "ethereumSealB64": "c2VhbA==",
            }
        elif endpoint == "/v5/rooms/verify":
            result = {
                "ok": True,
                "realReceipt": True,
                "proofMode": "groth16",
                "journalHash": request.get("journalHash", "0x" + hex_of("j:" + job_id)),
                "ethereumSealB64": "c2VhbA==",
            }
        elif endpoint in {
            "/v5/data-availability/prepare", "/v5/data-availability/prove",
            "/v5/data-availability/verify",
        }:
            result = self.data_availability_result(endpoint, request)
        elif endpoint in {"/v5/aggregates/prove", "/v5/aggregates/verify"}:
            result = self.aggregate_result(endpoint, request)
        else:
            raise AssertionError(f"stub queue got unknown endpoint {endpoint}")
        raw = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
        job = {"jobId": job_id, "resultBytes": raw, "resultDigest": hashlib.sha256(raw).hexdigest()}
        self.jobs[job_id] = job
        return job

    def data_availability_result(self, endpoint: str, request: dict) -> dict:
        """/v5/data-availability/* contract from hosting.rs: the prover derives
        the KZG blob vectors from the canonical data and every command binds
        one statement to the complete equivalence witness."""
        witness = dict(request.get("equivalenceWitness") or {})
        base = hex_of("da:{}:{}:{}".format(
            witness.get("roomId"), witness.get("journalHash"), witness.get("blobStartIndex"),
        ))
        if "blobVersionedHashes" not in witness:
            witness["blobVersionedHashes"] = ["0x" + hex_of("vh:" + base)]
            witness["commitments"] = ["0x" + (hex_of("comm:" + base) + hex_of("comm2:" + base))[:96]]
            witness["evaluationPoints"] = ["0x" + hex_of("evp:" + base)]
            witness["evaluations"] = ["0x" + hex_of("evl:" + base)]
        statement = "0x" + hex_of("da-stmt:" + canonical(witness))
        blob_b64 = base64.b64encode(bytes.fromhex(hex_of("blob:" + base)) * 4).decode()

        def manifest(seal: str) -> dict:
            return {
                "canonicalDataHash": "0x" + hex_of("cdh:" + base),
                "canonicalDataLength": "1",
                "blobStartIndex": witness.get("blobStartIndex", 0),
                "blobVersionedHashes": witness["blobVersionedHashes"],
                "commitments": witness["commitments"],
                "evaluationPoints": witness["evaluationPoints"],
                "evaluations": witness["evaluations"],
                "kzgProofs": ["0x" + "00" * 48],
                "equivalenceSeal": seal,
                "fallbackDeadlineBlock": "0",
                "fallbackSignature": "0x",
            }

        if endpoint == "/v5/data-availability/prepare":
            return {
                "equivalenceWitness": witness,
                "statement": statement,
                "dataAvailabilityManifest": manifest("0x"),
                "blobsB64": [blob_b64],
                "encoding": "31-byte-big-endian-field-elements-v1",
            }
        if endpoint == "/v5/data-availability/prove":
            return {
                "kind": "data-availability-equivalence",
                "statement": statement,
                "programId": DA_PROGRAM_ID,
                "proofMode": "groth16",
                "realCuda": True,
                "gpuUuid": "GPU-stub-4090",
                "receiptB64": b64_of("da-receipt:" + statement),
                "ethereumSealHex": "0x1234",
                "equivalenceWitness": witness,
                "dataAvailabilityManifest": manifest("0x1234"),
                "blobsB64": [blob_b64],
            }
        return {
            "ok": True,
            "statement": statement,
            "programId": DA_PROGRAM_ID,
            "dataAvailabilityManifest": manifest("0x"),
        }

    def aggregate_result(self, endpoint: str, request: dict) -> dict:
        """/v5/aggregates/prove + /v5/aggregates/verify contract from
        hosting.rs cmd_prove_aggregate_v1 / cmd_verify_aggregate_v1."""
        witness = request.get("aggregateWitness") or {}
        members = witness.get("members") or []
        statement = "0x" + hex_of("agg-stmt:" + canonical(witness))
        if endpoint == "/v5/aggregates/prove":
            return {
                "kind": "recursive-room-aggregate",
                "statement": statement,
                "programId": AGGREGATE_PROGRAM_ID,
                "proofMode": "groth16",
                "realCuda": True,
                "gpuUuid": "GPU-stub-4090",
                "receiptB64": b64_of("agg-receipt:" + statement),
                "ethereumSealHex": "0x5678",
                "aggregateWitness": witness,
                "memberCount": len(members),
            }
        return {
            "ok": True,
            "statement": statement,
            "programId": AGGREGATE_PROGRAM_ID,
            "memberCount": len(members),
        }


class StubHandler(BaseHTTPRequestHandler):
    server_version = "zkdeal-soak-stub/1"

    @property
    def stub(self) -> StubState:
        return self.server.stub  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: ANN002
        pass

    def send_json(self, status: int, value: dict) -> None:
        payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_raw(self, status: int, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def body(self) -> dict:
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}

    def idem_key(self) -> str:
        return str(self.headers.get("idempotency-key", ""))

    def correlation(self) -> str:
        return str(self.headers.get("x-correlation-id", ""))

    def replay_or(self, compute):  # noqa: ANN001
        key = self.idem_key()
        with self.stub.lock:
            if key and key in self.stub.idempotent:
                status, value = self.stub.idempotent[key]
            else:
                status, value = compute()
                if key:
                    self.stub.idempotent[key] = (status, value)
        self.send_json(status, value)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        stub = self.stub
        if path == "/hosting/v1/capabilities":
            self.send_json(200, {
                "schemaVersion": 1,
                "negotiation": {"header": "Accept-Schema-Version", "supported": [1]},
                "managedL1Operations": {
                    "roomBatch": {"enabled": True},
                    "roomAggregate": {"enabled": True},
                    "poolSponsorMutation": {"enabled": True},
                },
                "addresses": {
                    "roomManager": ROOM_MANAGER,
                    "operationsAccount": ADDR_OPERATIONS,
                    "roomPool": ROOM_POOL,
                    "sponsorAccount": ADDR_SPONSOR,
                },
            })
            return
        if path == "/hosting/v1/ready":
            self.send_json(200, {"status": "ready"})
            return
        if path in {"/headless/health", "/prover/health", "/logs/health", "/backup/health"}:
            self.send_json(200, {"status": "ok", "cuda": True})
            return
        if path == "/hosting/v1/indexer/status":
            with stub.lock:
                stub.indexer_cursor += 1
                cursor = stub.indexer_cursor
            self.send_json(200, {
                "schemaVersion": 1,
                "indexerHeadMatchesL1": True,
                "unresolvedSafetyEvents": 0,
                "cursor": cursor,
                "eventId": f"evt-{cursor}",
            })
            return
        if path.startswith("/queue/v1/jobs/") and path.endswith("/result"):
            job_id = path.split("/")[4]
            with stub.lock:
                job = stub.jobs.get(job_id)
                if job is None:
                    self.send_json(404, {"error": "unknown job"})
                    return
                stub.job_reads[job_id] = stub.job_reads.get(job_id, 0) + 1
                payload = job["resultBytes"]
                if stub.tamper_outputs and stub.job_reads[job_id] >= 2:
                    # Still valid JSON, but different bytes: the sealed-output
                    # digest re-check must fail closed.
                    payload = payload[:-1] + b" \n"
            self.send_raw(200, payload)
            return
        if path.startswith("/queue/v1/jobs/"):
            job_id = path.split("/")[4]
            with stub.lock:
                job = stub.jobs.get(job_id)
            if job is None:
                self.send_json(404, {"error": "unknown job"})
                return
            self.send_json(200, {"jobId": job_id, "status": "DONE", "resultDigest": job["resultDigest"]})
            return
        if path.startswith("/hosting/v1/l1-transactions/"):
            operation_id = path.rsplit("/", 1)[1]
            with stub.lock:
                operation = stub.operations.get(operation_id)
            if operation is None:
                self.send_json(404, {"error": "unknown operation"})
                return
            self.send_json(200, operation)
            return
        if path == "/hosting/v1/billing/ledger":
            query = dict(
                part.split("=", 1) for part in urlsplit(self.path).query.split("&") if "=" in part
            )
            after = int(query.get("after", "0"))
            with stub.lock:
                entries = [entry for entry in stub.ledger if entry["entryId"] > after]
            self.send_json(200, {"entries": entries})
            return
        if path == "/hosting/v1/usage":
            with stub.lock:
                entries = [
                    {"entryId": entry["entryId"], "usageUnits": entry["usageUnits"]}
                    for entry in stub.ledger
                ]
            self.send_json(200, {"entries": entries})
            return
        if re_match := __import__("re").fullmatch(r"/hosting/v1/withdrawals/([^/]+)/([^/]+)/([^/]+)/proof", path):
            del re_match
            self.send_json(200, {
                "realProof": True,
                "finalized": True,
                "proof": {"leaf": "0x" + hex_of("leaf"), "siblings": []},
            })
            return
        if path.startswith("/hosting/v1/withdrawal-claims/"):
            claim_id = path.rsplit("/", 1)[1]
            with stub.lock:
                claim = stub.claims.get(claim_id)
            if claim is None:
                self.send_json(404, {"error": "unknown claim"})
                return
            self.send_json(200, claim)
            return
        if path == "/hosting/v1/sponsorships":
            self.send_json(200, {"sponsorships": []})
            return
        if path == "/fault/capabilities":
            self.send_json(200, {
                "schemaVersion": 1,
                "candidateId": CANDIDATE_ID,
                "planSha256": PLAN_SHA,
                "hostedIntegrationToken": HOSTED_TOKEN,
            })
            return
        self.send_json(404, {"error": f"stub GET route not found: {path}"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        stub = self.stub
        body = self.body()
        if path == "/hosting/v1/rooms/deployments":
            self.replay_or(lambda: (202, {
                "operationId": "room-intent-" + hex_of("room:" + self.idem_key())[:12],
                "status": "ACCEPTED",
                "roomId": str(body.get("roomId", "")),
            }))
            return
        if path in {"/rpc-a", "/rpc-a/", "/rpc-b", "/rpc-b/"}:
            if body.get("jsonrpc") != "2.0":
                self.send_json(400, {
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32600, "message": "invalid request"},
                })
                return
            with stub.lock:
                stub.trace("l1:" + str(body.get("method", "")))
                result = stub.l1_call(str(body.get("method", "")), body.get("params") or [])
            self.send_json(200, {"jsonrpc": "2.0", "id": body.get("id"), "result": result})
            return
        admission_submit = __import__("re").fullmatch(r"/rooms/([^/]+)/transactions", path)
        if admission_submit:
            room_id = admission_submit.group(1)
            if not str(self.headers.get("authorization", "")).startswith("Bearer eph_"):
                self.send_json(401, {
                    "decision": "NOT_ADMITTED",
                    "reason": "This coordinator requires an operator-issued admission credential.",
                    "nextAction": "Present the operator credential.",
                })
                return
            with stub.lock:
                stub.trace("admission-submit:" + room_id)
                status, value = stub.admit(room_id, body)
            self.send_json(status, value)
            return
        if path.startswith("/hosting/v1/admissions/") and path.endswith("/lease"):
            room_id = path.split("/")[4]
            with stub.lock:
                stub.trace("lease:" + room_id)
                entries = stub.lease(room_id)
            self.send_json(200, {"entries": entries})
            return
        if path.startswith("/hosting/v1/admissions/") and path.endswith("/ack"):
            room_id = path.split("/")[4]

            def acknowledge():
                # replay_or already holds the stub lock.
                stub.trace("ack:" + room_id)
                return 200, {"acknowledged": stub.acknowledge(room_id, body.get("admissionIds", []))}

            self.replay_or(acknowledge)
            return
        if path == "/queue/v1/jobs":
            key = self.idem_key()
            with stub.lock:
                job = stub.queue_job(key, body)
            self.send_json(202, {"jobId": job["jobId"]})
            return
        if path == "/hosting/v1/json-rpc":
            if body.get("jsonrpc") != "2.0":
                self.send_json(400, {
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32600, "message": "invalid request"},
                })
                return
            method = str(body.get("method", ""))
            if method in {"zkdeal_getBatches", "zkdeal_getRoomEvents"}:
                with stub.lock:
                    facts = [dict(fact) for fact in stub.facts]
                self.send_json(200, {"jsonrpc": "2.0", "id": body.get("id"), "result": facts})
                return
            self.send_json(200, {
                "jsonrpc": "2.0", "id": body.get("id"),
                "error": {"code": -32601, "message": f"unknown method {method}"},
            })
            return
        if path == "/hosting/v1/sponsorships":
            self.replay_or(lambda: (201, {
                "sponsorshipId": str(body.get("sponsorshipId", "")), "status": "ACTIVE",
            }))
            return
        operation_kinds = {
            "/hosting/v1/l1-operations/room-batches": "room-batch",
            "/hosting/v1/l1-operations/room-aggregates": "room-aggregate",
            "/hosting/v1/l1-operations/pool-finalized-checkpoints": "pool-checkpoint",
            "/hosting/v1/l1-operations/pool-beneficiary-disposals": "pool-disposal",
        }
        if path == "/hosting/v1/l1-operations/pool-sponsor-mutations":
            mutation = body.get("mutation") or {}
            kind = "sponsor-reserve" if mutation.get("kind") == "reserveAndStartForWithDataAvailabilityWithPermit" \
                else "sponsor-renew"
            self.replay_or(lambda: (200, stub.operation(self.idem_key(), kind, body, self.correlation())))
            return
        if path in operation_kinds:
            kind = operation_kinds[path]
            self.replay_or(lambda: (200, stub.operation(self.idem_key(), kind, body, self.correlation())))
            return
        withdrawal = __import__("re").fullmatch(r"/hosting/v1/withdrawals/([^/]+)/([^/]+)/([^/]+)/claims", path)
        if withdrawal:
            slot = ":".join(withdrawal.groups())

            def claim():
                if slot in stub.claimed_withdrawals:
                    return 409, {"error": {"code": "REPLAY_REJECTED", "message": "withdrawal already claimed"}}
                stub.claimed_withdrawals.add(slot)
                claim_id = "wc-" + hex_of("claim:" + self.idem_key())[:12]
                operation = stub.operation("wclaim:" + claim_id, "withdrawal-claim", {}, self.correlation())
                stub.claims[claim_id] = {
                    "claimId": claim_id, "status": "FINALIZED",
                    "operationId": operation["operationId"],
                }
                return 202, {"claimId": claim_id, "status": "QUEUED"}

            self.replay_or(claim)
            return
        if path == "/fault/v1/faults":
            if not str(self.headers.get("authorization", "")).startswith("Bearer eph_"):
                self.send_json(401, {"error": {"code": "UNAUTHORIZED", "message": "scoped bearer required"}})
                return
            binding = body.get("binding")
            expected_binding = {
                "candidateId": CANDIDATE_ID, "planSha256": PLAN_SHA,
                "hostedIntegrationToken": HOSTED_TOKEN,
            }
            if binding != expected_binding:
                self.send_json(409, {"error": {"code": "CANDIDATE_BINDING_MISMATCH", "message": "binding differs"}})
                return
            action = str(body.get("action", ""))
            parameters = body.get("parameters") or {}

            def fault():
                operation_id = "fc-" + hex_of("fc:" + self.idem_key())[:40]
                if action == "l1-reorg":
                    prepared = parameters.get("preparedOperationId")
                    if parameters.get("phase") == "prepare":
                        result = {
                            "phase": "PREPARED", "depth": parameters.get("depth"),
                            "previousBlockHash": "0x" + hex_of("prev:" + operation_id),
                        }
                    else:
                        if prepared not in stub.fault_operations:
                            return 409, {"error": {"code": "INCOMPLETE_OPERATION", "message": "not prepared"}}
                        result = {
                            "phase": "REPLACED", "rollbackDepth": parameters.get("depth"),
                            "previousBlockHash": "0x" + hex_of("prev:" + str(prepared)),
                            "canonicalBlockHash": "0x" + hex_of("canon:" + str(prepared)),
                        }
                elif action == "rpc-disagreement":
                    if parameters.get("phase") == "start":
                        result = {
                            "phase": "DISAGREEING",
                            "agreedBeforeA": "0x" + hex_of("agree"), "agreedBeforeB": "0x" + hex_of("agree"),
                            "disagreeA": "0x" + hex_of("disagree-a"), "disagreeB": "0x" + hex_of("disagree-b"),
                        }
                    else:
                        if parameters.get("preparedOperationId") not in stub.fault_operations:
                            return 409, {"error": {"code": "INCOMPLETE_OPERATION", "message": "not prepared"}}
                        result = {
                            "phase": "RESTORED",
                            "restoredA": "0x" + hex_of("restored"), "restoredB": "0x" + hex_of("restored"),
                        }
                elif action == "indexer-rollback":
                    if parameters.get("preparedOperationId") not in stub.fault_operations:
                        return 409, {"error": {"code": "INCOMPLETE_OPERATION", "message": "not prepared"}}
                    result = {"action": action, "applied": True, "rollbackApplied": True, "rollbackDepth": 3}
                elif action in {
                    "headless-restart", "prover-restart", "object-store-restart",
                    "database-restart", "coordinator-terminate",
                }:
                    result = {"action": action, "applied": True, "logicalTarget": action}
                else:
                    return 400, {"error": {"code": "INVALID_REQUEST", "message": f"unknown action {action}"}}
                stub.fault_operations.add(operation_id)
                result.update({
                    "operationId": operation_id,
                    "closureSha256": hex_of("closure:" + operation_id),
                    "correlationId": self.correlation(),
                    "candidateId": CANDIDATE_ID,
                })
                return 200, result

            self.replay_or(fault)
            return
        if path == "/failover/v1/failovers":
            candidate = str(body.get("candidateId", ""))

            def prepare():
                if body.get("activeCoordinatorId") != ACTIVE_ID or body.get("standbyCoordinatorId") != STANDBY_ID:
                    return 409, {"code": "topology-mismatch", "message": "coordinator ids differ"}
                response = {
                    "operationId": candidate,
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
                    "primaryTargetLsn": "0/5000000",
                    "standbyReplayLsn": "0/5000000",
                    "checkpointAgeSeconds": 0,
                }
                stub.failovers[candidate] = {"prepare": response}
                return 200, response

            self.replay_or(prepare)
            return
        failover_commit = __import__("re").fullmatch(r"/failover/v1/failovers/([^/]+)/commit", path)
        if failover_commit:
            candidate = failover_commit.group(1)

            def commit():
                record = stub.failovers.get(candidate)
                if not record:
                    return 409, {"code": "not-prepared", "message": "operation has no prepared boundary"}
                owner_hash = str(body.get("ownerResponseSha256", ""))
                if len(owner_hash) != 64:
                    return 400, {"code": "invalid-request", "message": "ownerResponseSha256 required"}
                return 200, {
                    "operationId": candidate,
                    "writerRouteCommitted": True,
                    "writerCoordinatorId": STANDBY_ID,
                    "oldWriterRouteRemoved": True,
                    "stableDatabaseEndpointRouted": True,
                    "signerAuthorityActivatedAfterFence": True,
                    "rtoSeconds": 3,
                    "targetCapturedAtUnixMs": 1, "committedAtUnixMs": 3001,
                }

            self.replay_or(commit)
            return
        self.send_json(404, {"error": f"stub POST route not found: {path}"})


class OwnerSoakDriverTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".test-tmp" / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.stub = StubState()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        self.server.stub = self.stub  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.manifest = self.build_manifest()
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.journal_path = self.root / "journal.ndjson"
        self.env = self.build_env()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.root, ignore_errors=True)
        parent = ROOT / ".test-tmp"
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

    def build_manifest(self) -> dict:
        digest = "a" * 64
        return {
            "schemaVersion": 1,
            "kind": "zkdeal-release-soak",
            "durationSeconds": 43200,
            "umbrellaSourceManifestSha256": digest,
            "sourceBundleArchiveSha256": digest,
            "sourceClosureSha256": digest,
            "physicalScenario": {
                "settlementScenarioSha256": digest,
                "deploymentAddressesSha256": digest,
                "ownerDurableCapabilitiesSha256": digest,
                "ownerAcceptanceToken": f"sha256:{digest}",
                "hostedBatchInput": "BatchInputV5",
                "fixturePrepare": False,
                "realCudaProof": True,
                "aggregateMembers": 8,
                "transactionBlobs": 6,
                "partialSuccessApplied": 7,
                "partialSuccessFailed": 1,
                "successOnlyCharging": True,
                "withdrawalClaim": True,
                "sponsorship": True,
                "preFinalityReorg": True,
                "freshDeployment": True,
                "restartResume": True,
                "ownerDurablePublishing": True,
                "castEncodingOnly": True,
                "directBroadcastAllowed": False,
            },
            "images": {
                name: f"registry.invalid/zkdeal/{name}@sha256:{digest}"
                for name in ("coordinator", "indexer", "reconciler", "headless", "prover", "ownerAcceptanceRunner")
            },
            "trustRoots": {
                "contractsAbiSha256": digest,
                "circuitManifestSha256": digest,
                "zkvmArtifactsSha256": digest,
                "generatedTrustRootClosureSha256": digest,
            },
            "chainSeed": {
                "chainId": 31337,
                "genesisHash": "0x" + "b" * 64,
                "seedSha256": digest,
                "rpcEndpoints": ["http://rpc-a:8545", "http://rpc-b:8545"],
            },
            "expected": {"usageUnits": EXPECTED_USAGE, "chargesWei": str(EXPECTED_WEI)},
            "budgets": {
                "maxUnresolvedSafetyEvents": 0,
                "maxUnresolvedClaims": 0,
                "maxDuplicateNonces": 0,
                "maxDuplicateCharges": 0,
                "maxFairnessWaitMs": 5000,
                "maxDeadlineMisses": 0,
            },
            "scheduledFaults": [
                {"kind": name, "atSecond": (index + 1) * 100}
                for index, name in enumerate(sorted(REQUIRED_FAULTS))
            ],
        }

    def build_env(self) -> dict[str, str]:
        tokens_dir = self.root / "tokens"
        tokens_dir.mkdir()
        env = {
            "ZKDEAL_SOAK_MANIFEST": str(self.manifest_path),
            "ZKDEAL_SOAK_JOURNAL": str(self.journal_path),
            "ZKDEAL_SOAK_STATE": str(self.root / "runner-state.json"),
            "ZKDEAL_SOAK_RESUME": "0",
            "SOAK_CANDIDATE_ID": CANDIDATE_ID,
            "HOSTED_INTEGRATION_TOKEN": HOSTED_TOKEN,
            "ACTIVE_COORDINATOR_ID": ACTIVE_ID,
            "STANDBY_COORDINATOR_ID": STANDBY_ID,
            "SOAK_FAILOVER_WITNESS_COUNT": "2",
            "COORDINATOR_URL": self.base,
            "INDEXER_URL": self.base,
            "QUEUE_URL": self.base,
            "HEADLESS_URL": self.base + "/headless",
            "PROVER_URL": self.base + "/prover",
            "LOG_QUERY_URL": self.base + "/logs",
            "ACCEPTANCE_BACKUP_URL": self.base + "/backup",
            "ACCEPTANCE_FAULT_URL": self.base + "/fault",
            "FAILOVER_PROVIDER_URL": self.base + "/failover",
            "L1_RPC_A": self.base + "/rpc-a",
            "L1_RPC_B": self.base + "/rpc-b",
        }
        for alias, env_name in driver.TOKEN_FILE_ENV.items():
            path = tokens_dir / f"{alias}.token"
            path.write_text("eph_" + hex_of("token:" + alias)[:40] + "\n", encoding="utf-8")
            os.chmod(path, 0o600)
            env[env_name] = str(path)
        return env

    def run_soak(self, env: dict[str, str], journal_hook=None, max_runs: int = 6):  # noqa: ANN001
        """Run the worker in-process; a WorkerKilled simulates the SIGKILL and
        the loop respawns with resume enabled, exactly like the supervisor."""

        def kill_hook():
            raise driver.WorkerKilled("simulated SIGKILL")

        runs = 0
        current = dict(env)
        while True:
            runs += 1
            if runs > max_runs:
                raise AssertionError("worker kill/respawn loop did not converge")
            try:
                clock = FakeClock()
                code = driver.run_worker(
                    environ=current, clock=clock, kill_hook=kill_hook, journal_hook=journal_hook,
                )
                return code, runs
            except driver.WorkerKilled:
                current = {**current, "ZKDEAL_SOAK_RESUME": "1"}

    def read_journal(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.journal_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def workload_harness(self):
        """One negotiated Workload against the stub, without the timeline."""
        clock = FakeClock()
        http = driver.Http(self.env, clock)
        stack = driver.Stack(http, clock, self.env, self.manifest)
        journal = driver.Journal(self.root / "unit-journal.ndjson")
        state = driver.DriverState(self.root / "unit-state.json", sha256_file(self.manifest_path))
        workload = driver.Workload(stack, journal, state, self.env, self.manifest)
        stack.capabilities()
        return workload, journal

    def test_manifest_fixture_is_a_valid_release_soak_manifest(self):
        validate_manifest(self.manifest)

    def test_l1_primitives_match_published_vectors(self):
        # Keccak-256 (Ethereum padding, not SHA3) and canonical RLP.
        self.assertEqual(
            driver.keccak256(b"").hex(),
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        )
        self.assertEqual(
            driver.keccak256(b"abc").hex(),
            "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
        )
        self.assertEqual(driver.rlp_encode(b"dog").hex(), "83646f67")
        self.assertEqual(driver.rlp_encode([b"cat", b"dog"]).hex(), "c88363617483646f67")
        self.assertEqual(driver.rlp_encode([]).hex(), "c0")
        self.assertEqual(driver.rlp_encode(0).hex(), "80")
        self.assertEqual(driver.rlp_encode(15).hex(), "0f")
        self.assertEqual(driver.rlp_encode(1024).hex(), "820400")
        # The two published anvil genesis identities.
        key = driver.private_key_int(DEPOSITOR_KEY)
        self.assertEqual(driver.private_key_address(key), DEPOSITOR_ADDRESS)
        self.assertEqual(
            driver.private_key_address(driver.private_key_int(
                "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
            )),
            "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
        )
        # Deterministic, low-s, recoverable, and valid under the independent
        # ECDSA verification equation r == x((z/s)G + (r/s)Q).
        digest = driver.keccak256(b"zkdeal owner soak admission vector")
        r, s, recovery = driver.sign_digest(digest, key)
        self.assertEqual((r, s, recovery), driver.sign_digest(digest, key))
        self.assertLessEqual(s, driver.SECP256K1_HALF_N)
        public_key = driver.recover_public_key(digest, r, s, recovery)
        self.assertEqual(driver.public_key_address(public_key), DEPOSITOR_ADDRESS)
        order = driver.SECP256K1_N
        inverse = pow(s, order - 2, order)
        point = driver._to_affine(driver._jacobian_add(
            driver._jacobian_multiply(driver.SECP256K1_G, (int.from_bytes(digest, "big") * inverse) % order),
            driver._jacobian_multiply((public_key[0], public_key[1], 1), (r * inverse) % order),
        ))
        self.assertEqual(point[0] % order, r)
        # A signed EIP-155 transaction recovers to its signer through an
        # independently reconstructed pre-image.
        raw, transaction_hash = driver.sign_legacy_transaction(
            nonce=7, gas_price=10 ** 9, gas_limit=21_000, to=ROOM_MANAGER,
            value=5, data=b"\x01\x02", chain_id=CHAIN_ID, private_key=key,
        )
        decoded = decode_legacy_transaction(raw)
        self.assertEqual(decoded["sender"], DEPOSITOR_ADDRESS)
        self.assertEqual(decoded["chainId"], CHAIN_ID)
        self.assertEqual(decoded["nonce"], 7)
        self.assertEqual(decoded["transactionHash"], transaction_hash)
        # The EIP-712 admission digest round-trips against a known signer.
        receipt = {
            "roomId": "101", "admissionId": "4",
            "transactionHash": transaction_hash,
            "depositInboxId": "2", "depositContentHash": "0x" + hex_of("content"),
            "deadlineBlock": "500", "maximumBatchIndex": "9",
            "bondEpoch": str(ROOM_BOND_EPOCH), "admissionFee": "0",
        }
        admission_digest = driver.admission_receipt_digest(CHAIN_ID, ROOM_MANAGER, receipt)
        signer_key = driver.private_key_int(ADMISSION_SIGNER_KEY)
        sr, ss, srec = driver.sign_digest(admission_digest, signer_key)
        signature = "0x" + (
            sr.to_bytes(32, "big") + ss.to_bytes(32, "big") + bytes([27 + srec])
        ).hex()
        self.assertEqual(
            driver.recover_signature_address(admission_digest, signature, "signature"),
            ADMISSION_SIGNER_ADDRESS,
        )

    def test_pulse_creates_its_own_admission_before_leasing(self):
        workload, journal = self.workload_harness()
        self.assertEqual(self.stub.admissions_for(driver.Workload.PULSE_ROOM), [])
        workload.pulse(0)
        room_id = driver.Workload.PULSE_ROOM
        admissions = self.stub.admissions_for(room_id)
        self.assertEqual(len(admissions), 1)
        record = admissions[0]
        self.assertEqual(record["status"], "ACKED")
        self.assertEqual(record["sender"], DEPOSITOR_ADDRESS)
        # One real L1 deposit backs it, and its inbox id is the admitted one.
        deposit = self.stub.deposits[(room_id, record["request"]["depositInboxId"])]
        self.assertEqual(deposit["beneficiary"], DEPOSITOR_ADDRESS)
        self.assertTrue(deposit["consumed"])
        # The deposit and the admission both precede the lease that hands it out.
        order = self.stub.calls
        self.assertIn("l1:eth_sendRawTransaction", order)
        self.assertLess(order.index("l1:eth_sendRawTransaction"), order.index(f"admission-submit:{room_id}"))
        self.assertLess(order.index(f"admission-submit:{room_id}"), order.index(f"lease:{room_id}"))
        # The journal carries the submit evidence and leases exactly that id.
        submits = [
            event for event in journal.events
            if event["kind"] == "submit" and "admissionId" in event
        ]
        self.assertEqual(len(submits), 1)
        self.assertEqual(submits[0]["admissionId"], record["admissionId"])
        self.assertEqual(submits[0]["roomId"], room_id)
        self.assertEqual(submits[0]["txHash"], record["transactionHash"])
        self.assertEqual(submits[0]["depositInboxId"], record["request"]["depositInboxId"])
        self.assertEqual(submits[0]["admissionSigner"], ADMISSION_SIGNER_ADDRESS)
        self.assertIsInstance(submits[0]["cursor"], int)
        leases = [event for event in journal.events if event["kind"] == "lease"]
        self.assertEqual(len(leases), 1)
        self.assertEqual(leases[0]["admissionIds"], [record["admissionId"]])
        # The admission bounds really satisfy what the coordinator enforces.
        deadline = int(record["request"]["deadlineBlock"])
        self.assertGreaterEqual(deadline, deposit["queuedAtBlock"] + MINIMUM_DEADLINE_LEAD_BLOCKS)
        self.assertGreater(int(record["request"]["maximumBatchIndex"]), ROOM_BATCH_INDEX)
        # A second pulse mints its own fresh deposit and admission.
        workload.pulse(1)
        self.assertEqual(len(self.stub.admissions_for(room_id)), 2)
        self.assertEqual(len(self.stub.deposits), 2)

    def test_rejected_admission_fails_closed_without_a_lease_event(self):
        self.stub.reject_admissions = True
        workload, journal = self.workload_harness()
        with self.assertRaisesRegex(driver.DriverError, "admission was refused"):
            workload.pulse(0)
        kinds = {event["kind"] for event in journal.events}
        self.assertNotIn("lease", kinds)
        self.assertNotIn("submit", kinds)
        self.assertEqual(self.stub.admissions_for(driver.Workload.PULSE_ROOM), [])
        # Nothing was leased or acked at the stub either.
        self.assertFalse([call for call in self.stub.calls if call.startswith(("lease:", "ack:"))])
        # And nothing was journaled to disk before the failure either.
        durable = self.root / "unit-journal.ndjson"
        written = [
            json.loads(line)
            for line in (durable.read_text(encoding="utf-8").splitlines() if durable.exists() else [])
            if line.strip()
        ]
        self.assertNotIn("lease", {event["kind"] for event in written})

    def test_a_refused_admission_is_recovered_with_a_fresh_deposit(self):
        # An expired deadline is exactly what a resumed worker replaying its
        # recorded request meets; the reviewed recovery is one fresh deposit
        # and one freshly bounded request, and only then a hard failure.
        self.stub.admission_refusals = 1
        workload, journal = self.workload_harness()
        workload.pulse(0)
        room_id = driver.Workload.PULSE_ROOM
        admissions = self.stub.admissions_for(room_id)
        self.assertEqual(len(admissions), 1)
        # The refused attempt leaves an orphaned pending deposit behind; the
        # committed admission names the second, freshly queued one.
        self.assertEqual(len(self.stub.deposits), 2)
        self.assertEqual(admissions[0]["request"]["depositInboxId"], "2")
        self.assertFalse(self.stub.deposits[(room_id, "1")]["consumed"])
        self.assertTrue(self.stub.deposits[(room_id, "2")]["consumed"])
        submits = [
            event for event in journal.events
            if event["kind"] == "submit" and "admissionId" in event
        ]
        self.assertEqual(len(submits), 1)
        self.assertEqual(submits[0]["depositInboxId"], "2")

    def test_unsigned_admission_receipt_is_not_accepted_as_slashable(self):
        workload, _journal = self.workload_harness()
        stack = workload.stack
        room_state = {"admissionSigner": ADMISSION_SIGNER_ADDRESS}
        request = {
            "transactionHash": "0x" + hex_of("tx"), "depositInboxId": "1",
            "deadlineBlock": "500", "maximumBatchIndex": "9", "admissionFee": "0",
        }
        deposit = {"depositContentHash": "0x" + hex_of("content")}
        receipt = {
            "roomId": "101", "admissionId": "1", "transactionHash": request["transactionHash"],
            "depositInboxId": "1", "depositContentHash": deposit["depositContentHash"],
            "deadlineBlock": "500", "maximumBatchIndex": "9",
            "bondEpoch": str(ROOM_BOND_EPOCH), "admissionFee": "0",
        }
        # Signed by an impostor rather than the room's on-chain admission signer.
        digest = driver.admission_receipt_digest(CHAIN_ID, ROOM_MANAGER, receipt)
        r, s, recovery = driver.sign_digest(digest, driver.private_key_int(DEPOSITOR_KEY))
        receipt["signature"] = "0x" + (
            r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([27 + recovery])
        ).hex()
        with self.assertRaisesRegex(driver.DriverError, "not the room's on-chain admissionSigner"):
            stack.validate_admission_receipt("101", request, deposit, room_state, receipt)

    def test_parser_requires_the_five_release_soak_markers(self):
        parsed = driver.parser().parse_args(list(driver.ARGV_MARKERS))
        self.assertTrue(parsed.submit_real_proof_jobs)
        self.assertTrue(parsed.restart)
        self.assertTrue(parsed.assert_durable)
        self.assertTrue(parsed.bounded_backoff)
        self.assertTrue(parsed.emit_evidence_closure)
        self.assertFalse(parsed.worker)
        for index in range(len(driver.ARGV_MARKERS)):
            partial = [marker for position, marker in enumerate(driver.ARGV_MARKERS) if position != index]
            with self.assertRaises(SystemExit):
                driver.parser().parse_args(partial)

    def test_plan_brackets_docker_host_fault_with_aggregate_cycles(self):
        faults = {item["kind"]: item["atSecond"] for item in self.manifest["scheduledFaults"]}
        docker_at = faults["docker-host-restart-resume"]
        clock = FakeClock()
        http = driver.Http(self.env, clock)
        stack = driver.Stack(http, clock, self.env, self.manifest)
        journal = driver.Journal(self.root / "plan-journal.ndjson")
        state = driver.DriverState(self.root / "plan-state.json", sha256_file(self.manifest_path))
        workload = driver.Workload(stack, journal, state, self.env, self.manifest)
        injector = driver.FaultInjector(http, stack, journal, state, clock, self.env)
        slots = driver.build_plan(self.manifest, workload, injector)
        aggregate_seconds = [slot.second for slot in slots if slot.key.startswith("aggregate-")]
        self.assertEqual(len(aggregate_seconds), 3)
        self.assertTrue(any(second < docker_at for second in aggregate_seconds))
        self.assertTrue(any(second > docker_at for second in aggregate_seconds))
        keys = [slot.key for slot in slots]
        for name in REQUIRED_FAULTS:
            self.assertIn(f"fault-{name}", keys)
        self.assertEqual(len(keys), len(set(keys)))

    def test_golden_virtual_12h_run_passes_the_real_closure_verifier(self):
        code, runs = self.run_soak(self.env)
        self.assertEqual(code, 0)
        # Exactly one docker-host SIGKILL/respawn on the golden path.
        self.assertEqual(runs, 2)
        result = verify_closure(self.manifest_path, self.journal_path)
        self.assertTrue(result["passed"])
        self.assertEqual(set(result["faults"]), REQUIRED_FAULTS)
        self.assertEqual(result["usageUnits"], EXPECTED_USAGE)
        self.assertEqual(result["chargesWei"], str(EXPECTED_WEI))
        self.assertGreaterEqual(result["durationSeconds"], 43200)
        events = self.read_journal()
        kinds = {event["kind"] for event in events}
        for kind in ("room-create", "submit", "lease", "live-prepare", "prove", "verify",
                     "blob-archive", "aggregate-settle", "sponsor", "reorg", "finalize",
                     "withdraw", "reconcile", "tx-broadcast", "charge", "fault", "recovered", "closure"):
            self.assertIn(kind, kinds)
        self.assertEqual(sum(1 for event in events if event["kind"] == "aggregate-settle"), 3)
        # 36 pulses, 3 aggregate cycles x (1 stale batch + 7 applied members),
        # 1 sponsorship, 1 withdrawal claim.
        self.assertEqual(sum(1 for event in events if event["kind"] == "charge"), 36 + 3 * (1 + 7) + 1 + 1)
        # Every cycle generated its own work: one deposit-backed admission per
        # pulse and one per aggregate member, each journaled exactly once.
        expected_admissions = 36 + 3 * driver.AGGREGATE_MEMBERS
        self.assertEqual(len(self.stub.deposits), expected_admissions)
        self.assertEqual(len(self.stub.admissions), expected_admissions)
        self.assertEqual(len(self.stub.admissions_for(driver.Workload.PULSE_ROOM)), 36)
        for room_id in driver.Workload.AGGREGATE_ROOMS:
            self.assertEqual(len(self.stub.admissions_for(room_id)), 3)
        submits = [event for event in events if event["kind"] == "submit" and "admissionId" in event]
        self.assertEqual(len(submits), expected_admissions)
        self.assertEqual(
            len({(event["roomId"], event["admissionId"]) for event in submits}), expected_admissions,
        )
        self.assertEqual(len({event["txHash"] for event in submits}), expected_admissions)
        self.assertEqual(
            len({(event["roomId"], event["depositInboxId"]) for event in submits}), expected_admissions,
        )
        # A repeated worker start against the closed journal is a no-op.
        self.assertEqual(driver.run_worker(environ={**self.env, "ZKDEAL_SOAK_RESUME": "1"}, clock=FakeClock()), 0)

    def test_resume_mid_aggregate_keeps_evidence_exactly_once(self):
        armed = {"value": True}

        def journal_hook(event: dict) -> None:
            if armed["value"] and event.get("kind") == "blob-archive":
                armed["value"] = False
                raise driver.WorkerKilled("simulated crash before a blob-archive write")

        code, runs = self.run_soak(self.env, journal_hook=journal_hook)
        self.assertEqual(code, 0)
        # One mid-aggregate crash plus the scheduled docker-host kill.
        self.assertEqual(runs, 3)
        result = verify_closure(self.manifest_path, self.journal_path)
        self.assertTrue(result["passed"])
        events = self.read_journal()
        for expected, event in enumerate(events, 1):
            self.assertEqual(event["seq"], expected)
        nonces = [event["nonceId"] for event in events if event["kind"] == "tx-broadcast"]
        self.assertEqual(len(nonces), len(set(nonces)))
        charges = [event["chargeId"] for event in events if event["kind"] == "charge"]
        self.assertEqual(len(charges), len(set(charges)))
        event_ids = [event["eventId"] for event in events if "eventId" in event]
        self.assertEqual(len(event_ids), len(set(event_ids)))
        seals: dict[str, str] = {}
        for event in events:
            if "outputId" in event:
                previous = seals.setdefault(event["outputId"], event["sealedOutputSha256"])
                self.assertEqual(previous, event["sealedOutputSha256"])
        recovered = [event for event in events if event["kind"] == "recovered"
                     and event["fault"] == "docker-host-restart-resume"]
        self.assertEqual(len(recovered), 1)
        self.assertGreaterEqual(recovered[0]["workerBoots"], 3)

    def test_ledger_divergence_fails_closed_before_any_closure(self):
        self.manifest["expected"]["usageUnits"] = EXPECTED_USAGE + 1
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(driver.DriverError, "do not match the manifest expected"):
            self.run_soak(self.env)
        events = self.read_journal()
        self.assertTrue(events)
        self.assertNotEqual(events[-1]["kind"], "closure")
        with self.assertRaises(DeploymentError):
            verify_closure(self.manifest_path, self.journal_path)

    def test_tampered_sealed_output_fails_closed_before_any_closure(self):
        self.stub.tamper_outputs = True
        with self.assertRaisesRegex(driver.DriverError, "do not match durable resultDigest"):
            self.run_soak(self.env)
        events = self.read_journal()
        self.assertNotIn("closure", {event["kind"] for event in events})

    def test_journal_tampered_after_the_fact_is_rejected_by_the_verifier(self):
        code, _runs = self.run_soak(self.env)
        self.assertEqual(code, 0)
        events = self.read_journal()
        for event in events:
            if event["kind"] == "finalize":
                event["sealedOutputSha256"] = "0" * 64
                break
        self.journal_path.write_text(
            "".join(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(DeploymentError, "sealed output changed"):
            verify_closure(self.manifest_path, self.journal_path)


if __name__ == "__main__":
    unittest.main()
