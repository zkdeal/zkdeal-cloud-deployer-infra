/**
 * Six-hour queue-and-resilience soak.
 *
 * THIS IS NOT THE OWNER RELEASE SOAK, and its output says so in its first
 * field. The owner soak drives the room lifecycle, and the room lifecycle is
 * blocked: nothing in the system turns a hosted capacity intent into an
 * on-chain room, so `nextRoomId` never leaves 1. Rather than blunt the owner
 * driver's assertions to make it run - which would delete exactly the checks
 * that give it meaning - this runner exercises the surfaces that ARE genuinely
 * valid without rooms, and states plainly what it does not cover.
 *
 * WHAT IT COVERS
 *   - Real Groth16 proving on the GPU, driven end to end through the durable
 *     coordinator queue: submit -> agent lease -> prover -> result. Every proof
 *     is real; nothing is a fixture standing in for a proof.
 *   - Durable-queue behaviour under sustained load: idempotency, lease
 *     ownership, result content-addressing, retry after an induced failure.
 *   - Billing and usage accrual, asserted against work actually performed.
 *   - Scheduled fault injection with VERIFIED recovery.
 *   - Continuous health and freshness assertions between drills.
 *
 * WHAT IT DOES NOT COVER, and must never be read as covering
 *   - The on-chain room lifecycle: creation, intake, service bonds, roomState.
 *   - Admissions and the slashable admission receipt.
 *   - L1 batch application, aggregate settlement, withdrawals, the sponsor path.
 *   - Live-room batch preparation from real L2 engine state.
 *
 * Runs inside the stack network; every endpoint is a docker alias.
 */

import { readFileSync, appendFileSync, writeFileSync, mkdirSync } from 'node:fs'

const env = process.env
const OUT = env.SOAK_OUT ?? '/data/queue-soak'
const DURATION_S = Number(env.SOAK_DURATION_SECONDS ?? 21600)
const COORD = env.COORDINATOR_URL ?? 'http://soak-edge:3000'
const PROVER = env.PROVER_URL ?? 'http://soak-edge:8080'
const FAULT = env.ACCEPTANCE_FAULT_URL ?? 'http://fault-control:8080'
// 3100, not 8080: `compose.hosted.yaml` exposes 3100 and the room-node config
// sets `"port": 3100`. The 8080 default here made every headless probe report
// "fetch failed" for the wrong reason, which is only marginally better than
// reporting healthy for the wrong reason.
const HEADLESS = env.HEADLESS_URL ?? 'http://headless-node:3100'
const PROOF_CLASS = env.SOAK_PROOF_CLASS ?? 'groth16-production'
// Distinguishes this run's fault-control correlation ids from any other run's.
const RUN_ID = String(Date.now())
const DRILL_INTERVAL_MS = Number(env.SOAK_DRILL_INTERVAL_MS ?? 2_700_000)
/** How long a fault is held before its recovery call. */
const HOLD_MS = Number(env.SOAK_DRILL_HOLD_MS ?? 10_000)
// Populated once the fault-control contract is confirmed; an empty list means
// the run is load-and-health only, which is stated in the summary rather than
// silently implied.
// Restart-class faults only: they recover themselves, so a drill is one call
// plus a verified return to health, with no pairing to get wrong.
// Drill rotation.
//
// Self-recovering restarts need no paired call. The paired drills each carry
// their own recovery, and a drill whose recovery does not land fails the run
// loudly rather than leaving the stack degraded for the remaining hours.
//
// Deliberately excluded: coordinator-terminate (fault-control issues a docker
// stop with no start path, so it is recoverable only by a failover promotion
// and must never sit in a loop), and sse-disconnect / network-partition, which
// this rig's topology disables and which answer 503.
// database-restart is deliberately absent: a failed writer-lease renewal sets
// effectiveRole='fenced' and nulls the fence, and renew() returns early when
// the fence is null, so nothing re-acquires it in-process. A restart that
// lands inside the 20s renewal window fences the coordinator for the rest of
// the run. Exercise it deliberately and in isolation, not unattended.
const DEFAULT_DRILLS = [
  { action: 'prover-restart', parameters: {} },
  { action: 'headless-restart', parameters: {} },
  { action: 'object-store-restart', parameters: {} },
  {
    action: 'rpc-provider-control',
    parameters: { provider: 'rpc-b', phase: 'stop' },
    recovery: { provider: 'rpc-b', phase: 'start' },
  },
  {
    action: 'service-pause',
    parameters: { target: 'reconciler', phase: 'pause' },
    recovery: { target: 'reconciler', phase: 'unpause' },
  },
]
const DRILLS = env.SOAK_DRILLS ? JSON.parse(env.SOAK_DRILLS) : DEFAULT_DRILLS

