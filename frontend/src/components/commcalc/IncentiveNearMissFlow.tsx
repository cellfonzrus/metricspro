'use client'
/**
 * THE GATED-PAYOUT WALKTHROUGH (§COMMCALC-81) — what a day nearly earned, drawn rather than told.
 *
 * Owner, 2026-11-18: *"we need to drive that behavior with constant coaching, add that under the
 * employee coaching module all across and make it a guided flow for them to understand which is in
 * picture rather than simple boring lingo."*
 *
 * THE ARGUMENT THIS HAS TO LAND, in one picture: a threshold gate does not withhold the pay on the
 * units you were short of. It withholds it on EVERY UNIT YOU ALREADY SOLD. So the bar below always
 * shows the work already done in full, with the small missing piece beside it and the whole bar's
 * worth of money underneath — because a rep who sees "you were 2 short, here is $0.00" learns
 * nothing, and a rep who sees "you did 3, two more would have paid for all five" acts tomorrow.
 *
 * THIS COMPONENT COMPUTES NOTHING. Every figure, every step and every sentence is the server's, out
 * of `incentive_shortfall` through one assembly (`_incentive_shortfall_payload`). There is no
 * threshold, no subtraction and no money arithmetic here: a second arithmetic beside the engine's is
 * how a screen comes to disagree with the payout it is explaining, and the action plan renders the
 * same items from the same source for the same reason.
 *
 * NO TENANT'S WORDS ARE IN HERE EITHER. Every category name on screen is the tenant's own, read off
 * their declared catalog server-side (RULE TWO, and the 2026-10-30 cross-tenant directive).
 */
import { useState } from 'react'
import { fmt } from '@/lib/client'

export type ShortfallStep = {
  n: number; step: string; title: string; line: string
  figure: number; unit: string; of: number | null
  detail?: { label: string; units: number; amount: number }[]
}
export type ShortfallItem = {
  severity: string; metric: string; title: string; detail: string
  shortfall_pct: number | null
  incentive: {
    rule: string; rule_id: string; kind: string; employee: string | null
    forgone: number
    shortfall: { basis: string; value: number; threshold: number; short_by: number
                 unit: string; scope: string } | null
    did: { key: string; label: string; units: number; amount: number }[]
    steps: ShortfallStep[]
  }
}
export type NearMissPayload = {
  period?: string
  sentence?: string
  empty_reason?: string
  summary?: {
    days_missed: number; forgone: number; forgone_within_reach: number
    by_rule: { rule: string; days: number; days_within_reach: number
               forgone: number; forgone_within_reach: number }[]
    by_employee: { employee: string; days: number; days_within_reach: number; forgone: number }[]
    blocked: { rule: string; reason: string; date?: string }[]
    closest: ({ date?: string; store_code?: string } & ShortfallItem['incentive']) | null
  }
  days?: { date: string; store_code: string; items: any[] }[]
}

const card: React.CSSProperties = {
  border: '1px solid var(--border)', borderRadius: 10, background: 'var(--surface)',
  padding: 14, marginBottom: 12,
}
const dot = (active: boolean, done: boolean): React.CSSProperties => ({
  width: 26, height: 26, borderRadius: 13, display: 'grid', placeItems: 'center',
  fontSize: 12, fontWeight: 700, cursor: 'pointer', flexShrink: 0,
  border: '1px solid var(--border)',
  background: active ? 'var(--green)' : done ? 'var(--c-bg-f0fdf4)' : 'var(--surface2)',
  color: active ? 'var(--c-fg-ffffff)' : done ? 'var(--c-fg-166534)' : 'var(--text3)',
})

/** The figure as the unit the server said it is. No rounding and no formatting decisions of ours. */
function figureText(v: number, unit: string) {
  return unit === 'money' ? fmt(v) : String(Math.round(v))
}

/**
 * THE PICTURE. Two segments on one track: what was sold, and what was missing. The sold segment is
 * drawn FULL WIDTH of what it was — never scaled down to look like a failure — because the whole
 * point is that the work was real and went unpaid.
 */
