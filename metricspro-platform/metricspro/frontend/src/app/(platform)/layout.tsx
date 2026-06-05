'use client'
import { useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'

const NAV = [
  { group: 'CommCalc', items: [
    { href: '/commcalc', label: 'Dashboard', icon: '📊' },
    { href: '/commcalc/upload', label: 'Upload Files', icon: '📁' },
    { href: '/commcalc/reports', label: 'All Reports', icon: '📋' },
    { href: '/commcalc/gp', label: 'Gross Profit', icon: '💰' },
    { href: '/commcalc/kpi', label: 'KPI Metrics', icon: '🎯' },
    { href: '/commcalc/flags', label: 'Flags', icon: '🚩' },
    { href: '/commcalc/discrepancy', label: 'Pay Discrepancy', icon: '⚠️' },
    { href: '/commcalc/settings', label: 'Commission Rates', icon: '⚙️' },
    { href: '/commcalc/expenses', label: 'Store Expenses', icon: '🏪' },
  ]},
  { group: 'StoreOps', items: [
    { href: '/storeops', label: 'Dashboard', icon: '🏠' },
    { href: '/storeops/schedule', label: 'Schedule', icon: '📅' },
    { href: '/storeops/employees', label: 'Employees', icon: '👥' },
    { href: '/storeops/timeoff', label: 'Time Off', icon: '🌴' },
    { href: '/storeops/payroll', label: 'Payroll', icon: '💵' },
  ]},
]

export default function PlatformLayout({ children }: { children: React.ReactNode }) {
  const [period, setPeriod] = useState('April 2026')
  const [collapsed, setCollapsed] = useState(false)
  const pathname = usePathname()

  return (
    <div style={{ display: 'flex', minHeight: '100vh', background: 'var(--bg)' }}>
      {/* Sidebar */}
      <aside style={{
        width: collapsed ? 56 : 220,
        background: 'var(--accent)',
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        transition: 'width 0.2s',
        overflow: 'hidden',
      }}>
        {/* Logo */}
        <div style={{ padding: '20px 16px 12px', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
          {!collapsed && (
            <div>
              <div style={{ color: 'white', fontWeight: 700, fontSize: 16 }}>MetricsPro</div>
              <div style={{ color: 'rgba(255,255,255,0.5)', fontSize: 11 }}>Commission Intelligence</div>
            </div>
          )}
          <button onClick={() => setCollapsed(!collapsed)} style={{
            background: 'none', border: 'none', color: 'rgba(255,255,255,0.6)',
            cursor: 'pointer', fontSize: 18, padding: collapsed ? '4px 0' : '8px 0 0',
            display: 'block',
          }}>
            {collapsed ? '→' : '←'}
          </button>
        </div>

        {/* Period selector */}
        {!collapsed && (
          <div style={{ padding: '12px 16px', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
            <label style={{ color: 'rgba(255,255,255,0.5)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Period</label>
            <input
              value={period}
              onChange={e => setPeriod(e.target.value)}
              style={{
                width: '100%', background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.2)',
                borderRadius: 6, color: 'white', padding: '5px 8px', fontSize: 13, marginTop: 4,
              }}
            />
          </div>
        )}

        {/* Navigation */}
        <nav style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
          {NAV.map(({ group, items }) => (
            <div key={group}>
              {!collapsed && (
                <div style={{ color: 'rgba(255,255,255,0.35)', fontSize: 10, textTransform: 'uppercase',
                  letterSpacing: '0.08em', padding: '12px 16px 4px', fontWeight: 600 }}>
                  {group}
                </div>
              )}
              {items.map(({ href, label, icon }) => {
                const active = pathname === href || pathname.startsWith(href + '/')
                return (
                  <Link key={href} href={href} style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: collapsed ? '10px 0' : '8px 16px',
                    justifyContent: collapsed ? 'center' : 'flex-start',
                    color: active ? 'white' : 'rgba(255,255,255,0.6)',
                    background: active ? 'rgba(255,255,255,0.12)' : 'transparent',
                    textDecoration: 'none', fontSize: 13, fontWeight: active ? 600 : 400,
                    borderLeft: active ? '3px solid rgba(255,255,255,0.6)' : '3px solid transparent',
                    transition: 'all 0.1s',
                  }}>
                    <span style={{ fontSize: 15 }}>{icon}</span>
                    {!collapsed && label}
                  </Link>
                )
              })}
            </div>
          ))}
        </nav>

        {/* Footer */}
        {!collapsed && (
          <div style={{ padding: '12px 16px', borderTop: '1px solid rgba(255,255,255,0.1)',
            color: 'rgba(255,255,255,0.4)', fontSize: 11 }}>
            Cellular Services · v1.0
          </div>
        )}
      </aside>

      {/* Main content */}
      <main style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {/* Top bar */}
        <header style={{
          background: 'white', borderBottom: '1px solid var(--border)',
          padding: '0 24px', height: 56, display: 'flex', alignItems: 'center',
          justifyContent: 'space-between', position: 'sticky', top: 0, zIndex: 10,
        }}>
          <div style={{ fontSize: 14, color: 'var(--text2)' }}>
            <span style={{ color: 'var(--text3)' }}>Period: </span>
            <span style={{ fontWeight: 600, color: 'var(--accent)' }}>{period}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 13, color: 'var(--text3)' }}>Cellular Services</span>
            <div style={{ width: 32, height: 32, background: 'var(--accent)', borderRadius: '50%',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'white', fontSize: 12, fontWeight: 700 }}>
              CS
            </div>
          </div>
        </header>

        {/* Page content */}
        <div style={{ flex: 1, padding: 24, minWidth: 0 }}>
          {children}
        </div>
      </main>
    </div>
  )
}
