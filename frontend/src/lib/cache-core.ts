'use client'
// ── SHARED CLIENT-SIDE SWR CACHE for slow-changing lookups (nav-perf, 2026-08-04) ────────────────
//
// WHY THIS EXISTS (owner complaint 2026-08-04: "it takes some time to load the screen when moving
// from one menu to the other"). Every page in this app is a client component that fetches on mount,
// so a menu hop shows a spinner until its fetch set resolves. Measured against production, the
// FLOOR for a single round trip is ~170 ms and the rosters/config every page re-fetches cost
// 170–500 ms EACH, every single time:
//
//     /storeops/employees        407 ms (house) / 428 ms (lux)   — re-fetched by 25 pages
//     /storeops/stores           400 / 410 ms                    — 16 pages
//     /closing/stores            247 / 241 ms                    — 12 pages
//     /asset/filter-options      450 / 437 ms                    — 12 pages
//     /core/tenant-settings      327 / 341 ms                    —  7 pages
//     /commcalc/carriers         173 / 173 ms                    — 10 pages
//     /core/roles                334 / 333 ms                    —  4 pages
//
// None of those change between two menu clicks. This module caches them per (user, acting org) with
// stale-while-revalidate semantics, so the SECOND and later visits to a page render from memory at
// ~0 ms while a background refresh keeps the data honest.
//
// ── THE PRIVACY INVARIANT (the reason this file is written the way it is) ────────────────────────
// A cross-tenant cache hit is a data leak, not a bug. Three structural defences, all tested in
// frontend/proofs/prove_api_cache.mjs:
//
//   1. THE KEY IS NAMESPACED BY IDENTITY. Every entry lives under `${userId}::${orgId}::${path}`
//      (ids are UUIDs and API paths are URL-encoded, so `::` cannot be forged into a collision).
//      Two tenants can never collide on a key because the org id is a key component, and two users
//      of the SAME tenant can never collide either (span-scoped endpoints like /storeops/employees
//      return DIFFERENT rows per caller, so the user id is a key component too).
//   2. IDENTITY COMES FROM THE SERVER, NOT FROM localStorage. `setCacheIdentity()` is called by
//      AuthProvider with the org the BACKEND resolved (/core/me → the middleware-verified acting
//      org). A tampered localStorage value cannot widen the namespace.
//   3. NO IDENTITY ⇒ NO CACHE AT ALL. Before /core/me resolves, `apiCached()` degrades to a plain
//      `api()` call: nothing is read from the cache and nothing is written to it. There is no
//      "anonymous" bucket that a later identity could inherit.
//
// Plus two hard rules that keep it from ever becoming a correctness problem:
//   • MEMORY ONLY. Nothing is persisted to localStorage/sessionStorage/IndexedDB, so nothing
//      survives a tab close, a hard reload, or another user signing in on the same machine.
//   • IDENTITY CHANGE ⇒ FULL PURGE. Switching tenant or signing out bumps an epoch and empties the
//      store; every in-flight request started under the old identity is discarded on arrival.
//
// ── WHAT MAY BE CACHED ───────────────────────────────────────────────────────────────────────────
// Rosters, option lists, config and permission/nav payloads — things a user does not expect to
// change while they click around. NEVER a report's numbers, a money total, or anything a user
// refreshes a page specifically to re-read. Report payloads keep calling `api()` directly.
//
// ── BACKWARD COMPATIBILITY ───────────────────────────────────────────────────────────────────────
// This module is PURELY ADDITIVE. `api()` is untouched, so all ~200 existing pages behave exactly as
// before; a page opts in one call at a time by swapping `api(p)` → `apiCached(p)` or a `useEffect`
// for `useCachedApi(p)`.

import { api } from './client'

// ── Tunables ─────────────────────────────────────────────────────────────────────────────────────
/** Below this age an entry is FRESH: served from memory, no network at all. */
export const DEFAULT_TTL_MS = 60_000
/** Between ttl and maxAge an entry is STALE: served instantly, refreshed in the background. */
export const DEFAULT_MAX_MS = 15 * 60_000

