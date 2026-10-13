'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, apiUpload, fmt, getActiveOrg } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { SetupNotice } from '@/lib/setupNotice'

// STORE TAKEOVER EVALUATION — owner directive 2026-10-12: *"need to make a module for evaluating the
// store sales for the store we need to take over, but without creating the actual stores but upload
// the ma comm, max tx and handset prdering data, import the expenses from our average expenses, keep
// the rent as what the actual rent is for the new store and calculate the p&l also need to fillter
// out the family plans with more than 2 lines from these reports for each month to assess if fraud is
// going on and cjhecking which phones are sold on those, create a sub report in that menu to take out
// those phones from profitability or leave them"*.
//
// THE BACKEND DOES ALL OF THE MATH AND ALL OF THE HONESTY (commcalc/prospect_eval.py, index
// §COMMCALC-03): every P&L line is booked by the same one-homes the company's own P&L books through,
// the expense average comes from the shared carry-forward reader, and every line the three uploads
// cannot answer comes back with a verdict and a reason. This page renders and never computes.
//
// IN PARTICULAR it never fills a blank with a 0. A line marked `not_measured` has no figure because
// nothing here can answer it, and a 0.00 would read as a measurement — which on a takeover decision
// is the difference between "this store takes no accessory money" and "we cannot see its register".

// ── The payload, typed. Written out rather than left as `any` because these field names ARE the
//    contract with prospect_eval.evaluate(), and a rename on either side should fail the typecheck.
type Dollars = number | null

interface FeedInfo {
  label: string
  report_key: string
  live_table: string | null
  grain: string
  answers: string
  carries_account_identity: boolean
  carries_device: boolean
}
interface Prospect {
  id: string
  name: string
  address?: string | null
  status?: string
  actual_rent_monthly?: number | string | null
  exclusion_mode?: string | null
  family_line_threshold?: number | null
  expense_basis_months?: number | null
  feed_rows?: Record<string, Record<string, number>>   // {period: {feed_key: rows}}
  periods?: string[]
}
interface FlaggedDevice { sku: string; lines: number; imeis: string[] }
interface FlaggedAccount {
  period: string
  account_key: string
  account_column: string
  lines: number
  over_threshold_by: number
  add_a_lines: number
  days: string[]
  same_day: boolean
  processor_accounts: string[]
  same_processor_account: boolean
  devices: FlaggedDevice[]
  line_ids: string[]
}
interface MultiLineSummary {
  threshold: number
  accounts: number
  lines: number
  lines_with_no_account: number
  account_columns_used: Record<string, number>
  lines_per_account: Record<string, number>
  flagged_accounts: number
  flagged_lines: number
  max_lines_on_one_account: number
}
interface ExpenseCell {
  expense_name: string
  expense_type?: string | null
  source_key?: string | null
  total: number
  months_present: number
  months_in_window: number
  months_missing: string[]
  stores: number
  store_coverage: number | null
  amount_if_carried: Dollars
  amount_all_stores: Dollars
  amount: Dollars
  basis: string
  origin: 'average' | 'actual'
  replaced_average?: Dollars
}
interface ExpenseWindow {
  months?: string[]
  months_in_window?: number
  lines?: number
  lines_absent_from_some_months?: { expense_name: string; months_present: number; months_in_window: number; months_missing: string[] }[]
  lines_not_on_every_store?: { expense_name: string; store_coverage: number; amount_if_carried: Dollars; amount_all_stores: Dollars }[]
  note?: string
  carried_from?: Record<string, string>
  error?: string
}
interface RentMeta {
  applied: boolean
  reason: string | null
  rent_lines: string[]
  entered: Dollars
  replaced_average: Dollars
}
interface CoverageLine {
  key: string
  label: string
  section: string
  verdict: 'measured' | 'not_measured' | 'uploaded_feed_missing'
  reason: string | null
  feeds: string[]
}
interface CoverageSummary {
  lines: number
  measured: number
  not_measured: number
  uploaded_feed_missing: number
  bottom_line_is: string
  bottom_line_note: string
}
interface Scenario {
  mode: string
  device_cost: number
  device_cost_meta: Record<string, number | string>
  commission_lines_kept: number
  commission_lines_dropped: number
  activations?: number
  coverage: CoverageLine[]
  coverage_summary: CoverageSummary
  measured_lines: string[]
}
interface StatementLine { key: string; label: string; amount: number; kind: string; note?: string }
interface StatementSection { name: string; type: string; lines: StatementLine[]; subtotal: number }
interface Statement {
  sections: StatementSection[]
  gross_profit: number
  net_income: number
  totals: { revenue: number; cogs: number; opex: number; other: number; expenses: number; gross_profit: number; net_income: number }
}
interface DeltaRow { keep: number; exclude: number; delta: number }
interface Delta {
  revenue: DeltaRow; cogs: DeltaRow; gross_profit: DeltaRow
  opex: DeltaRow; expenses: DeltaRow; net_income: DeltaRow
  share_of_bottom_line_at_risk: number | null
}
interface Evaluation {
  period?: string
  prospect?: Prospect
  feed_registry?: Record<string, FeedInfo>
  feeds_present?: string[]
  feeds_missing?: string[]
  rows_by_feed?: Record<string, number>
  period_basis?: { period?: string; basis?: string; from_feed?: string | null; months_available?: string[] }
  multi_line?: { summary: MultiLineSummary; flagged: FlaggedAccount[]; flagged_line_ids: string[]; flagged_imeis: string[] }
  expenses?: { matrix: Record<string, ExpenseCell>; rent: RentMeta; months?: string[]; window?: ExpenseWindow }
  default_mode?: string
  scenarios?: Record<string, Scenario>
  statements?: Record<string, Statement>
  delta?: Delta
  stores?: Record<string, ProspectStore>
  store_scenarios?: Record<string, StoreBlock>
  handset_orders?: HandsetOrders
  fraud?: FraudExposure
  break_even?: Record<string, BreakEven>
  error?: string
}
interface ProspectStore {
  account: string | null; address: string | null; name: string | null
  label: string; has_address: boolean; feeds: string[]
}
interface BreakEven {
  revenue: number; cost_of_goods: number; expenses: number; net_income: number
  activations: number
  needed: number | null
  per_activation: number | null
  already_profitable: boolean
  basis: string
  per_activation_basis: string
}
interface StoreBlock {
  store: ProspectStore
  expenses_included: boolean
  scenarios: Record<string, Scenario>
  statements?: Record<string, Statement>
  delta?: Delta
  break_even?: Record<string, BreakEven>
}
interface HandsetModel { model: string; orders: number; units: number; cost: number; unit_price_low: number; unit_price_high: number }
interface HandsetStore { store: string; account: string | null; orders: number; units: number; cost: number }
interface HandsetMonth { month: string; orders: number; units: number; cost: number }
interface HandsetOrders {
  models: HandsetModel[]
  by_store: HandsetStore[]
  by_month: HandsetMonth[]
  totals: { rows: number; priced_rows: number; orders: number; units: number; cost: number; models: number; stores: number }
  set_aside: { rows: number; order_types: Record<string, number>; reason: string }
}
interface FraudExposure {
  threshold: number; accounts: number; lines: number
  accounts_in_file: number; lines_in_file: number
  bands: Record<string, number>
  worst_account_lines: number
  net_with_them: number | null
  net_without_them: number | null
  moved: number | null
  share_of_bottom_line: number | null
  reading: string
}
interface ProspectList {
  prospects?: Prospect[]
  feed_registry?: Record<string, FeedInfo>
  config_columns_missing?: Record<string, string>
  error?: string
}
interface UploadResult {
  feed_label: string
  rows_written: number
  periods_replaced: string[]
  footer_rows_dropped: number
  rows_with_no_date: number
  rows_answering_nothing?: number
  substance_note?: string | null
}
interface PlRow { section: string; label: string; amount: number; key: string }

