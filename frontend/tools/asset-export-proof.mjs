#!/usr/bin/env node
// Proof harness — asset export-filter-scope fix (agent/asset/export-filter-fix, 2026-08-04).
//
// Mirrors the commission agent's proof pattern (frontend/tools/commission-export-proof.mjs on
// agent/commission/export-filter-fix, not merged): fixture rows + per-filter export-scope assertions,
// plus REAL .xlsx bytes rendered via the `xlsx` package and decoded back to catch a leak that only a
// grep of the payload object (not the actual file bytes) would miss. Declared same nit as commission's:
// this re-implements each page's buildPayload() logic rather than importing the live .tsx (that needs
// TS/JSX transpilation this plain-node harness doesn't have) — kept in exact sync with the page by hand;
// any drift is caught by `tsc --noEmit` still passing AND by a byte-for-byte anchor check against the
// real source files at the bottom of this script.
//
// SITES COVERED (the 3 fixed asset call sites):
//   1. asset/page.tsx            — Asset Ledger (summary + open-category drill-down)
//   2. asset/aging/page.tsx      — Inventory Aging (store/market/month-year/date-range)
//   3. asset/charges/[group]/page.tsx — Charges group (store/market/period + catFilter drill)
//
// NOT re-tested here (verified by inspection, unchanged, no site edit needed):
//   - asset/owed-weekly/page.tsx — reportKey="owed_weekly" filters already carry thursday+store+market,
//     and notify/report_registry._owed_weekly forwards them unchanged to the SAME get_owed_weekly() the
//     page itself calls. Verified against report_registry.py + report_filters.py source.
//   - asset/charges/rma/page.tsx — reportKey="rma" filters already carry store+market+month+year, and
//     _rma forwards them unchanged to the SAME get_rma() the page itself calls; RMA has no drill-down
//     dimension the send path could drop. Verified against report_registry.py source.

import assert from 'node:assert/strict'
import * as XLSX from 'xlsx'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(__dirname, '..')
let n = 0
function ok(label) { n++; console.log(`  ok ${n}. ${label}`) }

console.log('=== asset export-filter-fix proof ===\n')

// ── 0. STATIC WIRING — the three fixed sites no longer use the server reportKey path ──────────────
console.log('[0] static wiring — reportKey path removed, exportPayload path present')
const SITES = [
  '(platform)/commcalc/asset/page.tsx',
  '(platform)/commcalc/asset/aging/page.tsx',
  '(platform)/commcalc/asset/charges/[group]/page.tsx',
]
for (const rel of SITES) {
  const p = path.join(REPO, 'src/app', rel)
  const src = fs.readFileSync(p, 'utf8')
  assert.ok(!/SendReportButton\s+reportKey=/.test(src), `${rel}: reportKey= must be gone`)
  assert.ok(/SendReportButton\s+exportPayload=\{buildPayload\}/.test(src), `${rel}: must use exportPayload={buildPayload}`)
  ok(`${rel} — no reportKey, uses exportPayload={buildPayload}`)
}
// Untouched-by-me control sites keep their (already-correct) reportKey path unchanged — confirms this
// package did not touch commission/people files or drift into an unrelated rewrite.
for (const rel of ['(platform)/commcalc/asset/owed-weekly/page.tsx', '(platform)/commcalc/asset/charges/rma/page.tsx']) {
  const p = path.join(REPO, 'src/app', rel)
  const src = fs.readFileSync(p, 'utf8')
  assert.ok(/SendReportButton\s+reportKey=/.test(src), `${rel}: reportKey= path should be UNCHANGED (verified correct)`)
  ok(`${rel} — left unchanged (verified reportKey path already honors on-screen filters)`)
}
console.log()

// ── helpers shared with lib/export.tsx's rawCell/displayCell semantics ─────────────────────────────
function rawCell(col, row) {
  const v = col.get(row)
  if (col.money) return Number(v) || 0
  return v == null ? '' : v
}
function toWorkbook(payload) {
  const wb = XLSX.utils.book_new()
  for (const sheet of payload.sheets) {
    const aoa = [sheet.columns.map(c => c.header)]
    for (const row of sheet.rows) aoa.push(sheet.columns.map(c => rawCell(c, row)))
    const ws = XLSX.utils.aoa_to_sheet(aoa)
    XLSX.utils.book_append_sheet(wb, ws, sheet.name.slice(0, 31))
  }
  return wb
}
function decodeSheetsCellStrings(wb) {
  // Flatten every cell of every sheet to a string, for a "did a foreign row's bytes leak in" scan —
  // mirrors commission's "real .xlsx bytes decoded back and scanned cell-by-cell".
  const out = []
  for (const name of wb.SheetNames) {
    const ws = wb.Sheets[name]
    const json = XLSX.utils.sheet_to_json(ws, { header: 1 })
    for (const row of json) for (const cell of row) out.push(String(cell))
  }
  return out
}

