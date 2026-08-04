// Plan-commission per-line DRILL-DOWN: ordering, transaction grouping and category breakdown.
// PURE + DISPLAY-ONLY — nothing here computes, re-rates, rounds or alters a payout.
//
// OWNER DIRECTIVE 2026-08-04 (verbatim): "when drilling down the commission per line for the employee
// sort them by date first and then by the transaction id, all items for that transaction be paid
// together and also give filterable breakdown by different categories as it is giving for multi month
// and commission plan it should show twp edge accessories etc as different categories."
//
// What that means mechanically, and what this module guarantees:
//   1. ORDER — transactions run by trans DATE, then by TRANSACTION ID compared NUMERICALLY
//      ('297' before '5452', which a plain string sort gets backwards).
//   2. CONTIGUITY — every line of one transaction is emitted together, so a multi-line sale (plan line
//      + activation + financed device + case + access charge) reads as ONE sale with one subtotal.
//   3. CATEGORIES — the breakdown dimension is the PLAN RULE each line matched (twp / edge / accessory /
//      vhi1-4 / tablet / upgrade / activations / …). It is derived from the data at render time and is
//      NEVER a hard-coded list: rules are per-tenant, per-plan config (contract §3 RULE TWO), so a
//      hard-coded category list would be wrong for the next tenant by construction.
//
// SAFETY: `sortPlanLines(rows)` returns a PERMUTATION of `rows` — the same line objects, by identity,
// in a different order. Subtotals are plain sums of the `amount` the engine already put on each line.
// frontend/tools/plan-drilldown-proof.mjs asserts sort/contiguity/subtotals AND that the grand total is
// unchanged to the cent by every transform here.

export type PlanLine = {
  rule?: string | null
  date?: string | null
  trans_id?: string | null
  /** engine payout for THIS line. `null`/undefined = a flat bonus paid once per rep, not per line. */
  amount?: number | null
  /** false when the line matched a rule but does not count as a qualifying unit. */
  qualifies?: boolean
  /** true when the pay gate matched the line but paid nothing (the reason rides along). */
  suppressed?: boolean
  suppressed_reason?: string
  would_have_paid?: number
  [k: string]: any
}

/** Category label used when a plan rule carries no label of its own. */
export const UNNAMED_RULE = '(unnamed rule)'

/** A line's CATEGORY is the plan rule it matched — derived, never hard-coded. */
export function categoryOf(l: PlanLine): string {
  const s = l?.rule == null ? '' : String(l.rule).trim()
  return s || UNNAMED_RULE
}

/** A flat bonus is paid ONCE per rep, so its per-line amount is null and it adds $0 to any subtotal. */
export const isFlatOnce = (l: PlanLine): boolean => l?.amount == null

/** Money this line contributes to a subtotal. Non-numeric / flat-once ⇒ 0 (never NaN). */
export function lineAmount(l: PlanLine): number {
  const n = Number(l?.amount)
  return Number.isFinite(n) ? n : 0
}

/** A line counts as a UNIT when the engine qualified it and the pay gate did not suppress it. */
export const isUnit = (l: PlanLine): boolean => l?.suppressed !== true && l?.qualifies !== false

const round2 = (n: number) => Math.round((n + Number.EPSILON) * 100) / 100

/** Sum of `amount` over lines, rounded to cents exactly like the on-screen money cells. */
export const sumLines = (rows: PlanLine[]): number =>
  round2((rows || []).reduce((s, l) => s + lineAmount(l), 0))

const NUM_ONLY = /^\d+$/

/**
 * Numeric-aware transaction-id compare. '297' sorts BEFORE '5452' (a string sort puts '5452' first),
 * blanks sort last, and non-numeric ids fall back to a numeric-collation locale compare so a mixed
 * tenant ('A-12' / 'A-102') still reads naturally.
 */
export function compareTransId(a?: string | null, b?: string | null): number {
  const A = String(a ?? '').trim(), B = String(b ?? '').trim()
  if (A === B) return 0
  if (!A) return 1
  if (!B) return -1
  const na = NUM_ONLY.test(A), nb = NUM_ONLY.test(B)
  if (na && nb) {
    const d = Number(A) - Number(B)
    if (d !== 0) return d < 0 ? -1 : 1
    return A < B ? -1 : 1                      // '0042' vs '42': same number, stable tie-break
  }
  if (na !== nb) return na ? -1 : 1            // plain numbers before alphanumeric ids
  return A.localeCompare(B, 'en', { numeric: true, sensitivity: 'base' }) || (A < B ? -1 : 1)
}

/** A line's date normalized to YYYY-MM-DD (the payload already ships it that way). */
export const dateKey = (l: PlanLine): string => String(l?.date ?? '').trim().slice(0, 10)

/** Date compare with blanks LAST (an undated line must not lead the report). */
export function compareDate(a: string, b: string): number {
  if (a === b) return 0
  if (!a) return 1
  if (!b) return -1
  return a < b ? -1 : 1
}