export type CacheOpts = {
  /** Fresh window in ms (no network). Default 60 s. */
  ttlMs?: number
  /** Hard age limit in ms; past it the caller waits for the network. Default 15 min. */
  maxMs?: number
  /** Skip the cache for this call (always network) but still store the result. */
  force?: boolean
  /** Extra key material when the same path means different things (rare). */
  scope?: string
  /**
   * Store the result under THIS path's key instead of the requested one. For the rare case where a
   * user-initiated "re-check now" must add a server-side bypass parameter (`?fresh=1`) yet still
   * refresh the SAME cache entry the ordinary reads use — without it the bypass would populate a
   * second, parallel entry and the ordinary reads would keep serving the pre-fix answer.
   */
  cacheAs?: string
}

type Entry<T = any> = {
  data: T
  path: string               // the API path this entry answers (exact-match invalidation)
  at: number                 // ms epoch when `data` landed
  epoch: number              // identity epoch this entry was created under
  inflight?: Promise<T>      // shared promise: de-duplicates concurrent callers
  subs: Set<(e: Entry<T>) => void>
}

// ── Identity namespace ───────────────────────────────────────────────────────────────────────────
let _userId: string | null = null
let _orgId: string | null = null
let _epoch = 0
const store = new Map<string, Entry>()

/** True when a cache namespace is established. No identity ⇒ every call is a plain passthrough. */
export function cacheReady(): boolean { return !!_userId && !!_orgId }

/** The current namespace prefix, or null. Exported for tests/diagnostics only. */
export function cacheNamespace(): string | null {
  return cacheReady() ? `${_userId}::${_orgId}` : null
}

/**
 * Establish (or change) the cache identity. Called by AuthProvider once the BACKEND has resolved who
 * the caller is and which tenant they are acting as. Any change — different user, different acting
 * org, or sign-out (null, null) — bumps the epoch and PURGES the store, so an entry created for one
 * identity can never be read by another.
 */
export function setCacheIdentity(userId: string | null | undefined, orgId: string | null | undefined) {
  const u = userId || null
  const o = orgId || null
  if (u === _userId && o === _orgId) return
  _userId = u; _orgId = o
  _epoch++
  purgeAll()
}

/** Drop everything. Called on sign-out (via setCacheIdentity(null,null)) and available to callers. */
export function clearApiCache() { _epoch++; purgeAll() }

function purgeAll() {
  for (const e of store.values()) e.subs.clear()
  store.clear()
}

/**
 * Invalidate cached entries after a write. `match` is a substring of the path or a predicate over
 * the path. Entries with live subscribers are re-fetched immediately so open pages self-heal.
 */
export function invalidateApiCache(match: string | ((path: string) => boolean)) {
  const test = typeof match === 'function' ? match : (p: string) => p.includes(match)
  for (const [k, e] of Array.from(store.entries())) {
    if (!test(e.path)) continue
    if (e.subs.size) { void revalidate(k, e.path) }
    else store.delete(k)
  }
}

function keyFor(path: string, scope?: string): string | null {
  const ns = cacheNamespace()
  if (!ns) return null
  return scope ? `${ns}::${scope}::${path}` : `${ns}::${path}`
}

function notify(e: Entry) { for (const cb of Array.from(e.subs)) { try { cb(e) } catch { /* a listener must never break a fetch */ } } }

/**
 * Fetch `fetchPath` and store it under `key`. `cachePath` (default `fetchPath`) is what the entry
 * remembers as "its" path — used for background revalidation and for invalidateApiCache() matching.
 */