function Bar({ done, gap, of }: { done: number; gap: number; of: number }) {
  const total = of > 0 ? of : done + gap
  const pct = (v: number) => (total > 0 ? Math.max(0, Math.min(100, (v / total) * 100)) : 0)
  return (
    <div style={{ display: 'flex', height: 22, borderRadius: 11, overflow: 'hidden',
                  border: '1px solid var(--border)', background: 'var(--surface2)' }}>
      <div style={{ width: `${pct(done)}%`, background: 'var(--green)' }} />
      <div style={{ width: `${pct(gap)}%`, background: 'var(--c-bg-fffbeb)',
                    borderLeft: '1px dashed var(--c-fg-b45309)' }} />
    </div>
  )
}

function Walkthrough({ item }: { item: ShortfallItem }) {
  const steps = item.incentive?.steps || []
  const [at, setAt] = useState(0)
  if (steps.length === 0) return null
  const s = steps[Math.min(at, steps.length - 1)]
  const sf = item.incentive?.shortfall
  const done = sf ? sf.value : 0
  const gap = sf ? sf.short_by : 0
  const of = sf ? sf.threshold : 0

  return (
    <div style={{ marginTop: 10, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        {steps.map((st, i) => (
          <div key={st.n} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button onClick={() => setAt(i)} style={dot(i === at, i < at)} aria-label={st.title}>
              {i < at ? '✓' : st.n}
            </button>
            {i < steps.length - 1 && <div style={{ width: 18, height: 1, background: 'var(--border)' }} />}
          </div>
        ))}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: 'var(--text3)' }}>Step {at + 1} of {steps.length}</span>
      </div>

      <div style={{ fontSize: 12, color: 'var(--text2)', fontWeight: 600, marginBottom: 4 }}>{s.title}</div>
      <div style={{ fontSize: 30, fontWeight: 700, lineHeight: 1.1, marginBottom: 10 }}>
        {figureText(s.figure, s.unit)}
        {s.of !== null && s.unit !== 'money' &&
          <span style={{ fontSize: 15, fontWeight: 500, color: 'var(--text3)' }}> of {Math.round(s.of)}</span>}
      </div>

      {/* The bar is on every step, and the step decides what is emphasised — one picture the rep
          watches change, rather than four unrelated pictures. */}
      {sf && <Bar done={s.step === 'gate' ? of : done} gap={s.step === 'did' ? 0 : gap} of={of} />}

      <p style={{ fontSize: 14, margin: '10px 0 0', color: 'var(--text)' }}>{s.line}</p>

      {(s.detail || []).length > 0 && (
        <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 13, color: 'var(--text2)' }}>
          {(s.detail || []).map(d => (
            <li key={d.label}>
              {d.label}: <strong>{d.units > 0 ? d.units : fmt(d.amount)}</strong>
            </li>
          ))}
        </ul>
      )}

      <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
        <button className="btn btn-sm" disabled={at === 0} onClick={() => setAt(a => Math.max(0, a - 1))}>Back</button>
        {at < steps.length - 1
          ? <button className="btn btn-sm btn-primary" onClick={() => setAt(a => a + 1)}>Next</button>
          : <button className="btn btn-sm" onClick={() => setAt(0)}>Start again</button>}
      </div>
    </div>
  )
}

/**
 * ONE near miss, collapsed to its headline until somebody opens it. The headline is the server's
 * `title` and the money is the server's `forgone`, so a card can never say a different number from
 * the plan item built out of the same object.
 */
export function NearMissCard({ item, defaultOpen = false }: { item: ShortfallItem; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const inc = item.incentive || ({} as ShortfallItem['incentive'])
  const near = item.severity === 'critical'
  return (
    <div style={{ ...card, background: near ? 'var(--c-bg-fffbeb)' : 'var(--surface)' }}>
      <div onClick={() => setOpen(o => !o)} style={{ cursor: 'pointer', display: 'flex', gap: 10, alignItems: 'baseline' }}>
        <span style={{ fontSize: 16 }}>{near ? '🎯' : '📉'}</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 700 }}>{item.title}</div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
            {inc.employee ? `${inc.employee} · ` : ''}{inc.rule}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: near ? 'var(--c-fg-b45309)' : 'var(--text2)' }}>
            {fmt(inc.forgone || 0)}
          </div>
          <div style={{ fontSize: 10, color: 'var(--text3)' }}>unpaid</div>
        </div>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{open ? '▾' : '▸'}</span>
      </div>
      {open && <Walkthrough item={item} />}
      {open && (
        <p style={{ fontSize: 12, color: 'var(--text2)', margin: '12px 0 0', fontStyle: 'italic' }}>
          {item.detail}
        </p>
      )}
    </div>
  )
}

