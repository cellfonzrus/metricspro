'use client'
import { useState, useEffect, useMemo } from 'react'
import Link from 'next/link'
import { api, getActiveOrg } from '@/lib/client'
import PageIntro from '@/components/PageIntro'
import { REPORT_CATEGORIES } from '@/lib/reports'

// Reports Index — a documented directory of EVERY report on the platform, the companion to the System
// Schematic (owner 2026-08-29: "make an index of all the reports like we did the schematic"). The schematic
// maps how data flows; this maps every report — what it is, who can see it, and (from data-readiness) whether
// it is live for this tenant and what data feeds it. Catalog comes from lib/reports (the single source every
// surface reads); live/needs status is joined from /data-readiness. DISPLAY/documentation.
const orgQ = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

const norm = (s: string) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
// Match a catalog report to a data-readiness surface by name overlap (labels vs lineage surface strings differ).
function readinessFor(label: string, rdReports: any[]): any | null {
  const n = norm(label)
  if (!n) return null
  let best: any = null
  for (const r of rdReports || []) {
    const rn = norm(r.report)
    if (!rn) continue
    if (rn === n || rn.includes(n) || n.includes(rn)) {
      if (!best || Math.abs(rn.length - n.length) < Math.abs(norm(best.report).length - n.length)) best = r
    }
  }
  return best
}

export default function ReportsIndexPage() {
  const [rd, setRd] = useState<any>(null)
  const [q, setQ] = useState('')
  const [onlyLive, setOnlyLive] = useState(false)

  useEffect(() => {
    api(`/api/v1/commcalc/data-readiness${orgQ()}`).then(setRd).catch(() => setRd(null))
  }, [])

  const total = useMemo(() => REPORT_CATEGORIES.reduce((n, g) => n + g.reports.length, 0), [])
  const query = norm(q)

  const groups = useMemo(() => REPORT_CATEGORIES.map(grp => ({
    category: grp.category,
    reports: grp.reports
      .map(r => ({ ...r, _rd: readinessFor(r.label, rd?.reports || []) }))
      .filter(r => !query || norm(r.label).includes(query) || norm(r.desc || '').includes(query) || norm(grp.category).includes(query))
      .filter(r => !onlyLive || r._rd?.powered),
  })).filter(g => g.reports.length), [rd, query, onlyLive])

  const shownCount = groups.reduce((n, g) => n + g.reports.length, 0)

  const scopeLabel = (scopes?: string[]) =>
    !scopes || scopes.includes('store') || scopes.includes('self') ? 'All roles'
      : scopes.includes('market') ? 'Managers +'
      : 'Owners / admins'

  return (
    <div style={{ padding: '18px 22px', maxWidth: 1080 }}>
      <PageIntro
        title="🗂️ Reports Index"
        right={<Link href="/commcalc/schematic" style={{ fontSize: 12 }}>System Schematic →</Link>}
        help={<>Every report on the platform, in one directory — what it shows, who can see it, and (where the
          data-lineage map knows it) whether it&rsquo;s live for you and what data feeds it. The companion to the
          System Schematic, which maps how the data flows.</>}
      />

      {/* controls */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search reports…"
          style={{ flex: '1 1 240px', minWidth: 200, padding: '7px 11px', fontSize: 13, borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)' }} />
        <label style={{ fontSize: 12.5, color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={onlyLive} onChange={e => setOnlyLive(e.target.checked)} /> live only
        </label>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{shownCount} of {total} reports</span>
      </div>

      {groups.map(grp => (
        <div key={grp.category} style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>{grp.category} <span style={{ color: 'var(--text3)', fontWeight: 400, fontSize: 12 }}>· {grp.reports.length}</span></div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 10 }}>
            {grp.reports.map(r => (
              <Link key={r.href} href={r.href} className="card"
                style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 6, textDecoration: 'none', color: 'inherit' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontWeight: 700, fontSize: 13.5, flex: 1 }}>{r.label}</span>
                  {r._rd && (
                    <span style={{ fontSize: 10.5, fontWeight: 600, padding: '1px 7px', borderRadius: 999,
                      background: r._rd.powered ? '#dcfce7' : '#fef9c3', color: r._rd.powered ? '#166534' : '#854d0e' }}>
                      {r._rd.powered ? 'Live' : 'Needs data'}
                    </span>
                  )}
                </div>
                {r.desc && <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.4 }}>{r.desc}</div>}
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: 10.5, color: 'var(--text3)', marginTop: 'auto' }}>
                  <span>{scopeLabel(r.scopes)}</span>
                  {r._rd?.powered && r._rd.powered_by?.length ? <span>· fed by {r._rd.powered_by.slice(0, 3).join(', ')}</span> : null}
                  {r._rd && !r._rd.powered && r._rd.needs?.length ? <span>· needs {r._rd.needs.slice(0, 3).join(', ')}</span> : null}
                </div>
              </Link>
            ))}
          </div>
        </div>
      ))}

      {shownCount === 0 && <div style={{ color: 'var(--text3)', fontSize: 13, padding: 20 }}>No reports match “{q}”.</div>}
    </div>
  )
}
