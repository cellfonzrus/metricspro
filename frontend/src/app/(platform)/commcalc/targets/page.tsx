'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID, fmt, fmtN, localToday } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MultiSelect } from '@/lib/multiselect'

const CATS = [
  { key: 'activations', label: 'Activations', unit: 'count' },
  { key: 'upgrades', label: 'Upgrades', unit: 'count' },
  { key: 'byod', label: 'BYOD', unit: 'count' },
  { key: 'accessories', label: 'Accessories', unit: 'dollars' },
] as const

type CatKey = typeof CATS[number]['key']

function val(unit: string, n: number) {
  return unit === 'dollars' ? fmt(n || 0) : fmtN(n || 0, 1)
}

interface CatMetrics {
  unit: string; monthly: number; achieved_mtd: number; need: number
  base_today: number; today_target: number; pace: number; open_days_left: number
}
interface CalDay {
  date: string; hours: number; is_today: boolean; is_past: boolean
  cats: Record<string, { base: number; achieved: number | null }>
}
interface ConvT {
  boxes: number; billpays: number; rate: number; target: number
  meets_target: boolean; below_store?: boolean
}
interface CalResp {
  period: string; scope: string; store_code: string; rep: string | null
  scheduled_hours_total: number; open_days_total: number; today: string
  has_schedule: boolean; projected_open_days?: number
  categories: Record<string, CatMetrics>; calendar: CalDay[]; reps: string[]
  conversion?: { store: ConvT; rep?: ConvT }
}

function convColor(c?: ConvT) {
  if (!c) return 'var(--text3)'
  return c.rate >= c.target ? 'var(--green)' : '#dc2626'
}

