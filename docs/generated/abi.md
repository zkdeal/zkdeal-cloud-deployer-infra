# Contract ABI reference

Generated from Foundry artifacts owned by `web3-protocol`. This reference preserves canonical selectors, named ABI fields, tuple/struct layouts and available owner NatSpec; deployment consumes the original artifacts and never copies contract bytecode here.

## RoomManager

### Functions

| Signature | Selector | Mutability | Named inputs | Named outputs | Owner NatSpec |
|---|---|---|---|---|---|
| `DEFAULT_CHALLENGE_WINDOW_BLOCKS()` | `0xd14b5fb0` | `view` | `—` | `uint32 <unnamed>` | userdoc.notice: Recommended default challenge window (~1h on 12s L1 blocks). |
| `DEPLOYMENT_DOMAIN_TAG()` | `0xe1017899` | `view` | `—` | `string <unnamed>` | userdoc.notice: Domain-separation tag of the deployment-domain preimage. |
| `FIELD_PRIME()` | `0xa9931cf3` | `view` | `—` | `uint256 <unnamed>` | userdoc.notice: BN254 scalar field prime — txCommitments are field elements. |
| `MAX_BLOCKS()` | `0x26833dcc` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_MEMBERS()` | `0xea0e35b1` | `view` | `—` | `uint8 <unnamed>` | — |
| `MIN_CHALLENGE_WINDOW_BLOCKS()` | `0xb1a844da` | `view` | `—` | `uint32 <unnamed>` | userdoc.notice: Smallest challenge window a production deployment may use (~15 min on 12s L1 blocks). |
| `PROTOCOL_VERSION()` | `0xaa3aa460` | `view` | `—` | `uint256 <unnamed>` | userdoc.notice: Protocol version bound into the deployment domain (envelope.ts PROTOCOL_VERSION). Bumped on any breaking signed-encoding change. |
| `SIGNAL_COUNT()` | `0xf55fe23e` | `view` | `—` | `uint256 <unnamed>` | — |
| `abort(uint64)` | `0x9cf2b38b` | `nonpayable` | `uint64 roomId` | `—` | userdoc.notice: Refund path: available after the deadline ONLY if no valid checkpoint was ever submitted (before or after opening). Marks each joined member's own deposit claimable via claim(). |
| `approveGenesis(uint64,uint128,uint128)` | `0xaa7d548b` | `nonpayable` | `uint64 roomId; uint128 genesisRootHi; uint128 genesisRootLo` | `—` | userdoc.notice: Bind this member to a specific L2 genesis root (two 128-bit limbs). Every joined member must approve the SAME root before the creator may call openRoom — no member can be bound to deployed code + balances they did not explicitly approve. Re-calling with a different root overwrites the prior approval (useful if the coordinator rebuilt genesis); openRoom requires all current approvals to match its arguments exactly. |
| `claim(uint64)` | `0xaab8ab0c` | `nonpayable` | `uint64 roomId` | `—` | userdoc.notice: Pull-based payout (CEI, non-reentrant): zeroes the caller's claimable amount before transferring, so a reverting recipient can only block itself, never other members. |
| `claimable(uint64,address)` | `0x88d8b2a7` | `view` | `uint64 <unnamed>; address <unnamed>` | `uint96 <unnamed>` | userdoc.notice: Pull-based payouts: claimable wei per room per address. |
| `createRoom(uint8,uint8,uint96,uint64,uint32)` | `0x80ae42db` | `nonpayable` | `uint8 scenario; uint8 memberTarget; uint96 depositWei; uint64 deadline; uint32 challengeWindowBlocks` | `uint64 roomId` | userdoc.notice: Create a deal channel. `scenario` selects the L2 genesis contract set (contracts/scenarios.json); `memberTarget` must not exceed the circuit profile's MAX_MEMBERS. |
| `deploymentDomain()` | `0x2dcf05ab` | `view` | `—` | `uint256 <unnamed>` | userdoc.notice: Deployment domain field element — the anti-replay binding (review/circuits.md H-01). Computed ONCE at construction from this chain, THIS contract address and PROTOCOL_VERSION: keccak256("zkdeal/deployment/v4" \|\| uint256(chainid) \|\| address(this) \|\| uint256(PROTOCOL_VERSION)) reduced mod FIELD_PRIME. Identical to the zkdeal/protocol package `deploymentDomainField`. It is public signal 0 of every settlement proof, it seeds the proven header chain and it is inside the message every member signs — so a certificate produced for another chain/manager/version can never verify here, and members' room keys do not even exist on another deployment. |
| `finalize(uint64)` | `0x359bc19e` | `nonpayable` | `uint64 roomId` | `—` | userdoc.notice: After the challenge window of the highest-seq checkpoint has elapsed, anyone may finalize: freezes the accepted exits as claimable amounts and sets state Finalized. No transfers — members pull via claim(). |
| `getMembers(uint64)` | `0x4813dd50` | `view` | `uint64 roomId` | `(address,uint256,uint256,bool,uint128,uint128)[] <unnamed>` | userdoc.notice: Registered members in join order (== signal slot order). |
| `getPendingExits(uint64)` | `0x6e31910d` | `view` | `uint64 roomId` | `uint96[7] <unnamed>` | userdoc.notice: Exit amounts of the latest accepted (pending or finalized) checkpoint, member join order. |
| `getRoom(uint64)` | `0x96297d01` | `view` | `uint64 roomId` | `(address,uint8,uint8,uint8,uint96,uint64,uint32,uint96,uint128,uint128,uint64,uint32,uint64,uint128,uint128) <unnamed>` | userdoc.notice: Full room record (packed struct copy). |
| `joinRoom(uint64,uint256,uint256)` | `0xf71fe486` | `payable` | `uint64 roomId; uint256 pubX; uint256 pubY` | `—` | userdoc.notice: Join with the exact deposit and a room-scoped EdDSA (Baby Jubjub) public key. One membership per address; join order is the member/exit slot order in the public signals. |
| `nextRoomId()` | `0x07a52cab` | `view` | `—` | `uint64 <unnamed>` | — |
| `openRoom(uint64,uint128,uint128)` | `0xc7d15c7a` | `nonpayable` | `uint64 roomId; uint128 genesisRootHi; uint128 genesisRootLo` | `—` | userdoc.notice: Open the room with a genesis root. Reverts unless the room is full AND every member has approveGenesis'd this exact root. Callable by the creator only. |
| `submitCheckpoint(uint64,uint256[2],uint256[2][2],uint256[2],uint256[69],bytes[][])` | `0xaf7709f0` | `nonpayable` | `uint64 roomId; uint256[2] a; uint256[2][2] b; uint256[2] c; uint256[69] signals; bytes[][] txsPerBlock` | `—` | userdoc.notice: Submit a settlement certificate: Groth16 proof + the 69 public signals (layout v3) + the raw L2 transactions of every block for data availability. Permissionless: the proof itself requires N-of-N member signatures, so anyone holding it may settle (unilateral settlement, DESIGN-V2 §c2). A valid submission with seq strictly greater than the pending one supersedes it and restarts the challenge window (same seq: first valid wins — the second reverts). |
| `verifier()` | `0x2b7ac3f3` | `view` | `—` | `address <unnamed>` | — |

### Events

| Signature | Named/indexed fields | Anonymous | Owner NatSpec |
|---|---|---|---|
| `CheckpointSubmitted(uint64,uint32,uint128,uint128,uint256)` | `uint64 roomId indexed; uint32 seq; uint128 finalRootHi; uint128 finalRootLo; uint256 blocksHash` | `false` | — |
| `Claimed(uint64,address,uint96)` | `uint64 roomId indexed; address member indexed; uint96 amount` | `false` | — |
| `GenesisApproved(uint64,address,uint128,uint128)` | `uint64 roomId indexed; address member indexed; uint128 genesisRootHi; uint128 genesisRootLo` | `false` | — |
| `MemberJoined(uint64,address,uint8,uint256,uint256)` | `uint64 roomId indexed; address member indexed; uint8 index; uint256 pubX; uint256 pubY` | `false` | — |
| `RoomAborted(uint64)` | `uint64 roomId indexed` | `false` | — |
| `RoomCreated(uint64,address,uint8,uint8,uint96,uint64,uint32)` | `uint64 roomId indexed; address creator indexed; uint8 scenario; uint8 memberTarget; uint96 depositWei; uint64 deadline; uint32 challengeWindowBlocks` | `false` | — |
| `RoomFinalized(uint64,uint32)` | `uint64 roomId indexed; uint32 seq` | `false` | — |
| `RoomOpened(uint64,uint128,uint128,uint64)` | `uint64 roomId indexed; uint128 genesisRootHi; uint128 genesisRootLo; uint64 openedAtBlock` | `false` | — |

### Custom errors

| Signature | Named fields | Owner NatSpec |
|---|---|---|
| `AlreadyMember()` | `—` | — |
| `BadParams()` | `—` | — |
| `BadSeq()` | `—` | — |
| `ChallengeWindowOpen()` | `—` | — |
| `CheckpointExists()` | `—` | — |
| `DaMismatch(uint256)` | `uint256 blockIndex` | — |
| `DeadlineNotReached()` | `—` | — |
| `DeadlinePassed()` | `—` | — |
| `ExitSumMismatch()` | `—` | — |
| `GenesisNotApproved()` | `—` | — |
| `InvalidProof()` | `—` | — |
| `NoCheckpoint()` | `—` | — |
| `NotCreator()` | `—` | — |
| `NotMember()` | `—` | — |
| `NothingToClaim()` | `—` | — |
| `Reentrant()` | `—` | — |
| `RoomFull()` | `—` | — |
| `RoomNotFull()` | `—` | — |
| `SignalMismatch(uint256)` | `uint256 index` | — |
| `TransferFailed()` | `—` | — |
| `WrongDeposit()` | `—` | — |
| `WrongState()` | `—` | — |

### Struct and tuple layouts

#### `RoomManager.Member`

| Field | ABI type | Internal type |
|---|---|---|
| `addr` | `address` | `address` |
| `pubX` | `uint256` | `uint256` |
| `pubY` | `uint256` | `uint256` |
| `genesisApproved` | `bool` | `bool` |
| `approvedGenesisHi` | `uint128` | `uint128` |
| `approvedGenesisLo` | `uint128` | `uint128` |

#### `RoomManager.Room`

| Field | ABI type | Internal type |
|---|---|---|
| `creator` | `address` | `address` |
| `scenario` | `uint8` | `uint8` |
| `memberTarget` | `uint8` | `uint8` |
| `state` | `uint8` | `enum RoomManager.RoomState` |
| `depositWei` | `uint96` | `uint96` |
| `deadline` | `uint64` | `uint64` |
| `challengeWindowBlocks` | `uint32` | `uint32` |
| `totalEscrow` | `uint96` | `uint96` |
| `genesisRootHi` | `uint128` | `uint128` |
| `genesisRootLo` | `uint128` | `uint128` |
| `openedAtBlock` | `uint64` | `uint64` |
| `seq` | `uint32` | `uint32` |
| `submittedAtBlock` | `uint64` | `uint64` |
| `finalRootHi` | `uint128` | `uint128` |
| `finalRootLo` | `uint128` | `uint128` |


## RoomManagerBatchFacet

### Functions

| Signature | Selector | Mutability | Named inputs | Named outputs | Owner NatSpec |
|---|---|---|---|---|---|
| `CANONICAL_BYTES_PER_BLOB()` | `0x03f50906` | `view` | `—` | `uint256 <unnamed>` | — |
| `GRACE_BLOCKS()` | `0x418fc67b` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_ACTIVE_APPROVERS()` | `0x3964be7d` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_AGGREGATE_ROOMS()` | `0xe4a9fe8a` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_APPROVER_PROOF_DEPTH()` | `0x34233b33` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_BLOBS_PER_BATCH()` | `0xd6160d39` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_COLD_TEMPLATE_DATA_BYTES()` | `0x3c67ed66` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_FORCED_TRANSACTION_BYTES()` | `0x65e471f2` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_IMPORT_CONFIRMATIONS()` | `0xa72aea7a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_INBOX_ITEMS_PER_BATCH()` | `0xccc6d3eb` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PARTICIPANT_CAPACITY()` | `0x8b3af9b0` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PROTOCOL_FEE_BPS()` | `0x6d947e4b` | `view` | `—` | `uint16 <unnamed>` | — |
| `MAX_WITHDRAWALS_PER_EPOCH()` | `0x4ee7b9cd` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_WITHDRAWAL_PROOF_DEPTH()` | `0x1924b7ab` | `view` | `—` | `uint256 <unnamed>` | — |
| `MIN_ADMISSION_WINDOW()` | `0xffac207a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_DEPOSIT_CONFIRMATIONS()` | `0x04b45763` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_IMPORT_CONFIRMATIONS()` | `0x35774972` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_SERVICE_BOND_MULTIPLE()` | `0xfe15dcac` | `view` | `—` | `uint256 <unnamed>` | — |
| `RECOVERY_CLOSE_BOND_MULTIPLE()` | `0x7adfc8f8` | `view` | `—` | `uint64 <unnamed>` | — |
| `RECOVERY_CLOSE_CHALLENGE_MULTIPLE()` | `0xe7ab9737` | `view` | `—` | `uint64 <unnamed>` | — |
| `REPAIR_FEE_DIVISOR()` | `0x5445cbde` | `view` | `—` | `uint256 <unnamed>` | — |
| `REPAIR_WINDOW_BLOCKS()` | `0x1f903fe2` | `view` | `—` | `uint64 <unnamed>` | — |
| `applyVerifiedBatch(uint64,((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]),bool,bool)` | `0xc9a5e09d` | `nonpayable` | `uint64 roomId; ((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]) submission; bool requireApprovals; bool canonicalDataInCalldata` | `—` | — |
| `claimWithdrawal(uint64,uint64,(uint64,uint64,address,address,uint256),bytes32[])` | `0xb051a9f8` | `nonpayable` | `uint64 roomId; uint64 outboxEpoch; (uint64,uint64,address,address,uint256) withdrawal; bytes32[] proof` | `—` | — |
| `purchaseDeadlineGrace(uint64,(uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool))` | `0x71f8f51c` | `payable` | `uint64 roomId; (uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool) journal` | `—` | userdoc.notice: One-time, fee-paid acceptance widening for one exact pending transition. Permissionless: keyed by the journal hash, it can only widen the window for that proven transition, so a third party cannot harm and a garbage purchase extends nothing real. |
| `selectors()` | `0x6e25b978` | `pure` | `—` | `bytes4[] value` | — |
| `submitBatch(uint64,((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]))` | `0x62dad01b` | `nonpayable` | `uint64 roomId; ((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]) submission` | `—` | — |
| `submitRecoveryBatch(uint64,((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]))` | `0x85c9ac24` | `nonpayable` | `uint64 roomId; ((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]) submission` | `—` | — |

### Events

| Signature | Named/indexed fields | Anonymous | Owner NatSpec |
|---|---|---|---|
| `AdmissionOmissionChallenged(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `AdmissionReceiptDischarged(uint64,uint64,bytes32,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; uint256 cost` | `false` | — |
| `AdmissionRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `AggregateMemberOutcome(bytes32,uint8,uint64,uint64,bool,bytes4)` | `bytes32 aggregateHash indexed; uint8 memberIndex indexed; uint64 roomId indexed; uint64 batchIndex; bool applied; bytes4 failureSelector` | `false` | — |
| `ApproverChangeQueued(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 requestId indexed; bytes32 changeHash indexed` | `false` | — |
| `BatchAccepted(uint64,uint64,bytes32,bytes32,uint64,bool)` | `uint64 roomId indexed; uint64 batchIndex indexed; bytes32 postStateRoot indexed; bytes32 postApproverRoot; uint64 outboxEpoch; bool closed` | `false` | — |
| `ChallengePayoutClaimed(uint64,address,uint256)` | `uint64 roomId indexed; address payee indexed; uint256 amount` | `false` | — |
| `ColdTemplateDataPublished(uint64,bytes32,bytes)` | `uint64 roomId indexed; bytes32 dataHash indexed; bytes canonicalColdTemplateData` | `false` | — |
| `DataAvailabilityAccepted(uint64,uint64,uint8,bool,bool,bytes32)` | `uint64 roomId indexed; uint64 batchIndex indexed; uint8 configuredPolicy; bool usedBlob; bool usedAuthorizedFallback; bytes32 statementHash` | `false` | — |
| `DataAvailabilityConfigured(uint64,uint8,address,bytes32)` | `uint64 roomId indexed; uint8 policy; address fallbackAuthority indexed; bytes32 equivalenceProgramId indexed` | `false` | — |
| `DeadlineGracePurchased(uint64,bytes32,uint64,uint256)` | `uint64 roomId indexed; bytes32 journalHash indexed; uint64 graceDeadlineBlock; uint256 fee` | `false` | — |
| `DepositQueued(uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 inboxId indexed; address beneficiary indexed; address asset; uint256 amount` | `false` | — |
| `DepositRefunded(uint64,uint64,address)` | `uint64 roomId indexed; uint64 inboxId indexed; address depositor indexed` | `false` | — |
| `ForcedOutcomeRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `ForcedTransactionQueued(uint64,uint64,bytes32,uint64,bytes)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint64 deadlineBlock; bytes rawSignedTransaction` | `false` | — |
| `L1StateInputPublished(uint64,uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 importId indexed; uint64 sourceBlock indexed; bytes32 importRoot` | `false` | — |
| `LivenessAttested(uint64,uint64)` | `uint64 roomId indexed; uint64 attestedAt` | `false` | — |
| `OmissionChallengeOpened(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `OmissionChallengeRepaired(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; address challenger; uint256 repairFee` | `false` | — |
| `OmissionChallengeSettled(uint64,bytes32,address,uint256)` | `uint64 roomId indexed; bytes32 receiptHash indexed; address challenger indexed; uint256 penalty` | `false` | — |
| `ProtocolFeeAccrued(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeConfigured(uint16,address)` | `uint16 bps; address treasury` | `false` | — |
| `ProtocolFeeMadeClaimable(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeReversed(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeesClaimed(uint64,address,address,uint256)` | `uint64 roomId indexed; address asset indexed; address treasury indexed; uint256 amount` | `false` | — |
| `RecoveryBatchAccepted(uint64,uint64)` | `uint64 roomId indexed; uint64 batchIndex indexed` | `false` | — |
| `RoomClosedByRecovery(uint64,address)` | `uint64 roomId indexed; address closer indexed` | `false` | — |
| `RoomCreated(uint64,bytes32,bytes32,uint64,uint8,uint64)` | `uint64 roomId indexed; bytes32 coldTemplateId indexed; bytes32 initialApproverRoot indexed; uint64 activeApproverCount; uint8 authorizationMode; uint64 participantCapacity` | `false` | — |
| `RoomOwnershipAssigned(uint64,address,bytes32)` | `uint64 roomId indexed; address creator indexed; bytes32 managedAllocationId indexed` | `false` | — |
| `ServiceBondFunded(uint64,uint64,uint256)` | `uint64 roomId indexed; uint64 bondEpoch indexed; uint256 amount` | `false` | — |
| `ServiceBondWithdrawn(uint64,uint256)` | `uint64 roomId indexed; uint256 amount` | `false` | — |
| `WithdrawalClaimed(uint64,uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; uint64 index indexed; address recipient; address asset; uint256 amount` | `false` | — |
| `WithdrawalRootPublished(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; bytes32 withdrawalRoot indexed` | `false` | — |

### Custom errors

| Signature | Named fields | Owner NatSpec |
|---|---|---|
| `BadAccounting()` | `—` | — |
| `BadAdmission()` | `—` | — |
| `BadAggregate()` | `—` | — |
| `BadApproval()` | `—` | — |
| `BadApprover()` | `—` | — |
| `BadForcedTransaction()` | `—` | — |
| `BadImport()` | `—` | — |
| `BadInput()` | `—` | — |
| `BadProof()` | `—` | — |
| `BadTemplate()` | `—` | — |
| `BadWithdrawal()` | `—` | — |
| `BondUnavailable()` | `—` | — |
| `ChallengeNotSettleable()` | `—` | — |
| `CloseNotReady()` | `—` | — |
| `DataAvailabilityUnavailable()` | `—` | — |
| `DeadlinePassed()` | `—` | — |
| `DepositTooRecent()` | `—` | — |
| `DischargeUnavailable()` | `—` | — |
| `GraceUnavailable()` | `—` | — |
| `NothingToClaim()` | `—` | — |
| `RecoveryNotReady()` | `—` | — |
| `Reentrant()` | `—` | — |
| `Unauthorized()` | `—` | — |
| `UnsupportedAsset()` | `—` | — |
| `WrongState()` | `—` | — |

### Struct and tuple layouts

#### `RoomTypes.AdmissionOutcome`

| Field | ABI type | Internal type |
|---|---|---|
| `admissionId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `status` | `uint8` | `enum RoomTypes.AdmissionStatus` |
| `l2BlockNumber` | `uint64` | `uint64` |
| `transactionIndex` | `uint32` | `uint32` |
| `reasonHash` | `bytes32` | `bytes32` |

#### `RoomTypes.AdmissionReceipt`

| Field | ABI type | Internal type |
|---|---|---|
| `admissionId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `depositInboxId` | `uint64` | `uint64` |
| `depositContentHash` | `bytes32` | `bytes32` |
| `deadlineBlock` | `uint64` | `uint64` |
| `maximumBatchIndex` | `uint64` | `uint64` |
| `bondEpoch` | `uint64` | `uint64` |
| `admissionFee` | `uint256` | `uint256` |
| `signature` | `bytes` | `bytes` |

#### `RoomTypes.AdmissionRecord`

| Field | ABI type | Internal type |
|---|---|---|
| `receipt` | `(uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes)` | `struct RoomTypes.AdmissionReceipt` |
| `outcome` | `(uint64,bytes32,uint8,uint64,uint32,bytes32)` | `struct RoomTypes.AdmissionOutcome` |

#### `RoomTypes.ApproverApproval`

| Field | ABI type | Internal type |
|---|---|---|
| `index` | `uint64` | `uint64` |
| `joinedEpoch` | `uint64` | `uint64` |
| `nonce` | `uint64` | `uint64` |
| `deadline` | `uint64` | `uint64` |
| `member` | `address` | `address` |
| `proof` | `bytes32[]` | `bytes32[]` |
| `signature` | `bytes` | `bytes` |

#### `RoomTypes.ApproverChange`

| Field | ABI type | Internal type |
|---|---|---|
| `action` | `uint8` | `enum RoomTypes.ApproverAction` |
| `index` | `uint64` | `uint64` |
| `joinedEpoch` | `uint64` | `uint64` |
| `deadline` | `uint64` | `uint64` |
| `member` | `address` | `address` |
| `withdrawalCommitment` | `bytes32` | `bytes32` |
| `acceptanceSignature` | `bytes` | `bytes` |

#### `RoomTypes.AssetLiability`

| Field | ABI type | Internal type |
|---|---|---|
| `asset` | `address` | `address` |
| `pending` | `uint256` | `uint256` |
| `controlled` | `uint256` | `uint256` |
| `claimable` | `uint256` | `uint256` |
| `paid` | `uint256` | `uint256` |

#### `RoomTypes.BatchJournal`

| Field | ABI type | Internal type |
|---|---|---|
| `protocolVersion` | `uint256` | `uint256` |
| `deploymentDomain` | `bytes32` | `bytes32` |
| `roomId` | `uint64` | `uint64` |
| `authorizationMode` | `uint8` | `enum RoomTypes.AuthorizationMode` |
| `coldTemplateId` | `bytes32` | `bytes32` |
| `proofProgramId` | `bytes32` | `bytes32` |
| `proofSystemVersion` | `bytes32` | `bytes32` |
| `policyHash` | `bytes32` | `bytes32` |
| `batchIndex` | `uint64` | `uint64` |
| `startL2Block` | `uint64` | `uint64` |
| `endL2Block` | `uint64` | `uint64` |
| `preStateRoot` | `bytes32` | `bytes32` |
| `postStateRoot` | `bytes32` | `bytes32` |
| `batchDataHash` | `bytes32` | `bytes32` |
| `canonicalDataHash` | `bytes32` | `bytes32` |
| `preParticipantRoot` | `bytes32` | `bytes32` |
| `postParticipantRoot` | `bytes32` | `bytes32` |
| `preParticipantEpoch` | `uint64` | `uint64` |
| `postParticipantEpoch` | `uint64` | `uint64` |
| `preParticipantCount` | `uint64` | `uint64` |
| `postParticipantCount` | `uint64` | `uint64` |
| `participantCapacity` | `uint64` | `uint64` |
| `preApproverRoot` | `bytes32` | `bytes32` |
| `postApproverRoot` | `bytes32` | `bytes32` |
| `preApproverEpoch` | `uint64` | `uint64` |
| `postApproverEpoch` | `uint64` | `uint64` |
| `preActiveCount` | `uint64` | `uint64` |
| `postActiveCount` | `uint64` | `uint64` |
| `approverChangeCursorBefore` | `uint64` | `uint64` |
| `approverChangeCursorAfter` | `uint64` | `uint64` |
| `inboxCursorBefore` | `uint64` | `uint64` |
| `inboxCursorAfter` | `uint64` | `uint64` |
| `inboxRecordsHash` | `bytes32` | `bytes32` |
| `admissionCursorBefore` | `uint64` | `uint64` |
| `admissionCursorAfter` | `uint64` | `uint64` |
| `admissionRecordsHash` | `bytes32` | `bytes32` |
| `forcedCursorBefore` | `uint64` | `uint64` |
| `forcedCursorAfter` | `uint64` | `uint64` |
| `forcedOutcomesHash` | `bytes32` | `bytes32` |
| `importCursorBefore` | `uint64` | `uint64` |
| `importCursorAfter` | `uint64` | `uint64` |
| `importedL1Block` | `uint64` | `uint64` |
| `importedL1HeaderHash` | `bytes32` | `bytes32` |
| `importedL1StateRoot` | `bytes32` | `bytes32` |
| `importRoot` | `bytes32` | `bytes32` |
| `outboxEpoch` | `uint64` | `uint64` |
| `withdrawalRoot` | `bytes32` | `bytes32` |
| `preLiabilitiesHash` | `bytes32` | `bytes32` |
| `postLiabilitiesHash` | `bytes32` | `bytes32` |
| `approverChangesHash` | `bytes32` | `bytes32` |
| `l1InclusionDeadline` | `uint64` | `uint64` |
| `close` | `bool` | `bool` |

#### `RoomTypes.BatchSubmission`

| Field | ABI type | Internal type |
|---|---|---|
| `journal` | `(uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool)` | `struct RoomTypes.BatchJournal` |
| `seal` | `bytes` | `bytes` |
| `canonicalBatchData` | `bytes` | `bytes` |
| `approvals` | `(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[]` | `struct RoomTypes.ApproverApproval[]` |
| `approverChanges` | `(uint8,uint64,uint64,uint64,address,bytes32,bytes)[]` | `struct RoomTypes.ApproverChange[]` |
| `admissions` | `((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[]` | `struct RoomTypes.AdmissionRecord[]` |
| `forcedOutcomes` | `(uint64,bytes32,uint8,uint64,uint32,bytes32)[]` | `struct RoomTypes.ForcedOutcome[]` |
| `liabilities` | `(address,uint256,uint256,uint256,uint256)[]` | `struct RoomTypes.AssetLiability[]` |

#### `RoomTypes.ForcedOutcome`

| Field | ABI type | Internal type |
|---|---|---|
| `forcedId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `status` | `uint8` | `enum RoomTypes.AdmissionStatus` |
| `l2BlockNumber` | `uint64` | `uint64` |
| `transactionIndex` | `uint32` | `uint32` |
| `reasonHash` | `bytes32` | `bytes32` |

#### `RoomTypes.Withdrawal`

| Field | ABI type | Internal type |
|---|---|---|
| `index` | `uint64` | `uint64` |
| `approverEpoch` | `uint64` | `uint64` |
| `recipient` | `address` | `address` |
| `asset` | `address` | `address` |
| `amount` | `uint256` | `uint256` |


## RoomManagerChallengeFacet

### Functions

| Signature | Selector | Mutability | Named inputs | Named outputs | Owner NatSpec |
|---|---|---|---|---|---|
| `CANONICAL_BYTES_PER_BLOB()` | `0x03f50906` | `view` | `—` | `uint256 <unnamed>` | — |
| `GRACE_BLOCKS()` | `0x418fc67b` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_ACTIVE_APPROVERS()` | `0x3964be7d` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_AGGREGATE_ROOMS()` | `0xe4a9fe8a` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_APPROVER_PROOF_DEPTH()` | `0x34233b33` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_BLOBS_PER_BATCH()` | `0xd6160d39` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_COLD_TEMPLATE_DATA_BYTES()` | `0x3c67ed66` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_FORCED_TRANSACTION_BYTES()` | `0x65e471f2` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_IMPORT_CONFIRMATIONS()` | `0xa72aea7a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_INBOX_ITEMS_PER_BATCH()` | `0xccc6d3eb` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PARTICIPANT_CAPACITY()` | `0x8b3af9b0` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PROTOCOL_FEE_BPS()` | `0x6d947e4b` | `view` | `—` | `uint16 <unnamed>` | — |
| `MAX_WITHDRAWALS_PER_EPOCH()` | `0x4ee7b9cd` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_WITHDRAWAL_PROOF_DEPTH()` | `0x1924b7ab` | `view` | `—` | `uint256 <unnamed>` | — |
| `MIN_ADMISSION_WINDOW()` | `0xffac207a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_DEPOSIT_CONFIRMATIONS()` | `0x04b45763` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_IMPORT_CONFIRMATIONS()` | `0x35774972` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_SERVICE_BOND_MULTIPLE()` | `0xfe15dcac` | `view` | `—` | `uint256 <unnamed>` | — |
| `RECOVERY_CLOSE_BOND_MULTIPLE()` | `0x7adfc8f8` | `view` | `—` | `uint64 <unnamed>` | — |
| `RECOVERY_CLOSE_CHALLENGE_MULTIPLE()` | `0xe7ab9737` | `view` | `—` | `uint64 <unnamed>` | — |
| `REPAIR_FEE_DIVISOR()` | `0x5445cbde` | `view` | `—` | `uint256 <unnamed>` | — |
| `REPAIR_WINDOW_BLOCKS()` | `0x1f903fe2` | `view` | `—` | `uint64 <unnamed>` | — |
| `attestLiveness(uint64)` | `0xa88ee603` | `nonpayable` | `uint64 roomId` | `—` | userdoc.notice: Operator keepalive against the permissionless close tier. It deliberately never touches lastVerifiedAt: depositor refunds and the operator fold stay proof-anchored. |
| `claimChallengePayout(uint64)` | `0x62ef8029` | `nonpayable` | `uint64 roomId` | `—` | userdoc.notice: Pull the credits accrued by repairs, settlements and discharges. Batch submission never sends ETH to arbitrary addresses -- a push there would let a reverting receiver force the full slash -- so every credit exits here instead. |
| `closeInertRoom(uint64)` | `0x4351fe78` | `nonpayable` | `uint64 roomId` | `—` | userdoc.notice: Close a VALIDITY_ONLY room at its last proven state. Nothing else moves: the last proven root, cursors and liabilities stand, and every consequence -- refunds, withdrawals against stored roots, the bond exit clock -- is existing machinery. |
| `dischargeAdmissionReceipt(uint64,(uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes))` | `0x17b81e05` | `nonpayable` | `uint64 roomId; (uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes) receipt` | `—` | userdoc.notice: The operator buys back its own promise, time-priced: before the receipt deadline a quarter penalty, at or after it the same full penalty a sustained challenge costs, so the discharge-vs-challenge race is economically void. |
| `selectors()` | `0x6e25b978` | `pure` | `—` | `bytes4[] value` | — |
| `settleOmissionChallenge(uint64,bytes32)` | `0x9f45dcdc` | `nonpayable` | `uint64 roomId; bytes32 receiptHash` | `—` | userdoc.notice: Complete an unrepaired omission challenge at full penalty. Permissionless, so an offline challenger still gets credited. |

### Events

| Signature | Named/indexed fields | Anonymous | Owner NatSpec |
|---|---|---|---|
| `AdmissionOmissionChallenged(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `AdmissionReceiptDischarged(uint64,uint64,bytes32,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; uint256 cost` | `false` | — |
| `AdmissionRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `AggregateMemberOutcome(bytes32,uint8,uint64,uint64,bool,bytes4)` | `bytes32 aggregateHash indexed; uint8 memberIndex indexed; uint64 roomId indexed; uint64 batchIndex; bool applied; bytes4 failureSelector` | `false` | — |
| `ApproverChangeQueued(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 requestId indexed; bytes32 changeHash indexed` | `false` | — |
| `BatchAccepted(uint64,uint64,bytes32,bytes32,uint64,bool)` | `uint64 roomId indexed; uint64 batchIndex indexed; bytes32 postStateRoot indexed; bytes32 postApproverRoot; uint64 outboxEpoch; bool closed` | `false` | — |
| `ChallengePayoutClaimed(uint64,address,uint256)` | `uint64 roomId indexed; address payee indexed; uint256 amount` | `false` | — |
| `ColdTemplateDataPublished(uint64,bytes32,bytes)` | `uint64 roomId indexed; bytes32 dataHash indexed; bytes canonicalColdTemplateData` | `false` | — |
| `DataAvailabilityAccepted(uint64,uint64,uint8,bool,bool,bytes32)` | `uint64 roomId indexed; uint64 batchIndex indexed; uint8 configuredPolicy; bool usedBlob; bool usedAuthorizedFallback; bytes32 statementHash` | `false` | — |
| `DataAvailabilityConfigured(uint64,uint8,address,bytes32)` | `uint64 roomId indexed; uint8 policy; address fallbackAuthority indexed; bytes32 equivalenceProgramId indexed` | `false` | — |
| `DeadlineGracePurchased(uint64,bytes32,uint64,uint256)` | `uint64 roomId indexed; bytes32 journalHash indexed; uint64 graceDeadlineBlock; uint256 fee` | `false` | — |
| `DepositQueued(uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 inboxId indexed; address beneficiary indexed; address asset; uint256 amount` | `false` | — |
| `DepositRefunded(uint64,uint64,address)` | `uint64 roomId indexed; uint64 inboxId indexed; address depositor indexed` | `false` | — |
| `ForcedOutcomeRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `ForcedTransactionQueued(uint64,uint64,bytes32,uint64,bytes)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint64 deadlineBlock; bytes rawSignedTransaction` | `false` | — |
| `L1StateInputPublished(uint64,uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 importId indexed; uint64 sourceBlock indexed; bytes32 importRoot` | `false` | — |
| `LivenessAttested(uint64,uint64)` | `uint64 roomId indexed; uint64 attestedAt` | `false` | — |
| `OmissionChallengeOpened(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `OmissionChallengeRepaired(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; address challenger; uint256 repairFee` | `false` | — |
| `OmissionChallengeSettled(uint64,bytes32,address,uint256)` | `uint64 roomId indexed; bytes32 receiptHash indexed; address challenger indexed; uint256 penalty` | `false` | — |
| `ProtocolFeeAccrued(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeConfigured(uint16,address)` | `uint16 bps; address treasury` | `false` | — |
| `ProtocolFeeMadeClaimable(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeReversed(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeesClaimed(uint64,address,address,uint256)` | `uint64 roomId indexed; address asset indexed; address treasury indexed; uint256 amount` | `false` | — |
| `RecoveryBatchAccepted(uint64,uint64)` | `uint64 roomId indexed; uint64 batchIndex indexed` | `false` | — |
| `RoomClosedByRecovery(uint64,address)` | `uint64 roomId indexed; address closer indexed` | `false` | — |
| `RoomCreated(uint64,bytes32,bytes32,uint64,uint8,uint64)` | `uint64 roomId indexed; bytes32 coldTemplateId indexed; bytes32 initialApproverRoot indexed; uint64 activeApproverCount; uint8 authorizationMode; uint64 participantCapacity` | `false` | — |
| `RoomOwnershipAssigned(uint64,address,bytes32)` | `uint64 roomId indexed; address creator indexed; bytes32 managedAllocationId indexed` | `false` | — |
| `ServiceBondFunded(uint64,uint64,uint256)` | `uint64 roomId indexed; uint64 bondEpoch indexed; uint256 amount` | `false` | — |
| `ServiceBondWithdrawn(uint64,uint256)` | `uint64 roomId indexed; uint256 amount` | `false` | — |
| `WithdrawalClaimed(uint64,uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; uint64 index indexed; address recipient; address asset; uint256 amount` | `false` | — |
| `WithdrawalRootPublished(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; bytes32 withdrawalRoot indexed` | `false` | — |

### Custom errors

| Signature | Named fields | Owner NatSpec |
|---|---|---|
| `BadAccounting()` | `—` | — |
| `BadAdmission()` | `—` | — |
| `BadAggregate()` | `—` | — |
| `BadApproval()` | `—` | — |
| `BadApprover()` | `—` | — |
| `BadForcedTransaction()` | `—` | — |
| `BadImport()` | `—` | — |
| `BadInput()` | `—` | — |
| `BadProof()` | `—` | — |
| `BadTemplate()` | `—` | — |
| `BadWithdrawal()` | `—` | — |
| `BondUnavailable()` | `—` | — |
| `ChallengeNotSettleable()` | `—` | — |
| `CloseNotReady()` | `—` | — |
| `DataAvailabilityUnavailable()` | `—` | — |
| `DeadlinePassed()` | `—` | — |
| `DepositTooRecent()` | `—` | — |
| `DischargeUnavailable()` | `—` | — |
| `GraceUnavailable()` | `—` | — |
| `NothingToClaim()` | `—` | — |
| `RecoveryNotReady()` | `—` | — |
| `Reentrant()` | `—` | — |
| `Unauthorized()` | `—` | — |
| `UnsupportedAsset()` | `—` | — |
| `WrongState()` | `—` | — |

### Struct and tuple layouts

#### `RoomTypes.AdmissionReceipt`

| Field | ABI type | Internal type |
|---|---|---|
| `admissionId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `depositInboxId` | `uint64` | `uint64` |
| `depositContentHash` | `bytes32` | `bytes32` |
| `deadlineBlock` | `uint64` | `uint64` |
| `maximumBatchIndex` | `uint64` | `uint64` |
| `bondEpoch` | `uint64` | `uint64` |
| `admissionFee` | `uint256` | `uint256` |
| `signature` | `bytes` | `bytes` |


## RoomManagerHostingFacet

### Functions

| Signature | Selector | Mutability | Named inputs | Named outputs | Owner NatSpec |
|---|---|---|---|---|---|
| `CANONICAL_BYTES_PER_BLOB()` | `0x03f50906` | `view` | `—` | `uint256 <unnamed>` | — |
| `GRACE_BLOCKS()` | `0x418fc67b` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_ACTIVE_APPROVERS()` | `0x3964be7d` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_AGGREGATE_ROOMS()` | `0xe4a9fe8a` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_APPROVER_PROOF_DEPTH()` | `0x34233b33` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_BLOBS_PER_BATCH()` | `0xd6160d39` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_COLD_TEMPLATE_DATA_BYTES()` | `0x3c67ed66` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_FORCED_TRANSACTION_BYTES()` | `0x65e471f2` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_IMPORT_CONFIRMATIONS()` | `0xa72aea7a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_INBOX_ITEMS_PER_BATCH()` | `0xccc6d3eb` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PARTICIPANT_CAPACITY()` | `0x8b3af9b0` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PROTOCOL_FEE_BPS()` | `0x6d947e4b` | `view` | `—` | `uint16 <unnamed>` | — |
| `MAX_WITHDRAWALS_PER_EPOCH()` | `0x4ee7b9cd` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_WITHDRAWAL_PROOF_DEPTH()` | `0x1924b7ab` | `view` | `—` | `uint256 <unnamed>` | — |
| `MIN_ADMISSION_WINDOW()` | `0xffac207a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_DEPOSIT_CONFIRMATIONS()` | `0x04b45763` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_IMPORT_CONFIRMATIONS()` | `0x35774972` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_SERVICE_BOND_MULTIPLE()` | `0xfe15dcac` | `view` | `—` | `uint256 <unnamed>` | — |
| `RECOVERY_CLOSE_BOND_MULTIPLE()` | `0x7adfc8f8` | `view` | `—` | `uint64 <unnamed>` | — |
| `RECOVERY_CLOSE_CHALLENGE_MULTIPLE()` | `0xe7ab9737` | `view` | `—` | `uint64 <unnamed>` | — |
| `REPAIR_FEE_DIVISOR()` | `0x5445cbde` | `view` | `—` | `uint256 <unnamed>` | — |
| `REPAIR_WINDOW_BLOCKS()` | `0x1f903fe2` | `view` | `—` | `uint64 <unnamed>` | — |
| `applyAggregateMember((uint64,((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]),(bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes)))` | `0x486adc89` | `nonpayable` | `(uint64,((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]),(bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes)) member` | `—` | — |
| `selectors()` | `0x6e25b978` | `pure` | `—` | `bytes4[] value` | — |
| `submitAggregate(((uint64,((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]),(bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes))[],bytes))` | `0x5e8b37ac` | `nonpayable` | `((uint64,((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]),(bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes))[],bytes) aggregate` | `—` | userdoc.notice: Settle up to eight distinct rooms with one recursive receipt. Each member is applied in an isolated call frame, so a member that fails current L1 validation remains retryable and cannot roll back members already accepted. |
| `submitBatchWithDataAvailability(uint64,((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]),(bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes))` | `0xf128abc1` | `nonpayable` | `uint64 roomId; ((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]) submission; (bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes) manifest` | `—` | — |

### Events

| Signature | Named/indexed fields | Anonymous | Owner NatSpec |
|---|---|---|---|
| `AdmissionOmissionChallenged(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `AdmissionReceiptDischarged(uint64,uint64,bytes32,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; uint256 cost` | `false` | — |
| `AdmissionRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `AggregateMemberOutcome(bytes32,uint8,uint64,uint64,bool,bytes4)` | `bytes32 aggregateHash indexed; uint8 memberIndex indexed; uint64 roomId indexed; uint64 batchIndex; bool applied; bytes4 failureSelector` | `false` | — |
| `ApproverChangeQueued(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 requestId indexed; bytes32 changeHash indexed` | `false` | — |
| `BatchAccepted(uint64,uint64,bytes32,bytes32,uint64,bool)` | `uint64 roomId indexed; uint64 batchIndex indexed; bytes32 postStateRoot indexed; bytes32 postApproverRoot; uint64 outboxEpoch; bool closed` | `false` | — |
| `ChallengePayoutClaimed(uint64,address,uint256)` | `uint64 roomId indexed; address payee indexed; uint256 amount` | `false` | — |
| `ColdTemplateDataPublished(uint64,bytes32,bytes)` | `uint64 roomId indexed; bytes32 dataHash indexed; bytes canonicalColdTemplateData` | `false` | — |
| `DataAvailabilityAccepted(uint64,uint64,uint8,bool,bool,bytes32)` | `uint64 roomId indexed; uint64 batchIndex indexed; uint8 configuredPolicy; bool usedBlob; bool usedAuthorizedFallback; bytes32 statementHash` | `false` | — |
| `DataAvailabilityConfigured(uint64,uint8,address,bytes32)` | `uint64 roomId indexed; uint8 policy; address fallbackAuthority indexed; bytes32 equivalenceProgramId indexed` | `false` | — |
| `DeadlineGracePurchased(uint64,bytes32,uint64,uint256)` | `uint64 roomId indexed; bytes32 journalHash indexed; uint64 graceDeadlineBlock; uint256 fee` | `false` | — |
| `DepositQueued(uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 inboxId indexed; address beneficiary indexed; address asset; uint256 amount` | `false` | — |
| `DepositRefunded(uint64,uint64,address)` | `uint64 roomId indexed; uint64 inboxId indexed; address depositor indexed` | `false` | — |
| `ForcedOutcomeRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `ForcedTransactionQueued(uint64,uint64,bytes32,uint64,bytes)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint64 deadlineBlock; bytes rawSignedTransaction` | `false` | — |
| `L1StateInputPublished(uint64,uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 importId indexed; uint64 sourceBlock indexed; bytes32 importRoot` | `false` | — |
| `LivenessAttested(uint64,uint64)` | `uint64 roomId indexed; uint64 attestedAt` | `false` | — |
| `OmissionChallengeOpened(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `OmissionChallengeRepaired(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; address challenger; uint256 repairFee` | `false` | — |
| `OmissionChallengeSettled(uint64,bytes32,address,uint256)` | `uint64 roomId indexed; bytes32 receiptHash indexed; address challenger indexed; uint256 penalty` | `false` | — |
| `ProtocolFeeAccrued(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeConfigured(uint16,address)` | `uint16 bps; address treasury` | `false` | — |
| `ProtocolFeeMadeClaimable(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeReversed(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeesClaimed(uint64,address,address,uint256)` | `uint64 roomId indexed; address asset indexed; address treasury indexed; uint256 amount` | `false` | — |
| `RecoveryBatchAccepted(uint64,uint64)` | `uint64 roomId indexed; uint64 batchIndex indexed` | `false` | — |
| `RoomClosedByRecovery(uint64,address)` | `uint64 roomId indexed; address closer indexed` | `false` | — |
| `RoomCreated(uint64,bytes32,bytes32,uint64,uint8,uint64)` | `uint64 roomId indexed; bytes32 coldTemplateId indexed; bytes32 initialApproverRoot indexed; uint64 activeApproverCount; uint8 authorizationMode; uint64 participantCapacity` | `false` | — |
| `RoomOwnershipAssigned(uint64,address,bytes32)` | `uint64 roomId indexed; address creator indexed; bytes32 managedAllocationId indexed` | `false` | — |
| `ServiceBondFunded(uint64,uint64,uint256)` | `uint64 roomId indexed; uint64 bondEpoch indexed; uint256 amount` | `false` | — |
| `ServiceBondWithdrawn(uint64,uint256)` | `uint64 roomId indexed; uint256 amount` | `false` | — |
| `WithdrawalClaimed(uint64,uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; uint64 index indexed; address recipient; address asset; uint256 amount` | `false` | — |
| `WithdrawalRootPublished(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; bytes32 withdrawalRoot indexed` | `false` | — |

### Custom errors

| Signature | Named fields | Owner NatSpec |
|---|---|---|
| `BadAccounting()` | `—` | — |
| `BadAdmission()` | `—` | — |
| `BadAggregate()` | `—` | — |
| `BadApproval()` | `—` | — |
| `BadApprover()` | `—` | — |
| `BadForcedTransaction()` | `—` | — |
| `BadImport()` | `—` | — |
| `BadInput()` | `—` | — |
| `BadProof()` | `—` | — |
| `BadTemplate()` | `—` | — |
| `BadWithdrawal()` | `—` | — |
| `BondUnavailable()` | `—` | — |
| `ChallengeNotSettleable()` | `—` | — |
| `CloseNotReady()` | `—` | — |
| `DataAvailabilityUnavailable()` | `—` | — |
| `DeadlinePassed()` | `—` | — |
| `DepositTooRecent()` | `—` | — |
| `DischargeUnavailable()` | `—` | — |
| `GraceUnavailable()` | `—` | — |
| `NothingToClaim()` | `—` | — |
| `RecoveryNotReady()` | `—` | — |
| `Reentrant()` | `—` | — |
| `Unauthorized()` | `—` | — |
| `UnsupportedAsset()` | `—` | — |
| `WrongState()` | `—` | — |

### Struct and tuple layouts

#### `RoomTypes.AdmissionOutcome`

| Field | ABI type | Internal type |
|---|---|---|
| `admissionId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `status` | `uint8` | `enum RoomTypes.AdmissionStatus` |
| `l2BlockNumber` | `uint64` | `uint64` |
| `transactionIndex` | `uint32` | `uint32` |
| `reasonHash` | `bytes32` | `bytes32` |

#### `RoomTypes.AdmissionReceipt`

| Field | ABI type | Internal type |
|---|---|---|
| `admissionId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `depositInboxId` | `uint64` | `uint64` |
| `depositContentHash` | `bytes32` | `bytes32` |
| `deadlineBlock` | `uint64` | `uint64` |
| `maximumBatchIndex` | `uint64` | `uint64` |
| `bondEpoch` | `uint64` | `uint64` |
| `admissionFee` | `uint256` | `uint256` |
| `signature` | `bytes` | `bytes` |

#### `RoomTypes.AdmissionRecord`

| Field | ABI type | Internal type |
|---|---|---|
| `receipt` | `(uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes)` | `struct RoomTypes.AdmissionReceipt` |
| `outcome` | `(uint64,bytes32,uint8,uint64,uint32,bytes32)` | `struct RoomTypes.AdmissionOutcome` |

#### `RoomTypes.AggregateMember`

| Field | ABI type | Internal type |
|---|---|---|
| `roomId` | `uint64` | `uint64` |
| `submission` | `((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[])` | `struct RoomTypes.BatchSubmission` |
| `dataAvailability` | `(bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes)` | `struct RoomTypes.DataAvailabilityManifest` |

#### `RoomTypes.AggregateSubmission`

| Field | ABI type | Internal type |
|---|---|---|
| `members` | `(uint64,((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]),(bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes))[]` | `struct RoomTypes.AggregateMember[]` |
| `aggregateSeal` | `bytes` | `bytes` |

#### `RoomTypes.ApproverApproval`

| Field | ABI type | Internal type |
|---|---|---|
| `index` | `uint64` | `uint64` |
| `joinedEpoch` | `uint64` | `uint64` |
| `nonce` | `uint64` | `uint64` |
| `deadline` | `uint64` | `uint64` |
| `member` | `address` | `address` |
| `proof` | `bytes32[]` | `bytes32[]` |
| `signature` | `bytes` | `bytes` |

#### `RoomTypes.ApproverChange`

| Field | ABI type | Internal type |
|---|---|---|
| `action` | `uint8` | `enum RoomTypes.ApproverAction` |
| `index` | `uint64` | `uint64` |
| `joinedEpoch` | `uint64` | `uint64` |
| `deadline` | `uint64` | `uint64` |
| `member` | `address` | `address` |
| `withdrawalCommitment` | `bytes32` | `bytes32` |
| `acceptanceSignature` | `bytes` | `bytes` |

#### `RoomTypes.AssetLiability`

| Field | ABI type | Internal type |
|---|---|---|
| `asset` | `address` | `address` |
| `pending` | `uint256` | `uint256` |
| `controlled` | `uint256` | `uint256` |
| `claimable` | `uint256` | `uint256` |
| `paid` | `uint256` | `uint256` |

#### `RoomTypes.BatchJournal`

| Field | ABI type | Internal type |
|---|---|---|
| `protocolVersion` | `uint256` | `uint256` |
| `deploymentDomain` | `bytes32` | `bytes32` |
| `roomId` | `uint64` | `uint64` |
| `authorizationMode` | `uint8` | `enum RoomTypes.AuthorizationMode` |
| `coldTemplateId` | `bytes32` | `bytes32` |
| `proofProgramId` | `bytes32` | `bytes32` |
| `proofSystemVersion` | `bytes32` | `bytes32` |
| `policyHash` | `bytes32` | `bytes32` |
| `batchIndex` | `uint64` | `uint64` |
| `startL2Block` | `uint64` | `uint64` |
| `endL2Block` | `uint64` | `uint64` |
| `preStateRoot` | `bytes32` | `bytes32` |
| `postStateRoot` | `bytes32` | `bytes32` |
| `batchDataHash` | `bytes32` | `bytes32` |
| `canonicalDataHash` | `bytes32` | `bytes32` |
| `preParticipantRoot` | `bytes32` | `bytes32` |
| `postParticipantRoot` | `bytes32` | `bytes32` |
| `preParticipantEpoch` | `uint64` | `uint64` |
| `postParticipantEpoch` | `uint64` | `uint64` |
| `preParticipantCount` | `uint64` | `uint64` |
| `postParticipantCount` | `uint64` | `uint64` |
| `participantCapacity` | `uint64` | `uint64` |
| `preApproverRoot` | `bytes32` | `bytes32` |
| `postApproverRoot` | `bytes32` | `bytes32` |
| `preApproverEpoch` | `uint64` | `uint64` |
| `postApproverEpoch` | `uint64` | `uint64` |
| `preActiveCount` | `uint64` | `uint64` |
| `postActiveCount` | `uint64` | `uint64` |
| `approverChangeCursorBefore` | `uint64` | `uint64` |
| `approverChangeCursorAfter` | `uint64` | `uint64` |
| `inboxCursorBefore` | `uint64` | `uint64` |
| `inboxCursorAfter` | `uint64` | `uint64` |
| `inboxRecordsHash` | `bytes32` | `bytes32` |
| `admissionCursorBefore` | `uint64` | `uint64` |
| `admissionCursorAfter` | `uint64` | `uint64` |
| `admissionRecordsHash` | `bytes32` | `bytes32` |
| `forcedCursorBefore` | `uint64` | `uint64` |
| `forcedCursorAfter` | `uint64` | `uint64` |
| `forcedOutcomesHash` | `bytes32` | `bytes32` |
| `importCursorBefore` | `uint64` | `uint64` |
| `importCursorAfter` | `uint64` | `uint64` |
| `importedL1Block` | `uint64` | `uint64` |
| `importedL1HeaderHash` | `bytes32` | `bytes32` |
| `importedL1StateRoot` | `bytes32` | `bytes32` |
| `importRoot` | `bytes32` | `bytes32` |
| `outboxEpoch` | `uint64` | `uint64` |
| `withdrawalRoot` | `bytes32` | `bytes32` |
| `preLiabilitiesHash` | `bytes32` | `bytes32` |
| `postLiabilitiesHash` | `bytes32` | `bytes32` |
| `approverChangesHash` | `bytes32` | `bytes32` |
| `l1InclusionDeadline` | `uint64` | `uint64` |
| `close` | `bool` | `bool` |

#### `RoomTypes.BatchSubmission`

| Field | ABI type | Internal type |
|---|---|---|
| `journal` | `(uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool)` | `struct RoomTypes.BatchJournal` |
| `seal` | `bytes` | `bytes` |
| `canonicalBatchData` | `bytes` | `bytes` |
| `approvals` | `(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[]` | `struct RoomTypes.ApproverApproval[]` |
| `approverChanges` | `(uint8,uint64,uint64,uint64,address,bytes32,bytes)[]` | `struct RoomTypes.ApproverChange[]` |
| `admissions` | `((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[]` | `struct RoomTypes.AdmissionRecord[]` |
| `forcedOutcomes` | `(uint64,bytes32,uint8,uint64,uint32,bytes32)[]` | `struct RoomTypes.ForcedOutcome[]` |
| `liabilities` | `(address,uint256,uint256,uint256,uint256)[]` | `struct RoomTypes.AssetLiability[]` |

#### `RoomTypes.DataAvailabilityManifest`

| Field | ABI type | Internal type |
|---|---|---|
| `canonicalDataHash` | `bytes32` | `bytes32` |
| `canonicalDataLength` | `uint64` | `uint64` |
| `blobStartIndex` | `uint8` | `uint8` |
| `blobVersionedHashes` | `bytes32[]` | `bytes32[]` |
| `commitments` | `bytes[]` | `bytes[]` |
| `evaluationPoints` | `bytes32[]` | `bytes32[]` |
| `evaluations` | `bytes32[]` | `bytes32[]` |
| `kzgProofs` | `bytes[]` | `bytes[]` |
| `equivalenceSeal` | `bytes` | `bytes` |
| `fallbackDeadlineBlock` | `uint64` | `uint64` |
| `fallbackSignature` | `bytes` | `bytes` |

#### `RoomTypes.ForcedOutcome`

| Field | ABI type | Internal type |
|---|---|---|
| `forcedId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `status` | `uint8` | `enum RoomTypes.AdmissionStatus` |
| `l2BlockNumber` | `uint64` | `uint64` |
| `transactionIndex` | `uint32` | `uint32` |
| `reasonHash` | `bytes32` | `bytes32` |


## RoomManagerIntakeFacet

### Functions

| Signature | Selector | Mutability | Named inputs | Named outputs | Owner NatSpec |
|---|---|---|---|---|---|
| `CANONICAL_BYTES_PER_BLOB()` | `0x03f50906` | `view` | `—` | `uint256 <unnamed>` | — |
| `GRACE_BLOCKS()` | `0x418fc67b` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_ACTIVE_APPROVERS()` | `0x3964be7d` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_AGGREGATE_ROOMS()` | `0xe4a9fe8a` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_APPROVER_PROOF_DEPTH()` | `0x34233b33` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_BLOBS_PER_BATCH()` | `0xd6160d39` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_COLD_TEMPLATE_DATA_BYTES()` | `0x3c67ed66` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_FORCED_TRANSACTION_BYTES()` | `0x65e471f2` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_IMPORT_CONFIRMATIONS()` | `0xa72aea7a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_INBOX_ITEMS_PER_BATCH()` | `0xccc6d3eb` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PARTICIPANT_CAPACITY()` | `0x8b3af9b0` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PROTOCOL_FEE_BPS()` | `0x6d947e4b` | `view` | `—` | `uint16 <unnamed>` | — |
| `MAX_WITHDRAWALS_PER_EPOCH()` | `0x4ee7b9cd` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_WITHDRAWAL_PROOF_DEPTH()` | `0x1924b7ab` | `view` | `—` | `uint256 <unnamed>` | — |
| `MIN_ADMISSION_WINDOW()` | `0xffac207a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_DEPOSIT_CONFIRMATIONS()` | `0x04b45763` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_IMPORT_CONFIRMATIONS()` | `0x35774972` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_SERVICE_BOND_MULTIPLE()` | `0xfe15dcac` | `view` | `—` | `uint256 <unnamed>` | — |
| `RECOVERY_CLOSE_BOND_MULTIPLE()` | `0x7adfc8f8` | `view` | `—` | `uint64 <unnamed>` | — |
| `RECOVERY_CLOSE_CHALLENGE_MULTIPLE()` | `0xe7ab9737` | `view` | `—` | `uint64 <unnamed>` | — |
| `REPAIR_FEE_DIVISOR()` | `0x5445cbde` | `view` | `—` | `uint256 <unnamed>` | — |
| `REPAIR_WINDOW_BLOCKS()` | `0x1f903fe2` | `view` | `—` | `uint64 <unnamed>` | — |
| `challengeOmittedAdmission(uint64,(uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),bytes)` | `0xc857e486` | `nonpayable` | `uint64 roomId; (uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes) receipt; bytes rawSignedTransaction` | `—` | — |
| `createManagedRoom(address,bytes32,(bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[])` | `0x3346a946` | `nonpayable` | `address creator; bytes32 allocationId; (bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64) config; bytes32 coldTemplateId; bytes32 initialApproverRoot; uint64 initialActiveApproverCount; bytes32 initialParticipantRoot; uint64 initialParticipantCount; bytes canonicalColdTemplateData; address[] supportedAssets` | `uint64 roomId` | — |
| `createManagedRoomWithDataAvailability(address,bytes32,(bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),(uint8,address,bytes32),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[])` | `0x5d33a938` | `nonpayable` | `address creator; bytes32 allocationId; (bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64) config; (uint8,address,bytes32) dataAvailability; bytes32 coldTemplateId; bytes32 initialApproverRoot; uint64 initialActiveApproverCount; bytes32 initialParticipantRoot; uint64 initialParticipantCount; bytes canonicalColdTemplateData; address[] supportedAssets` | `uint64 roomId` | — |
| `createRoom((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[])` | `0x6e9bee68` | `nonpayable` | `(bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64) config; bytes32 coldTemplateId; bytes32 initialApproverRoot; uint64 initialActiveApproverCount; bytes32 initialParticipantRoot; uint64 initialParticipantCount; bytes canonicalColdTemplateData; address[] supportedAssets` | `uint64 roomId` | — |
| `createRoomWithDataAvailability((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),(uint8,address,bytes32),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[])` | `0xb8487b5f` | `nonpayable` | `(bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64) config; (uint8,address,bytes32) dataAvailability; bytes32 coldTemplateId; bytes32 initialApproverRoot; uint64 initialActiveApproverCount; bytes32 initialParticipantRoot; uint64 initialParticipantCount; bytes canonicalColdTemplateData; address[] supportedAssets` | `uint64 roomId` | — |
| `forceTransaction(uint64,bytes,uint64)` | `0x3913b8b2` | `nonpayable` | `uint64 roomId; bytes rawSignedTransaction; uint64 deadlineBlock` | `uint64 forcedId` | — |
| `fundServiceBond(uint64)` | `0x9442d5fd` | `payable` | `uint64 roomId` | `—` | devdoc.details: Restricted to the bond's own beneficiary. `withdrawServiceBond` returns the whole balance to `admissionSigner` and nothing tracks per-funder shares, so a permissionless entry point would only invite unrecoverable third-party gifts to the operator. |
| `queueApproverChange(uint64,(uint8,uint64,uint64,uint64,address,bytes32,bytes))` | `0xa4e36b10` | `nonpayable` | `uint64 roomId; (uint8,uint64,uint64,uint64,address,bytes32,bytes) change` | `uint64 requestId` | — |
| `queueDeposit(uint64,address,uint256,address)` | `0x63b22c00` | `payable` | `uint64 roomId; address asset; uint256 amount; address beneficiary` | `uint64 inboxId` | — |
| `refundExpiredDeposit(uint64,uint64)` | `0x8591b7de` | `nonpayable` | `uint64 roomId; uint64 inboxId` | `—` | — |
| `selectors()` | `0x6e25b978` | `pure` | `—` | `bytes4[] value` | — |
| `withdrawServiceBond(uint64)` | `0x4be23ca9` | `nonpayable` | `uint64 roomId` | `—` | — |

### Events

| Signature | Named/indexed fields | Anonymous | Owner NatSpec |
|---|---|---|---|
| `AdmissionOmissionChallenged(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `AdmissionReceiptDischarged(uint64,uint64,bytes32,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; uint256 cost` | `false` | — |
| `AdmissionRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `AggregateMemberOutcome(bytes32,uint8,uint64,uint64,bool,bytes4)` | `bytes32 aggregateHash indexed; uint8 memberIndex indexed; uint64 roomId indexed; uint64 batchIndex; bool applied; bytes4 failureSelector` | `false` | — |
| `ApproverChangeQueued(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 requestId indexed; bytes32 changeHash indexed` | `false` | — |
| `BatchAccepted(uint64,uint64,bytes32,bytes32,uint64,bool)` | `uint64 roomId indexed; uint64 batchIndex indexed; bytes32 postStateRoot indexed; bytes32 postApproverRoot; uint64 outboxEpoch; bool closed` | `false` | — |
| `ChallengePayoutClaimed(uint64,address,uint256)` | `uint64 roomId indexed; address payee indexed; uint256 amount` | `false` | — |
| `ColdTemplateDataPublished(uint64,bytes32,bytes)` | `uint64 roomId indexed; bytes32 dataHash indexed; bytes canonicalColdTemplateData` | `false` | — |
| `DataAvailabilityAccepted(uint64,uint64,uint8,bool,bool,bytes32)` | `uint64 roomId indexed; uint64 batchIndex indexed; uint8 configuredPolicy; bool usedBlob; bool usedAuthorizedFallback; bytes32 statementHash` | `false` | — |
| `DataAvailabilityConfigured(uint64,uint8,address,bytes32)` | `uint64 roomId indexed; uint8 policy; address fallbackAuthority indexed; bytes32 equivalenceProgramId indexed` | `false` | — |
| `DeadlineGracePurchased(uint64,bytes32,uint64,uint256)` | `uint64 roomId indexed; bytes32 journalHash indexed; uint64 graceDeadlineBlock; uint256 fee` | `false` | — |
| `DepositQueued(uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 inboxId indexed; address beneficiary indexed; address asset; uint256 amount` | `false` | — |
| `DepositRefunded(uint64,uint64,address)` | `uint64 roomId indexed; uint64 inboxId indexed; address depositor indexed` | `false` | — |
| `ForcedOutcomeRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `ForcedTransactionQueued(uint64,uint64,bytes32,uint64,bytes)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint64 deadlineBlock; bytes rawSignedTransaction` | `false` | — |
| `L1StateInputPublished(uint64,uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 importId indexed; uint64 sourceBlock indexed; bytes32 importRoot` | `false` | — |
| `LivenessAttested(uint64,uint64)` | `uint64 roomId indexed; uint64 attestedAt` | `false` | — |
| `OmissionChallengeOpened(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `OmissionChallengeRepaired(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; address challenger; uint256 repairFee` | `false` | — |
| `OmissionChallengeSettled(uint64,bytes32,address,uint256)` | `uint64 roomId indexed; bytes32 receiptHash indexed; address challenger indexed; uint256 penalty` | `false` | — |
| `ProtocolFeeAccrued(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeConfigured(uint16,address)` | `uint16 bps; address treasury` | `false` | — |
| `ProtocolFeeMadeClaimable(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeReversed(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeesClaimed(uint64,address,address,uint256)` | `uint64 roomId indexed; address asset indexed; address treasury indexed; uint256 amount` | `false` | — |
| `RecoveryBatchAccepted(uint64,uint64)` | `uint64 roomId indexed; uint64 batchIndex indexed` | `false` | — |
| `RoomClosedByRecovery(uint64,address)` | `uint64 roomId indexed; address closer indexed` | `false` | — |
| `RoomCreated(uint64,bytes32,bytes32,uint64,uint8,uint64)` | `uint64 roomId indexed; bytes32 coldTemplateId indexed; bytes32 initialApproverRoot indexed; uint64 activeApproverCount; uint8 authorizationMode; uint64 participantCapacity` | `false` | — |
| `RoomOwnershipAssigned(uint64,address,bytes32)` | `uint64 roomId indexed; address creator indexed; bytes32 managedAllocationId indexed` | `false` | — |
| `ServiceBondFunded(uint64,uint64,uint256)` | `uint64 roomId indexed; uint64 bondEpoch indexed; uint256 amount` | `false` | — |
| `ServiceBondWithdrawn(uint64,uint256)` | `uint64 roomId indexed; uint256 amount` | `false` | — |
| `WithdrawalClaimed(uint64,uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; uint64 index indexed; address recipient; address asset; uint256 amount` | `false` | — |
| `WithdrawalRootPublished(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; bytes32 withdrawalRoot indexed` | `false` | — |

### Custom errors

| Signature | Named fields | Owner NatSpec |
|---|---|---|
| `BadAccounting()` | `—` | — |
| `BadAdmission()` | `—` | — |
| `BadAggregate()` | `—` | — |
| `BadApproval()` | `—` | — |
| `BadApprover()` | `—` | — |
| `BadForcedTransaction()` | `—` | — |
| `BadImport()` | `—` | — |
| `BadInput()` | `—` | — |
| `BadProof()` | `—` | — |
| `BadTemplate()` | `—` | — |
| `BadWithdrawal()` | `—` | — |
| `BondUnavailable()` | `—` | — |
| `ChallengeNotSettleable()` | `—` | — |
| `CloseNotReady()` | `—` | — |
| `DataAvailabilityUnavailable()` | `—` | — |
| `DeadlinePassed()` | `—` | — |
| `DepositTooRecent()` | `—` | — |
| `DischargeUnavailable()` | `—` | — |
| `GraceUnavailable()` | `—` | — |
| `NothingToClaim()` | `—` | — |
| `RecoveryNotReady()` | `—` | — |
| `Reentrant()` | `—` | — |
| `Unauthorized()` | `—` | — |
| `UnsupportedAsset()` | `—` | — |
| `WrongState()` | `—` | — |

### Struct and tuple layouts

#### `RoomTypes.AdmissionReceipt`

| Field | ABI type | Internal type |
|---|---|---|
| `admissionId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `depositInboxId` | `uint64` | `uint64` |
| `depositContentHash` | `bytes32` | `bytes32` |
| `deadlineBlock` | `uint64` | `uint64` |
| `maximumBatchIndex` | `uint64` | `uint64` |
| `bondEpoch` | `uint64` | `uint64` |
| `admissionFee` | `uint256` | `uint256` |
| `signature` | `bytes` | `bytes` |

#### `RoomTypes.ApproverChange`

| Field | ABI type | Internal type |
|---|---|---|
| `action` | `uint8` | `enum RoomTypes.ApproverAction` |
| `index` | `uint64` | `uint64` |
| `joinedEpoch` | `uint64` | `uint64` |
| `deadline` | `uint64` | `uint64` |
| `member` | `address` | `address` |
| `withdrawalCommitment` | `bytes32` | `bytes32` |
| `acceptanceSignature` | `bytes` | `bytes` |

#### `RoomTypes.DataAvailabilityConfig`

| Field | ABI type | Internal type |
|---|---|---|
| `policy` | `uint8` | `enum RoomTypes.DataAvailabilityPolicy` |
| `fallbackAuthority` | `address` | `address` |
| `equivalenceProgramId` | `bytes32` | `bytes32` |

#### `RoomTypes.RoomConfig`

| Field | ABI type | Internal type |
|---|---|---|
| `policyHash` | `bytes32` | `bytes32` |
| `adapterPolicyRoot` | `bytes32` | `bytes32` |
| `importPublisher` | `address` | `address` |
| `minimumImportConfirmations` | `uint64` | `uint64` |
| `minimumDepositConfirmations` | `uint64` | `uint64` |
| `inactivityTimeout` | `uint64` | `uint64` |
| `authorizationMode` | `uint8` | `enum RoomTypes.AuthorizationMode` |
| `admissionSigner` | `address` | `address` |
| `maximumAdmissionWindow` | `uint64` | `uint64` |
| `minimumServiceBond` | `uint96` | `uint96` |
| `omissionPenalty` | `uint96` | `uint96` |
| `participantCapacity` | `uint64` | `uint64` |


## RoomManagerImportFacet

### Functions

| Signature | Selector | Mutability | Named inputs | Named outputs | Owner NatSpec |
|---|---|---|---|---|---|
| `CANONICAL_BYTES_PER_BLOB()` | `0x03f50906` | `view` | `—` | `uint256 <unnamed>` | — |
| `GRACE_BLOCKS()` | `0x418fc67b` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_ACTIVE_APPROVERS()` | `0x3964be7d` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_AGGREGATE_ROOMS()` | `0xe4a9fe8a` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_APPROVER_PROOF_DEPTH()` | `0x34233b33` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_BLOBS_PER_BATCH()` | `0xd6160d39` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_COLD_TEMPLATE_DATA_BYTES()` | `0x3c67ed66` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_FORCED_TRANSACTION_BYTES()` | `0x65e471f2` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_IMPORT_CONFIRMATIONS()` | `0xa72aea7a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_INBOX_ITEMS_PER_BATCH()` | `0xccc6d3eb` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PARTICIPANT_CAPACITY()` | `0x8b3af9b0` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PROTOCOL_FEE_BPS()` | `0x6d947e4b` | `view` | `—` | `uint16 <unnamed>` | — |
| `MAX_WITHDRAWALS_PER_EPOCH()` | `0x4ee7b9cd` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_WITHDRAWAL_PROOF_DEPTH()` | `0x1924b7ab` | `view` | `—` | `uint256 <unnamed>` | — |
| `MIN_ADMISSION_WINDOW()` | `0xffac207a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_DEPOSIT_CONFIRMATIONS()` | `0x04b45763` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_IMPORT_CONFIRMATIONS()` | `0x35774972` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_SERVICE_BOND_MULTIPLE()` | `0xfe15dcac` | `view` | `—` | `uint256 <unnamed>` | — |
| `RECOVERY_CLOSE_BOND_MULTIPLE()` | `0x7adfc8f8` | `view` | `—` | `uint64 <unnamed>` | — |
| `RECOVERY_CLOSE_CHALLENGE_MULTIPLE()` | `0xe7ab9737` | `view` | `—` | `uint64 <unnamed>` | — |
| `REPAIR_FEE_DIVISOR()` | `0x5445cbde` | `view` | `—` | `uint256 <unnamed>` | — |
| `REPAIR_WINDOW_BLOCKS()` | `0x1f903fe2` | `view` | `—` | `uint64 <unnamed>` | — |
| `publishL1StateInput(uint64,(uint64,uint64,address,bytes32,bytes32,bytes32,bytes32,bytes32,bytes,bytes32[]))` | `0x0b14025e` | `nonpayable` | `uint64 roomId; (uint64,uint64,address,bytes32,bytes32,bytes32,bytes32,bytes32,bytes,bytes32[]) input` | `uint64 importId; bytes32 importRoot` | — |
| `selectors()` | `0x6e25b978` | `pure` | `—` | `bytes4[] value` | — |

### Events

| Signature | Named/indexed fields | Anonymous | Owner NatSpec |
|---|---|---|---|
| `AdmissionOmissionChallenged(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `AdmissionReceiptDischarged(uint64,uint64,bytes32,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; uint256 cost` | `false` | — |
| `AdmissionRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `AggregateMemberOutcome(bytes32,uint8,uint64,uint64,bool,bytes4)` | `bytes32 aggregateHash indexed; uint8 memberIndex indexed; uint64 roomId indexed; uint64 batchIndex; bool applied; bytes4 failureSelector` | `false` | — |
| `ApproverChangeQueued(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 requestId indexed; bytes32 changeHash indexed` | `false` | — |
| `BatchAccepted(uint64,uint64,bytes32,bytes32,uint64,bool)` | `uint64 roomId indexed; uint64 batchIndex indexed; bytes32 postStateRoot indexed; bytes32 postApproverRoot; uint64 outboxEpoch; bool closed` | `false` | — |
| `ChallengePayoutClaimed(uint64,address,uint256)` | `uint64 roomId indexed; address payee indexed; uint256 amount` | `false` | — |
| `ColdTemplateDataPublished(uint64,bytes32,bytes)` | `uint64 roomId indexed; bytes32 dataHash indexed; bytes canonicalColdTemplateData` | `false` | — |
| `DataAvailabilityAccepted(uint64,uint64,uint8,bool,bool,bytes32)` | `uint64 roomId indexed; uint64 batchIndex indexed; uint8 configuredPolicy; bool usedBlob; bool usedAuthorizedFallback; bytes32 statementHash` | `false` | — |
| `DataAvailabilityConfigured(uint64,uint8,address,bytes32)` | `uint64 roomId indexed; uint8 policy; address fallbackAuthority indexed; bytes32 equivalenceProgramId indexed` | `false` | — |
| `DeadlineGracePurchased(uint64,bytes32,uint64,uint256)` | `uint64 roomId indexed; bytes32 journalHash indexed; uint64 graceDeadlineBlock; uint256 fee` | `false` | — |
| `DepositQueued(uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 inboxId indexed; address beneficiary indexed; address asset; uint256 amount` | `false` | — |
| `DepositRefunded(uint64,uint64,address)` | `uint64 roomId indexed; uint64 inboxId indexed; address depositor indexed` | `false` | — |
| `ForcedOutcomeRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `ForcedTransactionQueued(uint64,uint64,bytes32,uint64,bytes)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint64 deadlineBlock; bytes rawSignedTransaction` | `false` | — |
| `L1StateInputPublished(uint64,uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 importId indexed; uint64 sourceBlock indexed; bytes32 importRoot` | `false` | — |
| `LivenessAttested(uint64,uint64)` | `uint64 roomId indexed; uint64 attestedAt` | `false` | — |
| `OmissionChallengeOpened(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `OmissionChallengeRepaired(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; address challenger; uint256 repairFee` | `false` | — |
| `OmissionChallengeSettled(uint64,bytes32,address,uint256)` | `uint64 roomId indexed; bytes32 receiptHash indexed; address challenger indexed; uint256 penalty` | `false` | — |
| `ProtocolFeeAccrued(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeConfigured(uint16,address)` | `uint16 bps; address treasury` | `false` | — |
| `ProtocolFeeMadeClaimable(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeReversed(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeesClaimed(uint64,address,address,uint256)` | `uint64 roomId indexed; address asset indexed; address treasury indexed; uint256 amount` | `false` | — |
| `RecoveryBatchAccepted(uint64,uint64)` | `uint64 roomId indexed; uint64 batchIndex indexed` | `false` | — |
| `RoomClosedByRecovery(uint64,address)` | `uint64 roomId indexed; address closer indexed` | `false` | — |
| `RoomCreated(uint64,bytes32,bytes32,uint64,uint8,uint64)` | `uint64 roomId indexed; bytes32 coldTemplateId indexed; bytes32 initialApproverRoot indexed; uint64 activeApproverCount; uint8 authorizationMode; uint64 participantCapacity` | `false` | — |
| `RoomOwnershipAssigned(uint64,address,bytes32)` | `uint64 roomId indexed; address creator indexed; bytes32 managedAllocationId indexed` | `false` | — |
| `ServiceBondFunded(uint64,uint64,uint256)` | `uint64 roomId indexed; uint64 bondEpoch indexed; uint256 amount` | `false` | — |
| `ServiceBondWithdrawn(uint64,uint256)` | `uint64 roomId indexed; uint256 amount` | `false` | — |
| `WithdrawalClaimed(uint64,uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; uint64 index indexed; address recipient; address asset; uint256 amount` | `false` | — |
| `WithdrawalRootPublished(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; bytes32 withdrawalRoot indexed` | `false` | — |

### Custom errors

| Signature | Named fields | Owner NatSpec |
|---|---|---|
| `BadAccounting()` | `—` | — |
| `BadAdmission()` | `—` | — |
| `BadAggregate()` | `—` | — |
| `BadApproval()` | `—` | — |
| `BadApprover()` | `—` | — |
| `BadForcedTransaction()` | `—` | — |
| `BadImport()` | `—` | — |
| `BadInput()` | `—` | — |
| `BadProof()` | `—` | — |
| `BadTemplate()` | `—` | — |
| `BadWithdrawal()` | `—` | — |
| `BondUnavailable()` | `—` | — |
| `ChallengeNotSettleable()` | `—` | — |
| `CloseNotReady()` | `—` | — |
| `DataAvailabilityUnavailable()` | `—` | — |
| `DeadlinePassed()` | `—` | — |
| `DepositTooRecent()` | `—` | — |
| `DischargeUnavailable()` | `—` | — |
| `GraceUnavailable()` | `—` | — |
| `InvalidHeader()` | `—` | — |
| `NothingToClaim()` | `—` | — |
| `RecoveryNotReady()` | `—` | — |
| `Reentrant()` | `—` | — |
| `Unauthorized()` | `—` | — |
| `UnsupportedAsset()` | `—` | — |
| `WrongState()` | `—` | — |

### Struct and tuple layouts

#### `RoomTypes.L1StateInput`

| Field | ABI type | Internal type |
|---|---|---|
| `sourceBlock` | `uint64` | `uint64` |
| `expiryBlock` | `uint64` | `uint64` |
| `source` | `address` | `address` |
| `sourceCodeHash` | `bytes32` | `bytes32` |
| `storageKeysRoot` | `bytes32` | `bytes32` |
| `adapterId` | `bytes32` | `bytes32` |
| `adapterVersion` | `bytes32` | `bytes32` |
| `payloadCommitment` | `bytes32` | `bytes32` |
| `headerRlp` | `bytes` | `bytes` |
| `adapterPolicyProof` | `bytes32[]` | `bytes32[]` |


## RoomManagerObservationFacet

### Functions

| Signature | Selector | Mutability | Named inputs | Named outputs | Owner NatSpec |
|---|---|---|---|---|---|
| `CANONICAL_BYTES_PER_BLOB()` | `0x03f50906` | `view` | `—` | `uint256 <unnamed>` | — |
| `GRACE_BLOCKS()` | `0x418fc67b` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_ACTIVE_APPROVERS()` | `0x3964be7d` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_AGGREGATE_ROOMS()` | `0xe4a9fe8a` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_APPROVER_PROOF_DEPTH()` | `0x34233b33` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_BLOBS_PER_BATCH()` | `0xd6160d39` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_COLD_TEMPLATE_DATA_BYTES()` | `0x3c67ed66` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_FORCED_TRANSACTION_BYTES()` | `0x65e471f2` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_IMPORT_CONFIRMATIONS()` | `0xa72aea7a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_INBOX_ITEMS_PER_BATCH()` | `0xccc6d3eb` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PARTICIPANT_CAPACITY()` | `0x8b3af9b0` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PROTOCOL_FEE_BPS()` | `0x6d947e4b` | `view` | `—` | `uint16 <unnamed>` | — |
| `MAX_WITHDRAWALS_PER_EPOCH()` | `0x4ee7b9cd` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_WITHDRAWAL_PROOF_DEPTH()` | `0x1924b7ab` | `view` | `—` | `uint256 <unnamed>` | — |
| `MIN_ADMISSION_WINDOW()` | `0xffac207a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_DEPOSIT_CONFIRMATIONS()` | `0x04b45763` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_IMPORT_CONFIRMATIONS()` | `0x35774972` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_SERVICE_BOND_MULTIPLE()` | `0xfe15dcac` | `view` | `—` | `uint256 <unnamed>` | — |
| `RECOVERY_CLOSE_BOND_MULTIPLE()` | `0x7adfc8f8` | `view` | `—` | `uint64 <unnamed>` | — |
| `RECOVERY_CLOSE_CHALLENGE_MULTIPLE()` | `0xe7ab9737` | `view` | `—` | `uint64 <unnamed>` | — |
| `REPAIR_FEE_DIVISOR()` | `0x5445cbde` | `view` | `—` | `uint256 <unnamed>` | — |
| `REPAIR_WINDOW_BLOCKS()` | `0x1f903fe2` | `view` | `—` | `uint64 <unnamed>` | — |
| `admissionOutcomeHash(uint64,uint64)` | `0x22bdd5cf` | `view` | `uint64 roomId; uint64 admissionId` | `bytes32 <unnamed>` | — |
| `approvalNonce(uint64,address)` | `0x34cb3d89` | `view` | `uint64 roomId; address member` | `uint64 <unnamed>` | — |
| `assets(uint64)` | `0xfdb72eee` | `view` | `uint64 roomId` | `address[] <unnamed>` | — |
| `challengeEscrow(uint64)` | `0xed5cfba5` | `view` | `uint64 roomId` | `uint256 <unnamed>` | — |
| `challengePayoutOf(uint64,address)` | `0xff5a1965` | `view` | `uint64 roomId; address payee` | `uint256 <unnamed>` | — |
| `dataAvailabilityConfig(uint64)` | `0x68bf3d8a` | `view` | `uint64 roomId` | `(uint8,address,bytes32) <unnamed>` | — |
| `depositEntry(uint64,uint64)` | `0x863acbda` | `view` | `uint64 roomId; uint64 inboxId` | `(address,address,address,uint256,uint64,bool,bool) <unnamed>` | — |
| `forcedEntry(uint64,uint64)` | `0xa4a3e1f1` | `view` | `uint64 roomId; uint64 forcedId` | `(bytes32,uint64) <unnamed>` | — |
| `forcedOutcomeHash(uint64,uint64)` | `0xf8b88df6` | `view` | `uint64 roomId; uint64 forcedId` | `bytes32 <unnamed>` | — |
| `graceDeadline(uint64,bytes32)` | `0xf9503f5a` | `view` | `uint64 roomId; bytes32 journalHash` | `uint64 <unnamed>` | — |
| `hashAggregateSubmission(((uint64,((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]),(bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes))[],bytes))` | `0x34df49af` | `view` | `((uint64,((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]),(bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes))[],bytes) aggregate` | `bytes32 <unnamed>` | — |
| `hashApproverLeaf(uint64,address,uint64)` | `0x4089043e` | `pure` | `uint64 index; address member; uint64 joinedEpoch` | `bytes32 <unnamed>` | — |
| `hashBatchJournal((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool))` | `0x5692da78` | `pure` | `(uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool) journal` | `bytes32 <unnamed>` | — |
| `hashDataAvailabilityStatement(uint64,(uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),(bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes))` | `0x049aa8b8` | `view` | `uint64 roomId; (uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool) journal; (bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes) manifest` | `bytes32 <unnamed>` | — |
| `hashDepositContent(address,address,address,uint256)` | `0x8a127b4d` | `pure` | `address depositor; address beneficiary; address asset; uint256 amount` | `bytes32 <unnamed>` | — |
| `inboxRecordsHash(uint64,uint64,uint64)` | `0x8b6c15bc` | `view` | `uint64 roomId; uint64 cursorBefore; uint64 cursorAfter` | `bytes32 <unnamed>` | — |
| `isWithdrawalClaimed(uint64,uint64,uint64)` | `0x04d062b3` | `view` | `uint64 roomId; uint64 outboxEpoch; uint64 index` | `bool <unnamed>` | — |
| `liability(uint64,address)` | `0xac26b01e` | `view` | `uint64 roomId; address asset` | `(uint256,uint256,uint256,uint256) <unnamed>` | — |
| `managedAllocationId(uint64)` | `0x2e512038` | `view` | `uint64 roomId` | `bytes32 <unnamed>` | — |
| `omissionChallenge(uint64,bytes32)` | `0xc1d01033` | `view` | `uint64 roomId; bytes32 receiptHash` | `(address,uint64,uint64,uint64,bytes32,uint256,uint64) <unnamed>` | — |
| `reusableAfterOutboxEpoch(uint64,uint64)` | `0x563b68b9` | `view` | `uint64 roomId; uint64 index` | `uint64 <unnamed>` | — |
| `roomCreator(uint64)` | `0x1ad1c746` | `view` | `uint64 roomId` | `address <unnamed>` | — |
| `roomState(uint64)` | `0x9e0216cf` | `view` | `uint64 roomId` | `(uint8,uint8,bytes32,bytes32,bytes32,bytes32,bytes32,address,address,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,address,uint96,uint96,uint256,bytes32,uint64,bool) <unnamed>` | — |
| `selectors()` | `0x6e25b978` | `pure` | `—` | `bytes4[] value` | — |
| `verifyWithdrawalProof(uint64,uint64,(uint64,uint64,address,address,uint256),bytes32[])` | `0xb25f94b3` | `view` | `uint64 roomId; uint64 outboxEpoch; (uint64,uint64,address,address,uint256) withdrawal; bytes32[] proof` | `bool <unnamed>` | — |
| `withdrawalLeaf(uint64,uint64,(uint64,uint64,address,address,uint256))` | `0x6295ca8d` | `view` | `uint64 roomId; uint64 outboxEpoch; (uint64,uint64,address,address,uint256) withdrawal` | `bytes32 <unnamed>` | — |
| `withdrawalRoot(uint64,uint64)` | `0xc016d6f8` | `view` | `uint64 roomId; uint64 outboxEpoch` | `bytes32 <unnamed>` | — |

### Events

| Signature | Named/indexed fields | Anonymous | Owner NatSpec |
|---|---|---|---|
| `AdmissionOmissionChallenged(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `AdmissionReceiptDischarged(uint64,uint64,bytes32,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; uint256 cost` | `false` | — |
| `AdmissionRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `AggregateMemberOutcome(bytes32,uint8,uint64,uint64,bool,bytes4)` | `bytes32 aggregateHash indexed; uint8 memberIndex indexed; uint64 roomId indexed; uint64 batchIndex; bool applied; bytes4 failureSelector` | `false` | — |
| `ApproverChangeQueued(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 requestId indexed; bytes32 changeHash indexed` | `false` | — |
| `BatchAccepted(uint64,uint64,bytes32,bytes32,uint64,bool)` | `uint64 roomId indexed; uint64 batchIndex indexed; bytes32 postStateRoot indexed; bytes32 postApproverRoot; uint64 outboxEpoch; bool closed` | `false` | — |
| `ChallengePayoutClaimed(uint64,address,uint256)` | `uint64 roomId indexed; address payee indexed; uint256 amount` | `false` | — |
| `ColdTemplateDataPublished(uint64,bytes32,bytes)` | `uint64 roomId indexed; bytes32 dataHash indexed; bytes canonicalColdTemplateData` | `false` | — |
| `DataAvailabilityAccepted(uint64,uint64,uint8,bool,bool,bytes32)` | `uint64 roomId indexed; uint64 batchIndex indexed; uint8 configuredPolicy; bool usedBlob; bool usedAuthorizedFallback; bytes32 statementHash` | `false` | — |
| `DataAvailabilityConfigured(uint64,uint8,address,bytes32)` | `uint64 roomId indexed; uint8 policy; address fallbackAuthority indexed; bytes32 equivalenceProgramId indexed` | `false` | — |
| `DeadlineGracePurchased(uint64,bytes32,uint64,uint256)` | `uint64 roomId indexed; bytes32 journalHash indexed; uint64 graceDeadlineBlock; uint256 fee` | `false` | — |
| `DepositQueued(uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 inboxId indexed; address beneficiary indexed; address asset; uint256 amount` | `false` | — |
| `DepositRefunded(uint64,uint64,address)` | `uint64 roomId indexed; uint64 inboxId indexed; address depositor indexed` | `false` | — |
| `ForcedOutcomeRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `ForcedTransactionQueued(uint64,uint64,bytes32,uint64,bytes)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint64 deadlineBlock; bytes rawSignedTransaction` | `false` | — |
| `L1StateInputPublished(uint64,uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 importId indexed; uint64 sourceBlock indexed; bytes32 importRoot` | `false` | — |
| `LivenessAttested(uint64,uint64)` | `uint64 roomId indexed; uint64 attestedAt` | `false` | — |
| `OmissionChallengeOpened(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `OmissionChallengeRepaired(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; address challenger; uint256 repairFee` | `false` | — |
| `OmissionChallengeSettled(uint64,bytes32,address,uint256)` | `uint64 roomId indexed; bytes32 receiptHash indexed; address challenger indexed; uint256 penalty` | `false` | — |
| `ProtocolFeeAccrued(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeConfigured(uint16,address)` | `uint16 bps; address treasury` | `false` | — |
| `ProtocolFeeMadeClaimable(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeReversed(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeesClaimed(uint64,address,address,uint256)` | `uint64 roomId indexed; address asset indexed; address treasury indexed; uint256 amount` | `false` | — |
| `RecoveryBatchAccepted(uint64,uint64)` | `uint64 roomId indexed; uint64 batchIndex indexed` | `false` | — |
| `RoomClosedByRecovery(uint64,address)` | `uint64 roomId indexed; address closer indexed` | `false` | — |
| `RoomCreated(uint64,bytes32,bytes32,uint64,uint8,uint64)` | `uint64 roomId indexed; bytes32 coldTemplateId indexed; bytes32 initialApproverRoot indexed; uint64 activeApproverCount; uint8 authorizationMode; uint64 participantCapacity` | `false` | — |
| `RoomOwnershipAssigned(uint64,address,bytes32)` | `uint64 roomId indexed; address creator indexed; bytes32 managedAllocationId indexed` | `false` | — |
| `ServiceBondFunded(uint64,uint64,uint256)` | `uint64 roomId indexed; uint64 bondEpoch indexed; uint256 amount` | `false` | — |
| `ServiceBondWithdrawn(uint64,uint256)` | `uint64 roomId indexed; uint256 amount` | `false` | — |
| `WithdrawalClaimed(uint64,uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; uint64 index indexed; address recipient; address asset; uint256 amount` | `false` | — |
| `WithdrawalRootPublished(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; bytes32 withdrawalRoot indexed` | `false` | — |

### Custom errors

| Signature | Named fields | Owner NatSpec |
|---|---|---|
| `BadAccounting()` | `—` | — |
| `BadAdmission()` | `—` | — |
| `BadAggregate()` | `—` | — |
| `BadApproval()` | `—` | — |
| `BadApprover()` | `—` | — |
| `BadForcedTransaction()` | `—` | — |
| `BadImport()` | `—` | — |
| `BadInput()` | `—` | — |
| `BadProof()` | `—` | — |
| `BadTemplate()` | `—` | — |
| `BadWithdrawal()` | `—` | — |
| `BondUnavailable()` | `—` | — |
| `ChallengeNotSettleable()` | `—` | — |
| `CloseNotReady()` | `—` | — |
| `DataAvailabilityUnavailable()` | `—` | — |
| `DeadlinePassed()` | `—` | — |
| `DepositTooRecent()` | `—` | — |
| `DischargeUnavailable()` | `—` | — |
| `GraceUnavailable()` | `—` | — |
| `NothingToClaim()` | `—` | — |
| `RecoveryNotReady()` | `—` | — |
| `Reentrant()` | `—` | — |
| `Unauthorized()` | `—` | — |
| `UnsupportedAsset()` | `—` | — |
| `WrongState()` | `—` | — |

### Struct and tuple layouts

#### `IRoomManager.DepositEntry`

| Field | ABI type | Internal type |
|---|---|---|
| `depositor` | `address` | `address` |
| `beneficiary` | `address` | `address` |
| `asset` | `address` | `address` |
| `amount` | `uint256` | `uint256` |
| `queuedAtBlock` | `uint64` | `uint64` |
| `consumed` | `bool` | `bool` |
| `refunded` | `bool` | `bool` |

#### `IRoomManager.ForcedEntry`

| Field | ABI type | Internal type |
|---|---|---|
| `transactionHash` | `bytes32` | `bytes32` |
| `deadlineBlock` | `uint64` | `uint64` |

#### `IRoomManager.Liability`

| Field | ABI type | Internal type |
|---|---|---|
| `pending` | `uint256` | `uint256` |
| `controlled` | `uint256` | `uint256` |
| `claimable` | `uint256` | `uint256` |
| `paid` | `uint256` | `uint256` |

#### `IRoomManager.OmissionChallenge`

| Field | ABI type | Internal type |
|---|---|---|
| `challenger` | `address` | `address` |
| `openedAtBlock` | `uint64` | `uint64` |
| `depositInboxId` | `uint64` | `uint64` |
| `maximumBatchIndex` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `penalty` | `uint256` | `uint256` |
| `admissionId` | `uint64` | `uint64` |

#### `IRoomManager.Room`

| Field | ABI type | Internal type |
|---|---|---|
| `state` | `uint8` | `enum RoomTypes.RoomState` |
| `authorizationMode` | `uint8` | `enum RoomTypes.AuthorizationMode` |
| `coldTemplateId` | `bytes32` | `bytes32` |
| `proofProgramId` | `bytes32` | `bytes32` |
| `proofSystemVersion` | `bytes32` | `bytes32` |
| `policyHash` | `bytes32` | `bytes32` |
| `adapterPolicyRoot` | `bytes32` | `bytes32` |
| `importPublisher` | `address` | `address` |
| `verifier` | `address` | `address` |
| `verifierCodeHash` | `bytes32` | `bytes32` |
| `stateRoot` | `bytes32` | `bytes32` |
| `participantRoot` | `bytes32` | `bytes32` |
| `participantEpoch` | `uint64` | `uint64` |
| `participantCount` | `uint64` | `uint64` |
| `participantCapacity` | `uint64` | `uint64` |
| `approverRoot` | `bytes32` | `bytes32` |
| `approverEpoch` | `uint64` | `uint64` |
| `activeCount` | `uint64` | `uint64` |
| `batchIndex` | `uint64` | `uint64` |
| `l2BlockHeight` | `uint64` | `uint64` |
| `approverChangeCursor` | `uint64` | `uint64` |
| `nextApproverChangeId` | `uint64` | `uint64` |
| `inboxCursor` | `uint64` | `uint64` |
| `nextInboxId` | `uint64` | `uint64` |
| `admissionCursor` | `uint64` | `uint64` |
| `forcedCursor` | `uint64` | `uint64` |
| `nextForcedId` | `uint64` | `uint64` |
| `importCursor` | `uint64` | `uint64` |
| `nextImportId` | `uint64` | `uint64` |
| `outboxEpoch` | `uint64` | `uint64` |
| `minimumImportConfirmations` | `uint64` | `uint64` |
| `minimumDepositConfirmations` | `uint64` | `uint64` |
| `inactivityTimeout` | `uint64` | `uint64` |
| `lastVerifiedAt` | `uint64` | `uint64` |
| `closedAtBlock` | `uint64` | `uint64` |
| `maximumAdmissionWindow` | `uint64` | `uint64` |
| `bondEpoch` | `uint64` | `uint64` |
| `admissionSigner` | `address` | `address` |
| `minimumServiceBond` | `uint96` | `uint96` |
| `omissionPenalty` | `uint96` | `uint96` |
| `serviceBond` | `uint256` | `uint256` |
| `coldTemplateDataHash` | `bytes32` | `bytes32` |
| `lastAttestedAt` | `uint64` | `uint64` |
| `closedByRecovery` | `bool` | `bool` |

#### `RoomTypes.AdmissionOutcome`

| Field | ABI type | Internal type |
|---|---|---|
| `admissionId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `status` | `uint8` | `enum RoomTypes.AdmissionStatus` |
| `l2BlockNumber` | `uint64` | `uint64` |
| `transactionIndex` | `uint32` | `uint32` |
| `reasonHash` | `bytes32` | `bytes32` |

#### `RoomTypes.AdmissionReceipt`

| Field | ABI type | Internal type |
|---|---|---|
| `admissionId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `depositInboxId` | `uint64` | `uint64` |
| `depositContentHash` | `bytes32` | `bytes32` |
| `deadlineBlock` | `uint64` | `uint64` |
| `maximumBatchIndex` | `uint64` | `uint64` |
| `bondEpoch` | `uint64` | `uint64` |
| `admissionFee` | `uint256` | `uint256` |
| `signature` | `bytes` | `bytes` |

#### `RoomTypes.AdmissionRecord`

| Field | ABI type | Internal type |
|---|---|---|
| `receipt` | `(uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes)` | `struct RoomTypes.AdmissionReceipt` |
| `outcome` | `(uint64,bytes32,uint8,uint64,uint32,bytes32)` | `struct RoomTypes.AdmissionOutcome` |

#### `RoomTypes.AggregateMember`

| Field | ABI type | Internal type |
|---|---|---|
| `roomId` | `uint64` | `uint64` |
| `submission` | `((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[])` | `struct RoomTypes.BatchSubmission` |
| `dataAvailability` | `(bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes)` | `struct RoomTypes.DataAvailabilityManifest` |

#### `RoomTypes.AggregateSubmission`

| Field | ABI type | Internal type |
|---|---|---|
| `members` | `(uint64,((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]),(bytes32,uint64,uint8,bytes32[],bytes[],bytes32[],bytes32[],bytes[],bytes,uint64,bytes))[]` | `struct RoomTypes.AggregateMember[]` |
| `aggregateSeal` | `bytes` | `bytes` |

#### `RoomTypes.ApproverApproval`

| Field | ABI type | Internal type |
|---|---|---|
| `index` | `uint64` | `uint64` |
| `joinedEpoch` | `uint64` | `uint64` |
| `nonce` | `uint64` | `uint64` |
| `deadline` | `uint64` | `uint64` |
| `member` | `address` | `address` |
| `proof` | `bytes32[]` | `bytes32[]` |
| `signature` | `bytes` | `bytes` |

#### `RoomTypes.ApproverChange`

| Field | ABI type | Internal type |
|---|---|---|
| `action` | `uint8` | `enum RoomTypes.ApproverAction` |
| `index` | `uint64` | `uint64` |
| `joinedEpoch` | `uint64` | `uint64` |
| `deadline` | `uint64` | `uint64` |
| `member` | `address` | `address` |
| `withdrawalCommitment` | `bytes32` | `bytes32` |
| `acceptanceSignature` | `bytes` | `bytes` |

#### `RoomTypes.AssetLiability`

| Field | ABI type | Internal type |
|---|---|---|
| `asset` | `address` | `address` |
| `pending` | `uint256` | `uint256` |
| `controlled` | `uint256` | `uint256` |
| `claimable` | `uint256` | `uint256` |
| `paid` | `uint256` | `uint256` |

#### `RoomTypes.BatchJournal`

| Field | ABI type | Internal type |
|---|---|---|
| `protocolVersion` | `uint256` | `uint256` |
| `deploymentDomain` | `bytes32` | `bytes32` |
| `roomId` | `uint64` | `uint64` |
| `authorizationMode` | `uint8` | `enum RoomTypes.AuthorizationMode` |
| `coldTemplateId` | `bytes32` | `bytes32` |
| `proofProgramId` | `bytes32` | `bytes32` |
| `proofSystemVersion` | `bytes32` | `bytes32` |
| `policyHash` | `bytes32` | `bytes32` |
| `batchIndex` | `uint64` | `uint64` |
| `startL2Block` | `uint64` | `uint64` |
| `endL2Block` | `uint64` | `uint64` |
| `preStateRoot` | `bytes32` | `bytes32` |
| `postStateRoot` | `bytes32` | `bytes32` |
| `batchDataHash` | `bytes32` | `bytes32` |
| `canonicalDataHash` | `bytes32` | `bytes32` |
| `preParticipantRoot` | `bytes32` | `bytes32` |
| `postParticipantRoot` | `bytes32` | `bytes32` |
| `preParticipantEpoch` | `uint64` | `uint64` |
| `postParticipantEpoch` | `uint64` | `uint64` |
| `preParticipantCount` | `uint64` | `uint64` |
| `postParticipantCount` | `uint64` | `uint64` |
| `participantCapacity` | `uint64` | `uint64` |
| `preApproverRoot` | `bytes32` | `bytes32` |
| `postApproverRoot` | `bytes32` | `bytes32` |
| `preApproverEpoch` | `uint64` | `uint64` |
| `postApproverEpoch` | `uint64` | `uint64` |
| `preActiveCount` | `uint64` | `uint64` |
| `postActiveCount` | `uint64` | `uint64` |
| `approverChangeCursorBefore` | `uint64` | `uint64` |
| `approverChangeCursorAfter` | `uint64` | `uint64` |
| `inboxCursorBefore` | `uint64` | `uint64` |
| `inboxCursorAfter` | `uint64` | `uint64` |
| `inboxRecordsHash` | `bytes32` | `bytes32` |
| `admissionCursorBefore` | `uint64` | `uint64` |
| `admissionCursorAfter` | `uint64` | `uint64` |
| `admissionRecordsHash` | `bytes32` | `bytes32` |
| `forcedCursorBefore` | `uint64` | `uint64` |
| `forcedCursorAfter` | `uint64` | `uint64` |
| `forcedOutcomesHash` | `bytes32` | `bytes32` |
| `importCursorBefore` | `uint64` | `uint64` |
| `importCursorAfter` | `uint64` | `uint64` |
| `importedL1Block` | `uint64` | `uint64` |
| `importedL1HeaderHash` | `bytes32` | `bytes32` |
| `importedL1StateRoot` | `bytes32` | `bytes32` |
| `importRoot` | `bytes32` | `bytes32` |
| `outboxEpoch` | `uint64` | `uint64` |
| `withdrawalRoot` | `bytes32` | `bytes32` |
| `preLiabilitiesHash` | `bytes32` | `bytes32` |
| `postLiabilitiesHash` | `bytes32` | `bytes32` |
| `approverChangesHash` | `bytes32` | `bytes32` |
| `l1InclusionDeadline` | `uint64` | `uint64` |
| `close` | `bool` | `bool` |

#### `RoomTypes.BatchSubmission`

| Field | ABI type | Internal type |
|---|---|---|
| `journal` | `(uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool)` | `struct RoomTypes.BatchJournal` |
| `seal` | `bytes` | `bytes` |
| `canonicalBatchData` | `bytes` | `bytes` |
| `approvals` | `(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[]` | `struct RoomTypes.ApproverApproval[]` |
| `approverChanges` | `(uint8,uint64,uint64,uint64,address,bytes32,bytes)[]` | `struct RoomTypes.ApproverChange[]` |
| `admissions` | `((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[]` | `struct RoomTypes.AdmissionRecord[]` |
| `forcedOutcomes` | `(uint64,bytes32,uint8,uint64,uint32,bytes32)[]` | `struct RoomTypes.ForcedOutcome[]` |
| `liabilities` | `(address,uint256,uint256,uint256,uint256)[]` | `struct RoomTypes.AssetLiability[]` |

#### `RoomTypes.DataAvailabilityConfig`

| Field | ABI type | Internal type |
|---|---|---|
| `policy` | `uint8` | `enum RoomTypes.DataAvailabilityPolicy` |
| `fallbackAuthority` | `address` | `address` |
| `equivalenceProgramId` | `bytes32` | `bytes32` |

#### `RoomTypes.DataAvailabilityManifest`

| Field | ABI type | Internal type |
|---|---|---|
| `canonicalDataHash` | `bytes32` | `bytes32` |
| `canonicalDataLength` | `uint64` | `uint64` |
| `blobStartIndex` | `uint8` | `uint8` |
| `blobVersionedHashes` | `bytes32[]` | `bytes32[]` |
| `commitments` | `bytes[]` | `bytes[]` |
| `evaluationPoints` | `bytes32[]` | `bytes32[]` |
| `evaluations` | `bytes32[]` | `bytes32[]` |
| `kzgProofs` | `bytes[]` | `bytes[]` |
| `equivalenceSeal` | `bytes` | `bytes` |
| `fallbackDeadlineBlock` | `uint64` | `uint64` |
| `fallbackSignature` | `bytes` | `bytes` |

#### `RoomTypes.ForcedOutcome`

| Field | ABI type | Internal type |
|---|---|---|
| `forcedId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `status` | `uint8` | `enum RoomTypes.AdmissionStatus` |
| `l2BlockNumber` | `uint64` | `uint64` |
| `transactionIndex` | `uint32` | `uint32` |
| `reasonHash` | `bytes32` | `bytes32` |

#### `RoomTypes.Withdrawal`

| Field | ABI type | Internal type |
|---|---|---|
| `index` | `uint64` | `uint64` |
| `approverEpoch` | `uint64` | `uint64` |
| `recipient` | `address` | `address` |
| `asset` | `address` | `address` |
| `amount` | `uint256` | `uint256` |


## RoomManagerValidationFacet

### Functions

| Signature | Selector | Mutability | Named inputs | Named outputs | Owner NatSpec |
|---|---|---|---|---|---|
| `CANONICAL_BYTES_PER_BLOB()` | `0x03f50906` | `view` | `—` | `uint256 <unnamed>` | — |
| `GRACE_BLOCKS()` | `0x418fc67b` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_ACTIVE_APPROVERS()` | `0x3964be7d` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_AGGREGATE_ROOMS()` | `0xe4a9fe8a` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_APPROVER_PROOF_DEPTH()` | `0x34233b33` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_BLOBS_PER_BATCH()` | `0xd6160d39` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_COLD_TEMPLATE_DATA_BYTES()` | `0x3c67ed66` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_FORCED_TRANSACTION_BYTES()` | `0x65e471f2` | `view` | `—` | `uint256 <unnamed>` | — |
| `MAX_IMPORT_CONFIRMATIONS()` | `0xa72aea7a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_INBOX_ITEMS_PER_BATCH()` | `0xccc6d3eb` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PARTICIPANT_CAPACITY()` | `0x8b3af9b0` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_PROTOCOL_FEE_BPS()` | `0x6d947e4b` | `view` | `—` | `uint16 <unnamed>` | — |
| `MAX_WITHDRAWALS_PER_EPOCH()` | `0x4ee7b9cd` | `view` | `—` | `uint64 <unnamed>` | — |
| `MAX_WITHDRAWAL_PROOF_DEPTH()` | `0x1924b7ab` | `view` | `—` | `uint256 <unnamed>` | — |
| `MIN_ADMISSION_WINDOW()` | `0xffac207a` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_DEPOSIT_CONFIRMATIONS()` | `0x04b45763` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_IMPORT_CONFIRMATIONS()` | `0x35774972` | `view` | `—` | `uint64 <unnamed>` | — |
| `MIN_SERVICE_BOND_MULTIPLE()` | `0xfe15dcac` | `view` | `—` | `uint256 <unnamed>` | — |
| `RECOVERY_CLOSE_BOND_MULTIPLE()` | `0x7adfc8f8` | `view` | `—` | `uint64 <unnamed>` | — |
| `RECOVERY_CLOSE_CHALLENGE_MULTIPLE()` | `0xe7ab9737` | `view` | `—` | `uint64 <unnamed>` | — |
| `REPAIR_FEE_DIVISOR()` | `0x5445cbde` | `view` | `—` | `uint256 <unnamed>` | — |
| `REPAIR_WINDOW_BLOCKS()` | `0x1f903fe2` | `view` | `—` | `uint64 <unnamed>` | — |
| `selectors()` | `0x6e25b978` | `pure` | `—` | `bytes4[] value` | — |
| `validateBatch(uint64,((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]),bool,bool)` | `0x7b3512ef` | `nonpayable` | `uint64 roomId; ((uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool),bytes,bytes,(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[],(uint8,uint64,uint64,uint64,address,bytes32,bytes)[],((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[],(uint64,bytes32,uint8,uint64,uint32,bytes32)[],(address,uint256,uint256,uint256,uint256)[]) submission; bool requireApprovals; bool canonicalDataInCalldata` | `—` | — |

### Events

| Signature | Named/indexed fields | Anonymous | Owner NatSpec |
|---|---|---|---|
| `AdmissionOmissionChallenged(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `AdmissionReceiptDischarged(uint64,uint64,bytes32,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; uint256 cost` | `false` | — |
| `AdmissionRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `AggregateMemberOutcome(bytes32,uint8,uint64,uint64,bool,bytes4)` | `bytes32 aggregateHash indexed; uint8 memberIndex indexed; uint64 roomId indexed; uint64 batchIndex; bool applied; bytes4 failureSelector` | `false` | — |
| `ApproverChangeQueued(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 requestId indexed; bytes32 changeHash indexed` | `false` | — |
| `BatchAccepted(uint64,uint64,bytes32,bytes32,uint64,bool)` | `uint64 roomId indexed; uint64 batchIndex indexed; bytes32 postStateRoot indexed; bytes32 postApproverRoot; uint64 outboxEpoch; bool closed` | `false` | — |
| `ChallengePayoutClaimed(uint64,address,uint256)` | `uint64 roomId indexed; address payee indexed; uint256 amount` | `false` | — |
| `ColdTemplateDataPublished(uint64,bytes32,bytes)` | `uint64 roomId indexed; bytes32 dataHash indexed; bytes canonicalColdTemplateData` | `false` | — |
| `DataAvailabilityAccepted(uint64,uint64,uint8,bool,bool,bytes32)` | `uint64 roomId indexed; uint64 batchIndex indexed; uint8 configuredPolicy; bool usedBlob; bool usedAuthorizedFallback; bytes32 statementHash` | `false` | — |
| `DataAvailabilityConfigured(uint64,uint8,address,bytes32)` | `uint64 roomId indexed; uint8 policy; address fallbackAuthority indexed; bytes32 equivalenceProgramId indexed` | `false` | — |
| `DeadlineGracePurchased(uint64,bytes32,uint64,uint256)` | `uint64 roomId indexed; bytes32 journalHash indexed; uint64 graceDeadlineBlock; uint256 fee` | `false` | — |
| `DepositQueued(uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 inboxId indexed; address beneficiary indexed; address asset; uint256 amount` | `false` | — |
| `DepositRefunded(uint64,uint64,address)` | `uint64 roomId indexed; uint64 inboxId indexed; address depositor indexed` | `false` | — |
| `ForcedOutcomeRecorded(uint64,uint64,bytes32,uint8)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint8 status` | `false` | — |
| `ForcedTransactionQueued(uint64,uint64,bytes32,uint64,bytes)` | `uint64 roomId indexed; uint64 forcedId indexed; bytes32 transactionHash indexed; uint64 deadlineBlock; bytes rawSignedTransaction` | `false` | — |
| `L1StateInputPublished(uint64,uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 importId indexed; uint64 sourceBlock indexed; bytes32 importRoot` | `false` | — |
| `LivenessAttested(uint64,uint64)` | `uint64 roomId indexed; uint64 attestedAt` | `false` | — |
| `OmissionChallengeOpened(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 transactionHash indexed; address challenger; uint256 penalty` | `false` | — |
| `OmissionChallengeRepaired(uint64,uint64,bytes32,address,uint256)` | `uint64 roomId indexed; uint64 admissionId indexed; bytes32 receiptHash indexed; address challenger; uint256 repairFee` | `false` | — |
| `OmissionChallengeSettled(uint64,bytes32,address,uint256)` | `uint64 roomId indexed; bytes32 receiptHash indexed; address challenger indexed; uint256 penalty` | `false` | — |
| `ProtocolFeeAccrued(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeConfigured(uint16,address)` | `uint16 bps; address treasury` | `false` | — |
| `ProtocolFeeMadeClaimable(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeeReversed(uint64,address,uint64,uint256)` | `uint64 roomId indexed; address asset indexed; uint64 inboxId indexed; uint256 fee` | `false` | — |
| `ProtocolFeesClaimed(uint64,address,address,uint256)` | `uint64 roomId indexed; address asset indexed; address treasury indexed; uint256 amount` | `false` | — |
| `RecoveryBatchAccepted(uint64,uint64)` | `uint64 roomId indexed; uint64 batchIndex indexed` | `false` | — |
| `RoomClosedByRecovery(uint64,address)` | `uint64 roomId indexed; address closer indexed` | `false` | — |
| `RoomCreated(uint64,bytes32,bytes32,uint64,uint8,uint64)` | `uint64 roomId indexed; bytes32 coldTemplateId indexed; bytes32 initialApproverRoot indexed; uint64 activeApproverCount; uint8 authorizationMode; uint64 participantCapacity` | `false` | — |
| `RoomOwnershipAssigned(uint64,address,bytes32)` | `uint64 roomId indexed; address creator indexed; bytes32 managedAllocationId indexed` | `false` | — |
| `ServiceBondFunded(uint64,uint64,uint256)` | `uint64 roomId indexed; uint64 bondEpoch indexed; uint256 amount` | `false` | — |
| `ServiceBondWithdrawn(uint64,uint256)` | `uint64 roomId indexed; uint256 amount` | `false` | — |
| `WithdrawalClaimed(uint64,uint64,uint64,address,address,uint256)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; uint64 index indexed; address recipient; address asset; uint256 amount` | `false` | — |
| `WithdrawalRootPublished(uint64,uint64,bytes32)` | `uint64 roomId indexed; uint64 outboxEpoch indexed; bytes32 withdrawalRoot indexed` | `false` | — |

### Custom errors

| Signature | Named fields | Owner NatSpec |
|---|---|---|
| `BadAccounting()` | `—` | — |
| `BadAdmission()` | `—` | — |
| `BadAggregate()` | `—` | — |
| `BadApproval()` | `—` | — |
| `BadApprover()` | `—` | — |
| `BadForcedTransaction()` | `—` | — |
| `BadImport()` | `—` | — |
| `BadInput()` | `—` | — |
| `BadProof()` | `—` | — |
| `BadTemplate()` | `—` | — |
| `BadWithdrawal()` | `—` | — |
| `BondUnavailable()` | `—` | — |
| `ChallengeNotSettleable()` | `—` | — |
| `CloseNotReady()` | `—` | — |
| `DataAvailabilityUnavailable()` | `—` | — |
| `DeadlinePassed()` | `—` | — |
| `DepositTooRecent()` | `—` | — |
| `DischargeUnavailable()` | `—` | — |
| `GraceUnavailable()` | `—` | — |
| `NothingToClaim()` | `—` | — |
| `RecoveryNotReady()` | `—` | — |
| `Reentrant()` | `—` | — |
| `Unauthorized()` | `—` | — |
| `UnsupportedAsset()` | `—` | — |
| `WrongState()` | `—` | — |

### Struct and tuple layouts

#### `RoomTypes.AdmissionOutcome`

| Field | ABI type | Internal type |
|---|---|---|
| `admissionId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `status` | `uint8` | `enum RoomTypes.AdmissionStatus` |
| `l2BlockNumber` | `uint64` | `uint64` |
| `transactionIndex` | `uint32` | `uint32` |
| `reasonHash` | `bytes32` | `bytes32` |

#### `RoomTypes.AdmissionReceipt`

| Field | ABI type | Internal type |
|---|---|---|
| `admissionId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `depositInboxId` | `uint64` | `uint64` |
| `depositContentHash` | `bytes32` | `bytes32` |
| `deadlineBlock` | `uint64` | `uint64` |
| `maximumBatchIndex` | `uint64` | `uint64` |
| `bondEpoch` | `uint64` | `uint64` |
| `admissionFee` | `uint256` | `uint256` |
| `signature` | `bytes` | `bytes` |

#### `RoomTypes.AdmissionRecord`

| Field | ABI type | Internal type |
|---|---|---|
| `receipt` | `(uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes)` | `struct RoomTypes.AdmissionReceipt` |
| `outcome` | `(uint64,bytes32,uint8,uint64,uint32,bytes32)` | `struct RoomTypes.AdmissionOutcome` |

#### `RoomTypes.ApproverApproval`

| Field | ABI type | Internal type |
|---|---|---|
| `index` | `uint64` | `uint64` |
| `joinedEpoch` | `uint64` | `uint64` |
| `nonce` | `uint64` | `uint64` |
| `deadline` | `uint64` | `uint64` |
| `member` | `address` | `address` |
| `proof` | `bytes32[]` | `bytes32[]` |
| `signature` | `bytes` | `bytes` |

#### `RoomTypes.ApproverChange`

| Field | ABI type | Internal type |
|---|---|---|
| `action` | `uint8` | `enum RoomTypes.ApproverAction` |
| `index` | `uint64` | `uint64` |
| `joinedEpoch` | `uint64` | `uint64` |
| `deadline` | `uint64` | `uint64` |
| `member` | `address` | `address` |
| `withdrawalCommitment` | `bytes32` | `bytes32` |
| `acceptanceSignature` | `bytes` | `bytes` |

#### `RoomTypes.AssetLiability`

| Field | ABI type | Internal type |
|---|---|---|
| `asset` | `address` | `address` |
| `pending` | `uint256` | `uint256` |
| `controlled` | `uint256` | `uint256` |
| `claimable` | `uint256` | `uint256` |
| `paid` | `uint256` | `uint256` |

#### `RoomTypes.BatchJournal`

| Field | ABI type | Internal type |
|---|---|---|
| `protocolVersion` | `uint256` | `uint256` |
| `deploymentDomain` | `bytes32` | `bytes32` |
| `roomId` | `uint64` | `uint64` |
| `authorizationMode` | `uint8` | `enum RoomTypes.AuthorizationMode` |
| `coldTemplateId` | `bytes32` | `bytes32` |
| `proofProgramId` | `bytes32` | `bytes32` |
| `proofSystemVersion` | `bytes32` | `bytes32` |
| `policyHash` | `bytes32` | `bytes32` |
| `batchIndex` | `uint64` | `uint64` |
| `startL2Block` | `uint64` | `uint64` |
| `endL2Block` | `uint64` | `uint64` |
| `preStateRoot` | `bytes32` | `bytes32` |
| `postStateRoot` | `bytes32` | `bytes32` |
| `batchDataHash` | `bytes32` | `bytes32` |
| `canonicalDataHash` | `bytes32` | `bytes32` |
| `preParticipantRoot` | `bytes32` | `bytes32` |
| `postParticipantRoot` | `bytes32` | `bytes32` |
| `preParticipantEpoch` | `uint64` | `uint64` |
| `postParticipantEpoch` | `uint64` | `uint64` |
| `preParticipantCount` | `uint64` | `uint64` |
| `postParticipantCount` | `uint64` | `uint64` |
| `participantCapacity` | `uint64` | `uint64` |
| `preApproverRoot` | `bytes32` | `bytes32` |
| `postApproverRoot` | `bytes32` | `bytes32` |
| `preApproverEpoch` | `uint64` | `uint64` |
| `postApproverEpoch` | `uint64` | `uint64` |
| `preActiveCount` | `uint64` | `uint64` |
| `postActiveCount` | `uint64` | `uint64` |
| `approverChangeCursorBefore` | `uint64` | `uint64` |
| `approverChangeCursorAfter` | `uint64` | `uint64` |
| `inboxCursorBefore` | `uint64` | `uint64` |
| `inboxCursorAfter` | `uint64` | `uint64` |
| `inboxRecordsHash` | `bytes32` | `bytes32` |
| `admissionCursorBefore` | `uint64` | `uint64` |
| `admissionCursorAfter` | `uint64` | `uint64` |
| `admissionRecordsHash` | `bytes32` | `bytes32` |
| `forcedCursorBefore` | `uint64` | `uint64` |
| `forcedCursorAfter` | `uint64` | `uint64` |
| `forcedOutcomesHash` | `bytes32` | `bytes32` |
| `importCursorBefore` | `uint64` | `uint64` |
| `importCursorAfter` | `uint64` | `uint64` |
| `importedL1Block` | `uint64` | `uint64` |
| `importedL1HeaderHash` | `bytes32` | `bytes32` |
| `importedL1StateRoot` | `bytes32` | `bytes32` |
| `importRoot` | `bytes32` | `bytes32` |
| `outboxEpoch` | `uint64` | `uint64` |
| `withdrawalRoot` | `bytes32` | `bytes32` |
| `preLiabilitiesHash` | `bytes32` | `bytes32` |
| `postLiabilitiesHash` | `bytes32` | `bytes32` |
| `approverChangesHash` | `bytes32` | `bytes32` |
| `l1InclusionDeadline` | `uint64` | `uint64` |
| `close` | `bool` | `bool` |

#### `RoomTypes.BatchSubmission`

| Field | ABI type | Internal type |
|---|---|---|
| `journal` | `(uint256,bytes32,uint64,uint8,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,bytes32,bytes32,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,bytes32,uint64,uint64,uint64,bytes32,bytes32,bytes32,uint64,bytes32,bytes32,bytes32,bytes32,uint64,bool)` | `struct RoomTypes.BatchJournal` |
| `seal` | `bytes` | `bytes` |
| `canonicalBatchData` | `bytes` | `bytes` |
| `approvals` | `(uint64,uint64,uint64,uint64,address,bytes32[],bytes)[]` | `struct RoomTypes.ApproverApproval[]` |
| `approverChanges` | `(uint8,uint64,uint64,uint64,address,bytes32,bytes)[]` | `struct RoomTypes.ApproverChange[]` |
| `admissions` | `((uint64,bytes32,uint64,bytes32,uint64,uint64,uint64,uint256,bytes),(uint64,bytes32,uint8,uint64,uint32,bytes32))[]` | `struct RoomTypes.AdmissionRecord[]` |
| `forcedOutcomes` | `(uint64,bytes32,uint8,uint64,uint32,bytes32)[]` | `struct RoomTypes.ForcedOutcome[]` |
| `liabilities` | `(address,uint256,uint256,uint256,uint256)[]` | `struct RoomTypes.AssetLiability[]` |

#### `RoomTypes.ForcedOutcome`

| Field | ABI type | Internal type |
|---|---|---|
| `forcedId` | `uint64` | `uint64` |
| `transactionHash` | `bytes32` | `bytes32` |
| `status` | `uint8` | `enum RoomTypes.AdmissionStatus` |
| `l2BlockNumber` | `uint64` | `uint64` |
| `transactionIndex` | `uint32` | `uint32` |
| `reasonHash` | `bytes32` | `bytes32` |


## RoomPoolManager

### Functions

| Signature | Selector | Mutability | Named inputs | Named outputs | Owner NatSpec |
|---|---|---|---|---|---|
| `DEFAULT_ADMIN_ROLE()` | `0xa217fddf` | `view` | `—` | `bytes32 <unnamed>` | — |
| `FINALITY_ORACLE_ROLE()` | `0x54b104ce` | `view` | `—` | `bytes32 <unnamed>` | — |
| `MIN_HEARTBEAT_TIMEOUT_BLOCKS()` | `0x1cca4a82` | `view` | `—` | `uint64 <unnamed>` | — |
| `MONITOR_ROLE()` | `0x4d9b47e2` | `view` | `—` | `bytes32 <unnamed>` | — |
| `NODE_ADMIN_ROLE()` | `0x3ced4509` | `view` | `—` | `bytes32 <unnamed>` | — |
| `PAUSER_ROLE()` | `0xe63ab1e9` | `view` | `—` | `bytes32 <unnamed>` | — |
| `POOL_CONTROLLER_ROLE()` | `0x52314457` | `view` | `—` | `bytes32 <unnamed>` | — |
| `SPONSOR_ROLE()` | `0xc2d79444` | `view` | `—` | `bytes32 <unnamed>` | — |
| `TEMPLATE_ADMIN_ROLE()` | `0x1090c6dc` | `view` | `—` | `bytes32 <unnamed>` | — |
| `TREASURY_ROLE()` | `0xd11a57ec` | `view` | `—` | `bytes32 <unnamed>` | — |
| `UPGRADER_ROLE()` | `0xf72c0d8b` | `view` | `—` | `bytes32 <unnamed>` | — |
| `UPGRADE_INTERFACE_VERSION()` | `0xad3cb1cc` | `view` | `—` | `string <unnamed>` | — |
| `accessToken()` | `0xe243c5fb` | `view` | `—` | `address <unnamed>` | — |
| `allocationState(bytes32)` | `0x5d13c78c` | `view` | `bytes32 allocationId` | `(address,bytes32,bytes32,bytes32,uint8,uint64,uint64,uint64,uint64,uint64,uint128,uint256,uint256,uint256,uint64,address,bytes32,uint64) <unnamed>` | — |
| `allocations(bytes32)` | `0xcd4a5488` | `view` | `bytes32 allocationId` | `address user; bytes32 nodeId; bytes32 slotId; bytes32 presetId; uint8 status; uint64 startBlock; uint64 proofDeadlineBlock; uint64 deadlineBlocksFromStart; uint64 priceEpoch; uint64 roomId; uint128 runningPricePerBlock; uint256 treasuryCharge; uint256 fixedCharge; uint256 runningEscrow; uint64 lastSettledBlock; address payer; bytes32 renewedFrom; uint64 checkpointBatchIndex` | — |
| `assertEscrowSolvent()` | `0x4b4a0723` | `view` | `—` | `—` | — |
| `beginNodeDrain(bytes32)` | `0xd7ceb78e` | `nonpayable` | `bytes32 nodeId` | `—` | devdoc.details: The transition is controller-authorized and one-way. Clearing the pending hash and advancing the nonce invalidates every capacity confirmation prepared before the drain. Repeating the call while already draining is an idempotent no-op. userdoc.notice: Stops `nodeId` from accepting new reservations while its existing allocations finish or hand off. |
| `cancelColdPreparation(uint64)` | `0x6e79f402` | `nonpayable` | `uint64 requestId` | `—` | — |
| `claimServiceFees()` | `0x097b7e13` | `nonpayable` | `—` | `—` | — |
| `claimTreasuryFees()` | `0xd1ba24e7` | `nonpayable` | `—` | `—` | — |
| `claimableServiceFees(address)` | `0xd2285897` | `view` | `address serviceAccount` | `uint256 amount` | — |
| `coldRequestState(uint64)` | `0x9e3c29e2` | `view` | `uint64 requestId` | `(address,bytes32,bytes32,bytes32,bytes32,bytes32,uint8,uint64,uint256,address) <unnamed>` | — |
| `coldRequests(uint64)` | `0xf1bb54a7` | `view` | `uint64 coldRequestId` | `address user; bytes32 nodeId; bytes32 slotId; bytes32 presetId; bytes32 requestHash; bytes32 coldTemplateId; uint8 status; uint64 expiryBlock; uint256 fee; address payer` | — |
| `coldTemplates()` | `0x53ee54af` | `view` | `—` | `address <unnamed>` | — |
| `completeColdPreparation(uint64,bytes32)` | `0x03db4992` | `nonpayable` | `uint64 requestId; bytes32 coldTemplateId` | `—` | — |
| `configureHostingFacet(address)` | `0xc57b5f9a` | `nonpayable` | `address facet` | `—` | — |
| `configureSlot(bytes32,bytes32,bytes32,uint64,uint64,uint64,uint32)` | `0x31cf1f07` | `nonpayable` | `bytes32 nodeId; bytes32 slotId; bytes32 presetId; uint64 minDeadlineBlocks; uint64 maxDeadlineBlocks; uint64 localProofTargetSeconds; uint32 capacityCap` | `—` | — |
| `confirmCapacityProfile(bytes32,bytes32,bytes32[],uint32[])` | `0x386b875d` | `nonpayable` | `bytes32 nodeId; bytes32 profileHash; bytes32[] slotIds; uint32[] readySlots` | `—` | — |
| `disposeRoom(bytes32)` | `0xed97f11a` | `nonpayable` | `bytes32 allocationId` | `—` | — |
| `finalizedCheckpoints(uint64)` | `0xc00edc7e` | `view` | `uint64 roomId` | `uint64 batchIndex; bytes32 stateRoot; uint64 l1BlockNumber; bytes32 l1BlockHash; uint64 recordedAtBlock` | — |
| `getRoleAdmin(bytes32)` | `0x248a9ca3` | `view` | `bytes32 role` | `bytes32 <unnamed>` | devdoc.details: Returns the admin role that controls `role`. See {grantRole} and {revokeRole}. To change a role's admin, use {_setRoleAdmin}. |
| `grantRole(bytes32,address)` | `0x2f2ff15d` | `nonpayable` | `bytes32 role; address account` | `—` | devdoc.details: Grants `role` to `account`. If `account` had not been already granted `role`, emits a {RoleGranted} event. Requirements: - the caller must have ``role``'s admin role. May emit a {RoleGranted} event. |
| `hasRole(bytes32,address)` | `0x91d14854` | `view` | `bytes32 role; address account` | `bool <unnamed>` | devdoc.details: Returns `true` if `account` has been granted `role`. |
| `hostingFacet()` | `0xf5fbc3d8` | `view` | `—` | `address <unnamed>` | — |
| `hostingFacetCodeHash()` | `0x89a134f1` | `view` | `—` | `bytes32 <unnamed>` | — |
| `initialize(address,address,address,address,address,address,address)` | `0x35876476` | `nonpayable` | `address accessToken_; address roomManager_; address coldTemplates_; address treasury_; address admin; address controller; address guardian` | `—` | — |
| `markNodeStale(bytes32)` | `0x31071e98` | `nonpayable` | `bytes32 nodeId` | `—` | — |
| `nodeDelegates(bytes32,address)` | `0xb5287e16` | `view` | `bytes32 nodeId; address account` | `bool allowed` | — |
| `nodeState(bytes32)` | `0x9e18497c` | `view` | `bytes32 nodeId` | `(address,address,bytes32,bytes32,uint8,uint64,uint64,uint64,uint64,address,address,address) <unnamed>` | — |
| `nodes(bytes32)` | `0xd86e697d` | `view` | `bytes32 nodeId` | `address serviceAccount; address boundAccount; bytes32 metadataHash; bytes32 pendingProfileHash; uint8 status; uint64 heartbeatTimeoutBlocks; uint64 lastHealthyBlock; uint64 profileNonce; uint64 activeAllocations; address livenessAccount; address operationsAccount; address payoutAccount` | — |
| `pause()` | `0x8456cb59` | `nonpayable` | `—` | `—` | — |
| `paused()` | `0x5c975abb` | `view` | `—` | `bool <unnamed>` | devdoc.details: Returns true if the contract is paused, and false otherwise. |
| `presets(bytes32)` | `0x02b9e3ed` | `view` | `bytes32 presetId` | `bytes32 coldTemplateId; bytes32 policyHash; bool exists` | — |
| `prices(bytes32,bytes32)` | `0xbe986600` | `view` | `bytes32 nodeId; bytes32 slotId` | `uint64 epoch; uint64 validUntilBlock; uint128 accessPrice; uint128 coldPreparationPrice; uint128 pricePerDeadlineBlock; uint128 runningPricePerBlock` | — |
| `proxiableUUID()` | `0x52d1902d` | `view` | `—` | `bytes32 <unnamed>` | devdoc.details: Implementation of the ERC-1822 {proxiableUUID} function. This returns the storage slot used by the implementation. It is used to validate the implementation's compatibility when performing an upgrade. IMPORTANT: A proxy pointing at a proxiable contract should not be considered proxiable itself, because this risks bricking a proxy that upgrades to it, by delegating to itself until out of gas. Thus it is critical that this function revert if invoked through a proxy. This is guaranteed by the `notDelegated` modifier. |
| `publishPriceEpoch(bytes32,bytes32,uint64,uint128,uint128,uint128,uint128)` | `0xb38d3fd6` | `nonpayable` | `bytes32 nodeId; bytes32 slotId; uint64 validUntilBlock; uint128 accessPrice; uint128 coldPreparationPrice; uint128 pricePerDeadlineBlock; uint128 runningPricePerBlock` | `—` | — |
| `quarantineNode(bytes32)` | `0xa4588ca0` | `nonpayable` | `bytes32 nodeId` | `—` | — |
| `quote(bytes32,bytes32,uint64,uint64)` | `0xc923a152` | `view` | `bytes32 nodeId; bytes32 slotId; uint64 deadlineBlocksFromStart; uint64 priceEpoch` | `uint256 fixedCharge; uint256 runningEscrow; uint256 totalCharge` | — |
| `registerNode(bytes32,address,address,bytes32,uint64)` | `0x7dbd304a` | `nonpayable` | `bytes32 nodeId; address serviceAccount; address boundAccount; bytes32 metadataHash; uint64 heartbeatTimeoutBlocks` | `—` | — |
| `registerPreset(bytes32,bytes32,bytes32)` | `0x10cdb0fa` | `nonpayable` | `bytes32 presetId; bytes32 coldTemplateId; bytes32 policyHash` | `—` | — |
| `renounceRole(bytes32,address)` | `0x36568abe` | `nonpayable` | `bytes32 role; address callerConfirmation` | `—` | devdoc.details: Revokes `role` from the calling account. Roles are often managed via {grantRole} and {revokeRole}: this function's purpose is to provide a mechanism for accounts to lose their privileges if they are compromised (such as when a trusted device is misplaced). If the calling account had been revoked `role`, emits a {RoleRevoked} event. Requirements: - the caller must be `callerConfirmation`. May emit a {RoleRevoked} event. |
| `reportNodeHeartbeat(bytes32,bytes32)` | `0x7cd0e630` | `nonpayable` | `bytes32 nodeId; bytes32 profileHash` | `—` | — |
| `requestCapacityProfile(bytes32,bytes32)` | `0x7eae52d7` | `nonpayable` | `bytes32 nodeId; bytes32 profileHash` | `—` | — |
| `requestColdPreparationWithPermit(bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint256,(uint256,uint256,uint8,bytes32,bytes32))` | `0xa53c5369` | `nonpayable` | `bytes32 nodeId; bytes32 slotId; bytes32 presetId; bytes32 requestHash; uint64 expiryBlock; uint64 priceEpoch; uint256 maxCharge; (uint256,uint256,uint8,bytes32,bytes32) permit` | `uint64 requestId` | — |
| `reserveAndStartWithPermit((bytes32,bytes32,bytes32,uint64,uint64,uint256),((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]),(uint256,uint256,uint8,bytes32,bytes32))` | `0x6fcca7d5` | `nonpayable` | `(bytes32,bytes32,bytes32,uint64,uint64,uint256) <unnamed>; ((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]) <unnamed>; (uint256,uint256,uint8,bytes32,bytes32) <unnamed>` | `bytes32 <unnamed>; uint64 <unnamed>` | — |
| `reserveRoomWithPermit((bytes32,bytes32,bytes32,uint64,uint64,uint256),(uint256,uint256,uint8,bytes32,bytes32))` | `0x4615435f` | `nonpayable` | `(bytes32,bytes32,bytes32,uint64,uint64,uint256) <unnamed>; (uint256,uint256,uint8,bytes32,bytes32) <unnamed>` | `bytes32 <unnamed>` | — |
| `retireNode(bytes32)` | `0x13ca0607` | `nonpayable` | `bytes32 nodeId` | `—` | devdoc.details: Retirement is restricted to the node administrator. A node must have no live reservations/rooms and no confirmable capacity profile. Repeating a completed retirement is idempotent. userdoc.notice: Irreversibly retires a fully drained node. |
| `revokeRole(bytes32,address)` | `0xd547741f` | `nonpayable` | `bytes32 role; address account` | `—` | devdoc.details: Revokes `role` from `account`. If `account` had been granted `role`, emits a {RoleRevoked} event. Requirements: - the caller must have ``role``'s admin role. May emit a {RoleRevoked} event. |
| `roomManager()` | `0x02d13871` | `view` | `—` | `address <unnamed>` | — |
| `setNodeDelegate(bytes32,address,bool)` | `0x96efc52c` | `nonpayable` | `bytes32 nodeId; address account; bool allowed` | `—` | — |
| `settleRunningFees(bytes32)` | `0x8024c080` | `nonpayable` | `bytes32 allocationId` | `uint256 serviceEarned` | — |
| `slotState(bytes32,bytes32)` | `0xd9baffa2` | `view` | `bytes32 nodeId; bytes32 slotId` | `(bytes32,uint64,uint64,uint64,uint32,uint32,bool) <unnamed>` | — |
| `slots(bytes32,bytes32)` | `0x3f86192c` | `view` | `bytes32 nodeId; bytes32 slotId` | `bytes32 presetId; uint64 minDeadlineBlocks; uint64 maxDeadlineBlocks; uint64 localProofTargetSeconds; uint32 capacityCap; uint32 readySlots; bool exists` | — |
| `startReservedRoom(bytes32,((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]))` | `0x1c1e500a` | `nonpayable` | `bytes32 <unnamed>; ((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]) <unnamed>` | `uint64 <unnamed>` | — |
| `supportsInterface(bytes4)` | `0x01ffc9a7` | `view` | `bytes4 interfaceId` | `bool <unnamed>` | devdoc.details: Returns true if this contract implements the interface defined by `interfaceId`. See the corresponding https://eips.ethereum.org/EIPS/eip-165#how-interfaces-are-identified[ERC section] to learn more about how these ids are created. This function call must use less than 30 000 gas. |
| `topUpRunningCredit(bytes32,uint256,(uint256,uint256,uint8,bytes32,bytes32))` | `0x1b3f3e3c` | `nonpayable` | `bytes32 allocationId; uint256 amount; (uint256,uint256,uint8,bytes32,bytes32) permit` | `—` | — |
| `totalServiceClaimable()` | `0x6bb22f89` | `view` | `—` | `uint256 <unnamed>` | — |
| `totalTreasuryClaimable()` | `0xbef73389` | `view` | `—` | `uint256 <unnamed>` | — |
| `totalUserEscrow()` | `0xf8b5d764` | `view` | `—` | `uint256 <unnamed>` | — |
| `treasury()` | `0x61d027b3` | `view` | `—` | `address <unnamed>` | — |
| `unpause()` | `0x3f4ba83a` | `nonpayable` | `—` | `—` | — |
| `upgradeToAndCall(address,bytes)` | `0x4f1ef286` | `payable` | `address newImplementation; bytes data` | `—` | devdoc.details: Upgrade the implementation of the proxy to `newImplementation`, and subsequently execute the function call encoded in `data`. Calls {_authorizeUpgrade}. Emits an {Upgraded} event. |

### Events

| Signature | Named/indexed fields | Anonymous | Owner NatSpec |
|---|---|---|---|
| `AllocationDisposed(bytes32,uint64,uint256,uint256)` | `bytes32 allocationId indexed; uint64 roomId indexed; uint256 serviceEarned; uint256 refunded` | `false` | — |
| `AllocationRenewed(bytes32,bytes32,uint64,uint256,uint256,uint64)` | `bytes32 previousAllocationId indexed; bytes32 newAllocationId indexed; uint64 roomId indexed; uint256 oldEscrowRefund; uint256 newTokenCharge; uint64 proofDeadlineBlock` | `false` | — |
| `AllocationReserved(bytes32,address,bytes32,bytes32,uint64,uint256)` | `bytes32 allocationId indexed; address user indexed; bytes32 nodeId indexed; bytes32 slotId; uint64 deadlineBlocksFromStart; uint256 tokenCharge` | `false` | — |
| `AllocationUsed(bytes32,uint64,uint64,uint64)` | `bytes32 allocationId indexed; uint64 roomId indexed; uint64 startBlock; uint64 proofDeadlineBlock` | `false` | — |
| `CapacityProfileConfirmed(bytes32,bytes32,uint64)` | `bytes32 nodeId indexed; bytes32 profileHash indexed; uint64 profileNonce` | `false` | — |
| `CapacityProfileRequested(bytes32,bytes32,uint64)` | `bytes32 nodeId indexed; bytes32 profileHash indexed; uint64 profileNonce` | `false` | — |
| `ColdPreparationCancelled(uint64,uint256)` | `uint64 requestId indexed; uint256 refund` | `false` | — |
| `ColdPreparationCompleted(uint64,bytes32,uint256)` | `uint64 requestId indexed; bytes32 coldTemplateId indexed; uint256 fee` | `false` | — |
| `ColdPreparationRequested(uint64,address,bytes32,bytes32,bytes32,uint64)` | `uint64 requestId indexed; address user indexed; bytes32 requestHash indexed; bytes32 nodeId; bytes32 presetId; uint64 expiryBlock` | `false` | — |
| `FinalizedCheckpointRecorded(uint64,uint64,bytes32,uint64,bytes32)` | `uint64 roomId indexed; uint64 batchIndex indexed; bytes32 stateRoot indexed; uint64 l1BlockNumber; bytes32 l1BlockHash` | `false` | — |
| `HostingFacetConfigured(address,bytes32)` | `address facet indexed; bytes32 codeHash indexed` | `false` | — |
| `Initialized(uint64)` | `uint64 version` | `false` | — |
| `NodeAuthoritiesConfigured(bytes32,address,address,address)` | `bytes32 nodeId indexed; address livenessAccount indexed; address operationsAccount indexed; address payoutAccount` | `false` | — |
| `NodeDrainStarted(bytes32,uint64,bytes32,uint64)` | `bytes32 nodeId indexed; uint64 activeAllocations; bytes32 cancelledProfileHash; uint64 profileNonce` | `false` | — |
| `NodeRegistered(bytes32,address,address,uint64)` | `bytes32 nodeId indexed; address serviceAccount indexed; address boundAccount indexed; uint64 heartbeatTimeoutBlocks` | `false` | — |
| `NodeRetired(bytes32,uint64)` | `bytes32 nodeId indexed; uint64 retiredAtBlock` | `false` | — |
| `NodeStatusChanged(bytes32,uint8,uint64)` | `bytes32 nodeId indexed; uint8 status; uint64 observedBlock` | `false` | — |
| `Paused(address)` | `address account` | `false` | — |
| `PriceEpochPublished(bytes32,bytes32,uint64,uint64)` | `bytes32 nodeId indexed; bytes32 slotId indexed; uint64 epoch indexed; uint64 validUntilBlock` | `false` | — |
| `RoleAdminChanged(bytes32,bytes32,bytes32)` | `bytes32 role indexed; bytes32 previousAdminRole indexed; bytes32 newAdminRole indexed` | `false` | — |
| `RoleGranted(bytes32,address,address)` | `bytes32 role indexed; address account indexed; address sender indexed` | `false` | — |
| `RoleRevoked(bytes32,address,address)` | `bytes32 role indexed; address account indexed; address sender indexed` | `false` | — |
| `RunningCreditAdded(bytes32,uint256)` | `bytes32 allocationId indexed; uint256 amount` | `false` | — |
| `RunningFeesSettled(bytes32,uint64,uint256)` | `bytes32 allocationId indexed; uint64 throughBlock; uint256 serviceEarned` | `false` | — |
| `ServiceFeesClaimed(address,uint256)` | `address serviceAccount indexed; uint256 amount` | `false` | — |
| `SlotConfigured(bytes32,bytes32,bytes32,uint64,uint64,uint32)` | `bytes32 nodeId indexed; bytes32 slotId indexed; bytes32 presetId indexed; uint64 minDeadlineBlocks; uint64 maxDeadlineBlocks; uint32 capacityCap` | `false` | — |
| `SponsoredEscrowFunded(address,address,bytes32,uint256)` | `address payer indexed; address beneficiary indexed; bytes32 escrowReference indexed; uint256 amount` | `false` | — |
| `TreasuryFeesClaimed(address,uint256)` | `address treasury indexed; uint256 amount` | `false` | — |
| `Unpaused(address)` | `address account` | `false` | — |
| `Upgraded(address)` | `address implementation indexed` | `false` | — |

### Custom errors

| Signature | Named fields | Owner NatSpec |
|---|---|---|
| `AccessControlBadConfirmation()` | `—` | — |
| `AccessControlUnauthorizedAccount(address,bytes32)` | `address account; bytes32 neededRole` | — |
| `AddressEmptyCode(address)` | `address target` | — |
| `BadInput()` | `—` | — |
| `CapacityUnavailable()` | `—` | — |
| `DeadlineOutOfRange()` | `—` | — |
| `ERC1967InvalidImplementation(address)` | `address implementation` | — |
| `ERC1967NonPayable()` | `—` | — |
| `EnforcedPause()` | `—` | — |
| `EscrowInsolvent()` | `—` | — |
| `ExpectedPause()` | `—` | — |
| `FailedCall()` | `—` | — |
| `InvalidInitialization()` | `—` | — |
| `NotInitializing()` | `—` | — |
| `Reentrant()` | `—` | — |
| `SafeERC20FailedOperation(address)` | `address token` | — |
| `StalePrice()` | `—` | — |
| `UUPSUnauthorizedCallContext()` | `—` | — |
| `UUPSUnsupportedProxiableUUID(bytes32)` | `bytes32 slot` | — |
| `Unauthorized()` | `—` | — |
| `UnsafeNodeRetirement(uint64,bytes32)` | `uint64 activeAllocations; bytes32 pendingProfileHash` | — |
| `WrongState()` | `—` | — |

### Struct and tuple layouts

#### `RoomPoolStorage.Allocation`

| Field | ABI type | Internal type |
|---|---|---|
| `user` | `address` | `address` |
| `nodeId` | `bytes32` | `bytes32` |
| `slotId` | `bytes32` | `bytes32` |
| `presetId` | `bytes32` | `bytes32` |
| `status` | `uint8` | `enum RoomPoolStorage.AllocationStatus` |
| `startBlock` | `uint64` | `uint64` |
| `proofDeadlineBlock` | `uint64` | `uint64` |
| `deadlineBlocksFromStart` | `uint64` | `uint64` |
| `priceEpoch` | `uint64` | `uint64` |
| `roomId` | `uint64` | `uint64` |
| `runningPricePerBlock` | `uint128` | `uint128` |
| `treasuryCharge` | `uint256` | `uint256` |
| `fixedCharge` | `uint256` | `uint256` |
| `runningEscrow` | `uint256` | `uint256` |
| `lastSettledBlock` | `uint64` | `uint64` |
| `payer` | `address` | `address` |
| `renewedFrom` | `bytes32` | `bytes32` |
| `checkpointBatchIndex` | `uint64` | `uint64` |

#### `RoomPoolStorage.ColdPreparationRequest`

| Field | ABI type | Internal type |
|---|---|---|
| `user` | `address` | `address` |
| `nodeId` | `bytes32` | `bytes32` |
| `slotId` | `bytes32` | `bytes32` |
| `presetId` | `bytes32` | `bytes32` |
| `requestHash` | `bytes32` | `bytes32` |
| `coldTemplateId` | `bytes32` | `bytes32` |
| `status` | `uint8` | `enum RoomPoolStorage.ColdRequestStatus` |
| `expiryBlock` | `uint64` | `uint64` |
| `fee` | `uint256` | `uint256` |
| `payer` | `address` | `address` |

#### `RoomPoolStorage.Node`

| Field | ABI type | Internal type |
|---|---|---|
| `serviceAccount` | `address` | `address` |
| `boundAccount` | `address` | `address` |
| `metadataHash` | `bytes32` | `bytes32` |
| `pendingProfileHash` | `bytes32` | `bytes32` |
| `status` | `uint8` | `enum RoomPoolStorage.NodeStatus` |
| `heartbeatTimeoutBlocks` | `uint64` | `uint64` |
| `lastHealthyBlock` | `uint64` | `uint64` |
| `profileNonce` | `uint64` | `uint64` |
| `activeAllocations` | `uint64` | `uint64` |
| `livenessAccount` | `address` | `address` |
| `operationsAccount` | `address` | `address` |
| `payoutAccount` | `address` | `address` |

#### `RoomPoolStorage.PermitData`

| Field | ABI type | Internal type |
|---|---|---|
| `value` | `uint256` | `uint256` |
| `deadline` | `uint256` | `uint256` |
| `v` | `uint8` | `uint8` |
| `r` | `bytes32` | `bytes32` |
| `s` | `bytes32` | `bytes32` |

#### `RoomPoolStorage.ReservationRequest`

| Field | ABI type | Internal type |
|---|---|---|
| `nodeId` | `bytes32` | `bytes32` |
| `slotId` | `bytes32` | `bytes32` |
| `presetId` | `bytes32` | `bytes32` |
| `deadlineBlocksFromStart` | `uint64` | `uint64` |
| `priceEpoch` | `uint64` | `uint64` |
| `maxTokenCharge` | `uint256` | `uint256` |

#### `RoomPoolStorage.RoomCreation`

| Field | ABI type | Internal type |
|---|---|---|
| `config` | `(bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64)` | `struct RoomTypes.RoomConfig` |
| `coldTemplateId` | `bytes32` | `bytes32` |
| `initialApproverRoot` | `bytes32` | `bytes32` |
| `initialActiveApproverCount` | `uint64` | `uint64` |
| `initialParticipantRoot` | `bytes32` | `bytes32` |
| `initialParticipantCount` | `uint64` | `uint64` |
| `canonicalColdTemplateData` | `bytes` | `bytes` |
| `supportedAssets` | `address[]` | `address[]` |

#### `RoomPoolStorage.SlotProfile`

| Field | ABI type | Internal type |
|---|---|---|
| `presetId` | `bytes32` | `bytes32` |
| `minDeadlineBlocks` | `uint64` | `uint64` |
| `maxDeadlineBlocks` | `uint64` | `uint64` |
| `localProofTargetSeconds` | `uint64` | `uint64` |
| `capacityCap` | `uint32` | `uint32` |
| `readySlots` | `uint32` | `uint32` |
| `exists` | `bool` | `bool` |

#### `RoomTypes.RoomConfig`

| Field | ABI type | Internal type |
|---|---|---|
| `policyHash` | `bytes32` | `bytes32` |
| `adapterPolicyRoot` | `bytes32` | `bytes32` |
| `importPublisher` | `address` | `address` |
| `minimumImportConfirmations` | `uint64` | `uint64` |
| `minimumDepositConfirmations` | `uint64` | `uint64` |
| `inactivityTimeout` | `uint64` | `uint64` |
| `authorizationMode` | `uint8` | `enum RoomTypes.AuthorizationMode` |
| `admissionSigner` | `address` | `address` |
| `maximumAdmissionWindow` | `uint64` | `uint64` |
| `minimumServiceBond` | `uint96` | `uint96` |
| `omissionPenalty` | `uint96` | `uint96` |
| `participantCapacity` | `uint64` | `uint64` |


## RoomPoolHostingFacet

### Functions

| Signature | Selector | Mutability | Named inputs | Named outputs | Owner NatSpec |
|---|---|---|---|---|---|
| `DEFAULT_ADMIN_ROLE()` | `0xa217fddf` | `view` | `—` | `bytes32 <unnamed>` | — |
| `FINALITY_ORACLE_ROLE()` | `0x54b104ce` | `view` | `—` | `bytes32 <unnamed>` | — |
| `MONITOR_ROLE()` | `0x4d9b47e2` | `view` | `—` | `bytes32 <unnamed>` | — |
| `NODE_ADMIN_ROLE()` | `0x3ced4509` | `view` | `—` | `bytes32 <unnamed>` | — |
| `PAUSER_ROLE()` | `0xe63ab1e9` | `view` | `—` | `bytes32 <unnamed>` | — |
| `POOL_CONTROLLER_ROLE()` | `0x52314457` | `view` | `—` | `bytes32 <unnamed>` | — |
| `SPONSOR_ROLE()` | `0xc2d79444` | `view` | `—` | `bytes32 <unnamed>` | — |
| `TEMPLATE_ADMIN_ROLE()` | `0x1090c6dc` | `view` | `—` | `bytes32 <unnamed>` | — |
| `TREASURY_ROLE()` | `0xd11a57ec` | `view` | `—` | `bytes32 <unnamed>` | — |
| `UPGRADER_ROLE()` | `0xf72c0d8b` | `view` | `—` | `bytes32 <unnamed>` | — |
| `accessToken()` | `0xe243c5fb` | `view` | `—` | `address <unnamed>` | — |
| `allocations(bytes32)` | `0xcd4a5488` | `view` | `bytes32 allocationId` | `address user; bytes32 nodeId; bytes32 slotId; bytes32 presetId; uint8 status; uint64 startBlock; uint64 proofDeadlineBlock; uint64 deadlineBlocksFromStart; uint64 priceEpoch; uint64 roomId; uint128 runningPricePerBlock; uint256 treasuryCharge; uint256 fixedCharge; uint256 runningEscrow; uint64 lastSettledBlock; address payer; bytes32 renewedFrom; uint64 checkpointBatchIndex` | — |
| `claimableServiceFees(address)` | `0xd2285897` | `view` | `address serviceAccount` | `uint256 amount` | — |
| `coldRequests(uint64)` | `0xf1bb54a7` | `view` | `uint64 coldRequestId` | `address user; bytes32 nodeId; bytes32 slotId; bytes32 presetId; bytes32 requestHash; bytes32 coldTemplateId; uint8 status; uint64 expiryBlock; uint256 fee; address payer` | — |
| `coldTemplates()` | `0x53ee54af` | `view` | `—` | `address <unnamed>` | — |
| `configureNodeAuthorities(bytes32,address,address,address)` | `0x29d57590` | `nonpayable` | `bytes32 nodeId; address livenessAccount; address operationsAccount; address payoutAccount` | `—` | — |
| `finalizedCheckpoints(uint64)` | `0xc00edc7e` | `view` | `uint64 roomId` | `uint64 batchIndex; bytes32 stateRoot; uint64 l1BlockNumber; bytes32 l1BlockHash; uint64 recordedAtBlock` | — |
| `getRoleAdmin(bytes32)` | `0x248a9ca3` | `view` | `bytes32 role` | `bytes32 <unnamed>` | devdoc.details: Returns the admin role that controls `role`. See {grantRole} and {revokeRole}. To change a role's admin, use {_setRoleAdmin}. |
| `grantRole(bytes32,address)` | `0x2f2ff15d` | `nonpayable` | `bytes32 role; address account` | `—` | devdoc.details: Grants `role` to `account`. If `account` had not been already granted `role`, emits a {RoleGranted} event. Requirements: - the caller must have ``role``'s admin role. May emit a {RoleGranted} event. |
| `hasRole(bytes32,address)` | `0x91d14854` | `view` | `bytes32 role; address account` | `bool <unnamed>` | devdoc.details: Returns `true` if `account` has been granted `role`. |
| `hostingFacet()` | `0xf5fbc3d8` | `view` | `—` | `address <unnamed>` | — |
| `hostingFacetCodeHash()` | `0x89a134f1` | `view` | `—` | `bytes32 <unnamed>` | — |
| `nodeDelegates(bytes32,address)` | `0xb5287e16` | `view` | `bytes32 nodeId; address account` | `bool allowed` | — |
| `nodes(bytes32)` | `0xd86e697d` | `view` | `bytes32 nodeId` | `address serviceAccount; address boundAccount; bytes32 metadataHash; bytes32 pendingProfileHash; uint8 status; uint64 heartbeatTimeoutBlocks; uint64 lastHealthyBlock; uint64 profileNonce; uint64 activeAllocations; address livenessAccount; address operationsAccount; address payoutAccount` | — |
| `paused()` | `0x5c975abb` | `view` | `—` | `bool <unnamed>` | devdoc.details: Returns true if the contract is paused, and false otherwise. |
| `presets(bytes32)` | `0x02b9e3ed` | `view` | `bytes32 presetId` | `bytes32 coldTemplateId; bytes32 policyHash; bool exists` | — |
| `prices(bytes32,bytes32)` | `0xbe986600` | `view` | `bytes32 nodeId; bytes32 slotId` | `uint64 epoch; uint64 validUntilBlock; uint128 accessPrice; uint128 coldPreparationPrice; uint128 pricePerDeadlineBlock; uint128 runningPricePerBlock` | — |
| `recordFinalizedCheckpoint(uint64,uint64,bytes32,uint64,bytes32)` | `0xe19bc67e` | `nonpayable` | `uint64 roomId; uint64 batchIndex; bytes32 stateRoot; uint64 l1BlockNumber; bytes32 l1BlockHash` | `—` | — |
| `renewRoomForWithPermit(bytes32,address,(bytes32,bytes32,bytes32,uint64,uint64,uint256),(uint256,uint256,uint8,bytes32,bytes32))` | `0xf180fe5d` | `nonpayable` | `bytes32 previousAllocationId; address beneficiary; (bytes32,bytes32,bytes32,uint64,uint64,uint256) request; (uint256,uint256,uint8,bytes32,bytes32) permit` | `bytes32 newAllocationId` | — |
| `renewRoomWithPermit(bytes32,(bytes32,bytes32,bytes32,uint64,uint64,uint256),(uint256,uint256,uint8,bytes32,bytes32))` | `0xe2d8342a` | `nonpayable` | `bytes32 previousAllocationId; (bytes32,bytes32,bytes32,uint64,uint64,uint256) request; (uint256,uint256,uint8,bytes32,bytes32) permit` | `bytes32 newAllocationId` | — |
| `renounceRole(bytes32,address)` | `0x36568abe` | `nonpayable` | `bytes32 role; address callerConfirmation` | `—` | devdoc.details: Revokes `role` from the calling account. Roles are often managed via {grantRole} and {revokeRole}: this function's purpose is to provide a mechanism for accounts to lose their privileges if they are compromised (such as when a trusted device is misplaced). If the calling account had been revoked `role`, emits a {RoleRevoked} event. Requirements: - the caller must be `callerConfirmation`. May emit a {RoleRevoked} event. |
| `requestColdPreparationForWithPermit(address,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint256,(uint256,uint256,uint8,bytes32,bytes32))` | `0x474c57b9` | `nonpayable` | `address beneficiary; bytes32 nodeId; bytes32 slotId; bytes32 presetId; bytes32 requestHash; uint64 expiryBlock; uint64 priceEpoch; uint256 maxCharge; (uint256,uint256,uint8,bytes32,bytes32) permit` | `uint64 requestId` | — |
| `reserveAndStartForWithDataAvailabilityWithPermit(address,(bytes32,bytes32,bytes32,uint64,uint64,uint256),((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]),(uint8,address,bytes32),(uint256,uint256,uint8,bytes32,bytes32))` | `0x827ac259` | `nonpayable` | `address beneficiary; (bytes32,bytes32,bytes32,uint64,uint64,uint256) request; ((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]) creation; (uint8,address,bytes32) dataAvailability; (uint256,uint256,uint8,bytes32,bytes32) permit` | `bytes32 allocationId; uint64 roomId` | — |
| `reserveAndStartForWithPermit(address,(bytes32,bytes32,bytes32,uint64,uint64,uint256),((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]),(uint256,uint256,uint8,bytes32,bytes32))` | `0xa13d88f3` | `nonpayable` | `address beneficiary; (bytes32,bytes32,bytes32,uint64,uint64,uint256) request; ((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]) creation; (uint256,uint256,uint8,bytes32,bytes32) permit` | `bytes32 allocationId; uint64 roomId` | — |
| `reserveAndStartWithDataAvailabilityWithPermit((bytes32,bytes32,bytes32,uint64,uint64,uint256),((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]),(uint8,address,bytes32),(uint256,uint256,uint8,bytes32,bytes32))` | `0x40fb9ce3` | `nonpayable` | `(bytes32,bytes32,bytes32,uint64,uint64,uint256) request; ((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]) creation; (uint8,address,bytes32) dataAvailability; (uint256,uint256,uint8,bytes32,bytes32) permit` | `bytes32 allocationId; uint64 roomId` | — |
| `reserveAndStartWithPermit((bytes32,bytes32,bytes32,uint64,uint64,uint256),((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]),(uint256,uint256,uint8,bytes32,bytes32))` | `0x6fcca7d5` | `nonpayable` | `(bytes32,bytes32,bytes32,uint64,uint64,uint256) request; ((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]) creation; (uint256,uint256,uint8,bytes32,bytes32) permit` | `bytes32 allocationId; uint64 roomId` | — |
| `reserveRoomForWithPermit(address,(bytes32,bytes32,bytes32,uint64,uint64,uint256),(uint256,uint256,uint8,bytes32,bytes32))` | `0xcad6516b` | `nonpayable` | `address beneficiary; (bytes32,bytes32,bytes32,uint64,uint64,uint256) request; (uint256,uint256,uint8,bytes32,bytes32) permit` | `bytes32 allocationId` | — |
| `reserveRoomWithPermit((bytes32,bytes32,bytes32,uint64,uint64,uint256),(uint256,uint256,uint8,bytes32,bytes32))` | `0x4615435f` | `nonpayable` | `(bytes32,bytes32,bytes32,uint64,uint64,uint256) request; (uint256,uint256,uint8,bytes32,bytes32) permit` | `bytes32 allocationId` | — |
| `revokeRole(bytes32,address)` | `0xd547741f` | `nonpayable` | `bytes32 role; address account` | `—` | devdoc.details: Revokes `role` from `account`. If `account` had been granted `role`, emits a {RoleRevoked} event. Requirements: - the caller must have ``role``'s admin role. May emit a {RoleRevoked} event. |
| `roomManager()` | `0x02d13871` | `view` | `—` | `address <unnamed>` | — |
| `slots(bytes32,bytes32)` | `0x3f86192c` | `view` | `bytes32 nodeId; bytes32 slotId` | `bytes32 presetId; uint64 minDeadlineBlocks; uint64 maxDeadlineBlocks; uint64 localProofTargetSeconds; uint32 capacityCap; uint32 readySlots; bool exists` | — |
| `startReservedRoom(bytes32,((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]))` | `0x1c1e500a` | `nonpayable` | `bytes32 allocationId; ((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]) creation` | `uint64 roomId` | — |
| `startReservedRoomWithDataAvailability(bytes32,((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]),(uint8,address,bytes32))` | `0x3d4058c1` | `nonpayable` | `bytes32 allocationId; ((bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64),bytes32,bytes32,uint64,bytes32,uint64,bytes,address[]) creation; (uint8,address,bytes32) dataAvailability` | `uint64 roomId` | — |
| `supportsInterface(bytes4)` | `0x01ffc9a7` | `view` | `bytes4 interfaceId` | `bool <unnamed>` | devdoc.details: Returns true if this contract implements the interface defined by `interfaceId`. See the corresponding https://eips.ethereum.org/EIPS/eip-165#how-interfaces-are-identified[ERC section] to learn more about how these ids are created. This function call must use less than 30 000 gas. |
| `totalServiceClaimable()` | `0x6bb22f89` | `view` | `—` | `uint256 <unnamed>` | — |
| `totalTreasuryClaimable()` | `0xbef73389` | `view` | `—` | `uint256 <unnamed>` | — |
| `totalUserEscrow()` | `0xf8b5d764` | `view` | `—` | `uint256 <unnamed>` | — |
| `treasury()` | `0x61d027b3` | `view` | `—` | `address <unnamed>` | — |

### Events

| Signature | Named/indexed fields | Anonymous | Owner NatSpec |
|---|---|---|---|
| `AllocationDisposed(bytes32,uint64,uint256,uint256)` | `bytes32 allocationId indexed; uint64 roomId indexed; uint256 serviceEarned; uint256 refunded` | `false` | — |
| `AllocationRenewed(bytes32,bytes32,uint64,uint256,uint256,uint64)` | `bytes32 previousAllocationId indexed; bytes32 newAllocationId indexed; uint64 roomId indexed; uint256 oldEscrowRefund; uint256 newTokenCharge; uint64 proofDeadlineBlock` | `false` | — |
| `AllocationReserved(bytes32,address,bytes32,bytes32,uint64,uint256)` | `bytes32 allocationId indexed; address user indexed; bytes32 nodeId indexed; bytes32 slotId; uint64 deadlineBlocksFromStart; uint256 tokenCharge` | `false` | — |
| `AllocationUsed(bytes32,uint64,uint64,uint64)` | `bytes32 allocationId indexed; uint64 roomId indexed; uint64 startBlock; uint64 proofDeadlineBlock` | `false` | — |
| `CapacityProfileConfirmed(bytes32,bytes32,uint64)` | `bytes32 nodeId indexed; bytes32 profileHash indexed; uint64 profileNonce` | `false` | — |
| `CapacityProfileRequested(bytes32,bytes32,uint64)` | `bytes32 nodeId indexed; bytes32 profileHash indexed; uint64 profileNonce` | `false` | — |
| `ColdPreparationCancelled(uint64,uint256)` | `uint64 requestId indexed; uint256 refund` | `false` | — |
| `ColdPreparationCompleted(uint64,bytes32,uint256)` | `uint64 requestId indexed; bytes32 coldTemplateId indexed; uint256 fee` | `false` | — |
| `ColdPreparationRequested(uint64,address,bytes32,bytes32,bytes32,uint64)` | `uint64 requestId indexed; address user indexed; bytes32 requestHash indexed; bytes32 nodeId; bytes32 presetId; uint64 expiryBlock` | `false` | — |
| `FinalizedCheckpointRecorded(uint64,uint64,bytes32,uint64,bytes32)` | `uint64 roomId indexed; uint64 batchIndex indexed; bytes32 stateRoot indexed; uint64 l1BlockNumber; bytes32 l1BlockHash` | `false` | — |
| `HostingFacetConfigured(address,bytes32)` | `address facet indexed; bytes32 codeHash indexed` | `false` | — |
| `Initialized(uint64)` | `uint64 version` | `false` | — |
| `NodeAuthoritiesConfigured(bytes32,address,address,address)` | `bytes32 nodeId indexed; address livenessAccount indexed; address operationsAccount indexed; address payoutAccount` | `false` | — |
| `NodeDrainStarted(bytes32,uint64,bytes32,uint64)` | `bytes32 nodeId indexed; uint64 activeAllocations; bytes32 cancelledProfileHash; uint64 profileNonce` | `false` | — |
| `NodeRegistered(bytes32,address,address,uint64)` | `bytes32 nodeId indexed; address serviceAccount indexed; address boundAccount indexed; uint64 heartbeatTimeoutBlocks` | `false` | — |
| `NodeRetired(bytes32,uint64)` | `bytes32 nodeId indexed; uint64 retiredAtBlock` | `false` | — |
| `NodeStatusChanged(bytes32,uint8,uint64)` | `bytes32 nodeId indexed; uint8 status; uint64 observedBlock` | `false` | — |
| `Paused(address)` | `address account` | `false` | — |
| `PriceEpochPublished(bytes32,bytes32,uint64,uint64)` | `bytes32 nodeId indexed; bytes32 slotId indexed; uint64 epoch indexed; uint64 validUntilBlock` | `false` | — |
| `RoleAdminChanged(bytes32,bytes32,bytes32)` | `bytes32 role indexed; bytes32 previousAdminRole indexed; bytes32 newAdminRole indexed` | `false` | — |
| `RoleGranted(bytes32,address,address)` | `bytes32 role indexed; address account indexed; address sender indexed` | `false` | — |
| `RoleRevoked(bytes32,address,address)` | `bytes32 role indexed; address account indexed; address sender indexed` | `false` | — |
| `RunningCreditAdded(bytes32,uint256)` | `bytes32 allocationId indexed; uint256 amount` | `false` | — |
| `RunningFeesSettled(bytes32,uint64,uint256)` | `bytes32 allocationId indexed; uint64 throughBlock; uint256 serviceEarned` | `false` | — |
| `ServiceFeesClaimed(address,uint256)` | `address serviceAccount indexed; uint256 amount` | `false` | — |
| `SlotConfigured(bytes32,bytes32,bytes32,uint64,uint64,uint32)` | `bytes32 nodeId indexed; bytes32 slotId indexed; bytes32 presetId indexed; uint64 minDeadlineBlocks; uint64 maxDeadlineBlocks; uint32 capacityCap` | `false` | — |
| `SponsoredEscrowFunded(address,address,bytes32,uint256)` | `address payer indexed; address beneficiary indexed; bytes32 escrowReference indexed; uint256 amount` | `false` | — |
| `TreasuryFeesClaimed(address,uint256)` | `address treasury indexed; uint256 amount` | `false` | — |
| `Unpaused(address)` | `address account` | `false` | — |

### Custom errors

| Signature | Named fields | Owner NatSpec |
|---|---|---|
| `AccessControlBadConfirmation()` | `—` | — |
| `AccessControlUnauthorizedAccount(address,bytes32)` | `address account; bytes32 neededRole` | — |
| `BadInput()` | `—` | — |
| `CapacityUnavailable()` | `—` | — |
| `DeadlineOutOfRange()` | `—` | — |
| `EnforcedPause()` | `—` | — |
| `EscrowInsolvent()` | `—` | — |
| `ExpectedPause()` | `—` | — |
| `InvalidInitialization()` | `—` | — |
| `NotInitializing()` | `—` | — |
| `Reentrant()` | `—` | — |
| `SafeERC20FailedOperation(address)` | `address token` | — |
| `StalePrice()` | `—` | — |
| `Unauthorized()` | `—` | — |
| `UnsafeNodeRetirement(uint64,bytes32)` | `uint64 activeAllocations; bytes32 pendingProfileHash` | — |
| `WrongState()` | `—` | — |

### Struct and tuple layouts

#### `RoomPoolStorage.PermitData`

| Field | ABI type | Internal type |
|---|---|---|
| `value` | `uint256` | `uint256` |
| `deadline` | `uint256` | `uint256` |
| `v` | `uint8` | `uint8` |
| `r` | `bytes32` | `bytes32` |
| `s` | `bytes32` | `bytes32` |

#### `RoomPoolStorage.ReservationRequest`

| Field | ABI type | Internal type |
|---|---|---|
| `nodeId` | `bytes32` | `bytes32` |
| `slotId` | `bytes32` | `bytes32` |
| `presetId` | `bytes32` | `bytes32` |
| `deadlineBlocksFromStart` | `uint64` | `uint64` |
| `priceEpoch` | `uint64` | `uint64` |
| `maxTokenCharge` | `uint256` | `uint256` |

#### `RoomPoolStorage.RoomCreation`

| Field | ABI type | Internal type |
|---|---|---|
| `config` | `(bytes32,bytes32,address,uint64,uint64,uint64,uint8,address,uint64,uint96,uint96,uint64)` | `struct RoomTypes.RoomConfig` |
| `coldTemplateId` | `bytes32` | `bytes32` |
| `initialApproverRoot` | `bytes32` | `bytes32` |
| `initialActiveApproverCount` | `uint64` | `uint64` |
| `initialParticipantRoot` | `bytes32` | `bytes32` |
| `initialParticipantCount` | `uint64` | `uint64` |
| `canonicalColdTemplateData` | `bytes` | `bytes` |
| `supportedAssets` | `address[]` | `address[]` |

#### `RoomTypes.DataAvailabilityConfig`

| Field | ABI type | Internal type |
|---|---|---|
| `policy` | `uint8` | `enum RoomTypes.DataAvailabilityPolicy` |
| `fallbackAuthority` | `address` | `address` |
| `equivalenceProgramId` | `bytes32` | `bytes32` |

#### `RoomTypes.RoomConfig`

| Field | ABI type | Internal type |
|---|---|---|
| `policyHash` | `bytes32` | `bytes32` |
| `adapterPolicyRoot` | `bytes32` | `bytes32` |
| `importPublisher` | `address` | `address` |
| `minimumImportConfirmations` | `uint64` | `uint64` |
| `minimumDepositConfirmations` | `uint64` | `uint64` |
| `inactivityTimeout` | `uint64` | `uint64` |
| `authorizationMode` | `uint8` | `enum RoomTypes.AuthorizationMode` |
| `admissionSigner` | `address` | `address` |
| `maximumAdmissionWindow` | `uint64` | `uint64` |
| `minimumServiceBond` | `uint96` | `uint96` |
| `omissionPenalty` | `uint96` | `uint96` |
| `participantCapacity` | `uint64` | `uint64` |


## RoomPoolNodeRegistry

### Functions

| Signature | Selector | Mutability | Named inputs | Named outputs | Owner NatSpec |
|---|---|---|---|---|---|
| `DEFAULT_ADMIN_ROLE()` | `0xa217fddf` | `view` | `—` | `bytes32 <unnamed>` | — |
| `FINALITY_ORACLE_ROLE()` | `0x54b104ce` | `view` | `—` | `bytes32 <unnamed>` | — |
| `MIN_HEARTBEAT_TIMEOUT_BLOCKS()` | `0x1cca4a82` | `view` | `—` | `uint64 <unnamed>` | — |
| `MONITOR_ROLE()` | `0x4d9b47e2` | `view` | `—` | `bytes32 <unnamed>` | — |
| `NODE_ADMIN_ROLE()` | `0x3ced4509` | `view` | `—` | `bytes32 <unnamed>` | — |
| `PAUSER_ROLE()` | `0xe63ab1e9` | `view` | `—` | `bytes32 <unnamed>` | — |
| `POOL_CONTROLLER_ROLE()` | `0x52314457` | `view` | `—` | `bytes32 <unnamed>` | — |
| `SPONSOR_ROLE()` | `0xc2d79444` | `view` | `—` | `bytes32 <unnamed>` | — |
| `TEMPLATE_ADMIN_ROLE()` | `0x1090c6dc` | `view` | `—` | `bytes32 <unnamed>` | — |
| `TREASURY_ROLE()` | `0xd11a57ec` | `view` | `—` | `bytes32 <unnamed>` | — |
| `UPGRADER_ROLE()` | `0xf72c0d8b` | `view` | `—` | `bytes32 <unnamed>` | — |
| `accessToken()` | `0xe243c5fb` | `view` | `—` | `address <unnamed>` | — |
| `allocations(bytes32)` | `0xcd4a5488` | `view` | `bytes32 allocationId` | `address user; bytes32 nodeId; bytes32 slotId; bytes32 presetId; uint8 status; uint64 startBlock; uint64 proofDeadlineBlock; uint64 deadlineBlocksFromStart; uint64 priceEpoch; uint64 roomId; uint128 runningPricePerBlock; uint256 treasuryCharge; uint256 fixedCharge; uint256 runningEscrow; uint64 lastSettledBlock; address payer; bytes32 renewedFrom; uint64 checkpointBatchIndex` | — |
| `beginNodeDrain(bytes32)` | `0xd7ceb78e` | `nonpayable` | `bytes32 nodeId` | `—` | devdoc.details: The transition is controller-authorized and one-way. Clearing the pending hash and advancing the nonce invalidates every capacity confirmation prepared before the drain. Repeating the call while already draining is an idempotent no-op. userdoc.notice: Stops `nodeId` from accepting new reservations while its existing allocations finish or hand off. |
| `claimableServiceFees(address)` | `0xd2285897` | `view` | `address serviceAccount` | `uint256 amount` | — |
| `coldRequests(uint64)` | `0xf1bb54a7` | `view` | `uint64 coldRequestId` | `address user; bytes32 nodeId; bytes32 slotId; bytes32 presetId; bytes32 requestHash; bytes32 coldTemplateId; uint8 status; uint64 expiryBlock; uint256 fee; address payer` | — |
| `coldTemplates()` | `0x53ee54af` | `view` | `—` | `address <unnamed>` | — |
| `configureSlot(bytes32,bytes32,bytes32,uint64,uint64,uint64,uint32)` | `0x31cf1f07` | `nonpayable` | `bytes32 nodeId; bytes32 slotId; bytes32 presetId; uint64 minDeadlineBlocks; uint64 maxDeadlineBlocks; uint64 localProofTargetSeconds; uint32 capacityCap` | `—` | — |
| `confirmCapacityProfile(bytes32,bytes32,bytes32[],uint32[])` | `0x386b875d` | `nonpayable` | `bytes32 nodeId; bytes32 profileHash; bytes32[] slotIds; uint32[] readySlots` | `—` | — |
| `finalizedCheckpoints(uint64)` | `0xc00edc7e` | `view` | `uint64 roomId` | `uint64 batchIndex; bytes32 stateRoot; uint64 l1BlockNumber; bytes32 l1BlockHash; uint64 recordedAtBlock` | — |
| `getRoleAdmin(bytes32)` | `0x248a9ca3` | `view` | `bytes32 role` | `bytes32 <unnamed>` | devdoc.details: Returns the admin role that controls `role`. See {grantRole} and {revokeRole}. To change a role's admin, use {_setRoleAdmin}. |
| `grantRole(bytes32,address)` | `0x2f2ff15d` | `nonpayable` | `bytes32 role; address account` | `—` | devdoc.details: Grants `role` to `account`. If `account` had not been already granted `role`, emits a {RoleGranted} event. Requirements: - the caller must have ``role``'s admin role. May emit a {RoleGranted} event. |
| `hasRole(bytes32,address)` | `0x91d14854` | `view` | `bytes32 role; address account` | `bool <unnamed>` | devdoc.details: Returns `true` if `account` has been granted `role`. |
| `hostingFacet()` | `0xf5fbc3d8` | `view` | `—` | `address <unnamed>` | — |
| `hostingFacetCodeHash()` | `0x89a134f1` | `view` | `—` | `bytes32 <unnamed>` | — |
| `markNodeStale(bytes32)` | `0x31071e98` | `nonpayable` | `bytes32 nodeId` | `—` | — |
| `nodeDelegates(bytes32,address)` | `0xb5287e16` | `view` | `bytes32 nodeId; address account` | `bool allowed` | — |
| `nodeState(bytes32)` | `0x9e18497c` | `view` | `bytes32 nodeId` | `(address,address,bytes32,bytes32,uint8,uint64,uint64,uint64,uint64,address,address,address) <unnamed>` | — |
| `nodes(bytes32)` | `0xd86e697d` | `view` | `bytes32 nodeId` | `address serviceAccount; address boundAccount; bytes32 metadataHash; bytes32 pendingProfileHash; uint8 status; uint64 heartbeatTimeoutBlocks; uint64 lastHealthyBlock; uint64 profileNonce; uint64 activeAllocations; address livenessAccount; address operationsAccount; address payoutAccount` | — |
| `presets(bytes32)` | `0x02b9e3ed` | `view` | `bytes32 presetId` | `bytes32 coldTemplateId; bytes32 policyHash; bool exists` | — |
| `prices(bytes32,bytes32)` | `0xbe986600` | `view` | `bytes32 nodeId; bytes32 slotId` | `uint64 epoch; uint64 validUntilBlock; uint128 accessPrice; uint128 coldPreparationPrice; uint128 pricePerDeadlineBlock; uint128 runningPricePerBlock` | — |
| `publishPriceEpoch(bytes32,bytes32,uint64,uint128,uint128,uint128,uint128)` | `0xb38d3fd6` | `nonpayable` | `bytes32 nodeId; bytes32 slotId; uint64 validUntilBlock; uint128 accessPrice; uint128 coldPreparationPrice; uint128 pricePerDeadlineBlock; uint128 runningPricePerBlock` | `—` | — |
| `quarantineNode(bytes32)` | `0xa4588ca0` | `nonpayable` | `bytes32 nodeId` | `—` | — |
| `quote(bytes32,bytes32,uint64,uint64)` | `0xc923a152` | `view` | `bytes32 nodeId; bytes32 slotId; uint64 deadlineBlocksFromStart; uint64 priceEpoch` | `uint256 fixedCharge; uint256 runningEscrow; uint256 totalCharge` | — |
| `registerNode(bytes32,address,address,bytes32,uint64)` | `0x7dbd304a` | `nonpayable` | `bytes32 nodeId; address serviceAccount; address boundAccount; bytes32 metadataHash; uint64 heartbeatTimeoutBlocks` | `—` | — |
| `registerPreset(bytes32,bytes32,bytes32)` | `0x10cdb0fa` | `nonpayable` | `bytes32 presetId; bytes32 coldTemplateId; bytes32 policyHash` | `—` | — |
| `renounceRole(bytes32,address)` | `0x36568abe` | `nonpayable` | `bytes32 role; address callerConfirmation` | `—` | devdoc.details: Revokes `role` from the calling account. Roles are often managed via {grantRole} and {revokeRole}: this function's purpose is to provide a mechanism for accounts to lose their privileges if they are compromised (such as when a trusted device is misplaced). If the calling account had been revoked `role`, emits a {RoleRevoked} event. Requirements: - the caller must be `callerConfirmation`. May emit a {RoleRevoked} event. |
| `reportNodeHeartbeat(bytes32,bytes32)` | `0x7cd0e630` | `nonpayable` | `bytes32 nodeId; bytes32 profileHash` | `—` | — |
| `requestCapacityProfile(bytes32,bytes32)` | `0x7eae52d7` | `nonpayable` | `bytes32 nodeId; bytes32 profileHash` | `—` | — |
| `retireNode(bytes32)` | `0x13ca0607` | `nonpayable` | `bytes32 nodeId` | `—` | devdoc.details: Retirement is restricted to the node administrator. A node must have no live reservations/rooms and no confirmable capacity profile. Repeating a completed retirement is idempotent. userdoc.notice: Irreversibly retires a fully drained node. |
| `revokeRole(bytes32,address)` | `0xd547741f` | `nonpayable` | `bytes32 role; address account` | `—` | devdoc.details: Revokes `role` from `account`. If `account` had been granted `role`, emits a {RoleRevoked} event. Requirements: - the caller must have ``role``'s admin role. May emit a {RoleRevoked} event. |
| `roomManager()` | `0x02d13871` | `view` | `—` | `address <unnamed>` | — |
| `setNodeDelegate(bytes32,address,bool)` | `0x96efc52c` | `nonpayable` | `bytes32 nodeId; address account; bool allowed` | `—` | — |
| `slotState(bytes32,bytes32)` | `0xd9baffa2` | `view` | `bytes32 nodeId; bytes32 slotId` | `(bytes32,uint64,uint64,uint64,uint32,uint32,bool) <unnamed>` | — |
| `slots(bytes32,bytes32)` | `0x3f86192c` | `view` | `bytes32 nodeId; bytes32 slotId` | `bytes32 presetId; uint64 minDeadlineBlocks; uint64 maxDeadlineBlocks; uint64 localProofTargetSeconds; uint32 capacityCap; uint32 readySlots; bool exists` | — |
| `supportsInterface(bytes4)` | `0x01ffc9a7` | `view` | `bytes4 interfaceId` | `bool <unnamed>` | devdoc.details: Returns true if this contract implements the interface defined by `interfaceId`. See the corresponding https://eips.ethereum.org/EIPS/eip-165#how-interfaces-are-identified[ERC section] to learn more about how these ids are created. This function call must use less than 30 000 gas. |
| `totalServiceClaimable()` | `0x6bb22f89` | `view` | `—` | `uint256 <unnamed>` | — |
| `totalTreasuryClaimable()` | `0xbef73389` | `view` | `—` | `uint256 <unnamed>` | — |
| `totalUserEscrow()` | `0xf8b5d764` | `view` | `—` | `uint256 <unnamed>` | — |
| `treasury()` | `0x61d027b3` | `view` | `—` | `address <unnamed>` | — |

### Events

| Signature | Named/indexed fields | Anonymous | Owner NatSpec |
|---|---|---|---|
| `AllocationDisposed(bytes32,uint64,uint256,uint256)` | `bytes32 allocationId indexed; uint64 roomId indexed; uint256 serviceEarned; uint256 refunded` | `false` | — |
| `AllocationRenewed(bytes32,bytes32,uint64,uint256,uint256,uint64)` | `bytes32 previousAllocationId indexed; bytes32 newAllocationId indexed; uint64 roomId indexed; uint256 oldEscrowRefund; uint256 newTokenCharge; uint64 proofDeadlineBlock` | `false` | — |
| `AllocationReserved(bytes32,address,bytes32,bytes32,uint64,uint256)` | `bytes32 allocationId indexed; address user indexed; bytes32 nodeId indexed; bytes32 slotId; uint64 deadlineBlocksFromStart; uint256 tokenCharge` | `false` | — |
| `AllocationUsed(bytes32,uint64,uint64,uint64)` | `bytes32 allocationId indexed; uint64 roomId indexed; uint64 startBlock; uint64 proofDeadlineBlock` | `false` | — |
| `CapacityProfileConfirmed(bytes32,bytes32,uint64)` | `bytes32 nodeId indexed; bytes32 profileHash indexed; uint64 profileNonce` | `false` | — |
| `CapacityProfileRequested(bytes32,bytes32,uint64)` | `bytes32 nodeId indexed; bytes32 profileHash indexed; uint64 profileNonce` | `false` | — |
| `ColdPreparationCancelled(uint64,uint256)` | `uint64 requestId indexed; uint256 refund` | `false` | — |
| `ColdPreparationCompleted(uint64,bytes32,uint256)` | `uint64 requestId indexed; bytes32 coldTemplateId indexed; uint256 fee` | `false` | — |
| `ColdPreparationRequested(uint64,address,bytes32,bytes32,bytes32,uint64)` | `uint64 requestId indexed; address user indexed; bytes32 requestHash indexed; bytes32 nodeId; bytes32 presetId; uint64 expiryBlock` | `false` | — |
| `FinalizedCheckpointRecorded(uint64,uint64,bytes32,uint64,bytes32)` | `uint64 roomId indexed; uint64 batchIndex indexed; bytes32 stateRoot indexed; uint64 l1BlockNumber; bytes32 l1BlockHash` | `false` | — |
| `HostingFacetConfigured(address,bytes32)` | `address facet indexed; bytes32 codeHash indexed` | `false` | — |
| `Initialized(uint64)` | `uint64 version` | `false` | — |
| `NodeAuthoritiesConfigured(bytes32,address,address,address)` | `bytes32 nodeId indexed; address livenessAccount indexed; address operationsAccount indexed; address payoutAccount` | `false` | — |
| `NodeDrainStarted(bytes32,uint64,bytes32,uint64)` | `bytes32 nodeId indexed; uint64 activeAllocations; bytes32 cancelledProfileHash; uint64 profileNonce` | `false` | — |
| `NodeRegistered(bytes32,address,address,uint64)` | `bytes32 nodeId indexed; address serviceAccount indexed; address boundAccount indexed; uint64 heartbeatTimeoutBlocks` | `false` | — |
| `NodeRetired(bytes32,uint64)` | `bytes32 nodeId indexed; uint64 retiredAtBlock` | `false` | — |
| `NodeStatusChanged(bytes32,uint8,uint64)` | `bytes32 nodeId indexed; uint8 status; uint64 observedBlock` | `false` | — |
| `PriceEpochPublished(bytes32,bytes32,uint64,uint64)` | `bytes32 nodeId indexed; bytes32 slotId indexed; uint64 epoch indexed; uint64 validUntilBlock` | `false` | — |
| `RoleAdminChanged(bytes32,bytes32,bytes32)` | `bytes32 role indexed; bytes32 previousAdminRole indexed; bytes32 newAdminRole indexed` | `false` | — |
| `RoleGranted(bytes32,address,address)` | `bytes32 role indexed; address account indexed; address sender indexed` | `false` | — |
| `RoleRevoked(bytes32,address,address)` | `bytes32 role indexed; address account indexed; address sender indexed` | `false` | — |
| `RunningCreditAdded(bytes32,uint256)` | `bytes32 allocationId indexed; uint256 amount` | `false` | — |
| `RunningFeesSettled(bytes32,uint64,uint256)` | `bytes32 allocationId indexed; uint64 throughBlock; uint256 serviceEarned` | `false` | — |
| `ServiceFeesClaimed(address,uint256)` | `address serviceAccount indexed; uint256 amount` | `false` | — |
| `SlotConfigured(bytes32,bytes32,bytes32,uint64,uint64,uint32)` | `bytes32 nodeId indexed; bytes32 slotId indexed; bytes32 presetId indexed; uint64 minDeadlineBlocks; uint64 maxDeadlineBlocks; uint32 capacityCap` | `false` | — |
| `SponsoredEscrowFunded(address,address,bytes32,uint256)` | `address payer indexed; address beneficiary indexed; bytes32 escrowReference indexed; uint256 amount` | `false` | — |
| `TreasuryFeesClaimed(address,uint256)` | `address treasury indexed; uint256 amount` | `false` | — |

### Custom errors

| Signature | Named fields | Owner NatSpec |
|---|---|---|
| `AccessControlBadConfirmation()` | `—` | — |
| `AccessControlUnauthorizedAccount(address,bytes32)` | `address account; bytes32 neededRole` | — |
| `BadInput()` | `—` | — |
| `CapacityUnavailable()` | `—` | — |
| `DeadlineOutOfRange()` | `—` | — |
| `EscrowInsolvent()` | `—` | — |
| `InvalidInitialization()` | `—` | — |
| `NotInitializing()` | `—` | — |
| `Reentrant()` | `—` | — |
| `StalePrice()` | `—` | — |
| `Unauthorized()` | `—` | — |
| `UnsafeNodeRetirement(uint64,bytes32)` | `uint64 activeAllocations; bytes32 pendingProfileHash` | — |
| `WrongState()` | `—` | — |

### Struct and tuple layouts

#### `RoomPoolStorage.Node`

| Field | ABI type | Internal type |
|---|---|---|
| `serviceAccount` | `address` | `address` |
| `boundAccount` | `address` | `address` |
| `metadataHash` | `bytes32` | `bytes32` |
| `pendingProfileHash` | `bytes32` | `bytes32` |
| `status` | `uint8` | `enum RoomPoolStorage.NodeStatus` |
| `heartbeatTimeoutBlocks` | `uint64` | `uint64` |
| `lastHealthyBlock` | `uint64` | `uint64` |
| `profileNonce` | `uint64` | `uint64` |
| `activeAllocations` | `uint64` | `uint64` |
| `livenessAccount` | `address` | `address` |
| `operationsAccount` | `address` | `address` |
| `payoutAccount` | `address` | `address` |

#### `RoomPoolStorage.SlotProfile`

| Field | ABI type | Internal type |
|---|---|---|
| `presetId` | `bytes32` | `bytes32` |
| `minDeadlineBlocks` | `uint64` | `uint64` |
| `maxDeadlineBlocks` | `uint64` | `uint64` |
| `localProofTargetSeconds` | `uint64` | `uint64` |
| `capacityCap` | `uint32` | `uint32` |
| `readySlots` | `uint32` | `uint32` |
| `exists` | `bool` | `bool` |


## Source hashes

- `web3-protocol/contracts/out/l1/RoomManager.sol/RoomManager.json` — `sha256:2b94c6661bad8112eb3a538cdd68aa96cdff698afc2d93a08136db2621d3b457`
- `web3-protocol/contracts/out/RoomManagerBatchFacet.sol/RoomManagerBatchFacet.json` — `sha256:a1b14a3573a7b6751e6289a228bcdc69cea05f3ca9774091b284f384ba8323cd`
- `web3-protocol/contracts/out/RoomManagerChallengeFacet.sol/RoomManagerChallengeFacet.json` — `sha256:4109d0c05cd3615dbce5d78c780ead30b76fb10cf3dea83caf216cc955258e19`
- `web3-protocol/contracts/out/RoomManagerHostingFacet.sol/RoomManagerHostingFacet.json` — `sha256:78519eeeac5e9d0ca85aec01c9c10ba8186719e5f991581ef75e4aeb321a2f68`
- `web3-protocol/contracts/out/RoomManagerIntakeFacet.sol/RoomManagerIntakeFacet.json` — `sha256:09c6fa651efd359630c1ee421ab2cb3bfc9b217015d75c3f3c13230607ea5c11`
- `web3-protocol/contracts/out/RoomManagerImportFacet.sol/RoomManagerImportFacet.json` — `sha256:c42e88065b71a659add6d8d74d6a8d01757941ea07e71fca3174048fa8c1acf5`
- `web3-protocol/contracts/out/RoomManagerObservationFacet.sol/RoomManagerObservationFacet.json` — `sha256:b5656c796f326ca5a56e1be9c0a5d78e6f1e92d5765d8e7757a6d22705bbb909`
- `web3-protocol/contracts/out/RoomManagerValidationFacet.sol/RoomManagerValidationFacet.json` — `sha256:9181769d3bd7edaa0d3bb02c916a741cb14383f11fee347f9f6945baa4a56ffa`
- `web3-protocol/contracts/out/RoomPoolManager.sol/RoomPoolManager.json` — `sha256:3b304db144926a2540a053b632ad4dd26c320225b2bfc00144af3bf1c2b779c9`
- `web3-protocol/contracts/out/RoomPoolHostingFacet.sol/RoomPoolHostingFacet.json` — `sha256:6dec143e526cbac3d2157e255a12f2815c5e804e5b9c9d4972faf6c271d80ae6`
- `web3-protocol/contracts/out/RoomPoolNodeRegistry.sol/RoomPoolNodeRegistry.json` — `sha256:043cdf44dc4062863f2542126ea6e06faf5669d6fdab78b1ae0dc8f60991a24f`