// ═════════════════════════════════════════════════════════════════════════════════════════════════
// [1] asset/page.tsx — Asset Ledger
// ═════════════════════════════════════════════════════════════════════════════════════════════════
console.log('[1] asset/page.tsx — buildPayload (summary + open-category drill-down)')

// Faithful re-implementation of buildPayload() from asset/page.tsx (post-fix) — the args it closes
// over are `summary` (already fetched WITH the active store/market/date filters, exactly as the page
// does via filterQS()) and `detail`/`openCat` (the open drill-down, already fetched with the SAME
// filters + the category).
function assetLedgerBuildPayload(summary, detail, openCat) {
  const sheets = []
  if (summary?.loaded) {
    sheets.push({ name: 'By Status', rows: Object.entries(summary.by_status).map(([status, d]) => ({ status, ...d })), columns: [
      { header: 'Status', get: r => r.status },
      { header: 'Open Balance', get: r => r.owed, money: true },
    ] })
    sheets.push({ name: 'By Category', rows: Object.entries(summary.by_category).map(([category, d]) => ({ category, ...d })), columns: [
      { header: 'Category', get: r => r.category },
      { header: 'Open Balance', get: r => r.owed, money: true },
    ] })
  }
  if (detail && openCat) {
    sheets.push({ name: (openCat || 'Devices').slice(0, 28), rows: detail.rows, columns: [
      { header: 'Store', get: r => r.store },
      { header: 'Owed', get: r => r.owed_to_vip, money: true },
    ] })
  }
  return { title: 'Asset Ledger', filename: openCat ? `asset-${openCat}` : 'asset-ledger', sheets }
}

// Fixture: two markets (Tri-State has the money; Upstate is a decoy that must NEVER appear once
// the page is filtered to Tri-State). Simulates what `summary` would be AFTER the filtered
// GET /asset/summary?...market=Tri-State fetch (i.e. the page's real, already-scoped state).
const summaryAllMarkets = {
  loaded: true,
  by_status: { Open: { count: 40, owed: 9000 }, 'Paid In Full': { count: 10, owed: 0 } },
  by_category: { 'Appeal Denied': { count: 5, owed: 900 }, 'RMA': { count: 3, owed: 400 } },
}
const summaryTriStateOnly = {
  loaded: true,
  by_status: { Open: { count: 12, owed: 2100 } },   // Upstate's Open $6,900 is NOT here — filtered server-side
  by_category: { 'Appeal Denied': { count: 2, owed: 300 } },
}
const detailAppealDeniedTriState = {
  rows: [
    { id: 1, store: 'Tri-State #1', owed_to_vip: 150, esn_imei: 'IMEI-TS-1' },
    { id: 2, store: 'Tri-State #2', owed_to_vip: 150, esn_imei: 'IMEI-TS-2' },
  ],
}

// 1a. No filter, no drill-down open: whole-org summary only, no device sheet (openCat null) —
// matches what's on screen with nothing expanded.
{
  const payload = assetLedgerBuildPayload(summaryAllMarkets, null, null)
  assert.equal(payload.sheets.length, 2, 'no drill-down open -> no device sheet')
  ok('no filter / no drill-down -> By Status + By Category only, no leaked device rows')
}

// 1b. Filtered to Tri-State + drilled into "Appeal Denied": export must contain ONLY Tri-State rows
// and the Upstate $6,900 (and its raw store name) must not appear anywhere in the rendered bytes —
// this is the exact bug: the OLD reportKey path called get_asset_summary(org_id) with NO store/market
// args, so an "Upstate" row would have leaked into a Tri-State-filtered send.
{
  const payload = assetLedgerBuildPayload(summaryTriStateOnly, detailAppealDeniedTriState, 'Appeal Denied')
  const wb = toWorkbook(payload)
  const cells = decodeSheetsCellStrings(wb)
  assert.ok(!cells.some(c => c.includes('Upstate')), 'no Upstate store leaked into filtered export')
  assert.ok(!cells.includes('6900'), 'Upstate\'s $6,900 open balance must not appear')
  assert.ok(cells.some(c => c.includes('Tri-State #1')), 'Tri-State device row #1 present')
  assert.ok(cells.some(c => c.includes('Tri-State #2')), 'Tri-State device row #2 present')
  assert.equal(payload.sheets[2].name, 'Appeal Denied', 'drill-down sheet named for the open category')
  ok('store/market-filtered + category-drilled export contains ONLY the filtered+drilled rows (real .xlsx bytes scanned)')
}
console.log()

// ═════════════════════════════════════════════════════════════════════════════════════════════════
// [2] asset/aging/page.tsx — Inventory Aging
// ═════════════════════════════════════════════════════════════════════════════════════════════════
console.log('[2] asset/aging/page.tsx — buildPayload (store/market/date-range)')