export type TxnGroup = {
  key: string
  /** '' when the line carries no transaction id (such a line is its own group — never merged). */
  trans_id: string
  /** earliest line date in the group; drives the group's position in the report. */
  date: string
  lines: PlanLine[]
  subtotal: number
  units: number
  /** count of flat-bonus lines (paid once per rep) inside this transaction. */
  flat_lines: number
  /** distinct rule labels this transaction paid on, in the order they appear. */
  categories: string[]
}

/**
 * Group lines by TRANSACTION, ordered by (date, numeric trans id). Grouping by trans id — rather than
 * by (date, trans id) — is what GUARANTEES contiguity: even if a transaction's lines disagree on date
 * (a data oddity), they still render as one sale, positioned by the earliest of them.
 * Array#sort is stable (ES2019), so equal keys keep the payload's original order.
 */
export function groupPlanLinesByTxn(rows: PlanLine[]): TxnGroup[] {
  const byKey = new Map<string, { key: string; trans_id: string; date: string; lines: PlanLine[]; seq: number }>()
  ;(rows || []).forEach((l, i) => {
    const t = String(l?.trans_id ?? '').trim()
    const k = t ? `t:${t}` : `x:${i}`
    let g = byKey.get(k)
    if (!g) { g = { key: k, trans_id: t, date: dateKey(l), lines: [], seq: i }; byKey.set(k, g) }
    else {
      const d = dateKey(l)
      if (d && compareDate(d, g.date) < 0) g.date = d
    }
    g.lines.push(l)
  })
  const groups = Array.from(byKey.values())
  groups.sort((a, b) =>
    compareDate(a.date, b.date) || compareTransId(a.trans_id, b.trans_id) || (a.seq - b.seq))
  return groups.map(g => {
    const lines = g.lines.slice().sort((x, y) => compareDate(dateKey(x), dateKey(y)))
    const cats: string[] = []
    for (const l of lines) { const c = categoryOf(l); if (!cats.includes(c)) cats.push(c) }
    return {
      key: g.key, trans_id: g.trans_id, date: g.date, lines,
      subtotal: sumLines(lines),
      units: lines.filter(isUnit).length,
      flat_lines: lines.filter(isFlatOnce).length,
      categories: cats,
    }
  })
}

/** The flat, display-ordered line list: date → numeric trans id, transaction lines contiguous. */
export const sortPlanLines = (rows: PlanLine[]): PlanLine[] =>
  groupPlanLinesByTxn(rows).reduce<PlanLine[]>((acc, g) => (acc.push(...g.lines), acc), [])

export type CategoryStat = {
  category: string
  lines: number
  units: number
  txns: number
  amount: number
  flat_lines: number
}

/**
 * Per-category totals, derived from the rules actually present in the data (pick-don't-type, RULE
 * THREE/FIVE): biggest payer first, ties alphabetical, so the chips read as a ranking.
 */
export function planCategories(rows: PlanLine[]): CategoryStat[] {
  const m = new Map<string, CategoryStat & { _txns: Set<string> }>()
  for (const l of rows || []) {
    const c = categoryOf(l)
    let s = m.get(c)
    if (!s) { s = { category: c, lines: 0, units: 0, txns: 0, amount: 0, flat_lines: 0, _txns: new Set() }; m.set(c, s) }
    s.lines += 1
    if (isUnit(l)) s.units += 1
    if (isFlatOnce(l)) s.flat_lines += 1
    s.amount += lineAmount(l)
    const t = String(l?.trans_id ?? '').trim()
    if (t) s._txns.add(t)
  }
  return Array.from(m.values())
    .map(s => ({ category: s.category, lines: s.lines, units: s.units, txns: s._txns.size,
                 amount: round2(s.amount), flat_lines: s.flat_lines }))
    .sort((a, b) => (b.amount - a.amount) || a.category.localeCompare(b.category))
}

/** Keep only the selected categories. EMPTY selection = ALL categories (the default view). */
export function filterPlanLinesByCategory(rows: PlanLine[], selected: Iterable<string>): PlanLine[] {
  const set = selected instanceof Set ? selected : new Set(selected)
  if (!set.size) return rows || []
  return (rows || []).filter(l => set.has(categoryOf(l)))
}

export type PlanLineTotals = { lines: number; units: number; txns: number; amount: number; flat_lines: number }

/** Grand totals over whatever set of lines is currently visible. */
export function planLineTotals(rows: PlanLine[]): PlanLineTotals {
  const txns = new Set<string>()
  let units = 0, flat = 0
  for (const l of rows || []) {
    const t = String(l?.trans_id ?? '').trim()
    if (t) txns.add(t)
    if (isUnit(l)) units += 1
    if (isFlatOnce(l)) flat += 1
  }
  return { lines: (rows || []).length, units, txns: txns.size, amount: sumLines(rows), flat_lines: flat }
}
