'use client'
import { useState, useEffect, useMemo, useCallback } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ReportShell } from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'

// ACCESSORY COST AUDIT — READ-ONLY. Nothing on this page changes what anyone is paid; it explains the
// numbers a %-of-GP payout is computed FROM, and shows what the SAME period would have paid under each
// of the owner's options. "Today" is read out of the live commission-plan preview (the function the
// calculate path pays from), so the current column can never drift from the money.
//
// WHY IT EXISTS (owner, 2026-07-31): accessory %-GP payouts looked "inconsistent" — a $24.99 screen
// protector paid $0 while a $14.99 pair of headphones paid a number nobody could explain. Two INPUT
// problems, neither visible anywhere before: (1) commcalc.raw_sales has NO cost column, so cost is
// implied (ext_price − gp) and an item whose POS catalog cost equals its retail price has GP $0 →
// $0 payout, by arithmetic; (2) a plan rule's rate is a FRACTION (0.10 = 10%) with no clamp on save,
// so a rate typed as a whole percent pays 100×.

type Opt = 'current' | 'option_a' | 'option_b' | 'option_c' | 'option_r'
const OPTS: Opt[] = ['current', 'option_a', 'option_b', 'option_c', 'option_r']

const REP_COLS: ExportColumn[] = [
  { header: 'Rep', get: r => r.rep, role: 'rep' },
  { header: 'Store', get: r => r.store, role: 'store' },
  { header: 'Market', get: r => r.market },
  { header: 'Plan', get: r => r.plan_name },
  { header: 'Lines', get: r => r.matched_lines, type: 'number' },
  { header: 'Suspect', get: r => r.suspect_lines, type: 'number' },
  { header: 'Today $', get: r => r.current, money: true },
  { header: 'B — % of price', get: r => r.option_b, money: true },
  { header: 'Δ B', get: r => r.delta_b, money: true },
  { header: 'C — guarded', get: r => r.option_c, money: true },
  { header: 'Δ C', get: r => r.delta_c, money: true },
  { header: 'R — rate ÷ 100', get: r => r.option_r, money: true },
  { header: 'Δ R', get: r => r.delta_r, money: true },
]

const ITEM_COLS: ExportColumn[] = [
  { header: 'Item', get: r => r.product },
  { header: 'SKU', get: r => r.sku },
  { header: 'Department', get: r => r.department },
  { header: 'Category', get: r => r.category },
  { header: 'Lines', get: r => r.lines, type: 'number' },
  { header: 'Sold $', get: r => r.ext_price, money: true },
  { header: 'GP $', get: r => r.gp, money: true },
  { header: 'Implied cost (min)', get: r => r.implied_cost_min, money: true },
  { header: 'Implied cost (max)', get: r => r.implied_cost_max, money: true },
  { header: 'POS catalog cost', get: r => r.catalog_cost, money: true },
  { header: 'Paid today $', get: r => r.paid, money: true },
  { header: 'Data check', get: r => (r.flag_labels || []).join(' ') },
]

