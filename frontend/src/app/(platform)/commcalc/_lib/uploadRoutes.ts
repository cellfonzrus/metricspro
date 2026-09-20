// UPLOAD ROUTE METADATA — how a file is POSTED for a given upload route key, and what that write
// does to stored data. ONE home for the Upload page and the Upload wizard (2026-09-20).
//
// This is NOT a list of which reports a tenant is offered. WHICH routes are shown is decided by the
// report-kind registry (GET /commcalc/report-kinds → lib/report-kinds.ts → the one visibility
// function): a route here renders only when a VISIBLE registry row names it in `upload_types`. A
// row nobody's registry names is never rendered, however much metadata it has here. Which carrier
// or POS a route belongs to is therefore registry DATA, never a tag in this file (RULE TWO) — the
// `carrier?:` tags the Upload page used to carry are gone, and no vendor is named.
//
// `mode` MIRRORS the backend's real write semantics in backend/app/modules/commcalc/router.py
// (`_upload_file_impl`) — keep it in sync if the backend's keying changes:
//   • DATE_KEYED = {daily_sales, ma_commission, ma_daily_tx, ma_fulfillment} → delete-then-insert PER DAY
//     ⇒ 'additive_daily': new days add, a re-upload of the same day refreshes only that day.
//   • x_report (upsert on org+close_date+store+tender_type) / inventory_aging (per-store snapshot upsert)
//     ⇒ 'additive_keyed': nothing outside the file's own keys is ever cleared.
//   • has_period types (sales, payment_detail, mi_report, dlar_rep, dlar_store, comp_report) → the
//     SELECTED period is deleted then re-inserted ⇒ 'replace_period'.
//   • catalog / master_cats (has_period === false) → the whole table is wiped ⇒ 'replace_all'.
export type UploadMode = 'additive_daily' | 'additive_keyed' | 'replace_period' | 'replace_all'
export const MODE_UI: Record<UploadMode, { verb: string; explain: string }> = {
  additive_daily: { verb: '⬆️ Upload additional file',
    explain: 'Adds the days in the file. Re-uploading the same day is safe — it refreshes only that day; other days stay.' },
  additive_keyed: { verb: '⬆️ Upload additional file',
    explain: 'Adds or refreshes only what this file covers — nothing outside it is cleared.' },
  replace_period: { verb: '📂 Replace period file',
    explain: 'Clears & replaces everything stored for the selected period.' },
  replace_all: { verb: '📂 Replace all data',
    explain: 'Replaces ALL stored rows for this report — not just one period.' },
}
export const modeVerb = (mode: UploadMode, prior: boolean) => (prior ? MODE_UI[mode].verb : '📂 Choose File')

/** A period / day-grain report posted to the generic /commcalc/upload/<file_type> capture. */
export type PeriodRoute = { id: string; label: string; icon: string; required: boolean; desc: string; mode: UploadMode }
export const PERIOD_ROUTES: Record<string, PeriodRoute> = {
  sales:          { id: 'sales',          label: 'Sales transactions',       icon: '🛍️', required: true,  mode: 'replace_period', desc: 'POS sales transaction details (all columns)' },
  daily_sales:    { id: 'daily_sales',    label: 'Daily sales upload',       icon: '📅', required: false, mode: 'additive_daily', desc: 'Append daily transactions — no period wipe, deduped by transaction id' },
  payment_detail: { id: 'payment_detail', label: 'Payment detail',           icon: '💳', required: true,  mode: 'replace_period', desc: 'Payment processor commission payment detail' },
  dlar_rep:       { id: 'dlar_rep',       label: 'Metrics — rep report',     icon: '📊', required: true,  mode: 'replace_period', desc: 'Rep KPI report (per-carrier portal)' },
  dlar_store:     { id: 'dlar_store',     label: 'Metrics — store report',   icon: '🏪', required: false, mode: 'replace_period', desc: 'Store-level KPI data (per-carrier portal)' },
  mi_report:      { id: 'mi_report',      label: 'MI & ATU report',          icon: '💰', required: false, mode: 'replace_period', desc: 'Monthly incentive + ATU payout' },
  catalog:        { id: 'catalog',        label: 'Product catalog',          icon: '📱', required: false, mode: 'replace_all',    desc: 'Product catalog + cost/category — the POS "Product Update" (product-id) or the UPC "Product Catalog Update" variant' },
  master_cats:    { id: 'master_cats',    label: 'Payment categories',       icon: '🗂️', required: false, mode: 'replace_all',    desc: 'Payment type → category mapping' },
  comp_report:    { id: 'comp_report',    label: 'Comprehensive comp report', icon: '🏦', required: false, mode: 'replace_period', desc: 'Carrier store-level rebates & MDF' },
  inventory_aging: { id: 'inventory_aging', label: 'Inventory aging (POS)',  icon: '📦', required: false, mode: 'additive_keyed', desc: 'POS inventory aging — per-store value snapshot' },
  x_report:       { id: 'x_report',       label: 'X-report (POS tenders)',   icon: '🧾', required: false, mode: 'additive_keyed', desc: 'POS daily tenders by type — reconciles vs the daily closing sheet' },
  // Carrier marketplace portal exports (mig 083). Date-grain: the period derives per ROW, so no
  // period selection; re-uploads are day-idempotent.
  ma_commission:  { id: 'ma_commission',  label: 'Marketplace commission details', icon: '🧾', required: false, mode: 'additive_daily', desc: 'Per-activation commission detail — spiffs M1–M6, rebates, MRC net discount' },
  ma_daily_tx:    { id: 'ma_daily_tx',    label: 'Marketplace daily transactions', icon: '📆', required: false, mode: 'additive_daily', desc: 'Daily airtime/top-up transactions — merchant discount = your margin' },
  ma_fulfillment: { id: 'ma_fulfillment', label: 'Marketplace handset fulfillment', icon: '🚚', required: false, mode: 'additive_daily', desc: 'Marketplace handset fulfillment orders' },
}
export const PERIODLESS = new Set(['catalog', 'master_cats', 'inventory_aging', 'x_report', 'ma_commission', 'ma_daily_tx', 'ma_fulfillment'])

