'use client'
// PLAN MATCH CONFIG — the shared "pick, don't type" surface for every place a Commission Plan (or a
// multi-month installment schedule) matches a sale line (AGENT_CONTRACT §3b / RULE THREE).
//
// OWNER DIRECTIVE 2026-07-25: "Value not entered in commission plan should be from a drop down menu of
// available options." A hand-typed match value fails silently and in money:
//   • a value this tenant's data never contains matches NOTHING → the rule pays $0 and nothing says so;
//   • two hand-typed CONTAINS patterns that overlap ('home internet' + 'vhi', luxelink) match the SAME
//     lines → the rep is paid twice for one sale.
// So every value is picked from the tenant's OWN observed values (GET /commcalc/plan-field-options), and
// the two failure modes above are shown INLINE, computed exactly.
//
// HOW THE COUNTS CAN BE EXACT WITHOUT SHIPPING SALE LINES: rule matching depends only on seven columns
// (department / category / contract_type / tender_type / trans_type / product_desc / sku), so the backend
// sends the DISTINCT COMBINATIONS of those columns with a line count each (dictionary-encoded). Matching a
// rule against a few hundred facet rows gives the same number as matching every line — instantly, with no
// round trip per keystroke.
//
// `matchesFacet` below is a deliberate MIRROR of commission_engine._rule_matches (equals / contains / in,
// case-insensitive, plus the mig-232 `_ct_resolved` candidate when the tenant's contract-type resolution
// is 'mapped'). Parity with the Python is proven case-by-case by
// backend/scratchpad/plan_options_proof.py → frontend/scratchpad/prove_plan_match.mjs. DISPLAY ONLY: these
// numbers are a warning label, never a payout.
import { useMemo, useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import EntityPicker, { EntityOption } from '@/components/EntityPicker'

export type FieldOption = {
  value: string; lines?: number; stored_only?: boolean; resolved_bucket?: boolean; config_hits?: number
}
export type FieldInfo = {
  values: FieldOption[]; distinct?: number; truncated?: boolean; free_text?: boolean; closed?: boolean
  note?: string | null; resolution?: string
}
export type VocabItem = { value: string; label: string; help?: string; synthetic?: boolean; closed?: boolean; column?: boolean; uses?: string }
export type Vocab = {
  match_fields: VocabItem[]; match_ops: VocabItem[]; payout_kinds: VocabItem[]
  tier_bases: { value: string; label: string; help?: string }[]; tier_metrics: string[]
}
export type Facets = {
  columns: string[]; dict: Record<string, string[]>; ct_resolved: string[] | null
  rows: number[][]; truncated?: boolean; lines_covered?: number; lines_total?: number; combos_total?: number
}
export type PlanOptions = {
  ready?: boolean; vocab: Vocab; fields: Record<string, FieldInfo>; facets: Facets | null
  periods?: { value: string; lines: number }[]
  window?: { months: number; labels: string[] }
  source?: string; source_table?: string; bounded?: boolean; degraded?: boolean; note?: string | null
  contract_type_resolution?: string
}
export type MatchRule = { match_field?: string; match_op?: string; match_value?: string; label?: string | null; qualifies?: boolean }

// The vocabulary the page falls back to if the endpoint is unreachable — identical to what the pages
// hard-coded before, so a backend hiccup degrades to today's behaviour instead of an empty editor.
export const FALLBACK_VOCAB: Vocab = {
  match_fields: ['any', 'contract_type', 'tender_type', 'department', 'category', 'product_desc', 'sku', 'trans_type', 'accessory', 'activation_bucket']
    .map(v => ({ value: v, label: v })),
  match_ops: ['equals', 'contains', 'in'].map(v => ({ value: v, label: v })),
  payout_kinds: [
    { value: 'flat_per_unit', label: 'Flat $ per unit', uses: 'amount' },
    { value: 'pct_mrc', label: '% of MRC (raw_mi)', uses: 'pct' },
    { value: 'pct_gp', label: '% of GP', uses: 'pct' },
    { value: 'pct_price', label: '% of price (sale price)', uses: 'pct' },
    { value: 'pct_price_over_cost', label: '% of price − cost', uses: 'pct' },
    { value: 'flat', label: 'Flat $ once', uses: 'amount' },
  ],
  tier_bases: [
    { value: '', label: 'Legacy — every qualifying matched line (default)' },
    { value: 'transactions', label: 'Distinct transactions matching the tier rule' },
    { value: 'lines', label: 'Lines matching the tier rule' },
  ],
  tier_metrics: ['none', 'activations', 'upgrades', 'boxes'],
}

// ── the matcher mirror (see the header note) ─────────────────────────────────────────────────────
const norm = (v: unknown) => (v ?? '').toString().trim().toLowerCase()

/** commission_engine._rule_matches, evaluated against ONE decoded facet row. */
export function matchesFacet(get: (col: string) => string, rule: MatchRule, ctResolved?: string): boolean {
  const field = norm(rule.match_field || 'any')
  if (field === 'any') return true
  const op = norm(rule.match_op || 'equals')
  const want = norm(rule.match_value || '')
  const candidates = [norm(get(field))]
  if (field === 'contract_type' && ctResolved !== undefined) {
    const alt = norm(ctResolved)
    if (alt && alt !== candidates[0]) candidates.push(alt)
  }
  for (const have of candidates) {
    if (op === 'contains') { if (want !== '' && have.includes(want)) return true }
    else if (op === 'in') {
      const opts = want.split(',').map(s => s.trim()).filter(Boolean)
      if (opts.includes(have)) return true
    } else if (have === want) return true
  }
  return false
}

/** True when a rule's field can be counted from the facet table (synthetic fields cannot). */
export function analysable(opts: PlanOptions | null, rule: MatchRule): boolean {
  const f = norm(rule.match_field || 'any')
  if (!opts?.facets) return false
  return f === 'any' || opts.facets.columns.includes(f)
}

type Counts = { lines: number; rows: number }

/** Row accessor for one encoded facet row: [...one dict index per column, (ct_resolved index), lines]. */
function rowReader(fx: Facets, row: number[]) {
  const get = (col: string) => {
    const i = fx.columns.indexOf(col)
    return i < 0 ? '' : (fx.dict[col]?.[row[i]] ?? '')
  }
  const ct = fx.ct_resolved ? (fx.ct_resolved[row[fx.columns.length]] ?? '') : undefined
  return { get, ct, lines: row[row.length - 1] || 0 }
}

/** Exact matched-line count for one rule over the facet table. */
export function countMatches(opts: PlanOptions | null, rule: MatchRule): Counts | null {
  const fx = opts?.facets
  if (!fx || !analysable(opts, rule)) return null
  let lines = 0, rows = 0
  for (const row of fx.rows) {
    const r = rowReader(fx, row)
    if (matchesFacet(r.get, rule, r.ct)) { lines += r.lines; rows++ }
  }
  return { lines, rows }
}

/** Exact count of lines matched by BOTH rules — the double-pay guard. */
export function countOverlap(opts: PlanOptions | null, a: MatchRule, b: MatchRule): number | null {
  const fx = opts?.facets
  if (!fx || !analysable(opts, a) || !analysable(opts, b)) return null
  let lines = 0
  for (const row of fx.rows) {
    const r = rowReader(fx, row)
    if (matchesFacet(r.get, a, r.ct) && matchesFacet(r.get, b, r.ct)) lines += r.lines
  }
  return lines
}

export type MatchStats = {
  perRule: { lines: number; rows: number; analysable: boolean }[]
  /** overlaps[i][j] = lines matched by BOTH rule i and rule j (0 on the diagonal). */
  overlaps: number[][]
}

/** A cheap stable key for a rule list — so the O(rules² × facets) pass runs only when a matcher CHANGES,
 *  not on every keystroke elsewhere in the editor. */
export function rulesSignature(rules: MatchRule[]): string {
  return rules.map(r => `${r.match_field || 'any'}|${r.match_op || 'equals'}|${r.match_value || ''}|${r.qualifies === false ? 0 : 1}|${r.label || ''}`).join('¦')
}

/** Matched-line counts + the full pairwise overlap matrix, computed ONCE per matcher change. */
export function usePlanMatchStats(opts: PlanOptions | null, rules: MatchRule[]): MatchStats {
  const sig = rulesSignature(rules)
  return useMemo(() => {
    const fx = opts?.facets
    const n = rules.length
    const perRule = rules.map(() => ({ lines: 0, rows: 0, analysable: false }))
    const overlaps: number[][] = rules.map(() => new Array(n).fill(0))
    if (!fx) return { perRule, overlaps }
    const masks: Uint8Array[] = rules.map(() => new Uint8Array(fx.rows.length))
    rules.forEach((r, i) => { perRule[i].analysable = analysable(opts, r) })
    fx.rows.forEach((row, ri) => {
      const rd = rowReader(fx, row)
      rules.forEach((r, i) => {
        if (!perRule[i].analysable) return
        if (matchesFacet(rd.get, r, rd.ct)) {
          masks[i][ri] = 1
          perRule[i].lines += rd.lines
          perRule[i].rows += 1
        }
      })
      for (let i = 0; i < n; i++) {
        if (!masks[i][ri]) continue
        for (let j = i + 1; j < n; j++) {
          if (!masks[j][ri]) continue
          overlaps[i][j] += rd.lines
          overlaps[j][i] += rd.lines
        }
      }
    })
    return { perRule, overlaps }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts, sig])
}

// ── the picker ───────────────────────────────────────────────────────────────────────────────────
const num = (n?: number) => (n ?? 0).toLocaleString()

function toOptions(info: FieldInfo | undefined, current: string[]): EntityOption[] {
  const out: EntityOption[] = []
  const seen = new Set<string>()
  for (const v of info?.values || []) {
    const key = v.value.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    const bits: string[] = []
    if (v.stored_only) bits.push('in a saved rule — not in current data')
    else if (v.resolved_bucket) bits.push('resolved activation bucket')
    else if (typeof v.lines === 'number') bits.push(`${num(v.lines)} line${v.lines === 1 ? '' : 's'}`)
    if (v.config_hits) bits.push(`${v.config_hits} classification rule${v.config_hits === 1 ? '' : 's'}`)
    out.push({ id: v.value, label: v.value, sublabel: bits.join(' · ') || undefined })
  }
  // ZERO-WIPE: a value already saved on the plan is ALWAYS offered, even when the current data no longer
  // contains it — the picker may never silently drop what an operator configured.
  for (const c of current) {
    if (!c || seen.has(c.toLowerCase())) continue
    seen.add(c.toLowerCase())
    out.unshift({ id: c, label: c, sublabel: 'not in current data' })
  }
  return out
}

/**
 * CheckboxValuePicker — a real-checkbox multi-select for a rule's value (OWNER DIRECTIVE 2026-08-28:
 * "the items to be picked from in/contains should be check boxes … the full drop down does not open and
 * then closes after the 1st item is picked"). Two things it fixes over the plain combo-box:
 *   • REAL CHECKBOXES that STAY OPEN so several values are checked in one go (a filter box on top).
 *   • The panel is PORTALED to <body> with position:fixed, so it is NOT clipped by the rules table's
 *     `overflow-x:auto` (that clipping is exactly why the dropdown "did not fully open"). Fixed + portal
 *     also survives a transformed modal ancestor.
 * Emits the selected id array; the caller joins to the comma list the engine parses and flips the op to
 * 'in' when 2+ are checked (so an operator can never leave it on 'equals' and silently pay $0).
 */
function CheckboxValuePicker({ options, selected, onChange, width = 200, ariaLabel = 'Match value',
  placeholder = 'pick one or more…' }: {
  options: EntityOption[]; selected: string[]; onChange: (ids: string[]) => void
  width?: number; ariaLabel?: string; placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [rect, setRect] = useState<{ left: number; top: number; width: number } | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  const reposition = useCallback(() => {
    const r = btnRef.current?.getBoundingClientRect()
    if (r) setRect({ left: r.left, top: r.bottom + 4, width: r.width })
  }, [])

  useEffect(() => {
    if (!open) return
    reposition()
    const onMove = () => reposition()
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    function onDoc(e: MouseEvent) {
      const t = e.target as Node
      if (btnRef.current?.contains(t) || panelRef.current?.contains(t)) return
      setOpen(false); setQuery('')
    }
    document.addEventListener('mousedown', onDoc)
    return () => {
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
      document.removeEventListener('mousedown', onDoc)
    }
  }, [open, reposition])

  const selLower = useMemo(() => new Set(selected.map(s => s.toLowerCase())), [selected])
  const byId = useMemo(() => { const m: Record<string, EntityOption> = {}; options.forEach(o => { m[o.id] = o }); return m }, [options])
  const q = query.trim().toLowerCase()
  const filtered = useMemo(() => (!q ? options
    : options.filter(o => o.label.toLowerCase().includes(q) || (o.sublabel || '').toLowerCase().includes(q))), [options, q])

  function toggle(id: string) {
    const has = selected.some(s => s.toLowerCase() === id.toLowerCase())
    onChange(has ? selected.filter(s => s.toLowerCase() !== id.toLowerCase()) : [...selected, id])
  }

  const summary = selected.length === 0 ? placeholder
    : selected.length <= 2 ? selected.map(id => byId[id]?.label || id).join(', ')
    : `${selected.length} selected`

  return (
    <div style={{ display: 'inline-block', width }}>
      <button ref={btnRef} type="button" aria-haspopup="listbox" aria-expanded={open} aria-label={ariaLabel}
        onClick={() => setOpen(o => !o)}
        style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%', textAlign: 'left',
          padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13,
          background: 'var(--surface)', cursor: 'pointer', color: selected.length ? 'var(--text1)' : 'var(--text3)' }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{summary}</span>
        <span aria-hidden style={{ fontSize: 10, color: 'var(--text3)' }}>▾</span>
      </button>
      {open && rect && typeof document !== 'undefined' && createPortal(
        <div ref={panelRef} role="listbox" aria-multiselectable
          style={{ position: 'fixed', zIndex: 4000, left: rect.left, top: rect.top, width: 'max-content',
            minWidth: Math.max(rect.width, 220), maxWidth: 360, background: 'var(--surface)',
            border: '1px solid var(--border)', borderRadius: 8, boxShadow: '0 6px 24px rgba(0,0,0,0.22)', padding: 6 }}>
          <input autoFocus value={query} onChange={e => setQuery(e.target.value)} placeholder="Filter…"
            aria-label={`Filter ${ariaLabel}`}
            style={{ width: '100%', padding: '6px 8px', marginBottom: 4, borderRadius: 6, border: '1px solid var(--border)',
              fontSize: 12, background: 'var(--surface)', color: 'var(--text1)', boxSizing: 'border-box' }}
            onKeyDown={e => { if (e.key === 'Escape') { setOpen(false); setQuery('') } }} />
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, maxHeight: 260, overflowY: 'auto' }}>
            {filtered.length === 0 && <li style={{ padding: '7px 8px', fontSize: 12, color: 'var(--text3)' }}>No matching values</li>}
            {filtered.map(o => {
              const checked = selLower.has(o.id.toLowerCase())
              return (
                <li key={o.id} role="option" aria-selected={checked} onClick={() => toggle(o.id)}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', fontSize: 13, cursor: 'pointer', borderRadius: 6 }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface2)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  <input type="checkbox" checked={checked} readOnly tabIndex={-1} style={{ pointerEvents: 'none' }} />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {o.label}{o.sublabel ? <span style={{ color: 'var(--text3)' }}> · {o.sublabel}</span> : null}
                  </span>
                </li>
              )
            })}
          </ul>
          <div style={{ borderTop: '1px solid var(--border)', marginTop: 4, paddingTop: 4, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>{selected.length} selected</span>
            <span>
              {selected.length > 0 && (
                <button type="button" className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px', marginRight: 6 }}
                  onClick={() => onChange([])}>Clear</button>
              )}
              <button type="button" className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }}
                onClick={() => { setOpen(false); setQuery('') }}>Done</button>
            </span>
          </div>
        </div>, document.body)}
    </div>
  )
}

