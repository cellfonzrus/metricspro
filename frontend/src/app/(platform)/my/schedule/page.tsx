'use client'
// MY SCHEDULE — the employee's own published schedule, for the next week or fortnight.
//
// OWNER (2026-11-27): *"under employee login it does not show the employee thier own schdule"* and,
// clarifying: *"it shows your next shift at the bottom on the main page it shoudl also show in
// schedule whic his made for teh next 2 weeks or one week"*.
//
// WHAT WAS WRONG. A tile's link is `pages[0]` of its surface in `backend/app/core/rep_surface.py`,
// and the schedule surface's first page was `/storeops/timeoff` — so the tile labelled "My schedule"
// opened the time-off REQUEST FORM. The only schedule an employee could see was the four-row "Your
// next shifts" list inlined on `/my`. This page is the missing read; the tile now opens it.
//
// THIS IS NOT THE SCHEDULE EDITOR. `/storeops/schedule` is the manager's builder (whole-roster grid,
// shift writes, hours-trend report) and stays refused to a confined employee. Nothing here writes.
//
// WHOSE SHIFTS THESE ARE IS THE SERVER'S ANSWER. `GET /storeops/shifts?mine=1` — the SAME read the
// grid uses, narrowed by the token (`own_emp_ids`). This page deliberately does NOT filter rows by
// comparing an employee id, which is the defect it was built alongside: a shift created on the
// Schedule page stores the numeric `employees.id` ("45") while a login carries the business id
// ("E45"), so a browser-side comparison on one form answers ZERO and reads as "nothing scheduled".
import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api, addDays, parseLocalDate } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { shiftHours } from '../../storeops/lib/scheduleExport'

type Shift = {
  id?: number
  shift_date?: string
  start_time?: string
  end_time?: string
  store_code?: string
  status?: string
  scheduled_hours?: number
}

// The two windows the owner named, as DAY COUNTS from today — a rolling look-ahead, not a work-week
// grid. A person reading their own schedule on a phone asks "what am I working next", and a rolling
// window answers that on any day of the week without needing the tenant's work-week start.
const WINDOWS = [
  { days: 7, label: 'Next 7 days' },
  { days: 14, label: 'Next 14 days' },
] as const

