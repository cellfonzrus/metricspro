'use client'
// Fleet Failure Triage — the HOUSE support team's CROSS-TENANT view of every tenant's failure logs (mig 716).
// Server-gated (_support_ctx: super_admin OR house-org membership w/ modules.support); a tenant user gets 403.
// Same grouping + plain-English UX as /failures, filterable by tenant / module / kind / date; club a group of
// similar failures (across tenants) into ONE fix request → super-admin approval → the automation queue.
import { useState, useEffect, useCallback, useMemo, Fragment } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

type Doc = { layman_meaning?: string | null; layman_fix?: string | null; escalate_when?: string | null; code_hint?: string | null }
type Affected = { org_id: string; count: number; org_name?: string }
type Group = {
  kind: string; label: string; module: string; known: boolean; doc: Doc
  count: number; unreviewed_count: number; reviewed_count: number
  latest_at: string | null; severity: string; sample_ids: string[]; all_reviewed: boolean; affected_orgs: Affected[]
}
type Row = {
  id: string; org_id: string; tenant_name: string; category: string; severity: string
  employee_name: string | null; store_code: string | null; message: string; created_at: string; reviewed?: boolean
}

const SEV: Record<string, string> = { error: '#dc2626', warning: '#d97706', info: '#2563eb' }
const when = (iso?: string | null) => { if (!iso) return '—'; try { return new Date(iso).toLocaleString() } catch { return iso } }

