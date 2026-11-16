// WEEKLY PERFORMANCE REVIEW — what the exported file says (owner 2026-11-16).
//
// PURE. No React, no fetch, no formatting of the screen — just the sheets the one shared toolbar
// (`ReportExportBar` → `lib/export` → `/notify/send-file`) hands to Excel, PDF, Print and Send.
//
// THE RULE THIS FILE EXISTS FOR, borrowed verbatim from Zero Sales (`lib/zero-sales-export.ts`):
// a spreadsheet has no colour, no tooltip and no banner. An item nobody measured must therefore
// export BLANK with its state in words — never 0, which in a cell reads as "we checked and it is
// zero". That distinction is the whole point of a review pack: a district manager acts on it.
import type { ExportSheet } from '@/lib/export'

/** A row behind an item, in the shape the server serves for THAT item. Every value a row carries is
 *  a figure, a word or an absence — never a nested object — so one row type serves all seven items
 *  and the page can render any of them without an `any`. */
export type ReviewRow = Record<string, string | number | boolean | null | undefined>
/** A detail block: counts, totals and (for profit) the losing stores. */
export type ReviewDetail = Record<string, string | number | null | undefined | ReviewRow[]>

export type ReviewItem = {
  key: string; label: string; unit: 'money' | 'count' | 'pct'; window: string
  state: 'measured' | 'not_measured'; figure: number | null; reason: string
  rows: ReviewRow[]; detail: ReviewDetail; source: string
}
export type Review = {
  scope: { kind: string; label: string; store_codes: string[]; note: string }
  week: { date_from: string; date_to: string; week_ending: string; label: string }
  items: ReviewItem[]
  coverage: { items: number; measured: number; not_measured: number }
}
export type ReviewPack = {
  as_of: string; period: string
  week: Review['week']
  company: Review | null; markets: Review[]; unplaced: Review | null
}

/** THE STATE IN WORDS — one home. The page and the file say the same thing about the same item. */
export const WR_STATE_WORD: Record<string, string> = {
  measured: 'measured',
  not_measured: 'not measured',
}

/** What a figure covers, in words a manager reads. A monthly figure on a weekly pack is honest only
 *  when the pack says it is monthly. */
export const WR_WINDOW_WORD: Record<string, string> = {
  week: 'this week',
  period: 'month to date',
  today: 'as of today',
}

/** Every review in the pack, consolidated first — the order the meeting works through. */
export function reviewsOf(pack: ReviewPack | null): Review[] {
  if (!pack) return []
  const out: Review[] = []
  if (pack.company) out.push(pack.company)
  out.push(...(pack.markets || []))
  if (pack.unplaced) out.push(pack.unplaced)
  return out
}

/** A figure for a CELL: blank when nobody measured it, never 0. */
export function cellFigure(item: ReviewItem): number | '' {
  return item.state === 'measured' && item.figure !== null ? item.figure : ''
}

/** A figure for the SCREEN: an em dash when nobody measured it, never 0. */
export function screenFigure(item: ReviewItem): string {
  if (item.state !== 'measured' || item.figure === null) return '—'
  if (item.unit === 'money') {
    return item.figure.toLocaleString(undefined, { style: 'currency', currency: 'USD' })
  }
  if (item.unit === 'pct') return `${item.figure > 0 ? '+' : ''}${item.figure.toFixed(1)}%`
  return item.figure.toLocaleString()
}

type SheetRow = Record<string, string | number>
const cols = (headers: string[]) =>
  headers.map(h => ({ header: h, field: h, get: (r: SheetRow) => r[h] }))

/** The sheets the toolbar exports: the review itself, then the rows behind the measured items. */
export function weeklyReviewSheets(pack: ReviewPack | null): ExportSheet[] {
  const reviews = reviewsOf(pack)
  if (!reviews.length) return []
  const summary = reviews.flatMap(r => r.items.map(i => ({
    Scope: r.scope.label,
    Item: i.label,
    Covers: WR_WINDOW_WORD[i.window] || i.window,
    Figure: cellFigure(i),
    Unit: i.unit,
    State: WR_STATE_WORD[i.state] || i.state,
    'Why not measured': i.state === 'measured' ? '' : i.reason,
    'Read from': i.source,
  })))
  const sheets: ExportSheet[] = [{
    name: 'Review',
    heading: `Weekly Performance Review · week ${pack?.week?.label || ''}`,
    columns: cols(['Scope', 'Item', 'Covers', 'Figure', 'Unit', 'State', 'Why not measured',
                   'Read from']),
    rows: summary,
  }]

  const stores = reviews.flatMap(r => {
    const profit = r.items.find(i => i.key === 'profit')
    if (!profit || profit.state !== 'measured') return []
    return (profit.rows || []).map((s: ReviewRow) => ({
      Scope: r.scope.label, Store: s.store_code, Address: s.address,
      Revenue: s.revenue, 'Net profit': s.net_profit,
    }))
  })
  if (stores.length) {
    sheets.push({ name: 'Profit by store', heading: 'Profit by store',
                  columns: cols(['Scope', 'Store', 'Address', 'Revenue', 'Net profit']),
                  rows: stores, pageBreakBefore: true })
  }

  const carriers = reviews.flatMap(r => {
    const aging = r.items.find(i => i.key === 'aging')
    if (!aging || aging.state !== 'measured' || r.scope.kind !== 'company') return []
    return (aging.rows || []).map((c: ReviewRow) => ({
      Carrier: c.label,
      'Sell within (days)': c.sell_within_days ?? '',
      'Window declared': c.declared ? 'yes' : 'no — the house default answered',
      Devices: c.devices, 'Past the window': c.missed, Owed: c.owed,
    }))
  })
  if (carriers.length) {
    sheets.push({ name: 'Inventory aging', heading: 'Inventory aging by carrier',
                  columns: cols(['Carrier', 'Sell within (days)', 'Window declared', 'Devices',
                                 'Past the window', 'Owed']),
                  rows: carriers, pageBreakBefore: true })
  }

  const reps = reviews.flatMap(r => {
    const comm = r.items.find(i => i.key === 'commission')
    if (!comm || comm.state !== 'measured' || r.scope.kind !== 'company') return []
    return (comm.rows || []).map((p: ReviewRow) => ({
      Rep: p.rep, Store: p.store_code, Payout: p.payout,
    }))
  })
  if (reps.length) {
    sheets.push({ name: 'Commission by rep', heading: 'Commission by rep',
                  columns: cols(['Rep', 'Store', 'Payout']), rows: reps, pageBreakBefore: true })
  }
  return sheets
}

/** A count or total off an item's detail block. A detail key nobody served reads 0 HERE and nowhere
 *  else: the caller uses it to decide whether to print a sentence at all, never as a figure. */
export function detailCount(detail: ReviewDetail | undefined, key: string): number {
  const v = detail?.[key]
  return typeof v === 'number' ? v : 0
}

/** A row's cell as text, for a table that renders whatever shape its item carries. */
export function rowText(v: ReviewRow[string]): string {
  return v === null || v === undefined ? '—' : String(v)
}

/** A row's cell as money, blank-safe: an absent figure is an em dash, never $0.00. */
export function rowMoney(v: ReviewRow[string]): string {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  return Number.isNaN(n) ? String(v)
    : n.toLocaleString(undefined, { style: 'currency', currency: 'USD' })
}
