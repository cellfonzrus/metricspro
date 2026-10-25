'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { useAuth } from '@/lib/auth-context'
import { usePosTerm } from '@/lib/report-labels'
import { MarketStorePicker, type StoreOpt } from '../_lib/MarketStorePicker'
import DrillDownPanel, { DrillTileLinks, useDrillTile, type DrillPanel, type DrillRow } from '@/components/DrillDownPanel'
import { narrowPanels, screenKey } from '@/lib/drill-narrow'

// Reconciliation sheet — every day's closing-vs-POS discrepancies for the month.
// BLOCK = cash short or credit over (these stop a rep from closing). FLAG = cash over / credit
// under / count mismatch (allowed but flagged). PENDING = POS sales not loaded / rep unmatched yet.
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const th: React.CSSProperties = { textAlign: 'left', padding: '7px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '7px 10px', borderTop: '1px solid var(--border)', fontSize: 13, whiteSpace: 'nowrap' }

const thisMonth = () => localToday().slice(0, 7)
const SEV: Record<string, { bg: string; fg: string; label: string }> = {
  block: { bg: 'var(--c-bg-fde8e8)', fg: 'var(--c-fg-b42318)', label: '⛔ Block' },
  flag: { bg: 'var(--c-bg-fef3e2)', fg: 'var(--c-fg-b45309)', label: '⚠️ Flag' },
  pending: { bg: 'var(--surface2)', fg: 'var(--text3)', label: '⏳ Pending' },
}

export default function ClosingReconPage() {
  const { user, permissions } = useAuth()
  const { pos } = usePosTerm()   // the tenant's POS name in copy (lib/report-labels.ts)
  const [period, setPeriod] = useState(thisMonth())
  const [market, setMarket] = useState('')
  const [tol, setTol] = useState('1')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<'all' | 'block' | 'flag' | 'pending'>('all')
  // OWNER DIRECTIVE 2026-08-04 (market->store cascade + checkbox picker): replaces the old single-
  // select native `<select>` store filter. `market` above stays the server-side scope AUTO-default
  // (unchanged); `fMarkets`/`fStores` are the new manual, editable, MULTI-select client-side filters —
  // this endpoint returns the whole period's rows once, so narrowing by market/store is client-side
  // the same way the direction checkboxes below already work.
  const [fMarkets, setFMarkets] = useState<string[]>([])
  const [fStores, setFStores] = useState<string[]>([])
  const [dateFilter, setDateFilter] = useState('')
  const [dirs, setDirs] = useState({ cash_over: true, cash_under: true, credit_over: true, credit_under: true })
  const [pStores, setPStores] = useState<any[]>([])

  useEffect(() => { if (user?.market && permissions?.scope === 'market') setMarket(user.market) }, [user, permissions])
  useEffect(() => { apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setPStores(Array.isArray(s) ? s : (s?.stores || []))).catch(() => {}) }, [])

  const load = useCallback(() => {
    if (!period) return
    setLoading(true)
    api(`/api/v1/closing/recon?period=${period}&tolerance=${tol || 1}${market ? `&market=${encodeURIComponent(market)}` : ''}`)
      .then(setData).catch(console.error).finally(() => setLoading(false))
  }, [period, market, tol])
  useEffect(() => { load() }, [load])

  const [charged, setCharged] = useState<Record<string, boolean>>({})
  async function chargeRow(e: any) {
    const k = `${e.date}|${e.rep}|${e.metric}`
    try {
      await api('/api/v1/commcalc/chargeback-review', { method: 'POST', body: JSON.stringify({
        source: 'closing_recon', severity: e.severity === 'block' ? 'critical' : 'warning', needs_review: true,
        store: e.store_address, store_code: e.store_code, period, occurred_date: e.date,
        suggested_rep: e.rep && e.rep !== '—' ? e.rep : '', amount: Math.abs(e.variance || 0),
        detail: `Closing ${e.metric}: ${e.reason || ''}`.trim(),
        dedupe_key: `closing:${e.date}:${e.store_code}:${e.rep}:${e.metric}`,
      }) })
      setCharged(c => ({ ...c, [k]: true }))
    } catch (err: any) { alert('Charge failed: ' + (err?.message || err)) }
  }

  // Memoised, not an inline `||`: two memos below take it as a dependency, and a fresh [] on every
  // render would re-narrow every drill panel each time.
  const errors = useMemo<any[]>(() => data?.errors || [], [data])
  // Market joined onto each error row from the org-scoped store roster (by code, else address) — the
  // same join shape the sibling recon pages already use. Feeds both the cascade widget's options AND
  // the actual store/market narrowing below.
  const marketByKey = useMemo(() => {
    const m: Record<string, string> = {}
    for (const st of pStores) {
      if (!st.market) continue
      for (const k of [st.store_code, st.store_address, st.address]) {
        const key = (k || '').trim().toLowerCase()
        if (key && !m[key]) m[key] = st.market
      }
    }
    return m
  }, [pStores])
  // Memoised because the selection below is memoised on it: an inline closure is a new value on
  // every render, which would re-narrow every drill panel each time (and is what the
  // preserve-manual-memoization rule objects to).
  const marketOf = useCallback(
    (e: any) => marketByKey[(e.store_code || '').trim().toLowerCase()] || marketByKey[(e.store_address || '').trim().toLowerCase()] || '',
    [marketByKey])
  const storesForCascade: StoreOpt[] = useMemo(() => {
    const seen = new Map<string, StoreOpt>()
    for (const e of errors) {
      const id = (e.store_address || e.store_code || '').trim()
      if (id && !seen.has(id)) seen.set(id, { id, label: id, market: marketOf(e) || null })
    }
    return [...seen.values()]
  }, [errors, marketOf])
  const dateOpts = Array.from(new Set(errors.map(e => e.date).filter(Boolean))).sort()
  // A money row's direction: cash short=under / cash over; credit over / credit under. Non-money rows null.
  const dirOf = useCallback((e: any): keyof typeof dirs | null => {
    if (e.variance == null) return null
    if (e.metric === 'cash') return e.variance < 0 ? 'cash_under' : 'cash_over'
    if (e.metric === 'credit') return e.variance > 0 ? 'credit_over' : 'credit_under'
    return null
  }, [])
  const fMarketsFold = useMemo(() => new Set(fMarkets.map(m => m.trim().toLowerCase())), [fMarkets])
  const fStoresFold = useMemo(() => new Set(fStores.map(s => s.trim().toLowerCase())), [fStores])
  // THE POPULATION THE COUNTS DESCRIBE — every filter on this page EXCEPT the severity one, which
  // is what the four tiles themselves select. Separating the two is the point: a tile that narrowed
  // to its own selection would read as the whole month's blocks the moment you clicked it.
  const base = useMemo(() => errors.filter(e => {
    const storeKey = (e.store_address || e.store_code || '').trim().toLowerCase()
    if (fStoresFold.size && !fStoresFold.has(storeKey)) return false
    if (fMarketsFold.size && !fMarketsFold.has((marketOf(e) || '').trim().toLowerCase())) return false
    if (dateFilter && e.date !== dateFilter) return false
    const d = dirOf(e)                       // direction checkboxes only hide the money rows they name
    if (d && !dirs[d]) return false
    return true
  }), [errors, fStoresFold, fMarketsFold, dateFilter, dirs, marketOf, dirOf])
  const shown = base.filter(e => filter === 'all' || e.severity === filter)

  // ── §CORE-17: the counts and the accusations behind them are ONE fold ─────────────────────────
  // These four tiles read the SERVER's whole-month summary while the table below them is filtered on
  // the client, so under any store, market, date or direction filter they described a different
  // population than the rows on screen — and there was no way at all from a count to the evidence.
  // Now each tile reads its number off its panel NARROWED to this selection (`lib/drill-narrow`,
  // §CORE-17.3d), and every row names the closing row its declared figure was filed on and the
  // sales feed its counterpart came from. A pending row names no feed, because none answered.
  const drills = useMemo<Record<string, DrillPanel>>(() => data?.drilldown || {}, [data])
  const selected = useMemo(() => new Set(base.map(e => screenKey({
    date: e.date, store_code: e.store_code, rep: e.rep, metric: e.metric,
  }))), [base])
  const panels = useMemo(() => narrowPanels(drills, (_r, k) => selected.has(k)), [drills, selected])
  const count = (k: string, fallback: () => number) =>
    panels[k] ? (panels[k].figure?.amount ?? 0) : fallback()
  const sev = (want: string) => () => base.filter(e => e.severity === want).length
  const drill = useDrillTile(panels)
  const dl = drill.dl
  const unpanelled = useMemo(() => Object.keys(drills).filter(k => !panels[k]), [drills, panels])
  const DRILL_COLUMNS = [
    { header: 'Rep', get: (r: DrillRow) => String(r.fields?.rep || '—'),
      sortValue: (r: DrillRow) => String(r.fields?.rep || '') },
    { header: 'Figure', get: (r: DrillRow) => String(r.fields?.metric || '—'),
      sortValue: (r: DrillRow) => String(r.fields?.metric || '') },
    { header: 'Declared', get: (r: DrillRow) => (r.fields?.declared == null ? '—' : fmt(Number(r.fields.declared))),
      sortValue: (r: DrillRow) => Number(r.fields?.declared ?? 0) },
    { header: pos, get: (r: DrillRow) => (r.fields?.counterpart == null ? '—' : fmt(Number(r.fields.counterpart))),
      sortValue: (r: DrillRow) => Number(r.fields?.counterpart ?? 0) },
    { header: 'Why', get: (r: DrillRow) => String(r.fields?.verdict || '—'),
      sortValue: (r: DrillRow) => String(r.fields?.verdict || '') },
  ]
  const money = (v: any) => v == null ? '—' : fmt(v)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔎 Closing Reconciliation</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Declared closing vs {pos} daily sales — every day&apos;s errors. <b>Block</b> = cash short / credit over (stops the rep closing); <b>Flag</b> = cash over / credit under / count mismatch.
          </p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <input type="month" style={sel} value={period} onChange={e => setPeriod(e.target.value)} />
        <MarketStorePicker
          stores={storesForCascade}
          selectedMarkets={fMarkets} onMarketsChange={setFMarkets}
          selectedStores={fStores} onStoresChange={setFStores}
        />
        <select style={sel} value={dateFilter} onChange={e => setDateFilter(e.target.value)}>
          <option value="">All dates</option>
          {dateOpts.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        {market && <span style={{ fontSize: 12, color: 'var(--text3)' }}>Market: {market}</span>}
        <label style={{ fontSize: 12, color: 'var(--text3)' }}>Tolerance $<input style={{ ...sel, width: 60, marginLeft: 4 }} value={tol} onChange={e => setTol(e.target.value)} /></label>
      </div>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', padding: '8px 12px', background: 'var(--surface2)', borderRadius: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)' }}>Discrepancy:</span>
        <DirCheck label="Cash over" on={dirs.cash_over} set={v => setDirs(d => ({ ...d, cash_over: v }))} />
        <DirCheck label="Cash under" on={dirs.cash_under} set={v => setDirs(d => ({ ...d, cash_under: v }))} />
        <DirCheck label="Credit over" on={dirs.credit_over} set={v => setDirs(d => ({ ...d, credit_over: v }))} />
        <DirCheck label="Credit under" on={dirs.credit_under} set={v => setDirs(d => ({ ...d, credit_under: v }))} />
        <div style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
          <button className="btn btn-secondary" style={{ fontSize: 11, padding: '3px 9px' }} onClick={() => setDirs({ cash_over: true, cash_under: true, credit_over: true, credit_under: true })}>All</button>
          <button className="btn btn-secondary" style={{ fontSize: 11, padding: '3px 9px' }} onClick={() => setDirs({ cash_over: false, cash_under: false, credit_over: false, credit_under: false })}>None</button>
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 12, marginBottom: 16 }}>
            <Tile label="Blocks" value={count('blocks', sev('block'))} tone="#b42318" onClick={() => setFilter('block')} active={filter === 'block'}
              drill={drill} drills={dl('blocks', 'the blocked closes')} />
            <Tile label="Flags" value={count('flags', sev('flag'))} tone="#b45309" onClick={() => setFilter('flag')} active={filter === 'flag'}
              drill={drill} drills={dl('flags', 'the flagged closes')} />
            <Tile label="Recon pending" value={count('pending', sev('pending'))} tone="var(--text3)" onClick={() => setFilter('pending')} active={filter === 'pending'}
              drill={drill} drills={dl('pending', 'the reps it cannot judge')} />
            <Tile label="All rows" value={count('total', () => base.length)} tone="var(--text2)" onClick={() => setFilter('all')} active={filter === 'all'}
              drill={drill} drills={dl('total', 'every row')} />
          </div>

          {/* ONE panel at a time, under the tiles — each row with the closing row and the feed behind it. */}
          {drill.open && (
            <div style={{ marginBottom: 16 }}>
              <DrillDownPanel panel={drill.panel} extraColumns={DRILL_COLUMNS}
                emptyNote="No row in this selection contributed to this count." />
            </div>
          )}
          {unpanelled.length > 0 && (
            <div style={{ fontSize: 11.5, color: 'var(--text3)', marginBottom: 12 }}>
              {unpanelled.length} count(s) above could not be opened for this selection — there are
              more rows behind them than the report serves at once, so narrowing them to your filter
              would leave rows out. Pick one month, fewer stores or a single date to open them.
            </div>
          )}

          {shown.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
              {errors.length === 0 ? 'No closing rows to reconcile for this month yet.' : 'No rows match this filter. 🎉'}
            </div>
          ) : (
            <div className="card table-wrapper" style={{ padding: 0 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Date', 'Store', 'Rep', 'Issue', 'Metric', 'Declared', pos, 'Variance', ''].map((h, i) =>
                    <th key={i} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {shown.map((e, i) => {
                    const sv = SEV[e.severity] || SEV.flag
                    return (
                      <tr key={i}>
                        <td style={td}>{e.date}</td>
                        <td style={td}>{e.store_address || e.store_code || '—'}</td>
                        <td style={td}>{e.rep || <span style={{ color: 'var(--text3)' }}>store</span>}</td>
                        <td style={td}><span style={{ background: sv.bg, color: sv.fg, padding: '2px 8px', borderRadius: 99, fontSize: 11, fontWeight: 600 }}>{sv.label}</span></td>
                        <td style={td}>{e.metric}</td>
                        <td style={td}>{e.metric === 'activations' || e.metric === 'upgrades' ? (e.declared ?? '—') : money(e.declared)}</td>
                        <td style={td}>{e.metric === 'activations' || e.metric === 'upgrades' ? (e.b2b ?? '—') : money(e.b2b)}</td>
                        <td style={{ ...td, fontWeight: 600, color: sv.fg }}>{e.variance == null ? '—' : (e.metric === 'activations' || e.metric === 'upgrades' ? `${e.variance > 0 ? '+' : ''}${e.variance}` : `${e.variance > 0 ? '+' : ''}${fmt(e.variance)}`)}</td>
                        <td style={{ ...td, color: 'var(--text3)', whiteSpace: 'normal', maxWidth: 280 }}>
                          {e.reason}
                          {e.rep && e.rep !== '—' && (e.metric === 'cash' || e.metric === 'credit') && e.severity !== 'pending' && (
                            charged[`${e.date}|${e.rep}|${e.metric}`]
                              ? <span style={{ color: 'var(--c-fg-16794a)', fontSize: 11, marginLeft: 6 }}>✓ charged</span>
                              : <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px', marginLeft: 6 }} onClick={() => chargeRow(e)} title="Send to the chargeback bucket for this rep">🔻 Charge</button>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}

const Tile = ({ label, value, tone, onClick, active, drills, drill }: {
  label: string; value: number; tone: string; onClick: () => void; active: boolean
  drills?: { key: string; label: string }[]
  drill?: { open: string | null; toggle: (k: string) => void }
}) => (
  <div className="card" style={{ padding: 14, cursor: 'pointer', borderColor: active ? tone : undefined, borderWidth: active ? 2 : 1, borderStyle: 'solid' }} onClick={onClick}>
    <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
    <div style={{ fontSize: 24, fontWeight: 700, marginTop: 2, color: tone }}>{value}</div>
    {/* The whole card is the severity filter, so the drill link stops the click from reaching it —
        otherwise opening the evidence would also change what the table is showing. */}
    {drill && drills && drills.length ? (
      <div onClick={ev => ev.stopPropagation()}>
        <DrillTileLinks drills={drills} open={drill.open} onToggle={drill.toggle} />
      </div>
    ) : null}
  </div>
)

const DirCheck = ({ label, on, set }: { label: string; on: boolean; set: (v: boolean) => void }) => (
  <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 5, cursor: 'pointer' }}>
    <input type="checkbox" checked={on} onChange={e => set(e.target.checked)} /> {label}
  </label>
)
