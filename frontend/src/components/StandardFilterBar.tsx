'use client'
// StandardFilterBar — the shared, universal report filter bar (RULE FIVE, §3d). One component renders the
// core set — period (month OR date-range) · store(s) · market · rep/employee(s) — all PICK-DON'T-TYPE
// (§3b, multi-select via EntityPicker) over ORG-SCOPED option sources, and drives the page + its exports.
//
// Option sources (any subset): pass `storeOptions`/`marketOptions`/`repOptions` from data the page already
// loaded (org-scoped by construction — use `optionsFromRows`), OR give `optionsUrl` (e.g.
// '/api/v1/core/filter-options') to fetch the org roster (stores from storeops.stores + store_mapping,
// reps from the employee roster). Options passed explicitly always win over the fetch.
//
// Filter state is OWNED by the page (a StandardFilterValue) so the page can both render filtered rows and
// hand the SAME filtered rows to its exporter (what-you-see-is-what-exports). Show only the controls that
// make sense for a surface via `show`; appending module-specific filters via `right` is allowed — the core
// set is never substituted (§3d).
import { useEffect, useMemo, useState } from 'react'
import EntityPicker, { type EntityOption } from '@/components/EntityPicker'
import { api } from '@/lib/client'
import type { StandardFilterValue } from '@/lib/standard-filters'
import { isStandardFilterActive } from '@/lib/standard-filters'

type OptIn = EntityOption[] | string[] | undefined
const toOpts = (o: OptIn): EntityOption[] =>
  !o ? [] : (o as any[]).map(x => (typeof x === 'string' ? { id: x, label: x } : x as EntityOption))

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const lbl: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }

export type StandardFilterBarProps = {
  value: StandardFilterValue
  onChange: (v: StandardFilterValue) => void
  /** Which core controls to render. Omit market/rep on surfaces where they have no meaning (document the
   *  deviation) — the doctrine allows applying the core set WHERE MEANINGFUL, never substituting it. */
  show?: { period?: boolean; stores?: boolean; markets?: boolean; reps?: boolean }
  periodMode?: 'month' | 'range' | 'none'
  /** Month options for month mode (org-scoped, e.g. the periods the data actually has). */
  periods?: string[]
  storeOptions?: OptIn
  marketOptions?: OptIn
  repOptions?: EntityOption[]
  /** Fetch stores/markets/reps from an org-scoped endpoint returning {stores,markets,reps}. Options props win. */
  optionsUrl?: string
  /** Appended module-specific controls (allowed; never replaces the core set). */
  right?: React.ReactNode
  storeLabel?: string
  marketLabel?: string
  repLabel?: string
}

export default function StandardFilterBar({
  value, onChange, show, periodMode = 'month', periods, storeOptions, marketOptions, repOptions,
  optionsUrl, right, storeLabel = 'Stores…', marketLabel = 'Markets…', repLabel = 'People…',
}: StandardFilterBarProps) {
  const s = { period: true, stores: true, markets: true, reps: true, ...(show || {}) }
  const [fetched, setFetched] = useState<{ stores?: EntityOption[]; markets?: EntityOption[]; reps?: EntityOption[] }>({})

  useEffect(() => {
    if (!optionsUrl) return
    let alive = true
    api(optionsUrl).then((d: any) => {
      if (!alive || !d) return
      const stores: EntityOption[] = (d.stores || []).map((x: any) =>
        typeof x === 'string' ? { id: x, label: x } : { id: x.store ?? x.id ?? x.address, label: x.store ?? x.label ?? x.address, sublabel: x.market || undefined })
      const markets: EntityOption[] = (d.markets || []).map((x: any) => (typeof x === 'string' ? { id: x, label: x } : x))
      const reps: EntityOption[] = (d.reps || []).map((x: any) =>
        typeof x === 'string' ? { id: x, label: x } : { id: x.id ?? x.name, label: x.label ?? x.name, sublabel: x.sublabel ?? x.email ?? undefined })
      setFetched({ stores, markets, reps })
    }).catch(() => {})
    return () => { alive = false }
  }, [optionsUrl])

  const storeOpts = useMemo(() => (storeOptions ? toOpts(storeOptions) : fetched.stores || []), [storeOptions, fetched.stores])
  const marketOpts = useMemo(() => (marketOptions ? toOpts(marketOptions) : fetched.markets || []), [marketOptions, fetched.markets])
  const repOpts = useMemo(() => (repOptions ?? fetched.reps ?? []), [repOptions, fetched.reps])

  const set = (patch: Partial<StandardFilterValue>) => onChange({ ...value, ...patch })
  const active = isStandardFilterActive(value, periodMode === 'range')

  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
      {s.period && periodMode === 'month' && (
        <label style={lbl}>Month
          {periods && periods.length > 0
            ? <select style={sel} value={value.period && value.period.length === 7 ? value.period : ''} onChange={e => set({ period: e.target.value })}>
                {periods.map(m => <option key={m} value={m}>{m}</option>)}
                {value.period && !periods.includes(value.period) && <option value={value.period}>{value.period}</option>}
              </select>
            : <input type="month" style={sel} value={value.period || ''} onChange={e => set({ period: e.target.value })} />}
        </label>
      )}
      {s.period && periodMode === 'range' && (
        <>
          <label style={lbl}>From <input type="date" style={sel} value={value.period || ''} onChange={e => set({ period: e.target.value })} /></label>
          <label style={lbl}>To <input type="date" style={sel} value={value.periodTo || ''} onChange={e => set({ periodTo: e.target.value })} /></label>
        </>
      )}
      {s.markets && marketOpts.length > 0 && (
        <EntityPicker multi options={marketOpts} value={value.markets} onChange={ids => set({ markets: ids })} placeholder={marketLabel} width={160} ariaLabel="Filter by market" />
      )}
      {s.stores && storeOpts.length > 0 && (
        <EntityPicker multi options={storeOpts} value={value.stores} onChange={ids => set({ stores: ids })} placeholder={storeLabel} width={180} ariaLabel="Filter by store" />
      )}
      {s.reps && repOpts.length > 0 && (
        <EntityPicker multi options={repOpts} value={value.reps} onChange={ids => set({ reps: ids })} placeholder={repLabel} width={190} ariaLabel="Filter by person" />
      )}
      {active && (
        <button className="btn btn-secondary" style={{ fontSize: 12 }}
          onClick={() => onChange({ period: periodMode === 'range' ? '' : value.period, periodTo: '', stores: [], markets: [], reps: [] })}>
          Clear filters
        </button>
      )}
      {right}
    </div>
  )
}
