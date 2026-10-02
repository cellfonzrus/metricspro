// A DATA SOURCE'S PLAIN NAME — the one frontend home (owner 2026-10-02, index §19.37: "hide database names from
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
  store_mapping: 'Store Mapping',
  payout_config: 'payout rates',
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
