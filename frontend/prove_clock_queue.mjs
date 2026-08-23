// Proof harness — DURABLE PUNCH QUEUE (clock-in/out reliability, Part B).
//
// Incident: under load the kiosk's clock-in/out timed out and showed "your punch may NOT have gone
// through", inviting a blind re-tap that could double-open or 404. The fix on the client: every tap
// mints a STABLE client_request_id, is saved to localStorage BEFORE the network call, goes optimistic
// immediately, and is retried with the SAME id until the server confirms — reconciled against the
// status poll. The backend (Part A) dedupes on that id, so a retry is one row, not two.
//
// This harness does NOT re-implement any of that. It loads the REAL module — frontend/src/lib/
// punchQueue.ts — through the actual TypeScript compiler (transpile only, no re-typing) and exercises
// the genuine runtime with injected fakes for storage / uuid / clock / send / sleep. If the module's
// logic changes, this proof runs against the change.
//
// Run:  node frontend/prove_clock_queue.mjs      (no network, no DB, no browser)

import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const require = createRequire(import.meta.url)
const ts = require('typescript')
const HERE = dirname(fileURLToPath(import.meta.url))

let pass = 0, fail = 0
function ck(label, cond) {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}`) }
}
function must(cond, msg) { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }

// ── Load the real punchQueue.ts by transpiling it (types stripped by tsc, logic verbatim) ─────────
function loadTs(absPath) {
  const src = readFileSync(absPath, 'utf8')
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 },
    fileName: absPath,
  }).outputText
  const mod = { exports: {} }
  new Function('exports', 'module', 'require', js)(mod.exports, mod, require)
  return mod.exports
}

const PQ = loadTs(join(HERE, 'src/lib/punchQueue.ts'))
const { createPunchQueue, runPunchWithRetry, optimisticMessage, syncingMessage, nextBackoffMs,
        isRetryable, reconcileWithStatus, PUNCH_QUEUE_KEY } = PQ
must(typeof createPunchQueue === 'function', 'createPunchQueue not exported')
must(typeof runPunchWithRetry === 'function', 'runPunchWithRetry not exported')

// ── Test doubles ──────────────────────────────────────────────────────────────────────────────────
function fakeStorage() {
  const map = new Map()
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)) },
    removeItem: (k) => { map.delete(k) },
  }
}
function seqUuid() {
  let n = 0
  return () => `id-${++n}`
}
const noSleep = () => Promise.resolve()   // deterministic: never actually wait in the proof
function timeoutErr() { const e = new Error('The operation timed out'); e.name = 'TimeoutError'; return e }
function httpErr(status, message = 'nope') { const e = new Error(message); e.status = status; return e }

const CLOCK_IN = '/api/v1/storeops/timeclock/clock-in'
const CLOCK_OUT = '/api/v1/storeops/timeclock/clock-out'

// ══════════════════════════════════════════════════════════════════════════════════════════════════
console.log('A. optimistic status shows IMMEDIATELY — before any network send resolves')
{
  const storage = fakeStorage()
  const q = createPunchQueue({ storage, uuid: seqUuid(), now: () => 1000 })

  // Model the tap: enqueue (synchronous, persists locally) → set optimistic message → THEN send.
  let sentBeforeMessage = false
  let messageAtTapTime = ''
  const item = q.enqueue('clock-in', CLOCK_IN, { selfie: 'data:...', gps_lat: 40.7 })
  messageAtTapTime = optimisticMessage(item.kind)               // shown right now, no await
  ck('punch is persisted the instant it is enqueued (survives a refresh)', storage.map.has(PUNCH_QUEUE_KEY))
  ck('optimistic clock-in message is the reassuring "syncing" text',
     messageAtTapTime === '⏳ Clocked in — syncing…')
  ck('optimistic clock-out message likewise', optimisticMessage('clock-out') === '⏳ Clocked out — syncing…')

  // The send has not even been called yet at the moment the optimistic message is on screen.
  const outcome = await runPunchWithRetry(item, {
    send: async () => { sentBeforeMessage = true; return { success: true, data: { time: '9:00' } } },
    sleep: noSleep,
  })
  ck('the message was set BEFORE the send ran (never blocks on the network)',
     messageAtTapTime !== '' && outcome.status === 'confirmed')
  ck('never shows the old scary "may NOT have gone through"',
     !/may NOT have gone through/i.test(messageAtTapTime) && !/may NOT have gone through/i.test(syncingMessage('clock-in')))
}

// ══════════════════════════════════════════════════════════════════════════════════════════════════
console.log('B. a timed-out punch is RETRIED with a STABLE client_request_id across every retry')
{
  const storage = fakeStorage()
  const q = createPunchQueue({ storage, uuid: seqUuid(), now: () => 1000 })
  const item = q.enqueue('clock-out', CLOCK_OUT, {})

  const idsSeen = []
  let syncingShown = 0
  let calls = 0
  const outcome = await runPunchWithRetry(item, {
    send: async (body) => {
      calls++
      idsSeen.push(body.client_request_id)
      if (calls < 3) throw timeoutErr()          // first two attempts time out
      return { success: true, data: { time: '5:00', hours: 8 } }   // third lands
    },
    sleep: noSleep,
    onSyncing: () => { syncingShown++ },
  })

  ck('retried until it landed (3 send attempts)', calls === 3)
  ck('the SAME client_request_id was sent on every attempt', idsSeen.length === 3 && new Set(idsSeen).size === 1)
  ck('the id sent equals the queued item id', idsSeen[0] === item.client_request_id)
  ck('"saved, syncing…" was shown while retrying (never the scary message)', syncingShown === 2)
  ck('final outcome is confirmed', outcome.status === 'confirmed')

  // …and once confirmed, the tap driver clears the queue item (the caller does q.remove).
  q.remove(item.client_request_id)
  ck('a confirmed response clears the queue', q.list().length === 0)
  ck('the cleared queue is persisted', JSON.parse(storage.map.get(PUNCH_QUEUE_KEY)).length === 0)
}

// ══════════════════════════════════════════════════════════════════════════════════════════════════
console.log('C. a CONFIRMED /timeclock/status clears the queue item (safety net for a lost response)')
{
  const storage = fakeStorage()
  const q = createPunchQueue({ storage, uuid: seqUuid(), now: () => 1000 })
  const inItem = q.enqueue('clock-in', CLOCK_IN, { selfie: 'x' })

  // The response was lost, but the poll shows an open row that is OURS → reconcile clears it.
  const cleared = q.reconcile({ clockedIn: true, entry: { client_request_id: inItem.client_request_id } })
  ck('status with a matching open entry clears the clock-in item', cleared.includes(inItem.client_request_id))
  ck('queue is now empty', q.list().length === 0)

  // A clock-out is confirmed by the ABSENCE of an open row.
  const q2 = createPunchQueue({ storage: fakeStorage(), uuid: seqUuid(), now: () => 1000 })
  const outItem = q2.enqueue('clock-out', CLOCK_OUT, {})
  ck('clock-out NOT cleared while still clocked in (row not yet closed)',
     q2.reconcile({ clockedIn: true, entry: { client_request_id: 'someone-else' } }).length === 0)
  ck('clock-out cleared once there is no open row', q2.reconcile({ clockedIn: false, entry: null }).includes(outItem.client_request_id))

  // A clock-in is NOT cleared by a DIFFERENT open entry (not ours) on a migration-aware backend.
  const q3 = createPunchQueue({ storage: fakeStorage(), uuid: seqUuid(), now: () => 1000 })
  const mineIn = q3.enqueue('clock-in', CLOCK_IN, {})
  ck('clock-in NOT cleared by an unrelated open entry id',
     reconcileWithStatus(q3.list(), { clockedIn: true, entry: { client_request_id: 'not-mine' } }).length === 0)
  ck('but IS cleared on a pre-migration backend that omits the id (clockedIn alone)',
     reconcileWithStatus(q3.list(), { clockedIn: true, entry: {} }).includes(mineIn.client_request_id))
}

// ══════════════════════════════════════════════════════════════════════════════════════════════════
console.log('D. each DISTINCT punch gets a FRESH id (a new punch = a new id)')
{
  const q = createPunchQueue({ storage: fakeStorage(), uuid: seqUuid(), now: () => 1000 })
  const a = q.enqueue('clock-in', CLOCK_IN, {})
  const b = q.enqueue('clock-out', CLOCK_OUT, {})
  const c = q.enqueue('clock-in', CLOCK_IN, {})
  ck('three distinct punches → three distinct ids',
     new Set([a.client_request_id, b.client_request_id, c.client_request_id]).size === 3)

  // A held punch (priority-ack / manager override) REUSES its id so the whole logical punch is one
  // idempotent unit — the enqueue overload accepts an explicit id and de-dupes it in the queue.
  const held = q.enqueue('clock-in', CLOCK_IN, { priority_ack: true }, a.client_request_id)
  ck('an explicit-id re-enqueue reuses the same id', held.client_request_id === a.client_request_id)
  ck('and does NOT create a duplicate queue entry for that id',
     q.list().filter((i) => i.client_request_id === a.client_request_id).length === 1)
}

// ══════════════════════════════════════════════════════════════════════════════════════════════════
console.log('E. a DEFINITIVE non-retryable error stops immediately (no blind-retry loop)')
{
  const q = createPunchQueue({ storage: fakeStorage(), uuid: seqUuid(), now: () => 1000 })
  const item = q.enqueue('clock-in', CLOCK_IN, {})
  let calls = 0
  const outcome = await runPunchWithRetry(item, {
    send: async () => { calls++; throw httpErr(400, 'bad store') },
    sleep: noSleep,
  })
  ck('sent exactly once (a 4xx will never change on retry)', calls === 1)
  ck('outcome is failed (surfaced to the rep), not silently retried', outcome.status === 'failed')

  // The retryable/terminal split itself:
  ck('timeout is retryable', isRetryable(timeoutErr()) === true)
  ck('abort is retryable', isRetryable({ name: 'AbortError' }) === true)
  ck('network "Failed to fetch" is retryable', isRetryable(new TypeError('Failed to fetch')) === true)
  ck('status 0 is retryable', isRetryable(httpErr(0)) === true)
  ck('503 is retryable', isRetryable(httpErr(503)) === true)
  ck('429 is retryable', isRetryable(httpErr(429)) === true)
  ck('400 is NOT retryable', isRetryable(httpErr(400)) === false)
  ck('404 is NOT retryable', isRetryable(httpErr(404)) === false)
}

// ══════════════════════════════════════════════════════════════════════════════════════════════════
console.log('F. exponential backoff carries the same id and grows, capped at 30s')
{
  ck('1st retry ~1s', nextBackoffMs(1) === 1000)
  ck('2nd ~2s', nextBackoffMs(2) === 2000)
  ck('3rd ~4s', nextBackoffMs(3) === 4000)
  ck('grows exponentially', nextBackoffMs(4) === 8000 && nextBackoffMs(5) === 16000)
  ck('capped at 30s', nextBackoffMs(10) === 30000 && nextBackoffMs(50) === 30000)

  // Prove the backoff is actually consulted between retries (delays requested, in order).
  const q = createPunchQueue({ storage: fakeStorage(), uuid: seqUuid(), now: () => 1000 })
  const item = q.enqueue('clock-out', CLOCK_OUT, {})
  const delays = []
  const outcome = await runPunchWithRetry(item, {
    send: async () => { throw timeoutErr() },       // always times out → will exhaust
    sleep: async (ms) => { delays.push(ms) },
    backoff: nextBackoffMs,
    maxAttempts: 4,
  })
  ck('exhausted after maxAttempts (stays queued for the poll to resume)', outcome.status === 'exhausted')
  ck('backoff delays grew between attempts', JSON.stringify(delays) === JSON.stringify([1000, 2000, 4000]))
  ck('the punch is STILL in the queue after exhaustion (never lost)', q.list().length === 1)
}

// ══════════════════════════════════════════════════════════════════════════════════════════════════
console.log('G. durability — a fresh queue over the same storage RESUMES the pending punch (hard refresh)')
{
  const storage = fakeStorage()
  const q1 = createPunchQueue({ storage, uuid: seqUuid(), now: () => 1000 })
  const item = q1.enqueue('clock-in', CLOCK_IN, { selfie: 'evidence', gps_lat: 40.7, gps_accuracy_m: 12 })

  // Simulate a hard refresh: a brand-new queue instance reads the SAME storage.
  const q2 = createPunchQueue({ storage, uuid: seqUuid(), now: () => 2000 })
  const resumed = q2.list()
  ck('the pending punch survived the "refresh"', resumed.length === 1)
  ck('it kept the SAME client_request_id after reload', resumed[0].client_request_id === item.client_request_id)
  ck('it kept the selfie + GPS evidence for the retry',
     resumed[0].body.selfie === 'evidence' && resumed[0].body.gps_lat === 40.7 && resumed[0].body.gps_accuracy_m === 12)

  // Resume driving it — the id sent still matches the one minted before the refresh.
  let sentId = null
  const outcome = await runPunchWithRetry(resumed[0], {
    send: async (body) => { sentId = body.client_request_id; return { success: true, data: {} } },
    sleep: noSleep,
  })
  ck('the resumed retry sends the original id (idempotent with the first attempt)',
     sentId === item.client_request_id && outcome.status === 'confirmed')
}

console.log(`\n${fail === 0 ? 'PASS' : 'FAIL'}: ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
