'use client'
// MANAGEMENT WATCHDOG — the board (owner ask 2026-10-05: "keep these reports in management dashboard
// under different reports so it is easy for the management to review each area and take appropriate
// action, name it Management Watch dog").
//
// ONE PAGE PER AREA, AND THE AREA LIST IS DERIVED, NEVER TYPED HERE. Every card below comes from
// GET /commcalc/watchdog/board, which builds it from the backend flag registry
// (app/modules/commcalc/flag_registry.py — the one home for "what kind of finding is this, how bad is
// it, and who reviews it"). So a new detector appears on this board by registering there, and this
// page can never drift from the rules. A hard-coded list here would be the second copy the index
// rules forbid.
//
// HOW THIS DIFFERS FROM /compliance, so nobody builds the same board twice:
//   /compliance  one row per QUEUE — ten different tables, ten different owners. "What is open?"
//   /watchdog    one page per AREA of the findings table. "Which part of the business do I go act on?"
//
// EVERY AREA IS SHOWN, even at zero, because a manager must be able to tell "nothing open in cash"
// from "cash is not watched". And an `unassigned` card appears only when a finding of an unregistered
// kind landed — counted and named rather than dropped, so the board is never read as complete when it
// is not.
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import PageIntro from '@/components/PageIntro'
import StatTile from '@/components/StatTile'

// The canonical four-level scale (app/modules/commcalc/flag_registry.SEVERITIES). The same four keys
// the Flags page colours on, so the two surfaces agree.
const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: '#dc2626', HIGH: '#d97706', MEDIUM: '#2563eb', LOW: '#64748b',
}

type Area = {
  area: string
  label: string
  blurb: string
  open_count: number
  worst_severity: string | null
  types: string[]
}
type Board = {
  period?: string
  as_of?: string
  total_open?: number
  areas?: Area[]
  unregistered_types?: string[]
}

export default function ManagementWatchdogPage() {
  const { period } = usePeriod()
  const [board, setBoard] = useState<Board | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    setErr('')
    api(`/api/v1/commcalc/watchdog/board?period=${encodeURIComponent(period || '')}`)
      .then((d: Board) => setBoard(d))
      .catch(e => { setErr(e?.message || String(e)); setBoard(null) })
      .finally(() => setLoading(false))
  }, [period])

  const areas = board?.areas || []

  return (
    <div>
      <PageIntro
        title="Management Watchdog"
        help={
          <>
            Every finding the platform raises, grouped by the part of the business you would go and
            act on. Each area opens its own report so one person can review it end to end.
            A finding stays here until somebody rules on it, and it clears itself the moment its
            cause is fixed — nothing is deleted, so the decision trail survives.
          </>
        }
      />

      {err && (
        <div style={{ border: '1px solid var(--red, #dc2626)', borderRadius: 8, padding: '10px 12px',
                      marginBottom: 14, fontSize: 13, color: 'var(--text2)' }}>
          The watchdog board could not be loaded: {err}
          {/* Deliberately NOT rendered as "0 findings" — a failed read and a clean estate must never
              look the same (the fake-zero rule this house applies everywhere). */}
        </div>
      )}

      {loading && !board && <p style={{ color: 'var(--text2)', fontSize: 13 }}>Loading…</p>}

      {board && (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
            <StatTile label="Open findings" value={board.total_open ?? 0} hero
                      sub={board.period ? `for ${board.period}` : undefined} />
            <StatTile label="Areas watched" value={(board.areas || []).length}
                      sub="each with its own report" />
          </div>

          {!!(board.unregistered_types || []).length && (
            <div style={{ border: '1px dashed var(--border)', borderRadius: 8, padding: '10px 12px',
                          marginBottom: 14, fontSize: 13, color: 'var(--text2)' }}>
              <strong>Some findings have no area yet.</strong> These kinds arrived from a check that
              has not been registered: {(board.unregistered_types || []).join(', ')}. They are counted
              under <em>Unclassified</em> below rather than hidden, so this board is not read as
              complete when it is not.
            </div>
          )}

          <div style={{ display: 'grid', gap: 12,
                        gridTemplateColumns: 'repeat(auto-fill, minmax(290px, 1fr))' }}>
            {areas.map(a => {
              const colour = a.worst_severity ? SEVERITY_COLORS[a.worst_severity] : undefined
              return (
                <Link key={a.area} href={`/watchdog/${a.area}`} style={{ textDecoration: 'none' }}>
                  <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14,
                                height: '100%', display: 'flex', flexDirection: 'column', gap: 8,
                                borderLeft: `3px solid ${colour || 'var(--border)'}` }}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                      <span style={{ fontSize: 15, fontWeight: 650, color: 'var(--text)' }}>
                        {a.label}
                      </span>
                      <span style={{ marginLeft: 'auto', fontSize: 20, fontWeight: 700,
                                     fontVariantNumeric: 'tabular-nums', color: 'var(--text)' }}>
                        {a.open_count}
                      </span>
                    </div>
                    {a.worst_severity && (
                      <span style={{ alignSelf: 'flex-start', fontSize: 10, fontWeight: 700,
                                     letterSpacing: '0.4px', color: colour,
                                     background: (colour || '#64748b') + '18',
                                     padding: '2px 7px', borderRadius: 999 }}>
                        {a.worst_severity}
                      </span>
                    )}
                    <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.45, color: 'var(--text2)' }}>
                      {a.blurb}
                    </p>
                    {/* "Nothing open" is said in words, because a bare 0 reads as "not measured". */}
                    {a.open_count === 0 && (
                      <span style={{ fontSize: 12, color: 'var(--text3)' }}>
                        Nothing open — this area is watched and clear.
                      </span>
                    )}
                  </div>
                </Link>
              )
            })}
          </div>

          <div style={{ marginTop: 18, fontSize: 13 }}>
            <Link href="/watchdog/void-register" style={{ color: 'var(--blue, #2563eb)' }}>
              Open the void &amp; return register →
            </Link>
            <span style={{ color: 'var(--text2)', marginLeft: 8 }}>
              every reversed or returned line for the period, with who rang it
            </span>
          </div>
        </>
      )}
    </div>
  )
}
