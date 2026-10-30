// THE SENDABLE ZERO SALES REPORT — index §CORE-27.
//
// OWNER ASK (2026-10-30, verbatim): *"Zero Sales does not have send notification bar, please note
// this also in teh indsex all reports must have the satandard filters and the send bar by default"*.
//
// WHAT WAS ACTUALLY MISSING, AND WHAT WAS NOT. Sending has ONE home already
// (`lib/send-report.SendReportButton`, reached through `<ReportExportBar>`) and the standard filters
// have ONE home already (`components/StandardFilterBar`), which this report was already using. The
// only gap on this screen was CONTENT: the button can send an `ExportPayload`, and nothing turned
// this report's five-state grid into one. So this file is a converter, not a second sender — the
// same posture as `lib/tile-snapshot`, whose sheet it reuses rather than restating.
//
// THE ONE RULE THIS FILE EXISTS TO CARRY OFF THE SCREEN. The page's banner says it: *a missing feed
// is not a zero*. On screen a day nothing landed for is an amber square whose tooltip says "not
// reported". A spreadsheet has no colour and no tooltip, so the rule has to live in the CELL:
//
//   · a MEASURED day exports its transaction count, including a real 0;
//   · a day nobody measured exports BLANK — never 0, because a 0 in a mailed file is read as a
//     counted fact and there is no banner beside it to say otherwise;
//   · every row states its state IN WORDS, so a reader knows which kind of blank they are looking at;
//   · the measured-zero and not-reported day counts are SEPARATE columns and are never added.
//
// RULE TWO: no carrier, tenant, market or store name appears here. The alert threshold, the state
// words and the day columns are all read from the payload, so every tenant mails its own figures
// through this one converter.
//
// PURE and DB-free: no React, no fetch, no clock. Unit tests: `src/lib/__tests__/zero-sales-export.test.ts`.
// Wiring (the page dereferences this, renders the shared bar, and no second copy of the state words
// exists) is locked by `backend/harness_report_toolbar_coverage.py`.
import type { ExportColumn, ExportSheet } from '@/lib/export'
import { tileSnapshotSheet, type SnapshotTile } from '@/lib/tile-snapshot'

/** ONE HOME for the word each of this report's five states is called by. The page renders these
 *  same words beside its colours and the export prints them; a second copy is how the file and the
 *  screen come to disagree about what a square meant. */
export const ZS_STATE_WORD: Record<string, string> = {
  had_sales: 'had sales',
  measured_zero: 'measured zero',
  not_reported: 'not reported',
  closed: 'closed',
  in_progress: 'in progress',
  off: 'not scheduled',
  rule_refused: 'rule refused',
}
export const zsStateWord = (state: string): string => ZS_STATE_WORD[state] || ZS_STATE_WORD.closed

export const ZS_STORE_SHEET = 'Stores'
export const ZS_REP_SHEET = 'Reps'

/** Only the states in which a transaction count is a MEASURED fact. Every other state's count is an
 *  absence, and an absence is exported blank. */
const MEASURED = new Set(['had_sales', 'measured_zero'])

type ZsRow = {
  grain: 'store' | 'rep'
  scope_key: string | string[]
  label: string
  parent: string | null
  state: string
  day_states: Record<string, string>
  day_counts: Record<string, number | null>
  zero_days: number
  days_measured_zero: number
  days_not_reported: number
  trading_calendar: string
  alerting: boolean
  note: string
}

type ZsPayload = {
  days: string[]
  rows: ZsRow[]
  totals: {
    scopes: number; store_days_measured_zero: number; store_days_not_reported: number
    stores_alerting: number; stores_unknown_calendar: number
  }
  config: Record<string, unknown>
  rules_refused: boolean
  market_of: Record<string, string>
  date_from: string
  date_to: string
}

/** PURE: one column per day in the window. The HEADER is the date, so the file needs no legend to
 *  be read in date order, and the cell obeys the measured/absent rule above. */
function dayColumns(days: string[]): ExportColumn[] {
  return days.map(d => ({
    header: d,
    align: 'right' as const,
    agg: 'none' as const,   // a transaction count per day does not add across days into anything
    get: (r: ZsRow) => {
      if (!MEASURED.has(r.day_states?.[d])) return ''
      const n = r.day_counts?.[d]
      return n == null ? '' : n
    },
  }))
}

