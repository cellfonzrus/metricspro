'use client'
// ── "There are 2 ways to calculate employee commission" — ONE header, rendered on both surfaces ──
// Owner directive 2026-09-20 (verbatim in commissionWays.ts): the Executive-MTD flat way is Option 1 and
// sits on top; the customised step-by-step structure is Option 2 and sits below; the top of the page says
// there are two ways. Platform-wide: nothing here names a carrier, POS or tenant.
//
// The order and the copy are READ from commissionWays.COMMISSION_WAYS — this component never spells the
// header or an option title itself, and neither does a page. Links are ScreenLinks (self-gated by RBAC):
// the Executive MTD page for Option 1, and the page each feed of that report is uploaded on, which comes
// from GET /commcalc/report-kinds (`useReportKinds().feedsFor('exec_mtd')` — the inverse of `showsIn` over
// the same `shows_in` / `where` payload; no second link map).
//
// PRESENTATION ONLY. `selectedBasis` is the selected plan's persisted commission_basis (mig 298), shown
// read-only so the person can see which way that plan pays through today; the choice itself is still made
// on the plan editor's "Make Executive MTD this plan's commission basis" box, exactly as before.
import type { CSSProperties } from 'react'
import ScreenLink, { SCREENS, type ScreenKey } from '@/components/ScreenLink'
import { useReportKinds } from '@/lib/report-kinds'
import { COMMISSION_WAYS, COMMISSION_WAYS_HEADER, EXEC_MTD_FLAT, optionHeading, wayForBasis, type CommissionWay } from './commissionWays'

const numStyle: CSSProperties = {
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 24, height: 24, borderRadius: '50%',
  background: 'var(--accent, #2563eb)', color: '#fff', fontWeight: 700, fontSize: 12, flexShrink: 0,
}

function isScreenKey(k: string | undefined | null): k is ScreenKey {
  return !!k && Object.prototype.hasOwnProperty.call(SCREENS, k)
}

/**
 * "The Executive MTD counts what is uploaded as: <kind> under <page> · …" — where the data feeding
 * Option 1 is uploaded, derived from the report-kind registry's `shows_in` / `where` (never listed here).
 */
export function ExecMtdFeeds({ style }: { style?: CSSProperties }) {
  const { loaded, error, feedsFor } = useReportKinds()
  const screen = EXEC_MTD_FLAT.reads
  if (!screen) return null
  const feeds = feedsFor(screen)
  const lead = <>The <ScreenLink to={screen} /> counts what is uploaded as</>
  if (!loaded) return <div data-testid="exec-mtd-feeds" style={style}>{lead}: working out which uploads feed it…</div>
  if (error) return <div data-testid="exec-mtd-feeds" style={style}>{lead}: could not read the report-kind registry ({error}).</div>
  if (!feeds.length) {
    return (
      <div data-testid="exec-mtd-feeds" style={style}>
        {lead}: no report kind feeds it for your declared POS / carrier yet — define one under <ScreenLink to="onboarding_intake" />.
      </div>
    )
  }
  return (
    <div data-testid="exec-mtd-feeds" style={style}>
      {lead}:{' '}
      {feeds.map((f, i) => (
        <span key={f.key}>
          {i > 0 && ' · '}
          <b>{f.label}</b>
          {f.where && <> under {isScreenKey(f.where.screen) ? <ScreenLink to={f.where.screen}>{f.where.label}</ScreenLink> : f.where.label}</>}
        </span>
      ))}
    </div>
  )
}

/**
 * The header. `anchors` lets a surface point each option at its own section (the structure page's
 * cards by default; the plan editor points Option 1 at its basis box). `compact` is the one-paragraph
 * form for a surface whose body is not laid out as the two options.
 */
export default function CommissionWaysHeader({ selectedBasis, planName, anchors, compact = false, style }: {
  selectedBasis?: string | null
  planName?: string | null
  anchors?: Partial<Record<CommissionWay['key'], string>>
  compact?: boolean
  style?: CSSProperties
}) {
  const current = planName ? wayForBasis(selectedBasis) : null
  const hrefOf = (w: CommissionWay) => anchors?.[w.key] ?? `#${w.anchor}`
  return (
    <div className="card" data-testid="commission-ways-header" style={{ padding: 16, marginBottom: 14, borderLeft: '4px solid var(--accent, #2563eb)', ...style }}>
      <div style={{ fontWeight: 700, fontSize: compact ? 14 : 16, marginBottom: 8 }}>{COMMISSION_WAYS_HEADER}</div>
      <ol data-testid="commission-ways" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: compact ? 6 : 10 }}>
        {COMMISSION_WAYS.map(w => (
          <li key={w.key} data-way={w.key} data-basis={w.basis} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            <span style={numStyle}>{w.n}</span>
            <div style={{ fontSize: 13, lineHeight: 1.5 }}>
              <a href={hrefOf(w)} style={{ color: 'var(--accent)', fontWeight: 700 }}>{optionHeading(w)}</a>
              {!compact && (
                <div style={{ color: 'var(--text2)' }}>
                  {w.layman}
                  {w.reads && <> Open the <ScreenLink to={w.reads} /> to see those numbers.</>}
                </div>
              )}
              {!compact && w.reads && <ExecMtdFeeds style={{ color: 'var(--text2)', marginTop: 4 }} />}
            </div>
          </li>
        ))}
      </ol>
      {current && (
        <div data-testid="commission-ways-current" style={{ fontSize: 12, color: 'var(--text2)', marginTop: 10 }}>
          <b style={{ color: 'var(--text)' }}>{planName}</b> currently pays through <b>Option {current.n}</b> ({current.title}).
          That choice is saved on the plan — change it with the “Make Executive MTD this plan’s commission basis” box on <ScreenLink to="incentive_plans" />.
        </div>
      )}
      {compact && (
        <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 8 }}>
          Set either one up step by step on <ScreenLink to="commission_structure" />.
        </div>
      )}
    </div>
  )
}
