'use client'
import { useState, useEffect } from 'react'
import { api } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import Link from 'next/link'

function weekRange() {
  const d = new Date()
  const day = d.getDay()
  const mon = new Date(d); mon.setDate(d.getDate() - (day === 0 ? 6 : day - 1))
  const sun = new Date(mon); sun.setDate(mon.getDate() + 6)
  const iso = (x: Date) => x.toISOString().slice(0, 10)
  return { start: iso(mon), end: iso(sun) }
}

const LINKS = [
  { href: '/storeops/schedule', icon: '📅', label: 'Schedule', desc: 'Build and edit weekly shifts' },
  { href: '/storeops/reports', icon: '📋', label: 'Hours & Payroll Reports', desc: 'Hours & payroll, by employee/store' },
  { href: '/reports', icon: '📊', label: 'Report Center', desc: 'All the reports you have access to' },
  { href: '/storeops/timeoff', icon: '🌴', label: 'Time Off', desc: 'Requests & approvals' },
  { href: '/storeops/swaps', icon: '🔄', label: 'Shift Swaps', desc: 'Swap requests & approvals' },
  { href: '/storeops/payroll', icon: '💵', label: 'Payroll', desc: 'Scheduled vs actual pay' },
  { href: '/storeops/staffing', icon: '🔥', label: 'Staffing Heat Map', desc: 'Demand by hour → staff required vs scheduled' },
  { href: '/storeops/admin', icon: '🛠️', label: 'Admin', desc: 'Employees, pay rates, stores' },
]

export default function StoreOpsDashboard() {
  const [emps, setEmps] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [timeoff, setTimeoff] = useState<any[]>([])
  const [shifts, setShifts] = useState<any[]>([])
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
  }, [])

  const activeStores = stores.filter(s => s.is_active).length
  const pendingTO = timeoff.filter(t => t.status === 'pending').length
  const weekHours = shifts.reduce((s, x) => s + (Number(x.scheduled_hours) || 0), 0)

  const Tile = ({ label, value, accent, href }: { label: string; value: string; accent?: string; href: string }) => (
    <Link href={href} style={{ textDecoration: 'none', color: 'inherit' }}>
      <div className="card" style={{ padding: '14px 18px', minWidth: 150, borderLeft: `4px solid ${accent || 'var(--accent)'}`, cursor: 'pointer' }}>
        <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
        <div style={{ fontSize: 24, fontWeight: 700, marginTop: 2 }}>{value}</div>
        <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 4 }}>View →</div>
      </div>
    </Link>
  )

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏠 StoreOps</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Scheduling, time off, shift swaps, and payroll across your stores.</p>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 22 }}>
            <Tile label="Active employees" value={String(emps.length)} accent="#2563eb" href="/storeops/employees" />
            <Tile label="Active stores" value={String(activeStores)} accent="#059669" href="/storeops/admin" />
            <Tile label="This week's scheduled hrs" value={weekHours.toFixed(1)} accent="#d97706" href="/storeops/schedule" />
            <Tile label="Pending time off" value={String(pendingTO)} accent={pendingTO ? '#dc2626' : '#6b7280'} href="/storeops/timeoff" />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12 }}>
            {LINKS.map(l => (
              <Link key={l.href} href={l.href} style={{ textDecoration: 'none' }}>
                <div className="card" style={{ padding: 16, cursor: 'pointer', height: '100%' }}>
                  <div style={{ fontSize: 22 }}>{l.icon}</div>
                  <div style={{ fontWeight: 700, fontSize: 15, marginTop: 6, color: 'var(--text)' }}>{l.label}</div>
                  <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 2 }}>{l.desc}</div>
                </div>
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
