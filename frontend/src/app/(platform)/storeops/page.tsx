'use client'
// Workforce tiled dashboard (Phase W2, owner directive 2026-09-01). Replaces the old small LINKS
// landing with the full tile hub — the side-menu entries stay as the secondary path. Keeps the
// StatTile KPI row and extends it with pending-work counts (time off, swaps, time-clock
// permissions), each best-effort off an endpoint that already exists — a failed count never blocks
// the render. Tile taxonomy is the owner's spec: Schedule · Shift Approvals · Attendance (incl. the
// page renamed 'Lateness %') · Employees · Reports · Store Setup + Employee Setup as SEPARATE tiles
// (the /storeops/admin split).
import { useState, useEffect } from 'react'
import { api } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import Link from 'next/link'
import StatTile from '@/components/StatTile'

function weekRange() {
  const d = new Date()
  const day = d.getDay()
  const mon = new Date(d); mon.setDate(d.getDate() - (day === 0 ? 6 : day - 1))
  const sun = new Date(mon); sun.setDate(mon.getDate() + 6)
  const iso = (x: Date) => x.toISOString().slice(0, 10)
  return { start: iso(mon), end: iso(sun) }
}

type Item = { href: string; icon: string; label: string; desc: string }
type Group = { title: string; desc: string; items: Item[] }

const GROUPS: Group[] = [
  {
    title: 'Schedule',
    desc: 'Build the week and manage every change to it.',
    items: [
      { href: '/storeops/schedule', icon: '📅', label: 'Schedule', desc: 'Build and edit weekly shifts — the week defaults to the current pay period.' },
      { href: '/storeops/timeoff', icon: '🌴', label: 'Time Off', desc: 'Requests & approvals — PTO, sick, unpaid.' },
      { href: '/storeops/swaps', icon: '🔄', label: 'Shift Swaps', desc: 'Swap requests & approvals; approving reassigns the shift(s).' },
      { href: '/storeops/shift-extensions', icon: '⏱️', label: 'Shift Extensions', desc: 'Keep someone past scheduled end — DM approves ahead of time.' },
      { href: '/storeops/hours-budget', icon: '📊', label: 'Hours Budget', desc: 'Weekly hours limits per store, and who is over them.' },
      { href: '/storeops/staffing', icon: '🔥', label: 'Staffing Heat Map', desc: 'Demand by hour → staff required vs scheduled.' },
    ],
  },
  {
    title: 'Shift Approvals',
    desc: 'Time that needs a manager\'s tick before it counts.',
    items: [
      { href: '/storeops/timeclock-permissions', icon: '⏳', label: 'Time-clock Permissions', desc: 'Re-clock-ins and extra time past shift end, held until approved.' },
      { href: '/storeops/timeclock', icon: '🕐', label: 'Time Clock', desc: 'Clock punches and day totals per store.' },
    ],
  },
  {
    title: 'Attendance',
    desc: 'Who showed up, and on time.',
    items: [
      { href: '/storeops/attendance', icon: '🚨', label: 'Attendance Exceptions', desc: 'No-shows, missed punches and other exceptions.' },
      { href: '/storeops/accountability', icon: '🎓', label: 'Lateness %', desc: 'Lateness & attendance patterns per employee — coaching material, with dates and times.' },
    ],
  },
  {
    title: 'Employees',
    desc: 'The roster and your own span.',
    items: [
      { href: '/storeops/employees', icon: '👥', label: 'Employees', desc: 'The employee roster — edit inline, deactivate, merge duplicates.' },
      { href: '/storeops/team', icon: '🫂', label: 'My Team', desc: 'Performance for every store and rep under you.' },
    ],
  },
  {
    title: 'Reports',
    desc: 'Hours & payroll rollups, and the whole Report Center.',
    items: [
      { href: '/storeops/reports', icon: '📋', label: 'Hours & Payroll Reports', desc: 'Hours & payroll, by employee/store, for any pay period.' },
      { href: '/reports', icon: '📊', label: 'Report Center', desc: 'All the reports you have access to.' },
    ],
  },
  {
    title: 'Store Setup',
    desc: 'The stores themselves.',
    items: [
      { href: '/storeops/setup/stores', icon: '🏬', label: 'Store Setup', desc: 'Store codes, addresses, markets, time zones, targets — plus bulk upload.' },
    ],
  },
  {
    title: 'Employee Setup',
    desc: 'The people records behind scheduling.',
    items: [
      { href: '/storeops/setup/employees', icon: '🧑‍🔧', label: 'Employee Setup', desc: 'Names, IDs, home stores, contact info, active flags — plus bulk upload.' },
    ],
  },
]

