// A DATA SOURCE'S PLAIN NAME — the one frontend home (owner 2026-10-02, index §19.38: "hide database names from
// the users").
//
// A customer reads "MI & ATU report", never the table it lands in. A page that names a feed — in a sentence, a
// tooltip, a column header, or a table name the API sent as data (`target_table`, `source_table`, `table`) —
// says it through `sourceLabel()`. The wording follows the report-kind registry's own labels
// (backend commcalc/report_kinds.HOUSE_KINDS) and the MA pull registry's display names (commcalc/report_pull.py),
// so a feed has one name wherever it shows.
//
// Every key below must be a REGISTERED table (database/migrations or data_lineage_registry) — the lock
// (backend/harness_carrier_vocab_guard.py §INFRA, IW3) fails a stale key; the same lock fails the build when a
// table / schema / env / hosting name reaches rendered copy or a table field is rendered without this function.
const SOURCE_LABEL: Record<string, string> = {
  raw_sales: 'monthly sales upload',
  daily_sales_feed: 'daily sales feed',
  raw_mi: 'MI & ATU report',
  raw_comp_report: 'Comprehensive comp report',
  raw_payment_detail: 'Commission payment detail',
  raw_dlar_rep: 'Rep KPI report',
  raw_dlar_store: 'Store KPI report',
  raw_catalog: 'Product catalog',
  raw_categories: 'Payment categories',
  raw_ma_commission: 'Marketplace commission details',
  raw_ma_daily_tx: 'Marketplace daily transactions',
  raw_ma_fulfillment: 'Marketplace handset fulfillment',
  raw_ma_sim_assignment: 'Activation SIM assignment report',
  raw_ma_pr_activation: 'PR activation details',
  raw_custom_import: 'custom report upload',
  raw_epay_daily_tx: 'bill-payment settlement report',
  raw_vendor_rebate: 'vendor rebate history',
  raw_sales_product: 'sales by product',
  raw_sales_invoice: 'sales by invoice',
  raw_sales_invoice_tender: 'sales by invoice (tenders)',
  carrier_commission: 'carrier commission statement',
  rep_commissions: 'calculated rep commissions',
  commission_ledger: 'Commission Ledger',
  pos_tender_summary: 'X-report (tenders)',
  inventory_value: 'inventory aging report',
  daily_closing: 'daily closing sheet',
  // The cash-accountability trail (§CORE-17 drill-downs, owner ask 2026-10-24). A manager reads
  // "the management envelope count", never a table name.
  envelope_count: 'management envelope count',
  cash_pickup: 'cash pickup envelope',
  billpay_pickup: 'bill-payment pickup envelope',
  closing_attempt: 'closing submit attempt',
  daily_closing_verification: "the district manager's verification",
  // The accountability trail (§CORE-17.3f): a chargeback is the penalty recorded against a person
  // for a missed verification, and a reader sees that, not the table it sits in.
  ops_chargeback: 'recorded chargeback',
  // The day-recon trail (§CORE-17.3c). A manager reads "the bank deposit slip", never a table.
  bank_deposit: 'bank deposit slip',
  merchant_settlement_day: 'card processor settlement',
  store_mapping: 'Store Mapping',
  payout_config: 'payout rates',
  // The clock-in/clock-out package (mig 1073, index §14z). The variance board names the feeds it
  // could not read, and a manager reads "the clock in / clock out report", never a table.
  raw_clock_punch: 'clock in / clock out report',
  timelog: 'our own time clock',
  manual_hours: 'hand-entered hours',
  shifts: 'the schedule',
  clock_variance_correction: 'recorded clock corrections',
  clock_feed_login_link: 'approved clock-report logins',
  // What the carrier OWES against what it PAID (§CORE-17.3s). A reader sees what each record is for
  // — an appeal, a ladder, an approval — never the table it sits in.
  appeal_recovery: 'denied appeal recovery ledger',
  // Worded WITHOUT the phrase "multi-month" on purpose. `harness_multimonth_offer_lock.py`
  // treats that phrase in any frontend file as a surface OFFERING multi-month pay, and this
  // file is the shared map every drill-down batch adds to — so excusing the whole file by name
  // would outlive the reason for it and silence the lock for a later entry that really does
  // offer the option. "The installment pay ladder" is the plain name either way.
  plan_installment_schedule: 'the installment pay ladder',
  installment_promote: 'approved expected-to-earned moves',
  // The finance statements and the liabilities composition (§CORE-17.3j). An owner reads "the
  // computed financial statements", never the table the snapshot sits in.
  account_statements: 'computed financial statements',
  journal_entries: 'manual journal entries',
  asset_ledger: 'device asset landing ledger',
  vip_invoices: 'distributor invoices',
  vip_paygo_payments: 'distributor device-financing payments',
  store_borrowings: 'inter-store borrowing record',
  // The device-asset family (§CORE-17.3l). An operator reads "the register's inventory count", never
  // the table, and never the POS vendor's name — the tenant's own POS term comes from
  // `lib/report-labels.ts` in copy, so the plain name here stays product-neutral (RULE TWO).
  b2b_inventory: 'point-of-sale inventory count',
  store_borrowing_payments: 'inter-store borrowing paybacks',
  inventory_aging_device: 'inventory aging (per device)',
  employees: 'employee records',
  payroll_settings: 'payroll withholding setup',
  store_lease: 'store lease & insurance setup',
  // The targets family (§CORE-17.3m). A TARGET is a number somebody typed into a settings screen,
  // so its plain name is the screen a manager sets it on — never the row it lands in. The declared
  // half of a store's daily counts is the same fact one table along (mig 1072).
  targets: 'monthly target settings',
  target_line: 'target settings (declared category)',
  closing_metric_line: 'declared daily counts',
  stores: 'store setup',
  // The subsidy-entitlement report (§CORE-17.3v). A store's first entitled month, how many months
  // the term runs and what one month is worth are numbers somebody typed into company settings, so
  // the plain name is the screen they were entered on — never the row they land in.
  commission_org_config: 'company commission settings',
  // The commission-disagreement trail (§CORE-17.3k). A manager reads "the pay discrepancy run" —
  // the stored result of pressing Run Detection — never the table it lands in.
  discrepancy_results: 'the pay discrepancy run',
  // The accessory money family (§CORE-17.3o). A RATE and a THRESHOLD are numbers somebody typed
  // into a settings screen, so their plain name is the setting a manager edits — never the row it
  // lands in. `raw_catalog` ("Product catalog") already had its name above, and the accessory
  // target's own settings row is `targets`, named with the targets family.
  commission_rule: 'commission plan rules',
  // The sales-and-margin family (§CORE-17.3r). The month's expenses are a sheet somebody types
  // into, so the plain name is the sheet a manager edits — never the row it lands in. The schedule
  // ('the schedule') and the store roster ('store setup') already have their names above.
  store_expenses: 'the expenses sheet',
  flag_rules: 'accessory flag thresholds',
}

/** The plain name of a data source. An unknown table still never prints as an identifier: `raw_` is dropped and
 *  the underscores become spaces ("raw_ma_new_feed" → "Ma new feed"). Empty in, empty out. */
export function sourceLabel(table: string | null | undefined): string {
  if (!table) return ''
  const t = String(table).replace(/^[a-z_]+\./, '')
  if (SOURCE_LABEL[t]) return SOURCE_LABEL[t]
  const words = t.replace(/^raw_/, '').replace(/_/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : ''
}