/** A file that loads into another module through its own endpoint (not the generic capture).
 *  `traceKeys` = the upload_type/file_type these endpoints record their ingest under (NOT always the
 *  route id); `tracked: false` = the endpoint writes no ingest journal, so no last-upload line is shown. */
export type ModuleRoute = { id: string; label: string; icon: string; endpoint: string; needsDate: boolean; desc: string
                            mode: UploadMode; traceKeys?: string[]; tracked?: boolean }
export const MODULE_ROUTES: Record<string, ModuleRoute> = {
  hotsheet:      { id: 'hotsheet',      label: 'Pricing hotsheet',      icon: '🏷️', endpoint: 'commcalc/hotsheet/upload', needsDate: true,
    mode: 'additive_keyed', traceKeys: ['hotsheet'],
    desc: 'Carrier promo pricing by device — powers the hotsheet expected-vs-paid recon. Pick the effective date.' },
  vip_workbook:  { id: 'vip_workbook',  label: 'Distributor workbook',  icon: '🧾', endpoint: 'commcalc/vip/upload', needsDate: false,
    mode: 'replace_all', traceKeys: ['vip_workbook', 'vip_invoices'],
    desc: 'Distributor scraper workbook (invoices / lines / devices sheets). Full replace of distributor history.' },
  asset_ledger:  { id: 'asset_ledger',  label: 'Asset ledger',          icon: '📒', endpoint: 'asset/upload', needsDate: false,
    mode: 'replace_all', tracked: false,
    desc: 'Asset-lending workbook — wipes & re-inserts all asset rows, then backfills market + flags.' },
  daily_closing: { id: 'daily_closing', label: 'Daily closing sheet',   icon: '🧮', endpoint: 'closing/upload', needsDate: false,
    mode: 'additive_daily', tracked: false,
    desc: '"Envelopes Data" export — one row per rep per day; idempotent per day.' },
}

/** A structured (non-file) upload that lives on its own page — linked, not inlined. */
export type LinkRoute = { id: string; label: string; icon: string; href: string; desc: string }
export const LINK_ROUTES: Record<string, LinkRoute> = {
  b2b_inventory: { id: 'b2b_inventory', label: 'Inventory recon', icon: '📦', href: '/commcalc/asset/inventory-recon',
    desc: 'On-hand inventory by store & category — structured entry/recon, not a single file. Opens its page.' },
}

/** The wizard's curated per-route note (polish the connector registry does not carry). */
export const ROUTE_NOTES: Record<string, string> = {
  sales: 'Daily sales exports arrive automatically via the email subscription (→ daily feed + Sales Feed Recon). Set the "Sales" connector to auto (Connectors page) to also auto-build the monthly commission basis from that feed — then this manual upload is only a fallback.',
  comp_report: 'Posts in arrears — a month is often empty until the carrier publishes it. The sweep replaces the open month daily and freezes it at month-end.',
  hotsheet: 'Pick the date it became effective.',
  asset_ledger: 'Auto-swept with the distributor sweep. Manual upload still available.',
  daily_closing: 'Auto-imports via a Google service account once set up — configure on Daily Closing → Auto-Import. Manual upload still available.',
}
export const ROUTE_URL_OVERRIDE: Record<string, string> = { daily_closing: '/closing/imports' }

/** Every route key an ingest journal lookup should answer for. */
export const ALL_TRACE_KEYS = [
  ...Object.keys(PERIOD_ROUTES),
  ...Object.values(MODULE_ROUTES).flatMap(m => m.traceKeys || []),
]