function revalidate<T>(key: string, fetchPath: string, cachePath?: string): Promise<T> {
  const path = cachePath ?? fetchPath
  const existing = store.get(key)
  if (existing?.inflight) return existing.inflight as Promise<T>
  const bornEpoch = _epoch
  const p = api(fetchPath).then((data: T) => {
    // Identity changed while this was in flight (tenant switch / sign-out) → discard, never store.
    if (bornEpoch !== _epoch) { store.delete(key); return data }
    const cur = store.get(key)
    const next: Entry<T> = { data, path, at: Date.now(), epoch: bornEpoch, subs: cur?.subs || new Set() }
    store.set(key, next)
    notify(next)
    return data
  }).catch((err) => {
    // NEVER cache a failure: drop the in-flight marker but keep any good data we already had, so a
    // transient backend blip degrades to "slightly stale" instead of "the page went blank".
    const cur = store.get(key)
    if (cur) { delete cur.inflight; if (cur.data === undefined) store.delete(key) }
    throw err
  })
  const cur = store.get(key)
  if (cur) { cur.inflight = p }
  else store.set(key, { data: undefined as any, path, at: 0, epoch: bornEpoch, inflight: p, subs: new Set() })
  return p
}

/** A cache entry that may be served right now, plus whether it needs a background refresh. */
function peek(key: string | null, ttlMs: number, maxMs: number): { data: any; stale: boolean } | null {
  if (!key) return null
  const e = store.get(key)
  if (!e || e.data === undefined || e.epoch !== _epoch) return null
  const age = Date.now() - e.at
  if (age >= maxMs) return null
  return { data: e.data, stale: age >= ttlMs }
}

/**
 * Cached GET with stale-while-revalidate.
 *   fresh  (age < ttl)          → resolves from memory, no network
 *   stale  (ttl ≤ age < maxAge) → resolves from memory IMMEDIATELY, refreshes in the background
 *   miss / expired              → awaits the network (concurrent callers share one request)
 * Falls back to a plain `api()` call whenever no identity is established.
 */
export async function apiCached<T = any>(path: string, opts: CacheOpts = {}): Promise<T> {
  const ttlMs = opts.ttlMs ?? DEFAULT_TTL_MS
  const maxMs = Math.max(opts.maxMs ?? DEFAULT_MAX_MS, ttlMs)
  const cachePath = opts.cacheAs ?? path
  const key = keyFor(cachePath, opts.scope)
  if (!key) return api(path)                                   // no identity ⇒ never touch the cache
  if (!opts.force) {
    const hit = peek(key, ttlMs, maxMs)
    if (hit) {
      // background refresh of a stale entry always uses the CANONICAL path, never a one-off bypass
      if (hit.stale) void revalidate(key, cachePath).catch(() => {})
      return hit.data as T
    }
  }
  return revalidate<T>(key, path, cachePath)
}

// ── INTERNAL surface for the React binding (src/lib/cache.ts) ───────────────────────────────────
// Exported so `useCachedApi` can render synchronously from the store and re-render when a
// background revalidation lands, WITHOUT this engine importing React. Not part of the public API —
// pages use `apiCached()` / `useCachedApi()`.

/** Synchronous peek by PATH (not key). Returns null on a miss, an expired entry, or no identity. */
export function _peek(path: string | null, ttlMs: number, maxMs: number, scope?: string) {
  if (!path) return null
  return peek(keyFor(path, scope), ttlMs, maxMs)
}

/** Subscribe to revalidations of `path`. No-op (returns a no-op unsubscribe) when there's no identity. */
export function _subscribe(path: string | null, scope: string | undefined, cb: (data: any) => void): () => void {
  const key = path ? keyFor(path, scope) : null
  if (!key) return () => {}
  let entry = store.get(key)
  if (!entry) { entry = { data: undefined as any, path: path!, at: 0, epoch: _epoch, subs: new Set() }; store.set(key, entry) }
  const wrapped = (e: Entry) => cb(e.data)
  entry.subs.add(wrapped)
  return () => { entry!.subs.delete(wrapped) }
}

/** Diagnostics/tests only: how many entries are held right now. */
export function _size(): number { return store.size }
/** Diagnostics/tests only: the raw key list (namespaced), for asserting isolation. */
export function _keys(): string[] { return Array.from(store.keys()) }