/**
 * The ONE match-value input used by every plan/tier/schedule matcher.
 *  • op 'in'       → MULTI picker; the stored value stays a comma list (what the engine parses).
 *  • op 'contains' → typeahead over observed values, free entry ALLOWED (a substring pattern is not a value).
 *  • op 'equals'   → single picker; free entry only where the option list can't be complete
 *                    (product_desc, or a list the backend truncated).
 * When `onOpChange` is provided and the field has a known value list, the value is picked with REAL
 * CHECKBOXES (CheckboxValuePicker) and the op is auto-flipped to 'in' as soon as 2+ values are checked —
 * so multi-value picking is obvious and can never be left on 'equals' (which matches the literal comma
 * string and silently pays $0).
 */
export function MatchValuePicker({ opts, field, op, value, onChange, onOpChange, width = 200, ariaLabel = 'Match value' }: {
  opts: PlanOptions | null; field: string; op: string; value: string
  onChange: (v: string) => void; onOpChange?: (op: string) => void; width?: number; ariaLabel?: string
}) {
  const f = norm(field || 'any')
  const o = norm(op || 'equals')
  const info = opts?.fields?.[f]
  const isAny = f === 'any'
  // Values are ALWAYS a comma list under the hood; both 'in' and (checkbox-managed) 'equals' read/write it.
  const multi = o === 'in'
  const selected = useMemo(() => (value || '').split(',').map(s => s.trim()).filter(Boolean), [value])
  const options = useMemo(() => toOptions(info, selected), [info, selected])
  const hasList = (info?.values?.length ?? 0) > 0
  const canManageOp = typeof onOpChange === 'function'
  // CHECKBOX multi-select whenever the field has a known value list and this is not a 'contains' pattern.
  // If the caller lets us manage the op (onOpChange), checkboxes work from 'equals' too and we flip to 'in'
  // when 2+ are checked; without that we only show checkboxes when the op is already 'in'.
  const useCheckbox = !isAny && o !== 'contains' && hasList && (multi || canManageOp)
  // Zero-wipe legacy: a rule saved as `equals "a,b"` (multi value under a single-match op) is repaired to
  // 'in' once, so it actually matches instead of silently paying $0. Fires at most once (o then === 'in').
  useEffect(() => {
    if (canManageOp && o === 'equals' && selected.length >= 2) onOpChange!('in')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canManageOp, o, selected.length])
  // Free entry is allowed ONLY where a closed list would be wrong or unusable: a 'contains' PATTERN (a
  // substring is not a value), a genuinely free-text field, a list the backend had to truncate, or NO
  // options at all (a brand-new tenant, or sales that couldn't be read — never lock the editor).
  const allowCreate = !isAny && (o === 'contains' || !!info?.free_text || !!info?.truncated || !info
    || (info.values?.length ?? 0) === 0)
  if (isAny) {
    return <EntityPicker options={[]} value={null} disabled width={width}
      placeholder="(every line — no value)" onChange={() => { }} ariaLabel={ariaLabel} />
  }
  if (useCheckbox) {
    return <CheckboxValuePicker options={options} selected={selected} width={width} ariaLabel={ariaLabel}
      onChange={ids => {
        onChange(ids.join(','))
        if (canManageOp && ids.length >= 2 && o !== 'in') onOpChange!('in')
      }} />
  }
  if (multi) {
    return <EntityPicker multi options={options} value={selected} width={width} allowCreate={allowCreate}
      placeholder="pick one or more…" onChange={ids => onChange(ids.join(','))}
      onCreate={v => onChange([...selected, v].join(','))}
      createLabel={v => `Use “${v}” (not in current data)`} ariaLabel={ariaLabel} />
  }
  return <EntityPicker options={options} value={selected[0] || null} width={width} allowCreate={allowCreate}
    placeholder={o === 'contains' ? 'pick or type a pattern…' : 'pick a value…'}
    onChange={v => onChange(v || '')} onCreate={v => onChange(v)}
    createLabel={v => (o === 'contains' ? `Use “${v}” as a pattern` : `Use “${v}” (not in current data)`)}
    ariaLabel={ariaLabel} />
}

