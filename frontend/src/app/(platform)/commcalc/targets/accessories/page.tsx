'use client'
import { useState, useEffect, Fragment, useCallback } from 'react'
import { api, ORG_ID, fmt, localToday } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MultiSelect } from '@/lib/multiselect'

// Accessory Sales target tracker: per store, the monthly accessory-$ target vs achieved MTD vs what's
// still needed (total + per-remaining-day pace), with a behind/on-track flag. Reuses the Daily Targets
// summary endpoint (categories.accessories). Expand a store to see each rep's accessory contribution.
// RULE FIVE: store/market/rep filter bar drives the page + exports (server-side). Trending Acc. = the
// projected month-end accessory $ read straight from Executive MTD (one source, moves together).
type Acc = { unit: string; monthly: number; achieved_mtd: number; need: number; base_today: number; today_target: number; pace: number; open_days_left: number; setup_fee_mtd?: number }
type MSOpt = { value: string; label?: string }

export default function AccessoryTargetsPage() {
  const { period } = usePeriod()
  const [rows, setRows] = useState<any[]>([])
  const [filters, setFilters] = useState<{ stores: MSOpt[]; markets: string[]; reps: string[] }>({ stores: [], markets: [], reps: [] })
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  // RULE FIVE standardized filters — applied SERVER-SIDE.
  const [selStores, setSelStores] = useState<string[]>([])
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [selReps, setSelReps] = useState<string[]>([])

  const load = useCallback(() => {
    setLoading(true)
    const qs = new URLSearchParams()
    qs.set('org_id', ORG_ID); qs.set('today', localToday()); qs.set('include_untargeted', '1')
    selStores.forEach((s) => qs.append('stores', s))
    selMarkets.forEach((s) => qs.append('markets', s))
    selReps.forEach((s) => qs.append('reps', s))
    api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/summary?${qs.toString()}`)
      .then((d: any) => {
        setFilters(d.filters || { stores: [], markets: [], reps: [] })
        setRows((d.stores || []).filter((s: any) => {
          const a = s.categories?.accessories || {}
          // Show a store if it has an accessory target OR any accessory sales achieved this month —
          // so accessory $ is tracked even before per-store targets are configured.
          return (a.monthly || 0) > 0 || (a.achieved_mtd || 0) > 0 || (s.trending_acc_sales || 0) > 0
        }))
      })
      .catch(console.error).finally(() => setLoading(false))
  }, [period, selStores, selMarkets, selReps])
  useEffect(() => { load() }, [load])

  const hasFilter = selStores.length > 0 || selMarkets.length > 0 || selReps.length > 0
  const clearFilters = () => { setSelStores([]); setSelMarkets([]); setSelReps([]) }

  const acc = (s: any): Acc => s.categories?.accessories || { unit: 'dollars', monthly: 0, achieved_mtd: 0, need: 0, base_today: 0, today_target: 0, pace: 0, open_days_left: 0 }
  const setupFee = (s: any): number => Number(acc(s).setup_fee_mtd || 0)
  const trend = (s: any): number => Number(s.trending_acc_sales || 0)
  const pct = (a: Acc) => a.monthly ? Math.min(100, Math.round(100 * a.achieved_mtd / a.monthly)) : 0
  const onTrack = (a: Acc) => a.achieved_mtd >= (a.base_today || 0) - 0.01

  const tot = rows.reduce((t, s) => {
    const a = acc(s); t.monthly += a.monthly; t.achieved += a.achieved_mtd; t.need += Math.max(0, a.need); t.today += a.today_target; t.trend += trend(s); return t
  }, { monthly: 0, achieved: 0, need: 0, today: 0, trend: 0 })

  function buildPayload(): ExportPayload {
    return {
      title: 'Accessory Sales Targets', subtitle: period, filename: `accessory-targets_${period.replace(/\s+/g, '-')}`,
      sheets: [{ name: 'By store', rows, columns: [
        { header: 'Store', get: (s: any) => s.address || s.store_code },
        { header: 'Target $', get: (s: any) => acc(s).monthly, money: true },
        { header: 'Achieved MTD $', get: (s: any) => acc(s).achieved_mtd, money: true },
        { header: 'of which set-up fee $', get: (s: any) => setupFee(s), money: true },
        { header: 'Trending $', get: (s: any) => trend(s), money: true },
        { header: '% to goal', get: (s: any) => pct(acc(s)) },
        { header: 'Remaining $', get: (s: any) => Math.max(0, acc(s).need), money: true },
        { header: "Today's target $", get: (s: any) => acc(s).today_target, money: true },
        { header: '$/day needed', get: (s: any) => acc(s).pace, money: true },
        { header: 'Open days left', get: (s: any) => acc(s).open_days_left },
        { header: 'Status', get: (s: any) => onTrack(acc(s)) ? 'On track' : 'Behind' },
      ] }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <a href="/commcalc/targets" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Daily Targets</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>🔖 Accessory Sales Targets</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 720 }}>
            Per store: the monthly accessory‑$ goal, achieved so far this month, the projected month‑end
            (<b>Trending</b>, same source as Executive MTD), and what's still needed — total remaining,
            today's target, and the $/day pace for the days left.
          </p>
        </div>
        {rows.length > 0 && <div style={{ display: 'flex', gap: 8 }}><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></div>}
      </div>

      {/* RULE FIVE standardized filter bar — store(s) / market(s) / rep(s), pick-don't-type over the org's
          real data, applied server-side so the tiles, table, trending AND exports reflect the selection. */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        {filters.stores.length > 0 && <MultiSelect allLabel="All stores" width={160} value={selStores} options={filters.stores} onChange={setSelStores} searchable />}
        {filters.markets.length > 0 && <MultiSelect allLabel="All markets" width={140} value={selMarkets} options={filters.markets} onChange={setSelMarkets} />}
        {filters.reps.length > 0 && <MultiSelect allLabel="All reps" width={150} value={selReps} options={filters.reps} onChange={setSelReps} searchable />}
        {hasFilter && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={clearFilters}>Clear filters</button>}
        {selReps.length > 0 && <span style={{ fontSize: 11, color: 'var(--text3)' }}>rep filter narrows the per‑store breakdown; store totals stay whole‑store</span>}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : rows.length === 0 ? (
        <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>
          {hasFilter
            ? <>No accessory sales match the selected filter for {period}. <span style={{ color: 'var(--accent)', cursor: 'pointer' }} onClick={clearFilters}>Clear filters</span>.</>
            : <>No accessory targets set for {period}. Set them in Target Settings (accessory $ per store).</>}
        </div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 14, marginBottom: 16 }}>
            <Stat label="Accessory target (all stores)" value={fmt(tot.monthly)} />
            <Stat label="Achieved MTD" value={fmt(tot.achieved)} color="#16a34a" sub={tot.monthly ? `${Math.round(100 * tot.achieved / tot.monthly)}% to goal` : undefined} />
            <Stat label="Trending (proj. month-end)" value={fmt(tot.trend)} color="var(--accent)" sub={tot.monthly ? `${Math.round(100 * tot.trend / tot.monthly)}% of goal` : undefined} />
            <Stat label="Still needed" value={fmt(tot.need)} color="#d97706" />
            <Stat label="Needed today" value={fmt(tot.today)} color="var(--accent)" />
          </div>

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980 }}>
              <thead><tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                {['Store', 'Target', 'Achieved', 'Set-up fee', 'Trending', '% to goal', 'Remaining', "Today's target", '$/day needed', 'Days left', 'Status'].map(h =>
                  <th key={h} style={{ textAlign: h === 'Store' ? 'left' : 'right', padding: '9px 12px', whiteSpace: 'nowrap' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {rows.map(s => {
                  const a = acc(s); const p = pct(a); const noTarget = (a.monthly || 0) <= 0; const ok = onTrack(a)
                  return (
                    <Fragment key={s.store_code}>
                      <tr onClick={() => setOpen(o => ({ ...o, [s.store_code]: !o[s.store_code] }))}
                        style={{ borderTop: '1px solid var(--border)', cursor: 'pointer', background: ok ? undefined : '#fffaf5' }}>
                        <td style={{ padding: '9px 12px', fontSize: 13, fontWeight: 600 }}>{(s.reps?.length) ? (open[s.store_code] ? '▾ ' : '▸ ') : ''}{s.address || s.store_code}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(a.monthly)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, color: '#16a34a' }}>{fmt(a.achieved_mtd)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, color: 'var(--text3)' }} title="Device set-up fee counted toward the accessory target (reported separately)">{fmt(setupFee(s))}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, color: 'var(--accent)', fontWeight: 600 }}>{fmt(trend(s))}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 12 }}>
                          <div style={{ display: 'inline-block', width: 70, height: 7, background: 'var(--surface2)', borderRadius: 4, overflow: 'hidden', verticalAlign: 'middle', marginRight: 6 }}>
                            <div style={{ width: `${p}%`, height: '100%', background: ok ? '#16a34a' : '#d97706' }} />
                          </div>{p}%
                        </td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, fontWeight: 600 }}>{fmt(Math.max(0, a.need))}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(a.today_target)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(a.pace)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, color: 'var(--text2)' }}>{a.open_days_left}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 12 }}>
                          {noTarget
                            ? <span style={{ background: 'var(--surface2)', color: 'var(--text2)', borderRadius: 5, padding: '1px 8px', fontWeight: 600 }}>No target</span>
                            : <span style={{ background: ok ? '#dcfce7' : '#ffedd5', color: ok ? '#065f46' : '#9a3412', borderRadius: 5, padding: '1px 8px', fontWeight: 600 }}>{ok ? 'On track' : 'Behind'}</span>}
                        </td>
                      </tr>
                      {open[s.store_code] && (s.reps || []).map((rp: any, i: number) => (
                        <tr key={s.store_code + '_' + i} style={{ background: 'var(--surface2)', fontSize: 12 }}>
                          <td style={{ padding: '5px 12px 5px 30px', color: 'var(--text2)' }}>{rp.rep || '(unnamed)'}</td>
                          <td colSpan={1} />
                          <td style={{ padding: '5px 12px', textAlign: 'right', color: '#16a34a' }}>{fmt(rp.accessories || 0)}</td>
                          <td style={{ padding: '5px 12px', textAlign: 'right', color: 'var(--text3)' }}>{fmt(rp.accessory_setup_fee || 0)}</td>
                          <td colSpan={7} style={{ padding: '5px 12px', color: 'var(--text3)' }}>accessory $ contributed (set-up fee shown separately)</td>
                        </tr>
                      ))}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 12 }}>
            "Behind" = achieved MTD is under the pace expected by today. <b>Trending</b> is the projected
            month‑end accessory $ (MTD × days‑in‑month ÷ complete days elapsed) taken from the SAME source as
            Executive MTD — the two always agree. "$/day needed" spreads the remaining target over the open
            days left in the month. Stores with accessory sales but marked <b>No target</b> still appear here
            so achieved $ is tracked — set a target for them in Target Settings to get pacing. <b>Achieved MTD
            counts accessory sales revenue PLUS the device set-up fee</b> (owner directive) — the set-up-fee
            portion is broken out in its own <b>Set-up fee</b> column so nothing is blended silently. The
            set-up-fee lines are identified per-tenant in the Sales Report → Accessory settings.
          </p>
        </>
      )}
    </div>
  )
}

function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return <div className="card" style={{ padding: '14px 16px' }}>
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: color || 'var(--text1)' }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
  </div>
}
