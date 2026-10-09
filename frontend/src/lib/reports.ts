import { Permissions, Scope, canSeeItem } from '@/lib/rbac'

// Canonical catalog of REPORTS across every module — the single source for the unified Reports hub
// (/reports) and the employee portal surfacing. Curated (not every nav item is a report); keep in
// sync when a new report page ships.
export type ReportDef = { href: string; label: string; module: string; scopes?: Scope[]; desc?: string }
export type PortalCfg = Record<string, { enabled: boolean; roles: string[]; label?: string; category?: string }>

export const REPORT_CATEGORIES: { category: string; reports: ReportDef[] }[] = [
  { category: 'Commissions', reports: [
    { href: '/commcalc', label: 'Commissions Dashboard', module: 'commissions' },
    { href: '/commcalc/exec', label: 'Owner Overview', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/activations', label: 'Activations', module: 'commissions', scopes: ['all', 'market'], desc: 'POS Activation Details basis of truth — distinct devices by market/store, Upgrade toggle, and automatic reconciliation against the sales feed' },
    { href: '/commcalc/schematic', label: 'System Schematic', module: 'commissions', scopes: ['all'], desc: 'Data-lineage map: how ingested data and metrics feed each other, with code references and plain-English effects' },
    { href: '/commcalc/reports-index', label: 'Reports Index', module: 'commissions', scopes: ['all', 'market'], desc: 'A searchable directory of every report on the platform — what each shows, who can see it, and whether it is live for you and what data feeds it' },
    { href: '/commcalc/onboarding', label: 'Setup Wizard', module: 'commissions', scopes: ['all'], desc: 'Guided onboarding: every data feed & config the platform needs, whether it is set up for you, and where to complete each one' },
    { href: '/commcalc/integrations', label: 'Integrations', module: 'commissions', scopes: ['all'], desc: 'One page for every data connection & import — plain-English purpose, live connected/not-set-up status, and a 2-step setup wizard for each' },
    { href: '/commcalc/gp', label: 'Gross Profit', module: 'commissions' },
    { href: '/commcalc/kpi', label: 'KPI Metrics', module: 'commissions' },
    { href: '/commcalc/kpi-failing', label: 'Failing KPIs', module: 'commissions', scopes: ['all', 'market'], desc: 'High-level overview of every KPI below target — org, market and store grain with rep drill-down' },
    { href: '/commcalc/zero-sales', label: 'Zero Sales', module: 'commissions', scopes: ['all', 'market'], desc: "Store-days — and the rep-days underneath them — with no activation and no upgrade, over any date range. A day counts as zero only when the feed DID carry that store and none of its transactions was an activation or upgrade; a day nothing landed for reads 'not reported', never 0, and is never counted towards a run of consecutive zero days. Managers are emailed after N consecutive zero trading days (config, house default 2)." },
    { href: '/commcalc/coaching', label: 'Rep Coaching', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/sales-analyzer', label: 'Retention Analysis', module: 'commissions', scopes: ['all', 'market', 'store'], desc: '3-month retention by rep/store — cohort retained vs churned, driven by whether the month-3 residual was paid' },
    { href: '/commcalc/sales-comparison', label: 'Sales Comparison', module: 'commissions', desc: 'Month-over-month / year-over-year % change per item sold (phones, BYOD, accessories, tablets, financing) across all stores' },
    { href: '/commcalc/peer-comparison', label: 'Peer Sales Comparison', module: 'commissions', scopes: ['all', 'market'], desc: 'Stores grouped by bill-payment volume — the measure of people through the door — then compared on total boxes (with the new / port / BYOD / upgrade / swap / tablet split), add-a-line, family plan %, accessory $ and accessory $ per box. Within a band the footfall is the same, so a gap is sell-through rather than location; each store is scored against its own band median and best.' },
    { href: '/commcalc/targets/report-cards', label: 'Manager Report Cards', module: 'commissions', scopes: ['all', 'market'], desc: 'Every item assigned to a district manager for each of their stores — the four target categories, conversion, and keeping up with stores of the same footfall — each one checked off by the system as met, missed, or no target set, and scored over the items that actually had a target. The card below it rolls the same check-off up to the manager above, a row per district manager, so accountability does not stop at the district. A store the org tree cannot place is listed separately with the reason, never dropped and never given a guessed owner.' },
    { href: '/commcalc/spiff-impact', label: 'Spiff Impact', module: 'commissions', scopes: ['all', 'market'], desc: 'Pick one carrier pay type and see, per store, what it is worth to the commission payout revenue (its share of the P&L line it actually books to), by how much it raises net profit (profit with it against profit without it, from the stored per-store P&L), and which stores are not earning it on the boxes they sell — ranked inside each store’s own bill-payment traffic band, so the gap is sell-through and not footfall.' },
    { href: '/commcalc/accessory-target-plan', label: 'Accessory Target Allocation', module: 'commissions', scopes: ['all', 'market'], desc: 'Set one company accessory sales goal — a fixed total, or a % up or down from a named basis — and the report works out the target each store should carry, weighted by its own accessories-per-box over the last two months against the boxes it is projected to sell. Shows each of the last two months in its own column, month to date, the projected month-end, the target in force and the extension needed to meet the goal, then assigns the result to every store or just the ones picked from the multi-select. A store that sold boxes and attached nothing is weighted at the company rate and flagged, never given a $0 target.' },
    { href: '/commcalc/product-mix', label: 'Product Mix & Ports', module: 'commissions', scopes: ['all', 'market'], desc: 'What each store and each rep actually sold — which handset, and what the customer paid for it, banded free / budget / mid / premium — set against accessory $ per box and the share of their boxes that ported in. A rep is flagged only when BOTH are below the median of the reps in view, because the two signals correlate only weakly and a blended score would hide the rep who ports well and attaches nothing. Shows the carrier\u2019s own port-in rate per store beside ours, measures the relationship between the cheap-device share and both outcomes every run rather than assuming it, and hands the lagging stores to the Peer Sales Comparison\u2019s own band, median and coaching sentence so this screen and the action plan name the same stores.' },
    { href: '/commcalc/comp-trend', label: 'Total Compensation', module: 'commissions', scopes: ['all', 'market'] },
    // DM GATE — mirrors the rbac.ts NAV row 1:1 (owner directive 2026-08-07). This catalog is the
    // SECOND door to the same page (Report Center /reports + the employee portal), and clearedFor()
    // gates it with canSeeItem() on THIS object — so without the same `scopes` a store-scoped user
    // would still be shown a Flags link here that the layout Guard (canAccessPath, which reads NAV)
    // then bounces: the "the tab is there but clicking it does nothing" class. Chargebacks already
    // carries the pair; Flags now matches it on both surfaces.
    { href: '/commcalc/flags', label: 'Flags', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/chargebacks', label: 'Chargebacks & Fraud', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/accessory-flags', label: 'Accessory Flags', module: 'commissions', scopes: ['all', 'market'] },
    // DM GATE mirrored 1:1 from the rbac.ts NAV row: this catalog is the SECOND door to the same
    // page and clearedFor() gates it with canSeeItem() on THIS object, so without the same `scopes`
    // a store-scoped user would be shown a link the layout Guard then bounces.
    { href: '/commcalc/commission-withholding', label: 'Commission Withholding', module: 'commissions', scopes: ['all', 'market'], desc: 'Every activation the carrier took commission back on, with what was taken, whether commission landed against the same activation in a later month and in which months, the processor payment actually made against it shown in parallel and never netted against the clawback, and the appeal state a manager set. An activation that could not be looked up is reported as not known, never as a loss.' },
    { href: '/commcalc/discrepancy', label: 'Pay Discrepancy', module: 'commissions', desc: 'Underpaid / unpaid activations per month — the carrier bounty gap engine, plus POS activations verified against the payment processor\'s commission feeds: sold-but-unpaid rows attributed via uploadable business rules, or flagged "no business rule configured"' },
    { href: '/commcalc/imei-rebates', label: 'IMEI Rebate Reconciliation', module: 'commissions', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/carrier-vs-pay', label: 'Carrier Earned vs Employee Paid', module: 'commissions', scopes: ['all', 'market'], desc: "Per rep, per month: what the carrier statement (or the processor payment feed) says we earned on that rep's activations, against what we actually paid them. Two different ledgers — dealer revenue and payroll expense — reported side by side and never summed. An un-uploaded feed reads 'not reported', never $0.00 earned, and no margin is published for a rep whose carrier side was not measured." },
    { href: '/commcalc/vendor-rebates', label: 'Vendor Rebate History (earned, per line)', module: 'commissions', scopes: ['all', 'market'], desc: 'Every rebate/commission line the carrier statement says it OWES, landed per component. Earned and Collected are reported separately and never summed — this feed proves no payment and books nothing to the P&L, GP or payout.' },
    { href: '/commcalc/ma-handsets', label: 'Marketplace Handset COGS', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/device-cost-recon', label: 'Device Cost Reconciliation', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/sales-recon', label: 'Sales Feed Recon', module: 'commissions', scopes: ['all', 'market'] },
  ] },
  { category: 'Flags & Compliance', reports: [
    // MANAGEMENT WATCHDOG (owner ask 2026-10-05, index §53): "keep these reports in management
    // dashboard under different reports so it is easy for the management to review each area and
    // take appropriate action". The board, then each AREA as its own report so one person can own
    // one area end to end. DM GATE mirrored 1:1 from the rbac.ts NAV rows — this catalog is the
    // SECOND door to the same pages and clearedFor() gates it with canSeeItem() on THESE objects,
    // so without the same `scopes` a store-scoped user would be shown a link the layout Guard then
    // bounces (the "the tab is there but clicking it does nothing" class).
    { href: '/watchdog', label: 'Management Watchdog', module: 'commissions', scopes: ['all', 'market'], desc: 'Every finding the platform raises, grouped by the part of the business you would go and act on — cash and closing, voids and returns, sales against commission, fraud, port-outs, inventory, distributor, and feed integrity. Each area opens its own report. A finding stays until somebody rules on it and clears itself when its cause is fixed; nothing is deleted, so the decision trail survives. An area with nothing open says so in words rather than showing a bare 0, and a finding whose kind is not registered is counted under Unclassified rather than hidden.' },
    { href: '/watchdog/cash', label: 'Watchdog · Cash & Closing', module: 'commissions', scopes: ['all', 'market'], desc: 'Drawer and card variances on store-days that were accepted, waved through on the final try while still mismatched, or submitted and never corrected — plus the same person turning up on repeated variance days. Every figure is the close gate\'s OWN recorded comparison, never recomputed, and a store-day the sales feed never carried shows no variance rather than a fabricated zero.' },
    { href: '/watchdog/voids', label: 'Watchdog · Voids, Returns & Waived Fees', module: 'commissions', scopes: ['all', 'market'], desc: 'Reversed and returned transactions above the allowed share, one device both sold and voided, voided lines carrying nobody\'s name, and activations with no setup fee charged. The classification is the commission path\'s own rule read a second way, so what counts as a void here is what counts as a void for pay.' },
    { href: '/watchdog/void-register', label: 'Void & Return Register', module: 'commissions', scopes: ['all', 'market'], desc: 'Every voided, returned and unnamed sales line for the period, with the rate by person and by store. These lines are already excluded from commission and gross profit — this is the first place they are counted rather than only thrown away. A rate reads "—", never 0%, where there were no lines to measure.' },
    { href: '/watchdog/commission', label: 'Watchdog · Sales vs Commission', module: 'commissions', scopes: ['all', 'market'], desc: 'Sales the commission feed never carried, sales paid at an amount that does not match the sale, commission clawed back, and installments withheld because the line stopped paying.' },
    { href: '/watchdog/fraud', label: 'Watchdog · Fraud & Identity', module: 'commissions', scopes: ['all', 'market'], desc: 'One device used on several lines, and activation patterns that do not look like retail selling.' },
    { href: '/watchdog/churn', label: 'Watchdog · Port-outs & Churn', module: 'commissions', scopes: ['all', 'market'], desc: 'Subscribers that ported out, were transferred to another dealer, or were suspended for non-payment, banded by how soon after the sale.' },
    { href: '/watchdog/inventory', label: 'Watchdog · Inventory & Assets', module: 'commissions', scopes: ['all', 'market'], desc: 'Units the ledger and the shelf disagree about, devices reimbursed below the published rate, returns never credited, and devices sold below cost.' },
    { href: '/watchdog/distributor', label: 'Watchdog · Distributor & Payables', module: 'commissions', scopes: ['all', 'market'], desc: 'Invoices due and overdue, credit memos that do not reconcile, and rebates earned but never received.' },
    { href: '/watchdog/feed', label: 'Watchdog · Feed & Data Integrity', module: 'commissions', scopes: ['all', 'market'], desc: 'A store or payment type the platform received data for but cannot place — a gap that makes every other number for that store wrong.' },
    { href: '/compliance', label: 'Flags & Compliance Dashboard', module: 'commissions', scopes: ['all', 'market'], desc: 'Per-queue open counts across every flag, exception and compliance queue on the platform — each count is the same query as the page that owns the queue, and a probe that fails shows "—" rather than a fake 0' },
    // DM GATE mirrored 1:1 from the rbac.ts NAV row above: this catalog is the SECOND door to the
    // same page, and clearedFor() gates it with canSeeItem() on THIS object — without the same
    // `scopes` a store-scoped user would be shown a link the layout Guard then bounces.
    { href: '/commcalc/manager-followup', label: 'Follow Up With Managers', module: 'commissions', scopes: ['all', 'market'], desc: 'Every pending job a manager owns, rolled up per store and queue with the age of the oldest — what has passed the escalation age reaches the manager above the owner too. Open items carrying no store are counted and named rather than dropped, and any queue that cannot yet be assigned per manager is listed with the reason, so the board is never read as "that is everything". A digest goes out daily at the configured time by WhatsApp and email.' },
  ]},
  { category: 'Targets', reports: [
    { href: '/commcalc/targets', label: 'Daily Targets', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/action-plan', label: 'Action Plan', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/my', label: 'My Targets', module: 'targets' },
  ] },
  { category: 'Team', reports: [
    { href: '/storeops/team', label: 'My Team', module: 'storeops', scopes: ['all', 'market', 'store'] },
  ] },
  { category: 'Asset', reports: [
    // Neutral label (owner 2026-09-04): "Asset Ledger" is Boost-side vocabulary; the page itself is
    // carrier-gated in NAV_CARRIERS, and this catalog label stays carrier-neutral.
    { href: '/commcalc/asset', label: 'Assets', module: 'asset' },
  ] },
  { category: 'Distributor', reports: [
    { href: '/commcalc/vip', label: 'Distributor Invoices', module: 'vip' },
  ] },
  { category: 'Accounts', reports: [
    { href: '/accounts', label: 'Accounts Dashboard', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/pl', label: 'P&L Statement', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/balance-sheet', label: 'Balance Sheet', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/recon', label: 'Reconciliation', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/residual-per-sub', label: 'Residual per Subscriber', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/trends', label: 'Trends (all metrics)', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/analysis', label: 'Financial Analysis (charts · projections · valuation)', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/cash-flow', label: 'Cash Flow Statement', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/liabilities-due', label: 'Current Monetary Liabilities', module: 'accounts', scopes: ['all', 'market'], desc: 'Owed to distributor, payments due this week, payroll & payroll tax due, rents and recurring expenses due — per store' },
    { href: '/accounts/device-purchases', label: 'Device Purchases', module: 'accounts', scopes: ['all', 'market'], desc: 'Cost of every phone/device the distributor billed us in a period, segregated by company and by store. PURCHASES (what was billed) — deliberately separate from device COGS (what the units we sold cost us)' },
    { href: '/accounts/device-payable', label: 'Device Payable as at a date', module: 'accounts', scopes: ['all', 'market'], desc: 'Of the devices already billed to us on a chosen day, which ones had not yet been paid for — by company and by store. A BACKDATED payable read from each unit\u2019s own payment date, not from a current status; non-device items (chargebacks, fees, SIMs) are reported separately on a billed basis' },
  ] },
  { category: 'StoreOps', reports: [
    { href: '/storeops/reports', label: 'Hours / Payroll Reports', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/payroll', label: 'Payroll', module: 'storeops', scopes: ['all', 'market'] },
  ] },
  { category: 'Daily Closing', reports: [
    { href: '/closing', label: 'Closing Dashboard', module: 'closing', scopes: ['all', 'market', 'store'] },
    { href: '/closing/recon', label: 'Closing Reconciliation', module: 'closing', scopes: ['all', 'market'] },
  ] },
]

const asNav = (r: ReportDef) => ({ href: r.href, label: r.label, icon: '', module: r.module, scopes: r.scopes })

// Does the report's clearance (module + scope) allow this user? (Roles & Access rules.)
export function clearedFor(perms: Permissions, r: ReportDef): boolean {
  return canSeeItem(perms, asNav(r) as any)
}

// Reports to surface in a user's portal: enabled in config + role allowed + has clearance.
export function myPortalReports(perms: Permissions, roleName: string | null, cfg: PortalCfg) {
  const out: { category: string; reports: ReportDef[] }[] = []
  for (const grp of REPORT_CATEGORIES) {
    const reports = grp.reports.filter(r => {
      const c = cfg[r.href]
      if (!c || !c.enabled) return false
      if (c.roles && c.roles.length && roleName && !c.roles.includes(roleName)) return false
      return clearedFor(perms, r)
    })
    if (reports.length) out.push({ category: grp.category, reports })
  }
  return out
}
