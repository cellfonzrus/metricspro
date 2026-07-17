'use client'
import { useEffect, useState, useCallback, useMemo, Fragment } from 'react'
import { api } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'

// Failure Logs — a system log of failures the app hits (e.g. a valid rep rejected by kiosk face-match),
// each WITH a how-to-fix note, so admins can diagnose recurring issues. Admin-only by default (RBAC —
// grant the /failures page to a role to share it). Also hosts the configurable clock-in face sensitivity.

type Row = {
  id: string; category: string; severity: string; source: string | null
  employee_name: string | null; store_code: string | null; message: string
  detail: any; remediation: string | null; status: string; created_at: string
  resolved_by: string | null; resolved_note: string | null
}
type Ftype = { key: string; label: string; severity?: string; remediation?: string }

const SEV: Record<string, string> = { error: '#dc2626', warning: '#d97706', info: '#2563eb' }
const STATUS: Record<string, string> = { open: '#dc2626', resolved: '#16a34a', ignored: '#6b7280' }

export default function FailureLogsPage() {
  const [rows, setRows] = useState<Row[]>([])
  const [types, setTypes] = useState<Ftype[]>([])
  const [openCount, setOpenCount] = useState(0)
  const [fStatus, setFStatus] = useState('open')
  const [fCat, setFCat] = useState('')
  // RULE FIVE (§3d) standard filter bar. failure_log has no `market` column, so market is omitted here
  // (documented deviation — the doctrine applies the core set WHERE MEANINGFUL). Period is a date range
  // over created_at; store/person options are derived from the loaded (org-scoped) rows.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [expanded, setExpanded] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  // config
  const [thr, setThr] = useState(0.60)
  const [disabled, setDisabled] = useState<string[]>([])
  const [canConfigure, setCanConfigure] = useState(false)
  const [cfgMsg, setCfgMsg] = useState('')

  const load = useCallback(() => {
    setLoading(true); setErr('')
    const q = new URLSearchParams()
    if (fStatus) q.set('status', fStatus)
    if (fCat) q.set('category', fCat)
    api(`/api/v1/core/failures?${q.toString()}`)
      .then((d: any) => { setRows(d.failures || []); setOpenCount(d.open_count || 0); setCanConfigure(!!d.can_configure) })
      .catch((e: any) => setErr(e?.message || String(e)))
      .finally(() => setLoading(false))
  }, [fStatus, fCat])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    api('/api/v1/core/failure-types').then((d: any) => setTypes(d.types || [])).catch(() => {})
    api('/api/v1/core/failures/config').then((d: any) => {
      setThr(Number(d.face_match_threshold) || 0.60); setDisabled(d.disabled_categories || []); setCanConfigure(!!d.can_configure)
    }).catch(() => {})
  }, [])

  async function setStatus(id: string, status: string) {
    try { await api(`/api/v1/core/failures/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }); load() }
    catch (e: any) { setErr(e?.message || String(e)) }
  }
  async function saveConfig() {
    setCfgMsg('')
    try {
      await api('/api/v1/core/failures/config', { method: 'PUT', body: JSON.stringify({ face_match_threshold: thr, disabled_categories: disabled }) })
      setCfgMsg('✅ Saved — new clock-ins use this immediately.')
    } catch (e: any) { setCfgMsg('❌ ' + (e?.message || e)) }
  }
  const toggleCat = (k: string) => setDisabled(d => d.includes(k) ? d.filter(x => x !== k) : [...d, k])

  const labelOf = (k: string) => types.find(t => t.key === k)?.label || k
  const when = (iso: string) => { try { return new Date(iso).toLocaleString() } catch { return iso } }
  const cell: React.CSSProperties = { padding: '8px 10px', fontSize: 13, borderTop: '1px solid var(--border)', verticalAlign: 'top' }

  // Standard-filter accessors + derived (org-scoped) options, and the visible (filtered) rows that drive
  // BOTH the table and the exports (what-you-see-is-what-exports).
  const acc = { store: (r: Row) => r.store_code, rep: (r: Row) => r.employee_name, date: (r: Row) => r.created_at }
  const opts = useMemo(() => optionsFromRows(rows, acc), [rows])
  const visibleRows = useMemo(() => filterRows(rows, filt, acc), [rows, filt])

  // RULE FOUR (§3c) exports — the currently-loaded rows already reflect the status/type filters (applied
  // server-side) PLUS the standard filter bar (client-side), so `visibleRows` IS the visible view.
  const exportCols: ExportColumn[] = [
    { header: 'When', field: 'created_at', type: 'date', get: r => when(r.created_at) },
    { header: 'Type', field: 'category', get: r => labelOf(r.category) },
    { header: 'Severity', field: 'severity', get: r => r.severity },
    { header: 'Employee', field: 'employee_name', role: 'rep', get: r => r.employee_name || '' },
    { header: 'Store', field: 'store_code', role: 'store', get: r => r.store_code || '' },
    { header: 'Source', field: 'source', get: r => r.source || '' },
    { header: 'What happened', field: 'message', get: r => r.message },
    { header: 'Status', field: 'status', get: r => r.status },
    { header: 'How to fix', field: 'remediation', get: r => r.remediation || '' },
  ]

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🩺 Failure Logs</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
          Things the system couldn’t complete — like a valid rep rejected at kiosk clock-in — each with a
          <b> how-to-fix</b>. Admin-only by default; grant the <code>/failures</code> page to a role on
          Roles &amp; Access to share it. {openCount > 0 && <b style={{ color: '#dc2626' }}>{openCount} open.</b>}
        </p>
      </div>

      {/* Config */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>⚙️ Clock-in face sensitivity</div>
        <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '0 0 10px', maxWidth: 720 }}>
          Controls how close a live face must be to the enrolled one. <b>Higher = easier match</b> (fewer
          false rejects for the same rep); too high risks accepting a wrong face. Default <b>0.60</b>. If many
          reps are being rejected, nudge it toward 0.65.
        </p>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
          <input type="range" min={0.45} max={0.72} step={0.01} value={thr} disabled={!canConfigure}
            onChange={e => setThr(Number(e.target.value))} style={{ width: 260 }} />
          <span style={{ fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{thr.toFixed(2)}</span>
          <span style={{ fontSize: 12, color: 'var(--text3)' }}>{thr <= 0.52 ? 'strict' : thr >= 0.66 ? 'very lenient' : thr >= 0.61 ? 'lenient' : 'balanced'}</span>
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
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <select className="input" value={fStatus} onChange={e => setFStatus(e.target.value)} style={{ padding: '6px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13 }}>
          <option value="">All statuses</option><option value="open">Open</option><option value="resolved">Resolved</option><option value="ignored">Ignored</option>
        </select>
        <select value={fCat} onChange={e => setFCat(e.target.value)} style={{ padding: '6px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13 }}>
          <option value="">All types</option>
          {types.map(t => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
        <button className="btn btn-sm" onClick={load}>Refresh</button>
      </div>

      {/* RULE FIVE (§3d) standard universal filter bar — period (range) · store(s) · person(s). Drives the
          table AND the export. Market omitted (failure_log carries no market). */}
      <StandardFilterBar
        value={filt} onChange={setFilt} periodMode="range"
        show={{ period: true, stores: true, markets: false, reps: true }}
        storeOptions={opts.stores} repOptions={opts.reps}
        storeLabel="Stores…" repLabel="Employees…"
        right={<><span style={{ flex: 1 }} /><ReportExportBar title="Failure Logs" filename="failure_logs" columns={exportCols} rows={visibleRows} /></>}
      />

      {err && <div className="card" style={{ padding: 14, color: '#dc2626' }}>{err}{err.includes('112') && ' — run migration 112_failure_log.sql in Supabase.'}</div>}
      {loading ? <div className="card" style={{ padding: 16 }}>Loading…</div> : (
        <div className="card table-wrapper" style={{ padding: 0 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['When', 'Type', 'Who / where', 'What happened', 'Status', ''].map(h =>
                <th key={h} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {visibleRows.length === 0 && <tr><td style={cell} colSpan={6}>No failures for this filter. 🎉</td></tr>}
              {visibleRows.map(r => (
                <Fragment key={r.id}>
                  <tr>
                    <td style={{ ...cell, whiteSpace: 'nowrap', color: 'var(--text3)' }}>{when(r.created_at)}</td>
                    <td style={cell}><span style={{ color: SEV[r.severity] || 'var(--text2)', fontWeight: 600 }}>{labelOf(r.category)}</span></td>
                    <td style={cell}>{r.employee_name || '—'}{r.store_code ? <span style={{ color: 'var(--text3)' }}> · {r.store_code}</span> : ''}</td>
                    <td style={cell}>
                      {r.message}
                      <button onClick={() => setExpanded(expanded === r.id ? null : r.id)} style={{ marginLeft: 8, fontSize: 11, color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer' }}>
                        {expanded === r.id ? 'hide fix' : 'how to fix'}
                      </button>
                    </td>
                    <td style={cell}><span style={{ color: STATUS[r.status], fontWeight: 600, textTransform: 'capitalize' }}>{r.status}</span></td>
                    <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                      {r.status === 'open' ? (
                        <>
                          <button className="btn btn-sm" onClick={() => setStatus(r.id, 'resolved')}>Resolve</button>
                          <button className="btn btn-sm" style={{ marginLeft: 4 }} onClick={() => setStatus(r.id, 'ignored')}>Ignore</button>
                        </>
                      ) : <button className="btn btn-sm" onClick={() => setStatus(r.id, 'open')}>Reopen</button>}
                    </td>
                  </tr>
                  {expanded === r.id && (
                    <tr>
                      <td style={{ ...cell, background: 'var(--surface2)' }} colSpan={6}>
                        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
                          <b>How to fix:</b> {r.remediation || '—'}
                          {r.detail && <pre style={{ marginTop: 8, fontSize: 11, color: 'var(--text3)', whiteSpace: 'pre-wrap' }}>{JSON.stringify(r.detail, null, 1)}</pre>}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
