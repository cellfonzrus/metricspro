'use client'
import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import type { EntityOption } from '@/components/EntityPicker'
import type { StandardFilterValue } from '@/lib/standard-filters'
import { SortableTh, useTableSort } from '@/components/SortableTh'
import { usePosTerm } from '@/lib/report-labels'

// CASH VARIANCE AFTER MANAGEMENT RECON — owner ask 2026-10-10, verbatim: "need to create a report
// after management recon to assess the shortage and overage based on what the management received
// compared with the b2b data and the cash envelope, the data will never match so we ignore upto $5
// of variance, the system should then be smart to find the trends of a particular store / employee
// and highlight the same- ti shoudl be all dynamic with all filters".
//
// Reads GET /closing/cash-variance (pure logic in closing/cash_variance.py, proof
// harness_cash_variance.py). This screen renders; it computes no variance and spells no threshold.
//
// THE ONE THING TO UNDERSTAND BEFORE READING A NUMBER HERE: a difference between two cash figures
// only means something when both figures count the SAME cash. The server carries each figure with
// its basis and refuses to subtract across bases, which is why some cells read "not measured"
// instead of showing a number. Measured live before this shipped, the naive subtraction called 141
// of 275 counted store-days SHORT — every one of them by the bill-payment cash to the cent.
//
// THE PAIR COLUMNS ARE SERVER-DECLARED (`data.pairs` <- cash_variance.PAIRS). This file spells one
// pair key, the headline, and iterates the rest, so a pair added later gets its tile, its column
// and its export with the right label and cannot carry a stale header.
const NO_MARKET = '(no market)'
const HEADLINE = 'received_vs_envelope'
const csv = (a: string[]) => (a.length ? a.join(',') : undefined)

const VERDICTS: [string, string][] = [
  ['', 'All store-days'],
  ['discrepancy', 'Out of band (short or over)'],
  ['short', 'Short only'],
  ['over', 'Over only'],
  ['ok', 'Inside the band'],
  ['not_measured', 'Cannot be measured'],
]
const BADGE: Record<string, string> = {
  short: '🔻 Short', over: '🔺 Over', ok: '✅ Inside band', not_measured: '— Not measured',
}
const TREND_ICON: Record<string, string> = {
  repeat_short: '🔻', repeat_over: '🔺', one_direction: '📐', nothing_measurable: '🫥',
}

type Pair = { key: string; label: string; note?: string }
type Cmp = {
  measured?: boolean; variance?: number | null; verdict?: string | null
  reason?: string | null; reason_label?: string | null; basis?: string | null
}
type Row = { pairs?: Record<string, Cmp>; legs?: Record<string, any> } & Record<string, any>

const money = (v: any) => (v == null ? '' : fmt(v))
// A pair that could not be measured must never render as a number or a blank — a blank reads as a
// pair that tied, which is the absence-is-zero defect wearing display clothes.
const cmpText = (c?: Cmp) => (c?.measured ? money(c.variance) : '—')
const cmpTitle = (c?: Cmp) => (c?.measured ? `${c.verdict} on the ${c.basis} basis` : (c?.reason_label || ''))

const rowCell = (r: any, f: string) => (f.startsWith('p:') ? (r?.pairs?.[f.slice(2)]?.variance ?? null) : r?.[f])
const groupCell = (g: any, f: string) =>
  (f.startsWith('h:') ? (g?.pairs?.[HEADLINE]?.[f.slice(2)] ?? null) : g?.[f])

