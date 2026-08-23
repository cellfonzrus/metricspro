// ── Durable punch queue (clock-in/out reliability, Part B) ───────────────────────────────────────
// PAYROLL-CRITICAL. A punch is labor hours; a lost tap is a rep working for free (or a manager
// chasing a "did it go through?" ticket). Root cause of the incident: under load a saturated single
// worker made clock-in/out time out, and the kiosk showed "your punch may NOT have gone through" —
// scary, and it invited a blind re-tap that could double-open or 404. The fix on the client:
//
//   1. Every tap mints a STABLE `client_request_id` (crypto.randomUUID) and enqueues the punch to
//      localStorage BEFORE the network call, so a crash/refresh mid-flight never drops it.
//   2. The UI goes optimistic immediately ("Clocked in — syncing…") — never the old scary message.
//   3. The punch is sent WITH that id; on a timeout/network blip it is retried with exponential
//      backoff carrying the SAME id (the backend now dedupes on it — one row, idempotent_replay),
//      showing "Saved — syncing…". Only a definitive non-retryable error surfaces an error.
//   4. The 15s `GET /timeclock/status` poll RECONCILES: an open row that is ours (or simply the
//      clocked-in/out state we asked for) confirms the punch and clears the queue item — the safety
//      net for when the response itself was the thing that got lost.
//
// This module is PURE (no React, no direct browser globals — storage/uuid/clock/send/sleep are all
// injected), so the whole retry+reconcile behavior is provable headlessly (prove_clock_queue.mjs).

export type PunchKind = 'clock-in' | 'clock-out'

// One queued punch. `body` is the request body WITHOUT the id (selfie + GPS + store live here for a
// clock-in so a retry re-sends the exact same evidence); `client_request_id` is the single source of
// truth for the id and is merged into the body only at send time, so every retry carries one id.
export interface PunchItem {
  client_request_id: string
  kind: PunchKind
  path: string
  body: Record<string, unknown>
  createdAt: number
  attempts: number
  lastError?: string
}

// Minimal storage shape — window.localStorage satisfies it; a fake satisfies it in the proof.
export interface PunchStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

export const PUNCH_QUEUE_KEY = 'mp_punch_queue_v1'

// Optimistic status shown the instant the rep taps — the punch is saved locally, so we say so with
// confidence and NEVER "may not have gone through".
export function optimisticMessage(kind: PunchKind): string {
  return kind === 'clock-out' ? '⏳ Clocked out — syncing…' : '⏳ Clocked in — syncing…'
}

// Shown once a send times out / the network blips and we fall into backoff retry: still reassuring,
// because the punch is durably saved and will land.
export function syncingMessage(kind: PunchKind): string {
  return kind === 'clock-out' ? '⏳ Clocked out — saved, syncing…' : '⏳ Clocked in — saved, syncing…'
}

// Exponential backoff (attempt is 1-based): 1s, 2s, 4s, 8s, 16s, capped at 30s.
export function nextBackoffMs(attempt: number, baseMs = 1000, capMs = 30000): number {
  const raw = baseMs * Math.pow(2, Math.max(0, attempt - 1))
  return Math.min(raw, capMs)
}

// A retryable failure is one where the punch MIGHT have (or definitely has not yet) landed and a
// same-id retry is safe and useful: request timeouts, aborted/saturated connections, network drops,
// and transient server states (429/5xx). A definitive client error (4xx other than 429) is terminal
// — retrying will never change the answer, so we stop and surface it. An unrecognized error is
// treated as non-retryable so we fail loudly instead of looping forever.
export function isRetryable(err: unknown): boolean {
  if (!err) return false
  const name = String((err as { name?: unknown })?.name || '')
  if (name === 'TimeoutError' || name === 'AbortError') return true
  const status = Number((err as { status?: unknown })?.status)
  if (status === 0) return true
  if (status === 408 || status === 429) return true
  if (status >= 500 && status <= 599) return true
  if (status >= 400 && status < 500) return false
  const m = String((err as { message?: unknown })?.message ?? err ?? '')
  if (/failed to fetch|network\s*error|networkerror|load failed|timeout|timed out|aborted/i.test(m)) return true
  return false
}

// Given the queued items and the latest /timeclock/status, return the ids the server has CONFIRMED
// (so they can be cleared). A clock-in is confirmed when there is an open entry that is ours — or,
// on a pre-migration backend that doesn't echo client_request_id, simply that the rep is now clocked
// in. A clock-out is confirmed when there is no open entry any more (the row we were closing is
// closed). This is the backstop for a lost RESPONSE: the server acted, we just never heard the ack.
export function reconcileWithStatus(items: PunchItem[], status: unknown): string[] {
  if (!status || typeof status !== 'object') return []
  const s = status as { clockedIn?: unknown; entry?: { client_request_id?: unknown } | null }
  const clockedIn = s.clockedIn === true
  const entryId = s.entry && typeof s.entry === 'object' ? (s.entry as { client_request_id?: unknown }).client_request_id : undefined
  const cleared: string[] = []
  for (const it of items) {
    if (it.kind === 'clock-in') {
      if (clockedIn && (entryId == null || entryId === it.client_request_id)) cleared.push(it.client_request_id)
    } else {
      if (!clockedIn) cleared.push(it.client_request_id)
    }
  }
  return cleared
}

