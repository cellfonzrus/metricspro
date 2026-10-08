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

// ── THE ONE HOME for what a scope is CALLED on screen (owner report 2026-10-03) ──────────────────
// Owner (verbatim): *"the app shows the company id not the name of the company in the settings to
// choose the company to work in."*
//
// THE CLASS. Every finance surface — the Accounts hub, the P&L, the Balance Sheet, the Cash Flow,
// their export covers and their scheduled copies — rendered `scope_label || scope_key`.
// `scope_label` is a COPY persisted into `commcalc.account_statements` at compute time;
// `scope_key` for a company scope is the literal string `company:<uuid>`. So a company renamed
// after its snapshot, or a snapshot computed before the company was named, showed the user a raw
// uuid as the name of their company.
//
// THE FIX, one home for both sides: the BACKEND resolves the name once, in
// `account/coa.scope_display_label` (index §13b.1), by DEREFERENCING the canonical entity
// inventory `coa.org_companies` — the stored label is only a fallback. Every read that ships a
// scope (`/account/overview`, `/account/pl|balance-sheet|cash-flow`, `/account/pl-range`,
// `/account/statement`) stamps the answer as `scope_display`. This file is the frontend's ONLY
// reader of that fact: no page composes a label itself, and nothing here falls back to
// `scope_key`. `backend/harness_scope_label_lock.py` fails the build if a page starts to.
//
// RULE TWO: nothing below knows a tenant, company or carrier name — a scope is an opaque key and a
// display name is whatever the registry says it is.

/** A fact the backend resolved; the frontend never re-derives it. Masked rather than leaked when a
 *  response predates the field: a raw `company:<uuid>` is never shown to a human. */
const SCOPE_UNKNOWN = 'Unknown scope'

/** Anything a read hands back that carries a scope: a scope row, or a statement response. */
export type ScopeNamed = { scope_key?: string | null; scope?: string | null; scope_display?: string | null }

/** THE display name of a scope — `scope_display` as the backend's one home resolved it.
 *  A missing/blank field (an older cached response) degrades to a generic word, NEVER to the key. */
export function scopeDisplay(row: ScopeNamed | null | undefined): string {
  const d = String(row?.scope_display || '').trim()
  return d || SCOPE_UNKNOWN
}

/** THE display name with a caller-supplied fallback that is ITSELF already a display name (never a
 *  key) — for a response that carries no scope of its own. */
export function scopeDisplayOr(row: ScopeNamed | null | undefined, fallback: string): string {
  const d = String(row?.scope_display || '').trim()
  return d || String(fallback || '').trim() || SCOPE_UNKNOWN
}

/** The same answer for a statement read, where the page may have a chosen scope key but no response
 *  yet. The key is used only to say WHETHER something is selected — never as the label. */
export function statementScopeDisplay(data: ScopeNamed | null | undefined,
                                      scopes: ScopeNamed[] | null | undefined,
                                      selectedKey: string): string {
  const fromData = String(data?.scope_display || '').trim()
  if (fromData) return fromData
  const hit = (scopes || []).find(s => String(s?.scope_key || '') === String(selectedKey || ''))
  const fromList = String(hit?.scope_display || '').trim()
  return fromList || SCOPE_UNKNOWN
}

