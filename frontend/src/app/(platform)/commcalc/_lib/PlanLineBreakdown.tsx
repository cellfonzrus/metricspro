'use client'
// PLAN-COMMISSION per-line drill-down — the ONE table both surfaces render:
//   • /commcalc/reports → Individual Rep → 🔍 Plan commission (the modal the owner was reading)
//   • /commcalc/commission-explain → "1 · Commission-Plan component"
//
// OWNER DIRECTIVE 2026-08-04: lines sorted by DATE then TRANSACTION ID, every line of one transaction
// rendered together with its own subtotal ("all items for that transaction be paid together"), plus a
// filterable breakdown by category — where a category is the PLAN RULE the line matched (twp, edge,
// accessory, vhi1-4, …), derived from the data, never a hard-coded list (rules are per-tenant config).
//
// DUAL MEMBERSHIP (2026-08-04, display-only): the plan engine evaluates EVERY rule against EVERY line,
// so one sale line legitimately appears TWICE here — e.g. the accessory inside an edge-financed sale
// shows under the accessory rule (paying) AND under the edge rule (⛔, per-device dedup suppression).
// That is correct, but the ⛔-only presentation read as "the accessory wasn't paid" and sent a real
// diagnosis down the wrong path ("accessories are being classified as edge") when the actual defect was
// an accessory rule matching nothing. So the table now SAYS it: the ⛔ carries its reason inline and
// names the rule that suppressed it (not "an accessory suppression"), same-line rows sit together with
// the PAYING row first, and each cross-references the other.
//
// DISPLAY ONLY. Every number shown is the amount the engine already put on the line; the subtotals are
// plain sums of those amounts. Nothing here writes, recalculates or re-rates anything.
// The visual language deliberately mirrors the multi-month (installments) drill-down — the grouped
// header + inner table is the same shape as its per-device card — so the two read as one product.
import { useMemo, useState } from 'react'
import { fmt } from '@/lib/client'
import {
  categoryOf, crossRefFor, filterPlanLinesByCategory, groupPlanLinesByTxn, isFlatOnce, isUnit,
  planCategories, planLineMembership, planLineTotals, type PlanLine,
} from './planLines'

const COLS = ['Rule', 'Date', 'Trans ID', 'Product', 'Contract', 'Basis', 'Price', 'GP', 'Line $']
const RIGHT = new Set(['Price', 'GP', 'Line $'])

