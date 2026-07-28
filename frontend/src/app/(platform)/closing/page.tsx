'use client'
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import StandardFilterBar from '@/components/StandardFilterBar'
import type { EntityOption } from '@/components/EntityPicker'
import type { StandardFilterValue } from '@/lib/standard-filters'
import ReportShell from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import SubmissionsTable, { monthStart } from './_lib/SubmissionsTable'

const csv = (a: string[]) => (a.length ? a.join(',') : undefined)

export default function ClosingDashboard() {
  const { user, permissions } = useAuth()
  // RULE FIVE (§3d), OWNER DIRECTIVE 2026-07-28 (same-day follow-up): ONE standardized filter bar
  // (date-range + store(s)/market(s)/rep(s)) drives the tiles AND all three tabs — By-store/By-rep
  // used to have only a month + market select while "All submissions" already had the full bar; that
  // asymmetry is fixed by lifting the filter state here and passing it down into <SubmissionsTable>.
  const [filt, setFilt] = useState<StandardFilterValue>(() => ({ period: monthStart(), periodTo: localToday(), stores: [], markets: [], reps: [] }))
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'detail' | 'store' | 'rep'>('detail')
  const [readiness, setReadiness] = useState<any>(null)   // self-diagnostic (2026-07-16), best-effort
  // Anti-clobber: only the LATEST in-flight rollup request may land (a fast filter change used to let
  // a slower, stale response overwrite a newer one — the timeclock last-response-wins race class).
  const reqRef = useRef(0)

  useEffect(() => {
    const mkt = user?.market
    if (mkt && permissions?.scope === 'market') setFilt(f => (f.markets.length ? f : { ...f, markets: [mkt] }))
  }, [user, permissions])

  // Canonical, org-scoped option sources (pick-don't-type §3b) — NEVER derived from the loaded rollup
  // response (the original bug: an empty/filtered rollup meant empty market options, a circular trap).
  const [pStores, setPStores] = useState<any[]>([])
  const [pEmps, setPEmps] = useState<any[]>([])
  useEffect(() => {
    api('/api/v1/closing/stores').then((s: any) => setPStores(Array.isArray(s) ? s : [])).catch(() => {})
    api('/api/v1/storeops/employees?all_company=true').then((r: any) => setPEmps(Array.isArray(r) ? r : (r?.employees || []))).catch(() => {})
  }, [])
  const storeOptions: EntityOption[] = useMemo(
    () => pStores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, sublabel: s.market || undefined })),
    [pStores])
  const marketOptions: EntityOption[] = useMemo(() => {
    const real = Array.from(new Set(pStores.map((s: any) => s.market).filter(Boolean))).sort()
    return [...real.map((m: string) => ({ id: m, label: m })), { id: '(no market)', label: '(no market)' }]
  }, [pStores])
  const repOptions: EntityOption[] = useMemo(
    () => pEmps.filter((e: any) => (e.name || '').trim()).map((e: any) => ({ id: e.name, label: e.name, sublabel: e.email || undefined })),
    [pEmps])

  const load = useCallback(() => {
    if (!filt.period) return
    const myReq = ++reqRef.current
    setLoading(true)
    const qs = new URLSearchParams()
    qs.set('date_from', filt.period)
    qs.set('date_to', filt.periodTo || filt.period)
    const s = csv(filt.stores); if (s) qs.set('stores', s)
    const m = csv(filt.markets); if (m) qs.set('markets', m)
    const r = csv(filt.reps); if (r) qs.set('reps', r)
    api(`/api/v1/closing/rollup?${qs.toString()}`)
      .then(d => { if (reqRef.current === myReq) setData(d) })
      .catch(console.error)
      .finally(() => { if (reqRef.current === myReq) setLoading(false) })
  }, [filt.period, filt.periodTo, filt.stores, filt.markets, filt.reps])
  useEffect(() => { load() }, [load])
  // Surface config/data gaps (no stores mapped, no B2B sales source, no X-report ever, module not
  // entitled) right on the dashboard instead of letting empty tiles/recon speak for themselves.
  useEffect(() => { api('/api/v1/closing/readiness').then(setReadiness).catch(() => {}) }, [])

  const t = data?.totals || {}
  const byStore: any[] = data?.by_store || []
  const byRep: any[] = data?.by_rep || []
  const cashTotal = (r: any) => (r.store_cash || 0) + (r.epay_cash || 0)
  const cardTotal = (r: any) => (r.store_cc || 0) + (r.epay_cc || 0)
  const cov = data ? `${data.verified_keys}/${data.submitted_keys}` : '—'

  const storeColumns: ExportColumn[] = useMemo(() => [
    { header: 'Store', field: 'store_address', role: 'store', get: (r: any) => r.store_address || r.store_name || '—' },
    { header: 'Market', field: 'market', get: (r: any) => r.market },
    { header: 'Days', field: 'days', type: 'number', get: (r: any) => r.days },
    { header: 'Cash $', field: 'cash', money: true, get: (r: any) => cashTotal(r) },
    { header: 'Credit $', field: 'credit', money: true, get: (r: any) => cardTotal(r) },
    { header: 'Accessory $', field: 'acc_sale', money: true, get: (r: any) => r.acc_sale },
    { header: 'Other $', field: 'other_account', money: true, get: (r: any) => r.other_account },
    { header: 'Upgrades #', field: 'upgrade_count', type: 'number', get: (r: any) => r.upgrade_count },
    { header: 'New Lines #', field: 'new_line_count', type: 'number', get: (r: any) => r.new_line_count },
    { header: 'Postpaid #', field: 'postpaid_count', type: 'number', get: (r: any) => r.postpaid_count },
    { header: 'Submissions #', field: 'rows', type: 'number', get: (r: any) => r.rows },
  ], [])

  const repColumns: ExportColumn[] = useMemo(() => [
    { header: 'Rep', field: 'employee_name', role: 'rep', get: (r: any) => r.employee_name || '—' },
    { header: 'Store', field: 'store_address', role: 'store', get: (r: any) => r.store_address || '—' },
    { header: 'Market', field: 'market', get: (r: any) => r.market },
    { header: 'Days', field: 'days', type: 'number', get: (r: any) => r.days },
    { header: 'Cash $', field: 'cash', money: true, get: (r: any) => cashTotal(r) },
    { header: 'Credit $', field: 'credit', money: true, get: (r: any) => cardTotal(r) },
    { header: 'Accessory $', field: 'acc_sale', money: true, get: (r: any) => r.acc_sale },
    { header: 'Other $', field: 'other_account', money: true, get: (r: any) => r.other_account },
    { header: 'Upgrades #', field: 'upgrade_count', type: 'number', get: (r: any) => r.upgrade_count },
    { header: 'New Lines #', field: 'new_line_count', type: 'number', get: (r: any) => r.new_line_count },
    { header: 'Postpaid #', field: 'postpaid_count', type: 'number', get: (r: any) => r.postpaid_count },
    { header: 'Submissions #', field: 'rows', type: 'number', get: (r: any) => r.rows },
  ], [])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Daily Closing</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Closing summaries by store and by rep, with DM verification coverage.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Link href="/closing/submit" className="btn btn-primary" style={{ fontSize: 13 }}>➕ Submit closing</Link>
          <Link href="/closing/verify" className="btn btn-secondary" style={{ fontSize: 13 }}>✅ DM verify</Link>
          <Link href="/closing/cash-position" className="btn btn-secondary" style={{ fontSize: 13 }}>💰 Cash Position</Link>
          <Link href="/closing/duplicates" className="btn btn-secondary" style={{ fontSize: 13 }}>🧾 Duplicates</Link>
          <Link href="/closing/count-config" className="btn btn-secondary" style={{ fontSize: 13 }}>🔢 Count fields</Link>
          <Link href="/closing/readiness" className="btn btn-secondary" style={{ fontSize: 13 }}>🩺 Readiness</Link>
        </div>
      </div>

      {readiness && !readiness.ok && (
        <Link href="/closing/readiness" className="card" style={{ display: 'block', padding: 12, marginBottom: 14, background: '#fdeaea', border: '1px solid #f3b4b4', textDecoration: 'none', color: 'inherit' }}>
          🔴 <b>{readiness.issues.filter((i: any) => i.severity === 'critical').length} critical wiring gap(s)</b> found for this tenant — {readiness.issues.find((i: any) => i.severity === 'critical')?.message} <span style={{ textDecoration: 'underline' }}>See all →</span>
        </Link>
      )}

      {/* Filters — RULE FIVE core set (date-range + store(s)/market(s)/rep(s)), canonical org-scoped
          option sources. Drives the tiles + all 3 tabs below (including "All submissions", which no
          longer renders its own competing bar — see SubmissionsTable's filterValue prop). */}
      <StandardFilterBar
        value={filt} onChange={setFilt}
        periodMode="range"
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
        storeLabel="Stores…" marketLabel="Markets…" repLabel="Employees…"
      />

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          {/* Summary tiles */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 12, marginBottom: 18 }}>
            <Tile label="Cash collected" value={fmt(cashTotal(t))} />
            <Tile label="Credit collected" value={fmt(cardTotal(t))} />
            <Tile label="Accessory sales" value={fmt(t.acc_sale)} />
            <Tile label="Other (Zelle/CashApp)" value={fmt(t.other_account)} />
            <Tile label="Activations" value={`${(t.new_line_count || 0) + (t.postpaid_count || 0)}`} sub={`${t.new_line_count || 0} new · ${t.postpaid_count || 0} postpaid`} />
            <Tile label="Upgrades" value={`${t.upgrade_count || 0}`} />
            <Tile label="Rep submissions" value={`${t.rows || 0}`} sub={`${t.days || 0} day(s)`} />
            <Tile label="DM verified" value={cov} sub="store-days verified" />
          </div>

          {/* Tabs */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
            {(['detail', 'store', 'rep'] as const).map(x => (
              <button key={x} className={`btn ${tab === x ? 'btn-primary' : 'btn-secondary'}`} style={{ fontSize: 13 }} onClick={() => setTab(x)}>
                {x === 'detail' ? '🧾 All submissions' : x === 'store' ? '🏬 By store' : '🧑 By rep'}
              </button>
            ))}
          </div>

          {tab === 'detail' ? (
            <SubmissionsTable filterValue={filt} onFilterChange={setFilt}
              storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions} />
          ) : tab === 'store' ? (
            byStore.length === 0 ? (
              <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>No closing rows for this range yet.</div>
            ) : (
              <ReportShell title="Daily Closing — By Store" subtitle={`${filt.period} → ${filt.periodTo}`}
                filename={`daily-closing-by-store_${filt.period}_${filt.periodTo}`}
                columns={storeColumns} rows={byStore} stickyHeader totals />
            )
          ) : (
            byRep.length === 0 ? (
              <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>No rep submissions for this range yet.</div>
            ) : (
              <ReportShell title="Daily Closing — By Rep" subtitle={`${filt.period} → ${filt.periodTo}`}
                filename={`daily-closing-by-rep_${filt.period}_${filt.periodTo}`}
                columns={repColumns} rows={byRep} stickyHeader totals />
            )
          )}
        </>
      )}
    </div>
  )
}

const Tile = ({ label, value, sub }: { label: string; value: string; sub?: string }) => (
  <div className="card" style={{ padding: 14 }}>
    <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
  </div>
)
