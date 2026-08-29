'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'

// ── Report-pull mapping admin (mig 207, RULE TWO) ────────────────────────────────────────────────
// The automated VidaPay / T-CETRA report pull is DRIVEN by this config, not hard-coded. Each report's
// target table + source-header→dest-column mapping is visible and editable here. Editing a row that
// shows "default" creates a tenant-scoped OVERRIDE; the house default stays intact. This is admin-only
// visibility/edit — the tenant never pulls from the frontend; the scheduled sweep + ▶ Pull now run it.

const cell: React.CSSProperties = { padding: '5px 8px', fontSize: 13, borderBottom: '1px solid var(--border)' }
const th: React.CSSProperties = { textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }
const inp: React.CSSProperties = { width: '100%', padding: '5px 7px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)' }

type MapRow = { src: string; col: string; type: string }

function toRows(column_map: any): MapRow[] {
  return Object.entries(column_map || {}).map(([src, v]: any) =>
    typeof v === 'object' && v ? { src, col: v.col || '', type: v.type || 'text' } : { src, col: String(v), type: 'text' })
}
function fromRows(rows: MapRow[]): any {
  const out: any = {}
  for (const r of rows) {
    if (!r.src.trim() || !r.col.trim()) continue
    out[r.src.trim()] = r.type === 'text' ? r.col.trim() : { col: r.col.trim(), type: r.type }
  }
  return out
}

