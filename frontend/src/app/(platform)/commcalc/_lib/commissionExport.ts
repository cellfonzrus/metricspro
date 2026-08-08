// Commission-report EXPORT PAYLOAD — pure, dependency-free, and therefore testable.
//
// WHY THIS FILE EXISTS (owner bug 2026-08-04):
//   "when commission for one employee is exported it sends the commission for all employees in one
//    pdf, it should only send the one which is selected."
// The Send button on /commcalc/reports took the SERVER-rendered path —
//   <SendReportButton reportKey="commissions" filters={{ period }} />
// — so notify/report_registry._commissions re-ran get_commissions() for the WHOLE org with nothing
// but the period. Every rep's pay landed in the emailed / WhatsApped PDF + XLSX no matter who was
// selected on screen. That is a pay-privacy leak that LEAVES the system, not merely a wrong download.
//
// The fix is structural rather than "pass more filters": every format now renders IN-BROWSER from
// the rows already on screen, so there is no second query that could forget a filter. Scope:
//   • breakdown / compensation → the standard-bar FILTERED rows (store · market · rep(s))
//   • individual               → the SELECTED rep, ALONE
// The input rows come from /commcalc/commissions, which is already org-scoped, so tenant isolation
// is inherited (contract §2). READ-PATH ONLY — no rate, tier, plan rule or stored payout is touched.
//
// Everything imported here is `import type`, so this module compiles and runs standalone under plain
// node — which is what tools/commission-export-proof.mjs exercises.
import type { ExportColumn, ExportPayload } from '@/lib/export'
import type { StandardFilterValue } from '@/lib/standard-filters'

export type CommissionTab = 'breakdown' | 'individual' | 'compensation'

/** The subset of a /commcalc/commissions row the export reads. Kept structural (not the page's `Rep`
 *  interface) so this module has zero imports from the page. */
export type CommissionRow = {
  epay_salesperson: string
  storeops_name?: string
  store?: string
  tier?: number
  kpis_met?: number
  total_kpis?: number
  premium_acts?: number; byod_acts?: number; upgrade_acts?: number
  premium_comm?: number; byod_comm?: number; upgrade_comm?: number
  acc_comm?: number; setup_fee_comm?: number; trade_in_comm?: number; acima_comm?: number
  subtotal?: number; total_payout?: number
  residual_installment_comm?: number
  installment_comm_sale?: number
  carrier_statement_comm?: number
  plan_comm?: number
  plan_name?: string
  ops_chargeback_deduction?: number
  ops_chargeback_lines?: OpsChargebackLine[]
}

/** A POSTED ops-accountability chargeback line (retail-ops writes these; read-only here). */
export type OpsChargebackLine = {
  label?: string; amount?: number; reason?: string
  incident_date?: string; store?: string; status?: string
}

/** A reviewable chargeback item from /commcalc/chargebacks. */
export type ChargebackItem = {
  id?: string; epay_salesperson?: string; source?: string; description?: string
  mdn?: string; imei?: string; amount?: number; deduct?: boolean
}

/** Synthetic rows the single-rep statement builds (they are not commission rows). */
type KVRow = { k: string; v: string }
type LineRow = { item: string; count?: string | number; rate?: string; amount?: number }

export type CommissionExportInput = {
  tab: CommissionTab
  period: string
  isBoost: boolean
  /** every loaded row — used only for the "N of M" subtitle, never exported */
  reps: CommissionRow[]
  /** the standard-bar filtered rows — the export scope on the list tabs */
  filtered: CommissionRow[]
  /** the rep the Individual tab is showing — the ONLY person that tab may export */
  currentRep: CommissionRow | null
  filt: StandardFilterValue
  /** commission config (rates shown in the Rate column) */
  cfg: Record<string, number>
  /** reviewable chargeback items for the period, all reps */
  chargebacks: ChargebackItem[]
  /** mirrors the on-screen Installment column, which only appears when someone has installment pay */
  hasInstallment: boolean
  /** OPTIONAL, display-only (owner 2026-08-06): repLabel(row) → the rep's Google store rating(s) as
   *  ONE cell ("S123 4.6/4.7 · S200 4.8/4.7"). Present only when the page actually rendered chips, so
   *  the export stays WYSIWYG; absent ⇒ every payload is byte-identical to before this field existed.
   *  Carries NO money and is never summed. */
  ratingByRep?: Record<string, string>
}