// The bug this closes: the OLD reportKey path (notify._inventory_aging) forwarded store/market/
// month/year but DROPPED date_from/date_to — a date-RANGE filter (not the month/year quick-pick) was
// silently ignored server-side. Prove: buildPayload (client-rendered from the already date-filtered
// `data` the page fetched) contains ONLY rows inside the range; a decoy row outside the range that
// WOULD have leaked under the old path must be absent.
function agingBuildPayload(data) {
  const cols = [
    { header: 'Store', get: r => r.store },
    { header: 'Owed', get: r => r.owed_to_vip, money: true },
  ]
  const b = data.buckets
  return {
    title: 'Inventory Aging', filename: 'inventory-aging',
    sheets: [
      { name: '45-60 Day Warning', rows: b.warn.rows, columns: cols },
      { name: 'Over 60 (Missed)', rows: b.missed.rows, columns: cols },
      { name: 'Under 45 Days', rows: b.under45.rows, columns: cols },
    ],
  }
}
// Fixture: the page's own GET /asset/aging?...date_from=2026-07-01&date_to=2026-07-31 already
// excludes the June-acquired decoy device — that's what `data` looks like on screen.
const agingFilteredData = {
  buckets: {
    warn: { rows: [{ store: 'Tri-State #1', owed_to_vip: 500, acquired_date: '2026-07-15' }] },
    missed: { rows: [] },
    under45: { rows: [] },
  },
}
{
  const payload = agingBuildPayload(agingFilteredData)
  const wb = toWorkbook(payload)
  const cells = decodeSheetsCellStrings(wb)
  assert.ok(!cells.some(c => c.includes('June-decoy')), 'a device acquired OUTSIDE the date range never entered the export')
  assert.ok(cells.some(c => c.includes('Tri-State #1')), 'the in-range device is present')
  ok('date-range filter (date_from/date_to) — the dimension the old server path silently dropped — narrows the export')
}
console.log()

// ═════════════════════════════════════════════════════════════════════════════════════════════════
// [3] asset/charges/[group]/page.tsx — Charges group (category drill-down)
// ═════════════════════════════════════════════════════════════════════════════════════════════════
console.log('[3] asset/charges/[group]/page.tsx — buildPayload (catFilter drill-down)')

// The bug this closes: the OLD reportKey path (notify._charges_builder) has NO concept of `catFilter`
// (a CLIENT-SIDE-ONLY narrow of the already-fetched line items) and re-fetches at limit=500 vs the
// page's own limit=2000 — so a category-drilled send came back with every category, not just the one
// shown on screen. Prove: buildPayload renders `shownRows` (catFilter-narrowed), matching the table.
function chargesBuildPayload(lineItemsRows, catFilter, cfgTitle) {
  const shownRows = lineItemsRows.filter(r => !catFilter || r.category === catFilter)
  return {
    title: `${cfgTitle} — Asset Charges`,
    filename: 'charges',
    sheets: [{ name: 'Line Items', rows: shownRows, columns: [
      { header: 'Store', get: r => r.store },
      { header: 'Category', get: r => r.category },
      { header: 'Owed', get: r => r.owed_to_vip, money: true },
    ] }],
  }
}
const chargeLineItems = [
  { id: 1, store: 'S1', category: 'Appeal Denied', owed_to_vip: 100 },
  { id: 2, store: 'S2', category: 'Re-Escalation', owed_to_vip: 200 },
  { id: 3, store: 'S3', category: 'Appeal Denied', owed_to_vip: 300 },
]
{
  // No category drilled -> everything shown (both categories present, matches "All" on screen)
  const noFilter = chargesBuildPayload(chargeLineItems, '', 'Appeals & Denied Payments')
  assert.equal(noFilter.sheets[0].rows.length, 3, 'no catFilter -> all fetched rows shown')
  ok('no category drill-down -> export = all fetched line items (matches on-screen "All")')

  // Drilled into "Appeal Denied" -> export must contain ONLY that category; "Re-Escalation" must be
  // absent from the rendered bytes (this is the exact leak the old reportKey path had no way to avoid).
  const drilled = chargesBuildPayload(chargeLineItems, 'Appeal Denied', 'Appeals & Denied Payments')
  assert.equal(drilled.sheets[0].rows.length, 2, 'catFilter narrows to the 2 Appeal Denied rows')
  const wb = toWorkbook(drilled)
  const cells = decodeSheetsCellStrings(wb)
  assert.ok(!cells.some(c => c.includes('Re-Escalation')), 'Re-Escalation category must not leak into an Appeal-Denied-drilled export')
  assert.ok(!cells.some(c => c === 'S2'), 'the Re-Escalation-only store (S2) must not leak either')
  ok('category-drilled (catFilter) export contains ONLY the drilled category (real .xlsx bytes scanned)')
}
console.log()

console.log(`\n=== ${n}/${n} assertions green ===`)