export default function WorkforceDashboard() {
  const [emps, setEmps] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [timeoff, setTimeoff] = useState<any[]>([])
  const [shifts, setShifts] = useState<any[]>([])
  // Pending-work counts (Phase W2) — each from an endpoint that already exists, each best-effort:
  // a 403/404/parse failure just leaves the count at null and its tile renders 0-styled, never a spinner.
  const [swapsPending, setSwapsPending] = useState<number | null>(null)
  const [clockPending, setClockPending] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const wk = weekRange()

  useEffect(() => {
    Promise.all([
      apiCached('/api/v1/storeops/employees', LOOKUP).catch(() => []),
      apiCached('/api/v1/storeops/stores', LOOKUP).catch(() => []),
      api('/api/v1/storeops/time-off').catch(() => []),
      api(`/api/v1/storeops/shifts?week_start=${wk.start}&week_end=${wk.end}`).catch(() => []),
    ]).then(([e, s, t, sh]) => { setEmps(e || []); setStores(s || []); setTimeoff(t || []); setShifts(sh || []) })
      .finally(() => setLoading(false))
    // Never block the page on these — they arrive whenever they arrive.
    api('/api/v1/storeops/shift-swaps')
      .then((sw: any) => setSwapsPending((Array.isArray(sw) ? sw : []).filter((x: any) => x.status === 'pending').length))
      .catch(() => {})
    api('/api/v1/storeops/timeclock/permissions')
      .then((r: any) => setClockPending(((r?.permissions || []) as any[]).filter((x: any) => x.status === 'pending').length))
      .catch(() => {})
  }, [])

  const activeStores = stores.filter(s => s.is_active).length
  const pendingTO = timeoff.filter(t => t.status === 'pending').length
  const weekHours = shifts.reduce((s, x) => s + (Number(x.scheduled_hours) || 0), 0)

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏠 Workforce</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Scheduling, approvals, attendance and the roster across your stores. Payroll has its own
          dashboard — <Link href="/payroll" style={{ color: 'var(--accent)' }}>open Payroll →</Link>
        </p>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>
      ) : (
        <>
          <div className="stat-grid" style={{ marginBottom: 22 }}>
            <StatTile label="Active employees" value={emps.length.toLocaleString()} accent="#2563eb" href="/storeops/employees" sub="View →" />
            <StatTile label="Active stores" value={activeStores.toLocaleString()} accent="#059669" href="/storeops/setup/stores" sub="View →" />
            <StatTile label="This week's scheduled hrs" value={weekHours.toFixed(1)} accent="#d97706" href="/storeops/schedule" sub="View →" />
            <StatTile label="Pending time off" value={pendingTO.toLocaleString()} accent={pendingTO ? '#dc2626' : '#6b7280'} href="/storeops/timeoff"
              sub="View →" delta={pendingTO ? { value: 'needs review', dir: 'down' } : undefined} />
            <StatTile label="Open shift swaps" value={(swapsPending ?? 0).toLocaleString()} accent={swapsPending ? '#dc2626' : '#6b7280'} href="/storeops/swaps"
              sub="View →" delta={swapsPending ? { value: 'needs review', dir: 'down' } : undefined} />
            <StatTile label="Time-clock approvals" value={(clockPending ?? 0).toLocaleString()} accent={clockPending ? '#dc2626' : '#6b7280'} href="/storeops/timeclock-permissions"
              sub="View →" delta={clockPending ? { value: 'needs review', dir: 'down' } : undefined} />
          </div>

          <div style={{ display: 'grid', gap: 22 }}>
            {GROUPS.map(g => (
              <section key={g.title}>
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 15, fontWeight: 700 }}>{g.title}</div>
                  <div style={{ fontSize: 12, color: 'var(--text3)' }}>{g.desc}</div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
                  {g.items.map(it => (
                    <Link key={it.href} href={it.href} className="card" style={{
                      padding: 14, display: 'flex', gap: 12, alignItems: 'flex-start',
                      textDecoration: 'none', color: 'inherit', border: '1px solid var(--border)',
                    }}>
                      <div style={{ fontSize: 22, lineHeight: 1 }}>{it.icon}</div>
                      <div>
                        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 2 }}>{it.label}</div>
                        <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.4 }}>{it.desc}</div>
                      </div>
                    </Link>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