// ── The send/retry driver ────────────────────────────────────────────────────────────────────────
export interface RunPunchDeps {
  // Sends the punch. `body` already carries the stable client_request_id. Resolves on ANY server
  // response (success, idempotent_replay, or a needs_* prompt — all mean the server heard us);
  // rejects for a timeout / network drop / HTTP error.
  send: (body: Record<string, unknown>) => Promise<unknown>
  sleep: (ms: number) => Promise<void>
  onSyncing?: () => void
  retryable?: (e: unknown) => boolean
  backoff?: (attempt: number) => number
  maxAttempts?: number
}

export type RunPunchOutcome =
  | { status: 'confirmed'; res: unknown; attempts: number }
  | { status: 'failed'; error: unknown; attempts: number }     // non-retryable — surface it, drop the item
  | { status: 'exhausted'; error: unknown; attempts: number }  // retryable but hit the attempt cap — STAYS queued for the next resume/poll

// Retries the SAME punch (same client_request_id) with exponential backoff until the server responds
// or a definitive non-retryable error. Never loses the punch: on 'exhausted' the item is left in the
// queue so the status-poll resume drives it again.
export async function runPunchWithRetry(item: PunchItem, deps: RunPunchDeps): Promise<RunPunchOutcome> {
  const retryable = deps.retryable || isRetryable
  const backoff = deps.backoff || nextBackoffMs
  const maxAttempts = deps.maxAttempts ?? 6
  let attempts = 0
  let lastError: unknown
  while (attempts < maxAttempts) {
    attempts++
    try {
      const res = await deps.send({ ...item.body, client_request_id: item.client_request_id })
      return { status: 'confirmed', res, attempts }
    } catch (e) {
      lastError = e
      if (!retryable(e)) return { status: 'failed', error: e, attempts }
      if (attempts >= maxAttempts) break
      if (deps.onSyncing) deps.onSyncing()
      await deps.sleep(backoff(attempts))
    }
  }
  return { status: 'exhausted', error: lastError, attempts }
}

// ── The durable queue (localStorage-backed, guarded) ─────────────────────────────────────────────
export interface PunchQueue {
  // `id` lets a follow-up send (priority-ack / manager override of a held punch) reuse the SAME
  // stable id as the original tap, so the whole logical punch stays one idempotent unit server-side.
  enqueue(kind: PunchKind, path: string, body: Record<string, unknown>, id?: string): PunchItem
  list(): PunchItem[]
  get(id: string): PunchItem | undefined
  remove(id: string): void
  markAttempt(id: string, error?: string): void
  reconcile(status: unknown): string[]
  clear(): void
}

export function createPunchQueue(deps: { storage: PunchStorage; uuid: () => string; now: () => number }): PunchQueue {
  const { storage, uuid, now } = deps

  function load(): PunchItem[] {
    try {
      const raw = storage.getItem(PUNCH_QUEUE_KEY)
      const arr = raw ? JSON.parse(raw) : []
      return Array.isArray(arr) ? (arr as PunchItem[]) : []
    } catch {
      return []
    }
  }

  let items: PunchItem[] = load()

  function save(): void {
    try {
      storage.setItem(PUNCH_QUEUE_KEY, JSON.stringify(items))
    } catch {
      /* a full/absent localStorage must never break a punch — the in-memory copy still drives this session */
    }
  }

  return {
    enqueue(kind, path, body, id) {
      const item: PunchItem = { client_request_id: id || uuid(), kind, path, body, createdAt: now(), attempts: 0 }
      items = [...items.filter((i) => i.client_request_id !== item.client_request_id), item]
      save()
      return item
    },
    list() {
      return [...items]
    },
    get(id) {
      return items.find((i) => i.client_request_id === id)
    },
    remove(id) {
      items = items.filter((i) => i.client_request_id !== id)
      save()
    },
    markAttempt(id, error) {
      items = items.map((i) => (i.client_request_id === id ? { ...i, attempts: i.attempts + 1, lastError: error } : i))
      save()
    },
    reconcile(status) {
      const ids = reconcileWithStatus(items, status)
      if (ids.length) {
        const drop = new Set(ids)
        items = items.filter((i) => !drop.has(i.client_request_id))
        save()
      }
      return ids
    },
    clear() {
      items = []
      save()
    },
  }
}
