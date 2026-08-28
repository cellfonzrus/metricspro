import AsyncStorage from '@react-native-async-storage/async-storage'

import { api, ApiError, AuthError } from '@/api/client'
import { getOnline, onConnectivityChange } from './net'

// ── Offline mutation queue ─────────────────────────────────────────────────────────────────────
// Frontline actions must survive a dead store network. A clock-in the rep taps at 9:00 with no
// signal cannot be silently lost — it is enqueued here, persisted to disk, and replayed FIFO the
// moment connectivity returns. The same applies to POS sales and CRM activity logs.
//
// Design:
//   • FIFO, persisted to AsyncStorage (the *contents* aren't secret — a clock-in body is a store
//     code + a timestamp; the auth token is attached fresh at replay time from SecureStore, never
//     stored in the queue).
//   • Replay stops on the first network error (status 0) or auth error — still offline / signed out,
//     try again later. A 4xx/5xx business error (e.g. "already clocked in") is terminal for that
//     item: it moves to a `failed` list the UI can surface, and the queue continues.
//   • Idempotency: each item carries a client-generated `clientId`; handlers that hit money/attendance
//     paths should forward it so a double-replay is deduped server-side where supported. (The backend
//     clock-in already guards against a second open entry; checkout is a single atomic RPC.)
export type QueuedMethod = 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export type QueuedMutation = {
  id: string
  clientId: string
  kind: string // e.g. 'timeclock.clock-in' — for UI grouping / dedupe
  label: string // human summary for the "pending sync" list
  method: QueuedMethod
  path: string
  body?: unknown
  createdAt: number
  attempts: number
  lastError?: string
}

export type FailedMutation = QueuedMutation & { failedAt: number }

const PENDING_KEY = 'mp_offline_queue_v1'
const FAILED_KEY = 'mp_offline_failed_v1'

type State = { pending: QueuedMutation[]; failed: FailedMutation[] }
let state: State = { pending: [], failed: [] }
let loaded = false
let flushing = false

const subscribers = new Set<(s: State) => void>()
function notify() {
  const snapshot = { pending: [...state.pending], failed: [...state.failed] }
  for (const cb of Array.from(subscribers)) {
    try {
      cb(snapshot)
    } catch {
      /* ignore */
    }
  }
}

export function subscribeQueue(cb: (s: State) => void): () => void {
  subscribers.add(cb)
  if (loaded) cb({ pending: [...state.pending], failed: [...state.failed] })
  return () => subscribers.delete(cb)
}

async function persist() {
  await Promise.all([
    AsyncStorage.setItem(PENDING_KEY, JSON.stringify(state.pending)),
    AsyncStorage.setItem(FAILED_KEY, JSON.stringify(state.failed)),
  ]).catch(() => {})
}

export async function loadQueue(): Promise<void> {
  if (loaded) return
  try {
    const [p, f] = await Promise.all([
      AsyncStorage.getItem(PENDING_KEY),
      AsyncStorage.getItem(FAILED_KEY),
    ])
    state.pending = p ? JSON.parse(p) : []
    state.failed = f ? JSON.parse(f) : []
  } catch {
    state = { pending: [], failed: [] }
  }
  loaded = true
  notify()
  // Flush automatically whenever we come back online.
  onConnectivityChange((online) => {
    if (online) void flushQueue()
  })
}

function uid(): string {
  // Not crypto — just a unique-enough id for a local queue entry.
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

/**
 * A fresh client id for an attendance/money mutation, minted BEFORE the online attempt so the very
 * first send and every durable replay carry the SAME `client_request_id` — the backend dedupes on it,
 * so a saturation timeout that actually landed server-side can't become a double punch.
 */
export function newClientId(): string {
  return uid()
}

/**
 * Enqueue a mutation for guaranteed eventual delivery. Returns the queued item. If online, a flush
 * is kicked off immediately (so the common case is "sends right away, just via the durable path").
 */
export async function enqueue(input: {
  kind: string
  label: string
  method: QueuedMethod
  path: string
  body?: unknown
  // Optional stable id. When the caller already attempted the mutation online with a client_request_id
  // (see clockInDurable), it passes that same id here so the durable retry stays idempotent with the
  // online attempt. Omitted → a fresh id is minted.
  clientId?: string
}): Promise<QueuedMutation> {
  await loadQueue()
  const { clientId, ...rest } = input
  const item: QueuedMutation = {
    id: uid(),
    clientId: clientId || uid(),
    attempts: 0,
    createdAt: Date.now(),
    ...rest,
  }
  state.pending.push(item)
  await persist()
  notify()
  if (getOnline()) void flushQueue()
  return item
}

// Forward the item's stable `clientId` to the server as `client_request_id` on the attendance/money
// paths that dedupe on it (timeclock). It was generated per item but never sent — sending it is what
// makes a FIFO replay (or a saturation-timeout retry) idempotent: one punch, not two. Other kinds'
// bodies are sent unchanged so a stray field can't 422 an endpoint that doesn't expect it.
function replayBody(item: QueuedMutation): unknown {
  if (!item.kind.startsWith('timeclock.')) return item.body
  const b = item.body && typeof item.body === 'object' ? (item.body as Record<string, unknown>) : {}
  return { ...b, client_request_id: item.clientId }
}

async function replay(item: QueuedMutation): Promise<unknown> {
  const body = replayBody(item)
  switch (item.method) {
    case 'POST':
      return api.post(item.path, body)
    case 'PUT':
      return api.put(item.path, body)
    case 'PATCH':
      return api.patch(item.path, body)
    case 'DELETE':
      return api.del(item.path)
  }
}

/**
 * Attempt to send everything pending, oldest first. Safe to call concurrently (guarded). Stops early
 * on a network/auth error, leaving the rest for the next attempt.
 */
export async function flushQueue(): Promise<void> {
  await loadQueue()
  if (flushing || !getOnline() || state.pending.length === 0) return
  flushing = true
  try {
    while (state.pending.length > 0 && getOnline()) {
      const item = state.pending[0]
      item.attempts += 1
      try {
        await replay(item)
        state.pending.shift() // success → drop it
        await persist()
        notify()
      } catch (e) {
        if (e instanceof AuthError) {
          item.lastError = 'Signed out — will retry after sign-in'
          break // don't burn attempts while unauthenticated
        }
        if (e instanceof ApiError && e.status === 0) {
          item.lastError = 'Offline — will retry'
          break // network died mid-flush
        }
        // Terminal business error (4xx/5xx): move to failed for user review, keep going.
        const failed: FailedMutation = {
          ...item,
          failedAt: Date.now(),
          lastError: e instanceof Error ? e.message : String(e),
        }
        state.failed.unshift(failed)
        state.pending.shift()
        await persist()
        notify()
      }
    }
  } finally {
    flushing = false
  }
}

export async function discardFailed(id: string): Promise<void> {
  state.failed = state.failed.filter((f) => f.id !== id)
  await persist()
  notify()
}

/** Re-queue a previously-failed item for another try. */
export async function retryFailed(id: string): Promise<void> {
  const f = state.failed.find((x) => x.id === id)
  if (!f) return
  state.failed = state.failed.filter((x) => x.id !== id)
  const { failedAt, ...rest } = f
  state.pending.push({ ...rest, attempts: 0, lastError: undefined })
  await persist()
  notify()
  if (getOnline()) void flushQueue()
}

export function getQueueSnapshot(): State {
  return { pending: [...state.pending], failed: [...state.failed] }
}