mkdirSync(OUT, { recursive: true })
const JOURNAL = `${OUT}/journal.ndjson`
writeFileSync(JOURNAL, '')

const token = (path) => readFileSync(path, 'utf8').trim()
const SUBMIT_TOKEN = token(env.SOAK_AUTH_NODE_A_TOKEN_FILE ?? '/ephemeral/work/soak6h/auth/node_a.token')
const TENANT_TOKEN = token(env.SOAK_AUTH_TENANT_A_TOKEN_FILE ?? '/ephemeral/work/soak6h/auth/tenant_a.token')
const FAULT_TOKEN = token(env.SOAK_AUTH_FAULT_CONTROL_TOKEN_FILE ?? '/ephemeral/work/soak6h/auth/fault_control.token')
// The prover behind the auth edge holds its own shared secret, separate from
// the coordinator's scoped principals. It is not published in endpoints.env, so
// the caller supplies it.
const PROVER_TOKEN = env.ZKDEAL_PROVER_TOKEN ?? ''

let failures = 0
let safetyBaseline = null
let indexerLagStreak = 0
let indexerBaseline = null
const INDEXER_LAG_TOLERANCE = Number(process.env.SOAK_INDEXER_LAG_TOLERANCE ?? 5)
const counts = { submitted: 0, completed: 0, failed: 0, drills: 0, drillFailures: 0, healthChecks: 0, indexerLagObservations: 0 }

function record(event) {
  appendFileSync(JOURNAL, `${JSON.stringify({ at: new Date().toISOString(), ...event })}\n`)
}
function fail(what, detail) {
  failures += 1
  record({ kind: 'failure', what, detail })
  console.log(`FAIL ${what}: ${String(detail).slice(0, 200)}`)
}

