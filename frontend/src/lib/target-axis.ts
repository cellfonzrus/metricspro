// ── THE TARGET AXIS — one answer to "what is targetable", shared by every target surface ─────────
//
// Owner, 2026-10-10: *"the target needs to be set for the gross revenue per day / week / month or
// backward calculate what is needed to reach a certain level ... but i dont wnat them to be hard
// coded it should be a design effect"*
//
// THE DEFECT THIS CLOSES. The list of target categories was hard-coded in SEVEN places on
// 2026-10-11 — four in the backend, three here — each with its own spelling of the same four
// labels and units. So a tenant that declared "Package drop-offs" on Settings → Count Fields
// (mig 1072) could declare it every day and then find nowhere on any screen to set a target for
// it, because every screen's list ended at `accessories`. Fixing one screen would have been the
// patchwork the owner banned; seven copies is how they would have drifted anyway.
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

// One in-flight request and one cached answer per period, so five components on one screen do not
// make five identical calls. Keyed by period because a tenant can declare a category mid-month.
const cache = new Map<string, TargetCat[]>()
const inflight = new Map<string, Promise<TargetCat[]>>()

export async function fetchTargetAxis(period: string): Promise<TargetCat[]> {
  const key = period || ''
  const hit = cache.get(key)
  if (hit) return hit
  const live = inflight.get(key)
  if (live) return live
  const p = (async () => {
    try {
      const r = await api(`/commcalc/targets/${encodeURIComponent(period)}/axis?org_id=${ORG_ID}`)
      const axis: TargetCat[] = Array.isArray(r?.axis) && r.axis.length ? r.axis : [...BUILTIN_CATS]
      cache.set(key, axis)
      return axis
    } catch {
      // The axis route being unreachable must not blank a target screen: fall back to the built-ins,
      // which is exactly what every screen showed before this module existed.
      return [...BUILTIN_CATS]
    } finally {
      inflight.delete(key)
    }
  })()
  inflight.set(key, p)
  return p
}

/** THE hook every target surface uses. Returns the built-ins immediately, then the tenant's full
 *  axis once it arrives — so nothing ever renders an empty category list. */
export function useTargetAxis(period: string): { cats: TargetCat[]; loaded: boolean } {
  const [cats, setCats] = useState<TargetCat[]>(() => cache.get(period || '') || [...BUILTIN_CATS])
  const [loaded, setLoaded] = useState<boolean>(() => !!cache.get(period || ''))
  useEffect(() => {
    let alive = true
    fetchTargetAxis(period).then((a) => { if (alive) { setCats(a); setLoaded(true) } })
    return () => { alive = false }
  }, [period])
  return { cats, loaded }
}

/** The categories whose target is saved as a `commcalc.target_line` row rather than a column — the
 *  ones a settings screen renders in its declared section. */
export const declaredCats = (cats: TargetCat[]) => cats.filter((c) => c.store === 'line')

/** Format a figure in its category's own unit. Passed the formatters rather than importing them, so
 *  this module stays free of render concerns and a caller keeps its own money formatting. */
export function catValue(unit: string, n: any, fmtMoney: (v: number) => string,
                         fmtNum: (v: number, dp?: number) => string): string {
  const v = Number(n) || 0
  return unit === 'dollars' ? fmtMoney(v) : fmtNum(v, 1)
}
