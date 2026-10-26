'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MultiSelect } from '@/lib/multiselect'
import DrillDownPanel, {
  DrillTileLinks, useDrillTile, type DrillExtraColumn, type DrillPanel, type DrillRow,
} from '@/components/DrillDownPanel'

function ymd(d: Date) {
  const y = d.getFullYear(), m = String(d.getMonth()+1).padStart(2,'0'), day = String(d.getDate()).padStart(2,'0')
  return `${y}-${m}-${day}`
}
// VIP bills on FRIDAY. getDay()===5 is Friday, so this returns the upcoming billing Friday.
// (The API filter key is historically named `thursday`; it carries this Friday date.)
function upcomingFriday(from = new Date()) {
  const d = new Date(from); const diff = (5 - d.getDay() + 7) % 7
  d.setDate(d.getDate() + diff); return d
}
function shiftWeek(iso: string, weeks: number) {
  const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + weeks*7); return ymd(d)
}
function pretty(iso: string) {
  return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { weekday:'short', month:'short', day:'numeric', year:'numeric' })
}

type Bucket = { count: number; owed: number }
/** §COMMCALC-34 — the DECLARED-CADENCE roll-up, served only where the consignment ledger answered
 *  nothing. `periods` are the carrier's own billing periods over the SAME rows the Daily Owed
 *  surface reads, so the week cannot total what the day disagrees with. `measured` is the server's
 *  allowlist verdict, not ours: a period it did not measure is shown WITHOUT a figure. */
type DeclaredPeriod = {
  key: string | null; start: string | null; end: string | null; label: string
  basis: string; measured: boolean; count: number; amount: number | null
}
type Declared = {
  error?: string
  cadence?: string; weekday?: number | null; declared?: boolean
  window?: { start: string | null; end: string | null }
  periods: DeclaredPeriod[]
  total_owed: number | null
  unplaced_rows?: number; unplaced_amount?: number | null
  bases?: Record<string, number>; note?: string | null
  source?: string | null; grain?: string | null; source_note?: string | null
  entities?: string[]
}
type Report = {
  thursday: string
  due_this_week: { sold: Bucket; aging: Bucket; total: Bucket }
  by_store: { store: string; market: string; sold_count: number; sold_owed: number; aging_count: number; aging_owed: number; total_owed: number }[]
  upcoming: { thursday: string; sold_owed: number; aging_owed: number; total_owed: number; count: number }[]
  rows: any[]
  total_due_rows: number
  /** WHICH SOURCE ANSWERED, named by the server so the screen never has to infer it.
   *  `stored_column` — the consignment ledger stamped the billing day itself.
   *  `shared_owed_read` — the declared cadence rolled the shared owed read up instead. */
  period_source?: string
  period_basis?: string
  declared_period?: Declared
  /** §CORE-17.3l — one panel per KPI, folded by the server over the By-Store rows below for THIS
   *  billing Friday. The device counts beside each KPI carry no panel: every panel row states them. */
  drilldown?: Record<string, DrillPanel>
}

// The extra columns these panels carry off each drill row's own `fields` (each with a `sortValue`,
// per harness_table_sort_lock).
const dtext = (r: DrillRow, field: string) => {
  const v = r.fields?.[field]
  return v == null || v === '' ? '' : String(v)
}
const WEEK_DRILL_COLUMNS: DrillExtraColumn[] = [
  { header: 'Market', get: r => dtext(r, 'market') || '—', sortValue: r => dtext(r, 'market') },
  { header: 'Devices', get: r => dtext(r, 'detail') || '—', sortValue: r => dtext(r, 'detail') },
]

// A BASIS IS A WORD, NOT A NUMBER (§COMMCALC-34.3). The server names what said so; the screen
// prints that word rather than deciding anything, so a basis it has never seen reads as itself
// instead of silently rendering as "the carrier's own billing day".
const BASIS_WORDS: Record<string, string> = {
  period_field: "the carrier's own stamped billing period",
  weekly_declared: "the weekly billing day declared for this carrier",
  due_date: "each invoice's own due date — this carrier has no weekly batch",
  house_default: "each invoice's own due date (no billing rhythm is declared for this carrier)",
  weekday_undeclared: "each invoice's own due date — a weekly cadence is declared but NOT which weekday",
  no_date: 'no due date was recorded',
}
const basisWord = (b?: string) => (b && BASIS_WORDS[b]) || b || 'not stated'