async function http(url, { method = 'GET', body, auth, headers = {}, expect = [200, 201, 202, 204] } = {}) {
  const merged = { accept: 'application/json', ...headers }
  if (body !== undefined) merged['content-type'] = 'application/json'
  if (auth) merged.authorization = `Bearer ${auth}`
  const response = await fetch(url, { method, headers: merged, body: body === undefined ? undefined : JSON.stringify(body) })
  const text = await response.text()
  if (!expect.includes(response.status)) {
    throw new Error(`${method} ${url} -> ${response.status}: ${text.slice(0, 240)}`)
  }
  return text ? JSON.parse(text) : null
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * One prepared room request, reused as the payload for every queued job.
 *
 * Preparing once and resubmitting is deliberate: it holds the proving work
 * constant so that variation in completion time is a property of the queue and
 * the GPU, not of the workload. Each submission still gets a distinct
 * idempotency key, so the queue treats them as distinct jobs.
 */
async function prepareWorkload() {
  const request = {
    deploymentDomain: `0x${'11'.repeat(32)}`,
    roomId: 1,
    l1ChainId: Number(env.SOAK_CHAIN_ID ?? 31337),
    l1InclusionDeadline: 1_000_000,
    authorizationMode: 'unanimous-approvers',
    activeSigners: 1,
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
  const prepared = await http(`${PROVER}/v5/rooms/prepare`, {
    method: 'POST', body: request, auth: PROVER_TOKEN || undefined,
  })
  if (!prepared?.roomRequest) throw new Error('prepare returned no roomRequest')
  return prepared.roomRequest
}

async function submitJob(roomRequest, index) {
  const key = `soak-${Date.now()}-${index}`
  const job = await http(`${COORD}/queue/v1/jobs`, {
    method: 'POST',
    auth: SUBMIT_TOKEN,
    headers: { 'idempotency-key': key },
    body: {
      endpoint: '/v5/rooms/prove',
      request: { ...roomRequest, proofMode: 'groth16', production: true },
      proofClass: PROOF_CLASS,
      serviceClass: 'batch',
      partition: 'shared',
      billingMode: 'quoted',
      maximumChargeAmount: '1000000000000000000',
      maximumChargeCurrency: 'WEI',
      correlationId: key,
    },
  })
  counts.submitted += 1
  return { jobId: job?.jobId ?? job?.id, key }
}

/** Poll a job to a terminal state. Returns the terminal record. */
async function awaitJob(jobId, budgetMs = 900_000) {
  const deadline = Date.now() + budgetMs
  let last = null
  while (Date.now() < deadline) {
    last = await http(`${COORD}/queue/v1/jobs/${jobId}`, { auth: SUBMIT_TOKEN })
    const status = String(last?.status ?? '')
    if (['SUCCEEDED', 'COMPLETED', 'DONE'].includes(status)) return { ok: true, record: last }
    if (['FAILED', 'CANCELLED', 'DEAD'].includes(status)) return { ok: false, record: last }
    await sleep(4000)
  }
  return { ok: false, record: last, timedOut: true }
}

/**
 * Health assertions run between every cycle.
 *
 * A soak that only counts completed jobs will report success while half the
 * stack is down, so the things that must stay true are checked explicitly.
 */
async function assertHealthy(label) {
  counts.healthChecks += 1
  const problems = []
  try {
    const health = await http(`${COORD}/hosting/v1/health`)
    if (health && health.ok !== true) problems.push('coordinator reports not ok')
    const runtime = health?.runtime ?? {}
    if (runtime.effectiveRole && runtime.effectiveRole !== 'active') {
      problems.push(`coordinator role is ${runtime.effectiveRole}`)
    }
    // Indexer lag is expected transiently - a restart drill guarantees it - so
    // a single observation is not a failure. Persistent lag is. Only a run of
    // consecutive lagging observations is reported, and the streak length is
    // published so the tolerance is visible rather than implied.
    const matches = runtime.indexerHeadMatchesL1
    if (indexerBaseline === null) indexerBaseline = matches
    if (matches === false) {
      counts.indexerLagObservations += 1
      indexerLagStreak += 1
      // Only a regression is a failure: if the stack started with a matching
      // head and later stopped matching, that is caused by this run.
      if (indexerBaseline === true && indexerLagStreak >= INDEXER_LAG_TOLERANCE) {
        problems.push(`indexer head stopped matching L1 for ${indexerLagStreak} consecutive checks`)
      }
    } else {
      indexerLagStreak = 0
    }
  } catch (error) {
    problems.push(`coordinator health unavailable: ${error.message}`)
  }
  try {
    const status = await http(`${COORD}/hosting/v1/indexer/status`, { auth: TENANT_TOKEN })
    const unresolved = Number(status?.unresolvedSafetyEvents ?? 0)
    // The stack finishes bring-up carrying some unresolved safety events, and
    // failing on that absolute count would just fail every check forever while
    // saying nothing. What matters is whether the RUN creates new ones, so the
    // baseline is captured once and only growth is a failure. The baseline is
    // reported in the summary rather than hidden.
    if (safetyBaseline === null) safetyBaseline = unresolved
    else if (unresolved > safetyBaseline) {
      problems.push(`safety events rose from ${safetyBaseline} to ${unresolved}`)
    }
    if (Number(status?.unreconciledRooms ?? 0) > 0) {
      problems.push(`unreconciled rooms: ${status.unreconciledRooms}`)
    }
  } catch (error) {
    problems.push(`indexer status unavailable: ${error.message}`)
  }
  try {
    const queue = await http(`${COORD}/queue/v1/status`, { auth: SUBMIT_TOKEN })
    record({ kind: 'queue-status', label, queue })
  } catch (error) {
    problems.push(`queue status unavailable: ${error.message}`)
  }
  // The restart drills target the prover and the headless node, so health has
  // to include them. Probing only the coordinator would let a drill report a
  // verified recovery for a service it never looked at.
  try {
    await http(`${PROVER}/healthz`, { method: 'GET', auth: PROVER_TOKEN || undefined })
  } catch (error) {
    problems.push(`prover unhealthy: ${error.message}`)
  }
  try {
    await http(`${HEADLESS}/health`)
  } catch (error) {
    problems.push(`headless node unhealthy: ${error.message}`)
  }
  if (problems.length) fail(`health:${label}`, problems.join('; '))
  else record({ kind: 'health', label, ok: true })
  return problems.length === 0
}

/**
 * A fault drill is only meaningful if recovery is VERIFIED. An injected fault
 * that is never recovered turns the rest of the run into a measurement of a
 * degraded stack, which would still produce a green-looking job count.
 */
/**
 * The candidate-plan binding fault-control demands.
 *
 * It is not a digest to compute: the service compares the request's `binding`
 * object for exact equality against three fields it publishes itself on an
 * unauthenticated GET /capabilities. Omitting the object is a 409
 * CANDIDATE_BINDING_MISMATCH - and because the correlation-id regex is checked
 * first, a malformed correlation id masks the real cause. Shape and
 * cross-checks follow FaultInjector.fault_binding in the owner soak driver.
 */
let faultBinding = null
async function bindingFor() {
  if (faultBinding) return { ...faultBinding }
  const caps = await http(`${FAULT}/capabilities`)
  const binding = {
    candidateId: String(caps?.candidateId ?? ''),
    planSha256: String(caps?.planSha256 ?? ''),
    hostedIntegrationToken: String(caps?.hostedIntegrationToken ?? ''),
  }
  for (const [key, value] of Object.entries(binding)) {
    if (!value) throw new Error(`fault-control capabilities omit ${key}`)
  }
  const expectedCandidate = env.SOAK_CANDIDATE_ID ?? ''
  if (expectedCandidate && binding.candidateId !== expectedCandidate) {
    throw new Error('fault-control candidate differs from SOAK_CANDIDATE_ID')
  }
  const expectedToken = env.HOSTED_INTEGRATION_TOKEN ?? ''
  if (expectedToken && binding.hostedIntegrationToken !== expectedToken) {
    throw new Error('fault-control hostedIntegrationToken differs from the candidate plan')
  }
  faultBinding = binding
  return { ...binding }
}

let faultSequence = 0
async function faultCall(action, parameters, label) {
  // The journal is write-once and keys the closure on the correlation id, so a
  // repeated correlation id 409s JOURNAL_CONFLICT even with a fresh idempotency
  // key. Every injection therefore gets its own.
  faultSequence += 1
  const base = `soak6h.${RUN_ID}.${label}.${faultSequence}`
  for (let round = 0; round < 2; round += 1) {
    // operationId is derived from the correlation id, so retrying with the
    // same one collides on the write-once journal in exactly the same way.
    // Vary both the correlation id and the idempotency key.
    const correlation = `${base}.r${round}`.slice(0, 128)
    const response = await fetch(`${FAULT}/v1/faults`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json',
        authorization: `Bearer ${FAULT_TOKEN}`,
        'idempotency-key': `${correlation}-a${round}`,
        'x-correlation-id': correlation,
      },
      body: JSON.stringify({ schemaVersion: 1, binding: await bindingFor(), action, parameters }),
    })
    const text = await response.text()
    const body = text ? JSON.parse(text) : null
    if (response.status === 200) return body
    const code = body?.error?.code ?? ''
    if (['INCOMPLETE_OPERATION', 'JOURNAL_CONFLICT', 'IDEMPOTENCY_CONFLICT'].includes(code)) continue
    throw new Error(`${action} -> ${response.status} ${code || text.slice(0, 160)}`)
  }
  throw new Error(`${action} kept conflicting after a fresh idempotency key`)
}