/** PURE: the four summary tiles the screen shows, as the shared snapshot row.
 *
 *  EVERY tile declares its change UNMEASURED. This report compares no two periods — it reads one
 *  window — so there is no base to divide by, and a mailed `± 0%` would read as "this held steady"
 *  about a figure nothing was compared with (the §COMMCALC-19 defect, in its general form). The
 *  honest cell says `not measured` and the note says what the figure is. */
export function zeroSalesTiles(p: ZsPayload): SnapshotTile[] {
  if (p.rules_refused) return []
  const t = p.totals
  const window = `${p.date_from} → ${p.date_to}`
  const threshold = String((p.config || {}).consecutive_days ?? '—')
  return [
    { label: 'Stores at or past the alert threshold', value: t.stores_alerting, measured: false,
      note: `${threshold} or more consecutive trading days with no activation and no upgrade, ${window}` },
    { label: 'Store-days measured zero', value: t.store_days_measured_zero, measured: false,
      note: `the feed carried the store and it sold no activation or upgrade, ${window}` },
    { label: 'Store-days not reported', value: t.store_days_not_reported, measured: false,
      note: `no feed landed — never counted as a zero, ${window}` },
    { label: 'Stores with an unknown trading calendar', value: t.stores_unknown_calendar, measured: false,
      note: `no schedule in this window, so every day was evaluated, ${window}` },
  ]
}

/** PURE: the whole report as the sheets a send carries — the tile row first, because that is what a
 *  manager reads, then one sheet per grain. The two grains are kept APART rather than nested: they
 *  are the same days one grain finer, and a reader adding a store row to its own rep rows would
 *  count every day twice (the same reason the drill-down panels show rep rows without counting them). */
export function zeroSalesSheets(p: ZsPayload): ExportSheet[] {
  const days = p.days || []
  const rows = p.rows || []
  const market = (r: ZsRow) => p.market_of?.[String(r.scope_key)] || ''
  // A run of consecutive zero days is a CLAIM about consecutiveness. Zero of them is not "0 days in
  // a row" — it is "no run to report" — and the screen prints an em dash for exactly that reason.
  const run = (r: ZsRow) => (r.zero_days ? r.zero_days : '')

  const storeCols: ExportColumn[] = [
    { header: 'Store', get: (r: ZsRow) => r.label, role: 'store' },
    { header: 'Market', get: market, role: 'store' },
    { header: 'State', get: (r: ZsRow) => zsStateWord(r.state) },
    { header: 'Alerting', get: (r: ZsRow) => (r.alerting ? 'yes' : 'no') },
    { header: 'Consecutive zero days', get: run, align: 'right', type: 'number', agg: 'none' },
    { header: 'Measured zero days', get: (r: ZsRow) => r.days_measured_zero, align: 'right', type: 'number' },
    { header: 'Not reported days', get: (r: ZsRow) => r.days_not_reported, align: 'right', type: 'number' },
    { header: 'Trading calendar', get: (r: ZsRow) => r.trading_calendar },
    { header: 'Note', get: (r: ZsRow) => r.note || '' },
    ...dayColumns(days),
  ]
  const repCols: ExportColumn[] = [
    { header: 'Rep', get: (r: ZsRow) => r.label, role: 'rep' },
    { header: 'Store', get: (r: ZsRow) => r.parent || '', role: 'store' },
    { header: 'State', get: (r: ZsRow) => zsStateWord(r.state) },
    { header: 'Consecutive zero days', get: run, align: 'right', type: 'number', agg: 'none' },
    { header: 'Measured zero days', get: (r: ZsRow) => r.days_measured_zero, align: 'right', type: 'number' },
    { header: 'Not reported days', get: (r: ZsRow) => r.days_not_reported, align: 'right', type: 'number' },
    { header: 'Note', get: (r: ZsRow) => r.note || '' },
    ...dayColumns(days),
  ]
  return [
    tileSnapshotSheet(zeroSalesTiles(p)),
    { name: ZS_STORE_SHEET, columns: storeCols, rows: rows.filter(r => r.grain === 'store') },
    { name: ZS_REP_SHEET, columns: repCols, rows: rows.filter(r => r.grain === 'rep') },
  ]
}
