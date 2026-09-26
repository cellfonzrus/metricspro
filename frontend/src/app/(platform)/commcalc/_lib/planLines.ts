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

/** A row that actually PUT MONEY on this sale (a flat-once bonus counts: it pays, just not per line). */
export const isPaying = (l: PlanLine): boolean =>
  l?.suppressed !== true && (isFlatOnce(l) || lineAmount(l) > 0)

// ── DUAL MEMBERSHIP (display-only, 2026-08-04) ────────────────────────────────────────────────────
// The plan engine evaluates EVERY rule against EVERY line. So one sale line legitimately appears MORE
// THAN ONCE in this drill-down — e.g. the accessory inside an edge-financed sale shows up under the
// accessory rule (paying) AND under the edge rule (⛔, per-device dedup suppression). That is correct
// engine behaviour, but a ⛔-only presentation reads as "the accessory was not paid", which is exactly
// how a misconfigured accessory rule got diagnosed as "accessories are being classified as edge".
// These helpers let the table SAY which rows are the same sale line. They compute nothing about money.

const idPart = (v: any): string => {
  const n = Number(v)
  return Number.isFinite(n) && String(v ?? '').trim() !== '' ? n.toFixed(2)
    : String(v ?? '').trim().toLowerCase()
}

/**
 * Identity of the underlying SALE LINE — NOT of the rendered row. Built only from fields BOTH
 * surfaces already carry (rule is deliberately excluded: differing rule is the whole point).
 * Two physically distinct lines that agree on every one of these fields are indistinguishable in the
 * payload, so treating them as one identity is the honest reading; the helpers below count members
 * rather than assuming a 1:1 pairing.
 */
export function lineIdentity(l: PlanLine): string {
  return [
    idPart(l?.trans_id), dateKey(l), idPart(l?.product), idPart(l?.contract_type),
    idPart(l?.ext_price), idPart(l?.gp), idPart(l?.imei) || idPart(l?.mdn),
  ].join('|')
}

export type LineMembership = {
  /** rules this sale line PAID under, in display order. */
  paying: { rule: string; amount: number; flat: boolean }[]
  /** rules this sale line was SUPPRESSED under, in display order, with the engine's own reason. */
  suppressed: { rule: string; reason: string; would_have_paid: number }[]
}

/** identity → which rules paid it and which suppressed it. PURE; no money is derived or altered. */
export function planLineMembership(rows: PlanLine[]): Map<string, LineMembership> {
  const m = new Map<string, LineMembership>()
  for (const l of rows || []) {
    const k = lineIdentity(l)
    let e = m.get(k)
    if (!e) { e = { paying: [], suppressed: [] }; m.set(k, e) }
    if (l?.suppressed === true) {
      e.suppressed.push({ rule: categoryOf(l), reason: String(l?.suppressed_reason || '').trim(),
                          would_have_paid: Number(l?.would_have_paid) || 0 })
    } else if (isPaying(l)) {
      e.paying.push({ rule: categoryOf(l), amount: lineAmount(l), flat: isFlatOnce(l) })
    }
  }
  return m
}

export type CrossRef = {
  /** OTHER rules that paid this same sale line (empty ⇒ nothing to cross-reference). */
  paidElsewhere: { rule: string; amount: number; flat: boolean }[]
  /** OTHER rules that matched this same sale line but paid nothing. */
  alsoSuppressed: { rule: string; reason: string; would_have_paid: number }[]
}

/** What to say NEXT TO one row about the other rows of the same sale line. PURE. */
export function crossRefFor(l: PlanLine, m: Map<string, LineMembership>): CrossRef {
  const e = m.get(lineIdentity(l))
  const self = categoryOf(l)
  if (!e) return { paidElsewhere: [], alsoSuppressed: [] }
  return {
    paidElsewhere: e.paying.filter(p => p.rule !== self),
    alsoSuppressed: e.suppressed.filter(s => s.rule !== self),
  }
}

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

// ── PER ACTIVATION / PER UPGRADE (owner 2026-09-25) ──────────────────────────────────────────────
// "commisison for teh reps need to be claculated per action and per upgrade as defined in teh incentive
// payout, the system sis calculating per line item". The ENGINE decides what one activation / upgrade is
// (backend `line_class.activation_events`, THE definition) and stamps every matched line with its event
// (`event_id` / `event_key` / `event_type`). This module only GROUPS by that stamp — it never derives an
// event from a product name, a phone number or a line count (harness_activation_event_lock.py pins it).

/** The words an event type reads as. Display only; the type itself comes from the engine. */
export const EVENT_TYPE_LABEL: Record<string, string> = {
  activation: 'Activation', port: 'Port-in', byod: 'BYOD activation', upgrade: 'Upgrade',
}
/** The per-invoice counters the header shows, in order: new lines (activation + port + BYOD) and upgrades. */
export const EVENT_COUNTERS: { key: string; label: string; types: string[] }[] = [
  { key: 'activations', label: 'Activations', types: ['activation', 'port', 'byod'] },
  { key: 'upgrades', label: 'Upgrades', types: ['upgrade'] },
]

export type EventGroup = {
  id: string
  /** the phone line (or device) the engine identified the event by; '' for a whole-invoice event */
  key: string
  key_kind: string
  type: string
  lines: PlanLine[]
  /** Σ line $ across the event's lines — one payment per paying rule, the rest ⛔ */
  amount: number
}