function Kpi({ label, value, sub, color, children }: { label: string; value: string; sub?: string; color?: string; children?: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: '18px 22px' }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color: color || 'var(--text1)' }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>{sub}</div>}
      {children}
    </div>
  )
}

export default function OwedWeeklyPage() {
  // Selected billing Friday (YYYY-MM-DD). Sent to the API under the legacy `thursday` key.
  const [friday, setFriday] = useState(ymd(upcomingFriday()))
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [selStores, setSelStores] = useState<string[]>([])
  const [markets, setMarkets] = useState<string[]>([])
  const [stores, setStores] = useState<{store:string;market:string}[]>([])
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)
  // §CORE-17.3l — the Friday, the markets and the stores are all QUERY PARAMETERS, so the server's
  // fold already covers exactly what is on screen; `lib/drill-narrow` is NOT wired in.
  const panels: Record<string, DrillPanel> = report?.drilldown || {}
  const drill = useDrillTile(panels)

  useEffect(() => {
    apiCached(`/api/v1/asset/filter-options?org_id=${ORG_ID}`, LOOKUP)
      .then((d:any) => { setMarkets(d.markets || []); setStores(d.stores || []) })
      .catch(console.error)
  }, [])

  useEffect(() => { load() }, [friday, selMarkets, selStores])

  async function load() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ org_id: ORG_ID, thursday: friday })
      if (selMarkets.length) qs.set('market', selMarkets.join(','))
      if (selStores.length) qs.set('store', selStores.join(','))
      const d = await api(`/api/v1/asset/owed-weekly?${qs.toString()}`)
      setReport(d)
    } catch(e) { console.error(e) }
    setLoading(false)
  }

  // §COMMCALC-34 — what ANSWERED, read off the server's own word rather than inferred from a zero.
  const dec = report?.declared_period
  const onShared = report?.period_source === 'shared_owed_read'

  const visibleStores = selMarkets.length ? stores.filter(s => selMarkets.includes(s.market)) : stores
  // When markets change, drop any selected store no longer in the visible set.
  function onMarketsChange(vs: string[]) {
    setSelMarkets(vs)
    const allowed = new Set((vs.length ? stores.filter(s => vs.includes(s.market)) : stores).map(s => s.store))
    setSelStores(prev => prev.filter(st => allowed.has(st)))
  }

  function buildPayload(): ExportPayload {
    const parts: string[] = []
    if (selMarkets.length) parts.push(selMarkets.join(', '))
    if (selStores.length) parts.push(selStores.join(', '))
    const filterLabel = parts.join(' · ') || 'All markets'
    return {
      title: 'Weekly Owed to Distributor',
      subtitle: `Billing ${pretty(friday)} · ${filterLabel}`,
      filename: `owed-weekly-${friday}${selStores.length===1?'-'+selStores[0].replace(/[^a-z0-9]+/gi,'-').toLowerCase():selStores.length>1?'-'+selStores.length+'stores':''}`,
      sheets: [
        // §COMMCALC-34 — the export carries the SAME figures the screen does. Where the declared
        // roll-up answered, the two consignment sheets are empty by construction, so the exported
        // file would otherwise be three blank columns of a carrier arrangement this tenant has not
        // got. The period sheet prints "not measured" in words rather than a 0.00 a spreadsheet
        // would then happily total.
        ...(onShared && report?.declared_period && !report.declared_period.error ? [
          { name:'Billing periods', rows:report.declared_period.periods||[], columns:[
            { header:'Billing period', get:(r:any)=>r.label },
            { header:'Covers from', get:(r:any)=>r.start||'' },
            { header:'Covers to', get:(r:any)=>r.end||'' },
            { header:report.declared_period.grain === 'order_line' ? 'Order lines' : 'Devices', get:(r:any)=>r.count||0, align:'right' as const },
            { header:'Owed', get:(r:any)=> (r.measured && r.amount != null ? r.amount : 'not measured') },
            { header:'What said so', get:(r:any)=>basisWord(r.basis) },
          ]},
        ] : []),
        { name:'By Store', rows:report?.by_store||[], columns:[
          { header:'Store', get:r=>r.store },
          { header:'Market', get:r=>r.market },
          { header:'Sold #', get:r=>r.sold_count, align:'right' },
          { header:'Sold Owed', get:r=>r.sold_owed, money:true },
          { header:'Aged #', get:r=>r.aging_count, align:'right' },
          { header:'Aged Owed', get:r=>r.aging_owed, money:true },
          { header:'Total Owed', get:r=>r.total_owed, money:true },
        ]},
        { name:'Devices', rows:report?.rows||[], columns:[
          { header:'Store', get:r=>r.store },
          { header:'Device', get:r=>r.device_model },
          { header:'IMEI/ESN', get:r=>r.esn_imei },
          { header:'Phone', get:r=>r.phone_number },
          { header:'Contract', get:r=>r.contract_type },
          { header:'Path', get:r=>r.bill_path },
          { header:'Sold', get:r=> r.date_sold ? String(r.date_sold).slice(0,10) : '' },
          { header:'Due', get:r=> r.due_date ? String(r.due_date).slice(0,10) : '' },
          { header:'Owed', get:r=>r.owed_to_vip, money:true },
          { header:'Distributor Invoice #', get:r=>r.vip_invoice_number },
          { header:'Distributor Invoice Date', get:r=> r.vip_invoice_date ? String(r.vip_invoice_date).slice(0,10) : '' },
        ]},
      ],
    }
  }

  return (
    <div>
      <div style={{ marginBottom: 20, display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:12, flexWrap:'wrap' }}>
        <div>
          <a href="/commcalc/asset" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Asset Ledger</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>Weekly Owed to Distributor</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {onShared
              ? 'What is owed to the distributor in this period. This carrier raises an invoice per order rather than billing a weekly batch, so the money is grouped by its own declared billing cadence — over the same payable rows the Daily Owed screen reads.'
              : 'What the Distributor collects each Friday — phones sold (billed the following Friday) and aged inventory past 60 days.'}
          </p>
        </div>
        {report && <ExportButtons payload={buildPayload} />}
        {report && <SendReportButton reportKey="owed_weekly" filters={{ thursday: friday, ...(selStores.length?{store:selStores.join(',')}:{}), ...(selMarkets.length?{market:selMarkets.join(',')}:{}) }} />}
      </div>

      {/* Controls */}
      <div className="card" style={{ padding: 14, marginBottom: 20, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn" onClick={() => setFriday(shiftWeek(friday, -1))}>◀ Prev</button>
        <div style={{ fontWeight: 700, fontSize: 15, minWidth: 200, textAlign: 'center' }}>{pretty(friday)}</div>
        <button className="btn" onClick={() => setFriday(shiftWeek(friday, 1))}>Next ▶</button>
        <button className="btn" onClick={() => setFriday(ymd(upcomingFriday()))}>This week</button>
        <div style={{ flex: 1 }} />
        <MultiSelect allLabel="All markets" width={150} value={selMarkets} options={markets} onChange={onMarketsChange} />
        <MultiSelect allLabel="All stores" width={150} value={selStores} searchable
          options={visibleStores.map(s => ({ value: s.store }))} onChange={setSelStores} />
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>Loading…</div>
      ) : !report ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>No data.</div>
      ) : (
        <>
          {/* ── §COMMCALC-34 — THE DECLARED BILLING PERIOD ────────────────────────────────────
              Served only where the consignment ledger answered nothing. Everything below this
              block reads `asset_ledger.billing_friday` — one carrier's weekly consignment rhythm
              — so for a carrier that raises an invoice per order it was all Σ over an empty set,
              and the screen said so by saying nothing. */}
          {dec && (
            <div className="card" style={{ padding: 0, marginBottom: 24, borderLeft: '3px solid var(--accent)' }}>
              <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
                🧾 Owed in this period
                <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12, marginLeft: 8 }}>
                  {dec.window?.start && dec.window?.end
                    ? `${pretty(dec.window.start)} → ${pretty(dec.window.end)}`
                    : 'window not resolved'}
                </span>
              </div>

              {/* A SOURCE THAT COULD NOT BE READ IS REPORTED, never read as "nothing is owed"
                  (§COMMCALC-29). A $0.00 here would be a measurement this screen does not have. */}
              {dec.error ? (
                <div style={{ padding: '16px 18px', fontSize: 13, color: 'var(--c-fg-d97706)' }}>
                  <strong>Not measured.</strong> This carrier has no weekly consignment batch, so the
                  figures below are read from the per-invoice payable rows instead — and that read
                  could not be completed just now. Nothing here means &quot;nothing is owed&quot;; it
                  means this screen does not know. Reload, and if it persists the roll-up is reported
                  rather than guessed.
                </div>
              ) : (
                <>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, padding: 18 }}>
                    <Kpi label="Total owed in this period"
                         value={dec.total_owed == null ? '—' : fmt(dec.total_owed)}
                         sub={`${(dec.periods || []).reduce((a, p) => a + (p.count || 0), 0).toLocaleString()} ${dec.grain === 'order_line' ? 'order lines' : 'devices'}`}
                         color="var(--accent)" />
                    <Kpi label="Billing periods inside it"
                         value={(dec.periods || []).length.toLocaleString()}
                         sub={basisWord(report.period_basis)} />
                    {/* NOTHING IS DROPPED AND NOTHING IS GUESSED — money nobody could date is
                        reported BESIDE the periods, never folded into the nearest one. */}
                    <Kpi label="Could not be dated"
                         value={dec.unplaced_rows ? (dec.unplaced_amount == null ? '—' : fmt(dec.unplaced_amount)) : '$0.00'}
                         sub={dec.unplaced_rows
                           ? `${dec.unplaced_rows.toLocaleString()} row${dec.unplaced_rows === 1 ? '' : 's'} carry no due date — reported, not dated`
                           : 'every row carried a due date'}
                         color={dec.unplaced_rows ? 'var(--c-fg-d97706)' : undefined} />
                  </div>

                  {/* The cadence sentence and the grain sentence come from the SERVER, one home
                      each, so the screen says the same thing wherever it says it. */}
                  {(dec.note || dec.source_note) && (
                    <div style={{ padding: '0 18px 14px', fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
                      {dec.note && <div>{dec.note}</div>}
                      {dec.source_note && <div style={{ marginTop: 6 }}>{dec.source_note}</div>}
                    </div>
                  )}

                  <div style={{ overflowX: 'auto', borderTop: '1px solid var(--border)' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 620 }}>
                      <thead>
                        <tr style={{ background: 'var(--surface2)' }}>
                          {['Billing period', 'Covers', dec.grain === 'order_line' ? 'Order lines' : 'Devices', 'Owed', 'What said so'].map(h => (
                            <th key={h} style={{ textAlign: 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {(dec.periods || []).length === 0 ? (
                          <tr><td colSpan={5} style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>
                            No payable rows fall in this period for the current filter.
                          </td></tr>
                        ) : dec.periods.map((pd, i) => (
                          <tr key={pd.key || `unplaced-${i}`} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                            <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 500 }}>{pd.label}</td>
                            <td style={{ padding: '9px 14px', fontSize: 12, color: 'var(--text2)', whiteSpace: 'nowrap' }}>
                              {pd.start && pd.end ? (pd.start === pd.end ? pretty(pd.start) : `${pretty(pd.start)} → ${pretty(pd.end)}`) : '—'}
                            </td>
                            <td style={{ padding: '9px 14px', fontSize: 13 }}>{(pd.count || 0).toLocaleString()}</td>
                            {/* A PERIOD NOBODY MEASURED IS SHOWN WITHOUT A FIGURE — never as $0.00,
                                which the reader would take for a measured zero (§CORE-17.3i). */}
                            <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 700 }}>
                              {pd.measured && pd.amount != null ? fmt(pd.amount)
                                : <span style={{ fontWeight: 400, color: 'var(--text3)' }}>not measured</span>}
                            </td>
                            <td style={{ padding: '9px 14px', fontSize: 12, color: 'var(--text2)' }}>{basisWord(pd.basis)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}

          {/* The consignment sections below are this report's ORIGINAL path and are left exactly as
              they were. They are hidden only where the declared roll-up really answered, because
              three $0.00 cards and "Nothing bills on this Friday" printed under a real total would
              contradict the figure above them. */}
          {!onShared && <>
          {/* KPI cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 24 }}>
            <Kpi label="Total Due This Friday" value={fmt(report.due_this_week.total.owed)} sub={`${report.due_this_week.total.count.toLocaleString()} devices`} color="var(--accent)">
              <DrillTileLinks drills={drill.dl('total_owed', 'the stores behind this')} open={drill.open} onToggle={drill.toggle} />
            </Kpi>
            <Kpi label="Sold Phones" value={fmt(report.due_this_week.sold.owed)} sub={`${report.due_this_week.sold.count.toLocaleString()} sold — billed this week`} color="var(--c-fg-059669)">
              <DrillTileLinks drills={drill.dl('sold_owed', 'the stores behind this')} open={drill.open} onToggle={drill.toggle} />
            </Kpi>
            <Kpi label="Aged > 60 Days (Never Sold)" value={fmt(report.due_this_week.aging.owed)} sub={`${report.due_this_week.aging.count.toLocaleString()} devices past due date`} color="var(--c-fg-d97706)">
              <DrillTileLinks drills={drill.dl('aging_owed', 'the stores behind this')} open={drill.open} onToggle={drill.toggle} />
            </Kpi>
          </div>

          {/* ONE panel open at a time, below the strip — the shared renderer. */}
          {drill.panel && (
            <div style={{ marginBottom: 24 }}>
              <DrillDownPanel panel={drill.panel} extraColumns={WEEK_DRILL_COLUMNS} />
            </div>
          )}

          {/* By store */}
          <div className="card" style={{ padding: 0, marginBottom: 24 }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
              🏬 By Store — due {pretty(report.thursday)}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Store','Market','Sold #','Sold Owed','Aged #','Aged Owed','Total Owed'].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report.by_store.length === 0 ? (
                    <tr><td colSpan={7} style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>Nothing bills on this Friday for the current filter.</td></tr>
                  ) : report.by_store.map((s, i) => (
                    <tr key={s.store} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                      <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 500 }}>{s.store}</td>
                      <td style={{ padding: '9px 14px', fontSize: 12, color: 'var(--text2)' }}>{s.market || '—'}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13 }}>{s.sold_count}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13, color: 'var(--c-fg-059669)' }}>{fmt(s.sold_owed)}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13 }}>{s.aging_count}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13, color: 'var(--c-fg-d97706)' }}>{fmt(s.aging_owed)}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 700 }}>{fmt(s.total_owed)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Upcoming Fridays */}
          <div className="card" style={{ padding: 0, marginBottom: 24 }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
              📅 Upcoming Fridays
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--surface2)' }}>
                  {['Friday','Devices','Sold Owed','Aged Owed','Total'].map(h => (
                    <th key={h} style={{ textAlign: 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {report.upcoming.length === 0 ? (
                  <tr><td colSpan={5} style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>No upcoming billings in range.</td></tr>
                ) : report.upcoming.map((u, i) => (
                  <tr key={u.thursday}
                      onClick={() => setFriday(u.thursday)}
                      style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)', cursor: 'pointer' }}>
                    <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 500 }}>{pretty(u.thursday)}</td>
                    <td style={{ padding: '9px 14px', fontSize: 13 }}>{u.count.toLocaleString()}</td>
                    <td style={{ padding: '9px 14px', fontSize: 13, color: 'var(--c-fg-059669)' }}>{fmt(u.sold_owed)}</td>
                    <td style={{ padding: '9px 14px', fontSize: 13, color: 'var(--c-fg-d97706)' }}>{fmt(u.aging_owed)}</td>
                    <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 700 }}>{fmt(u.total_owed)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Device rows */}
          <div className="card" style={{ padding: 0 }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
              📱 Devices billing this Friday <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>({report.rows.length.toLocaleString()} of {report.total_due_rows.toLocaleString()})</span>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 820 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Store','Device','IMEI/ESN','Phone','Contract','Path','Sold','Due','Owed','Distributor Invoice #','Invoice Date'].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report.rows.map((r, i) => (
                    <tr key={r.id} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.store || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.device_model || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 11, fontFamily: 'monospace' }}>{r.esn_imei || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.phone_number || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.contract_type || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 11 }}>
                        <span style={{ background: r.bill_path === 'aging' ? '#fef3c7' : '#d1fae5', color: r.bill_path === 'aging' ? '#92400e' : '#065f46', borderRadius: 5, padding: '1px 7px', fontWeight: 600 }}>{r.bill_path}</span>
                      </td>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.date_sold || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.due_date || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, fontWeight: 600 }}>{fmt(r.owed_to_vip || 0)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 11, fontFamily: 'monospace' }}>{r.vip_invoice_number || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.vip_invoice_date ? String(r.vip_invoice_date).slice(0,10) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          </>}
        </>
      )}
    </div>
  )
}