const LINE_COLS: ExportColumn[] = [
  { header: 'Rep', get: r => r.rep, role: 'rep' },
  { header: 'Date', get: r => r.date, type: 'date' },
  { header: 'Trans ID', get: r => r.trans_id },
  { header: 'Item', get: r => r.product },
  { header: 'SKU', get: r => r.sku },
  { header: 'Rule', get: r => r.rule },
  { header: 'Rate', get: r => r.pct, type: 'number' },
  { header: 'Price', get: r => r.ext_price, money: true },
  { header: 'GP', get: r => r.gp, money: true },
  { header: 'Implied cost', get: r => r.implied_cost, money: true },
  { header: 'POS catalog cost', get: r => r.catalog_cost, money: true },
  { header: 'Paid today', get: r => r.current, money: true },
  { header: 'A — catalog cost', get: r => r.option_a, money: true },
  { header: 'B — % of price', get: r => r.option_b, money: true },
  { header: 'C — guarded', get: r => r.option_c, money: true },
  { header: 'R — rate ÷ 100', get: r => r.option_r, money: true },
  { header: 'Data check', get: r => (r.flag_labels || []).join(' ') },
]

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function AccessoryCostAuditPage() {
  const { period } = usePeriod()
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [cBasis, setCBasis] = useState<'price' | 'assumed_gp'>('price')
  const [assume, setAssume] = useState('')

  const load = useCallback(() => {
    if (!period) return
    setBusy(true); setErr('')
    const qs = new URLSearchParams({ org_id: ORG_ID, c_basis: cBasis })
    if (cBasis === 'assumed_gp' && assume.trim()) qs.set('assume_gp_pct', assume.trim())
    api(`/api/v1/commcalc/accessory-cost-audit/${encodeURIComponent(period)}?${qs.toString()}`)
      .then(setData)
      .catch(e => setErr(String(e?.message || e)))
      .finally(() => setBusy(false))
  }, [period, cBasis, assume])

  useEffect(() => { load() }, [load])

  const repRows = data?.by_rep || []
  const acc = useMemo(() => ({ store: (r: any) => r.store, market: (r: any) => r.market, rep: (r: any) => r.rep }), [])
  const shownReps = useMemo(() => filterRows(repRows, filt, acc), [repRows, filt, acc])
  const lineAcc = useMemo(() => ({ rep: (r: any) => r.rep, date: (r: any) => r.date }), [])
  const shownLines = useMemo(() => filterRows(data?.lines || [], filt, lineAcc), [data, filt, lineAcc])
  const opts = useMemo(() => optionsFromRows(repRows, acc), [repRows, acc])

  const totals = data?.totals || {}
  const cnt = data?.counts || {}

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Accessory Cost Audit — what the % is being paid on</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          {period} · <b>read-only</b> · nothing on this page changes a payout. “Today” is read from the live
          commission-plan preview, so it always equals what the engine pays right now.
        </p>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <StandardFilterBar
          value={filt} onChange={setFilt} periodMode="none"
          show={{ period: false, stores: true, markets: true, reps: true }}
          storeOptions={opts.stores} marketOptions={opts.markets} repOptions={opts.reps}
          right={
            <>
              <label style={{ fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                Option C fallback
                <select style={sel} value={cBasis} onChange={e => setCBasis(e.target.value as any)}>
                  <option value="price">pay the same rate on the PRICE</option>
                  <option value="assumed_gp">pay the rate on an assumed GP margin</option>
                </select>
              </label>
              {cBasis === 'assumed_gp' && (
                <label style={{ fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                  margin (0.40 = 40%)
                  <input style={{ ...sel, width: 90 }} type="number" step="0.01" placeholder="0.40"
                    value={assume} onChange={e => setAssume(e.target.value)} />
                </label>
              )}
              <button className="btn btn-secondary" onClick={load} disabled={busy}>{busy ? 'Loading…' : 'Refresh'}</button>
            </>
          }
        />
      </div>

      {err && <div className="card" style={{ borderLeft: '4px solid var(--red)', marginBottom: 14, fontSize: 13 }}>{err}</div>}
      {data && !data.ready && (
        <div className="card" style={{ borderLeft: '4px solid var(--amber)', marginBottom: 14, fontSize: 13 }}>{data.note}</div>
      )}

      {data?.ready && (
        <>
          {/* ── what the rates say ─────────────────────────────────────────────────────── */}
          <div className="card" style={{ marginBottom: 14 }}>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>The rules doing the paying</div>
            {(data.rules || []).length === 0 ? (
              <div style={{ fontSize: 13, color: 'var(--text3)' }}>{data.note || 'No %-of-basis rule matched anything.'}</div>
            ) : (
              <table style={{ width: '100%', fontSize: 12.5 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Rule', 'Pays', 'Stored rate', 'Matches', 'Lines', 'Suspect', 'Paid today', 'Data check'].map(h =>
                    <th key={h} style={{ textAlign: 'left', padding: '4px 6px', color: 'var(--text2)' }}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {(data.rules || []).map((r: any, i: number) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '4px 6px' }}>{r.label || r.rule_id}</td>
                      <td style={{ padding: '4px 6px' }}>{r.payout_kind}</td>
                      <td style={{ padding: '4px 6px', fontWeight: 700, color: (r.rate_flags || []).length ? 'var(--red)' : 'var(--text)' }}>
                        {r.pct}{(r.rate_flags || []).length ? ' ⚠' : ` (= ${(Number(r.pct) * 100).toFixed(2)}%)`}
                      </td>
                      <td style={{ padding: '4px 6px', color: 'var(--text3)' }}>{r.match_field} {r.match_op} “{r.match_value}”</td>
                      <td style={{ padding: '4px 6px' }}>{r.matched_lines}</td>
                      <td style={{ padding: '4px 6px', color: r.suspect_lines ? 'var(--red)' : 'var(--text3)' }}>{r.suspect_lines}</td>
                      <td style={{ padding: '4px 6px' }}>{fmt(r.paid)}</td>
                      <td style={{ padding: '4px 6px', color: 'var(--red)', fontSize: 11.5 }}>{(r.rate_flag_labels || []).join(' ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* ── the options table ──────────────────────────────────────────────────────── */}
          <div className="card" style={{ marginBottom: 14 }}>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>What {period} would have paid — the owner's options</div>
            <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 8 }}>
              {cnt.matched_lines} matched line(s), <b style={{ color: cnt.suspect_lines ? 'var(--red)' : 'inherit' }}>{cnt.suspect_lines}</b> with an
              unusable cost. Nothing below has been applied — this is what each choice WOULD produce.
            </div>
            <table style={{ width: '100%', fontSize: 13 }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Option', 'Total for the period', 'Δ vs today'].map(h =>
                  <th key={h} style={{ textAlign: 'left', padding: '5px 8px', color: 'var(--text2)' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {OPTS.map(k => {
                  const v = totals[k]
                  const d = k === 'current' ? 0 : Number(v || 0) - Number(totals.current || 0)
                  const unknown = k === 'option_a' && data.deltas?.option_a === 'unknown'
                  return (
                    <tr key={k} style={{ borderTop: '1px solid var(--border)', fontWeight: k === 'current' ? 700 : 400 }}>
                      <td style={{ padding: '5px 8px' }}>{data.option_labels?.[k] || k}</td>
                      <td style={{ padding: '5px 8px' }}>{unknown ? '—' : fmt(v)}</td>
                      <td style={{ padding: '5px 8px', color: d < 0 ? 'var(--red)' : d > 0 ? 'var(--green)' : 'var(--text3)' }}>
                        {k === 'current' ? '—' : unknown ? 'unknown until the POS costs are corrected' : fmt(d)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 8, lineHeight: 1.6 }}>
              <b>A</b> can only be computed for items the POS catalog already carries a cost for
              ({data.catalog_rows || 0} catalog row(s) loaded); everything else is “unknown until the owner sets a cost”
              — it is never guessed. <b>C</b> currently falls back to{' '}
              <b>{data.option_c?.basis === 'assumed_gp' ? `an assumed ${(Number(data.option_c?.assume_gp_pct || 0) * 100).toFixed(0)}% GP margin` : '% of the price'}</b>{' '}
              on suspect lines only; healthy lines are untouched. <b>R</b> is exact: every %-payout is linear in the
              rate, so “the rate read as a percent” is today ÷ 100.
            </div>
          </div>

          {/* ── per rep ────────────────────────────────────────────────────────────────── */}
          <div style={{ marginBottom: 14 }}>
            <ReportShell title={`Per rep — ${period}`} subtitle="what each option would move, per person"
              filename={`accessory-cost-options-${period}`.replace(/\s+/g, '-')}
              columns={REP_COLS} rows={shownReps} totals compact stickyHeader />
          </div>

          {/* ── the item list (Option A's worksheet) ───────────────────────────────────── */}
          <div style={{ marginBottom: 14 }}>
            <ReportShell title={`Items — ${period}`}
              subtitle="the exact list to fix in the POS, with the cost each line implies"
              filename={`accessory-cost-items-${period}`.replace(/\s+/g, '-')}
              columns={ITEM_COLS} rows={data.items || []} totals compact stickyHeader
              rowStyle={(r: any) => ((r.flags || []).length ? { background: '#fffbeb' } : undefined)} />
          </div>

          {/* ── every flagged line ─────────────────────────────────────────────────────── */}
          <ReportShell title={`Flagged lines — ${period}`}
            subtitle={`${cnt.flagged_shown || 0} line(s) whose cost cannot be trusted as a payout basis`}
            filename={`accessory-cost-lines-${period}`.replace(/\s+/g, '-')}
            columns={LINE_COLS} rows={shownLines} totals compact stickyHeader />
        </>
      )}
    </div>
  )
}
