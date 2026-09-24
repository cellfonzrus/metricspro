// ── The Account hub's Expenses column + the per-scope drill-down (owner request 2026-09-21) ──────
// Owner: *"in teh finance accout module add expenses column also and let each store be drilled down
// to get more details"*.
//
// PURE + DISPLAY-ONLY. Nothing in this file derives a dollar. It exists so the hub can never grow a
// SECOND answer to "what are this store's expenses for this period":
//
//   · THE NUMBER comes from the backend, where `account/analysis.pl_totals` owns it once
//     (`expenses` = Σ `analysis.EXPENSE_SECTIONS` subtotals — the statement sections that sit below
//     gross profit). `GET /account/overview/{period}` dereferences that home, so the column IS the
//     P&L's expense total for the same scope + period, to the cent. The hub does not re-sum
//     `commcalc.store_expenses`, does not net GP − NI, and does not know what an expense feed is.
//   · THE DETAIL comes from `GET /account/pl/{period}?scope=<scope_key>` — the SAME endpoint the
//     /accounts/pl page reads, unfiltered, so the drill-down renders the canonical stored snapshot
//     rather than a fourth place store financials are computed. No new endpoint was added.
//   · THE TIE-OUT below re-adds the panel's section subtotals and compares them to the column. It is
//     a CHECK, not a source: when the two disagree the panel says so out loud instead of quietly
//     showing a different number (`expenseTieOut`).
//
// EXPENSE_SECTIONS is the display-side mirror of `analysis.EXPENSE_SECTIONS` (the backend tuple is
// the registry of record). `frontend/prove_accounts_expenses_column.mjs` reads the Python source and
// FAILS if the two ever differ, so a new below-GP section cannot appear in the statement and be
// silently missing from this panel.
//
// RULE TWO: nothing here branches on a tenant, carrier, company or store name — a scope key is an
// opaque string and every row uses the same mechanism.

/** Statement section types that ARE expenses. Mirrors `account/analysis.EXPENSE_SECTIONS`. */
export const EXPENSE_SECTIONS = ['opex', 'other'] as const

/** Display titles for the sections a drill-down renders (same words the P&L uses). */
export const EXPENSE_SECTION_TITLE: Record<string, string> = { opex: 'Operating Expenses', other: 'Other' }

export type ScopeRow = {
  scope_key: string
  scope_label?: string | null
  revenue?: number | null
  gross_profit?: number | null
  net_income?: number | null
  /** Σ EXPENSE_SECTIONS from the stored P&L snapshot; ABSENT when this scope has no P&L. */
  expenses?: number | null
  assets?: number | null
  balanced?: boolean
}

export type StatementLine = { key?: string; label?: string; amount?: number; kind?: string; note?: string; detail?: Record<string, number> }
export type StatementSection = { name?: string; type?: string; lines?: StatementLine[]; subtotal?: number }
export type Statement = { sections?: StatementSection[]; gross_profit?: number; net_operating_income?: number; net_income?: number }

/** An absent figure is NOT $0.00. A scope with no computed P&L has nothing to report; a scope whose
 *  P&L reports $0 of expenses has MEASURED zero. The two render differently everywhere in finance,
 *  so the difference is carried as data, not as a falsy number. */
export type Measured = { reported: boolean; amount: number }

export function scopeExpenses(row: ScopeRow | null | undefined): Measured {
  const v = row?.expenses
  if (v === undefined || v === null || typeof v !== 'number' || !isFinite(v)) return { reported: false, amount: 0 }
  return { reported: true, amount: v }
}

/** True when this scope row addresses a single store (the rows the owner asked to drill). */
export function isStoreScope(scopeKey: string | null | undefined): boolean {
  return String(scopeKey || '').startsWith('store:')
}

/** The address behind a `store:<address>` key — the canonical store spelling the statements use. */
export function scopeAddress(scopeKey: string | null | undefined): string {
  const k = String(scopeKey || '')
  return k.startsWith('store:') ? k.slice('store:'.length) : k
}

/** THE canonical read for a scope's statement detail — the same endpoint /accounts/pl fetches.
 *  Unfiltered: `scope` alone returns the stored snapshot for that scope, byte-identical to the page. */
export function scopeStatementPath(period: string, scopeKey: string, orgId: string): string {
  return `/api/v1/account/pl/${encodeURIComponent(period)}?scope=${encodeURIComponent(scopeKey)}&org_id=${orgId}`
}

/** Where "see everything" goes: the existing P&L page, already filtered to this scope. One detail
 *  view for the whole module — the drill-down panel is a preview of it, not a replacement. */
export function scopeDetailHref(scopeKey: string): string {
  return `/accounts/pl?scope=${encodeURIComponent(scopeKey)}`
}

export function scopeBalanceSheetHref(scopeKey: string): string {
  return `/accounts/balance-sheet?scope=${encodeURIComponent(scopeKey)}`
}

/** The expense sections of a statement payload, in EXPENSE_SECTIONS order, empty ones dropped.
 *  Read straight off the snapshot — labels, amounts, notes and per-line detail all as stored. */
export function expenseSections(st: Statement | null | undefined): StatementSection[] {
  const secs = st?.sections || []
  const out: StatementSection[] = []
  for (const t of EXPENSE_SECTIONS) {
    const s = secs.find(x => x?.type === t)
    if (s && (s.lines || []).length > 0) out.push(s)
  }
  return out
}

/** TIE-OUT ONLY (never a source): Σ of the expense sections' own subtotals on the payload. */
export function statementExpenseSubtotal(st: Statement | null | undefined): number {
  const secs = st?.sections || []
  let t = 0
  for (const type of EXPENSE_SECTIONS) {
    const s = secs.find(x => x?.type === type)
    t += Number(s?.subtotal || 0)
  }
  return Math.round(t * 100) / 100
}

/** Does the drilled statement still add up to the column? Reported, never hidden. Cent-tolerant
 *  because the column and the snapshot are both stored to 2dp. */
export function expenseTieOut(column: Measured, st: Statement | null | undefined):
  { checked: boolean; agree: boolean; delta: number; detail: number } {
  const detail = statementExpenseSubtotal(st)
  if (!column.reported || !st) return { checked: false, agree: true, delta: 0, detail }
  const delta = Math.round((detail - column.amount) * 100) / 100
  return { checked: true, agree: Math.abs(delta) < 0.005, delta, detail }
}

/** Gross profit − expenses == net income is the identity the column is built on. Surfaced so the
 *  panel can say it plainly; a row that fails it is a snapshot defect worth seeing, not hiding. */
export function marginIdentity(row: ScopeRow): { checked: boolean; agree: boolean; delta: number } {
  const e = scopeExpenses(row)
  if (!e.reported || typeof row.gross_profit !== 'number' || typeof row.net_income !== 'number') {
    return { checked: false, agree: true, delta: 0 }
  }
  const delta = Math.round((row.gross_profit - e.amount - row.net_income) * 100) / 100
  return { checked: true, agree: Math.abs(delta) < 0.015, delta }
}
