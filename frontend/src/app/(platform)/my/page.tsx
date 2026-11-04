'use client'
// MY DAY — the tiled landing a sales rep and a store manager land on when they sign in.
//
// OWNER (2026-11-04): *"Create a tiled front end for their clock in specifically so when the sales
// reps or the store manager logs in they land on that landing page."*
//
// THE TILES ARE NOT DECLARED HERE. They come from `/me`
// (`permissions.rep_surface.tiles`), built by `backend/app/core/rep_surface.py` — the SAME
// declaration the API middleware refuses on and the menu gates on. A second list in the browser is
// exactly how a rep ended up with a menu that disagreed with the server (index §CORE-33), so this
// page renders what it is given and decides nothing about who may reach what.
//
// NOR IS THE PUNCH. Clocking in is one home, the kiosk at `/portal` (selfie, GPS, the offline punch
// queue, the block-and-hold for an unscheduled tap). This page shows the LIVE status from
// `GET /storeops/timeclock/status` — the kiosk's own read — and the tile opens it. Rebuilding the
// punch here would be the duplicate the house rules forbid.
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { REP_LANDING } from '@/lib/rbac'

type ClockStatus = { clockedIn?: boolean; entry?: { clock_in?: string }; pending_permissions?: unknown[] }
type Tile = { key: string; label: string; icon: string; page?: string | null; what?: string }
type Shift = { shift_date?: string; start_time?: string; end_time?: string; store_code?: string; employee_id?: string }

const card: React.CSSProperties = {
  display: 'block', padding: '18px 16px', borderRadius: 14, border: '1px solid var(--border)',
  background: 'var(--surface)', textDecoration: 'none', color: 'inherit', minHeight: 104,
}

function iso(d: Date) { return d.toISOString().slice(0, 10) }

export default function MyDayPage() {
  const { user, permissions } = useAuth()
  const tiles: Tile[] = (permissions?.rep_surface?.tiles || []) as Tile[]
  const [status, setStatus] = useState<ClockStatus | null>(null)
  const [statusErr, setStatusErr] = useState('')
  const [shifts, setShifts] = useState<Shift[] | null>(null)

  useEffect(() => {
    api('/api/v1/storeops/timeclock/status')
      .then(setStatus)
      .catch((e: unknown) => setStatusErr(e instanceof Error ? e.message : String(e)))
  }, [])

  // Their own next shifts. The rows are narrowed SERVER-side by `schedule_emp_ids` (§14w), so this
  // asks for the window and filters to this person's own id rather than inventing a second rule
  // about whose schedule it may show.
  useEffect(() => {
    const today = new Date(); const end = new Date(); end.setDate(end.getDate() + 13)
    api(`/api/v1/storeops/shifts?week_start=${iso(today)}&week_end=${iso(end)}`)
      .then((rows: unknown) => {
        const mine = (Array.isArray(rows) ? rows : []).filter((r: Shift) =>
          !user?.employee_id || String(r.employee_id || '') === String(user.employee_id))
        setShifts(mine.slice(0, 4))
      })
      .catch(() => setShifts([]))
  }, [user?.employee_id])

  const clockedIn = !!status?.clockedIn
  const since = status?.entry?.clock_in
    ? new Date(status.entry.clock_in).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
    : ''

  return (
    <div style={{ maxWidth: 980, margin: '0 auto' }}>
      <h1 style={{ margin: '0 0 4px', fontSize: 24 }}>
        {user?.full_name ? `Hello, ${user.full_name.split(' ')[0]}` : 'My day'}
      </h1>
      <p style={{ margin: '0 0 18px', color: 'var(--text3)', fontSize: 14 }}>
        Start by clocking in. Everything that is yours is on this page.
      </p>

      {/* THE CLOCK — the first thing on the page, because it is the first thing of the day. */}
      <div style={{ ...card, padding: 20, marginBottom: 18,
                    borderColor: clockedIn ? 'var(--c-bg-16a34a)' : 'var(--border)' }}>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 34, lineHeight: 1 }}>⏱</span>
          <div style={{ flex: 1, minWidth: 220 }}>
            <div style={{ fontWeight: 700, fontSize: 18 }}>
              {status === null && !statusErr ? 'Checking the clock…'
                : clockedIn ? `You are clocked in${since ? ` since ${since}` : ''}`
                : 'You are not clocked in'}
            </div>
            <div style={{ color: 'var(--text3)', fontSize: 13, marginTop: 2 }}>
              {statusErr
                ? 'The clock could not be reached just now — open the clock-in screen to try there.'
                : clockedIn ? 'Open the clock-in screen when you are ready to clock out.'
                : 'Open the clock-in screen to clock in for your shift.'}
            </div>
          </div>
          <Link href="/portal" style={{ padding: '12px 20px', borderRadius: 10, border: 'none',
                 background: clockedIn ? 'var(--c-bg-f59e0b)' : 'var(--c-bg-059669)',
                 color: 'var(--c-fg-ffffff)', fontWeight: 700, fontSize: 16, textDecoration: 'none' }}>
            {clockedIn ? 'Clock out' : 'Clock in'}
          </Link>
        </div>
        {!!(status?.pending_permissions?.length) && (
          <div style={{ marginTop: 12, fontSize: 13, color: 'var(--c-fg-b45309)' }}>
            ⏳ Waiting on your manager&apos;s permission.
          </div>
        )}
      </div>

      {/* THE TILES — the server's list, in the server's order. */}
      <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))' }}>
        {tiles.filter(t => t.page && t.page !== REP_LANDING).map(t => (
          <Link key={t.key} href={t.page as string} style={card}>
            <div style={{ fontSize: 26, lineHeight: 1 }}>{t.icon}</div>
            <div style={{ fontWeight: 700, marginTop: 8 }}>{t.label}</div>
            {t.what && <div style={{ color: 'var(--text3)', fontSize: 12.5, marginTop: 4 }}>{t.what}</div>}
          </Link>
        ))}
      </div>

      {/* Their own next shifts, inline — a rep has no business on the manager's schedule editor, and
          this is the part of it that is theirs. */}
      <h2 style={{ fontSize: 16, margin: '22px 0 8px' }}>Your next shifts</h2>
      <div style={{ ...card, minHeight: 0, padding: 14 }}>
        {shifts === null && <div style={{ color: 'var(--text3)', fontSize: 13 }}>Loading…</div>}
        {shifts?.length === 0 && (
          <div style={{ color: 'var(--text3)', fontSize: 13 }}>
            Nothing scheduled for you in the next two weeks.
          </div>
        )}
        {!!shifts?.length && (
          <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'grid', gap: 6 }}>
            {shifts.map((s, i) => (
              <li key={i} style={{ display: 'flex', gap: 10, fontSize: 14, flexWrap: 'wrap' }}>
                <strong style={{ minWidth: 120 }}>
                  {s.shift_date ? new Date(s.shift_date + 'T12:00:00').toLocaleDateString('en-US',
                    { weekday: 'short', month: 'short', day: 'numeric' }) : ''}
                </strong>
                <span>{[s.start_time, s.end_time].filter(Boolean).join(' – ') || '—'}</span>
                {s.store_code && <span style={{ color: 'var(--text3)' }}>{s.store_code}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      {tiles.length === 0 && (
        <p style={{ marginTop: 20, color: 'var(--text3)', fontSize: 13 }}>
          This is the landing page for store staff. Your own role has the full menu, so nothing is
          tiled here for you.
        </p>
      )}
    </div>
  )
}
