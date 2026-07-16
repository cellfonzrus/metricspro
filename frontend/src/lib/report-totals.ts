// report-totals.ts — pure aggregation for a report TOTAL row. Shared by ReportShell's
// on-screen <tfoot> AND its export payload so the exported total is IDENTICAL to the total
// on screen (RULE FOUR — what you see is what you export). No React / no imports, so the
// aggregation math is unit-testable in plain Node.
//
// A column's aggregation is resolved once and used everywhere:
//   • money / numeric-count columns  → SUM of all the (filtered) rows given
//   • average / ratio / % columns    → recomputed MEAN (a SUM of ratios is meaningless);
//                                       an unweighted mean of the displayed values — a
//                                       WEIGHTED ratio needs the underlying bases, so a page
//                                       that wants that passes `agg` / a precomputed column
//   • text / date / anything else    → blank (the first blank column carries the label)
// A page can override any column with an explicit `agg` ('sum' | 'avg' | 'none').

export type Agg = 'sum' | 'avg' | 'none'

// Minimal structural column shape — ExportColumn (lib/export) satisfies this, so ReportShell
// passes its own columns straight through without a cast.
export interface TotalsCol {
  header: string
  get: (row: any) => any
  money?: boolean
  align?: 'left' | 'right'
  type?: 'text' | 'money' | 'number' | 'date'
  agg?: Agg
}

export interface TotalCell {
  text: string             // formatted for on-screen / PDF / Print
  raw: string | number     // unformatted for Excel (real numbers stay numeric)
  isMoney: boolean
  agg: Agg
}

// Headers that name an average / ratio / percentage. `[^a-z]` boundaries keep these from
// matching mid-word (e.g. "rate" inside "Generated" / "Operator" must NOT trip it).
const AVG_RE = /(^|[^a-z])(avg|average|mean|rate|ratio|percent|pct|margin)([^a-z]|$)|%|\bper\b/i

export const isMoneyCol = (c: TotalsCol): boolean => !!c.money || c.type === 'money'

// Parse a genuine number, tolerating currency formatting ($ , % whitespace) but REJECTING
// anything with stray letters. Critically this must NOT read a code like "X-12" as -12 (which a
// naive strip-then-parse does), or the auto-sum guard would sum a text column. `Number('')` is 0,
// not NaN, so we also require at least one digit.
export function toNumber(v: any): number | null {
  if (v == null || v === '') return null
  if (typeof v === 'number') return isFinite(v) ? v : null
  const cleaned = String(v).trim().replace(/[$,%\s]/g, '')
  if (!/^[-+]?(\d+\.?\d*|\.\d+)$/.test(cleaned)) return null
  const n = Number(cleaned)
  return isNaN(n) ? null : n
}

const moneyFmt = (n: any) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(n) || 0)
const numberFmt = (n: number) =>
  Number.isInteger(n) ? n.toLocaleString('en-US') : n.toLocaleString('en-US', { maximumFractionDigits: 2 })

// Decide how ONE column aggregates in the TOTAL row.
export function resolveAgg(col: TotalsCol, rows: any[]): Agg {
  if (col.agg) return col.agg                         // explicit page override always wins
  if (AVG_RE.test(col.header || '')) return 'avg'     // ratio/%/rate → mean, never a sum
  if (isMoneyCol(col)) return 'sum'
  if (col.type === 'text' || col.type === 'date') return 'none'
  // numeric-count columns (type:number, or right-aligned) → sum, but ONLY when every present
  // value is actually numeric (guards a right-aligned text column from being summed to 0).
  if (col.type === 'number' || col.align === 'right') {
    const present = rows.map(r => col.get(r)).filter(v => v != null && v !== '')
    if (present.length === 0) return 'none'
    return present.every(v => toNumber(v) != null) ? 'sum' : 'none'
  }
  return 'none'
}

// Build the full TOTAL row (one cell per column) over the given rows. `firstLabel` lands in
// the first NON-aggregated column (typically the leading label column).
export function computeTotalRow(cols: TotalsCol[], rows: any[], firstLabel = 'TOTAL'): TotalCell[] {
  const aggs = cols.map(c => resolveAgg(c, rows))
  const labelIdx = aggs.findIndex(a => a === 'none')   // -1 if every column aggregates
  return cols.map((col, i) => {
    const isMoney = isMoneyCol(col)
    const agg = aggs[i]
    if (agg === 'sum') {
      const total = rows.reduce((s, r) => s + (toNumber(col.get(r)) ?? 0), 0)
      return { text: isMoney ? moneyFmt(total) : numberFmt(total), raw: total, isMoney, agg }
    }
    if (agg === 'avg') {
      const vals = rows.map(r => toNumber(col.get(r))).filter((v): v is number => v != null)
      if (!vals.length) return { text: '', raw: '', isMoney, agg }
      const mean = vals.reduce((a, b) => a + b, 0) / vals.length
      return { text: isMoney ? moneyFmt(mean) : numberFmt(mean), raw: mean, isMoney, agg }
    }
    if (i === labelIdx) return { text: firstLabel, raw: firstLabel, isMoney, agg }
    return { text: '', raw: '', isMoney, agg }
  })
}