/** The engine's events among `lines`, in first-seen order, plus the lines that belong to none. PURE. */
export function eventsOf(lines: PlanLine[]): { events: EventGroup[]; loose: PlanLine[]; counts: Record<string, number> } {
  const byId = new Map<string, EventGroup>()
  const loose: PlanLine[] = []
  for (const l of lines || []) {
    const id = String(l?.event_id ?? '').trim()
    if (!id) { loose.push(l); continue }
    let e = byId.get(id)
    if (!e) {
      e = { id, key: String(l?.event_key ?? ''), key_kind: String(l?.event_key_kind ?? ''),
            type: String(l?.event_type ?? ''), lines: [], amount: 0 }
      byId.set(id, e)
    }
    e.lines.push(l)
  }
  const events = Array.from(byId.values()).map(e => ({ ...e, amount: sumLines(e.lines) }))
  const counts: Record<string, number> = {}
  for (const c of EVENT_COUNTERS) counts[c.key] = events.filter(e => c.types.includes(e.type)).length
  return { events, loose, counts }
}

/** The basis label for one engine line: a per-event rule reads "$ per activation" / "$ per upgrade". */
export function basisLabel(rule: any, line: any, basisOfKind: Record<string, string>): string {
  const kind = String(rule?.payout_kind ?? '')
  if (String(rule?.unit_basis ?? '') === 'per_event' && line?.event_type) {
    return '$ per ' + (EVENT_TYPE_LABEL[line.event_type] || line.event_type).toLowerCase()
  }
  return basisOfKind[kind] || kind
}

/**
 * ONE engine rule line → the drill-down row both surfaces render (the reports modal and
 * commission-explain). The event stamp rides along untouched. Display only.
 */
export function toPlanLine(rule: any, l: any, basisOfKind: Record<string, string>): PlanLine {
  return {
    rule: rule?.label, basis: basisLabel(rule, l, basisOfKind), date: l?.date, trans_id: l?.trans_id,
    imei: l?.imei, mdn: l?.mdn, product: l?.product, contract_type: l?.contract_type,
    ext_price: l?.ext_price, gp: l?.gp, amount: l?.flat_once ? null : l?.amount,
    qualifies: l?.qualifies !== false, suppressed: !!l?.suppressed, suppressed_by: l?.suppressed_by || '',
    suppressed_reason: l?.suppressed_reason || '', would_have_paid: l?.would_have_paid ?? 0,
    event_id: l?.event_id || '', event_key: l?.event_key || '', event_key_kind: l?.event_key_kind || '',
    event_type: l?.event_type || '',
    // the sale this line is (index §6j) — stamped by the server (`payout_audience.stamp_line_identity`)
    event_label: l?.event_label || '', phone: l?.phone || '', customer: l?.customer || '',
  }
}

/**
 * The SALE a line is, as one label: the action (the server's class label, e.g. 'New activation' / 'Upgrade'),
 * the phone line and the customer — owner 2026-09-26: "on paid row show the action / upgrade with the details of
 * the phone number and customer name". Every part comes from the server's stamp; '' when it carries none.
 */
export function saleLabel(l: PlanLine): string {
  const action = l?.event_label || (l?.event_type ? (EVENT_TYPE_LABEL[String(l.event_type)] || String(l.event_type)) : '')
  return [action, l?.phone, l?.customer].filter(x => x && String(x).trim()).join(' · ')
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
  /** the engine's activation / upgrade events on this invoice (empty when no rule pays per event) */
  events: EventGroup[]
  /** per-invoice counters (EVENT_COUNTERS keys) — distinct events, never lines */
  event_counts: Record<string, number>
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
    const dated = g.lines.slice().sort((x, y) => compareDate(dateKey(x), dateKey(y)))
    // DUAL MEMBERSHIP ORDERING (display-only). Rows that are the SAME sale line are kept ADJACENT and
    // the PAYING one leads, so a ⛔ row can never be read as "this sale wasn't paid" — the row above it
    // says it was. `firstSeen` is the identity's first position in the date-sorted list, so a payload
    // where every line is distinct (the normal case) reduces to EXACTLY the previous order: firstSeen
    // is then each row's own index. Identity carries the date, so the date ordering above is preserved.
    // This is a PERMUTATION — no line is added, dropped, merged or re-rated.
    const firstSeen = new Map<string, number>()
    dated.forEach((l, i) => { const k = lineIdentity(l); if (!firstSeen.has(k)) firstSeen.set(k, i) })
    const order = dated.map((_l, i) => i)
    order.sort((a, b) =>
      ((firstSeen.get(lineIdentity(dated[a])) as number) - (firstSeen.get(lineIdentity(dated[b])) as number))
      || ((dated[a].suppressed === true ? 1 : 0) - (dated[b].suppressed === true ? 1 : 0))
      || (a - b))
    let lines = order.map(i => dated[i])
    // EVENT GROUPING (2026-09-25): when the engine stamped events, an event's lines sit together under
    // it (events in first-seen order, lines keeping the order above; lines of no event last). A payload
    // with no event stamp is untouched — this is the identity permutation for it.
    const ev = eventsOf(lines)
    if (ev.events.length) lines = [...ev.events.flatMap(e => e.lines), ...ev.loose]
    const cats: string[] = []
    for (const l of lines) { const c = categoryOf(l); if (!cats.includes(c)) cats.push(c) }
    return {
      key: g.key, trans_id: g.trans_id, date: g.date, lines,
      subtotal: sumLines(lines),
      units: lines.filter(isUnit).length,
      flat_lines: lines.filter(isFlatOnce).length,
      categories: cats,
      events: ev.events,
      event_counts: ev.counts,
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
