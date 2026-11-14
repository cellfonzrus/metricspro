// ── THE TARGET AXIS — one answer to "what is targetable", shared by every target surface ─────────
//
// Owner, 2026-10-10: *"the target needs to be set for the gross revenue per day / week / month or
// backward calculate what is needed to reach a certain level ... but i dont wnat them to be hard
// coded it should be a design effect"*
//
// THE DEFECT THIS CLOSES. The list of target categories was hard-coded in NINE places on
// 2026-10-11 — four in the backend, five here — each with its own spelling of the same four
// labels and units. So a tenant that declared "Package drop-offs" on Settings → Count Fields
// (mig 1072) could declare it every day and then find nowhere on any screen to set a target for
// it, because every screen's list ended at `accessories`. Fixing one screen would have been the
// patchwork the owner banned; nine copies is how they would have drifted anyway.
//
// WHERE THE FACT LIVES. `backend/app/modules/commcalc/targets_engine.target_axis` is the one home,
// served by `GET /commcalc/targets/{period}/axis`. This module is the browser's dereference of it,
// and `harness_target_axis_lock.py` fails the build if a screen grows its own list again.
import { useEffect, useState } from 'react'
import { api, ORG_ID } from '@/lib/client'

export type TargetUnit = 'count' | 'dollars'
/** Where a category's target physically lives — see the engine's `target_store`. */
export type TargetStore = 'column' | 'derived' | 'line'

export type TargetCat = {
  key: string
  label: string
  unit: TargetUnit
  store: TargetStore
  /** 'sales_feed' (counted from the POS feed) or 'declared' (typed on Submit Daily Closing). */
  source: 'sales_feed' | 'declared' | string
}

// The four built-in categories, for the moment BEFORE the axis has loaded and for the case where it
// cannot be loaded at all. A screen that rendered nothing until a fetch returned would be a
// regression for every existing tenant, so this is the floor — and it is the ONLY list of
// categories left in the frontend, which is what the build lock enforces. It must stay in step with
// the engine's BUILTIN_CATEGORIES / BUILTIN_LABELS / BUILTIN_UNITS; the lock checks that too.
export const BUILTIN_CATS: readonly TargetCat[] = [
  { key: 'activations', label: 'Activations', unit: 'count', store: 'column', source: 'sales_feed' },
  { key: 'upgrades', label: 'Upgrades', unit: 'count', store: 'column', source: 'sales_feed' },
  { key: 'byod', label: 'BYOD', unit: 'count', store: 'derived', source: 'sales_feed' },
  { key: 'accessories', label: 'Accessories', unit: 'dollars', store: 'column', source: 'sales_feed' },
] as const

/** The hint text the My Targets screen shows under a built-in's label. Declared categories have no
 *  hint: the admin's own label IS the explanation. */
export const BUILTIN_HINTS: Record<string, string> = {
  activations: 'premium + BYOD',
  upgrades: 'device upgrades',
  byod: 'KPI target',
  accessories: 'GP',
}

/** The grains a target can be asked at. The engine's arithmetic is monthly and a week or day is a
 *  window over it, which is why this is a display vocabulary and not a set of calculations. */
export const GRAINS = ['day', 'week', 'month'] as const
export type Grain = typeof GRAINS[number]

/** The axis, and whether asking for it FAILED. The two travel together on purpose: the fall back to
 *  the built-ins is right — a blank target screen would be a regression — but a screen that cannot
 *  tell the fall back from the tenant's real answer will show four categories and look correct.
 *
 *  That is exactly what happened (owner report 2026-11-14, index §CORE-44). The request was sent
 *  without the `/api/v1` prefix, so it reached the Next app, 404'd, and this catch answered with the
 *  built-ins for MONTHS. Every declared category was invisible on every target screen, no target for
 *  one could be typed, and `commcalc.target_line` held zero rows in the entire database. One
 *  character of address, and nothing anywhere said a word. A FAILED READ IS NOT AN ANSWER — the same
 *  rule the sign-in Guard already follows for an unreadable enforcement flag. */
export type TargetAxisResult = { cats: TargetCat[]; failed: boolean }

// One in-flight request and one cached answer per period, so five components on one screen do not
// make five identical calls. Keyed by period because a tenant can declare a category mid-month.
// A FAILURE IS NEVER CACHED: a blip must not become a session of wrong answers, and the next screen
// to mount asks again.
const cache = new Map<string, TargetCat[]>()
const inflight = new Map<string, Promise<TargetAxisResult>>()

export async function fetchTargetAxisResult(period: string): Promise<TargetAxisResult> {
  const key = period || ''
  const hit = cache.get(key)
  if (hit) return { cats: hit, failed: false }
  const live = inflight.get(key)
  if (live) return live
  const p = (async () => {
    try {
      // `/api/v1` IS the address: apiUrl() prefixes nothing, so a path without it asks the Next app
      // for a page of that name.
      const r = await api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/axis?org_id=${ORG_ID}`)
      const axis: TargetCat[] = Array.isArray(r?.axis) && r.axis.length ? r.axis : [...BUILTIN_CATS]
      cache.set(key, axis)
      return { cats: axis, failed: false }
    } catch {
      return { cats: [...BUILTIN_CATS], failed: true }
    } finally {
      inflight.delete(key)
    }
  })()
  inflight.set(key, p)
  return p
}

/** The categories alone, for a caller that has nowhere to say so. Prefer the hook. */
export async function fetchTargetAxis(period: string): Promise<TargetCat[]> {
  return (await fetchTargetAxisResult(period)).cats
}

/** THE hook every target surface uses. Returns the built-ins immediately, then the tenant's full
 *  axis once it arrives — so nothing ever renders an empty category list. `failed` is true when the
 *  categories on screen are that fall back rather than this tenant's own, so a screen whose JOB is
 *  setting targets can say so instead of quietly offering four. */
export function useTargetAxis(period: string): { cats: TargetCat[]; loaded: boolean; failed: boolean } {
  const [cats, setCats] = useState<TargetCat[]>(() => cache.get(period || '') || [...BUILTIN_CATS])
  const [loaded, setLoaded] = useState<boolean>(() => !!cache.get(period || ''))
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let alive = true
    fetchTargetAxisResult(period).then((a) => {
      if (!alive) return
      setCats(a.cats); setFailed(a.failed); setLoaded(true)
    })
    return () => { alive = false }
  }, [period])
  return { cats, loaded, failed }
}

/** The categories whose target is saved as a `commcalc.target_line` row rather than a column — the
 *  ones a settings screen renders in its declared section. */
export const declaredCats = (cats: TargetCat[]) => cats.filter((c) => c.store === 'line')