function iso(d: Date) {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function dayLabel(date: string) {
  const d = parseLocalDate(date)
  return {
    dow: d.toLocaleDateString('en-US', { weekday: 'long' }),
    md: d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
  }
}

const card: React.CSSProperties = {
  border: '1px solid var(--border)', background: 'var(--surface)', borderRadius: 14, padding: 14,
}

export default function MySchedulePage() {
  const [days, setDays] = useState<number>(14)
  // ONE state object, written ONLY from the fetch's own callbacks, and it CARRIES THE WINDOW it
  // answers for. "Loading" is then DERIVED (the answer in hand is for another window) rather than
  // set — an effect body that calls setState synchronously to clear the old rows triggers the
  // cascading render `react-hooks/set-state-in-effect` fails the build over.
  const [loaded, setLoaded] = useState<{ days: number; rows: Shift[]; err: string } | null>(null)

  const today = useMemo(() => iso(new Date()), [])
  const until = useMemo(() => addDays(today, days - 1), [today, days])

  useEffect(() => {
    let cancelled = false
    api(`/api/v1/storeops/shifts?week_start=${today}&week_end=${until}&mine=1`)
      .then((rows: unknown) => {
        if (cancelled) return
        setLoaded({ days, rows: Array.isArray(rows) ? (rows as Shift[]) : [], err: '' })
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setLoaded({ days, rows: [], err: e instanceof Error ? e.message : String(e) })
      })
    return () => { cancelled = true }
  }, [today, until, days])

  const settled = loaded && loaded.days === days
  const shifts = settled ? loaded.rows : null
  const err = settled ? loaded.err : ''

  // One entry per DAY in the window, so a day with nothing scheduled says so rather than being
  // absent — "am I off on Thursday" is the other half of the question this page answers.
  const byDay = useMemo(() => {
    const out: { date: string; rows: Shift[] }[] = []
    for (let i = 0; i < days; i++) {
      const d = addDays(today, i)
      out.push({
        date: d,
        rows: (shifts || [])
          .filter(s => String(s.shift_date || '').slice(0, 10) === d)
          .sort((a, b) => String(a.start_time).localeCompare(String(b.start_time))),
      })
    }
    return out
  }, [shifts, today, days])

  // Hours are the SHARED calculation (overnight-aware), never a second one: the grid, the emailed
  // PDF and this page must not disagree about how long a shift is.
  const totalHours = useMemo(
    () => (shifts || []).reduce((t, s) => t + shiftHours(s), 0), [shifts])
  const workingDays = byDay.filter(d => d.rows.length).length

  const exportPayload = (): ExportPayload => ({
    title: 'My schedule',
    subtitle: `${today} to ${until}`,
    filename: `my-schedule-${today}`,
    sheets: [{
      name: 'My schedule',
      columns: [
        { header: 'Day', get: r => r.day },
        { header: 'Date', get: r => r.date, type: 'date' },
        { header: 'Times', get: r => r.times },
        { header: 'Store', get: r => r.store },
        { header: 'Hours', get: r => r.hours, align: 'right', type: 'number' },
      ],
      rows: byDay.flatMap(d => d.rows.length
        ? d.rows.map(s => ({
            day: dayLabel(d.date).dow, date: d.date,
            times: [s.start_time, s.end_time].filter(Boolean).join(' – '),
            store: s.store_code || '', hours: Number(shiftHours(s).toFixed(2)),
          }))
        : [{ day: dayLabel(d.date).dow, date: d.date, times: 'Off', store: '', hours: 0 }]),
    }],
  })

  return (
    <div style={{ maxWidth: 760, margin: '0 auto' }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <h1 style={{ margin: '0 0 4px', fontSize: 24 }}>My schedule</h1>
        <Link href="/my" style={{ fontSize: 13, color: 'var(--text3)' }}>← My day</Link>
      </div>
      <p style={{ margin: '0 0 16px', color: 'var(--text3)', fontSize: 14 }}>
        The shifts published for you. Ask your manager for a change — this page does not edit it.
      </p>

      {/* THE WINDOW — the owner's "one week or two", and the only filter a one-person schedule has:
          a store or market picker would be a choice with one option, since these rows are already
          this person's wherever they worked them. */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center',
                    marginBottom: 14 }}>
        {WINDOWS.map(w => (
          <button key={w.days} onClick={() => setDays(w.days)}
                  style={{ padding: '8px 14px', borderRadius: 999, fontSize: 14, fontWeight: 600,
                           cursor: 'pointer',
                           border: `1px solid ${days === w.days ? 'transparent' : 'var(--border)'}`,
                           background: days === w.days ? 'var(--c-bg-059669)' : 'var(--surface)',
                           color: days === w.days ? 'var(--c-fg-ffffff)' : 'inherit' }}>
            {w.label}
          </button>
        ))}
        <span style={{ flex: 1 }} />
        {/* EXPORT, NOT SEND. The Send bar would need `/api/v1/notify/send-file` granted to a
            confined employee — an outbound-mail capability riding in on a page, which is exactly the
            class §CORE-33's security pass exists to catch. Export renders in the browser and logs
            through `/core/export-event`, which this surface already has. */}
        <ExportButtons payload={exportPayload} compact />
      </div>

      {err && (
        <div style={{ ...card, marginBottom: 12, fontSize: 13, color: 'var(--c-fg-b45309)' }}>
          Your schedule could not be loaded just now. {err}
        </div>
      )}

      {shifts !== null && !err && (
        <div style={{ ...card, marginBottom: 12, fontSize: 14 }}>
          <strong>{totalHours.toFixed(2)} hours</strong> scheduled over {workingDays}{' '}
          {workingDays === 1 ? 'day' : 'days'} in the next {days} days.
        </div>
      )}

      {shifts === null && <div style={{ color: 'var(--text3)', fontSize: 13 }}>Loading…</div>}

      {shifts !== null && (
        <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'grid', gap: 8 }}>
          {byDay.map(d => {
            const { dow, md } = dayLabel(d.date)
            const off = d.rows.length === 0
            return (
              <li key={d.date} style={{ ...card, padding: 12, opacity: off ? 0.62 : 1,
                                         display: 'flex', gap: 12, flexWrap: 'wrap',
                                         alignItems: 'center' }}>
                <div style={{ minWidth: 112 }}>
                  <div style={{ fontWeight: 700, fontSize: 15 }}>{dow}</div>
                  <div style={{ color: 'var(--text3)', fontSize: 12.5 }}>{md}</div>
                </div>
                <div style={{ flex: 1, minWidth: 180, display: 'grid', gap: 4 }}>
                  {off && <span style={{ color: 'var(--text3)', fontSize: 14 }}>Not scheduled</span>}
                  {d.rows.map((s, i) => (
                    <div key={s.id ?? i} style={{ display: 'flex', gap: 10, flexWrap: 'wrap',
                                                  fontSize: 14, alignItems: 'baseline' }}>
                      <strong>{[s.start_time, s.end_time].filter(Boolean).join(' – ') || '—'}</strong>
                      {s.store_code && (
                        <span style={{ color: 'var(--text3)' }}>{s.store_code}</span>
                      )}
                      <span style={{ color: 'var(--text3)', fontSize: 12.5 }}>
                        {shiftHours(s).toFixed(2)} h
                      </span>
                      {s.status && s.status !== 'scheduled' && (
                        <span style={{ fontSize: 12, padding: '1px 8px', borderRadius: 999,
                                       border: '1px solid var(--border)' }}>{s.status}</span>
                      )}
                    </div>
                  ))}
                </div>
              </li>
            )
          })}
        </ul>
      )}

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 16, fontSize: 14 }}>
        <Link href="/storeops/timeoff">Ask for time off</Link>
        <Link href="/storeops/swaps">Ask for a shift swap</Link>
      </div>
    </div>
  )
}
