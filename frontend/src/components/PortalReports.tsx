'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { myPortalReports, PortalCfg } from '@/lib/reports'
import { safeHref } from '@/lib/safe-url'   // H6: portal_reports.href is tenant-writable

// The reports an employee may see in their portal — admin-enabled (Report Center) + their role's
// clearance. Links open the real report page (auto-scoped to them by the Phase 5 span when enforced).
export default function PortalReports({ compact }: { compact?: boolean }) {
  const { permissions, user } = useAuth()
  const [cfg, setCfg] = useState<PortalCfg | null>(null)
  useEffect(() => { api('/api/v1/core/portal-reports').then((r: any) => setCfg(r?.config || {})).catch(() => setCfg({})) }, [])

  if (cfg === null) return <div style={{ color: 'var(--text3)', fontSize: 13, padding: 8 }}>Loading reports…</div>
  const groups = myPortalReports(permissions, (user as any)?.role || null, cfg)
  if (groups.length === 0) {
    return <div className="card" style={{ padding: 16, color: 'var(--text3)', fontSize: 13 }}>
      No reports have been shared with you yet. An admin can add them in the Report Center.
    </div>
  }
  return (
    <div style={{ display: 'grid', gap: compact ? 10 : 14 }}>
      {groups.map(g => (
        <div key={g.category} className="card" style={{ padding: compact ? 12 : 14 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', marginBottom: 8 }}>{g.category}</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {g.reports.filter(r => safeHref(r.href)).map(r => (
              <Link key={r.href} href={safeHref(r.href, '#')} style={{ display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '8px 12px', borderRadius: 9, border: '1px solid var(--border)', background: 'var(--surface)',
                color: 'var(--text)', textDecoration: 'none', fontSize: 13, fontWeight: 600 }}>
                📊 {r.label} <span style={{ color: 'var(--text3)' }}>→</span>
              </Link>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