export default function DailyTargetsPage() {
  const { period } = usePeriod()
  const [summary, setSummary] = useState<any[]>([])
  const [loadingSum, setLoadingSum] = useState(true)
  const [storeCode, setStoreCode] = useState('')
  const [scope, setScope] = useState<'store' | 'rep'>('store')
  const [rep, setRep] = useState('')
  const [detail, setDetail] = useState<CalResp | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [calCat, setCalCat] = useState<CatKey>('activations')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [freshness, setFreshness] = useState<{ daily?: any; dlar?: any } | null>(null)
  // RULE FIVE standardized filters — store(s) / market(s) / rep(s), applied SERVER-SIDE to the summary.
  const [selStores, setSelStores] = useState<string[]>([])
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [selReps, setSelReps] = useState<string[]>([])
  const [filterOpts, setFilterOpts] = useState<{ stores: any[]; markets: string[]; reps: string[] }>({ stores: [], markets: [], reps: [] })

  useEffect(() => { loadSummary() }, [period, selStores, selMarkets, selReps])
  useEffect(() => { if (storeCode) loadDetail() }, [storeCode, scope, rep, period])
  useEffect(() => { loadFreshness() }, [period])

  async function loadFreshness() {
    try {
      const [hist, dlar] = await Promise.all([
        api(`/api/v1/commcalc/upload/history?org_id=${ORG_ID}&period=${encodeURIComponent(period)}`).catch(() => []),
        api(`/api/v1/commcalc/dlar/sweep/config?org_id=${ORG_ID}`).catch(() => null),
      ])
      const rows = Array.isArray(hist) ? hist : []
      const daily = rows.find((r: any) => r.file_type === 'daily_sales') || rows.find((r: any) => r.file_type === 'sales')
      setFreshness({ daily, dlar })
    } catch { setFreshness(null) }
  }

  async function loadSummary() {
    setLoadingSum(true)
    try {
      const qs = new URLSearchParams()
      qs.set('org_id', ORG_ID); qs.set('today', localToday())
      selStores.forEach((s) => qs.append('stores', s))
      selMarkets.forEach((s) => qs.append('markets', s))
      selReps.forEach((s) => qs.append('reps', s))
      const d = await api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/summary?${qs.toString()}`)
      setSummary(d.stores || [])
      setFilterOpts(d.filters || { stores: [], markets: [], reps: [] })
      // keep the detail panel in sync with the filter — if the selected store is filtered out, jump to the first shown.
      if (d.stores?.length && !d.stores.some((s: any) => s.store_code === storeCode)) setStoreCode(d.stores[0].store_code)
    } catch (e) { console.error(e) }
    setLoadingSum(false)
  }

  async function loadDetail() {
    setLoadingDetail(true)
    try {
      const q = `scope=${scope}&store_code=${encodeURIComponent(storeCode)}${scope === 'rep' && rep ? `&rep=${encodeURIComponent(rep)}` : ''}&org_id=${ORG_ID}&today=${localToday()}`
      const d: CalResp = await api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/calendar?${q}`)
      setDetail(d)
      if (scope === 'rep' && !rep && d.reps?.length) setRep(d.reps[0])
    } catch (e) { console.error(e) }
    setLoadingDetail(false)
  }

  const th: React.CSSProperties = { textAlign: 'left', padding: '10px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }
  const td: React.CSSProperties = { padding: '8px 14px', fontSize: 13 }

  // Condensed export (Excel / PDF / Print) of the selected store/rep's daily targets + conversion.
  function buildPayload(): ExportPayload {
    if (!detail) return { title: 'Daily Targets', filename: 'daily-targets', sheets: [] }
    const who = scope === 'rep' && detail.rep ? detail.rep : (summary.find(s => s.store_code === storeCode)?.address || storeCode)
    const rows: any[] = CATS.map(c => {
      const m = detail.categories[c.key]
      return { category: c.label, today: val(m.unit, m.today_target), pace: val(m.unit, m.pace),
        need: val(m.unit, m.need), monthly: val(m.unit, m.monthly), achieved: val(m.unit, m.achieved_mtd) }
    })
    const conv = scope === 'rep' ? detail.conversion?.rep : detail.conversion?.store
    if (conv && detail.conversion) {
      rows.push({
        category: 'Conversion (boxes ÷ bill-pay)', today: `${conv.rate}%`,
        pace: `tgt ${detail.conversion.store.target}%`,
        need: scope === 'rep' && detail.conversion.rep?.below_store ? 'below store' : '',
        monthly: `store ${detail.conversion.store.rate}%`,
        achieved: `${conv.boxes} box / ${conv.billpays} bp`,
      })
    }
    const cols = [
      { header: 'Target', get: (r: any) => r.category },
      { header: 'Today', get: (r: any) => r.today, align: 'right' as const },
      { header: 'Pace/day', get: (r: any) => r.pace, align: 'right' as const },
      { header: 'Need', get: (r: any) => r.need, align: 'right' as const },
      { header: 'Monthly', get: (r: any) => r.monthly, align: 'right' as const },
      { header: 'Achieved', get: (r: any) => r.achieved, align: 'right' as const },
    ]
    return {
      title: `Daily Targets — ${who}`,
      subtitle: `${scope === 'rep' ? 'Rep · ' : ''}${period} · as of ${detail.today}`,
      filename: `Daily-Targets-${String(who).replace(/[^a-z0-9]+/gi, '-')}-${period.replace(/\s+/g, '-')}`,
      sheets: [{ name: 'Daily Targets', columns: cols, rows }],
    }
  }

  // All-stores summary export (Excel/PDF/Print/Send) — includes the Trending columns and honors the
  // active RULE FIVE filters (the `summary` rows are already server-filtered).
  function buildSummaryPayload(): ExportPayload {
    const cols = [
      { header: 'Store', get: (s: any) => s.address || s.store_code },
      { header: 'Market', get: (s: any) => s.market || '' },
      { header: 'Monthly', get: (s: any) => s.categories?.activations?.monthly ?? 0 },
      { header: 'Achieved', get: (s: any) => s.categories?.activations?.achieved_mtd ?? 0 },
      { header: 'Need', get: (s: any) => s.categories?.activations?.need ?? 0 },
      { header: "Today's Target", get: (s: any) => s.categories?.activations?.today_target ?? 0 },
      { header: 'Pace/day', get: (s: any) => s.categories?.activations?.pace ?? 0 },
      { header: 'Trending Box', get: (s: any) => s.trending_box ?? 0 },
      { header: 'Trending Acc. $', get: (s: any) => s.trending_acc_sales ?? 0, money: true },
      { header: 'Acc. Achieved (MTD)', get: (s: any) => s.categories?.accessories?.achieved_mtd ?? 0, money: true },
      { header: "Acc. Today's Target", get: (s: any) => s.categories?.accessories?.today_target ?? 0, money: true },
      { header: 'Acc. $/day needed', get: (s: any) => s.categories?.accessories?.pace ?? 0, money: true },
      { header: 'Device set-up fee (MTD, in acc.)', get: (s: any) => s.categories?.accessories?.setup_fee_mtd ?? 0, money: true },
      { header: 'Conversion %', get: (s: any) => s.conversion ? s.conversion.rate : '' },
    ]
    return {
      title: 'Daily Targets — Store Summary',
      subtitle: `${period} · Activations & Trending`,
      filename: `Daily-Targets-Summary-${period.replace(/\s+/g, '-')}`,
      sheets: [{ name: 'Store Summary', columns: cols, rows: summary }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
        <a href="/commcalc/targets" style={{ padding: '7px 16px', borderRadius: 9, fontSize: 13, fontWeight: 600, textDecoration: 'none', background: 'var(--accent)', color: '#fff', border: '1px solid var(--border)' }}>Daily Targets</a>
        <a href="/commcalc/targets/action-plan" style={{ padding: '7px 16px', borderRadius: 9, fontSize: 13, fontWeight: 600, textDecoration: 'none', background: 'var(--surface2)', color: 'var(--text2)', border: '1px solid var(--border)' }}>Action Plan</a>
      </div>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Daily Targets</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          {period} · Schedule-weighted daily targets with daily catch-up. Today's target rolls in any shortfall;
          pace spreads the remaining balance over the open days left.
        </p>
      </div>

      <FreshnessBanner daily={freshness?.daily} dlar={freshness?.dlar} period={period} />

      {/* RULE FIVE standardized filter bar — store(s) / market(s) / rep(s), pick-don't-type over the org's
          real data, applied server-side so the store summary + rep breakdown reflect the selection. */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        {filterOpts.stores.length > 0 && <MultiSelect allLabel="All stores" width={160} value={selStores} options={filterOpts.stores} onChange={setSelStores} searchable />}
        {filterOpts.markets.length > 0 && <MultiSelect allLabel="All markets" width={140} value={selMarkets} options={filterOpts.markets} onChange={setSelMarkets} />}
        {filterOpts.reps.length > 0 && <MultiSelect allLabel="All reps" width={150} value={selReps} options={filterOpts.reps} onChange={setSelReps} searchable />}
        {(selStores.length > 0 || selMarkets.length > 0 || selReps.length > 0) && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setSelStores([]); setSelMarkets([]); setSelReps([]) }}>Clear filters</button>}
        {selReps.length > 0 && <span style={{ fontSize: 11, color: 'var(--text3)' }}>rep filter narrows the per‑store breakdown; store totals stay whole‑store</span>}
      </div>

      {/* ── All-stores summary ── */}
      <div className="card" style={{ padding: 0, marginBottom: 24, overflowX: 'auto' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 600, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span>Store Summary — Activations &amp; Trending</span>
          {summary.length > 0 && <span style={{ display: 'flex', gap: 8 }}><ExportButtons payload={buildSummaryPayload} compact /><SendReportButton exportPayload={buildSummaryPayload} compact /></span>}
        </div>
        {loadingSum ? (
          <div style={{ textAlign: 'center', padding: 30, color: 'var(--text3)' }}>Loading…</div>
        ) : summary.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 30, color: 'var(--text3)' }}>
            No targets set. Add monthly targets in <strong>Target Settings</strong>.
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                {['Store', 'Monthly', 'Achieved', 'Need', "Today's Target", 'Pace/day', 'Trending Box', 'Trending Acc.', 'Acc. Ach.', 'Acc. Today', 'Acc. $/day', 'Conversion', ''].map(h => <th key={h} style={th}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {summary.flatMap((s, i) => {
                const a: CatMetrics = s.categories?.activations
                const ac: CatMetrics = s.categories?.accessories
                const setupMtd = Number((ac as any)?.setup_fee_mtd || 0)
                const storeRow = (
                  <tr key={s.store_code} style={{ borderBottom: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
                    <td style={td}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        {(s.reps?.length > 0) && (
                          <button onClick={() => setExpanded(e => ({ ...e, [s.store_code]: !e[s.store_code] }))}
                            style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 11, color: 'var(--text3)', padding: 0, width: 14, lineHeight: 1 }}
                            title={expanded[s.store_code] ? 'Hide reps' : 'Show reps'}>
                            {expanded[s.store_code] ? '▼' : '▶'}
                          </button>
                        )}
                        <div>
                          <div style={{ fontWeight: 600 }}>{s.address || s.store_code}</div>
                          <div style={{ fontSize: 11, color: 'var(--text3)' }}>{s.store_code}{s.market ? ` · ${s.market}` : ''}{s.reps?.length ? ` · ${s.reps.length} reps` : ''}</div>
                        </div>
                      </div>
                    </td>
                    <td style={td}>{fmtN(a?.monthly, 0)}</td>
                    <td style={td}>{fmtN(a?.achieved_mtd, 0)}</td>
                    <td style={{ ...td, color: a?.need > 0 ? '#b45309' : 'var(--green)' }}>{fmtN(a?.need, 0)}</td>
                    <td style={{ ...td, fontWeight: 700, color: 'var(--accent)' }}>{fmtN(a?.today_target, 1)}</td>
                    <td style={td}>{fmtN(a?.pace, 1)}</td>
                    <td style={{ ...td, color: 'var(--accent)', fontWeight: 600 }} title="Projected month-end activations (same source as Executive MTD)">{fmtN(s.trending_box, 0)}</td>
                    <td style={{ ...td, color: 'var(--accent)', fontWeight: 600 }} title="Projected month-end accessory $ (same source as Executive MTD)">{fmt(s.trending_acc_sales)}</td>
                    <td style={td} title="Accessory $ achieved MTD (incl. device set-up fee) — the actual accessory sales flowing from the feed">{fmt(ac?.achieved_mtd)}</td>
                    <td style={{ ...td, fontWeight: 700, color: 'var(--accent)' }} title={`Accessory $ needed today${setupMtd ? ` · includes ${fmt(setupMtd)} device set-up fee in achieved` : ''}`}>{fmt(ac?.today_target)}</td>
                    <td style={td} title={`Accessory $ needed per open day left${setupMtd ? ` · set-up fee counted toward the accessory target (${fmt(setupMtd)} MTD)` : ''}`}>{fmt(ac?.pace)}</td>
                    <td style={{ ...td, fontWeight: 600, color: convColor(s.conversion) }}>
                      {s.conversion ? `${s.conversion.rate}%` : '—'}
                      {s.conversion && (
                        <span style={{ display: 'block', fontSize: 10, fontWeight: 400, color: 'var(--text3)' }}>
                          {s.conversion.boxes}/{s.conversion.billpays} · tgt {s.conversion.target}%
                        </span>
                      )}
                    </td>
                    <td style={td}>
                      <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }}
                        onClick={() => { setScope('store'); setRep(''); setStoreCode(s.store_code) }}>
                        View →
                      </button>
                    </td>
                  </tr>
                )
                // Reps who worked/sold at this store, just below its row — their MTD
                // performance + conversion, so the store breaks down by person.
                const repRows = (expanded[s.store_code] ? (s.reps || []) : []).map((rp: any) => (
                  <tr key={s.store_code + ':' + rp.rep} style={{ borderBottom: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
                    <td style={{ ...td, paddingLeft: 28 }}>
                      <div style={{ fontSize: 12, color: 'var(--text2)' }}>↳ {rp.rep}{rp.below_store && rp.conversion?.billpays > 0 ? ' ⚠️ below store' : ''}</div>
                      <div style={{ fontSize: 10, color: 'var(--text3)' }}>{fmtN(rp.upgrades, 0)} upg · {fmt(rp.accessories)} acc{rp.accessory_setup_fee ? ` (incl ${fmt(rp.accessory_setup_fee)} set-up fee)` : ''}</div>
                    </td>
                    <td style={td}></td>
                    <td style={{ ...td, fontSize: 12 }}>{fmtN(rp.activations, 0)}</td>
                    <td style={td}></td>
                    <td style={td}></td>
                    <td style={td}></td>
                    <td style={td}></td>
                    <td style={td}></td>
                    <td style={td}></td>
                    <td style={td}></td>
                    <td style={{ ...td, fontSize: 12, fontWeight: 600, color: convColor(rp.conversion) }}>
                      {rp.conversion?.billpays > 0 ? `${rp.conversion.rate}%` : '—'}
                    </td>
                    <td style={td}>
                      <button className="btn" style={{ fontSize: 11, padding: '3px 8px' }}
                        onClick={() => { setScope('rep'); setRep(rp.rep); setStoreCode(s.store_code) }}>
                        View →
                      </button>
                    </td>
                  </tr>
                ))
                return [storeRow, ...repRows]
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Detail controls ── */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <select className="input" value={storeCode} onChange={e => { setStoreCode(e.target.value); setRep('') }} style={{ minWidth: 220 }}>
          {summary.map(s => <option key={s.store_code} value={s.store_code}>{s.address || s.store_code}</option>)}
        </select>
        <div style={{ display: 'flex', gap: 4, background: 'var(--surface2)', padding: 4, borderRadius: 10 }}>
          {(['store', 'rep'] as const).map(sc => (
            <button key={sc} className="btn" onClick={() => setScope(sc)} style={{
              background: scope === sc ? 'white' : 'transparent',
              color: scope === sc ? 'var(--accent)' : 'var(--text2)', fontSize: 13,
              boxShadow: scope === sc ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
            }}>{sc === 'store' ? 'Store' : 'By Rep'}</button>
          ))}
        </div>
        {scope === 'rep' && (
          <select className="input" value={rep} onChange={e => setRep(e.target.value)} style={{ minWidth: 200 }}>
            <option value="">Select rep…</option>
            {detail?.reps?.map(r => <option key={r} value={r}>{r}</option>)}
          </select>
        )}
        {detail && <span style={{ fontSize: 12, color: 'var(--text3)' }}>
          {fmtN(detail.scheduled_hours_total, 0)}h scheduled · {detail.open_days_total} open days{detail.projected_open_days ? ` (${detail.projected_open_days} projected)` : ''} · today {detail.today}
        </span>}
        {detail && <span style={{ marginLeft: 'auto' }}><><ExportButtons payload={buildPayload} compact /><SendReportButton exportPayload={buildPayload} compact /></></span>}
      </div>

      {detail && !detail.has_schedule && (
        <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 10, padding: '8px 14px', marginBottom: 16, fontSize: 13, color: '#92400e' }}>
          ⚠️ No StoreOps schedule loaded for this {scope === 'rep' ? 'rep' : 'store'} this period — targets can't be schedule-weighted, so the full remaining balance is shown under <strong>Pace</strong> and today's target is 0.
        </div>
      )}

      {loadingDetail ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>Loading…</div>
      ) : !detail ? null : (
        <>
          {/* Category tiles */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14, marginBottom: 24 }}>
            {CATS.map(c => {
              const m = detail.categories[c.key]
              if (!m) return null
              return (
                <div key={c.key} className="card">
                  <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>{c.label}</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', rowGap: 6, fontSize: 13 }}>
                    <span style={{ color: 'var(--text2)' }}>Monthly target</span>
                    <span style={{ fontWeight: 600 }}>{val(m.unit, m.monthly)}</span>
                    <span style={{ color: 'var(--text2)' }}>Achieved so far</span>
                    <span style={{ fontWeight: 600 }}>{val(m.unit, m.achieved_mtd)}</span>
                    <span style={{ color: 'var(--text2)' }}>Need to achieve</span>
                    <span style={{ fontWeight: 600, color: m.need > 0 ? '#b45309' : 'var(--green)' }}>{val(m.unit, m.need)}</span>
                    <span style={{ color: 'var(--text2)', paddingTop: 6, borderTop: '1px solid var(--border)' }}>Today's target</span>
                    <span style={{ fontWeight: 700, color: 'var(--accent)', paddingTop: 6, borderTop: '1px solid var(--border)' }}>{val(m.unit, m.today_target)}</span>
                    <span style={{ color: 'var(--text2)' }}>Pace / open day</span>
                    <span style={{ fontWeight: 600 }}>{val(m.unit, m.pace)}</span>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Conversion (boxes ÷ bill-payments) */}
          {detail.conversion && (
            <div className="card" style={{ marginBottom: 24 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
                <div style={{ fontWeight: 700, fontSize: 14 }}>Conversion <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>· boxes ÷ bill-payments · target {detail.conversion.store.target}%</span></div>
              </div>
              <div style={{ display: 'flex', gap: 28, marginTop: 12, flexWrap: 'wrap' }}>
                {detail.scope === 'rep' && detail.conversion.rep && (
                  <div>
                    <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{detail.rep}</div>
                    <div style={{ fontSize: 28, fontWeight: 800, color: convColor(detail.conversion.rep), lineHeight: 1.1 }}>{detail.conversion.rep.rate}%</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>{detail.conversion.rep.boxes} boxes / {detail.conversion.rep.billpays} bill-pays</div>
                  </div>
                )}
                <div>
                  <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Store</div>
                  <div style={{ fontSize: 28, fontWeight: 800, color: convColor(detail.conversion.store), lineHeight: 1.1 }}>{detail.conversion.store.rate}%</div>
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>{detail.conversion.store.boxes} boxes / {detail.conversion.store.billpays} bill-pays</div>
                </div>
              </div>
              {detail.scope === 'rep' && detail.conversion.rep?.below_store && (
                <div style={{ marginTop: 12, background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: '#b91c1c' }}>
                  ⚠️ <strong>{detail.rep}</strong>'s conversion ({detail.conversion.rep.rate}%) is below the store ({detail.conversion.store.rate}%) — this rep is pulling the store's productivity down.
                </div>
              )}
            </div>
          )}

          {/* Reps at this store — full breakdown, above the calendar */}
          {scope === 'store' && (() => {
            const storeReps = (summary.find(s => s.store_code === storeCode)?.reps || []) as any[]
            if (!storeReps.length) return null
            return (
              <div className="card" style={{ padding: 0, marginBottom: 24, overflowX: 'auto' }}>
                <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 600 }}>
                  Reps at this store <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>· MTD performance ({storeReps.length})</span>
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 620 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                      {['Rep', 'Activations', 'Upgrades', 'Accessories', 'Conversion', ''].map(h => <th key={h} style={th}>{h}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {storeReps.map((rp, i) => (
                      <tr key={rp.rep} style={{ borderBottom: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
                        <td style={td}>
                          <span style={{ fontWeight: 600 }}>{rp.rep}</span>
                          {rp.below_store && rp.conversion?.billpays > 0 && <span style={{ color: '#dc2626', fontSize: 11 }}> · ⚠️ below store</span>}
                        </td>
                        <td style={td}>{fmtN(rp.activations, 0)}</td>
                        <td style={td}>{fmtN(rp.upgrades, 0)}</td>
                        <td style={td}>{fmt(rp.accessories)}</td>
                        <td style={{ ...td, fontWeight: 600, color: convColor(rp.conversion) }}>
                          {rp.conversion?.billpays > 0 ? `${rp.conversion.rate}%` : '—'}
                        </td>
                        <td style={td}>
                          <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }}
                            onClick={() => { setScope('rep'); setRep(rp.rep) }}>View →</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          })()}

          {/* Calendar */}
          <div className="card" style={{ padding: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ fontWeight: 600 }}>Target Calendar</div>
              <div style={{ display: 'flex', gap: 4 }}>
                {CATS.map(c => (
                  <button key={c.key} className="btn" onClick={() => setCalCat(c.key)} style={{
                    background: calCat === c.key ? 'var(--accent)' : 'transparent',
                    color: calCat === c.key ? 'white' : 'var(--text2)', fontSize: 12, padding: '4px 10px',
                  }}>{c.label}</button>
                ))}
              </div>
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                  {['Date', 'Hours', 'Base target', 'Achieved'].map(h => <th key={h} style={th}>{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {detail.calendar.map(d => {
                  const cell = d.cats[calCat]
                  const unit = detail.categories[calCat]?.unit || 'count'
                  return (
                    <tr key={d.date} style={{
                      borderBottom: '1px solid var(--border)',
                      background: d.is_today ? '#eff6ff' : 'transparent',
                    }}>
                      <td style={{ ...td, fontWeight: d.is_today ? 700 : 400 }}>
                        {d.date}{d.is_today ? ' · today' : ''}
                      </td>
                      <td style={td}>{fmtN(d.hours, 1)}</td>
                      <td style={td}>{val(unit, cell?.base ?? 0)}</td>
                      <td style={{ ...td, color: cell?.achieved == null ? 'var(--text3)' : (cell.achieved >= (cell.base ?? 0) ? 'var(--green)' : '#b45309') }}>
                        {cell?.achieved == null ? '—' : val(unit, cell.achieved)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

// ── data-freshness banner: when was the data behind Daily Targets last refreshed ──
function FreshnessBanner({ daily, dlar, period }: { daily?: any; dlar?: any; period: string }) {
  const daysAgo = (iso?: string) => {
    if (!iso) return null
    return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
  }
  const fmtDate = (iso?: string) => iso
    ? new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
    : '—'
  const dAge = daysAgo(daily?.uploaded_at)
  const dlarAge = daysAgo(dlar?.last_run_at)
  // amber if a day+ stale, red if missing entirely
  const tone = daily ? (dAge != null && dAge >= 2 ? '#b45309' : 'var(--text2)') : '#dc2626'
  const bg = daily ? (dAge != null && dAge >= 2 ? '#fffbeb' : 'var(--surface2)') : '#fef2f2'
  const border = daily ? (dAge != null && dAge >= 2 ? '#fde68a' : 'var(--border)') : '#fecaca'
  return (
    <div className="card" style={{ padding: '10px 14px', marginBottom: 20, background: bg, border: `1px solid ${border}`, fontSize: 13, display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'center' }}>
      <span style={{ fontWeight: 600, color: 'var(--text2)' }}>📥 Data freshness</span>
      <span style={{ color: tone }}>
        <strong>Daily Sales</strong> (drives Achieved):{' '}
        {daily
          ? <>uploaded {fmtDate(daily.uploaded_at)}{dAge != null ? ` · ${dAge === 0 ? 'today' : dAge === 1 ? '1 day ago' : `${dAge} days ago`}` : ''}
              {' '}· {Number(daily.rows_saved || 0).toLocaleString()} rows{daily.period && daily.period !== period ? ` · ${daily.period} file` : ''}</>
          : <>no upload for {period} — Achieved reads 0 until you upload the daily sales file</>}
      </span>
      {dlar?.last_run_at && (
        <span style={{ color: 'var(--text3)' }}>
          <strong>Metrics KPIs</strong> (conversion/KPIs): synced {fmtDate(dlar.last_run_at)}{dlarAge != null ? ` · ${dlarAge === 0 ? 'today' : `${dlarAge}d ago`}` : ''}
          {dlar.last_status && dlar.last_status !== 'ok' ? ` (${dlar.last_status})` : ''}
        </span>
      )}
      <a href="/commcalc/upload" style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--accent)', textDecoration: 'none' }}>Upload →</a>
    </div>
  )
}