const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }
const orgParam1 = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

function money(v: Dollars | undefined): string {
  if (v === null || v === undefined) return '—'
  return fmt(v)
}
function num(v: number | string | null | undefined, digits = 0): string {
  if (v === null || v === undefined || v === '') return '—'
  const n = typeof v === 'number' ? v : Number(v)
  if (!Number.isFinite(n)) return String(v)
  return digits ? n.toFixed(digits) : n.toLocaleString()
}
function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(digits)}%`
}
function errText(e: unknown): string {
  if (e && typeof e === 'object' && 'message' in e) return String((e as { message?: unknown }).message)
  return String(e)
}

const VERDICT: Record<string, { bg: string; fg: string; icon: string }> = {
  measured: { bg: 'var(--surface2)', fg: 'var(--text2)', icon: '✓' },
  not_measured: { bg: '#fef3c7', fg: '#92400e', icon: '◻︎' },
  uploaded_feed_missing: { bg: '#fee2e2', fg: '#991b1b', icon: '⚑' },
}

// Hoisted out of the page component deliberately: a component created during render is a new type on
// every render, which remounts its subtree and loses any state inside it.
function Tile({ label, value, sub, warn }: { label: string; value: string; sub?: string; warn?: boolean }) {
  return (
    <div className="card" style={{ padding: '12px 16px', minWidth: 190 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 3, color: warn ? '#92400e' : 'var(--text1)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

const PL_COLS: ExportColumn[] = [
  { header: 'Section', get: (r: PlRow) => r.section },
  { header: 'Line', get: (r: PlRow) => r.label },
  { header: 'Amount', get: (r: PlRow) => r.amount, money: true },
]
const FLAG_COLS: ExportColumn[] = [
  { header: 'Month', get: (r: FlaggedAccount) => r.period },
  { header: 'Account', get: (r: FlaggedAccount) => r.account_key },
  { header: 'Account column', get: (r: FlaggedAccount) => r.account_column },
  { header: 'Lines', get: (r: FlaggedAccount) => r.lines, align: 'right' },
  { header: 'Add-a-lines', get: (r: FlaggedAccount) => r.add_a_lines, align: 'right' },
  { header: 'All on one day', get: (r: FlaggedAccount) => (r.same_day ? 'yes' : 'no') },
  { header: 'Days', get: (r: FlaggedAccount) => (r.days || []).join(', ') },
  { header: 'One store account', get: (r: FlaggedAccount) => (r.same_processor_account ? 'yes' : 'no') },
  {
    header: 'Phones sold on it',
    get: (r: FlaggedAccount) => (r.devices || [])
      .map(d => `${d.sku || '(no model)'}${d.lines > 1 ? ` ×${d.lines}` : ''}`).join(' · '),
  },
  {
    header: 'IMEIs',
    get: (r: FlaggedAccount) => (r.devices || []).flatMap(d => d.imeis || []).join(' '),
  },
]

// ── THE NEW TABLES (owner ask 2026-10-13) ────────────────────────────────────────────────────────
// Every one goes through ReportShell, which is the platform's ONE sorted/exportable table (owner
// directive 2026-08-10, locked by harness_table_sort_lock) — never a hand-rolled <table>.
const STORE_COLS: ExportColumn[] = [
  { header: 'Store', get: (r: StoreRow) => r.label },
  { header: 'Account', get: (r: StoreRow) => r.account || '—' },
  { header: 'Carrier revenue', get: (r: StoreRow) => r.revenue, money: true },
  { header: 'Device cost', get: (r: StoreRow) => r.cogs, money: true },
  { header: 'Profit or loss on measured revenue', get: (r: StoreRow) => r.contribution, money: true },
  { header: 'Activated lines', get: (r: StoreRow) => r.activations, align: 'right' },
  { header: 'Must sell in accessories', get: (r: StoreRow) => r.needed, money: true },
  { header: 'Accessories per box needed', get: (r: StoreRow) => r.perBox, money: true },
]
const HANDSET_MODEL_COLS: ExportColumn[] = [
  { header: 'Model', get: (r: HandsetModel) => r.model },
  { header: 'Orders', get: (r: HandsetModel) => r.orders, align: 'right' },
  { header: 'Units', get: (r: HandsetModel) => r.units, align: 'right' },
  { header: 'Cost', get: (r: HandsetModel) => r.cost, money: true },
  { header: 'Unit price', get: (r: HandsetModel) => (r.unit_price_low === r.unit_price_high ? money(r.unit_price_low) : `${money(r.unit_price_low)} – ${money(r.unit_price_high)}`), align: 'right' },
]
const HANDSET_STORE_COLS: ExportColumn[] = [
  { header: 'Store', get: (r: HandsetStore) => r.store },
  { header: 'Orders', get: (r: HandsetStore) => r.orders, align: 'right' },
  { header: 'Units', get: (r: HandsetStore) => r.units, align: 'right' },
  { header: 'Cost', get: (r: HandsetStore) => r.cost, money: true },
]
const HANDSET_MONTH_COLS: ExportColumn[] = [
  { header: 'Month', get: (r: HandsetMonth) => r.month },
  { header: 'Orders', get: (r: HandsetMonth) => r.orders, align: 'right' },
  { header: 'Units', get: (r: HandsetMonth) => r.units, align: 'right' },
  { header: 'Cost', get: (r: HandsetMonth) => r.cost, money: true },
]
// ── THE P&L AS A MATRIX: a LINE down the side, a STORE across the top (owner ask 2026-10-13:
// "it shoudl ahve 8 columns for each seaporate store"). Every cell is read from THAT store's own
// statement, which the server assembled with the same builder as the whole-prospect one — so a
// column never disagrees with the report above it, and the columns add across to "All stores".
interface MatrixRow {
  section: string
  label: string
  key: string
  kind: 'line' | 'total' | 'count'
  cells: Record<string, number | null>
  all: number | null
}
interface StoreRow {
  label: string; account: string | null; revenue: number; cogs: number; contribution: number
  activations: number; needed: number | null; perBox: number | null
}
const EXP_COLS: ExportColumn[] = [
  { header: 'Expense line', get: (r: ExpenseCell) => r.expense_name },
  { header: 'Booked', get: (r: ExpenseCell) => r.amount, money: true },
  { header: 'From', get: (r: ExpenseCell) => (r.origin === 'actual' ? "the prospect's actual" : 'our average') },
  { header: 'A store that has it pays', get: (r: ExpenseCell) => r.amount_if_carried, money: true },
  { header: 'A typical store pays', get: (r: ExpenseCell) => r.amount_all_stores, money: true },
  {
    header: 'On this share of stores',
    get: (r: ExpenseCell) => pct(r.store_coverage === null ? null : r.store_coverage * 100, 0),
    align: 'right',
  },
  { header: 'Months answered', get: (r: ExpenseCell) => `${r.months_present} of ${r.months_in_window}` },
  { header: 'Months missing', get: (r: ExpenseCell) => (r.months_missing || []).join(', ') || '—' },
  { header: 'Average it replaced', get: (r: ExpenseCell) => (r.replaced_average ?? null), money: true },
]

export default function StoreTakeoverPage() {
  const [list, setList] = useState<ProspectList | null>(null)
  const [pid, setPid] = useState('')
  const [period, setPeriod] = useState('')
  // The sub-report's filters. Deliberately NOT in the URL or stored: a filtered view that came back
  // tomorrow and hid rows somebody never chose to hide is how a list stops being trusted.
  const [fMinLines, setFMinLines] = useState('')
  const [fSameDay, setFSameDay] = useState(false)
  const [fAddALine, setFAddALine] = useState(false)
  const [fAccount, setFAccount] = useState('')
  const [fDevice, setFDevice] = useState('')
  const [mode, setMode] = useState('')          // '' = open on the prospect's own default
  const [data, setData] = useState<Evaluation | null>(null)
  const [loadedKey, setLoadedKey] = useState('')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [newName, setNewName] = useState('')
  const [rentDraft, setRentDraft] = useState('')
  // The prospect whose stored rent is in `rentDraft`. Compared rather than ignored, so a reload of
  // the same prospect never overwrites a figure the user is halfway through typing.
  const [rentFor, setRentFor] = useState('')

  const loadList = useCallback(() => {
    api(`/api/v1/commcalc/prospects${orgParam1()}`)
      .then((d: ProspectList) => {
        setList(d)
        const first = (d?.prospects || [])[0]
        setPid(p => p || (first ? String(first.id) : ''))
      })
      .catch(e => setList({ error: errText(e) }))
  }, [])
  useEffect(() => { loadList() }, [loadList])

  // The evaluation read. `loadedKey` is what makes "loading" a DERIVED value rather than a state set
  // synchronously inside the effect — the latter triggers the cascading re-render React now rejects.
  const key = pid ? `${pid}|${period}` : ''
  const rentKeyRef = rentFor
  const load = useCallback(() => {
    if (!pid) return
    const qs = new URLSearchParams()
    if (period) qs.set('period', period)
    api(`/api/v1/commcalc/prospects/${pid}/evaluation?${qs.toString()}${orgParam()}`)
      .then((d: Evaluation) => {
        setData(d)
        if (rentKeyRef !== pid) {
          setRentFor(pid)
          setRentDraft(d?.prospect?.actual_rent_monthly == null ? '' : String(d.prospect.actual_rent_monthly))
        }
      })
      .catch(e => setData({ error: errText(e) }))
      .finally(() => setLoadedKey(`${pid}|${period}`))
  }, [pid, period, rentKeyRef])
  useEffect(() => { load() }, [load])
  const loading = !!pid && loadedKey !== key

  const prospects: Prospect[] = list?.prospects || []
  const prospect: Prospect | null = data?.prospect
    || prospects.find(p => String(p.id) === pid)
    || null
  const registry: Record<string, FeedInfo> = data?.feed_registry || list?.feed_registry || {}
  const shownMode = mode || data?.default_mode || 'keep'
  const scenario: Scenario | null = data?.scenarios?.[shownMode] || null
  const statement: Statement | null = data?.statements?.[shownMode] || null
  const breakEven: BreakEven | null = data?.break_even?.[shownMode] || null
  const handsets: HandsetOrders | null = data?.handset_orders || null
  const fraud: FraudExposure | null = data?.fraud || null
  const delta: Delta | null = data?.delta || null
  const flagged: FlaggedAccount[] = useMemo(
    () => data?.multi_line?.flagged || [], [data])

  // FILTERS ON THE SUB-REPORT (owner ask 2026-10-12: "need the filters also"). They narrow the LIST
  // only — never the P&L, and never the keep-vs-exclude figures above it. Those two numbers are what
  // the flagged accounts are worth in total, and a number that silently followed a filter would
  // answer a question nobody asked. The row count below the controls says what is being shown.
  const shownFlagged = useMemo(() => {
    const min = Number(fMinLines) || 0
    const needle = fDevice.trim().toLowerCase()
    const acct = fAccount.trim().toLowerCase()
    return flagged.filter(r =>
      (!min || (r.lines || 0) >= min)
      && (!fSameDay || !!r.same_day)
      && (!fAddALine || (r.add_a_lines || 0) > 0)
      && (!acct || String(r.account_key || '').toLowerCase().includes(acct))
      && (!needle || (r.devices || []).some(d =>
        String(d.sku || '').toLowerCase().includes(needle)
        || (d.imeis || []).some(i => String(i).toLowerCase().includes(needle)))))
  }, [flagged, fMinLines, fSameDay, fAddALine, fAccount, fDevice])
  const mlSummary: Partial<MultiLineSummary> = data?.multi_line?.summary || {}
  const coverage: CoverageLine[] = scenario?.coverage || []
  const covSummary: Partial<CoverageSummary> = scenario?.coverage_summary || {}
  const expWindow: ExpenseWindow = data?.expenses?.window || {}
  const rentMeta: Partial<RentMeta> = data?.expenses?.rent || {}
  const notMeasured = coverage.filter(c => c.verdict !== 'measured')
  const partialLines = expWindow.lines_absent_from_some_months || []

  // ── THE P&L, as the finance engine assembled it. The rows are the statement's own, so a line this
  //    screen shows is a line the company's chart of accounts has.
  const plRows = useMemo<PlRow[]>(() => {
    const out: PlRow[] = []
    for (const sec of (statement?.sections || [])) {
      for (const ln of (sec.lines || [])) {
        if (!ln.amount) continue
        out.push({ section: sec.name, label: ln.label, amount: ln.amount, key: ln.key })
      }
    }
    return out
  }, [statement])

  // A ROW PER STORE, built from the per-store statements the server assembled with the SAME builder
  // as the whole-prospect statement. Nothing is re-summed here: a figure the screen computes is a
  // figure that can disagree with the report above it.
  const storeRows = useMemo<StoreRow[]>(() => {
    const blocks = data?.store_scenarios || {}
    return Object.keys(blocks).map(k => {
      const b = blocks[k]
      const t = b.statements?.[shownMode]?.totals
      const be = b.break_even?.[shownMode]
      return {
        label: b.store?.label || k,
        account: b.store?.account || null,
        revenue: t?.revenue ?? 0,
        cogs: t?.cogs ?? 0,
        contribution: t?.net_income ?? 0,
        activations: b.scenarios?.[shownMode]?.activations ?? 0,
        needed: be?.needed ?? null,
        perBox: be?.per_activation ?? null,
      }
    }).sort((a, b) => (b.contribution || 0) - (a.contribution || 0))
  }, [data?.store_scenarios, shownMode])

  // The columns ARE the stores, in the order the store index names them, with the whole prospect
  // last. Built with useMemo because a column list that is rebuilt every render re-mounts the table.
  const matrixStores = useMemo(() => {
    const blocks = data?.store_scenarios || {}
    return Object.keys(blocks)
      .map(k => ({ key: k, label: blocks[k].store?.label || k }))
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [data?.store_scenarios])

  // One row per P&L line the WHOLE-prospect statement carries (its own line vocabulary and order —
  // this screen invents no line), then the subtotals, the activated-line count and the two
  // break-even figures. Nothing is summed here: `all` is the whole statement's own figure, so if a
  // store's dollars were missed the row will visibly not add up rather than being quietly balanced.
  const matrixRows = useMemo<MatrixRow[]>(() => {
    const blocks = data?.store_scenarios || {}
    const keys = matrixStores.map(s => s.key)
    const lineOf = (st: Statement | undefined, k: string) =>
      (st?.sections || []).flatMap(sec => sec.lines || []).find(l => l.key === k)?.amount ?? null
    const out: MatrixRow[] = []
    for (const sec of (statement?.sections || [])) {
      for (const ln of (sec.lines || [])) {
        const cells: Record<string, number | null> = {}
        let any = false
        for (const k of keys) {
          const v = lineOf(blocks[k]?.statements?.[shownMode], ln.key)
          cells[k] = v
          if (v) any = true
        }
        if (!ln.amount && !any) continue
        out.push({ section: sec.name, label: ln.label, key: ln.key, kind: 'line', cells,
                   all: ln.amount ?? null })
      }
    }
    const totalRow = (label: string, pick: (t: Statement['totals']) => number) => {
      const cells: Record<string, number | null> = {}
      for (const k of keys) {
        const t = blocks[k]?.statements?.[shownMode]?.totals
        cells[k] = t ? pick(t) : null
      }
      return { section: 'Totals', label, key: 'total:' + label, kind: 'total' as const, cells,
               all: statement?.totals ? pick(statement.totals) : null }
    }
    out.push(totalRow('Carrier revenue', t => t.revenue))
    out.push(totalRow('Device cost', t => t.cogs))
    out.push(totalRow('Gross profit', t => t.gross_profit))
    out.push(totalRow('Expenses', t => t.expenses))
    // THE LINE HE ASKED FOR. With the full single-store expense now charged to every column
    // (owner directive 2026-10-13) this row is each store's profit or loss, not a contribution —
    // still on CARRIER revenue only, which §COMMCALC-03.7 says out loud and the label keeps saying.
    out.push(totalRow('Profit or loss on measured revenue', t => t.net_income))
    const actCells: Record<string, number | null> = {}
    for (const k of keys) actCells[k] = blocks[k]?.scenarios?.[shownMode]?.activations ?? null
    out.push({ section: 'Totals', label: 'Activated lines', key: 'total:activations', kind: 'count',
               cells: actCells, all: scenario?.activations ?? null })
    const beRow = (label: string, pick: (b: BreakEven) => number | null) => {
      const cells: Record<string, number | null> = {}
      for (const k of keys) {
        const b = blocks[k]?.break_even?.[shownMode]
        cells[k] = b ? pick(b) : null
      }
      return { section: 'Break-even', label, key: 'be:' + label, kind: 'total' as const, cells,
               all: breakEven ? pick(breakEven) : null }
    }
    out.push(beRow('Must sell in accessories', b => b.needed))
    out.push(beRow('Accessories per box needed', b => b.per_activation))
    return out
  }, [statement, data?.store_scenarios, matrixStores, shownMode, scenario?.activations, breakEven])

  const MATRIX_COLS = useMemo<ExportColumn[]>(() => ([
    { header: 'P&L line', get: (r: MatrixRow) => r.label, field: 'line' },
    { header: 'Section', get: (r: MatrixRow) => r.section, field: 'section' },
    ...matrixStores.map(st => ({
      header: st.label,
      field: 'store:' + st.key,
      get: (r: MatrixRow) => r.cells[st.key],
      money: true,
    })),
    { header: 'All stores', get: (r: MatrixRow) => r.all, money: true, field: 'all' },
  ]), [matrixStores])

  const expRows = useMemo<ExpenseCell[]>(() => {
    const m = data?.expenses?.matrix
    if (!m) return []
    return Object.keys(m).map(k => m[k]).sort((a, b) => (b.amount || 0) - (a.amount || 0))
  }, [data?.expenses?.matrix])

  const save = useCallback(async (path: string, body: unknown, method = 'POST') => {
    setBusy(path); setMsg(null)
    try {
      const r = await api(`/api/v1/commcalc${path}`, { method, body: JSON.stringify(body) })
      setMsg({ ok: true, text: 'Saved.' })
      loadList(); load()
      return r
    } catch (e) {
      setMsg({ ok: false, text: errText(e) })
      return null
    } finally { setBusy('') }
  }, [loadList, load])

  const uploadFeed = useCallback(async (feedKey: string, file: File) => {
    setBusy(feedKey); setMsg(null)
    const form = new FormData()
    form.append('feed_key', feedKey)
    form.append('file', file)
    if (period) form.append('period', period)
    try {
      const r: UploadResult = await apiUpload(`/api/v1/commcalc/prospects/${pid}/upload${orgParam1()}`, form)
      const hollow = (r.rows_answering_nothing || 0) > 0 && (r.rows_answering_nothing || 0) === (r.rows_written || 0)
      setMsg({
        ok: !hollow,
        text: `${r.feed_label}: ${(r.rows_written || 0).toLocaleString()} rows read into ${(r.periods_replaced || []).join(', ') || 'no month'}`
          + (r.footer_rows_dropped ? ` · ${r.footer_rows_dropped} total row(s) dropped` : '')
          + (r.rows_with_no_date ? ` · ${r.rows_with_no_date} row(s) had no readable date` : '')
          + (r.substance_note ? ` — ${r.substance_note}` : ''),
      })
      loadList(); load()
    } catch (e) {
      setMsg({ ok: false, text: errText(e) })
    } finally { setBusy('') }
  }, [pid, period, loadList, load])

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏷️ Store Takeover Evaluation</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Work out what a store you are <b>thinking of taking over</b> is worth, without creating the
          store. Upload its own carrier reports, and the evaluation books every dollar through the
          <b> same chart of accounts your own P&amp;L uses</b>, charges it <b>your stores&rsquo; average
          monthly expenses</b> with <b>its actual rent</b> in place of ours, and lists the subscriber
          accounts carrying <b>more lines in one month than they should</b> — with the phones sold on
          them, and the profit both with and without those phones.
        </p>
        <p className="pg-note" style={{ color: 'var(--text3)', fontSize: 12, margin: '6px 0 0' }}>
          Nothing here creates a store, a mapping or a statement. The rows you upload are read by this
          page and by nothing else — no P&amp;L, payout, payroll run or target can see them, so walking
          away from a deal leaves nothing behind.
        </p>
      </div>

      {list?.error &&
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: '#fee2e2', color: '#991b1b', fontSize: 13 }}>
          <b>❌ The prospect list could not be read.</b> {list.error}
        </div>}

      {list && Object.keys(list.config_columns_missing || {}).length > 0 &&
        <div className="card" style={{ padding: '10px 14px', marginBottom: 12, background: '#fef3c7', color: '#92400e', fontSize: 13 }}>
          <b>⚠︎ The per-company settings for this page are not available yet.</b>{' '}
          <SetupNotice
            lead={'The evaluation still runs on its built-in defaults \u2014 more than 2 lines on one ' +
                  'account, a 3-month expense basis, and rent read from the \u201cRent / Lease\u201d line ' +
                  '\u2014 but none of those can be changed for your company until then.'}
            detail={Object.values(list.config_columns_missing || {})} />
        </div>}

      {/* ── WHICH PROSPECT, WHICH MONTH ─────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Prospect{' '}
          <select style={{ ...sel, minWidth: 220 }} value={pid}
                  onChange={e => { setPid(e.target.value); setPeriod(''); setMode(''); setRentFor('') }}>
            <option value="">— pick a store being evaluated —</option>
            {prospects.map(p => (
              <option key={p.id} value={p.id}>{p.name}{p.status && p.status !== 'evaluating' ? ` · ${p.status}` : ''}</option>
            ))}
          </select>
        </label>
        {pid &&
          <label style={{ fontSize: 12, color: 'var(--text2)' }}
                 title="The month to evaluate. Blank opens on the latest month this prospect's own commission file covers.">
            Month{' '}
            <select style={sel} value={period} onChange={e => setPeriod(e.target.value)}>
              <option value="">latest uploaded</option>
              {(prospect?.periods || []).map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>}
        <span style={{ flex: 1 }} />
        <input style={{ ...sel, width: 180 }} placeholder="New prospect name" value={newName}
               onChange={e => setNewName(e.target.value)} />
        <button className="btn btn-secondary" style={{ fontSize: 12 }}
                disabled={!newName.trim() || busy !== ''}
                onClick={async () => {
                  const r = await save(`/prospects${orgParam1()}`, { name: newName.trim() })
                  const created = (r as { prospect?: Prospect } | null)?.prospect
                  if (created?.id) { setPid(String(created.id)); setNewName(''); setRentFor('') }
                }}>
          + Open an evaluation
        </button>
      </div>

      {msg &&
        <div className="card" style={{ padding: '10px 14px', marginBottom: 12, fontSize: 13, background: msg.ok ? 'var(--surface2)' : '#fee2e2', color: msg.ok ? 'var(--text2)' : '#991b1b' }}>
          {msg.ok ? '✓ ' : '❌ '}{msg.text}
        </div>}

      {!pid &&
        <div className="card" style={{ padding: '16px', fontSize: 13, color: 'var(--text2)' }}>
          Name the store you are evaluating above and press <b>Open an evaluation</b>. Then upload its
          three carrier reports and enter its rent.
        </div>}

      {pid && data?.error &&
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: '#fee2e2', color: '#991b1b', fontSize: 13 }}>
          <b>❌ The evaluation could not be built.</b> {data.error}
        </div>}

      {/* ── THE THREE UPLOADS, AND THE RENT ─────────────────────────────────────────────────── */}
      {pid && !data?.error &&
        <div className="card" style={{ padding: '14px 16px', marginBottom: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>What the evaluation is built from</div>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
            Each file is read with the same reader and the same column mapping your own uploads use, so
            a report that imports for your stores imports here. Uploading a month again replaces just
            that month of just that report.
          </div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {Object.keys(registry).map(k => {
              const f = registry[k]
              const counts = prospect?.feed_rows || {}
              const total = Object.keys(counts).reduce((n, p) => n + (counts[p]?.[k] || 0), 0)
              const here = period ? (counts[period]?.[k] || 0) : total
              return (
                <div key={k} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', minWidth: 250, flex: '1 1 260px' }}>
                  <div style={{ fontSize: 12, fontWeight: 700 }}>{f.label}</div>
                  <div style={{ fontSize: 11, color: 'var(--text3)', margin: '3px 0 7px' }}>{f.answers}</div>
                  <div style={{ fontSize: 12, color: total ? 'var(--text2)' : '#92400e', marginBottom: 7 }}>
                    {total
                      ? <>{here.toLocaleString()} row{here === 1 ? '' : 's'}{period ? ` in ${period}` : ' uploaded'}{total !== here ? ` (${total.toLocaleString()} in all)` : ''}</>
                      : <b>not uploaded yet</b>}
                  </div>
                  <input type="file" accept=".xlsx,.xls,.csv,.txt" disabled={busy !== ''}
                         style={{ fontSize: 11, maxWidth: '100%' }}
                         onChange={e => {
                           const fl = e.target.files?.[0]
                           e.target.value = ''
                           if (fl) uploadFeed(k, fl)
                         }} />
                </div>
              )
            })}
            <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', minWidth: 250, flex: '1 1 260px' }}>
              <div style={{ fontSize: 12, fontWeight: 700 }}>This store&rsquo;s actual rent</div>
              <div style={{ fontSize: 11, color: 'var(--text3)', margin: '3px 0 7px' }}>
                The one cost taken from the deal rather than from our average. Leave it empty and the
                averaged rent is used and labelled as an average.
              </div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <input style={{ ...sel, width: 110 }} inputMode="decimal" placeholder="per month"
                       value={rentDraft} onChange={e => setRentDraft(e.target.value)} />
                <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={busy !== ''}
                        onClick={async () => {
                          setRentFor('')      // let the saved figure come back from the server
                          await save(`/prospects/${pid}${orgParam1()}`,
                            { actual_rent_monthly: rentDraft.trim() === '' ? null : rentDraft }, 'PUT')
                        }}>
                  Save rent
                </button>
              </div>
              {rentMeta.applied === false && rentMeta.reason &&
                <div style={{ fontSize: 11, color: '#92400e', marginTop: 6 }}>⚠︎ {rentMeta.reason}</div>}
              {rentMeta.applied && rentMeta.replaced_average !== null && rentMeta.replaced_average !== undefined &&
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>
                  Our own stores average {money(rentMeta.replaced_average)} —
                  {(rentMeta.entered || 0) > rentMeta.replaced_average ? ' this one is dearer by ' : ' this one is cheaper by '}
                  <b>{money(Math.abs((rentMeta.entered || 0) - rentMeta.replaced_average))}</b> a month.
                </div>}
            </div>
          </div>
          {/* WHAT IS ACTUALLY IN THE TABLE — a row per feed, uploaded or not. The missing-feed
              sentence below was already here and was not enough: it does not say how MANY rows a
              present feed landed, so a feed read with the wrong column layout (66,388 rows that
              answer nothing — owner report 2026-10-13) read as a feed that worked. */}
          <div style={{ marginTop: 10, borderTop: '1px solid var(--line)', paddingTop: 8 }}>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4 }}>What has been uploaded</div>
            {Object.keys(registry).map(k => {
              const n = (data?.rows_by_feed || {})[k]
              const has = (n || 0) > 0
              return (
                <div key={k} style={{ fontSize: 12, display: 'flex', gap: 8, padding: '2px 0' }}>
                  <span style={{ color: has ? 'var(--ok, #15803d)' : '#b45309' }}>{has ? '✓' : '○'}</span>
                  <span style={{ flex: 1 }}>{registry[k]?.label || k}</span>
                  <b style={{ color: has ? 'var(--text1)' : '#b45309' }}>
                    {has ? `${(n || 0).toLocaleString()} rows this month` : 'nothing uploaded'}
                  </b>
                </div>
              )
            })}
            {data?.period_basis?.basis &&
              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>
                Showing <b>{data?.period}</b> — {data.period_basis.basis}.
                {(data.period_basis.months_available || []).length > 1 &&
                  ` Months with uploaded rows: ${(data.period_basis.months_available || []).join(', ')}.`}
              </div>}
          </div>
          {(data?.feeds_missing || []).length > 0 &&
            <div style={{ fontSize: 12, color: '#92400e', marginTop: 10 }}>
              ⚠︎ {(data?.feeds_missing || []).map(k => registry[k]?.label || k).join(' and ')}
              {(data?.feeds_missing || []).length === 1 ? ' has' : ' have'} not been uploaded, so the
              P&amp;L lines {(data?.feeds_missing || []).length === 1 ? 'it answers' : 'they answer'} are
              shown as not uploaded rather than as zero.
            </div>}
        </div>}

      {/* ── THE HEADLINE ─────────────────────────────────────────────────────────────────────── */}
      {pid && !loading && statement && scenario &&
        <>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 13, fontWeight: 700 }}>{prospect?.name} · {data?.period}</div>
            <span style={{ flex: 1 }} />
            <label style={{ fontSize: 12, color: 'var(--text2)' }}
                   title="Both figures are always computed. This only picks which one is shown; the other is beside it in the sub-report below.">
              Flagged multi-line accounts{' '}
              <select style={sel} value={shownMode} onChange={e => setMode(e.target.value)}>
                <option value="keep">kept in the profit</option>
                <option value="exclude">taken out of the profit</option>
              </select>
            </label>
          </div>

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
            <Tile label="Carrier-side revenue" value={money(statement.totals?.revenue)}
                  sub="what the carrier and distributor paid, plus airtime margin" />
            <Tile label="Device cost" value={money(statement.totals?.cogs)}
                  sub={`${num(scenario.device_cost_meta?.priced)} handsets priced off its own order sheet`} />
            <Tile label="Gross profit" value={money(statement.totals?.gross_profit)} />
            <Tile label="Our average expenses" value={money(statement.totals?.expenses)}
                  sub={(data?.expenses?.months || []).join(', ') || 'no basis months'} />
            <Tile label="Profit or loss on measured revenue" value={money(statement.totals?.net_income)}
                  sub="carrier revenue less device cost less our average expenses — accessory and bill-payment revenue are in NO takeover feed, so a real month is better than this by whatever the counter sells" warn />
          </div>

          {/* The honesty statement, above the numbers rather than under them. */}
          <div className="card" style={{ padding: '12px 16px', marginBottom: 16, background: '#fef3c7', color: '#92400e', fontSize: 13 }}>
            <b>◻︎ Read this before the bottom line.</b> {covSummary.bottom_line_note}
            {' '}<b>{covSummary.measured}</b> of {covSummary.lines} chart lines were measured;
            {' '}<b>{covSummary.not_measured}</b> cannot be answered from these three reports
            {covSummary.uploaded_feed_missing
              ? <> and <b>{covSummary.uploaded_feed_missing}</b> wait on a file you have not uploaded</>
              : null}.
          </div>

          {/* ── THE SUB-REPORT: the flagged accounts, and what they are worth ───────────────── */}
          <div className="card" style={{ padding: '14px 16px', marginBottom: 18 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>
              👥 {mlSummary.flagged_accounts || 0} account{mlSummary.flagged_accounts === 1 ? '' : 's'} carried
              more than {mlSummary.threshold} line{mlSummary.threshold === 1 ? '' : 's'} in {data?.period}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>
              Out of <b>{num(mlSummary.accounts)}</b> accounts and <b>{num(mlSummary.lines)}</b> activated
              lines, and the most on any one account was <b>{mlSummary.max_lines_on_one_account}</b>.
              {mlSummary.lines_with_no_account
                ? <> <b>{num(mlSummary.lines_with_no_account)}</b> line
                  {mlSummary.lines_with_no_account === 1 ? ' carried' : 's carried'} no account number at
                  all and {mlSummary.lines_with_no_account === 1 ? 'is' : 'are'} not grouped under a guess.</>
                : null}
              <br />
              <i>This is a list to look at, not a verdict.</i> A family of four buying four phones on one
              account on one day reads exactly like the thing you are worried about, so each row carries
              the evidence to tell them apart — the day, the store account, how many were add-a-lines,
              and the phones.
            </div>
            {delta &&
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
                <Tile label="Revenue on flagged accounts" value={money(Math.abs(delta.revenue?.delta || 0))}
                      sub={`${money(delta.revenue?.keep)} with · ${money(delta.revenue?.exclude)} without`} />
                <Tile label="Their device cost" value={money(Math.abs(delta.cogs?.delta || 0))}
                      sub="leaves with them, so the two sides stay comparable" />
                <Tile label="Contribution at stake" value={money(Math.abs(delta.net_income?.delta || 0))}
                      sub={`${money(delta.net_income?.keep)} with · ${money(delta.net_income?.exclude)} without`} />
                <Tile label="Share of the bottom line" value={pct(delta.share_of_bottom_line_at_risk, 2)}
                      sub="how much of the contribution rests on accounts nobody has cleared"
                      warn={(delta.share_of_bottom_line_at_risk || 0) > 5} />
              </div>}
            {flagged.length > 0 && (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center',
                            marginBottom: 10 }}>
                <input style={{ ...sel, width: 120 }} inputMode="numeric" placeholder="Min lines"
                       value={fMinLines} onChange={e => setFMinLines(e.target.value)} />
                <input style={{ ...sel, width: 150 }} placeholder="Account number"
                       value={fAccount} onChange={e => setFAccount(e.target.value)} />
                <input style={{ ...sel, width: 190 }} placeholder="Phone model or IMEI"
                       value={fDevice} onChange={e => setFDevice(e.target.value)} />
                <label style={{ fontSize: 12.5, display: 'flex', gap: 5, alignItems: 'center' }}>
                  <input type="checkbox" checked={fSameDay}
                         onChange={e => setFSameDay(e.target.checked)} />
                  All lines on one day
                </label>
                <label style={{ fontSize: 12.5, display: 'flex', gap: 5, alignItems: 'center' }}>
                  <input type="checkbox" checked={fAddALine}
                         onChange={e => setFAddALine(e.target.checked)} />
                  Has add-a-lines
                </label>
                {(fMinLines || fAccount || fDevice || fSameDay || fAddALine) &&
                  <button className="btn btn-secondary"
                          onClick={() => { setFMinLines(''); setFAccount(''); setFDevice('')
                                           setFSameDay(false); setFAddALine(false) }}>
                    Clear
                  </button>}
                <span style={{ fontSize: 12, color: 'var(--text3)' }}>
                  showing {num(shownFlagged.length)} of {num(flagged.length)}
                  {shownFlagged.length !== flagged.length
                    && ' — the figures above are still the full month'}
                </span>
              </div>
            )}
            {flagged.length === 0
              ? <div style={{ fontSize: 13, color: 'var(--text2)' }}>
                  No account carried more than {mlSummary.threshold} lines this month.
                </div>
              : shownFlagged.length === 0
                ? <div style={{ fontSize: 13, color: 'var(--text2)' }}>
                    None of the {num(flagged.length)} flagged accounts match these filters. Clear them
                    to see the whole month.
                  </div>
                : <ReportShell title="Accounts over the line threshold"
                               filename={`takeover-multiline-${data?.period || ''}`}
                               columns={FLAG_COLS} rows={shownFlagged} compact stickyHeader pinFirst />}
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 9 }}>
              Which column names the account is a setting, not a guess — this month it answered on{' '}
              {Object.keys(mlSummary.account_columns_used || {})
                .map(c => `${c} (${num((mlSummary.account_columns_used || {})[c])} lines)`)
                .join(', ') || '—'}.
            </div>
          </div>

          {/* ── THE P&L ──────────────────────────────────────────────────────────────────────── */}
          <ReportShell title={`P&L — ${prospect?.name || 'prospect'}, ${data?.period || ''} (${shownMode === 'exclude' ? 'flagged accounts excluded' : 'flagged accounts kept'})`}
                       subtitle="Booked line by line through the same chart of accounts your own P&L uses"
                       filename={`takeover-pl-${data?.period || ''}-${shownMode}`}
                       columns={PL_COLS} rows={plRows} compact stickyHeader
                       defaultGroupBy="Section" collapsibleGroups totals />

          {/* ── WHAT IT COULD NOT ANSWER ─────────────────────────────────────────────────────── */}
          <div className="card" style={{ padding: '14px 16px', margin: '18px 0' }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>Every chart line, and how it was answered</div>
            {notMeasured.length > 0
              ? <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {notMeasured.map(c => {
                    const v = VERDICT[c.verdict] || VERDICT.not_measured
                    return (
                      <div key={c.key} style={{ background: v.bg, color: v.fg, borderRadius: 7, padding: '7px 10px', fontSize: 12, flex: '1 1 300px' }}>
                        <b>{v.icon} {c.label}</b> — {c.reason}
                      </div>
                    )
                  })}
                </div>
              : <div style={{ fontSize: 13, color: 'var(--text2)' }}>Every line of the chart was answered.</div>}
          </div>

          {/* ── THE EXPENSE BASIS ────────────────────────────────────────────────────────────── */}
          <div className="card" style={{ padding: '14px 16px', marginBottom: 18 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>Our own average expenses, line by line</div>
            <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>{expWindow.note}</div>
            {partialLines.length > 0 &&
              <div style={{ fontSize: 12, color: '#92400e', marginBottom: 8 }}>
                ⚠︎ {partialLines.length} line{partialLines.length === 1 ? ' is' : 's are'} missing from
                some of these months, so {partialLines.length === 1 ? 'it is' : 'they are'} averaged over
                fewer months than the rest:{' '}
                {partialLines.map(x => `${x.expense_name} (no ${(x.months_missing || []).join(', ')})`).join('; ')}.
              </div>}
            {expWindow.error &&
              <div style={{ fontSize: 12, color: '#991b1b', marginBottom: 8 }}>❌ {expWindow.error}</div>}
            <ReportShell title="" filename={`takeover-expenses-${data?.period || ''}`}
                         columns={EXP_COLS} rows={expRows} compact stickyHeader totals />
          </div>
        </>}

      {/* ── WHAT THE COUNTER HAS TO SELL (owner ask 2026-10-13) ──────────────────────────────── */}
      {pid && !loading && statement && breakEven &&
        <div className="card" style={{ padding: '14px 16px', marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 2 }}>
            What this store has to sell in accessories
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 10 }}>
            No point-of-sale feed comes with a takeover pack, so nothing here can measure what the
            counter took. This is what it would have to take.
          </div>
          {breakEven.already_profitable
            ? <div style={{ fontSize: 13 }}>
                On the carrier money alone this store already clears{' '}
                <b>{money(breakEven.net_income)}</b> a month against our average expenses, so it does
                not need accessory revenue to break even. No target is shown, because a target of
                zero would read as &ldquo;sell nothing&rdquo;.
              </div>
            : <>
                <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                  <div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>Gross revenue</div>
                    <div style={{ fontSize: 15 }}>{money(breakEven.revenue)}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>Less device cost</div>
                    <div style={{ fontSize: 15 }}>{money(breakEven.cost_of_goods)}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>Less expenses</div>
                    <div style={{ fontSize: 15 }}>{money(breakEven.expenses)}</div>
                  </div>
                  <div style={{ borderLeft: '1px solid var(--line)', paddingLeft: 24 }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>Shortfall to break even</div>
                    <div style={{ fontSize: 20, fontWeight: 700, color: '#b91c1c' }}>
                      {money(breakEven.needed)}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>Over activated lines</div>
                    <div style={{ fontSize: 15 }}>{num(breakEven.activations)}</div>
                  </div>
                  <div style={{ borderLeft: '1px solid var(--line)', paddingLeft: 24 }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                      Accessories per box needed
                    </div>
                    <div style={{ fontSize: 24, fontWeight: 700 }}>
                      {breakEven.per_activation === null ? 'not divisible'
                        : money(breakEven.per_activation)}
                    </div>
                  </div>
                </div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 10 }}>
                  {breakEven.basis}. {breakEven.per_activation_basis}.
                </div>
              </>}
        </div>}

      {/* ── THE P&L WITH A COLUMN PER STORE ──────────────────────────────────────────────────── */}
      {pid && !loading && statement && matrixStores.length > 0 &&
        <div className="card" style={{ padding: '14px 16px', marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 2 }}>
            The P&amp;L, a column per store &mdash; {matrixStores.length} store
            {matrixStores.length === 1 ? '' : 's'} named by the uploaded sheets
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 10 }}>
            Every line is a line of <b>your own chart of accounts</b>, and every cell is read from
            that store&rsquo;s own statement &mdash; nothing on this screen is re-added.
            {' '}<b>Each column is charged the full average monthly expense of one of our own
            stores</b>, so its bottom line answers whether that site could stand on its own.
            {' '}<b>Revenue, device cost and activated lines add across to All stores; the expense
            and net-income rows do not</b> &mdash; {matrixStores.length} columns carry
            {' '}{matrixStores.length} copies of a one-store cost, on purpose. Splitting that cost
            across the sites instead would divide a per-store figure by {matrixStores.length} and
            make every column look profitable.
          </div>
          <ReportShell title="" filename={`takeover-pl-by-store-${data?.period || ''}`}
                       columns={MATRIX_COLS} rows={matrixRows} compact stickyHeader />
        </div>}

      {/* ── A COLUMN PER STORE, from the uploads ─────────────────────────────────────────────── */}
      {pid && !loading && statement && storeRows.length > 0 &&
        <div className="card" style={{ padding: '14px 16px', marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 2 }}>
            Store by store &mdash; {storeRows.length} store{storeRows.length === 1 ? '' : 's'} named
            by the uploaded sheets
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 10 }}>
            Picked up from the files, not typed in: the sheets carry the dealer&rsquo;s account on
            every row and the handset orders carry its address. Revenue and device cost add up to the
            statement above; net income does not, because <b>each store is charged the full average
            monthly expense of one of our own stores</b> rather than a share of it &mdash; that is
            what makes the figure an answer to &ldquo;could this site stand on its own&rdquo;.
          </div>
          <ReportShell title="" filename={`takeover-stores-${data?.period || ''}`}
                       columns={STORE_COLS} rows={storeRows} compact stickyHeader totals />
        </div>}

      {/* ── THE HANDSETS ORDERED ─────────────────────────────────────────────────────────────── */}
      {pid && !loading && handsets && handsets.totals.rows > 0 &&
        <div className="card" style={{ padding: '14px 16px', marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 2 }}>
            Handsets ordered &mdash; {num(handsets.totals.orders)} orders,{' '}
            {num(handsets.totals.units)} units, {money(handsets.totals.cost)}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 10 }}>
            Across every month uploaded, because a handset ordered in one month is often activated in
            the next.
            {handsets.set_aside.rows > 0 &&
              <> {' '}<b>{num(handsets.set_aside.rows)}</b> of {num(handsets.totals.rows)} rows
              carry no unit price and are set aside rather than counted as zero
              ({Object.keys(handsets.set_aside.order_types).slice(0, 6).join(', ')}
              {Object.keys(handsets.set_aside.order_types).length > 6 ? ', …' : ''}).</>}
          </div>
          <div style={{ fontSize: 12, fontWeight: 600, margin: '10px 0 4px' }}>By model</div>
          <ReportShell title="" filename={`takeover-handsets-models-${data?.period || ''}`}
                       columns={HANDSET_MODEL_COLS} rows={handsets.models} compact stickyHeader totals />
          <div style={{ fontSize: 12, fontWeight: 600, margin: '14px 0 4px' }}>By store</div>
          <ReportShell title="" filename={`takeover-handsets-stores-${data?.period || ''}`}
                       columns={HANDSET_STORE_COLS} rows={handsets.by_store} compact stickyHeader totals />
          <div style={{ fontSize: 12, fontWeight: 600, margin: '14px 0 4px' }}>By month</div>
          <ReportShell title="" filename={`takeover-handsets-months-${data?.period || ''}`}
                       columns={HANDSET_MONTH_COLS} rows={handsets.by_month} compact stickyHeader totals />
        </div>}

      {/* ── THE ACCOUNTS AT THE BOTTOM, AND THE NUMBER UNDERNEATH THEM ───────────────────────── */}
      {pid && !loading && statement && fraud && fraud.accounts > 0 &&
        <div className="card" style={{ padding: '14px 16px', marginTop: 14,
                                       borderLeft: '3px solid #b45309' }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 2 }}>
            Accounts carrying more than {fraud.threshold} lines in this month
          </div>
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginTop: 10,
                        alignItems: 'flex-end' }}>
            <div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>Accounts</div>
              <div style={{ fontSize: 24, fontWeight: 700 }}>{num(fraud.accounts)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                of {num(fraud.accounts_in_file)} in the file
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>Lines on them</div>
              <div style={{ fontSize: 24, fontWeight: 700 }}>{num(fraud.lines)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                of {num(fraud.lines_in_file)} activated
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>Worst single account</div>
              <div style={{ fontSize: 24, fontWeight: 700 }}>{num(fraud.worst_account_lines)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>lines</div>
            </div>
            <div style={{ borderLeft: '1px solid var(--line)', paddingLeft: 24 }}>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>Bottom line with them</div>
              <div style={{ fontSize: 15 }}>{money(fraud.net_with_them ?? undefined)}</div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                Bottom line with their money taken out
              </div>
              <div style={{ fontSize: 20, fontWeight: 700 }}>
                {money(fraud.net_without_them ?? undefined)}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                {fraud.moved === null ? 'not measured'
                  : `${money(Math.abs(fraud.moved))} moves`}
                {fraud.share_of_bottom_line !== null
                  ? ` · ${pct(fraud.share_of_bottom_line)} of the bottom line`
                  : ''}
              </div>
            </div>
          </div>
          <div style={{ fontSize: 12, marginTop: 10 }}>
            {Object.keys(fraud.bands).map(k => (
              <span key={k} style={{ marginRight: 14 }}>
                <b>{num(fraud.bands[k])}</b> account{fraud.bands[k] === 1 ? '' : 's'} with {k} lines
              </span>
            ))}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 10 }}>{fraud.reading}</div>
        </div>}

      {pid && !loading && !statement && !data?.error &&
        <div className="card" style={{ padding: '16px', fontSize: 13, color: 'var(--text2)' }}>
          Nothing to evaluate yet — upload this store&rsquo;s{' '}
          {registry.ma_commission?.label || 'carrier commission'} file to begin.
        </div>}

      {loading && <div style={{ fontSize: 13, color: 'var(--text3)', padding: 20 }}>Working out the numbers…</div>}
    </div>
  )
}