/**
 * The two inline guards, computed exactly from the facet table:
 *   • DEAD RULE  — "matches nothing in the last N months" (the typo / wrong-tenant-value case).
 *   • OVERLAP    — "N lines also match «other rule»" (the double-pay case that cost the owner $10/line).
 * Purely informational: an overlap is sometimes intentional (a base rule + a bonus rule), so this never
 * blocks a save. Rules that don't qualify pay nothing, so they are excluded from the overlap check.
 */
export function MatchWarnings({ opts, rules, stats, index, windowLabel }: {
  opts: PlanOptions | null; rules: MatchRule[]; stats: MatchStats; index: number; windowLabel?: string
}) {
  const rule = rules[index]
  const win = windowLabel || `the last ${opts?.window?.months || 3} months`
  const me = stats.perRule[index]
  const f = norm(rule?.match_field || 'any')
  if (!opts?.facets || !me) return null
  if (!me.analysable) {
    return <div style={{ fontSize: 10.5, color: 'var(--text3)' }}>
      {f === 'accessory' || f === 'activation_bucket'
        ? 'classified per line at calculation time — no preview count here'
        : 'no observed values for this field'}
    </div>
  }
  const overlaps = (rule.qualifies === false || me.lines === 0) ? [] : rules
    .map((s, j) => ({ s, j, n: stats.overlaps[index]?.[j] || 0 }))
    .filter(x => x.j !== index && x.n > 0 && x.s.qualifies !== false)
    .sort((a, b) => b.n - a.n)
    .slice(0, 3)
  const truncNote = opts.facets.truncated
    ? ` (of the ${num(opts.facets.lines_covered)} lines analysed)` : ''
  return (
    <div style={{ fontSize: 10.5, lineHeight: 1.5 }}>
      {me.lines === 0 ? (
        <div style={{ color: '#b45309' }}>⚠ matches nothing in {win}{truncNote}</div>
      ) : (
        <div style={{ color: 'var(--text3)' }}>{num(me.lines)} line{me.lines === 1 ? '' : 's'} in {win}{truncNote}</div>
      )}
      {overlaps.map(x => (
        <div key={x.j} style={{ color: '#b45309' }}>
          ⚠ {num(x.n)} of them also match {ruleName(x.s)} — both rules pay on those lines
        </div>
      ))}
      {/* the model-name guard — see MatchEvidence */}
      <MatchEvidence opts={opts} rule={rule} />
    </div>
  )
}