/**
 * THE WHOLE SECTION for the coaching screen. Renders nothing at all when the tenant declares no
 * gated rule — a business whose payouts have no threshold has no near miss to be coached on, and an
 * empty panel on their screen would be a permanent unanswered question.
 *
 * But when there IS a gated rule and still nothing to show, it SAYS WHY. A coaching surface that is
 * blank because the month was never uploaded looks exactly like a store that missed nothing, and one
 * of those is a data defect while the other is good news.
 */
export default function IncentiveNearMissFlow({ data, repFilter = '' }:
  { data: NearMissPayload | null; repFilter?: string }) {
  if (!data) return null
  const sum = data.summary
  const blocked = sum?.blocked || []
  const days = data.days || []
  const hasAny = days.length > 0 || blocked.length > 0
  if (!hasAny && !data.empty_reason) return null
  if (!hasAny && (data.empty_reason || '').startsWith('no rule')) return null

  // The items, newest day first, narrowed to one person when the page is. The server already decided
  // which day is the closest; this only chooses what is on screen.
  const want = repFilter.trim().toUpperCase()
  const items: { date: string; store: string; item: ShortfallItem }[] = []
  for (const d of [...days].reverse()) {
    for (const raw of d.items || []) {
      if (raw?.kind === 'blocked') continue
      if (want && String(raw?.employee || '').toUpperCase() !== want) continue
      // The plan item shape is built server-side; the card reads that, not the raw shortfall.
      const it = raw?.plan_item as ShortfallItem | undefined
      if (it) items.push({ date: d.date, store: d.store_code, item: it })
    }
  }

  return (
    <section style={{ marginBottom: 20 }}>
      <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 4px' }}>
        🎯 Pay they had already earned
      </h2>
      {data.sentence
        ? <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 12px', maxWidth: 820 }}>{data.sentence}</p>
        : <p style={{ fontSize: 13, color: 'var(--text3)', margin: '0 0 12px' }}>{data.empty_reason}</p>}

      {sum && sum.days_missed > 0 && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
          {[['Days that earned nothing', String(sum.days_missed)],
            ['Pay the gates withheld', fmt(sum.forgone)],
            ['Within one or two sales', fmt(sum.forgone_within_reach)]].map(([k, v]) => (
            <div key={k} style={{ ...card, marginBottom: 0, minWidth: 170, flex: '1 1 170px' }}>
              <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600 }}>{k}</div>
              <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>{v}</div>
            </div>
          ))}
        </div>
      )}

      {items.map(({ date, store, item }, i) => (
        <div key={`${date}-${store}-${item.incentive?.rule_id || i}`}>
          <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>
            {date}{store ? ` · ${store}` : ''}
          </div>
          <NearMissCard item={item} defaultOpen={i === 0} />
        </div>
      ))}

      {blocked.length > 0 && (
        <div style={{ ...card, background: 'var(--c-bg-fef2f2)' }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--c-fg-991b1b)' }}>
            ⚙️ {blocked.length} rule{blocked.length === 1 ? '' : 's'} could not be checked
          </div>
          <p style={{ fontSize: 12, color: 'var(--text2)', margin: '6px 0 0' }}>
            Nobody is coached on these, because the shortfall is not knowable until an admin finishes
            the rule. Until then the days they cover are not counted in the money above.
          </p>
          <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--text2)' }}>
            {blocked.slice(0, 8).map((b, i) => (
              <li key={`${b.rule}-${i}`}><strong>{b.rule}</strong>{b.date ? ` (${b.date})` : ''}: {b.reason}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
