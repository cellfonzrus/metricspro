'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { isSuperAdmin } from '@/lib/rbac'
import { REPORT_CATEGORIES, clearedFor, PortalCfg } from '@/lib/reports'

// Unified Report Center — every report across modules, in one place, categorized. Each report links
// to its real page. Admins can also toggle "show in employee portal" per report and choose which
// roles see it (the portal/employee surfaces honor this + the role's clearance).
export default function ReportCenterPage() {
  const { permissions } = useAuth()
  const admin = isSuperAdmin(permissions)
  const [cfg, setCfg] = useState<PortalCfg>({})
  const [roles, setRoles] = useState<{ name: string; display_name: string }[]>([])
  const [busy, setBusy] = useState('')

  const loadCfg = useCallback(() => {
    api('/api/v1/core/portal-reports').then((r: any) => setCfg(r?.config || {})).catch(() => {})
  }, [])
  useEffect(() => { loadCfg() }, [loadCfg])
  useEffect(() => { if (admin) api('/api/v1/core/roles').then((r: any) => setRoles(r?.roles || [])).catch(() => {}) }, [admin])

  async function save(href: string, label: string, category: string, patch: Partial<{ enabled: boolean; roles: string[] }>) {
    const cur = cfg[href] || { enabled: false, roles: [] }
    const next = { enabled: cur.enabled, roles: cur.roles || [], ...patch }
    setCfg(c => ({ ...c, [href]: { ...next, label, category } }))   // optimistic
    setBusy(href)
    try {
      await api('/api/v1/core/portal-reports', { method: 'PUT', body: JSON.stringify({ href, label, category, ...next }) })
    } catch (e: any) { alert('Save failed: ' + (e?.message || e)); loadCfg() }
    finally { setBusy('') }
  }

  const toggleRole = (href: string, label: string, cat: string, role: string) => {
    const cur = cfg[href]?.roles || []
    save(href, label, cat, { roles: cur.includes(role) ? cur.filter(r => r !== role) : [...cur, role] })
  }

  return (
    <div style={{ padding: 24, maxWidth: 980 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📊 Report Center</h1>
      <p style={{ color: 'var(--text3)', fontSize: 13, marginTop: 4 }}>
        Every report, in one place. {admin ? 'Toggle “Portal” to surface a report in the employee portal, and pick which roles can see it (employees still need the report’s clearance from Roles & Access).' : 'Open any report you have access to.'}
      </p>

      {REPORT_CATEGORIES.map(grp => {
        const visible = grp.reports.filter(r => admin || clearedFor(permissions, r))
        if (visible.length === 0) return null
        return (
          <div key={grp.category} className="card" style={{ padding: 0, overflow: 'hidden', marginBottom: 14 }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>{grp.category}</div>
            {visible.map(r => {
              const c = cfg[r.href]
              const inPortal = !!c?.enabled
              return (
                <div key={r.href} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 14px', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
                  <Link href={r.href} style={{ fontWeight: 600, color: 'var(--accent, #2563eb)', textDecoration: 'none' }}>{r.label}</Link>
                  {!clearedFor(permissions, r) && admin && <span style={{ fontSize: 11, color: 'var(--text3)' }}>(you lack clearance to open)</span>}
                  <span style={{ flex: 1 }} />
                  {admin && (
                    <>
                      <label style={{ fontSize: 12, fontWeight: 600, color: inPortal ? '#15803d' : 'var(--text3)', display: 'flex', alignItems: 'center', gap: 5 }}>
                        <input type="checkbox" checked={inPortal} disabled={busy === r.href}
                          onChange={e => save(r.href, r.label, grp.category, { enabled: e.target.checked })} /> Portal
                      </label>
                      {inPortal && (
                        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', alignItems: 'center' }}>
                          <span style={{ fontSize: 11, color: 'var(--text3)' }}>roles:</span>
                          {roles.map(rl => {
                            const on = (c?.roles || []).includes(rl.name)
                            return (
                              <button key={rl.name} onClick={() => toggleRole(r.href, r.label, grp.category, rl.name)}
                                title={(c?.roles || []).length === 0 ? 'No roles picked = all roles with clearance' : ''}
                                style={{ padding: '2px 8px', borderRadius: 12, border: '1px solid var(--border)', cursor: 'pointer',
                                  fontSize: 11, fontWeight: 600, background: on ? '#1E3A5F' : 'var(--surface)', color: on ? '#fff' : 'var(--text2)' }}>
                                {rl.display_name || rl.name}
                              </button>
                            )
                          })}
                          {(c?.roles || []).length === 0 && <span style={{ fontSize: 11, color: 'var(--text3)' }}>(all with clearance)</span>}
                        </div>
                      )}
                    </>
                  )}
                </div>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}