// identical to lib/client's `fmt` — duplicated (2 lines) so this module stays import-free & testable
const fmt = (n: number | undefined) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(n) || 0)
const slug = (s: string) => (s || 'report').replace(/[^\w]+/g, '-').replace(/^-|-$/g, '').toLowerCase()
const instOf = (r: CommissionRow) => (r.residual_installment_comm || 0) + (r.carrier_statement_comm || 0)
export const repLabel = (r: CommissionRow) => r.storeops_name || r.epay_salesperson

export function filterDesc(filt: StandardFilterValue): string {
  const p: string[] = []
  if (filt.stores?.length) p.push(`stores: ${filt.stores.join(', ')}`)
  if (filt.markets?.length) p.push(`markets: ${filt.markets.join(', ')}`)
  if (filt.reps?.length) p.push(`reps: ${filt.reps.join(', ')}`)
  return p.join(' · ')
}

/** The Google-rating column, appended only when the page has ratings to show (see ratingByRep). */
function ratingCol(ratingByRep?: Record<string, string>): ExportColumn[] {
  if (!ratingByRep || Object.keys(ratingByRep).length === 0) return []
  return [{ header: 'Google rating', get: (r: CommissionRow) => ratingByRep[repLabel(r)] || '' }]
}

function breakdownCols(hasInstallment: boolean, ratingByRep?: Record<string, string>): ExportColumn[] {
  return [
    { header: 'Rep', get: (r: CommissionRow) => repLabel(r) },
    { header: 'Salesperson', get: (r: CommissionRow) => r.epay_salesperson },
    { header: 'Store', get: (r: CommissionRow) => r.store },
    { header: 'Tier', get: (r: CommissionRow) => `${Math.round((r.tier || 0) * 100)}%`, align: 'right' },
    { header: 'KPIs', get: (r: CommissionRow) => `${r.kpis_met}/${r.total_kpis}`, align: 'right' },
    { header: 'PA', get: (r: CommissionRow) => r.premium_acts, align: 'right' },
    { header: 'BA', get: (r: CommissionRow) => r.byod_acts, align: 'right' },
    { header: 'UA', get: (r: CommissionRow) => r.upgrade_acts, align: 'right' },
    { header: 'ACC GP', get: (r: CommissionRow) => r.acc_comm, money: true },
    { header: 'ACIMA', get: (r: CommissionRow) => r.acima_comm, money: true },
    ...(hasInstallment ? [{ header: 'Installment', get: (r: CommissionRow) => instOf(r), money: true } as ExportColumn] : []),
    { header: 'Subtotal', get: (r: CommissionRow) => r.subtotal, money: true },
    { header: 'Payout', get: (r: CommissionRow) => r.total_payout, money: true },
    ...ratingCol(ratingByRep),
  ]
}

function compensationCols(ratingByRep?: Record<string, string>): ExportColumn[] {
  return [
    { header: 'Rep', get: (r: CommissionRow) => repLabel(r) },
    { header: 'Premium', get: (r: CommissionRow) => r.premium_comm, money: true },
    { header: 'BYOD', get: (r: CommissionRow) => r.byod_comm, money: true },
    { header: 'Upgrades', get: (r: CommissionRow) => r.upgrade_comm, money: true },
    { header: 'Accessories', get: (r: CommissionRow) => r.acc_comm, money: true },
    { header: 'Setup Fees', get: (r: CommissionRow) => r.setup_fee_comm, money: true },
    { header: 'Trade-Ins', get: (r: CommissionRow) => r.trade_in_comm, money: true },
    { header: 'ACIMA', get: (r: CommissionRow) => r.acima_comm, money: true },
    { header: 'Subtotal', get: (r: CommissionRow) => r.subtotal, money: true },
    { header: 'Payout', get: (r: CommissionRow) => r.total_payout, money: true },
    ...ratingCol(ratingByRep),
  ]
}

/** Chargeback arithmetic for ONE rep — the same sums the Individual tab prints on screen. */
export function repChargebacks(r: CommissionRow, chargebacks: ChargebackItem[]) {
  const items = (chargebacks || []).filter(cb => cb.epay_salesperson === r.epay_salesperson)
  const deducted = items.filter(c => c.deduct).reduce((s, c) => s + (c.amount || 0), 0)
  const opsLines = r.ops_chargeback_lines || []
  const opsTotal = r.ops_chargeback_deduction ?? opsLines.reduce((s, l) => s + (l.amount || 0), 0)
  return { items, deducted, opsLines, opsTotal, final: (r.total_payout || 0) - deducted - opsTotal }
}

