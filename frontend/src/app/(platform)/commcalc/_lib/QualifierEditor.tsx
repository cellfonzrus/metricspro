'use client'
/**
 * ATTACH QUALIFIER — the plan editor's input for a condition measured over the WHOLE SALE.
 *
 * OWNER DIRECTIVE 2026-10-12: *"Payment for edge on total which is defined as $25 must be accompanied
 * with atleast $25 worth of accessories , add this logic in the system not as hard coded but as a logic
 * platform wide which might be used by other carriers also , label the commission as $25 acc is must to
 * be qualified for $25 commission otherwise the commision falls back to $10 or whatever is paid to
 * Chicago market"*
 *
 * ONE HOME, DEREFERENCED. Every word of vocabulary here comes from the served `vocab` — which
 * `plan_options.vocabulary` builds by reading `commcalc/attach_qualifier` (index §COMMCALC-06). This
 * file names no measure, no default and no dollar figure of its own, and does NOT rebuild the
 * requirement sentence: that is `attach_qualifier.describe`, served as `rule.qualifier_requirement`
 * on the plan GET and displayed verbatim below. A second rendering of the same figures is a second
 * home, and the owner reads that sentence as the rule.
 *
 * WHY THE FIELDS LIVE IN A COMPONENT AND NOT INLINE: `plan-installments` edits rules through the same
 * vocabulary, so the next screen that needs a qualifier imports this rather than copying four inputs.
 */
import { Vocab } from './planMatch'

/** The qualifier columns as the editor holds them: a number or '' while being typed. */
export type QualifierRule = {
  payout_kind?: string
  qualifier_min?: number | string | null
  qualifier_measure?: string | null
  qualifier_scope?: string | null
  qualifier_match_field?: string | null
  qualifier_match_op?: string | null
  qualifier_match_value?: string | null
  qualifier_fallback_amount?: number | string | null
  qualifier_label?: string | null
  qualifier_requirement?: string | null
}
export type QualifierPatch = Partial<QualifierRule>

const box: React.CSSProperties = {
  fontSize: 12, padding: '4px 6px', border: '1px solid var(--border)',
  borderRadius: 4, background: 'var(--surface)', color: 'var(--text)',
}
const lab: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', gap: 2, fontSize: 11, color: 'var(--text3)',
}

/** True when this rule declares a qualifier at all. A minimum of 0 or below is NOT one — the engine
 *  reads it as off and migration 1079's CHECK refuses to store it. */
export function hasQualifier(rule: QualifierRule | null | undefined): boolean {
  const n = Number((rule || {}).qualifier_min)
  return Number.isFinite(n) && n > 0
}

/** True when the served vocabulary can confirm which measures the engine understands. False degrades
 *  the editor to hiding the fields: a money condition must not be entered against a guessed list. */
export function qualifierVocabReady(vocab: Vocab | null | undefined): boolean {
  return !!(vocab && (vocab.qualifier_measures || []).length && vocab.qualifier_defaults)
}