export type ScopeRow = {
  scope_key: string
  scope_label?: string | null
  /** THE display name, resolved by the backend's one home. Read it through `scopeDisplay`. */
  scope_display?: string | null
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

// ── THE DRILL PATH: company → market → store → line (owner report 2026-10-07, index §4e) ─────────
// Owner, verbatim: *"the details are missing and the details with the drop down does not tie any
// information which appears on the summary line, details should be same as on the p&l page, the
// expenses should be able to drill down, each line should be able to drill down to get to each level
// and finally down to the line level."*
//
// THREE things were wrong, and all three are the same class — a surface answering a question at a
// grain the answer was never produced at:
//
//   1. the panel showed only the EXPENSE sections, so it was not "the same as on the p&l page";
//   2. every per-company and per-store snapshot stored `detail: {}` (fixed in the BACKEND —
//      `coa.accrue_detail` + `engine._scoped`), so no line could drill to its rows;
//   3. there was no way down from a company to a market to a store at all.
//
// NOTHING BELOW DERIVES A DOLLAR. Every figure at every level is the server's own
// `GET /account/pl/{period}` answer, spelled by the ONE frontend query helper
// `_components/plStatement.plQuery` — the same request the P&L page makes. A market level is that
// request with the market in the filter; a store level is that request with the store in the filter.
// So a level TIES to the level above it by construction: both are the same read of the same
// per-store snapshots through `statement_filter`, not two summations.
//
// The market vocabulary is the canonical UNION market index (`GET /core/markets`, index §13) — the
// same authority the P&L's own market filter resolves through, so this drill can never offer a
// market the statement filter cannot bind.
//
// RULE TWO: a scope key, a market name and a store address are opaque strings here. Nothing branches
// on a tenant, carrier, company or store.

/** One rung of the drill path. `key` is what the request carries; `label` is what a human reads. */
export type DrillLevel = { kind: 'scope' | 'market' | 'store'; key: string; label: string }

/** The filter a path resolves to: the scope stays the ROOT scope (so a company's drill can never
 *  wander outside that company) and the market / store ride in the filter, exactly as the P&L page
 *  composes them. */
export function drillFilter(path: DrillLevel[]): { scope: string; stores: string[]; markets: string[] } {
  const root = path.find(p => p.kind === 'scope')
  const market = [...path].reverse().find(p => p.kind === 'market')
  const store = [...path].reverse().find(p => p.kind === 'store')
  return {
    scope: root ? root.key : 'consolidated',
    // A store is more specific than its market, so once a store is chosen the market adds nothing
    // and is dropped — carrying both would ask the filter to satisfy two predicates for one answer.
    stores: store ? [store.key] : [],
    markets: store ? [] : (market ? [market.key] : []),
  }
}

/** What the NEXT rung down offers. A store is the last rung — below it are the statement's own
 *  lines, and below those each line's drill rows. */
export function nextRung(path: DrillLevel[]): 'market' | 'store' | 'line' {
  if (path.some(p => p.kind === 'store')) return 'line'
  if (path.some(p => p.kind === 'market')) return 'store'
  return 'market'
}

/** A store scope has no markets or stores beneath it — the hub's By Store rows go straight to lines. */
export function drillRootLevels(scopeKey: string, scopeLabel: string): DrillLevel[] {
  const root: DrillLevel = { kind: 'scope', key: scopeKey, label: scopeLabel }
  return isStoreScope(scopeKey)
    ? [root, { kind: 'store', key: scopeAddress(scopeKey), label: scopeLabel }]
    : [root]
}

/** The markets of the org, as the canonical union index serves them, narrowed to the ones that
 *  actually have a store in this drill's scope when that store list is known. An empty `inScope`
 *  means "not known yet" and offers every market — never a silently shortened list. */
export function marketsForScope(allMarkets: string[], inScope?: string[] | null): string[] {
  const want = (inScope || []).map(s => String(s || '').trim().toLowerCase()).filter(Boolean)
  const seen = new Set<string>()
  const out: string[] = []
  for (const m of allMarkets || []) {
    const k = String(m || '').trim()
    if (!k || seen.has(k.toLowerCase())) continue
    if (want.length > 0 && !want.includes(k.toLowerCase())) continue
    seen.add(k.toLowerCase())
    out.push(k)
  }
  return out
}

/** EVERY section of a statement, in the P&L page's own order, empty ones dropped. The expense-only
 *  reader above is kept for the Expenses column's tie-out; this is what the panel renders, because
 *  the owner asked for "the same as on the p&l page". */
export const ALL_SECTIONS = ['revenue', 'cogs', 'opex', 'other'] as const

export function allSections(st: Statement | null | undefined): StatementSection[] {
  const secs = st?.sections || []
  const out: StatementSection[] = []
  for (const t of ALL_SECTIONS) {
    const s = secs.find(x => x?.type === t)
    if (s && (s.lines || []).length > 0) out.push(s)
  }
  return out
}

/** Display titles for every section — the same words the P&L page prints. */
export const SECTION_TITLE: Record<string, string> = {
  revenue: 'Revenue', cogs: 'Cost of Goods Sold', opex: 'Operating Expenses', other: 'Other',
}

/** TIE-OUT ONLY (never a source): does a line's drill-down add up to the line it sits under?
 *  Since the backend fix both come from the same per-store accumulation, so a mismatch means a
 *  stale snapshot — said out loud rather than hidden. `checked:false` = the line carries no drill
 *  rows, which is not a failure. */
export function lineTieOut(line: StatementLine | null | undefined):
  { checked: boolean; agree: boolean; detail: number; delta: number } {
  const d = line?.detail || {}
  const keys = Object.keys(d)
  if (keys.length === 0) return { checked: false, agree: true, detail: 0, delta: 0 }
  let t = 0
  for (const k of keys) t += Number(d[k] || 0)
  const detail = Math.round(t * 100) / 100
  const delta = Math.round((detail - Number(line?.amount || 0)) * 100) / 100
  return { checked: true, agree: Math.abs(delta) < 0.005, detail, delta }
}

/** The headline figures of a drill level, read straight off its statement — never recomputed.
 *  `reported:false` when the level has no computed statement: absent is not zero. */
export function levelTotals(st: Statement | null | undefined): {
  reported: boolean; revenue: number; gross_profit: number; expenses: number; net_income: number
} {
  if (!st) return { reported: false, revenue: 0, gross_profit: 0, expenses: 0, net_income: 0 }
  const secs = st.sections || []
  const sub = (t: string) => Number((secs.find(x => x?.type === t) || {}).subtotal || 0)
  return {
    reported: true,
    revenue: sub('revenue'),
    gross_profit: Number(st.gross_profit || 0),
    // The SAME definition as the hub's Expenses column (`EXPENSE_SECTIONS`), dereferenced — not a
    // second idea of what an expense is.
    expenses: Math.round(EXPENSE_SECTIONS.reduce((a, t) => a + sub(t), 0) * 100) / 100,
    net_income: Number(st.net_income || 0),
  }
}