/** The single-rep commission statement. Every sheet is built from `r` alone — no other row is
 *  reachable from here, which is the property the proof harness asserts. */
function individualSheets(r: CommissionRow, i: CommissionExportInput): ExportPayload['sheets'] {
  const { isBoost, cfg, period, chargebacks } = i
  const rating = i.ratingByRep?.[repLabel(r)] || ''
  const cb = repChargebacks(r, chargebacks)
  const sheets: ExportPayload['sheets'] = []
  const kv: ExportColumn[] = [
    { header: 'Field', get: (x: KVRow) => x.k },
    { header: 'Value', get: (x: KVRow) => x.v },
  ]
  sheets.push({
    name: 'Summary', columns: kv, rows: [
      { k: 'Rep', v: repLabel(r) },
      { k: 'Salesperson', v: r.epay_salesperson },
      { k: 'Store', v: r.store || '' },
      ...(rating ? [{ k: 'Google store rating', v: rating }] : []),
      { k: 'Period', v: period },
      ...(isBoost
        ? [{ k: 'Tier multiplier', v: `${Math.round((r.tier || 0) * 100)}%` },
           { k: 'KPIs met', v: `${r.kpis_met}/${r.total_kpis}` }]
        : [{ k: 'Commission Plan', v: r.plan_name || '— none assigned —' }]),
      { k: 'Subtotal (pre-tier)', v: fmt(r.subtotal) },
      { k: 'Total payout', v: fmt(r.total_payout) },
      { k: 'Chargebacks deducted', v: fmt(-cb.deducted) },
      { k: 'Ops chargebacks deducted', v: fmt(-cb.opsTotal) },
      { k: 'Final payout', v: fmt(cb.final) },
    ],
  })
  if (isBoost) {
    // the Boost KPI-tier line items, exactly as the card renders them
    const li: ExportColumn[] = [
      { header: 'Item', get: (x: LineRow) => x.item },
      { header: 'Count', get: (x: LineRow) => x.count, align: 'right' },
      { header: 'Rate', get: (x: LineRow) => x.rate, align: 'right' },
      { header: 'Commission', get: (x: LineRow) => x.amount, money: true },
    ]
    const rows: LineRow[] = [
      { item: 'Premium Activations', count: r.premium_acts, rate: `${fmt(cfg.premium_flat || 0)}/act`, amount: r.premium_comm },
      { item: 'BYOD Activations', count: r.byod_acts, rate: `${fmt((cfg.byod_flat || 0) + (cfg.byod_extra_spiff || 0))}/act`, amount: r.byod_comm },
      { item: 'Device Upgrades', count: r.upgrade_acts, rate: `${fmt(cfg.upgrade_flat || 0)}/act`, amount: r.upgrade_comm },
      { item: 'Accessories', count: 'GP', rate: '10% GP', amount: r.acc_comm },
      { item: 'Setup Fees', count: 'GP', rate: '10% GP', amount: r.setup_fee_comm },
      { item: 'Trade-In SPIFF', count: '—', rate: `${fmt(cfg.trade_in_spiff || 0)}/trade`, amount: r.trade_in_comm },
    ]
    if ((r.acima_comm || 0) > 0) rows.push({
      item: 'ACIMA Lease SPIFF', count: `${Math.round((r.acima_comm || 0) / (cfg.acima_spiff || 25))} txns`,
      rate: `${fmt(cfg.acima_spiff || 25)} each`, amount: r.acima_comm })
    rows.push({ item: 'Subtotal', count: '', rate: '', amount: r.subtotal })
    rows.push({ item: `× ${Math.round((r.tier || 0) * 100)}% Tier — Total Payout`, count: '', rate: '', amount: r.total_payout })
    sheets.push({ name: 'Line Items', columns: li, rows })
  } else {
    // plan-mode (non-Boost): pay comes from the assigned Commission Plan + the two installment engines
    const li: ExportColumn[] = [
      { header: 'Item', get: (x: LineRow) => x.item },
      { header: 'Amount', get: (x: LineRow) => x.amount, money: true },
    ]
    const iSale = r.installment_comm_sale || 0, iResid = r.residual_installment_comm || 0
    const rows: LineRow[] = [{ item: `Plan commission — ${r.plan_name || 'no plan assigned'}`, amount: r.plan_comm ?? 0 }]
    // mirrors the card: the sale-triggered row renders even at $0 (it is the one with a drill path),
    // unless the residual engine is the only payer — then a second $0 row would be meaningless
    if (iSale !== 0 || iResid === 0) rows.push({ item: `Multi-month installments${iResid !== 0 ? ' (sale-triggered)' : ''}`, amount: iSale })
    if (iResid !== 0) rows.push({ item: 'Multi-month installments (residual · raw_mi)', amount: iResid })
    rows.push({ item: 'Total Payout', amount: r.total_payout })
    sheets.push({ name: 'Line Items', columns: li, rows })
  }
  if (cb.items.length) sheets.push({
    name: 'Chargebacks', rows: cb.items, columns: [
      { header: 'Source', get: (c: ChargebackItem) => c.source },
      { header: 'Description', get: (c: ChargebackItem) => c.description },
      { header: 'MDN/IMEI', get: (c: ChargebackItem) => c.mdn || c.imei || '' },
      { header: 'Deduct?', get: (c: ChargebackItem) => (c.deduct ? 'yes' : 'no') },
      { header: 'Amount', get: (c: ChargebackItem) => c.amount, money: true },
    ],
  })
  if (cb.opsLines.length) sheets.push({
    name: 'Ops Chargebacks', rows: cb.opsLines, columns: [
      { header: 'Ops chargeback', get: (l: OpsChargebackLine) => l.label },
      { header: 'Reason', get: (l: OpsChargebackLine) => l.reason || '' },
      { header: 'Incident date', get: (l: OpsChargebackLine) => l.incident_date || '' },
      { header: 'Store', get: (l: OpsChargebackLine) => l.store || '' },
      { header: 'Status', get: (l: OpsChargebackLine) => l.status || 'posted' },
      { header: 'Amount', get: (l: OpsChargebackLine) => -(l.amount || 0), money: true },
    ],
  })
  return sheets
}

