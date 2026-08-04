'use client'
// ── React binding for the shared SWR cache (nav-perf 2026-08-04) ─────────────────────────────────
// The engine itself lives in `cache-core.ts` with NO React import, so the proof harness
// (frontend/prove_api_cache.mjs) can transpile and execute the REAL keying/SWR/purge code rather
// than a re-implementation of it. This file is the thin React surface plus a re-export of the
// engine, so a page only ever imports from '@/lib/cache'.
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiCached, DEFAULT_TTL_MS, DEFAULT_MAX_MS, _peek, _subscribe, type CacheOpts } from './cache-core'

export * from './cache-core'

export type CachedState<T> = {
  data: T | undefined
  error: Error | null
  /** true only when there is NOTHING to show yet — a cache hit never renders a spinner. */
  loading: boolean
  /** true while a background refresh of already-rendered data is in flight. */
  validating: boolean
  refresh: () => Promise<T | undefined>
}

/**
 * Hook form. Renders from cache synchronously on mount (no spinner, no flash) and revalidates in the
 * background. Pass `path = null` to stay idle (conditional fetches).
 */
export function useCachedApi<T = any>(path: string | null, opts: CacheOpts = {}): CachedState<T> {
  const ttlMs = opts.ttlMs ?? DEFAULT_TTL_MS
  const maxMs = Math.max(opts.maxMs ?? DEFAULT_MAX_MS, ttlMs)
  const scope = opts.scope
  const initial = _peek(path, ttlMs, maxMs, scope)

  const [data, setData] = useState<T | undefined>(initial?.data)
  const [error, setError] = useState<Error | null>(null)
  const [validating, setValidating] = useState(false)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const run = useCallback(async (force: boolean) => {
    if (!path) return undefined
    setValidating(true)
    try {
      const d = await apiCached<T>(path, { ttlMs, maxMs, scope, force })
      if (alive.current) { setData(d); setError(null) }
      return d
    } catch (e: any) {
      if (alive.current) setError(e instanceof Error ? e : new Error(String(e)))
      return undefined
    } finally {
      if (alive.current) setValidating(false)
    }
  }, [path, ttlMs, maxMs, scope])

  // Re-render when a background revalidation lands for this path (even one started by another page).
  useEffect(() => _subscribe(path, scope, (d) => { if (alive.current) setData(d) }), [path, scope])

  useEffect(() => {
    if (!path) return
    const hit = _peek(path, ttlMs, maxMs, scope)
    if (hit) { setData(hit.data); if (!hit.stale) return }      // fresh ⇒ nothing to do
    void run(false)
  }, [path, scope, ttlMs, maxMs, run])

  return { data, error, loading: data === undefined && !error, validating, refresh: () => run(true) }
}

// ── Named TTL profiles ───────────────────────────────────────────────────────────────────────────
// So call sites read as intent, not as magic numbers, and one place tunes them all.
/** Rosters, stores, markets, carriers, option lists — safe to hold for a few minutes. */
export const LOOKUP: CacheOpts = { ttlMs: 5 * 60_000, maxMs: 30 * 60_000 }
/** Tenant/role/permission config — changes rarely, but a wrong value is user-visible. */
export const CONFIG: CacheOpts = { ttlMs: 2 * 60_000, maxMs: 15 * 60_000 }
/** Status-ish payloads that should feel live but need not be re-fetched on every menu hop. */
export const STATUS: CacheOpts = { ttlMs: 45_000, maxMs: 5 * 60_000 }