/** The fields a description/SKU keyword is most often CONFUSED with — checked for the same word. */
const COLLISION_FIELDS = ['tender_type', 'department', 'category', 'contract_type', 'trans_type']

/** Distinct values of `col` among the facet rows this rule matches, biggest first. */
function matchedValues(opts: PlanOptions | null, rule: MatchRule, col: string): { value: string; lines: number }[] {
  const fx = opts?.facets
  if (!fx || !fx.columns.includes(col)) return []
  const acc = new Map<string, number>()
  for (const row of fx.rows) {
    const r = rowReader(fx, row)
    if (!matchesFacet(r.get, rule, r.ct)) continue
    const v = r.get(col)
    if (!v) continue
    acc.set(v, (acc.get(v) || 0) + r.lines)
  }
  return [...acc.entries()].map(([value, lines]) => ({ value, lines })).sort((a, b) => b.lines - a.lines)
}

/**
 * THE MODEL-NAME GUARD (owner ruling 2026-07-27 — the "edge" reclassification).
 *
 * A `contains` pattern on the item description matches on WORDING, so a word chosen to name a PAY
 * PROGRAM also catches every device whose MODEL name happens to contain it: a rule meaning the Edge
 * device-FINANCING program matched "Motorola Edge 2025" and paid $25 per handset line. The editor
 * showed a line COUNT, which looked healthy — it never showed WHAT was matched.
 *
 * So this shows two things, both computed from the facet payload already in the browser (no request):
 *   • the actual item descriptions the pattern hits — the operator sees the model name immediately;
 *   • whether the same word is ALSO a real value of another match field (tender_type / department /
 *     category / contract_type / trans_type) — usually the field the rule meant to key on.
 * Nothing about any particular keyword is hard-coded; both come from the tenant's own data.
 */
