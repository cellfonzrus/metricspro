'use client'
import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { api, apiUpload, fmt, getActiveOrg } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import StandardFilterBar from '@/components/StandardFilterBar'
// `filterRows` is deliberately NOT imported: it matches ONE store value per row, and this page's
// two store-keyed shapes do not have one — a flagged account can name several processor accounts,
// and the sections are keyed by store KEY rather than by the label a selection carries. `storeFilter`
// below is the page's single place for that, and it resolves key, label and account off one store
// record so the sections cannot disagree.
import { emptyStandardFilter, isStandardFilterActive,
         type StandardFilterValue } from '@/lib/standard-filters'
import { SetupNotice, PlatformDetail } from '@/lib/setupNotice'

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
interface ProductModel { model: string; lines: number }
interface ProductClassRow {
  key: string
  label: string
  lines: number
  share: number | null
  models: ProductModel[]
}
interface ProductBreakup {
  classes: ProductClassRow[]
  labels: Record<string, string>
  totals: { lines: number; classes_with_sales: number }
  basis_counts: Record<string, number>
  unplaced: { description: string; lines: number }[]
  reading: string
}
interface MonthRow extends MultiLineSummary { period: string }
interface MultiLineByMonth {
  threshold: number
  months: number
  periods: string[]
  by_month: MonthRow[]
  totals: MultiLineSummary
  flagged: FlaggedAccount[]
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
// ── THE ACTIVATIONS, PER MONTH (owner ask 2026-10-14) ───────────────────────────────────────────
// Every figure here is the server's, counted by the platform's ONE activation classifier — this page
// renders it and computes nothing. `classes_absent` names the classes this feed carries no value
// for, which the card states in words rather than printing a zero beside them.
interface ActivationMonth {
  period: string
  lines: number
  classified: number
  new_activations: number
  by_class: Record<string, number>
  add_a_lines: number
  add_a_line_configured: boolean
  excluded: number
  unclassified: number
  unclassified_values: { value: string; lines: number }[]
  products: Record<string, number>
  product_labels: Record<string, string>
}
interface ActivationsByMonth {
  months: number
  periods: string[]
  by_month: ActivationMonth[]
  totals: ActivationMonth
  classes: string[]
  class_labels: Record<string, string>
  classes_absent: string[]
  new_activation_sentence: string
  reading: string
}
interface ProductBreakupMonth extends ProductBreakup { period: string }
interface ProductMonthRow extends ProductClassRow { period: string }
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
  multi_line_by_month?: MultiLineByMonth
  products?: ProductBreakup
  activations_by_month?: ActivationsByMonth
  commission_leg_store_months?: LegStoreMonths | null
  products_by_month?: { months: number; periods: string[]; by_month: ProductBreakupMonth[]; totals: ProductBreakup }
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
  rule_basis?: RuleBasis
  our_stores?: OurStores
  declared?: {
    by_store: Record<string, Declared>
    accessory_total: number | null
    accessory_cogs_pct: number
    stores_declaring: string[]
    stores_without_declaration: string[]
    keys_naming_no_store: string[]
    accessory_basis: string
    rent_basis: string
  }
  commission_legs?: CommissionLegs | null
  error?: string
}
// ── COMPARE IT WITH A STORE WE ALREADY OWN (owner ask 2026-10-14) ───────────────────────────────
// One of OUR stores, as its own stored monthly P&L reports it. `revenue_on_lines` is the comparable
// figure: what that store earned on the SAME P&L revenue lines this takeover pack booked. The whole
// `revenue` is larger on purpose — a store we own also sells accessories and takes bill payments,
// and a takeover pack carries no feed for either.
interface OurStore {
  key: string; label: string; address: string
  revenue: number; revenue_on_lines: number | null
  cogs: number; gross_profit: number; expenses: number; net_income: number
}
interface OurStores {
  stores: OurStore[]
  lines: string[]
  line_labels: string[]
  computed_at: string | null
  reading: string
  comparable: boolean
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
// ── WHAT THE SELLER DECLARED, PER STORE (owner ask 2026-10-14) ──────────────────────────────────
// Two figures no carrier feed can carry: this site's accessory sales and this site's rent. Both are
// DECLARATIONS, and the screen says so rather than letting them sit among the measured numbers
// looking the same. `rent_our_average` is what our own stores pay, which is what the column charges
// when nothing is declared — so the cell opens on it and an entered figure replaces it.
interface Declared {
  accessory_sales: number | null
  accessory_declared: boolean
  accessory_cogs_pct: number
  accessory_cost: number | null
  accessory_gross_profit: number | null
  accessory_basis: string
  rent_entered: number | null
  rent_declared: boolean
  rent_reason: string | null
  rent_lines: string[]
  rent_our_average: number | null
  rent_basis: string
}
// ── M1 AND M2–M12 (owner report 2026-10-14: "i cant see the m2-m12 commision line") ─────────────
// The month-of-life ladder on both bases: what the commission sheet says was EARNED in the
// activation month, and what the daily-transaction rows say was RECEIVED as cash. For a takeover
// these answer different questions — what the book is worth against what is landing now.
interface LegLadder {
  basis: string | null
  basis_label: string | null
  months: Record<string, number>
  rungs: string[]
  buckets: { m1: number; trailing: number; unsplit: number }
  bucket_labels: Record<string, string>
  total: number
  measured: boolean
}
interface CommissionLegs {
  earned: LegLadder
  received: LegLadder
  trailing_total: number
  trailing_share: number | null
  reading: string
}
// ── THE M2–M12 BLOCK, PER STORE, EVERY MONTH (owner ask 2026-10-16, §COMMCALC-15) ───────────────
// The shape is §COMMCALC-12's own pivot — the same rows, cells, deltas and worst-rung read-out the
// live per-store leg trend renders — so this screen reads a payload it does not have to interpret.
interface LegStoreCell {
  period: string; m1: number; m2_12: number; unsplit: number; total: number
  partial: boolean; ladder: Record<string, number>
}
interface LegStoreRow {
  store_key: string; store: string; store_code: string; market: string; unattributed: boolean
  months: Record<string, LegStoreCell>; ladder: Record<string, number>
  total: number; complete_total: number; first: string | null; last: string | null
  delta: number | null; delta_pct: number | null
  m1_delta: number | null; m2_12_delta: number | null; m2_12_delta_pct: number | null
  worst_rung: { key: string; label: string; delta: number; from: number; to: number } | null
}
interface LegStoreBasis {
  rows: LegStoreRow[]; totals: LegStoreRow; measured: boolean
  basis: string | null; basis_label: string | null
  ties: Record<string, { shown: number; company: number; ok: boolean; gap: number }>
  ladder_months: string[]; ladder_labels: Record<string, string>
  ladder_columns: Record<string, string>; ladder_unknown_key: string
}
interface LegStoreMonths {
  months: string[]; months_available: string[]; partial_months: string[]
  rows_without_month: number; stores: number; unattributed_key: string
  trailing_label: string; basis_default: string; reading: string
  bases: Record<string, LegStoreBasis>
}
interface StoreBlock {
  store: ProspectStore
  expenses_included: boolean
  scenarios: Record<string, Scenario>
  statements?: Record<string, Statement>
  delta?: Delta
  break_even?: Record<string, BreakEven>
  declared?: Declared
  commission_legs?: CommissionLegs | null
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
// ── WHAT EACH COLUMN MEANS, PRINTED ON THE REPORT (owner ask 2026-10-14) ────────────────────────
// Owner, reading three rows of this very table: *"what doe sthis report signify- expain on teh report
// what each filter signifies- do tehy mean there is only ne accoutn for 27 lines"* and *"explain each
// coloumn"*. The explanation rides the COLUMN (`tip`), so ReportShell prints it as the header tooltip
// AND in its "What each column means" legend — one text, and a renamed or deleted column takes its
// explanation with it instead of leaving a paragraph describing something that is no longer there.
const FLAG_COLS: ExportColumn[] = [
  { header: 'Month', get: (r: FlaggedAccount) => r.period,
    tip: 'The month stamped on the commission lines, not the month they were paid.' },
  { header: 'Account', get: (r: FlaggedAccount) => r.account_key,
    tip: 'ONE carrier billing account. Every line on this row was activated on this single account '
       + '— that is what makes the row worth a look.' },
  { header: 'Account column', get: (r: FlaggedAccount) => r.account_column,
    tip: 'Which column of your file identified the account. Different files name it differently, so '
       + 'the report says which one it read rather than assuming.' },
  { header: 'Lines', get: (r: FlaggedAccount) => r.lines, align: 'right',
    tip: 'How many activated lines sit on that one account in that month.' },
  { header: 'Add-a-lines', get: (r: FlaggedAccount) => r.add_a_lines, align: 'right',
    tip: 'Of those lines, how many were added to an account that already had one. Counted by the same '
       + 'rule your live stores are counted under.' },
  { header: 'All on one day', get: (r: FlaggedAccount) => (r.same_day ? 'yes' : 'no'),
    tip: '"yes" means every line was rung on a single day. A family buying four phones at once reads '
       + 'this way too, so it is a reason to look, never a verdict.' },
  { header: 'Days', get: (r: FlaggedAccount) => (r.days || []).join(', '),
    tip: 'The actual dates the lines were rung.' },
  { header: 'One store account', get: (r: FlaggedAccount) => (r.same_processor_account ? 'yes' : 'no'),
    tip: '"yes" means every line was rung through the same store processor account; "no" means the '
       + 'lines are spread across more than one.' },
  {
    header: 'Phones sold on it',
    get: (r: FlaggedAccount) => (r.devices || [])
      .map(d => `${d.sku || '(no model)'}${d.lines > 1 ? ` ×${d.lines}` : ''}`).join(' · '),
    tip: 'The handsets activated on that account, with a count where the same model repeats.',
  },
  {
    header: 'IMEIs',
    get: (r: FlaggedAccount) => (r.devices || []).flatMap(d => d.imeis || []).join(' '),
    tip: 'The serial number of each handset, so a human can trace the actual devices.',
  },
]

// ── THE NEW TABLES (owner ask 2026-10-13) ────────────────────────────────────────────────────────
// Every one goes through ReportShell, which is the platform's ONE sorted/exportable table (owner
// directive 2026-08-10, locked by harness_table_sort_lock) — never a hand-rolled <table>.
// ── WHAT WE HAVE EVALUATED SO FAR (owner report 2026-10-14) ────────────────────────────────────
// Owner: *"it does not let you pick fromt eh drop down what we have evelauterd so far"*.
//
// The dropdown did list them, but the page auto-opened the first one so the list was never the thing
// you were looking at, and a name in a <select> does not tell you whether that evaluation has
// anything in it yet. This is the same list as a TABLE: what has been uploaded, how many months, and
// what is still missing, with one button per row. Through `ReportShell` like every other table on the
// platform (owner directive 2026-08-10) rather than a hand-rolled one.
interface ProspectRow {
  id: string; name: string; status: string
  months: number; rows: number
  present: string[]; missing: string[]
  latest: string
}
function prospectRows(prospects: Prospect[], registry: Record<string, FeedInfo>): ProspectRow[] {
  const keys = Object.keys(registry)
  return prospects.map(p => {
    const byPeriod = p.feed_rows || {}
    const periods = Object.keys(byPeriod)
    let rows = 0
    const present = new Set<string>()
    for (const per of periods) {
      for (const [feed, n] of Object.entries(byPeriod[per] || {})) {
        rows += Number(n) || 0
        if (Number(n) > 0) present.add(feed)
      }
    }
    return {
      id: String(p.id),
      name: p.name || '(unnamed)',
      status: p.status || 'evaluating',
      months: periods.length,
      rows,
      present: keys.filter(k => present.has(k)),
      // Only the feeds this platform KNOWS about are called missing. A feed the registry does not
      // name is not a gap in your upload, it is a gap in the registry.
      missing: keys.filter(k => !present.has(k)),
      latest: (p.periods || [])[0] || '',
    }
  })
}
function prospectCols(registry: Record<string, FeedInfo>,
                      onOpen: (id: string) => void): ExportColumn[] {
  const label = (k: string) => registry[k]?.label || k
  return [
    { header: 'Store being evaluated', get: (r: ProspectRow) => r.name,
      tip: 'The name you gave this evaluation. It is not a store on your platform and never becomes '
         + 'one — walking away from a deal leaves nothing behind.' },
    { header: 'Months uploaded', get: (r: ProspectRow) => r.months, align: 'right',
      tip: 'How many different months its carrier reports cover. More months is a better read: one '
         + 'month can be a good month.' },
    { header: 'Latest month', get: (r: ProspectRow) => r.latest || '—',
      tip: 'The most recent month any uploaded file covers.' },
    { header: 'Rows uploaded', get: (r: ProspectRow) => r.rows, align: 'right',
      tip: 'Every line read out of its files, across every month and every report.' },
    { header: 'Reports in', get: (r: ProspectRow) => r.present.map(label).join(' · ') || 'nothing yet',
      tip: 'Which of its carrier reports have landed.' },
    { header: 'Still to upload', get: (r: ProspectRow) => r.missing.map(label).join(' · ') || 'none',
      tip: 'Which reports this evaluation has never seen. The commission report is the one that '
         + 'carries the money, so an evaluation without it has no P&L to show.' },
    { header: 'Status', get: (r: ProspectRow) => r.status,
      tip: 'Where you have got to with this one.' },
    {
      header: 'Open',
      get: () => '',
      render: (r: ProspectRow) => (
        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 10px' }}
                onClick={() => onOpen(r.id)}>Open</button>
      ),
      tip: 'Opens this evaluation. Nothing is opened until you say so.',
    },
  ]
}
// ── THE COMPARISON COLUMN (owner ask 2026-10-14) ───────────────────────────────────────────────
// Owner: *"Add one more column to it with to pick our existing store to compare the sales of the
// existing PNL with the new ones we might take over"*.
//
// The pick is PER ROW, because that is the question a takeover asks: this site looks like that site
// of ours. Picking is the only interactive thing in the table and it is display-only — `get` returns
// the store's name so an export carries the comparison that was on screen, and every figure beside
// it is read from that store's own stored P&L rather than computed here.
//
// WHY THE FIRST MONEY COLUMN IS "on the same lines" AND NOT our store's revenue: a takeover pack
// carries the target's carrier feeds and nothing else, so our store's whole revenue is not the
// comparable number — the server narrows ours to the very P&L lines the target's statement booked
// (see `prospect_eval.comparable_revenue_lines`). The whole revenue sits beside it so the counter
// business a pack cannot show stays visible instead of reading as the target earning less.
//
// EVERY ONE OF THESE DECLARES `agg: 'none'`. They are figures about OUR stores, and the same store
// can be picked on two rows — a total row would then count it twice and show a figure our business
// never earned. (`report-totals` sums a money column unless the column says otherwise.)
function comparisonCols(ours: OurStores,
                        pick: Record<string, string>,
                        onPick: (rowKey: string, storeKey: string) => void): ExportColumn[] {
  const byKey = new Map(ours.stores.map(o => [o.key, o]))
  const ourOf = (r: StoreRow) => byKey.get(pick[r.key] || '') || null
  const sameLines = ours.line_labels.length
    ? `the same P&L lines this pack booked (${ours.line_labels.join(', ')})`
    : 'the same P&L lines this pack booked'
  return [
    {
      header: 'Compare with our store',
      field: 'compare_with',
      get: (r: StoreRow) => ourOf(r)?.label || '',
      render: (r: StoreRow) => (
        <select value={pick[r.key] || ''} onChange={e => onPick(r.key, e.target.value)}
                aria-label={`Compare ${r.label} with one of our stores`}
                style={{ fontSize: 11, padding: '2px 4px', maxWidth: 190 }}>
          <option value="">Pick one of our stores…</option>
          {ours.stores.map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
        </select>
      ),
      tip: 'Pick one of the stores we already own and its own monthly P&L appears in the columns to '
         + 'the right, beside this one. Nothing is recomputed: those are the figures the Finance hub '
         + 'computed for that store'
         + (ours.computed_at ? `, last on ${ours.computed_at}.` : '.'),
    },
    { header: 'Our revenue on the same lines', get: (r: StoreRow) => ourOf(r)?.revenue_on_lines ?? null,
      money: true, agg: 'none',
      tip: `THE COMPARABLE FIGURE: what our store earned on ${sameLines}. This is the number to put `
         + 'beside the carrier revenue on the left, because those are the only lines a takeover pack '
         + 'carries any feed for.' },
    { header: 'Revenue gap on those lines',
      get: (r: StoreRow) => (ourOf(r)?.revenue_on_lines == null ? null
                             : Math.round((r.revenue - (ourOf(r)!.revenue_on_lines as number)) * 100) / 100),
      money: true, agg: 'none',
      tip: 'This store less our store, on those same lines. Positive means the store we are looking '
         + 'at took more carrier money than ours did in this month; negative means less. Both sides '
         + 'are shown in this row, so the subtraction can be checked by eye.' },
    { header: 'Our whole revenue', get: (r: StoreRow) => ourOf(r)?.revenue ?? null,
      money: true, agg: 'none',
      tip: 'Everything our store took that month, including accessories and bill payments. It is '
         + 'deliberately the larger number: a takeover pack has no feed for counter business, so '
         + 'read the difference as what this pack cannot show, never as the target earning less.' },
    { header: 'Our gross profit', get: (r: StoreRow) => ourOf(r)?.gross_profit ?? null,
      money: true, agg: 'none',
      tip: 'Our store\u2019s gross profit for the month, straight off its P&L.' },
    { header: 'Our net income', get: (r: StoreRow) => ourOf(r)?.net_income ?? null,
      money: true, agg: 'none',
      tip: 'Our store\u2019s bottom line for the month, after its OWN real expenses. The column on '
         + 'the left is the target after one of our stores\u2019 AVERAGE expenses, so this is the '
         + 'closest thing to a like-for-like answer to \u201ccould that site stand on its own\u201d.' },
  ]
}
// ── THE TWO CELLS THE SELLER FILLS IN (owner ask 2026-10-14, §COMMCALC-03.26) ───────────────────
// Verbatim: *"the cell sould be able to fill in manually and include the rent cell also for each
// store ... or the bettwe rway is to create a cell with ouer rent and if it is diff then it can vbe
// user overwritten"*. The owner named two designs for the rent and picked the second, so the rent
// cell OPENS ON OUR OWN AVERAGE and typing over it replaces that average for this store only.
//
// A CELL THAT REMEMBERS ITS OWN VALUE IS A CELL THAT CAN LIE. The input shows the draft only while
// somebody is typing in it; the moment it is saved the draft is dropped and the cell renders the
// figure the SERVER returned, which is the same figure the P&L above was built from. That is why
// `onSave` reloads rather than patching local state.
//
// AN EMPTY BOX IS NOT A ZERO. Clearing the accessory cell means the seller has declared nothing and
// the line disappears from the statement; typing 0 is a CLAIM that this counter sells no
// accessories, and is charged as one. Same for the rent: cleared goes back to our average, 0 says
// this site pays no rent. The two tips say so, because the difference is invisible otherwise.
function declaredCols(pct: number,
                      draft: Record<string, string>,
                      onDraft: (key: string, value: string) => void,
                      onSave: (key: string, field: 'declared_accessory_sales' | 'declared_rent_monthly',
                               value: string) => void,
                      busy: boolean): ExportColumn[] {
  const cell = (r: StoreRow, field: 'declared_accessory_sales' | 'declared_rent_monthly',
                saved: number | null, placeholder: string) => {
    const dk = `${r.key}|${field}`
    const shown = draft[dk] !== undefined ? draft[dk] : (saved === null ? '' : String(saved))
    return (
      <input value={shown} inputMode="decimal" disabled={busy}
             aria-label={`${placeholder} for ${r.label}`}
             placeholder={placeholder}
             onChange={e => onDraft(dk, e.target.value)}
             onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
             onBlur={() => {
               if (draft[dk] === undefined) return
               const was = saved === null ? '' : String(saved)
               if (draft[dk].trim() === was.trim()) { onDraft(dk, undefined as unknown as string); return }
               onSave(r.key, field, draft[dk])
             }}
             style={{ fontSize: 11, padding: '2px 4px', width: 92, textAlign: 'right' }} />
    )
  }
  return [
    {
      header: 'Accessory sales declared by seller',
      field: 'declared_accessory_sales',
      get: (r: StoreRow) => r.declared?.accessory_sales ?? null,
      money: true, agg: 'none',
      render: (r: StoreRow) => cell(r, 'declared_accessory_sales',
                                    r.declared?.accessory_sales ?? null, 'per month'),
      tip: 'Type what the seller says this counter takes in accessories each month, and press Enter. '
         + 'No carrier feed records an accessory sale, so this is the only measurement of it that '
         + `exists — it is a DECLARATION, not something we have verified. Its cost is taken at ${Math.round(pct * 100)}% `
         + '(the accessory cost rate this company itself has set), and the revenue, the cost and the '
         + 'gross profit it adds are all shown so the figure can be argued with. An empty box means '
         + 'the seller has declared nothing; typing 0 is a claim that this counter sells nothing.',
    },
    { header: 'Cost on the declared sales', get: (r: StoreRow) => r.declared?.accessory_cost ?? null,
      money: true, agg: 'none',
      tip: `The declared sales at this company's own accessory cost rate of ${Math.round(pct * 100)}%. It books to `
         + 'the cost-of-goods line, so the gross profit on the left already has it taken off.' },
    { header: 'Gross profit on accessories', get: (r: StoreRow) => r.declared?.accessory_gross_profit ?? null,
      money: true, agg: 'none',
      tip: 'The declared sales less that cost — what the declaration adds to this store\u2019s gross '
         + 'profit, and through it to the bottom line and to the accessories the store still has to sell.' },
    {
      header: 'Rent for this store',
      field: 'declared_rent_monthly',
      get: (r: StoreRow) => (r.declared?.rent_entered ?? r.declared?.rent_our_average ?? null),
      money: true, agg: 'none',
      render: (r: StoreRow) => cell(r, 'declared_rent_monthly',
                                    r.declared?.rent_entered ?? null,
                                    r.declared?.rent_our_average !== null
                                      && r.declared?.rent_our_average !== undefined
                                      ? String(r.declared.rent_our_average) : 'per month'),
      tip: 'This box opens on the average monthly rent OUR OWN stores pay, which is what this '
         + 'evaluation charges a site that has declared nothing. Type this address\u2019s real rent '
         + 'and press Enter and it replaces that average for this store only — the expenses, the '
         + 'bottom line and the accessory target on this row all move with it. Clear the box to go '
         + 'back to our average.',
    },
    { header: 'Rent basis', get: (r: StoreRow) => (r.declared?.rent_declared ? 'Declared' : 'Our average'),
      agg: 'none',
      tip: 'Whether the rent charged on this row is the figure somebody typed for this address, or '
         + 'the average of what our own stores pay. A row reading "Our average" has not been priced '
         + 'on its own lease.' },
    { header: 'Of which M2\u2013M12', get: (r: StoreRow) => r.trailing,
      money: true, agg: 'none',
      tip: 'How much of this store\u2019s commission is trailing-leg money — owed on lines that were '
         + 'already sold before the takeover. It keeps arriving for a while after the keys change '
         + 'hands and then stops, so it is not recurring revenue and a price built on the total '
         + 'overpays for it.' },
  ]
}
const STORE_COLS: ExportColumn[] = [
  { header: 'Store', get: (r: StoreRow) => r.label },
  { header: 'Account', get: (r: StoreRow) => r.account || '—' },
  { header: 'Carrier revenue', get: (r: StoreRow) => r.revenue, money: true },
  { header: 'Device cost', get: (r: StoreRow) => r.cogs, money: true },
  // THE FIGURE THE ACCESSORY TARGET IS TAKEN OFF (owner ask 2026-10-14: "should have gross profit
  // instead of carrier evenue to claculate accessories to be sold"). Revenue less device cost, read
  // from that store's OWN statement — the same `gross_profit` total the P&L matrix row shows, never
  // a subtraction done here. The accessory requirement has always come off net income, which IS
  // this figure less our average expenses; this column is what makes that visible.
  { header: 'Gross profit', get: (r: StoreRow) => r.grossProfit, money: true },
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
  // THE BLOCK KEY, carried so the comparison column can remember which of OUR stores this row was
  // compared with. A label is not an identity: two sites can read the same on screen.
  key: string
  label: string; account: string | null; revenue: number; cogs: number; grossProfit: number
  contribution: number
  activations: number; needed: number | null; perBox: number | null
  // THE TWO DECLARED CELLS, carried on the row so the editable columns read the SERVER's saved
  // figure rather than a copy the screen keeps — a cell that remembers its own value is a cell that
  // can disagree with the P&L built from it.
  declared: Declared | null
  trailing: number | null
}
// ── WHOSE RULES BOOKED THIS (owner report 2026-10-14) ───────────────────────────────────────────
// The evaluation books through the company's own P&L code, and now under THIS organisation's own
// declared switches rather than the house defaults. A switch nobody has declared says so, with the
// migration that would let them, because a figure resting on a fallback must not read as a decision.
interface RuleSwitch {
  key: string; column: string; value: unknown; house_value: unknown; basis: string
  migration: string | null; answers: string
}
interface RuleBasis {
  switches: RuleSwitch[]
  declared: number
  house_default: number
  column_absent: number
  unknown_columns: string[]
  columns_missing: string[]
  migrations_missing: string[]
  reading: string
}
// ── EVERY MONTH, AT THE BOTTOM (owner ask 2026-10-13: "the accounts which are 2 more summarized at
// the bottom for each month with the drill down"). One row per uploaded month, computed by the server
// from the SAME grouping the month report above uses — the month on screen is one row of this table,
// and the backend harness fails the build if the two ever disagree.
const MONTH_COLS: ExportColumn[] = [
  { header: 'Month', get: (r: MonthRow) => r.period },
  { header: 'Accounts over the threshold', get: (r: MonthRow) => r.flagged_accounts, align: 'right' },
  { header: 'Lines on them', get: (r: MonthRow) => r.flagged_lines, align: 'right' },
  // A MAXIMUM AND A SHARE DO NOT ADD. Without `agg: 'none'` the total row would sum twelve months of
  // "worst account" into a number no account ever carried, and sum twelve percentages past 100.
  { header: 'Worst single account', get: (r: MonthRow) => r.max_lines_on_one_account, align: 'right',
    agg: 'none' },
  { header: 'Accounts in the month', get: (r: MonthRow) => r.accounts, align: 'right' },
  { header: 'Lines in the month', get: (r: MonthRow) => r.lines, align: 'right' },
  {
    header: 'Share of accounts flagged',
    get: (r: MonthRow) => (r.accounts ? `${((r.flagged_accounts / r.accounts) * 100).toFixed(1)}%` : '—'),
    align: 'right', agg: 'none',
  },
  { header: 'Lines with no account', get: (r: MonthRow) => r.lines_with_no_account, align: 'right' },
]
// ── WHAT WAS SOLD (owner ask 2026-10-13: "the breakup of Phones tablets, Fios VHI, and if any other
// item was sold"). The classes, their counts and their models are ALL the server's — this table
// renders them. "Share of lines" does not add, so it declares `agg: 'none'`; the line count does.
// A MONTH COLUMN IS WHAT GIVES THIS TABLE ITS MONTH FILTER (owner ask 2026-10-14: "the filter for
// each month is missing from each report"). ReportShell turns any month-roled column into a By-month
// dropdown, so the control comes from the shared shell and is the same on every report.
const PRODUCT_COLS: ExportColumn[] = [
  { header: 'Month', get: (r: ProductMonthRow) => r.period, role: 'month',
    tip: 'The month stamped on the commission line. Pick one in the month dropdown to read it alone.' },
  { header: 'What was sold', get: (r: ProductMonthRow) => r.label,
    tip: 'The class the line was placed in. Every line lands in exactly one, so the rows add up to '
       + 'the file.' },
  { header: 'Lines', get: (r: ProductMonthRow) => r.lines, align: 'right',
    tip: 'How many activated lines sold that class in that month.' },
  {
    header: 'Share of lines',
    get: (r: ProductMonthRow) => (r.share === null ? '—' : `${r.share.toFixed(2)}%`),
    align: 'right', agg: 'none',
    tip: "That class's share of its own month. Shares of different months do not add, which is why "
       + 'the total row leaves this blank.',
  },
  { header: 'Models', get: (r: ProductMonthRow) => r.models.length, align: 'right',
    tip: 'How many distinct models were sold in that class that month.' },
  {
    header: 'Most sold',
    get: (r: ProductMonthRow) => (r.models || []).slice(0, 3)
      .map(m => `${m.model}${m.lines > 1 ? ` ×${m.lines}` : ''}`).join(' · ') || '—',
    tip: 'The three best-selling models of that class, with counts.',
  },
]
// ── THE ACTIVATIONS, PER MONTH (owner ask 2026-10-14) ───────────────────────────────────────────
// "the activations per month details is also missing which breaks up teh new acts upgrade tablet
// fios etc". The Month column is deliberate twice over: it is the breakup the owner asked for AND it
// is what gives this table its OWN month filter, because ReportShell turns any month-roled column
// into a By-month dropdown. That is the answer to *"the filter for each month is missing from each
// report"* — the filter comes from the shared shell, so it is the same control on every report and
// no page types one of its own.
//
// NOTHING HERE ADDS THAT SHOULD NOT. Add-a-lines are a subset of the activations beside them, so
// both columns sum honestly; the unclassified count is a line count and sums; nothing is a ratio.
function actCols(labels: Record<string, string>, products: string[],
                 productLabels: Record<string, string>): ExportColumn[] {
  return [
    { header: 'Month', get: (r: ActivationMonth) => r.period, role: 'month',
      tip: 'The month stamped on the commission line, not the month it was paid.' },
    { header: 'Lines', get: (r: ActivationMonth) => r.lines, align: 'right',
      tip: 'Every activated line the commission sheet carries for that month.' },
    { header: 'New activations', get: (r: ActivationMonth) => r.new_activations, align: 'right',
      tip: 'Counted by the same rule your live stores are counted under, so this figure and a store '
         + "report mean the same thing. An add-a-line is an activation and is counted here too." },
    ...['activation', 'byod', 'port', 'upgrade'].map(k => ({
      header: labels[k] || k,
      get: (r: ActivationMonth) => r.by_class?.[k] ?? 0,
      align: 'right' as const,
      tip: `How many of that month's lines were ${(labels[k] || k).toLowerCase()}.`,
    })),
    { header: 'Add-a-lines', get: (r: ActivationMonth) => r.add_a_lines, align: 'right',
      tip: 'A line added to an account that already had one. It is counted as the activation it is '
         + 'as well, so this column deliberately overlaps the ones before it.' },
    ...products.map(k => ({
      header: productLabels[k] || k,
      get: (r: ActivationMonth) => r.products?.[k] ?? 0,
      align: 'right' as const,
      tip: `Lines whose product was placed as ${(productLabels[k] || k).toLowerCase()}.`,
    })),
    { header: 'Not classified', get: (r: ActivationMonth) => r.unclassified, align: 'right',
      tip: 'Lines whose activation type is a word nobody has told the platform about yet. Zero means '
         + 'every line was placed.' },
  ]
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
  // THE STANDARD FILTERS, at the top of the page and applying across it (owner ask 2026-10-13: "all
  // standard filters on top of the page so they run all across"). The platform's shared bar owns them
  // — <StandardFilterBar> over a StandardFilterValue (RULE FIVE, §3d) — so this page gets the same
  // month and store controls, the same pick-don't-type behaviour and the same Clear as every other
  // report, and never a filter bar of its own. The MONTH is the one the server query runs on; the
  // STORES narrow every section whose rows name a store, and the page says where they do not reach.
  const [sf, setSf] = useState<StandardFilterValue>(emptyStandardFilter())
  const period = sf.period || ''
  // The module-specific filters, appended to the bar rather than replacing any of the core set.
  // Deliberately NOT in the URL or stored: a filtered view that came back tomorrow and hid rows
  // somebody never chose to hide is how a list stops being trusted.
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
  const [openMonth, setOpenMonth] = useState('')   // the month whose accounts are drilled into
  const [rentDraft, setRentDraft] = useState('')
  // The prospect whose stored rent is in `rentDraft`. Compared rather than ignored, so a reload of
  // the same prospect never overwrites a figure the user is halfway through typing.
  const [rentFor, setRentFor] = useState('')

  // NOTHING IS OPENED FOR YOU (owner report 2026-10-14: "the main screen does nto tell you what to
  // do it just sits tehre and it pops up teh dat aautoamatically also it does not let you pick fromt
  // eh drop down what we have evelauterd so far"). This used to select `prospects[0]`, which had two
  // costs the owner felt at once: the page loaded a whole evaluation nobody asked for, and because
  // `pid` was never empty the START PANEL below — the only thing on the page that says what to do —
  // could never render. One cause, both complaints. The page now opens on that panel, every time.
  const loadList = useCallback(() => {
    api(`/api/v1/commcalc/prospects${orgParam1()}`)
      .then((d: ProspectList) => setList(d))
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

  // MEMOISED, not just derived: `list?.prospects || []` is a NEW array identity on every render, so
  // the table's column and row memos below would rebuild every time and re-mount the table under the
  // pointer. Same for the registry.
  const prospects = useMemo<Prospect[]>(() => list?.prospects || [], [list?.prospects])
  const prospect: Prospect | null = data?.prospect
    || prospects.find(p => String(p.id) === pid)
    || null
  const registry = useMemo<Record<string, FeedInfo>>(
    () => data?.feed_registry || list?.feed_registry || {},
    [data?.feed_registry, list?.feed_registry])
  const shownMode = mode || data?.default_mode || 'keep'
  const scenario: Scenario | null = data?.scenarios?.[shownMode] || null
  const statement: Statement | null = data?.statements?.[shownMode] || null
  const breakEven: BreakEven | null = data?.break_even?.[shownMode] || null
  // THE MONTH-OF-LIFE LEGS for the whole prospect. Null when the server could not read the two
  // ladder one-homes, which is reported as not measured rather than as a zero.
  const legs: CommissionLegs | null = data?.commission_legs || null
  const handsets: HandsetOrders | null = data?.handset_orders || null
  const fraud: FraudExposure | null = data?.fraud || null
  const delta: Delta | null = data?.delta || null
  const flagged: FlaggedAccount[] = useMemo(
    () => data?.multi_line?.flagged || [], [data])

  // ── WHAT THE STORE FILTER ADMITS — decided ONCE, read by every section that names a store ──────
  // One home for the page's own filter, because a matrix showing three stores beside a store table
  // showing five is worse than no filter at all. The option id is the store KEY (unique by
  // construction); the label and the processor account come off the same store record, so a section
  // keyed by address and a section keyed by account number narrow to the same stores.
  const storeOptions = useMemo(() => {
    const blocks = data?.store_scenarios || {}
    return Object.keys(blocks)
      .map(k => ({ id: k, label: blocks[k].store?.label || k,
                   sublabel: blocks[k].store?.account || undefined }))
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [data?.store_scenarios])
  const storeFilter = useMemo(() => {
    const blocks = data?.store_scenarios || {}
    if (!sf.stores.length) return null
    const keys = new Set<string>(), labels = new Set<string>(), accounts = new Set<string>()
    for (const k of sf.stores) {
      keys.add(k)
      const st = blocks[k]?.store
      if (st?.label) labels.add(String(st.label))
      if (st?.account) accounts.add(String(st.account))
    }
    return { keys, labels, accounts }
  }, [data?.store_scenarios, sf.stores])
  const filtering = isStandardFilterActive(sf)
  // WHAT WAS SOLD, for the evaluated month. The server counts it over the SAME commission rows the
  // P&L books from, so a line here is a line in the statement above. Not narrowed by the store
  // filter: the commission row names a processor account, not the store address these classes would
  // have to be keyed by, and a per-store count nobody could tie back is worse than none.
  // THE ACTIVATION TABLE'S COLUMNS depend on which product classes the server actually returned, so
  // they are built from its own labels rather than a list typed here that a new class would not be in.
  const acts: ActivationsByMonth | null = data?.activations_by_month || null
  const actColumns = useMemo<ExportColumn[]>(() => {
    if (!acts) return []
    const keys = Object.keys(acts.totals.product_labels || {})
      .filter(k => (acts.totals.products?.[k] ?? 0) > 0)
    return actCols(acts.class_labels || {}, keys, acts.totals.product_labels || {})
  }, [acts])
  const products: ProductBreakup | null = data?.products || null
  // EVERY MONTH, flattened to one row per (month, class), so the table carries the month dimension
  // the owner asked for and the shared shell can filter on it. The page's own month is NOT applied:
  // this is the month table, and narrowing it by the page would leave the dropdown with one choice.
  const productRows = useMemo<ProductMonthRow[]>(
    () => (data?.products_by_month?.by_month || [])
      .flatMap(m => (m.classes || []).map(c => ({ ...c, period: m.period }))),
    [data?.products_by_month])

  // EVERY MONTH THE UPLOAD COVERS, not just the one on screen. The rows are the server's own, so the
  // month the report is showing IS one of these rows. The MONTH filter deliberately does not narrow
  // this table: comparing the months is the entire question it answers, and a one-row comparison
  // answers nothing. The STORE filter does not narrow it either — an account's lines can sit on more
  // than one processor account, so a per-store count of them would be a figure nobody could tie back.
  // Both exclusions are stated under the table rather than left for somebody to discover.
  const monthRows = useMemo<MonthRow[]>(
    () => data?.multi_line_by_month?.by_month || [], [data?.multi_line_by_month])
  const monthTotals: Partial<MultiLineSummary> = data?.multi_line_by_month?.totals || {}
  // THE DRILL-DOWN: the accounts of the opened month, from the SAME flagged list the table counted —
  // every account carries its own month, so this is a slice and never a second read. The page's own
  // account/device/line filters apply here, as they do to the month report's list.
  const drilled = useMemo<FlaggedAccount[]>(() => {
    if (!openMonth) return []
    const min = Number(fMinLines) || 0
    const needle = fDevice.trim().toLowerCase()
    const acct = fAccount.trim().toLowerCase()
    return (data?.multi_line_by_month?.flagged || []).filter(r =>
      r.period === openMonth
      && (!min || (r.lines || 0) >= min)
      && (!fSameDay || !!r.same_day)
      && (!fAddALine || (r.add_a_lines || 0) > 0)
      && (!acct || String(r.account_key || '').toLowerCase().includes(acct))
      && (!needle || (r.devices || []).some(d =>
        String(d.sku || '').toLowerCase().includes(needle)
        || (d.imeis || []).some(i => String(i).toLowerCase().includes(needle)))))
  }, [data?.multi_line_by_month, openMonth, fMinLines, fSameDay, fAddALine, fAccount, fDevice])

  // FILTERS ON THE SUB-REPORT (owner ask 2026-10-12: "need the filters also"). They narrow the LIST
  // only — never the P&L, and never the keep-vs-exclude figures above it. Those two numbers are what
  // the flagged accounts are worth in total, and a number that silently followed a filter would
  // answer a question nobody asked. The row count below the controls says what is being shown.
  const shownFlagged = useMemo(() => {
    const min = Number(fMinLines) || 0
    const needle = fDevice.trim().toLowerCase()
    const acct = fAccount.trim().toLowerCase()
    return flagged.filter(r =>
      (!storeFilter || (r.processor_accounts || []).some(a => storeFilter.accounts.has(String(a))))
      && (!min || (r.lines || 0) >= min)
      && (!fSameDay || !!r.same_day)
      && (!fAddALine || (r.add_a_lines || 0) > 0)
      && (!acct || String(r.account_key || '').toLowerCase().includes(acct))
      && (!needle || (r.devices || []).some(d =>
        String(d.sku || '').toLowerCase().includes(needle)
        || (d.imeis || []).some(i => String(i).toLowerCase().includes(needle)))))
  }, [flagged, fMinLines, fSameDay, fAddALine, fAccount, fDevice, storeFilter])
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

  // The handset orders are keyed by the store's ADDRESS, which is the same store record the filter
  // read its labels from — so picking a store narrows this table to the same stores as the matrix.
  const handsetStores = useMemo<HandsetStore[]>(
    () => (data?.handset_orders?.by_store || [])
      .filter(r => !storeFilter || storeFilter.labels.has(String(r.store))),
    [data?.handset_orders, storeFilter])


  // A ROW PER STORE, built from the per-store statements the server assembled with the SAME builder
  // as the whole-prospect statement. Nothing is re-summed here: a figure the screen computes is a
  // figure that can disagree with the report above it.
  const storeRows = useMemo<StoreRow[]>(() => {
    const blocks = data?.store_scenarios || {}
    return Object.keys(blocks).filter(k => !storeFilter || storeFilter.keys.has(k)).map(k => {
      const b = blocks[k]
      const t = b.statements?.[shownMode]?.totals
      const be = b.break_even?.[shownMode]
      return {
        key: k,
        label: b.store?.label || k,
        account: b.store?.account || null,
        revenue: t?.revenue ?? 0,
        cogs: t?.cogs ?? 0,
        grossProfit: t?.gross_profit ?? 0,
        contribution: t?.net_income ?? 0,
        activations: b.scenarios?.[shownMode]?.activations ?? 0,
        needed: be?.needed ?? null,
        perBox: be?.per_activation ?? null,
        declared: b.declared || null,
        trailing: b.commission_legs?.trailing_total ?? null,
      }
    }).sort((a, b) => (b.contribution || 0) - (a.contribution || 0))
  }, [data?.store_scenarios, shownMode, storeFilter])

  // ── WHAT WE HAVE EVALUATED SO FAR, as the page's own first screen ─────────────────────────────
  // Memoised because a column list rebuilt every render re-mounts the table. `openProspect` is the
  // SAME state change the dropdown makes — one way to open an evaluation, two places to press it.
  const openProspect = useCallback((id: string) => {
    setPid(id); setSf(emptyStandardFilter()); setMode(''); setRentFor('')
  }, [])
  const listRows = useMemo<ProspectRow[]>(
    () => prospectRows(prospects, registry), [prospects, registry])
  const listColumns = useMemo<ExportColumn[]>(
    () => prospectCols(registry, openProspect), [registry, openProspect])

  // ── WHICH OF OUR STORES EACH ROW IS COMPARED WITH (owner ask 2026-10-14) ──────────────────────
  // Keyed by the prospect store's block key, so a pick survives sorting, filtering and a month
  // change. Deliberately NOT defaulted to our first store: a comparison nobody asked for would read
  // as a judgement the report never made.
  const ours: OurStores | null = data?.our_stores || null
  const [peerPick, setPeerPick] = useState<Record<string, string>>({})
  const pickPeer = useCallback((rowKey: string, storeKey: string) => {
    setPeerPick(prev => ({ ...prev, [rowKey]: storeKey }))
  }, [])
  // The column list is rebuilt when a pick changes because the cells close over the picks. It stays
  // a useMemo all the same: a NEW array on every render re-mounts the table and the open dropdown
  // would close under the pointer.

  // The columns ARE the stores, in the order the store index names them, with the whole prospect
  // last. Built with useMemo because a column list that is rebuilt every render re-mounts the table.
  const matrixStores = useMemo(() => {
    const blocks = data?.store_scenarios || {}
    return Object.keys(blocks)
      .filter(k => !storeFilter || storeFilter.keys.has(k))
      .map(k => ({ key: k, label: blocks[k].store?.label || k }))
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [data?.store_scenarios, storeFilter])

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

  // ── THE SEPARATE M2–M12 BLOCK, PER STORE (owner ask 2026-10-16, §COMMCALC-15) ───────────────
  // "give me a seaprate block with m2-m12 for each store with filters for all month and all
  // stores". TWO FILTERS, and they are the two the owner named:
  //   · STORES — the page's shared `storeFilter`, the same one record the P&L matrix and the store
  //     table narrow by, so picking a store narrows this block to the same stores and not a
  //     different set keyed another way.
  //   · MONTHS — this block's own selector, because its months are COLUMNS rather than a column.
  //     'All months' is the default and means every month the uploads name; the list of months
  //     comes from the payload (`months_available`), so a month nobody uploaded can never be
  //     offered. Narrowing hides a COLUMN and nothing else: no figure is recomputed on the screen.
  const legStores: LegStoreMonths | null = data?.commission_leg_store_months || null
  const [legBasis, setLegBasis] = useState('')
  const [legMonth, setLegMonth] = useState('')
  const legShownBasis = (legBasis && legStores?.bases?.[legBasis]) ? legBasis
    : (legStores?.basis_default || 'received')
  const legB: LegStoreBasis | null = legStores?.bases?.[legShownBasis] || null
  const legMonths = useMemo<string[]>(
    () => (legStores?.months || []).filter(m => !legMonth || m === legMonth),
    [legStores?.months, legMonth])
  // The rows are the server's, narrowed by the shared store filter. The unattributed row is kept
  // whatever the filter says: money that names no store belongs to no store, so hiding it behind a
  // store pick would make the rows stop adding to the figure above them without saying why.
  const legRows = useMemo<LegStoreRow[]>(
    () => (legB?.rows || []).filter(r => r.unattributed || !storeFilter
                                         || storeFilter.keys.has(r.store_key)
                                         || storeFilter.labels.has(r.store)),
    [legB?.rows, storeFilter])
  const LEG_COLS = useMemo<ExportColumn[]>(() => ([
    { header: 'Store', get: (r: LegStoreRow) => r.store, field: 'store', role: 'store' },
    ...legMonths.map(m => ({
      header: m + (legStores?.partial_months.includes(m) ? ' (part month)' : ''),
      field: 'm:' + m,
      get: (r: LegStoreRow) => r.months[m]?.m2_12 ?? null,
      money: true,
      tip: `M2–M12 commission in ${m}`
         + (legStores?.partial_months.includes(m)
            ? ' — this month has not finished, so it is left out of every change figure below'
            : ''),
    })),
    { header: 'M2–M12 total', get: (r: LegStoreRow) => r.total, money: true, field: 'trailing' },
    { header: '1st month, latest', field: 'm1_last',
      get: (r: LegStoreRow) => (r.last ? r.months[r.last]?.m1 ?? null : null), money: true,
      tip: 'The first-month commission in the latest FINISHED month — shown beside the trailing '
         + 'legs because a store whose 1st month is falling is shrinking now, while one whose 1st '
         + 'month holds lost its cohort months ago' },
    { header: 'M2–M12 change', get: (r: LegStoreRow) => r.m2_12_delta, money: true,
      field: 'trailing_delta', agg: 'none',
      tip: 'First to last FINISHED month the store appears in. Blank when it appears in only one' },
    { header: 'Change %', get: (r: LegStoreRow) => r.m2_12_delta_pct, field: 'trailing_delta_pct',
      agg: 'none', tip: 'Blank, never 0%, when the baseline month was $0 — "up from nothing" has '
                      + 'no percentage' },
    { header: 'Rung that fell hardest', field: 'worst_rung', agg: 'none',
      get: (r: LegStoreRow) => (r.worst_rung
        ? `${r.worst_rung.label} ${money(r.worst_rung.delta)}` : '') },
  ]), [legMonths, legStores?.partial_months])

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

  // DEFINED AFTER `save` DELIBERATELY: the dependency array of a useCallback is evaluated the
  // moment the component renders, so a hook up above that listed `save` would read it before its
  // own `const` had initialised and throw on the first paint.
  // ── THE TWO DECLARED CELLS (owner ask 2026-10-14) ────────────────────────────────────────────
  // The draft holds ONLY what is being typed right now. On save it is dropped and the cell goes back
  // to rendering the server's figure, which is the one the P&L above was built from — so a cell can
  // never show a number the statement does not carry.
  const [cellDraft, setCellDraft] = useState<Record<string, string>>({})
  const onCellDraft = useCallback((key: string, value: string) => {
    setCellDraft(prev => {
      const next = { ...prev }
      if (value === undefined) delete next[key]
      else next[key] = value
      return next
    })
  }, [])
  const saveCell = useCallback(async (storeKey: string,
                                      field: 'declared_accessory_sales' | 'declared_rent_monthly',
                                      value: string) => {
    if (!pid) return
    // A BLANK CLEARS, it does not zero. null means "the seller has declared nothing": the column
    // goes back to our average rent and books no accessory revenue, which is a different claim from
    // a declared 0.00 and is stored differently.
    const body: Record<string, unknown> = { store_key: storeKey }
    body[field] = value.trim() === '' ? null : value.trim()
    setCellDraft(prev => {
      const next = { ...prev }
      delete next[`${storeKey}|${field}`]
      return next
    })
    await save(`/prospects/${pid}/store-inputs${orgParam1()}`, body, 'PUT')
  }, [pid, save])
  const declaredPct = data?.declared?.accessory_cogs_pct ?? 0
  const storeColumns = useMemo<ExportColumn[]>(
    () => [...STORE_COLS,
           ...declaredCols(declaredPct, cellDraft, onCellDraft, saveCell, busy !== ''),
           ...(ours && ours.comparable ? comparisonCols(ours, peerPick, pickPeer) : [])],
    [declaredPct, cellDraft, onCellDraft, saveCell, busy, ours, peerPick, pickPeer])

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
                  onChange={e => openProspect(e.target.value)}>
            <option value="">— pick a store being evaluated —</option>
            {prospects.map(p => (
              <option key={p.id} value={p.id}>{p.name}{p.status && p.status !== 'evaluating' ? ` · ${p.status}` : ''}</option>
            ))}
          </select>
        </label>
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

      {/* ── THE STANDARD FILTERS, AT THE TOP, RUNNING ACROSS THE PAGE ────────────────────────────
          Owner ask 2026-10-13: *"all standard filters on top of the page so they run all across"*.
          The platform's shared <StandardFilterBar> (RULE FIVE, §3d) — never a bar of this page's own.
          · MONTH drives the server query, so every figure below it is that month's.
          · STORES narrow every section that names a store: the P&L matrix columns, the store table,
            the handset orders by store and the flagged-account list.
          · MARKET and PERSON are omitted, and the deviation is stated rather than silent: a store we
            do not own sits in no market of ours and has nobody on our roster, so those two controls
            could only ever filter everything away.
          The module's own filters are APPENDED on the right; the core set is never substituted. */}
      {pid && !data?.error &&
        <div style={{ position: 'sticky', top: 0, zIndex: 5, background: 'var(--bg, var(--surface))',
                      paddingTop: 6, marginBottom: 4 }}>
          <StandardFilterBar
            value={sf} onChange={setSf}
            show={{ period: true, stores: true, markets: false, reps: false }}
            periodMode="month" periods={prospect?.periods || []}
            storeOptions={storeOptions} storeLabel="Stores…"
            right={
              <>
                <label style={{ fontSize: 12, color: 'var(--text2)' }}
                       title="Both figures are always computed. This only picks which one is shown.">
                  Flagged accounts{' '}
                  <select style={sel} value={shownMode} onChange={e => setMode(e.target.value)}>
                    <option value="keep">kept in the profit</option>
                    <option value="exclude">taken out of the profit</option>
                  </select>
                </label>
                <input style={{ ...sel, width: 110 }} inputMode="numeric" placeholder="Min lines"
                       value={fMinLines} onChange={e => setFMinLines(e.target.value)} />
                <input style={{ ...sel, width: 140 }} placeholder="Account number"
                       value={fAccount} onChange={e => setFAccount(e.target.value)} />
                <input style={{ ...sel, width: 170 }} placeholder="Phone model or IMEI"
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
                  <button className="btn btn-secondary" style={{ fontSize: 12 }}
                          onClick={() => { setFMinLines(''); setFAccount(''); setFDevice('')
                                           setFSameDay(false); setFAddALine(false) }}>
                    Clear these
                  </button>}
              </>
            } />
          {filtering &&
            <div style={{ fontSize: 11.5, color: '#92400e', marginBottom: 10 }}>
              ◻︎ The store filter narrows the P&amp;L columns, the store table, the handset orders and
              the flagged accounts. The <b>whole-prospect</b> P&amp;L above them, its tiles and the
              keep-versus-exclude figures are the WHOLE month either way &mdash; they are what the
              prospect is worth in total, and a headline that silently followed a filter would answer
              a question nobody asked.
            </div>}
        </div>}

      {msg &&
        <div className="card" style={{ padding: '10px 14px', marginBottom: 12, fontSize: 13, background: msg.ok ? 'var(--surface2)' : '#fee2e2', color: msg.ok ? 'var(--text2)' : '#991b1b' }}>
          {msg.ok ? '✓ ' : '❌ '}{msg.text}
        </div>}

      {/* ── START HERE — the page's own first screen, never help text ─────────────────────────
          Owner report 2026-10-14: *"the main screen does nto tell you what to do it just sits
          tehre"*. Both intro paragraphs above carry `pg-note`, which globals.css HIDES unless an
          approved role has help switched on — so with help off this page showed a title and a
          dropdown and nothing else. Instructions a reader needs in order to USE the page are not
          help: they are the page. This panel carries NO `pg-note` and is what the page opens on. */}
      {!pid &&
        <>
          <div className="card" style={{ padding: '14px 16px', marginBottom: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
              Start here &mdash; three steps
            </div>
            <ol style={{ fontSize: 13, color: 'var(--text2)', margin: 0, paddingLeft: 20,
                         lineHeight: 1.7 }}>
              <li><b>Pick a store below</b>, or type a name in the box above and press{' '}
                <b>+ Open an evaluation</b> to start a new one.</li>
              <li><b>Upload its carrier reports</b> &mdash;{' '}
                {Object.keys(registry).length > 0
                  ? Object.values(registry).map(f => f.label).join(', ')
                  : 'its commission, daily-transaction and handset-order files'}. The commission
                report is the one that carries the money, so start with that one.</li>
              <li><b>Type its actual monthly rent</b>, which replaces ours in the expense average.
                Then read the P&amp;L, and compare it with one of your own stores.</li>
            </ol>
            <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 10 }}>
              Nothing here creates a store, a mapping or a statement, and nothing is opened until you
              open it. The rows you upload are read by this page and by nothing else.
            </div>
          </div>

          {prospects.length > 0
            ? <div className="card" style={{ padding: '14px 16px', marginBottom: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 2 }}>
                  What you have evaluated so far &mdash; {prospects.length}{' '}
                  store{prospects.length === 1 ? '' : 's'}
                </div>
                <div style={{ fontSize: 11.5, color: 'var(--text3)', marginBottom: 10 }}>
                  Newest first. Press <b>Open</b> on any row, or use the Prospect dropdown above.
                </div>
                <ReportShell title="" filename="takeover-evaluations"
                             columns={listColumns} rows={listRows} compact stickyHeader
                             notice={<><b>An evaluation with no commission report has no P&amp;L
                               yet.</b> The &ldquo;Still to upload&rdquo; column says what each one is
                               waiting for, and the row counts are what actually landed in the
                               platform &mdash; not what the file claimed.</>} />
              </div>
            : <div className="card" style={{ padding: '16px', fontSize: 13, color: 'var(--text2)' }}>
                You have not evaluated any store yet. Type a name in the box above and press{' '}
                <b>+ Open an evaluation</b> to start the first one.
              </div>}
        </>}

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
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>
              flagged accounts {shownMode === 'exclude' ? 'taken out of' : 'kept in'} the profit
            </span>
          </div>

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
            <Tile label="Carrier-side revenue" value={money(statement.totals?.revenue)}
                  sub="activation commission and month spiffs, airtime margin and residual, market-development money, fees and financing — booked under this organisation's own declared rules" />
            <Tile label="Device cost" value={money(statement.totals?.cogs)}
                  sub={`${num(scenario.device_cost_meta?.priced)} handsets priced off its own order sheet`} />
            <Tile label="Gross profit" value={money(statement.totals?.gross_profit)}
                  sub={(statement.totals?.cogs ?? 0) < 0
                    ? 'higher than revenue this month: the device purchase rebates these feeds carry exceed the handset cost, so cost of goods is negative'
                    : 'carrier-side revenue less device cost — the figure the accessory target is taken off, once our average expenses come out of it'} />
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

          {/* ── WHOSE RULES BOOKED THIS (owner report 2026-10-14) ──────────────────────────────
              The evaluation used to book through the company's own code while overriding the
              company's own declarations with house defaults, which on this prospect hid every
              month spiff and every MDF dollar and kept all airtime money out of the store columns.
              The switches are on screen now, each saying whether it is a decision or a fallback. */}
          {data?.rule_basis &&
            <div className="card" style={{ padding: '12px 16px', marginBottom: 18 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>
                ⚙ Booked under your own rules — {data.rule_basis.declared} of
                {' '}{data.rule_basis.switches.length} switches differ from the house answer
                {data.rule_basis.column_absent
                  ? <>, {data.rule_basis.column_absent} cannot be set on this system yet</>
                  : null}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
                {data.rule_basis.reading}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'auto auto 1fr', gap: '4px 12px', fontSize: 12 }}>
                {data.rule_basis.switches.map(sw =>
                  <React.Fragment key={sw.key}>
                    <span style={{ fontWeight: 600 }}>{sw.key.replace(/_/g, ' ')}</span>
                    <span style={{ fontFamily: 'ui-monospace, monospace' }}>
                      {Array.isArray(sw.value)
                        ? (sw.value.length ? sw.value.join(', ') : '(none declared)')
                        : String(sw.value)}
                    </span>
                    <span style={{ color: sw.basis === 'column_absent' ? '#92400e' : 'var(--text3)' }}>
                      {sw.basis === 'declared'
                        ? <><b>yours</b> — {sw.answers}</>
                        : sw.basis === 'house_default'
                          ? <>house answer, which you may have chosen or never set — {sw.answers}</>
                          : <>not set up on this system yet — {sw.answers}
                              {sw.migration ? <PlatformDetail detail={sw.migration} style={{ marginLeft: 6 }} /> : null}</>}
                    </span>
                  </React.Fragment>)}
              </div>
            </div>}

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
            {flagged.length > 0 && shownFlagged.length !== flagged.length && (
              <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
                Showing {num(shownFlagged.length)} of {num(flagged.length)} &mdash; the filters at the
                top of the page are narrowing this list; the figures above it are still the full month.
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
                               columns={FLAG_COLS} rows={shownFlagged} compact stickyHeader pinFirst
                               notice={<><b>What a row means.</b> One row is ONE carrier billing
                                 account that carried more than {mlSummary.threshold} activated
                                 line{mlSummary.threshold === 1 ? '' : 's'} in a single month &mdash; so
                                 the <b>Lines</b> figure is how many lines sit on that one account,
                                 not how many accounts there are. Nothing here concludes that anything
                                 is wrong: a family of four buying four phones on one account on one
                                 day reads exactly the same way. The columns are the evidence a person
                                 needs to tell the two apart, and the filters above only choose which
                                 of these accounts you are looking at &mdash; they never change a
                                 figure in the P&amp;L.</>} />}
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
                       defaultGroupBy="Section" collapsibleGroups totals
                       noticeTone={covSummary.not_measured ? 'warn' : 'info'}
                       notice={<><b>Read this before the bottom line.</b>{' '}
                         {covSummary.bottom_line_note} {covSummary.measured} of {covSummary.lines}{' '}
                         chart lines were measured and {covSummary.not_measured} cannot be answered
                         from the uploaded reports, so they read as not measured rather than as
                         zero.</>} />

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
                         columns={EXP_COLS} rows={expRows} compact stickyHeader totals
                         noticeTone={partialLines.length ? 'warn' : 'info'}
                         notice={<><b>These are OUR costs, not the prospect&rsquo;s.</b> Each line is
                           what one of our own stores pays for it, averaged over the complete months
                           we have, because a takeover pack carries no expenses of its own. A line
                           missing from some of those months is averaged over the months it is present
                           in and said so above, never spread thinner across months that never
                           recorded it.</>} />
          </div>
        </>}

      {/* ── M1 AND M2–M12 (owner report 2026-10-14: "i cant see the m2-m12 commision line") ─── */}
      {/* The money was never missing — it sat inside one commission line with the legs only in its
          drill-down. It matters more on a takeover than anywhere else on the platform, because
          M2–M12 is owed on lines that were SOLD BEFORE the keys change hands: it keeps arriving for
          a while and then stops, so a price built on the total overpays for it. */}
      {pid && !loading && legs &&
        <div className="card" style={{ padding: '14px 16px', marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 2 }}>
            First month and M2&ndash;M12
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 10 }}>
            {legs.reading}
          </div>
          {(['received', 'earned'] as const).map(basis => {
            const lad = legs[basis]
            if (!lad) return null
            return (
              <div key={basis} style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 12, fontWeight: 600 }}>
                  {basis === 'received' ? 'Cash received this month' : 'Earned in the activation month'}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6 }}>
                  {lad.basis_label || basis}
                </div>
                {!lad.measured
                  ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                      Nothing is measured on this basis for this month &mdash; the feed it reads
                      carries no month-of-life for any row. That is an absence, not a zero.
                    </div>
                  : <>
                      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                        {(['m1', 'trailing', 'unsplit'] as const).map(b => (
                          (b === 'unsplit' && !lad.buckets.unsplit) ? null :
                          <div key={b}>
                            <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                              {lad.bucket_labels[b] || b}
                            </div>
                            <div style={{ fontSize: 15 }}>{money(lad.buckets[b])}</div>
                          </div>
                        ))}
                        <div style={{ borderLeft: '1px solid var(--line)', paddingLeft: 20 }}>
                          <div style={{ fontSize: 11, color: 'var(--text3)' }}>Total</div>
                          <div style={{ fontSize: 15, fontWeight: 700 }}>{money(lad.total)}</div>
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 8 }}>
                        {lad.rungs.map(k => (
                          <div key={k} style={{ fontSize: 11 }}>
                            <span style={{ color: 'var(--text3)' }}>
                              {k === 'unknown' ? 'Unlabelled' : `M${k}`}{' '}
                            </span>
                            <b>{money(lad.months[k])}</b>
                          </div>
                        ))}
                      </div>
                    </>}
              </div>
            )
          })}
          {legs.trailing_share !== null &&
            <div style={{ fontSize: 12, marginTop: 4 }}>
              <b>{Math.round((legs.trailing_share || 0) * 1000) / 10}%</b> of this month&rsquo;s
              commission is trailing-leg money, {money(legs.trailing_total)} of it. Read that part as
              a wasting asset with a tail, not as revenue the site will keep earning.
            </div>}
          {/* WHERE THESE TWO NUMBERS ARE ON THE STATEMENT (owner ask 2026-10-16). The split is a
              presentation of the SAME dollars: both halves are revenue lines of the same section,
              so revenue, gross profit and the bottom line are identical to the cent either way. */}
          <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 8, lineHeight: 1.6 }}>
            <b>Both halves are lines on the P&amp;L above.</b> The M2&ndash;M12 money now sits on its
            own commission line there and in every store column, and the commission line above it is
            everything else &mdash; this month&rsquo;s own activations, and any commission dollar
            whose month-of-life the feed does not state. Splitting the line moves nothing: both are
            revenue lines of the same section, so revenue, gross profit and the bottom line are
            identical to the cent whether it is shown as one line or two.
          </div>
        </div>}

      {/* ── M2–M12 PER STORE, EVERY MONTH SIDE BY SIDE (owner ask 2026-10-16, §COMMCALC-15) ──── */}
      {/* "then give me a seaprate block with m2-m12 for each store with filters for all month and
          all stores". The block above answers the whole prospect for one month; this one answers
          every store for every month, which is the comparison that separates a site shrinking NOW
          (its 1st month falling) from one echoing a cohort lost months ago (1st month flat, M4–M6
          falling). Every figure is the server's, pivoted by the same module the live per-store leg
          trend uses — nothing on this screen computes a dollar. */}
      {pid && !loading && legStores && legB &&
        <div className="card" style={{ padding: '14px 16px', marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 2 }}>
            {legStores.trailing_label} per store, month by month
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 10 }}>
            {legStores.reading}
          </div>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center',
                        marginBottom: 10, fontSize: 12 }}>
            <label>
              <span style={{ color: 'var(--text3)', marginRight: 6 }}>Month</span>
              <select style={sel} value={legMonth} onChange={e => setLegMonth(e.target.value)}>
                <option value="">All months</option>
                {legStores.months_available.map(m => (
                  <option key={m} value={m}>
                    {m}{legStores.partial_months.includes(m) ? ' (part month)' : ''}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span style={{ color: 'var(--text3)', marginRight: 6 }}>Basis</span>
              <select style={sel} value={legShownBasis}
                      onChange={e => setLegBasis(e.target.value)}>
                {(['received', 'earned'] as const).map(b => (
                  <option key={b} value={b} disabled={!legStores.bases[b]?.measured}>
                    {b === 'received' ? 'Cash received (month paid)'
                                      : 'Earned (activation month)'}
                    {legStores.bases[b]?.measured ? '' : ' — not measured'}
                  </option>
                ))}
              </select>
            </label>
            <span style={{ color: 'var(--text3)' }}>
              {legRows.length} store{legRows.length === 1 ? '' : 's'}
              {storeFilter ? ' (filtered)' : ''} ·{' '}
              {legMonths.length} month{legMonths.length === 1 ? '' : 's'} shown of{' '}
              {legStores.months_available.length}
            </span>
          </div>
          <ReportShell title="" filename={`takeover-m2-m12-by-store-${pid}`}
                       columns={LEG_COLS} rows={legRows} compact stickyHeader pinFirst totals
                       noticeTone={Object.values(legB.ties).every(t => t.ok) ? 'info' : 'warn'}
                       notice={<>
                         <b>What this table is.</b> One row per store, one column per month, each
                         cell the {legStores.trailing_label} commission that store was paid (or
                         earned) in that month — {legB.basis_label || legShownBasis}. The rungs come
                         from your own file: {legB.ladder_months
                           .map(k => legB.ladder_labels[k]).join(', ')}. A month that has not
                         finished is marked a part month and is left out of every change figure,
                         because half a month of cash reads exactly like a store falling off a
                         cliff.
                         {legStores.partial_months.length > 0 &&
                           <> {' '}Part month{legStores.partial_months.length === 1 ? '' : 's'}:{' '}
                             {legStores.partial_months.join(', ')}.</>}
                         {legStores.rows_without_month > 0 &&
                           <> {' '}<b>{num(legStores.rows_without_month)} uploaded row
                             {legStores.rows_without_month === 1 ? '' : 's'} carry no month</b> and
                             are in no column here — that is a finding about the upload, not a
                             zero.</>}
                         {!Object.values(legB.ties).every(t => t.ok) &&
                           <> {' '}<b>These rows do not add to the company figure on{' '}
                             {Object.keys(legB.ties).filter(k => !legB.ties[k].ok).join(', ')}</b> —
                             money reached the total that the store axis could not place. It is
                             reported, not smoothed over.</>}
                       </>} />
        </div>}

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
                       columns={MATRIX_COLS} rows={matrixRows} compact stickyHeader
                       notice={<><b>Revenue, device cost and activations tie across the columns;
                         expenses and net income deliberately do not.</b> Each store column is charged
                         the FULL average monthly expense of one of our own stores, because that is
                         the question &mdash; could this one site stand on its own &mdash; and a share
                         of a per-store cost would make every column look profitable.</>} />
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
                       columns={storeColumns} rows={storeRows} compact stickyHeader totals
                       notice={<><b>Gross profit is the figure the accessory target comes off</b>
                         &mdash; carrier revenue less device cost, read from each store&rsquo;s own
                         statement rather than subtracted here. Carrier revenue is every incoming
                         carrier dollar these uploads carry, booked under your organisation&rsquo;s own
                         declared rules.
                         {ours && <><br /><b>Compare any row with a store you already own</b> using
                           the picker on the right. {ours.reading}</>}</>} />
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
                       columns={HANDSET_MODEL_COLS} rows={handsets.models} compact stickyHeader totals
                       noticeTone={handsets.set_aside.rows ? 'warn' : 'info'}
                       notice={<><b>These are orders placed, not lines activated.</b> Units times the
                         unit price on the order, the same multiplication the device cost on the
                         P&amp;L uses, so the two cannot disagree. A row carrying no unit price is set
                         aside and counted beside the table rather than treated as free.</>} />
          <div style={{ fontSize: 12, fontWeight: 600, margin: '14px 0 4px' }}>By store</div>
          <ReportShell title="" filename={`takeover-handsets-stores-${data?.period || ''}`}
                       columns={HANDSET_STORE_COLS} rows={handsetStores} compact stickyHeader totals
                       notice={<><b>Only the orders that name a ship-to store appear here.</b> An
                         order with no store is counted in the totals above but cannot be placed on a
                         site, so it is left out of this split rather than attributed to a guess.</>} />
          <div style={{ fontSize: 12, fontWeight: 600, margin: '14px 0 4px' }}>By month</div>
          <ReportShell title="" filename={`takeover-handsets-months-${data?.period || ''}`}
                       columns={HANDSET_MONTH_COLS} rows={handsets.by_month} compact stickyHeader totals
                       notice={<><b>Every uploaded month, so they can be compared</b> &mdash; use the
                         month filter on this table to read one alone. Orders carrying no date are
                         counted in the totals above and cannot appear in any month.</>} />
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

      {/* ── THE ACTIVATIONS, PER MONTH (owner ask 2026-10-14) ──────────────────────────────────
          "the activations per month details is also missing which breaks up teh new acts upgrade
          tablet fios etc". Counted by the platform's ONE activation classifier, so a figure here and
          a figure on a live store report mean the same thing. Not narrowed by the page's month on
          purpose: this IS the month table, and it carries its own month filter from the shared
          shell. Every number is the server's. */}
      {pid && !loading && acts && acts.totals.lines > 0 &&
        <div className="card" style={{ padding: '14px 16px', marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 2 }}>
            Activations, month by month
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
            {num(acts.totals.lines)} activated line{acts.totals.lines === 1 ? '' : 's'} across{' '}
            {acts.months} month{acts.months === 1 ? '' : 's'}, of which{' '}
            <b>{num(acts.totals.add_a_lines)}</b> {acts.totals.add_a_lines === 1 ? 'is' : 'are'} an
            add-a-line. Use the month dropdown on this table to read one month on its own.
          </div>
          <ReportShell title="" filename={`takeover-activations-by-month`}
                       columns={actColumns} rows={acts.by_month} compact stickyHeader pinFirst totals
                       noticeTone={acts.totals.unclassified ? 'warn' : 'info'}
                       notice={<>
                         <b>What this table is.</b> {acts.reading}
                         {acts.classes_absent.length > 0 &&
                           <> {' '}Your file records no{' '}
                             {acts.classes_absent.map(c => (acts.class_labels[c] || c).toLowerCase())
                               .join(' and no ')}{' '}
                             at all, so{' '}
                             {acts.classes_absent.length === 1 ? 'that column reads' : 'those columns read'}
                             {' '}zero because the file carries nothing to count, not because none were
                             sold.</>}
                         {acts.totals.unclassified > 0 &&
                           <> {' '}<b>{num(acts.totals.unclassified)} line
                             {acts.totals.unclassified === 1 ? '' : 's'} could not be placed</b>, under
                             the value{acts.totals.unclassified_values.length === 1 ? '' : 's'}{' '}
                             {acts.totals.unclassified_values.slice(0, 6)
                               .map(u => `${u.value} (${num(u.lines)})`).join(', ')}. Tell the platform
                             which kind of activation each of those words means and they will count.</>}
                       </>} />
        </div>}

      {/* ── WHAT WAS SOLD — the breakup at the end of the report (owner ask 2026-10-13) ──────────
          "the breakup of Phones tablets, Fios VHI, and if any other item was sold". The owner's own
          words name one carrier's brands for home internet; RULE TWO keeps a carrier name out of the
          code, so the class is "Home internet" and the words that match it are a config row. Every
          figure is the server's. */}
      {pid && !loading && products && products.totals.lines > 0 &&
        <div className="card" style={{ padding: '14px 16px', marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 2 }}>
            What was sold, month by month
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
            Every commission line of every uploaded month, placed in one class and one only, so the
            breakup adds up to the file. {data?.period} alone holds{' '}
            {num(products.totals.lines)} line{products.totals.lines === 1 ? '' : 's'}; use the month
            dropdown on this table to read any one month.
          </div>
          <ReportShell title="" filename="takeover-products-by-month"
                       columns={PRODUCT_COLS} rows={productRows} compact stickyHeader pinFirst totals
                       notice={<><b>What this table is.</b> Each row is one class of product in one
                         month. A line is placed by what the product is called on the sheet unless the
                         feed declares it outright, and the class is never guessed twice — so these
                         rows add up to the lines the P&amp;L above books from.</>} />
          {/* HOW EACH CLASS WAS DECIDED. A class read off a product description is not the evidence a
              class the feed declared is, and §66 was paid for exactly that confusion. */}
          <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 10, lineHeight: 1.6 }}>
            <b>How these were decided.</b>{' '}
            {num(products.basis_counts.declared_no_device)} line
            {products.basis_counts.declared_no_device === 1 ? '' : 's'} the feed itself declares as a
            customer bringing their own phone;{' '}
            {num(products.basis_counts.description_token)} placed by a word in the product name;{' '}
            {num(products.basis_counts.residual_device)} that matched no class and are counted as
            phones because this is a handset feed;{' '}
            {num(products.basis_counts.no_product_named)} naming no product at all.
            {products.basis_counts.residual_device > 0 &&
              <> Check the phone row&rsquo;s models below if you expect a category we have not set up
                 yet &mdash; anything that is plainly not a phone will be sitting there.</>}
          </div>
          {products.unplaced.length > 0 &&
            <div style={{ fontSize: 12, color: '#92400e', marginTop: 8 }}>
              ⚠︎ {products.unplaced.map(u => `${num(u.lines)} × ${u.description}`).join(', ')}
              {' '}&mdash; {products.unplaced.length === 1 ? 'this row names' : 'these rows name'} no
              product and {products.unplaced.length === 1 ? 'is' : 'are'} reported on their own line
              rather than counted as a phone.
            </div>}
        </div>}

      {/* ── THE SAME ACCOUNTS, MONTH BY MONTH, WITH THE DRILL-DOWN ──────────────────────────────
          Owner ask 2026-10-13: *"I need the accounts which are 2 more summarized at the bottom for
          each month with the drill down"*. One account carrying four lines in one month is a family;
          the same shape every month is a pattern, and that is the comparison no single month can
          make. Click a month to open its accounts. */}
      {pid && !loading && monthRows.length > 0 &&
        <div className="card" style={{ padding: '14px 16px', marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 2 }}>
            Accounts over {data?.multi_line_by_month?.threshold} lines, month by month
            &mdash; {monthRows.length} month{monthRows.length === 1 ? '' : 's'} uploaded
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
            Across every month this prospect has uploaded: <b>{num(monthTotals.flagged_accounts)}</b>
            {' '}account{monthTotals.flagged_accounts === 1 ? '' : 's'} over the threshold carrying
            {' '}<b>{num(monthTotals.flagged_lines)}</b> lines, out of
            {' '}{num(monthTotals.accounts)} accounts and {num(monthTotals.lines)} lines in all.
            {' '}An account is counted in each month it happens in: three lines added across three
            separate months is a customer who grew their plan, not a multi-line sale.
          </div>
          <ReportShell title="" filename={`takeover-multiline-by-month-${pid}`}
                       columns={MONTH_COLS} rows={monthRows} compact stickyHeader pinFirst totals
                       onRowClick={(r: MonthRow) => setOpenMonth(m => (m === r.period ? '' : r.period))}
                       notice={<><b>Click a month to open the accounts behind it.</b> Every uploaded
                         month is here on purpose, so comparing them is possible; the month filter on
                         this table narrows it to one. An account is counted in each month it happens
                         in, because three lines added across three months is a customer who grew
                         their plan rather than a multi-line sale.</>} />
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 9 }}>
            This table is every uploaded month on purpose, so the <b>Month</b> filter at the top does
            not narrow it &mdash; comparing the months is the whole question it answers. The
            {' '}<b>Stores</b> filter does not narrow it either: an account&rsquo;s lines can sit on
            more than one store account, so a per-store count of them could not be tied back to
            either store. The account, model and line filters DO apply to the accounts below.
          </div>
          {openMonth &&
            <div style={{ marginTop: 12, borderTop: '1px solid var(--line)', paddingTop: 10 }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8 }}>
                <div style={{ fontSize: 12.5, fontWeight: 700 }}>{openMonth}</div>
                <span style={{ fontSize: 12, color: 'var(--text3)' }}>
                  {num(drilled.length)} account{drilled.length === 1 ? '' : 's'} over the threshold
                </span>
                <span style={{ flex: 1 }} />
                <button className="btn btn-secondary" style={{ fontSize: 12 }}
                        onClick={() => setOpenMonth('')}>Close</button>
              </div>
              {drilled.length === 0
                ? <div style={{ fontSize: 13, color: 'var(--text2)' }}>
                    No account in {openMonth} is over the threshold once the filters at the top of the
                    page are applied.
                  </div>
                : <ReportShell title="" filename={`takeover-multiline-${openMonth}`}
                               columns={FLAG_COLS} rows={drilled} compact stickyHeader pinFirst
                               notice={<><b>The accounts behind {openMonth}.</b> Each row is one
                                 carrier billing account and its <b>Lines</b> figure is how many
                                 lines sit on it, so the rows do not add up to the month&rsquo;s
                                 account count.</>} />}
              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>
                These are the same accounts the month report above lists, for this month. What they are
                worth to the bottom line is measured for the evaluated month only &mdash; pick
                {' '}<b>{openMonth}</b> in the Month filter to see the P&amp;L with and without them.
              </div>
            </div>}
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