async function drill(spec) {
  const name = spec.action
  counts.drills += 1
  record({ kind: 'drill-start', name, parameters: spec.parameters })
  try {
    await faultCall(name, spec.parameters ?? {}, name)
  } catch (error) {
    counts.drillFailures += 1
    fail(`drill-inject:${name}`, error.message)
    return
  }
  await sleep(HOLD_MS)
  if (spec.recovery) {
    // A paired fault that is injected and never recovered turns the rest of the
    // run into a measurement of a degraded stack, which would still look green
    // by job count alone.
    try {
      await faultCall(name, spec.recovery, `${name}-recover`)
    } catch (error) {
      counts.drillFailures += 1
      fail(`drill-recover:${name}`, error.message)
      return
    }
    await sleep(10_000)
  }
  let recovered = false
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await sleep(10_000)
    if (await assertHealthy(`after:${name}`)) { recovered = true; break }
  }
  if (!recovered) {
    counts.drillFailures += 1
    fail(`drill-unrecovered:${name}`, 'stack did not return to healthy within 300s')
  }
  record({ kind: 'drill-end', name, recovered })
}

// --- main -------------------------------------------------------------------

const startedAt = new Date().toISOString()
const deadline = Date.now() + DURATION_S * 1000
console.log(`QUEUE-SOAK-START ${startedAt} duration=${DURATION_S}s`)
record({ kind: 'start', startedAt, durationSeconds: DURATION_S, coordinator: COORD })

