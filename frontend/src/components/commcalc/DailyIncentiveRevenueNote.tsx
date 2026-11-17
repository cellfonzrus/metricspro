'use client'
/**
 * WHAT THE DAY'S REVENUE FIGURE INCLUDES — stated beside the figure (§COMMCALC-80).
 *
 * Owner ruling 2026-11-17, answering how his October report's unclassified product lines should
 * count toward the day: "Whole day's revenue counts".
 *
 * WHY THIS IS A COMPONENT AND NOT A LINE OF JSX ON THE PAGE. The money a report carried on lines no
 * declared category claimed is reported under BOTH scopes — so an operator whose business counts
 * only the classified categories can still SEE what it is leaving out, which is the whole reason the
 * $22,653.31 went unnoticed for a month. That is three states to render (counted / not counted /
 * cannot be declared yet) and one of them is easy to drop. One home renders all three.
 *
 * THIS COMPONENT COMPUTES NOTHING. Every figure and every sentence is the server's: `scopeNote` is
 * `revenue_scope.scope_sentence`, the money is `unclassified_found`, the options are the endpoint's.
 * There is no subtraction, no addition and no threshold here, because a second arithmetic beside the
 * engine's is how a screen comes to disagree with the payout it is describing.
 */
import { fmt } from '@/lib/client'

export type RevenueScopeFields = {
  revenue_scope?: string
  revenue_scope_declared?: string
  revenue_scope_note?: string
  revenue_scope_overridden?: boolean
  revenue_scope_declaration_pending?: boolean
  revenue_scope_options?: { value: string; label: string }[]
  unclassified_found?: number
  unclassified_found_rows?: number
}

export default function DailyIncentiveRevenueNote({ d }: { d?: RevenueScopeFields | null }) {
  if (!d?.revenue_scope_note) return null
  const rows = d.unclassified_found_rows || 0
  // The server decides whether this money is in the figure. The screen reads its answer; it does not
  // re-derive it from the scope string, which would be the second copy of the policy.
  const counted = (d.revenue_scope || '') === 'whole_day'
  return (
    <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: '8px 10px',
      fontSize: 12, marginTop: 12 }}>
      {d.revenue_scope_note}
      {d.revenue_scope_overridden && (
        <strong> You are looking at this on purpose — it is not what this business has declared,
          and nothing has been changed.</strong>
      )}
      {!!rows && !counted && (
        <div style={{ marginTop: 4 }}>
          Declaring that the whole day&apos;s takings count would add{' '}
          <strong>{fmt(d.unclassified_found || 0)}</strong> to this day. Mapping those{' '}
          {rows} line{rows === 1 ? '' : 's'} to a category instead gives each category its own
          figure, and would let them pay the rules that name those categories — which counting
          alone never does.
        </div>
      )}
      {!!rows && counted && (
        <div style={{ marginTop: 4, color: 'var(--text3)' }}>
          This money reaches the day&apos;s total and the share of whoever rang it. It can never pay
          a rule that names a category, because nothing has said which category it is.
        </div>
      )}
      {d.revenue_scope_declaration_pending && (
        <div style={{ marginTop: 4, color: 'var(--text3)' }}>
          ⚙️ This business has not been able to choose yet — the setting is not in the database
          (1126_revenue_scope.sql / commcalc.commission_org_config.declared_revenue_scope). Until it
          is, only the categories we can place count, which is what every company reads today.
        </div>
      )}
    </div>
  )
}