export function MatchEvidence({ opts, rule, max = 4 }: { opts: PlanOptions | null; rule: MatchRule; max?: number }) {
  const f = norm(rule?.match_field || 'any')
  const op = norm(rule?.match_op || 'equals')
  const pattern = norm(rule?.match_value || '')
  const isPattern = op === 'contains' && !!pattern && (f === 'product_desc' || f === 'sku')
  const items = useMemo(() => (isPattern ? matchedValues(opts, rule, 'product_desc') : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [opts, f, op, pattern])
  const collisions = useMemo(() => {
    if (!isPattern) return []
    const out: { field: string; values: { value: string; lines: number }[] }[] = []
    for (const cf of COLLISION_FIELDS) {
      const vals = (opts?.fields?.[cf]?.values || [])
        .filter(v => norm(v.value).includes(pattern))
        .map(v => ({ value: v.value, lines: v.lines || 0 }))
      if (vals.length) out.push({ field: cf, values: vals.slice(0, 3) })
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts, f, op, pattern])
  if (!isPattern || (!items.length && !collisions.length)) return null
  const shown = items.slice(0, max)
  const more = items.length - shown.length
  return (
    <div style={{ fontSize: 10.5, lineHeight: 1.5, marginTop: 2 }}>
      {shown.length > 0 && (
        <div style={{ color: 'var(--text3)' }}>
          matches {items.length === 1 ? 'this item' : `${num(items.length)} different items`}:{' '}
          {shown.map(x => `“${x.value}” (${num(x.lines)})`).join(', ')}{more > 0 ? ` +${num(more)} more` : ''}
        </div>
      )}
      {collisions.map(c => (
        <div key={c.field} style={{ color: '#b45309' }}>
          ⚠ “{rule.match_value}” is also a value of <b>{c.field}</b>{' '}
          ({c.values.map(v => `“${v.value}”${v.lines ? ` · ${num(v.lines)} lines` : ''}`).join(', ')}) —
          if this rule means that, match on {c.field} instead of the item description.
        </div>
      ))}
    </div>
  )
}

export function ruleName(r: MatchRule): string {
  if (r.label) return `“${r.label}”`
  const f = (r.match_field || 'any')
  if (f === 'any') return 'the blanket rule (any line)'
  return `“${f} ${r.match_op || 'equals'} ${r.match_value || '(blank)'}”`
}

/** Small provenance strip: where the options came from + how fresh/complete they are. */
export function OptionsSourceNote({ opts }: { opts: PlanOptions | null }) {
  if (!opts) return null
  if (opts.ready === false) {
    return <span style={{ fontSize: 11, color: '#b45309' }}>
      ⚠ value suggestions unavailable — you can still type values
    </span>
  }
  if (opts.note) {
    return <span style={{ fontSize: 11, color: '#b45309' }}>⚠ {opts.note}</span>
  }
  const w = opts.window?.labels?.length ? opts.window.labels.join(', ') : 'recent months'
  const src = opts.source_table === 'feed' ? 'the daily sales feed' : 'monthly sales'
  return (
    <span style={{ fontSize: 11, color: 'var(--text3)' }}>
      Value suggestions come from this tenant’s own {src} ({w}
      {opts.facets?.combos_total ? ` · ${num(opts.facets.combos_total)} distinct line signatures` : ''}
      {opts.bounded ? ' · partial scan — run migration 240 for the full set' : ''}).
    </span>
  )
}