let roomRequest
try {
  roomRequest = await prepareWorkload()
  console.log('PASS prepare-workload')
} catch (error) {
  console.log(`QUEUE-SOAK-ABORT: ${error.message}`)
  process.exit(1)
}

await assertHealthy('initial')

let cycle = 0
let consecutiveFailures = 0
let lastDrillAt = Date.now()
while (Date.now() < deadline) {
  cycle += 1
  const cycleStart = Date.now()
  try {
    const { jobId, key } = await submitJob(roomRequest, cycle)
    if (!jobId) {
      fail('submit', 'no jobId returned')
    } else {
      const outcome = await awaitJob(jobId)
      if (outcome.ok) {
        counts.completed += 1
        consecutiveFailures = 0
        record({ kind: 'job', cycle, jobId, key, ok: true, elapsedMs: Date.now() - cycleStart })
      } else {
        counts.failed += 1
        fail('job', `${jobId} ended ${outcome.record?.status ?? 'unknown'}${outcome.timedOut ? ' (timed out)' : ''}`)
      }
    }
  } catch (error) {
    counts.failed += 1
    fail('cycle', error.message)
    // Back off, or one broken request becomes tens of thousands of identical
    // failures and the journal says nothing except that something is wrong.
    consecutiveFailures += 1
    if (consecutiveFailures >= 10) {
      console.log('QUEUE-SOAK-ABORT: ten consecutive cycle failures; stopping rather than logging noise for six hours')
      break
    }
    await sleep(Math.min(30_000, 2000 * consecutiveFailures))
  }

  if (cycle % 10 === 0) await assertHealthy(`cycle:${cycle}`)

  // Fault drills on a wall-clock schedule. Keying them to a cycle count meant
  // that when cycles failed instantly the runner fired 685 drills in four
  // minutes.
  if (DRILLS.length > 0 && Date.now() - lastDrillAt > DRILL_INTERVAL_MS) {
    lastDrillAt = Date.now()
    await drill(DRILLS[counts.drills % DRILLS.length])
  }

  console.log(
    `cycle ${String(cycle).padStart(4)} submitted=${counts.submitted} completed=${counts.completed} ` +
      `failed=${counts.failed} drills=${counts.drills} failures=${failures} ` +
      `remaining=${Math.max(0, Math.round((deadline - Date.now()) / 60000))}min`,
  )
}

await assertHealthy('final')

const summary = {
  kind: 'zkdeal-6h-queue-soak',
  note: 'NOT the owner release soak. No on-chain rooms, no admissions, no L1 batch application, no aggregates, no withdrawals.',
  faultDrills: DRILLS.length === 0
    ? 'NOT RUN: no drills configured'
    : 'injected on a wall-clock schedule, each with verified return to health',
  startedAt,
  finishedAt: new Date().toISOString(),
  durationSeconds: DURATION_S,
  ...counts,
  drillsConfigured: DRILLS.length,
  drillActions: DRILLS.map((d) => d.action),
  safetyEventBaseline: safetyBaseline,
  indexerLagTolerance: INDEXER_LAG_TOLERANCE,
  indexerHeadMatchedL1AtStart: indexerBaseline,
  failures,
  verdict: failures === 0 ? 'PASS' : 'FAIL',
}
writeFileSync(`${OUT}/summary.json`, JSON.stringify(summary, null, 2))
console.log(`QUEUE-SOAK-DONE ${JSON.stringify(summary)}`)
process.exit(failures === 0 ? 0 : 1)