export default function CashVariancePage() {
  const today = localToday()
  // RULE TWO: the tenant's OWN name for its point of sale, never a vendor spelled here
  // (lib/report-labels.ts; harness_carrier_vocab_guard's POS axis fails the build on one).
  const { pos } = usePosTerm()
  const [filt, setFilt] = useState<StandardFilterValue>({
    period: today.slice(0, 8) + '01', periodTo: today, stores: [], markets: [], reps: [],
  })
  const [verdict, setVerdict] = useState('')
  const [view, setView] = useState<'days' | 'stores' | 'employees'>('employees')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const reqRef = useRef(0)

  // Canonical org-scoped option sources (pick-don't-type) — the same sources every closing screen uses.
  const [pStores, setPStores] = useState<any[]>([])
  const [pEmps, setPEmps] = useState<any[]>([])
  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setPStores(Array.isArray(s) ? s : [])).catch(() => {})
    apiCached('/api/v1/storeops/employees?all_company=true', LOOKUP)
      .then((r: any) => setPEmps(Array.isArray(r) ? r : (r?.employees || []))).catch(() => {})
  }, [])
  const storeOptions: EntityOption[] = useMemo(
    () => pStores.filter((s: any) => s.store_code)
      .map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, sublabel: s.market || undefined })),
    [pStores])
  const marketOptions: EntityOption[] = useMemo(() => {
    const real = Array.from(new Set(pStores.map((s: any) => s.market).filter(Boolean))).sort()
    return [...real.map((m: string) => ({ id: m, label: m })), { id: NO_MARKET, label: NO_MARKET }]
  }, [pStores])
  const repOptions: EntityOption[] = useMemo(
    () => pEmps.filter((e: any) => (e.name || '').trim()).map((e: any) => ({ id: e.name, label: e.name })),
    [pEmps])

  const load = useCallback(() => {
    const myReq = ++reqRef.current
    setLoading(true); setErr('')
    const qs = new URLSearchParams()
    const from = filt.period || localToday()
    qs.set('date_from', from)
    qs.set('date_to', filt.periodTo || from)
    const s = csv(filt.stores); if (s) qs.set('stores', s)
    const m = csv(filt.markets); if (m) qs.set('markets', m)
    const r = csv(filt.reps); if (r) qs.set('reps', r)
    if (verdict) qs.set('verdict', verdict)
    api(`/api/v1/closing/cash-variance?${qs.toString()}`)
      .then(d => { if (reqRef.current === myReq) setData(d) })
      .catch(e => { if (reqRef.current === myReq) { setErr(e?.message || String(e)); setData(null) } })
      .finally(() => { if (reqRef.current === myReq) setLoading(false) })
  }, [filt, verdict])
  useEffect(() => { load() }, [load])

  const rows: Row[] = data?.rows || []
  const pairs: Pair[] = data?.pairs || []
  const byStore: any[] = data?.by_store || []
  const byEmp: any[] = data?.by_employee || []
  const trends: any[] = data?.trends || []
  const blockers: any[] = data?.blockers || []
  const band: number = data?.band ?? 0
  const t = data?.totals || {}

  // Columns: the fixed identity block, then ONE column per server-declared pair.
  const dayCols: [string, string][] = useMemo(() => ([
    ['Date', 'close_date'], ['Store', 'store_address'], ['Employee', 'employee_name'],
    ['Management received', 'l_received'], ['Cash envelope', 'l_declared'], [`${pos} cash`, 'l_pos'],
    ...pairs.map(p => [p.label, `p:${p.key}`] as [string, string]),
    ['Verdict', 'verdict'],
  ]), [pairs, pos])
  const groupCols = (label: string, idField: string): [string, string][] => ([
    [label, idField], ['Days', 'days'], ['Measured', 'h:measured'], ['Not measured', 'h:not_measured'],
    ['Short', 'h:short'], ['$ Short', 'h:short_total'], ['Over', 'h:over'], ['$ Over', 'h:over_total'],
    ['Inside band', 'h:ok'], ['Net', 'h:net_variance'], ['Last close', 'last_close'],
  ])

  const daySort = useTableSort(rows, rowCell, { field: 'close_date', dir: 'desc' })
  // Seeded on $ short descending: the point of these views is who is worst, so they open on the answer.
  const storeSort = useTableSort(byStore, groupCell, { field: 'h:short_total', dir: 'desc' })
  const empSort = useTableSort(byEmp, groupCell, { field: 'h:short_total', dir: 'desc' })

  const dayColumns: ExportColumn[] = useMemo(() => ([
    { header: 'Date', field: 'close_date', type: 'date', role: 'date', get: (r: Row) => r.close_date },
    { header: 'Store', field: 'store_address', role: 'store', get: (r: Row) => r.store_address },
    { header: 'Store code', field: 'store_code', get: (r: Row) => r.store_code },
    { header: 'Market', field: 'market', get: (r: Row) => r.market },
    { header: 'Employee', field: 'employee_name', role: 'rep', get: (r: Row) => r.employee_name },
    { header: 'Management received $', field: 'l_received', money: true, get: (r: Row) => r.legs?.received?.amount },
    { header: 'Management received — which cash', field: 'l_received_basis', get: (r: Row) => basisLabel(r.legs?.received?.basis) },
    { header: 'Cash envelope $', field: 'l_declared', money: true, get: (r: Row) => r.legs?.declared?.amount },
    { header: `${pos} cash $`, field: 'l_pos', money: true, get: (r: Row) => r.legs?.pos?.amount },
    // One pair of columns per server-declared pair — what-you-see-is-what-exports (RULE FOUR §3c),
    // and the REASON rides beside the number so a spreadsheet cannot collapse "not measured" to blank.
    ...pairs.flatMap(p => ([
      { header: `${p.label} $`, field: `p_${p.key}`, money: true,
        get: (r: Row) => (r.pairs?.[p.key]?.measured ? r.pairs?.[p.key]?.variance : null) },
      { header: `${p.label} — verdict`, field: `v_${p.key}`,
        get: (r: Row) => (r.pairs?.[p.key]?.measured ? r.pairs?.[p.key]?.verdict : 'not measured') },
      { header: `${p.label} — why not`, field: `w_${p.key}`,
        get: (r: Row) => r.pairs?.[p.key]?.reason_label || '' },
    ])),
    { header: 'Envelope opened', field: 'envelope_opened', get: (r: Row) => (r.envelope_opened ? 'Yes' : 'No') },
    { header: 'Bill-pay envelope counted', field: 'billpay_counted', get: (r: Row) => (r.billpay_counted ? 'Yes' : 'No') },
    { header: 'Picked up by', field: 'picked_up_by', get: (r: Row) => r.picked_up_by || '' },
  ]), [pairs, pos])

  const groupColumns = (label: string, idField: string): ExportColumn[] => ([
    { header: label, field: idField, get: (g: any) => g[idField] },
    { header: 'Days', field: 'days', type: 'number', get: (g: any) => g.days },
    { header: 'Measured', field: 'measured', type: 'number', get: (g: any) => g.pairs?.[HEADLINE]?.measured },
    { header: 'Not measured', field: 'not_measured', type: 'number', get: (g: any) => g.pairs?.[HEADLINE]?.not_measured },
    { header: 'Short days', field: 'short', type: 'number', get: (g: any) => g.pairs?.[HEADLINE]?.short },
    { header: '$ Short', field: 'short_total', type: 'money', get: (g: any) => g.pairs?.[HEADLINE]?.short_total },
    { header: 'Over days', field: 'over', type: 'number', get: (g: any) => g.pairs?.[HEADLINE]?.over },
    { header: '$ Over', field: 'over_total', type: 'money', get: (g: any) => g.pairs?.[HEADLINE]?.over_total },
    { header: 'Inside band', field: 'ok', type: 'number', get: (g: any) => g.pairs?.[HEADLINE]?.ok },
    { header: 'Net variance', field: 'net_variance', type: 'money', get: (g: any) => g.pairs?.[HEADLINE]?.net_variance },
    { header: 'First close', field: 'first_close', type: 'date', get: (g: any) => g.first_close },
    { header: 'Last close', field: 'last_close', type: 'date', get: (g: any) => g.last_close },
  ])

  // The server's own wording for a basis key it sent us. Falls back to the key only if the server
  // stopped serving that basis, which is visible rather than silent.
  function basisLabel(key?: string | null) {
    if (!key) return ''
    return (data?.bases || []).find((b: any) => b.key === key)?.label || key
  }

  const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const tile: React.CSSProperties = { padding: '10px 14px', borderRadius: 10, border: '1px solid var(--border)', background: 'var(--surface)', minWidth: 150 }
  const th: React.CSSProperties = { textAlign: 'left', padding: '7px 9px', fontSize: 12, color: 'var(--text2)', whiteSpace: 'nowrap' }
  const td: React.CSSProperties = { padding: '6px 9px', fontSize: 13, borderTop: '1px solid var(--border)' }

  const tab = (k: typeof view, label: string, n: number) => (
    <button key={k} onClick={() => setView(k)} style={{
      ...sel, cursor: 'pointer', fontWeight: view === k ? 700 : 500,
      background: view === k ? 'var(--surface2)' : 'var(--surface)',
    }}>{label} ({n})</button>
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💵 Cash Variance</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 820 }}>
            Shortage and overage after management recon: what <b>management received</b>, against the
            <b> cash envelope</b> the rep declared at closing, against the <b>{pos} cash</b> the till rang.
            Differences up to <b>{money(band)}</b> are treated as counting noise{data?.band_is_house_default ? ' (the house default)' : ' (set for this company)'}.
          </p>
          <p style={{ color: 'var(--text3)', fontSize: 12.5, margin: '6px 0 0', maxWidth: 820 }}>
            <b>Why some cells say “not measured”:</b> two cash figures can only be compared when they
            count the same cash. The cash envelope is the <i>whole drawer</i> including bill-payment
            cash; a pickup envelope may have had that bill-payment cash taken out of it already. Where
            the two do not match up, this report says so instead of showing a difference that is really
            a definition — that difference would read as a shortage against the person who handed the
            cash over.
          </p>
        </div>
        {!loading && rows.length > 0 && (
          <ReportExportBar
            title="Cash Variance"
            subtitle={`${filt.period} → ${filt.periodTo || filt.period} · band ${money(band)}`}
            filename={`cash-variance_${filt.period}_${filt.periodTo || filt.period}`}
            sheets={[
              { name: 'Store-days', columns: dayColumns, rows },
              { name: 'By employee', columns: groupColumns('Employee', 'employee_name'), rows: byEmp },
              { name: 'By store', columns: groupColumns('Store', 'store_code'), rows: byStore },
            ]}
          />
        )}
      </div>

      <StandardFilterBar
        value={filt} onChange={setFilt}
        periodMode="range"
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
        storeLabel="Stores…" marketLabel="Markets…" repLabel="Employees…"
        right={(
          <select style={sel} value={verdict} onChange={e => setVerdict(e.target.value)}
            title="Narrow to the store-days you want to review.">
            {VERDICTS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        )}
      />

      {err && <div style={{ color: 'var(--danger)', margin: '12px 0' }}>{err}</div>}
      {loading && <div style={{ color: 'var(--text2)', margin: '12px 0' }}>Loading…</div>}

      {!loading && !err && (
        <>
          {/* One tile per server-declared pair. `not_measured` sits beside short/over because how
              much of the question cannot be answered yet is the most useful number on this page. */}
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', margin: '14px 0' }}>
            <div style={tile}>
              <div style={{ fontSize: 12, color: 'var(--text2)' }}>Store-days in range</div>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{t.days ?? 0}</div>
            </div>
            {pairs.map(p => {
              const s = t.pairs?.[p.key] || {}
              return (
                <div key={p.key} style={tile} title={p.note || ''}>
                  <div style={{ fontSize: 12, color: 'var(--text2)' }}>{p.label}</div>
                  <div style={{ fontSize: 15, fontWeight: 700 }}>
                    🔻 {s.short ?? 0} · {money(s.short_total)}
                  </div>
                  <div style={{ fontSize: 13 }}>🔺 {s.over ?? 0} · {money(s.over_total)}</div>
                  <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                    ✅ {s.ok ?? 0} inside band · — {s.not_measured ?? 0} not measured
                  </div>
                </div>
              )
            })}
          </div>

          {/* "be smart to find the trends of a particular store / employee and highlight the same" */}
          {trends.length > 0 && (
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '10px 14px', marginBottom: 14, background: 'var(--surface)' }}>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>⚠️ Worth a look ({trends.length})</div>
              {trends.slice(0, 12).map((tr: any, i: number) => (
                <div key={i} style={{ padding: '6px 0', borderTop: i ? '1px solid var(--border)' : undefined }}>
                  <div style={{ fontSize: 13.5 }}>
                    <b>{TREND_ICON[tr.kind] || '•'} {tr.subject}</b>
                    <span style={{ color: 'var(--text2)' }}> — {tr.label}: {tr.detail}</span>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text3)' }}>{tr.why}</div>
                </div>
              ))}
              {trends.length > 12 && (
                <div style={{ fontSize: 12, color: 'var(--text3)', paddingTop: 6 }}>
                  …and {trends.length - 12} more. Export to see them all.
                </div>
              )}
            </div>
          )}

          {/* What this report CANNOT answer, counted — reported rather than hidden behind a shorter table. */}
          {blockers.length > 0 && (
            <div style={{ border: '1px dashed var(--border)', borderRadius: 10, padding: '10px 14px', marginBottom: 14 }}>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>What cannot be measured yet</div>
              {blockers.map((b: any) => (
                <div key={b.pair} style={{ fontSize: 13, color: 'var(--text2)', padding: '3px 0' }}>
                  <b>{b.pair_label}</b>: {b.days} of {b.of_days} store-days —{' '}
                  {(b.reasons || []).map((r: any) => `${r.label} (${r.days})`).join('; ')}
                </div>
              ))}
              <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>
                These are recording gaps, not shortages. Counting the envelope — both envelopes, where a
                store takes bill payments — is what turns them into measurable days.
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
            {tab('employees', 'By employee', byEmp.length)}
            {tab('stores', 'By store', byStore.length)}
            {tab('days', 'Store-days', rows.length)}
          </div>

          {view === 'days' && (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                <thead><tr>{dayCols.map(([label, f]) => (
                  <SortableTh key={label + f} field={f} sort={daySort.sort} onSort={daySort.toggle}
                    style={th} disabled={!f}>{label}</SortableTh>
                ))}</tr></thead>
                <tbody>
                  {daySort.sorted.map((r: Row, i: number) => (
                    <tr key={r.closing_row_id || i}>
                      <td style={td}>{r.close_date}</td>
                      <td style={td}>{r.store_address}</td>
                      <td style={td}>{r.employee_name}</td>
                      <td style={td} title={basisLabel(r.legs?.received?.basis) || r.legs?.received?.detail || ''}>
                        {r.legs?.received?.present ? money(r.legs.received.amount) : '—'}
                      </td>
                      <td style={td}>{r.legs?.declared?.present ? money(r.legs.declared.amount) : '—'}</td>
                      <td style={td}>{r.legs?.pos?.present ? money(r.legs.pos.amount) : '—'}</td>
                      {pairs.map(p => (
                        <td key={p.key} style={td} title={cmpTitle(r.pairs?.[p.key])}>
                          {cmpText(r.pairs?.[p.key])}
                        </td>
                      ))}
                      <td style={td}>{BADGE[r.verdict || ''] || r.verdict}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {(view === 'stores' || view === 'employees') && (() => {
            const isEmp = view === 'employees'
            const groups = isEmp ? empSort : storeSort
            const cols = groupCols(isEmp ? 'Employee' : 'Store', isEmp ? 'employee_name' : 'store_code')
            return (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                  <thead><tr>{cols.map(([label, f]) => (
                    <SortableTh key={label + f} field={f} sort={groups.sort} onSort={groups.toggle}
                      style={th} disabled={!f}>{label}</SortableTh>
                  ))}</tr></thead>
                  <tbody>
                    {groups.sorted.map((g: any, i: number) => {
                      const h = g.pairs?.[HEADLINE] || {}
                      return (
                        <tr key={(isEmp ? g.employee_name : g.store_code) || i}>
                          <td style={td}>
                            {isEmp ? g.employee_name : (g.store_address || g.store_code)}
                            <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                              {(isEmp ? (g.stores || []) : (g.employees || [])).slice(0, 3).join(', ')}
                            </div>
                          </td>
                          <td style={td}>{g.days}</td>
                          <td style={td}>{h.measured ?? 0}</td>
                          <td style={td}>{h.not_measured ?? 0}</td>
                          <td style={td}>{h.short ?? 0}</td>
                          <td style={td}>{money(h.short_total)}</td>
                          <td style={td}>{h.over ?? 0}</td>
                          <td style={td}>{money(h.over_total)}</td>
                          <td style={td}>{h.ok ?? 0}</td>
                          <td style={td}>{money(h.net_variance)}</td>
                          <td style={td}>{g.last_close}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )
          })()}

          {rows.length === 0 && (
            <div style={{ color: 'var(--text2)', padding: '18px 0' }}>
              No closings in this range for the chosen filters.
            </div>
          )}
          {data?.market_filter_skipped && (
            <div style={{ color: 'var(--text3)', fontSize: 12, marginTop: 8 }}>
              The store roster could not be read, so the market filter was not applied — nothing was dropped.
            </div>
          )}
        </>
      )}
    </div>
  )
}