export default function QualifierEditor(
  { rule, vocab, onChange }: { rule: QualifierRule; vocab: Vocab; onChange: (p: QualifierPatch) => void },
) {
  const d = vocab.qualifier_defaults
  if (!d) return null
  const measures = vocab.qualifier_measures || []
  const scopes = vocab.qualifier_scopes || []
  const on = hasQualifier(rule)
  // The payout kind decides whether a flat fallback can even be expressed: `_line_payout` prices a
  // %-of-basis rule off `pct`, so substituting a flat amount would change nothing while this screen
  // claimed a lesser rate. The engine refuses that pairing and reports it; the editor says so here
  // rather than letting the figure be typed and silently ignored.
  const flatRule = String(rule?.payout_kind || '') === 'flat_per_unit' || String(rule?.payout_kind || '') === 'flat'

  return (
    <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'flex-end', padding: '4px 2px' }}>
      <label style={lab}>Minimum on the sale
        <input style={{ ...box, width: 90 }} type="number" step="0.01" min="0" placeholder="e.g. 24"
          value={rule.qualifier_min ?? ''}
          onChange={e => onChange({ qualifier_min: e.target.value })} />
      </label>

      <label style={lab}>measured as
        <select style={{ ...box, width: 180 }} value={rule.qualifier_measure || d.measure}
          onChange={e => onChange({ qualifier_measure: e.target.value })}>
          {measures.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
      </label>

      <label style={lab}>over
        <select style={{ ...box, width: 140 }} value={rule.qualifier_scope || d.scope}
          onChange={e => onChange({ qualifier_scope: e.target.value })}>
          {scopes.map(sc => <option key={sc.value} value={sc.value}>{sc.label}</option>)}
        </select>
      </label>

      <label style={lab}>counting which lines
        <select style={{ ...box, width: 160 }} value={rule.qualifier_match_field || d.match_field}
          title="Which sibling lines on the sale count toward the minimum."
          onChange={e => onChange({ qualifier_match_field: e.target.value })}>
          {/* The accessory answer is the tenant's OWN curated accessory definition, read through
              plan_pay_gate.accessory_predicate — the one the money path already honours. */}
          <option value={d.match_field}>accessories (your accessory definition)</option>
          {vocab.match_fields.filter(f => f.value !== 'any' && f.value !== d.match_field)
            .map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
        </select>
      </label>

      {String(rule.qualifier_match_field || d.match_field) !== d.match_field && (
        <label style={lab}>whose value is
          <input style={{ ...box, width: 140 }} placeholder="value"
            value={rule.qualifier_match_value ?? ''}
            onChange={e => onChange({ qualifier_match_value: e.target.value })} />
        </label>
      )}

      {/* A NON-DEFAULT FIELD WITH NO VALUE IS THE ONE WAY TO DOCK PAY BY ACCIDENT HERE. The engine
          falls back to the default value for the accessory flag, which matches NOTHING against
          another field — so every sale would measure zero, fall short, and pay the lesser rate with
          nothing on screen saying why. Said here, where the choice is made. */}
      {on && String(rule.qualifier_match_field || d.match_field) !== d.match_field
          && !String(rule.qualifier_match_value || '').trim() && (
        <div style={{ fontSize: 11, color: '#b45309', maxWidth: 260 }}>
          Enter a value. This field is not the accessory flag, so with nothing to match, every sale
          measures zero, falls short, and pays the lesser rate.
        </div>
      )}

      <label style={lab}>falls back to
        <input style={{ ...box, width: 90 }} type="number" step="0.01" placeholder="$ (blank = $0)"
          disabled={!flatRule}
          title={flatRule
            ? 'The lesser flat rate a line earns when its sale falls short. Blank means it pays nothing, which is stated on the pay detail rather than implied.'
            : 'A flat fallback cannot be applied to a %-of-basis rule: the engine prices those off the percentage, so the figure would be stored and never read. Declare the lesser rate as its own rule instead.'}
          value={rule.qualifier_fallback_amount ?? ''}
          onChange={e => onChange({ qualifier_fallback_amount: e.target.value })} />
      </label>

      {on && !flatRule && (
        <div style={{ fontSize: 11, color: '#b45309', maxWidth: 260 }}>
          This rule pays a percentage, so a lesser flat rate cannot be substituted. The engine reports
          the pairing instead of changing the pay.
        </div>
      )}

      {on && (
        <div style={{ fontSize: 11, color: 'var(--text3)', maxWidth: 420 }}>
          {/* DERIVED by attach_qualifier.describe on the GET. Blank until the plan is saved and
              reloaded, because the sentence is built from the STORED figures, not from this draft. */}
          {rule.qualifier_requirement
            ? <><b>On screen:</b> {rule.qualifier_requirement}</>
            : 'Save the plan to see the sentence reps will read.'}
        </div>
      )}

      {on && (
        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 6px' }}
          title="Remove the condition. This rule goes back to paying its full rate on every matched line."
          onClick={() => onChange({
            qualifier_min: '', qualifier_measure: '', qualifier_scope: '',
            qualifier_match_field: '', qualifier_match_op: '', qualifier_match_value: '',
            qualifier_fallback_amount: '', qualifier_label: '',
          })}>clear</button>
      )}
    </div>
  )
}
