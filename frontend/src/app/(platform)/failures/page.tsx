'use client'
import { useEffect, useState, useCallback, useMemo, Fragment } from 'react'
import { api } from '@/lib/client'
import { apiCached, CONFIG } from '@/lib/cache'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

// Failure Logs (TRIAGE) — the system log of things the app couldn't complete, grouped by similar nature so
// an admin can read a plain-English "what this means / how to fix it", CLEAR a whole group at once (mark
// reviewed — the rows are kept for the audit trail, never deleted), and CLUB a group into a fix request for
// tech support. Admin-only by default (RBAC — grant the /failures page to a role to share it). Also hosts
// the configurable clock-in face sensitivity. (mig 716)

type Row = {
  id: string; category: string; severity: string; source: string | null
  employee_name: string | null; store_code: string | null; message: string
  detail: any; remediation: string | null; status: string; created_at: string
  reviewed?: boolean; reviewed_by?: string | null; reviewed_at?: string | null
}
type Doc = { layman_meaning?: string | null; layman_fix?: string | null; escalate_when?: string | null; code_hint?: string | null }
type Group = {
  kind: string; label: string; module: string; known: boolean; doc: Doc
  count: number; unreviewed_count: number; reviewed_count: number
  latest_at: string | null; severity: string; sample_ids: string[]; all_reviewed: boolean
}

const SEV: Record<string, string> = { error: '#dc2626', warning: '#d97706', info: '#2563eb' }
const STATUS: Record<string, string> = { open: '#dc2626', resolved: '#16a34a', ignored: '#6b7280' }
const when = (iso?: string | null) => { if (!iso) return '—'; try { return new Date(iso).toLocaleString() } catch { return iso } }

// KNOWN GAP CLOSED (auto-fix-pipeline design §2a): `detail` has carried the full technical context since
// mig 112 — for a `system_error` row that is {ref, method, path, exc_type, traceback} written by
// HardeningMiddleware — but NO UI ever rendered it, so the reference code the user was shown led nowhere
// and the trace could only be read with SQL. Each row now expands to show it.
const detailRef = (d: any) => (d && typeof d === 'object' ? (d.ref || null) : null)
const detailTrace = (d: any) => (d && typeof d === 'object' ? (d.traceback || null) : null)
// Everything in `detail` EXCEPT the traceback (rendered separately as a <pre>), so no field is hidden.
function detailRest(d: any): [string, string][] {
  if (!d || typeof d !== 'object' || Array.isArray(d)) return []
  return Object.entries(d)
    .filter(([k]) => k !== 'traceback')
    .map(([k, v]) => [k, typeof v === 'string' ? v : JSON.stringify(v)] as [string, string])
}