export default function FleetFailureTriage() {
  const [groups, setGroups] = useState<Group[]>([])
  const [rows, setRows] = useState<Row[]>([])
  const [tenants, setTenants] = useState<{ org_id: string; name: string }[]>([])
  const [modules, setModules] = useState<string[]>([])
  const [kinds, setKinds] = useState<{ kind: string; label: string }[]>([])
  const [reviewedFilter, setReviewedFilter] = useState<'false' | 'true' | ''>('false')
  const [fOrg, setFOrg] = useState(''); const [fModule, setFModule] = useState(''); const [fKind, setFKind] = useState('')
  const [dFrom, setDFrom] = useState(''); const [dTo, setDTo] = useState('')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    setLoading(true); setErr(''); setSel(new Set())
    const q = new URLSearchParams()
    if (reviewedFilter) q.set('reviewed', reviewedFilter)
    if (fOrg) q.set('org', fOrg); if (fModule) q.set('module', fModule); if (fKind) q.set('kind', fKind)
    if (dFrom) q.set('date_from', dFrom); if (dTo) q.set('date_to', dTo + 'T23:59:59')
    api(`/api/v1/helpdesk/support/failures?${q.toString()}`).then((d: any) => {
      const gs: Group[] = d.groups || []
      setGroups(gs); setRows(d.rows || []); setModules(d.modules || []); setKinds(d.kinds || [])
      setTenants(d.tenants || [])
      setOpen(prev => { const n = { ...prev }; gs.forEach(x => { if (!(x.kind in n)) n[x.kind] = !x.all_reviewed }); return n })
    }).catch((e: any) => setErr(e?.message || 'Could not load fleet failures')).finally(() => setLoading(false))
  }, [reviewedFilter, fOrg, fModule, fKind, dFrom, dTo])
  useEffect(() => { load() }, [load])

  const rowsByKind = useMemo(() => { const m: Record<string, Row[]> = {}; for (const r of rows) (m[r.category] ||= []).push(r); return m }, [rows])
  const toggleRow = (id: string) => setSel(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  const selectGroup = (kind: string, on: boolean) => setSel(s => { const n = new Set(s); for (const r of (rowsByKind[kind] || [])) on ? n.add(r.id) : n.delete(r.id); return n })
  const groupAllSelected = (kind: string) => { const rs = rowsByKind[kind] || []; return rs.length > 0 && rs.every(r => sel.has(r.id)) }

  async function clearIds(ids: string[], reviewed = true) {
    if (!ids.length) return
    setBusy(true); setMsg('')
    try {
      const d = await api('/api/v1/helpdesk/support/failures/bulk-review', { method: 'POST', body: JSON.stringify({ ids, reviewed }) })
      setMsg(`✅ ${reviewed ? 'Cleared' : 'Reopened'} ${d.count} row${d.count === 1 ? '' : 's'} across tenants.`); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  async function clubGroup(g: Group, escalate = false) {
    const spread = g.affected_orgs.length
    if (!window.confirm(`${escalate ? 'Escalate' : 'Club into a fix request'}?\n\n"${g.label}" — ${g.count} occurrence${g.count === 1 ? '' : 's'} across ${spread} tenant${spread === 1 ? '' : 's'}.\nCreates a fix request (pending super-admin approval). It does NOT change any log row or ship any code.`)) return
    setBusy(true); setMsg('')
    try {
      const d = await api('/api/v1/helpdesk/support/fix-requests', {
        method: 'POST', body: JSON.stringify({
          kind: g.kind, module: g.module, title: g.label,
          summary: g.doc.layman_meaning || g.label, proposed_action: g.doc.layman_fix || '',
          code_hint: g.doc.code_hint || '', sample_failure_ids: g.sample_ids,
          affected_orgs: g.affected_orgs.map(o => ({ org_id: o.org_id, count: o.count })), failure_count: g.count,
        }),
      })
      setMsg(`✅ Fix request ${d.id ? 'created' : 'queued'} — pending super-admin approval.`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  const sel2: React.CSSProperties = { padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }
  const cell: React.CSSProperties = { padding: '7px 10px', fontSize: 13, borderTop: '1px solid var(--border)', verticalAlign: 'top' }
  const exportCols: ExportColumn[] = [
    { header: 'When', get: (r: Row) => when(r.created_at) }, { header: 'Tenant', get: (r: Row) => r.tenant_name },
    { header: 'Type', get: (r: Row) => groups.find(g => g.kind === r.category)?.label || r.category },
    { header: 'Severity', get: (r: Row) => r.severity }, { header: 'Who', get: (r: Row) => r.employee_name || '' },
    { header: 'Store', get: (r: Row) => r.store_code || '' }, { header: 'What happened', get: (r: Row) => r.message },
    { header: 'Reviewed', get: (r: Row) => (r.reviewed ? 'yes' : 'no') },
  ]
  const unreviewedTotal = groups.reduce((a, g) => a + g.unreviewed_count, 0)

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🩺 Fleet Failure Triage</h1>
        <span style={{ fontSize: 13, color: 'var(--text3)' }}>Cross-tenant · {groups.length} group{groups.length === 1 ? '' : 's'}
          {unreviewedTotal > 0 && <span style={{ color: '#dc2626', fontWeight: 600 }}> · {unreviewedTotal} unreviewed</span>}</span>
        <span style={{ flex: 1 }} />
        <Link href="/admin/support/fix-requests" className="btn btn-sm">🛠️ Fix Requests</Link>
        <Link href="/admin/support" className="btn btn-sm">🎧 Console</Link>
      </div>
      <p className="pg-note" style={{ color: 'var(--text3)', fontSize: 12, marginTop: 4 }}>
        Every tenant’s failure logs in one place. Club similar failures into a fix request; a super-admin
        approves it into the automation queue. Clearing marks rows reviewed (kept for the audit trail).
      </p>

      <div className="card" style={{ padding: 12, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', margin: '10px 0' }}>
        <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
          {([['false', 'Unreviewed'], ['true', 'Reviewed'], ['', 'All']] as const).map(([v, lbl]) => (
            <button key={lbl} onClick={() => setReviewedFilter(v)} style={{ padding: '6px 12px', fontSize: 13, border: 'none', cursor: 'pointer', background: reviewedFilter === v ? 'var(--accent)' : 'transparent', color: reviewedFilter === v ? '#fff' : 'var(--text2)' }}>{lbl}</button>
          ))}
        </div>
        <select style={sel2} value={fOrg} onChange={e => setFOrg(e.target.value)}>
          <option value="">All tenants</option>{tenants.map(t => <option key={t.org_id} value={t.org_id}>{t.name}</option>)}</select>
        <select style={sel2} value={fModule} onChange={e => setFModule(e.target.value)}>
          <option value="">All modules</option>{modules.map(m => <option key={m} value={m}>{m}</option>)}</select>
        <select style={sel2} value={fKind} onChange={e => setFKind(e.target.value)}>
          <option value="">All kinds</option>{kinds.map(k => <option key={k.kind} value={k.kind}>{k.label}</option>)}</select>
        <input type="date" style={sel2} value={dFrom} onChange={e => setDFrom(e.target.value)} title="From" />
        <input type="date" style={sel2} value={dTo} onChange={e => setDTo(e.target.value)} title="To" />
        <button className="btn btn-sm btn-primary" disabled={sel.size === 0 || busy} onClick={() => clearIds([...sel], true)}>✓ Clear selected ({sel.size})</button>
        <span style={{ flex: 1 }} />
        <ReportExportBar title="Fleet Failures" filename="fleet_failures" columns={exportCols} rows={rows} />
      </div>
      {msg && <div style={{ fontSize: 12.5, marginBottom: 8 }}>{msg}</div>}
      {err && <div className="card" style={{ padding: 14, color: '#dc2626' }}>{err}{err.includes('716') && ' — run migration 716 in Supabase.'}</div>}

      {loading ? <div className="card" style={{ padding: 16 }}>Loading…</div> : groups.length === 0 ? (
        <div className="card" style={{ padding: 16, color: 'var(--text3)' }}>No {reviewedFilter === 'false' ? 'unreviewed ' : ''}failures across the fleet for this filter. 🎉</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {groups.map(g => {
            const grows = rowsByKind[g.kind] || []
            const isOpen = !!open[g.kind]
            return (
              <div key={g.kind} className="card" style={{ padding: 0, overflow: 'hidden', opacity: g.all_reviewed ? 0.78 : 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 14px', flexWrap: 'wrap', background: 'var(--surface2)' }}>
                  <button onClick={() => setOpen(o => ({ ...o, [g.kind]: !o[g.kind] }))} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 13, color: 'var(--text2)', width: 16 }}>{isOpen ? '▾' : '▸'}</button>
                  <span style={{ width: 8, height: 8, borderRadius: 8, background: SEV[g.severity] || '#888' }} />
                  <span style={{ fontWeight: 700, fontSize: 14 }}>{g.label}</span>
                  {!g.known && <span title="Unrecognized error code" style={{ fontSize: 10.5, padding: '1px 6px', borderRadius: 8, background: '#f59e0b22', color: '#b45309', border: '1px solid #f59e0b55' }}>unknown code</span>}
                  <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {g.module}</span>
                  <span style={{ fontSize: 12, color: 'var(--text2)' }}>{g.count} · {g.affected_orgs.length} tenant{g.affected_orgs.length === 1 ? '' : 's'}</span>
                  {g.unreviewed_count > 0 && <span style={{ fontSize: 11, padding: '1px 7px', borderRadius: 8, background: '#dc262622', color: '#dc2626', fontWeight: 600 }}>{g.unreviewed_count} unreviewed</span>}
                  {g.all_reviewed && <span style={{ fontSize: 11, padding: '1px 7px', borderRadius: 8, background: '#16a34a22', color: '#16a34a', fontWeight: 600 }}>all reviewed</span>}
                  <span style={{ fontSize: 11, color: 'var(--text3)' }}>latest {when(g.latest_at)}</span>
                  <span style={{ flex: 1 }} />
                  {g.unreviewed_count > 0 && <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => clearIds(grows.filter(r => !r.reviewed).map(r => r.id), true)}>Clear group</button>}
                  <button className="btn btn-sm" disabled={busy} onClick={() => clubGroup(g, !g.known)}>{g.known ? '🛠️ Club into fix request' : '🚨 Escalate'}</button>
                </div>
                {isOpen && (
                  <div>
                    <div style={{ padding: '12px 16px', display: 'grid', gap: 8, background: 'var(--surface)' }}>
                      <div style={{ fontSize: 13 }}><b>What this means:</b> {g.doc.layman_meaning || '—'}</div>
                      <div style={{ fontSize: 13 }}><b>How to fix it:</b> {g.doc.layman_fix || '—'}</div>
                      {g.doc.code_hint && <div style={{ fontSize: 12, color: 'var(--text3)' }}><b>Code area:</b> <code>{g.doc.code_hint}</code></div>}
                      <div style={{ fontSize: 12, color: 'var(--text2)' }}><b>Affected:</b> {g.affected_orgs.map(o => `${o.org_name || 'Tenant'} (${o.count})`).join(' · ')}</div>
                    </div>
                    <div className="table-wrapper" style={{ padding: 0 }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead><tr style={{ background: 'var(--surface2)' }}>
                          <th style={{ padding: '6px 10px', width: 30 }}><input type="checkbox" checked={groupAllSelected(g.kind)} onChange={e => selectGroup(g.kind, e.target.checked)} /></th>
                          {['When', 'Tenant', 'Who / where', 'What happened', 'Reviewed'].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
                        </tr></thead>
                        <tbody>
                          {grows.map(r => (
                            <tr key={r.id} style={{ background: sel.has(r.id) ? 'var(--accent)11' : undefined }}>
                              <td style={cell}><input type="checkbox" checked={sel.has(r.id)} onChange={() => toggleRow(r.id)} /></td>
                              <td style={{ ...cell, whiteSpace: 'nowrap', color: 'var(--text3)' }}>{when(r.created_at)}</td>
                              <td style={{ ...cell, fontWeight: 600, color: 'var(--accent)' }}>{r.tenant_name}</td>
                              <td style={cell}>{r.employee_name || '—'}{r.store_code ? <span style={{ color: 'var(--text3)' }}> · {r.store_code}</span> : ''}</td>
                              <td style={cell}>{r.message}</td>
                              <td style={cell}>{r.reviewed ? <button className="btn btn-sm" onClick={() => clearIds([r.id], false)}>Reopen</button> : <button className="btn btn-sm btn-primary" onClick={() => clearIds([r.id], true)}>Clear</button>}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
