'use client'
// "Why $0" panel — the ONE read-only explanation both drill-downs render when a plan IS attached but
// its rules matched no sale line:
//   • /commcalc/commission-explain → "1 · Incentive-Plan component"
//   • /commcalc/reports → Individual Rep → 🔍 Plan commission modal
//
// Driven entirely by the explain response's `plan_component.zero_diagnosis` (the engine's OWN coverage
// plan_warnings for the attached plan + a per-0-match-rule field distribution over THIS rep's lines).
// It NARRATES only — no auto-fix button, no re-key: a blind re-key could pay the wrong lines, so the
// remediation is a HINT that links to the Incentive Plans editor. Renders nothing when there is no
// diagnosis, so a rep whose plan simply has no lines still shows today's plain message.

interface FieldDist {
  total?: number; blank?: number; blank_pct?: number
  top_values?: { value: string; count: number }[]
  computed_field?: boolean; note?: string
}
interface ZeroRule {
  label?: string; match_field?: string; match_op?: string; match_value?: string
  matched_lines?: number; field_distribution?: FieldDist
}
interface ZeroDiagnosis {
  warnings?: { plan?: string; code?: string; severity?: string; message?: string }[]
  rules?: ZeroRule[]
  rep_lines_total?: number
}

const sevColor = (s?: string) => s === 'high' ? 'var(--red)' : s === 'medium' ? '#b45309' : 'var(--text2)'

function topValuesText(fd?: FieldDist): string {
  const tv = fd?.top_values || []
  if (tv.length === 0) return 'none'
  return tv.map(t => `“${t.value}” (${t.count})`).join(', ')
}

export default function WhyZeroPanel({ zd }: { zd?: ZeroDiagnosis | null }) {
  const warnings = zd?.warnings || []
  const rules = zd?.rules || []
  if (warnings.length === 0 && rules.length === 0) return null

  return (
    <div style={{ border: '1px solid var(--border)', borderLeft: '4px solid var(--red)', borderRadius: 6,
      padding: 12, background: 'var(--surface2)', marginTop: 8 }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Why $0 — the plan attached but no rule matched a sale line</div>

      {/* engine-authored warning / remediation text for the attached plan */}
      {warnings.length > 0 && (
        <ul style={{ margin: '0 0 8px', paddingLeft: 18, fontSize: 12.5, lineHeight: 1.6 }}>
          {warnings.map((w, i) => (
            <li key={i} style={{ color: sevColor(w.severity), marginBottom: 4 }}>{w.message}</li>
          ))}
        </ul>
      )}

      {/* per-rule: what the rule expects vs. what THIS rep's lines actually carry */}
      {rules.length > 0 && (
        <div style={{ fontSize: 12.5, color: 'var(--text2)' }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Rules that matched nothing for this rep</div>
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
            {rules.map((r, i) => {
              const fd = r.field_distribution
              return (
                <li key={i} style={{ marginBottom: 4 }}>
                  Rule <b style={{ color: 'var(--text)' }}>{r.label || '(unnamed rule)'}</b> expects{' '}
                  <b>{r.match_field || 'any'}</b> {r.match_op || 'equals'} <b>“{r.match_value ?? ''}”</b>{' '}
                  — matched <b>{r.matched_lines || 0}</b> line{(r.matched_lines || 0) === 1 ? '' : 's'}.{' '}
                  {fd?.computed_field ? (
                    <span style={{ color: 'var(--text3)' }}>{fd.note || 'This is a computed field.'}</span>
                  ) : (
                    <>This rep’s <b>{r.match_field}</b>: blank on <b>{fd?.blank_pct ?? 0}%</b>
                    {' '}of {fd?.total ?? 0} line{(fd?.total ?? 0) === 1 ? '' : 's'}; present values: {topValuesText(fd)}.</>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {/* remediation HINT — links to the editor; NO auto-fix (a blind re-key could pay wrong lines) */}
      <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8, lineHeight: 1.6 }}>
        Re-key this rule to a field the lines actually carry (e.g. Activation Type / activation_bucket), or
        fix the accessory classification, on the{' '}
        <a href="/commcalc/commission-plans" style={{ color: 'var(--accent)' }}>Incentive Plans editor</a>.
        {' '}Nothing is changed automatically — this is a diagnosis only.
      </div>
    </div>
  )
}
