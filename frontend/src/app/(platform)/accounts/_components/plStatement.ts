// THE single-month P&L read, from the browser's side, and the P&L's printable line layout (index §4 / §4d).
//
//   · `plQuery` is the ONE place the frontend spells `GET /account/pl/{period}` with a scope and a store /
//     market filter. The P&L page reads its month through it, and so does "Print each store" — one call
//     per store with ONLY that store in the filter, i.e. exactly what the page shows when that store alone
//     is selected. No second P&L derivation exists: every figure is the server's (`pl_single_month`).
//   · `plStatementRows` is the P&L's export layout (section lines, section subtotals, Gross Profit / Net
//     Operating Income / Net Income) — shared by the page's own export and the per-store print, so the two
//     can never lay a statement out differently.
//   · `perStorePayload` puts one store per printed page (lib/export `heading` + `pageBreakBefore`).
// DISPLAY ONLY — nothing here adds, subtracts or rounds a number.
import type { ExportColumn, ExportPayload, ExportSheet } from '@/lib/export'

export const PL_SECTION_TITLE: Record<string, string> = {
  revenue: 'Revenue', cogs: 'Cost of Goods Sold', opex: 'Operating Expenses', other: 'Other',
}

export function plQuery(period: string, scope: string, stores: string[], markets: string[], orgId: string): string {
  const filtered = stores.length > 0 || markets.length > 0
  const q = `scope=${encodeURIComponent(scope)}&org_id=${orgId}`
    + (filtered ? `&stores=${encodeURIComponent(stores.join('|'))}&markets=${encodeURIComponent(markets.join('|'))}` : '')
  return `/api/v1/account/pl/${encodeURIComponent(period)}?${q}`
}

export type PlRow = { section: string; line: string; amount: any }

export function plStatementRows(st: any): PlRow[] {
  const rows: PlRow[] = []
  ;(st?.sections || []).forEach((s: any) => {
    const title = PL_SECTION_TITLE[s.type] || s.type
    ;(s.lines || []).forEach((l: any) => rows.push({ section: title, line: l.label, amount: l.amount }))
    rows.push({ section: title, line: `  Subtotal — ${title}`, amount: s.subtotal })
  })
  rows.push({ section: 'Totals', line: 'Gross Profit', amount: st?.gross_profit })
  rows.push({ section: 'Totals', line: 'Net Operating Income', amount: st?.net_operating_income })
  rows.push({ section: 'Totals', line: 'Net Income', amount: st?.net_income })
  return rows
}

export const PL_COLUMNS: ExportColumn[] = [
  { header: 'Section', get: (r: any) => r.section },
  { header: 'Line', get: (r: any) => r.line },
  { header: 'Amount', get: (r: any) => r.amount, money: true },
]

// One printed page per store. `results` is in print order: each entry is the server's single-month P&L for
// that store alone (or the reason it could not be read). A store whose read failed or was never computed
// still gets its page, saying so — a missing store is never silently dropped from the printout.
export type StoreResult = { store: string; market?: string; data?: any; error?: string }

export function perStorePayload(results: StoreResult[], period: string, companyLabel: string): ExportPayload {
  const sheets: ExportSheet[] = results.map((r, i) => {
    const st = r.data?.statement
    const ok = !!(r.data?.computed && st)
    const sub = [period, 'Cash basis', companyLabel, r.market ? `Market: ${r.market}` : '',
      r.data?.stale ? 'STALE — data newer than statement' : ''].filter(Boolean).join(' · ')
    return {
      name: r.store,
      heading: `Profit & Loss — ${r.store}`,
      subheading: sub,
      pageBreakBefore: i > 0,
      columns: PL_COLUMNS,
      rows: ok ? plStatementRows(st)
        : [{ section: '—', line: r.error ? `Could not load: ${r.error}` : 'Not computed for this period', amount: null }],
    }
  })
  return {
    title: `Profit & Loss — each store (${results.length})`,
    subtitle: `${period} · Cash basis · ${companyLabel} · one store per page; each page is that store alone — `
      + 'the same figures the P&L shows with only that store selected. Company-wide lines (not booked to a store) '
      + 'read $0 on a store page; see the Consolidated view for them.',
    filename: `pl-each-store-${period.replace(/\s+/g, '-')}`,
    sheets,
  }
}