/** The one entry point every export format on /commcalc/reports goes through. */
export function buildCommissionExport(i: CommissionExportInput): ExportPayload {
  if (i.tab === 'individual') {
    const r = i.currentRep
    const who = r ? repLabel(r) : ''
    return {
      title: `Commission Statement — ${who || 'no rep selected'}`,
      subtitle: `${i.period}${r?.store ? ` · ${r.store}` : ''}${r && !i.isBoost && r.plan_name ? ` · plan: ${r.plan_name}` : ''}`,
      filename: `commission-${slug(who)}-${slug(i.period)}`,
      sheets: r ? individualSheets(r, i) : [],
    }
  }
  const fd = filterDesc(i.filt)
  const active = !!fd
  const isComp = i.tab === 'compensation'
  return {
    title: isComp ? 'Compensation by Line' : 'Rep Commission Report',
    subtitle: `${i.period} · ${i.filtered.length}${active ? ` of ${i.reps.length}` : ''} reps${fd ? ` · ${fd}` : ''}`,
    filename: `commissions-${isComp ? 'by-line-' : ''}${slug(i.period)}${active ? '-filtered' : ''}`,
    sheets: [{
      name: isComp ? 'Compensation by Line' : 'Rep Payouts',
      columns: isComp ? compensationCols(i.ratingByRep) : breakdownCols(i.hasInstallment, i.ratingByRep),
      rows: i.filtered,
    }],
  }
}

/** CSV over the SAME payload, so CSV · Excel · PDF · Print · Send carry byte-identical scope.
 *  Money cells stay raw numbers so a spreadsheet can total them. */
export function payloadToCsv(p: ExportPayload): string {
  const q = (v: unknown) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s }
  const lines: string[] = []
  for (const sh of p.sheets) {
    if (p.sheets.length > 1) lines.push(q(sh.name))
    lines.push(sh.columns.map(c => q(c.header)).join(','))
    for (const r of sh.rows) lines.push(sh.columns.map(c => q(c.get(r))).join(','))
    lines.push('')
  }
  return lines.join('\n')
}