export default function ReportMappingsPage() {
  const [reports, setReports] = useState<any[]>([])
  const [ready, setReady] = useState(true)
  const [msg, setMsg] = useState('')
  const [edit, setEdit] = useState<Record<string, any>>({})   // report_key -> draft

  async function load() {
    setMsg('')
    try {
      const r: any = await api('/api/v1/commcalc/report-pull-map')
      setReports(r.reports || []); setReady(r.ready !== false)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  useEffect(() => { load() }, [])

  function startEdit(rep: any) {
    setEdit(prev => ({
      ...prev, [rep.report_key]: {
        display_name: rep.display_name || '', target_table: rep.target_table || '',
        export_pref: rep.export_pref || 'csv', enabled: rep.enabled !== false,
        rows: toRows(rep.column_map), param_spec: JSON.stringify(rep.param_spec || {}, null, 2),
      },
    }))
  }
  function cancel(rk: string) { setEdit(prev => { const p = { ...prev }; delete p[rk]; return p }) }

  async function save(rep: any) {
    const d = edit[rep.report_key]; if (!d) return
    let param_spec: any
    try { param_spec = JSON.parse(d.param_spec || '{}') }
    catch { setMsg('❌ param_spec is not valid JSON for ' + rep.report_key); return }
    setMsg('⏳ Saving…')
    try {
      await api('/api/v1/commcalc/report-pull-map', {
        method: 'PUT', body: JSON.stringify({
          report_key: rep.report_key, display_name: d.display_name, target_table: d.target_table,
          export_pref: d.export_pref, enabled: d.enabled, processor: rep.processor || 'vidapay',
          column_map: fromRows(d.rows), param_spec,
        }),
      })
      setMsg('✅ Saved ' + rep.report_key); cancel(rep.report_key); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function resetOverride(rk: string) {
    if (!confirm('Drop this tenant override and fall back to the house default?')) return
    setMsg('⏳ Resetting…')
    try { await api(`/api/v1/commcalc/report-pull-map/${rk}/reset`, { method: 'POST', body: '{}' }); setMsg('✅ Reset ' + rk); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function reseed() {
    setMsg('⏳ Reseeding defaults…')
    try { const r: any = await api('/api/v1/commcalc/report-pull-map/reseed', { method: 'POST', body: '{}' }); setMsg(`✅ Seeded: ${(r.inserted || []).join(', ') || 'nothing new'}`); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  return (
    <div style={{ maxWidth: 1080 }}>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🗺️ Report-pull mapping</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          How the automated VidaPay / T-CETRA report pull maps each portal report to a database table and each
          source column to a destination column — <b>configuration, not code</b>. Editing a report that shows
          <span style={{ background: '#eef2ff', color: '#3730a3', padding: '0 6px', borderRadius: 6, fontSize: 12, margin: '0 4px' }}>default</span>
          creates a tenant override; the house default stays intact. The pull itself runs on a schedule and from
          <b> ▶ Pull now</b> on each portal login in <a href="/commcalc/email-imports#portal-logins" style={{ color: 'var(--accent,#2563eb)' }}>Data Imports</a> —
          the tenant never pulls from the UI.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <button className="btn btn-secondary" onClick={reseed} title="Insert any missing house-default report rows (idempotent mirror of migration 207's seed)">↻ Reseed defaults</button>
        <button className="btn btn-secondary" onClick={load}>Refresh</button>
        {!ready && <span style={{ fontSize: 12, color: '#b45309' }}>⚠️ migration 207 not applied — showing built-in defaults (read-only until it runs)</span>}
        {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
      </div>

      {reports.map((rep: any) => {
        const d = edit[rep.report_key]
        return (
          <div key={rep.report_key} className="card" style={{ padding: 0, marginBottom: 14 }}>
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <div style={{ fontWeight: 700, fontSize: 14 }}>{rep.display_name || rep.report_key}</div>
              <code style={{ fontSize: 11, color: 'var(--text3)' }}>{rep.report_key}</code>
              {rep.inherited
                ? <span style={{ background: '#eef2ff', color: '#3730a3', padding: '1px 7px', borderRadius: 6, fontSize: 11 }}>default</span>
                : <span style={{ background: '#ecfdf5', color: '#047857', padding: '1px 7px', borderRadius: 6, fontSize: 11 }}>override</span>}
              {rep.enabled === false && <span style={{ background: '#fef2f2', color: '#b91c1c', padding: '1px 7px', borderRadius: 6, fontSize: 11 }}>disabled</span>}
              {rep.param_spec?.calibration && <span style={{ background: '#fffbeb', color: '#92400e', padding: '1px 7px', borderRadius: 6, fontSize: 11 }}>calibration (params pinned on first live run)</span>}
              <div style={{ flex: 1 }} />
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>→ <b>{rep.target_table}</b>{rep.export_pref ? ` · ${rep.export_pref}` : ''}{rep.param_spec?.iterate_months ? ' · month-by-month' : ''}</div>
              {!d && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => startEdit(rep)}>Edit</button>}
              {!d && !rep.inherited && <button className="btn btn-secondary" style={{ fontSize: 12, color: '#dc2626' }} onClick={() => resetOverride(rep.report_key)}>Reset to default</button>}
            </div>

            {!d && (
              <div style={{ padding: '10px 14px' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}><th style={th}>Source column (portal)</th><th style={th}>→ Destination column</th><th style={th}>Type</th></tr></thead>
                  <tbody>
                    {toRows(rep.column_map).map((r, i) => (
                      <tr key={i}><td style={cell}>{r.src}</td><td style={{ ...cell, fontFamily: 'monospace' }}>{r.col}</td><td style={cell}>{r.type}</td></tr>
                    ))}
                    {toRows(rep.column_map).length === 0 && <tr><td style={{ ...cell, color: 'var(--text3)' }} colSpan={3}>No column map yet — generic report; the whole row is preserved in raw_row until its columns are pinned.</td></tr>}
                  </tbody>
                </table>
              </div>
            )}

            {d && (
              <div style={{ padding: '12px 14px' }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(170px,1fr))', gap: 10, marginBottom: 12 }}>
                  <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Display name (dropdown label)<br />
                    <input style={{ ...inp, marginTop: 4 }} value={d.display_name} onChange={e => setEdit(p => ({ ...p, [rep.report_key]: { ...d, display_name: e.target.value } }))} />
                    <span style={{ fontWeight: 400, fontSize: 11, color: 'var(--text3)' }}>Matched against the portal&apos;s own dropdown after normalising invisible characters (no-break spaces, zero-width spaces, en/em dashes, double spaces, case) — so a name that <i>looks</i> right now <i>is</i> right. Add <code>&quot;name_aliases&quot;: [&quot;other spelling&quot;]</code> to the parameter spec for a portal that renames it.</span></label>
                  <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Target table<br />
                    <input style={{ ...inp, marginTop: 4 }} value={d.target_table} onChange={e => setEdit(p => ({ ...p, [rep.report_key]: { ...d, target_table: e.target.value } }))} /></label>
                  <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Export<br />
                    <select style={{ ...inp, marginTop: 4 }} value={d.export_pref} onChange={e => setEdit(p => ({ ...p, [rep.report_key]: { ...d, export_pref: e.target.value } }))}>
                      <option value="csv">CSV</option><option value="excel">Excel</option></select></label>
                  <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', alignSelf: 'end' }}>
                    <input type="checkbox" checked={d.enabled} onChange={e => setEdit(p => ({ ...p, [rep.report_key]: { ...d, enabled: e.target.checked } }))} /> Enabled (pulled automatically)</label>
                </div>

                <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', margin: '4px 0 6px' }}>Column mapping (source header → destination column)</div>
                <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 8 }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}><th style={th}>Source column</th><th style={th}>Destination column</th><th style={th}>Type</th><th style={th}></th></tr></thead>
                  <tbody>
                    {d.rows.map((r: MapRow, i: number) => (
                      <tr key={i}>
                        <td style={cell}><input style={inp} value={r.src} onChange={e => { const rows = [...d.rows]; rows[i] = { ...r, src: e.target.value }; setEdit(p => ({ ...p, [rep.report_key]: { ...d, rows } })) }} /></td>
                        <td style={cell}><input style={{ ...inp, fontFamily: 'monospace' }} value={r.col} onChange={e => { const rows = [...d.rows]; rows[i] = { ...r, col: e.target.value }; setEdit(p => ({ ...p, [rep.report_key]: { ...d, rows } })) }} /></td>
                        <td style={cell}><select style={inp} value={r.type} onChange={e => { const rows = [...d.rows]; rows[i] = { ...r, type: e.target.value }; setEdit(p => ({ ...p, [rep.report_key]: { ...d, rows } })) }}>
                          <option value="text">text</option><option value="num">num</option><option value="date">date</option></select></td>
                        <td style={cell}><button className="btn btn-secondary" style={{ fontSize: 11, color: '#dc2626', padding: '2px 7px' }} onClick={() => { const rows = d.rows.filter((_: any, j: number) => j !== i); setEdit(p => ({ ...p, [rep.report_key]: { ...d, rows } })) }}>✕</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <button className="btn btn-secondary" style={{ fontSize: 12, marginBottom: 12 }} onClick={() => setEdit(p => ({ ...p, [rep.report_key]: { ...d, rows: [...d.rows, { src: '', col: '', type: 'text' }] } }))}>＋ Add column</button>

                <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', margin: '4px 0 6px' }}>Parameter spec (advanced — how the page is driven + month iteration)</div>
                <p style={{ fontSize: 11.5, color: 'var(--text3)', margin: '0 0 6px' }}>
                  Keys: <code>fields</code> (what to type on the portal form) · <code>iterate_months</code>/<code>interval_months</code>/<code>max_months_back</code> · <code>results_wait_s</code> (how long to wait for the results grid to populate before calling it a scrape timeout — default 90s; the wait costs time, never extra requests) · <code>name_aliases</code> (alternate dropdown spellings) · <code>generic</code>/<code>calibration</code>.
                </p>
                <textarea style={{ width: '100%', minHeight: 150, padding: 8, borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, fontFamily: 'monospace' }}
                  value={d.param_spec} onChange={e => setEdit(p => ({ ...p, [rep.report_key]: { ...d, param_spec: e.target.value } }))} />

                <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                  <button className="btn btn-primary" onClick={() => save(rep)}>Save</button>
                  <button className="btn btn-secondary" onClick={() => cancel(rep.report_key)}>Cancel</button>
                </div>
              </div>
            )}
          </div>
        )
      })}
      {reports.length === 0 && <div className="card" style={{ padding: 16, fontSize: 13, color: 'var(--text3)' }}>No report mappings — click <b>↻ Reseed defaults</b> (or run migration 207).</div>}
    </div>
  )
}
