/**
 * Mint a REAL cold-template proof and write everything the rig needs to
 * register it on chain.
 *
 * Registering a cold template is not an attestation: `ColdTemplateRegistry`
 * recomputes the statement from the template identity plus the genesis-package
 * hash and hands it to the registered verifier, so the seal has to come from an
 * actual proof over the actual genesis bytes. That is why the rig cannot use
 * `hex"01"` the way the drill scripts do, and why an always-accepting verifier
 * would make the whole exercise vacuous.
 *
 * The tracked fixture cannot be reused: `room-v5-real-proof.json` is
 * protocol_version 5, carries no `genesisDataHash`, and is the reason
 * `RoomManagerRealProof.t.sol` skips itself. This mints a fresh one.
 *
 * Only the COLD proof is minted. The existing bench helper also mints a room
 * proof, which costs GPU minutes the rig does not need at deploy time.
 *
 * The cold statement binds `templateId`, `initialStateRoot`, `policyHash`,
 * `proofProgramId`, `proofSystemVersion` and `genesisDataHash` - notably NOT
 * the deployment domain or a room id, so one cold proof is reusable across
 * rooms and deployments.
 *
 *   node mint-cold-template.mjs <out.json>
 */

import { writeFileSync } from 'node:fs'

const BASE = process.env.PROVER_URL ?? 'http://prover:8080'
const TOKEN = process.env.ZKDEAL_PROVER_TOKEN ?? ''
const out = process.argv[2] ?? '/data/cold-template.json'

async function post(path, body) {
  const headers = { 'content-type': 'application/json', accept: 'application/json' }
  if (TOKEN) headers.authorization = `Bearer ${TOKEN}`
  const response = await fetch(`${BASE}${path}`, { method: 'POST', headers, body: JSON.stringify(body) })
  const text = await response.text()
  if (!response.ok) throw new Error(`${path} -> HTTP ${response.status}: ${text.slice(0, 300)}`)
  return JSON.parse(text)
}

const MODE = process.env.SOAK_AUTHORIZATION_MODE ?? 'validity-only'

const prepare = {
  // ?? only guards null/undefined, and the caller passes an empty string when
  // the variable is unset upstream - which reaches the prover as an invalid
  // bytes32 rather than falling back.
  deploymentDomain: process.env.ZKDEAL_DEPLOYMENT_DOMAIN || `0x${'11'.repeat(32)}`,
  roomId: Number(process.env.SOAK_ROOM_ID ?? 1),
  l1ChainId: Number(process.env.SOAK_CHAIN_ID ?? 31337),
  l1InclusionDeadline: 1_000_000,
  // The policy hash the template commits to depends on the authorization mode,
  // and createRoom requires template.policyHash == config.policyHash. Preparing
  // in the mode the room will actually use is therefore not cosmetic - a
  // mismatch reverts BadTemplate.
  authorizationMode: MODE,
  // VALIDITY_ONLY forbids an approver set entirely - the intake facet requires
  // initialApproverRoot == 0 and initialActiveCount == 0 - so an active signer
  // count of 1 is rejected as "outside the protocol cap".
  activeSigners: MODE === 'validity-only' ? 0 : 1,
  participantCapacity: 128,
  registeredParticipants: 1,
  touchedParticipants: 1,
  touchedContracts: 1,
  residentAccounts: 2,
  residentMirrorVariables: 1,
  importedVariables: 0,
  workload: 'storage',
  stateCommitment: 'mpt',
}

const prepared = await post('/v5/rooms/prepare', prepare)
if (!prepared?.coldRequest) throw new Error('prepare returned no coldRequest')

// The template identity lives in `contractConfig`, not in `coldRequest` - the
// cold request carries only the witness and the proof mode. Reading it from the
// wrong place is what made the first run of this script abort, which is exactly
// what the assertion below was there to catch.
const contract = prepared.contractConfig
if (!contract) throw new Error('prepare returned no contractConfig')

// groth16 is required: the on-chain verifier consumes an Ethereum seal, and a
// succinct receipt has none. `ethereumSealB64` is null in any other mode.
const cold = await post('/v5/cold-templates/prove', { ...prepared.coldRequest, proofMode: 'groth16' })

for (const field of ['ethereumSealB64', 'templateId', 'genesisDataHash', 'canonicalColdTemplateDataB64', 'programId']) {
  if (!cold?.[field]) throw new Error(`cold proof omitted ${field}`)
}

const seal = `0x${Buffer.from(cold.ethereumSealB64, 'base64').toString('hex')}`
// contractConfig already carries this as hex; the base64 from the prove call is
// the same bytes. Prefer the hex so nothing depends on a re-encode round trip.
const canonical = contract.canonicalColdTemplateData
  ?? `0x${Buffer.from(cold.canonicalColdTemplateDataB64, 'base64').toString('hex')}`

const record = {
  schema: 'zkdeal/soak-cold-template/v1',
  mintedAtUtc: new Date().toISOString(),
  templateId: contract.templateId ?? cold.templateId,
  genesisDataHash: contract.genesisDataHash ?? cold.genesisDataHash,
  statement: cold.statement,
  programId: cold.programId,
  proofMode: cold.proofMode,
  seal,
  canonicalColdTemplateData: canonical,
  initialStateRoot: contract.initialStateRoot ?? null,
  policyHash: contract.policyHash ?? null,
  proofSystemVersion: contract.proofSystemVersion ?? null,
  proofProgramId: contract.proofProgramId ?? null,
  // createRoom needs these too, and prepare is the only thing that knows them.
  initialParticipantRoot: contract.initialParticipantRoot ?? null,
  initialParticipantCount: contract.initialParticipantCount ?? 1,
  initialApproverRoot: contract.initialApproverRoot ?? null,
  initialActiveCount: contract.initialActiveCount ?? 0,
  participantCapacity: prepare.participantCapacity,
  gpu: { name: cold.gpuName ?? null, uuid: cold.gpuUuid ?? null },
  cycles: cold.cycles ?? null,
  elapsedMs: cold.profile?.totalPipelineMs ?? null,
}

// A missing root or policy hash would make the register call revert deep in the
// registry with an opaque InvalidTemplate, so fail here where the cause is
// obvious.
for (const field of ['initialStateRoot', 'policyHash', 'proofSystemVersion']) {
  if (!record[field]) {
    throw new Error(
      `prepare did not expose ${field}; register() needs it and would revert InvalidTemplate without it`,
    )
  }
}

writeFileSync(out, JSON.stringify(record, null, 2))
console.log(`MINT-COLD-OK template=${record.templateId} genesis=${record.genesisDataHash}`)
console.log(`  seal ${(seal.length - 2) / 2} bytes, canonical ${(canonical.length - 2) / 2} bytes -> ${out}`)