export default function FailureLogsPage() {
  const [groups, setGroups] = useState<Group[]>([])
  const [rows, setRows] = useState<Row[]>([])
  const [reviewedFilter, setReviewedFilter] = useState<'false' | 'true' | ''>('false') // default = UNREVIEWED
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  // config (face sensitivity + logged categories)
  const [thr, setThr] = useState(0.60)
  const [disabled, setDisabled] = useState<string[]>([])
  const [types, setTypes] = useState<{ key: string; label: string }[]>([])
  const [canConfigure, setCanConfigure] = useState(false)
  const [cfgMsg, setCfgMsg] = useState('')

  const load = useCallback(() => {
    setLoading(true); setErr(''); setSel(new Set())
    const rq = reviewedFilter ? `?reviewed=${reviewedFilter}` : ''
    Promise.all([
      api(`/api/v1/core/failures/grouped${rq}`).catch((e: any) => { throw e }),
      api(`/api/v1/core/failures${rq}${rq ? '&' : '?'}limit=1000`).catch(() => ({ failures: [] })),
    ]).then(([g, f]: any[]) => {
      const gs: Group[] = g.groups || []
      setGroups(gs); setRows(f.failures || [])
      // collapsed-by-default for fully-reviewed groups; open the rest (owner: reviewed → collapse)
      setOpen(prev => { const n = { ...prev }; gs.forEach(x => { if (!(x.kind in n)) n[x.kind] = !x.all_reviewed }); return n })
    }).catch((e: any) => setErr(e?.message || String(e))).finally(() => setLoading(false))
  }, [reviewedFilter])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    // NAV-PERF 2026-08-04: the threshold/category registry is config (MEASURED 329 ms house /
    // 341 ms lux). The failure ROWS above stay uncached — they are live incident data.
    apiCached('/api/v1/core/failures/config', CONFIG).then((d: any) => {
      setThr(Number(d.face_match_threshold) || 0.60); setDisabled(d.disabled_categories || [])
      setTypes(d.types || []); setCanConfigure(!!d.can_configure)
    }).catch(() => {})
  }, [])

  const rowsByKind = useMemo(() => {
    const m: Record<string, Row[]> = {}
    for (const r of rows) (m[r.category] ||= []).push(r)
    return m
  }, [rows])

  const toggleRow = (id: string) => setSel(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  const selectGroup = (kind: string, on: boolean) => setSel(s => {
    const n = new Set(s); for (const r of (rowsByKind[kind] || [])) on ? n.add(r.id) : n.delete(r.id); return n
  })
  const groupAllSelected = (kind: string) => { const rs = rowsByKind[kind] || []; return rs.length > 0 && rs.every(r => sel.has(r.id)) }

  async function clearIds(ids: string[], reviewed = true) {
    if (!ids.length) return
    setBusy(true); setMsg('')
    try {
      const d = await api('/api/v1/core/failures/bulk-review', { method: 'POST', body: JSON.stringify({ ids, reviewed }) })
      setMsg(`✅ ${reviewed ? 'Cleared' : 'Reopened'} ${d.count} ${d.count === 1 ? 'row' : 'rows'}.`); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }
  const clearSelected = () => clearIds([...sel], true)
  const clearGroup = (g: Group) => clearIds((rowsByKind[g.kind] || []).filter(r => !r.reviewed).map(r => r.id), true)

  async function clubGroup(g: Group, escalate = false) {
    const action = escalate ? 'Escalate to tech support' : 'Club into a fix request'
    if (!window.confirm(`${action}?\n\n"${g.label}" — ${g.count} occurrence${g.count === 1 ? '' : 's'}.\nThis creates a fix request (pending super-admin approval). It does NOT change any log row.`)) return
    setBusy(true); setMsg('')
    try {
      await api('/api/v1/core/fix-requests', {
        method: 'POST', body: JSON.stringify({
          kind: g.kind, module: g.module, title: g.label,
          summary: g.doc.layman_meaning || g.label, proposed_action: g.doc.layman_fix || '',
          code_hint: g.doc.code_hint || '', sample_failure_ids: g.sample_ids, failure_count: g.count,
        }),
      })
      setMsg(`✅ Fix request created for "${g.label}" — it's now pending super-admin approval.`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  async function saveConfig() {
    setCfgMsg('')
    try {
      await api('/api/v1/core/failures/config', { method: 'PUT', body: JSON.stringify({ face_match_threshold: thr, disabled_categories: disabled }) })
      setCfgMsg('✅ Saved — new clock-ins use this immediately.')
    } catch (e: any) { setCfgMsg('❌ ' + (e?.message || e)) }
  }
  const toggleCat = (k: string) => setDisabled(d => d.includes(k) ? d.filter(x => x !== k) : [...d, k])

  const exportCols: ExportColumn[] = [
    { header: 'When', field: 'created_at', type: 'date', get: (r: Row) => when(r.created_at) },
    { header: 'Type', field: 'category', get: (r: Row) => groups.find(g => g.kind === r.category)?.label || r.category },
    { header: 'Severity', field: 'severity', get: (r: Row) => r.severity },
    { header: 'Employee', field: 'employee_name', role: 'rep', get: (r: Row) => r.employee_name || '' },
    { header: 'Store', field: 'store_code', role: 'store', get: (r: Row) => r.store_code || '' },
    { header: 'What happened', field: 'message', get: (r: Row) => r.message },
    { header: 'Reviewed', field: 'reviewed', get: (r: Row) => (r.reviewed ? 'yes' : 'no') },
    { header: 'How to fix', field: 'remediation', get: (r: Row) => r.remediation || '' },
  ]
  const cell: React.CSSProperties = { padding: '7px 10px', fontSize: 13, borderTop: '1px solid var(--border)', verticalAlign: 'top' }
  const unreviewedTotal = groups.reduce((a, g) => a + g.unreviewed_count, 0)

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🩺 Failure Logs</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 780 }}>
          Things the system couldn’t complete — grouped by similar nature, each with a plain-English
          <b> what this means</b> and <b>how to fix it</b>. Tick rows (or a whole group) and <b>Clear</b> to
          mark them reviewed — the rows are kept for the audit trail. {unreviewedTotal > 0 &&
          <b style={{ color: '#dc2626' }}>{unreviewedTotal} unreviewed.</b>}
        </p>
      </div>

      {/* Config: clock-in face sensitivity */}
      <details className="card" style={{ padding: 16, marginBottom: 16 }}>
        <summary style={{ fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>⚙️ Clock-in face sensitivity &amp; logging</summary>
        <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '10px 0', maxWidth: 720 }}>
          How close a live face must be to the enrolled one. <b>Higher = easier match</b> (fewer false
          rejects); too high risks accepting a wrong face. Default <b>0.60</b>.
        </p>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
          <input type="range" min={0.45} max={0.72} step={0.01} value={thr} disabled={!canConfigure}
            onChange={e => setThr(Number(e.target.value))} style={{ width: 260 }} />
          <span style={{ fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{thr.toFixed(2)}</span>
          {canConfigure && <button className="btn btn-sm btn-primary" onClick={saveConfig}>Save</button>}
          {cfgMsg && <span style={{ fontSize: 12 }}>{cfgMsg}</span>}
        </div>
        {!canConfigure && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>View only — a company admin can change this.</div>}
        {canConfigure && types.length > 0 && (
          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6 }}>Log these failure types (untick to stop logging one):</div>
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
              {types.map(t => (
                <label key={t.key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                  <input type="checkbox" checked={!disabled.includes(t.key)} onChange={() => toggleCat(t.key)} />
                  {t.label}
                </label>
              ))}
            </div>
          </div>
        )}
      </details>

      {/* Toolbar: reviewed filter + bulk clear + export */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
          {([['false', 'Unreviewed'], ['true', 'Reviewed'], ['', 'All']] as const).map(([v, lbl]) => (
            <button key={lbl} onClick={() => setReviewedFilter(v)} style={{
              padding: '6px 12px', fontSize: 13, border: 'none', cursor: 'pointer',
              background: reviewedFilter === v ? 'var(--accent)' : 'transparent',
              color: reviewedFilter === v ? '#fff' : 'var(--text2)',
            }}>{lbl}</button>
          ))}
        </div>
        <button className="btn btn-sm btn-primary" disabled={sel.size === 0 || busy} onClick={clearSelected}>
          ✓ Clear selected ({sel.size})
        </button>
        <button className="btn btn-sm" onClick={load}>Refresh</button>
        {msg && <span style={{ fontSize: 12.5 }}>{msg}</span>}
        <span style={{ flex: 1 }} />
        <ReportExportBar title="Failure Logs" filename="failure_logs" columns={exportCols} rows={rows} />
      </div>

      {err && <div className="card" style={{ padding: 14, color: '#dc2626' }}>{err}{(err.includes('112') || err.includes('716')) && ' — run migration 112 + 716 in Supabase.'}</div>}
      {loading ? <div className="card" style={{ padding: 16 }}>Loading…</div> : groups.length === 0 ? (
        <div className="card" style={{ padding: 16, color: 'var(--text3)' }}>No {reviewedFilter === 'false' ? 'unreviewed ' : ''}failures. 🎉</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {groups.map(g => {
            const grows = rowsByKind[g.kind] || []
            const isOpen = !!open[g.kind]
            return (
              <div key={g.kind} className="card" style={{ padding: 0, overflow: 'hidden', opacity: g.all_reviewed ? 0.78 : 1 }}>
                {/* Group header */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 14px', flexWrap: 'wrap', background: 'var(--surface2)' }}>
                  <button onClick={() => setOpen(o => ({ ...o, [g.kind]: !o[g.kind] }))} aria-label="toggle"
                    style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 13, color: 'var(--text2)', width: 16 }}>{isOpen ? '▾' : '▸'}</button>
                  <span style={{ width: 8, height: 8, borderRadius: 8, background: SEV[g.severity] || '#888' }} />
                  <span style={{ fontWeight: 700, fontSize: 14 }}>{g.label}</span>
                  {!g.known && <span title="Unrecognized error code" style={{ fontSize: 10.5, padding: '1px 6px', borderRadius: 8, background: '#f59e0b22', color: '#b45309', border: '1px solid #f59e0b55' }}>unknown code</span>}
                  <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {g.module}</span>
                  <span style={{ fontSize: 12, color: 'var(--text2)' }}>{g.count} occurrence{g.count === 1 ? '' : 's'}</span>
                  {g.unreviewed_count > 0 && <span style={{ fontSize: 11, padding: '1px 7px', borderRadius: 8, background: '#dc262622', color: '#dc2626', fontWeight: 600 }}>{g.unreviewed_count} unreviewed</span>}
                  {g.all_reviewed && <span style={{ fontSize: 11, padding: '1px 7px', borderRadius: 8, background: '#16a34a22', color: '#16a34a', fontWeight: 600 }}>all reviewed</span>}
                  <span style={{ fontSize: 11, color: 'var(--text3)' }}>latest {when(g.latest_at)}</span>
                  <span style={{ flex: 1 }} />
                  {g.unreviewed_count > 0 && <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => clearGroup(g)}>Clear group</button>}
                  {g.known
                    ? <button className="btn btn-sm" disabled={busy} onClick={() => clubGroup(g)}>🛠️ Club into fix request</button>
                    : <button className="btn btn-sm" disabled={busy} onClick={() => clubGroup(g, true)}>🚨 Escalate to support</button>}
                </div>

                {isOpen && (
                  <div>
                    {/* Plain-English */}
                    <div style={{ padding: '12px 16px', display: 'grid', gap: 8, background: 'var(--surface)' }}>
                      <div style={{ fontSize: 13 }}><b>What this means:</b> {g.doc.layman_meaning || '—'}</div>
                      <div style={{ fontSize: 13 }}><b>How to fix it:</b> {g.doc.layman_fix || '—'}</div>
                      {g.doc.escalate_when && <div style={{ fontSize: 12.5, color: 'var(--text2)' }}><b>Escalate to tech support when:</b> {g.doc.escalate_when}</div>}
                    </div>
                    {/* Rows in this group */}
                    <div className="table-wrapper" style={{ padding: 0 }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead><tr style={{ background: 'var(--surface2)' }}>
                          <th style={{ padding: '6px 10px', width: 30 }}>
                            <input type="checkbox" checked={groupAllSelected(g.kind)} onChange={e => selectGroup(g.kind, e.target.checked)} title="Select all in group" />
                          </th>
                          {['When', 'Who / where', 'What happened', 'Reviewed'].map(h =>
                            <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
                        </tr></thead>
                        <tbody>
                          {grows.length === 0 && <tr><td style={cell} colSpan={5}>No rows loaded for this group in the current filter.</td></tr>}
                          {grows.map(r => (
                            <Fragment key={r.id}>
                              <tr style={{ background: sel.has(r.id) ? 'var(--accent)11' : undefined }}>
                                <td style={cell}><input type="checkbox" checked={sel.has(r.id)} onChange={() => toggleRow(r.id)} /></td>
                                <td style={{ ...cell, whiteSpace: 'nowrap', color: 'var(--text3)' }}>{when(r.created_at)}</td>
                                <td style={cell}>{r.employee_name || '—'}{r.store_code ? <span style={{ color: 'var(--text3)' }}> · {r.store_code}</span> : ''}</td>
                                <td style={cell}>
                                  {r.message}
                                  {/* Technical detail + TRACEBACK (design §2a): present in the DB since mig 112,
                                      never rendered until now. Collapsed by default so the triage list stays
                                      readable; the reference code shown to the user is surfaced on the summary
                                      so an admin can match a user's report to the row without SQL. */}
                                  {(detailTrace(r.detail) || detailRest(r.detail).length > 0) && (
                                    <details style={{ marginTop: 5 }}>
                                      <summary style={{ fontSize: 11.5, color: 'var(--text3)', cursor: 'pointer' }}>
                                        Technical detail{detailRef(r.detail) ? ` · ref ${detailRef(r.detail)}` : ''}
                                        {detailTrace(r.detail) ? ' · traceback' : ''}
                                      </summary>
                                      {detailRest(r.detail).length > 0 && (
                                        <div style={{ fontSize: 11.5, color: 'var(--text2)', margin: '5px 0' }}>
                                          {detailRest(r.detail).map(([k, v]) => (
                                            <div key={k}><b>{k}:</b> {v.length > 400 ? v.slice(0, 400) + '…' : v}</div>
                                          ))}
                                        </div>
                                      )}
                                      {detailTrace(r.detail) && (
                                        <pre style={{ fontSize: 11, background: 'var(--surface2)', padding: 9, borderRadius: 7, overflow: 'auto', maxHeight: 300, whiteSpace: 'pre-wrap', margin: '5px 0 0' }}>
                                          {detailTrace(r.detail)}
                                        </pre>
                                      )}
                                    </details>
                                  )}
                                </td>
                                <td style={cell}>
                                  {r.reviewed
                                    ? <button className="btn btn-sm" onClick={() => clearIds([r.id], false)}>Reopen</button>
                                    : <button className="btn btn-sm btn-primary" onClick={() => clearIds([r.id], true)}>Clear</button>}
                                </td>
                              </tr>
                            </Fragment>
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