export default function PlanLineBreakdown({ rows, compact, children }: {
  rows: PlanLine[]
  /** modal density (the reports drill-down) vs page density (commission-explain). */
  compact?: boolean
  /** optional render-prop for a page-level flat table / export, fed the SAME visible rows (WYSIWYG). */
  children?: (visible: PlanLine[]) => React.ReactNode
}) {
  const [sel, setSel] = useState<string[]>([])          // empty = ALL categories (the default)
  const [showExtra, setShowExtra] = useState(false)

  const cats = useMemo(() => planCategories(rows), [rows])
  const all = useMemo(() => planLineTotals(rows), [rows])
  // A category that disappears from the payload (period change) must not keep filtering the table.
  const active = useMemo(() => sel.filter(c => cats.some(k => k.category === c)), [sel, cats])
  const visible = useMemo(() => filterPlanLinesByCategory(rows, active), [rows, active])
  const groups = useMemo(() => groupPlanLinesByTxn(visible), [visible])
  const shown = useMemo(() => planLineTotals(visible), [visible])
  // the SAME rows in the SAME display order — what any export/flat view below must receive (WYSIWYG)
  const ordered = useMemo(
    () => groups.reduce<PlanLine[]>((acc, g) => (acc.push(...g.lines), acc), []), [groups])
  // Same-sale-line membership is computed over ALL rows, not the visible ones: when the table is
  // filtered to just `edge`, "paid under accessory" is the MOST useful thing the ⛔ row can say.
  const membership = useMemo(() => planLineMembership(rows), [rows])

  const toggle = (c: string) => setSel(s => s.includes(c) ? s.filter(x => x !== c) : [...s, c])

  const pad = compact ? '5px 8px' : '6px 9px'
  const td: React.CSSProperties = { padding: pad }
  const th: React.CSSProperties = {
    padding: pad, fontSize: 10, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap',
  }
  // muted one-liner that ties a row to the OTHER rows of the same sale line
  const xref: React.CSSProperties = {
    fontSize: 10.5, fontWeight: 400, color: 'var(--text3)', lineHeight: 1.35, marginTop: 1,
  }
  // the ⛔'s own reason, in the open instead of only in a tooltip
  const why: React.CSSProperties = {
    fontSize: 10.5, fontWeight: 400, color: '#b45309', lineHeight: 1.35, marginTop: 1,
    whiteSpace: 'normal', maxWidth: 230, marginLeft: 'auto', textAlign: 'right',
  }
  const chip = (on: boolean): React.CSSProperties => ({
    padding: '3px 10px', borderRadius: 999, fontSize: 11.5, fontWeight: 600, cursor: 'pointer',
    border: `1px solid ${on ? 'var(--accent)' : 'var(--border)'}`,
    background: on ? 'var(--accent)' : 'var(--surface)',
    color: on ? '#fff' : 'var(--text2)', whiteSpace: 'nowrap',
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* ── CATEGORY CHIPS (pick-don't-type: every chip is a rule that REALLY paid in this payload) ── */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: 11, color: 'var(--text3)', marginRight: 2 }}>Category:</span>
        <button type="button" style={chip(active.length === 0)} onClick={() => setSel([])}
          title="Show every category">
          All · {all.lines} line{all.lines === 1 ? '' : 's'} · {fmt(all.amount)}
        </button>
        {cats.map(c => (
          <button type="button" key={c.category} style={chip(active.includes(c.category))}
            onClick={() => toggle(c.category)}
            title={`${c.category} — ${c.lines} line(s), ${c.units} unit(s), ${c.txns} transaction(s), ${fmt(c.amount)}`}>
            {c.category} · {c.lines} · {fmt(c.amount)}
          </button>
        ))}
      </div>

      {/* ── PER-CATEGORY BREAKDOWN (always visible; the selected chips only highlight, never hide it) ── */}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Category (plan rule)', 'Transactions', 'Lines', 'Units', 'Line $'].map(h =>
              <th key={h} style={{ ...th, textAlign: h === 'Category (plan rule)' ? 'left' : 'right' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {cats.map(c => {
              const on = active.includes(c.category)
              return (
                <tr key={c.category} onClick={() => toggle(c.category)}
                  title="Click to filter the lines below by this category"
                  style={{
                    borderTop: '1px solid var(--border)', cursor: 'pointer',
                    background: on ? 'var(--surface2)' : undefined,
                    opacity: active.length && !on ? 0.5 : 1,
                  }}>
                  <td style={{ ...td, fontWeight: on ? 700 : 500 }}>
                    {on ? '☑ ' : '☐ '}{c.category}
                    {c.flat_lines > 0 && (
                      <span style={{ color: 'var(--text3)', fontWeight: 400 }}> · flat bonus (paid once per rep)</span>
                    )}
                  </td>
                  <td style={{ ...td, textAlign: 'right' }}>{c.txns}</td>
                  <td style={{ ...td, textAlign: 'right' }}>{c.lines}</td>
                  <td style={{ ...td, textAlign: 'right' }}>{c.units}</td>
                  <td style={{ ...td, textAlign: 'right', fontWeight: 600 }}>{fmt(c.amount)}</td>
                </tr>
              )
            })}
          </tbody>
          <tfoot>
            <tr style={{ background: 'var(--surface2)', fontWeight: 700, borderTop: '2px solid var(--border)' }}>
              <td style={td}>All categories</td>
              <td style={{ ...td, textAlign: 'right' }}>{all.txns}</td>
              <td style={{ ...td, textAlign: 'right' }}>{all.lines}</td>
              <td style={{ ...td, textAlign: 'right' }}>{all.units}</td>
              <td style={{ ...td, textAlign: 'right', color: 'var(--accent)' }}>{fmt(all.amount)}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      {active.length > 0 && (
        <div style={{ fontSize: 11.5, color: 'var(--text2)' }}>
          Filtered to <b>{active.join(' · ')}</b> — {shown.lines} of {all.lines} lines,{' '}
          {shown.txns} transaction{shown.txns === 1 ? '' : 's'}, <b>{fmt(shown.amount)}</b> of {fmt(all.amount)}.
          {' '}<button type="button" className="btn btn-secondary" style={{ padding: '1px 8px', fontSize: 11 }}
            onClick={() => setSel([])}>Clear</button>
        </div>
      )}

      {/* ── LINES, GROUPED BY TRANSACTION (date → numeric trans id; one sale = one block) ── */}
      {visible.length === 0 ? (
        <div style={{ fontSize: 13, color: 'var(--text3)' }}>No lines in the selected category.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {COLS.map(h => <th key={h} style={{ ...th, textAlign: RIGHT.has(h) ? 'right' : 'left' }}>{h}</th>)}
            </tr></thead>
            {groups.map(g => (
              <tbody key={g.key}>
                {/* one transaction = one visually contiguous block with its own subtotal */}
                <tr style={{ background: 'var(--surface2)', borderTop: '2px solid var(--border)' }}>
                  <td colSpan={COLS.length - 1} style={{ ...td, fontSize: 11.5 }}>
                    🧾 <b>Trans {g.trans_id || '—'}</b>
                    <span style={{ color: 'var(--text2)' }}>
                      {' · '}{g.date || 'no date'}
                      {' · '}{g.lines.length} line{g.lines.length === 1 ? '' : 's'}
                      {' · '}{g.categories.join(' · ')}
                    </span>
                  </td>
                  <td style={{ ...td, textAlign: 'right', fontWeight: 700 }} title="Subtotal paid on this transaction">
                    {fmt(g.subtotal)}
                  </td>
                </tr>
                {g.lines.map((l, i) => {
                  // Same SALE LINE, other RULES. Non-empty only for a genuinely dual-membership line.
                  const x = crossRefFor(l, membership)
                  const dual = x.paidElsewhere.length > 0 || x.alsoSuppressed.length > 0
                  return (
                  <tr key={`${g.key}:${i}`} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ ...td, borderLeft: `3px solid ${dual ? 'var(--border)' : 'var(--surface2)'}` }}>
                      {categoryOf(l)}
                      {/* CROSS-REFERENCE — the same sale line under another rule, one muted line. */}
                      {x.paidElsewhere.length > 0 && (
                        <div style={xref} title="This is the same sale line as the paying row above">
                          ↳ same line · paid under {x.paidElsewhere
                            .map(p => `${p.rule}${p.flat ? '' : ` ${fmt(p.amount)}`}`).join(' · ')}
                        </div>
                      )}
                      {x.alsoSuppressed.length > 0 && (
                        <div style={xref} title="The same sale line also matched these rules, which paid nothing">
                          ↳ also matched {x.alsoSuppressed.map(s => s.rule).join(' · ')} ⛔
                        </div>
                      )}
                    </td>
                    <td style={{ ...td, whiteSpace: 'nowrap' }}>{l.date || '—'}</td>
                    <td style={{ ...td, fontFamily: 'monospace', color: 'var(--text3)' }}>{l.trans_id || '—'}</td>
                    <td style={td} title={l.product || ''}>{l.product || '—'}</td>
                    <td style={td}>{l.contract_type || '—'}</td>
                    <td style={{ ...td, color: 'var(--text3)' }}>{l.basis || '—'}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{fmt(l.ext_price)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{fmt(l.gp)}</td>
                    <td style={{ ...td, textAlign: 'right', fontWeight: 600 }}>
                      {isFlatOnce(l) ? 'flat (once)' : fmt(l.amount as number)}
                      {l.suppressed && (
                        <span style={{ color: '#dc2626', marginLeft: 4 }}
                          title={`Not paid — ${l.suppressed_reason || 'suppressed by the pay gate'}`
                            + (l.would_have_paid ? ` · would have paid ${fmt(l.would_have_paid)}` : '')}>⛔</span>
                      )}
                      {!isUnit(l) && !l.suppressed && (
                        <span style={{ color: 'var(--text3)', marginLeft: 4 }} title="Matched but non-qualifying">◦</span>
                      )}
                      {/* WHY the ⛔, in the open — naming the rule that suppressed it, so it can never
                          be misread as a suppression of the rule the reader had in mind. */}
                      {l.suppressed && (
                        <div style={why}>
                          {categoryOf(l)} — {l.suppressed_reason || 'suppressed by the pay gate'}
                        </div>
                      )}
                    </td>
                  </tr>
                )})}
              </tbody>
            ))}
            <tfoot>
              <tr style={{ background: 'var(--surface2)', fontWeight: 700, borderTop: '2px solid var(--border)' }}>
                <td colSpan={COLS.length - 1} style={{ ...td, textAlign: 'right', color: 'var(--text2)' }}>
                  {groups.length} transaction{groups.length === 1 ? '' : 's'} · {shown.lines} line
                  {shown.lines === 1 ? '' : 's'} · Σ line $:
                </td>
                <td style={{ ...td, textAlign: 'right', color: 'var(--accent)' }}>{fmt(shown.amount)}</td>
              </tr>
            </tfoot>
          </table>
          {/* Honesty: this column is the per-line money the rules produced. A flat bonus pays once per
              rep (no per-line amount) and the plan's tier multiplier is applied AFTER these lines, so
              Σ line $ is the pre-tier base — the plan total is the one stated above the table. */}
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>
            Σ line $ is the pre-tier line total{all.flat_lines > 0 ? ' and excludes flat bonuses (paid once per rep)' : ''};
            the plan’s tier multiplier is applied to it above.
          </div>
        </div>
      )}

      {children && (
        <div>
          <button type="button" className="btn btn-secondary" style={{ padding: '2px 10px', fontSize: 12 }}
            onClick={() => setShowExtra(v => !v)}>
            {showExtra ? '▾' : '▸'} Flat table · full columns · Excel / PDF / email / WhatsApp export
          </button>
          {showExtra && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 11.5, color: 'var(--text3)', marginBottom: 6 }}>
                Same rows, same order, same category filter as above — what you see is what exports.
              </div>
              {children(ordered)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
