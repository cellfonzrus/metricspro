'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'

// Generic FTP-pull sweep (Theme 6). Configure a vendor's FTP (host/creds/folder) and filename →
// upload-type patterns; the backend pulls new files on a schedule and routes each to the right parser.
// Nothing hard-coded — works for B2B Soft or any vendor that FTP-pushes report files.
const UPLOAD_TYPES = ['sales', 'daily_sales', 'payment_detail', 'mi_report', 'dlar_rep', 'dlar_store', 'comp_report', 'catalog']
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }
const lbl: React.CSSProperties = { fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 2 }

export default function FtpImportsPage() {
  const [cfg, setCfg] = useState<any>({ port: 21, passive: true, remote_dir: '/', patterns: [], frequency: 'daily', hour: 7 })
  const [pwd, setPwd] = useState('')
  const [test, setTest] = useState<any>(null)
  const [processed, setProcessed] = useState<any[]>([])
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    api('/api/v1/commcalc/ftp-sweep/config').then((c: any) => setCfg({ ...c, patterns: c.patterns || [] })).catch(() => {})
    api('/api/v1/commcalc/ftp-sweep/processed').then((p: any) => setProcessed(p || [])).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  const set = (patch: any) => setCfg((c: any) => ({ ...c, ...patch }))
  const setPat = (i: number, patch: any) => setCfg((c: any) => ({ ...c, patterns: c.patterns.map((p: any, j: number) => j === i ? { ...p, ...patch } : p) }))
  const addPat = () => setCfg((c: any) => ({ ...c, patterns: [...(c.patterns || []), { pattern: '', upload_type: 'sales', note: '' }] }))
  const delPat = (i: number) => setCfg((c: any) => ({ ...c, patterns: c.patterns.filter((_: any, j: number) => j !== i) }))

  const body = () => ({ ...cfg, password: pwd || undefined })

  async function save() {
    setBusy('save')
    try { await api('/api/v1/commcalc/ftp-sweep/config', { method: 'PUT', body: JSON.stringify(body()) }); setPwd(''); setMsg('✅ Saved.'); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
  }
  async function testConn() {
    setBusy('test'); setTest(null)
    try { const r: any = await api('/api/v1/commcalc/ftp-sweep/test', { method: 'POST', body: JSON.stringify(body()) }); setTest(r); setMsg(`✅ Connected — ${r.count} files in folder.`) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
  }
  async function runNow() {
    setBusy('run')
    try { const r: any = await api('/api/v1/commcalc/ftp-sweep/run-now', { method: 'POST', body: '{}' }); setMsg(r.ok ? `✅ Ingested ${r.ingested} file(s).` : `❌ ${r.error}`); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
  }

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔁 FTP Auto-Import</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Pull report files a vendor (e.g. B2B Soft) FTP-pushes, and route each filename to its upload parser. All configured here — nothing hard-coded.
        </p>
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 12 }}>
          <div><label style={lbl}>Host</label><input style={{ ...sel, width: '100%' }} placeholder="boxNNNN.bluehost.com" value={cfg.host || ''} onChange={e => set({ host: e.target.value })} /></div>
          <div><label style={lbl}>Port</label><input style={{ ...sel, width: '100%' }} value={cfg.port || 21} onChange={e => set({ port: Number(e.target.value) || 21 })} /></div>
          <div><label style={lbl}>Username</label><input style={{ ...sel, width: '100%' }} placeholder="b2b@metricspro.tech" value={cfg.username || ''} onChange={e => set({ username: e.target.value })} /></div>
          <div><label style={lbl}>Password {cfg.has_password && <span style={{ color: '#16794a' }}>(set)</span>}</label><input type="password" style={{ ...sel, width: '100%' }} placeholder={cfg.has_password ? '•••• keep' : 'password'} value={pwd} onChange={e => setPwd(e.target.value)} /></div>
          <div><label style={lbl}>Remote folder</label><input style={{ ...sel, width: '100%' }} placeholder="/b2b-reports/" value={cfg.remote_dir || ''} onChange={e => set({ remote_dir: e.target.value })} /></div>
          <div><label style={lbl}>Security</label>
            <label style={{ fontSize: 12, display: 'block' }}><input type="checkbox" checked={!!cfg.use_tls} onChange={e => set({ use_tls: e.target.checked })} /> FTP_TLS</label>
            <label style={{ fontSize: 12, display: 'block' }}><input type="checkbox" checked={cfg.passive !== false} onChange={e => set({ passive: e.target.checked })} /> Passive</label>
          </div>
          <div><label style={lbl}>Schedule</label>
            <select style={{ ...sel, width: '100%' }} value={cfg.frequency || 'daily'} onChange={e => set({ frequency: e.target.value })}><option value="daily">daily</option><option value="weekly">weekly</option></select>
          </div>
          <div><label style={lbl}>Hour (0–23)</label><input style={{ ...sel, width: '100%' }} value={cfg.hour ?? 7} onChange={e => set({ hour: Number(e.target.value) || 0 })} /></div>
          <div><label style={lbl}>Auto-run</label><label style={{ fontSize: 12 }}><input type="checkbox" checked={!!cfg.enabled} onChange={e => set({ enabled: e.target.checked })} /> enabled</label></div>
        </div>

        <div style={{ marginTop: 14, fontWeight: 600, fontSize: 13 }}>Filename → upload type</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 4 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>{['Filename pattern (glob)', 'Routes to', 'Note', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
          <tbody>
            {(cfg.patterns || []).map((p: any, i: number) => (
              <tr key={i}>
                <td style={cell}><input style={{ ...sel, width: '100%' }} placeholder="*Sales-Transaction-Details*" value={p.pattern || ''} onChange={e => setPat(i, { pattern: e.target.value })} /></td>
                <td style={cell}><select style={sel} value={p.upload_type} onChange={e => setPat(i, { upload_type: e.target.value })}>{UPLOAD_TYPES.map(u => <option key={u} value={u}>{u}</option>)}</select></td>
                <td style={cell}><input style={{ ...sel, width: '100%' }} placeholder="optional" value={p.note || ''} onChange={e => setPat(i, { note: e.target.value })} /></td>
                <td style={cell}><button className="btn btn-secondary" style={{ fontSize: 12, color: '#dc2626' }} onClick={() => delPat(i)}>✕</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <button className="btn btn-secondary" style={{ fontSize: 12, marginTop: 6 }} onClick={addPat}>+ Add pattern</button>

        <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <button className="btn btn-primary" disabled={busy === 'save'} onClick={save}>Save</button>
          <button className="btn btn-secondary" disabled={busy === 'test'} onClick={testConn}>{busy === 'test' ? 'Testing…' : 'Test connection'}</button>
          <button className="btn btn-secondary" disabled={busy === 'run'} onClick={runNow}>{busy === 'run' ? 'Running…' : 'Run now'}</button>
          {cfg.last_status && <span style={{ fontSize: 12, color: 'var(--text3)' }}>Last: {cfg.last_status} {cfg.last_run_at ? `· ${new Date(cfg.last_run_at).toLocaleString()}` : ''}</span>}
          {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
        </div>
      </div>

      {test && (
        <div className="card" style={{ padding: 14, marginBottom: 14 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>Folder contents ({test.count})</div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              {(test.files || []).map((f: any) => (
                <tr key={f.name}>
                  <td style={cell}>{f.name}</td>
                  <td style={{ ...cell, color: 'var(--text3)', fontSize: 12 }}>{f.size ? `${(f.size / 1024).toFixed(0)} KB` : ''}</td>
                  <td style={cell}>{f.matches ? <span className="badge" style={{ background: '#16794a', color: '#fff', fontSize: 11 }}>→ {f.matches}</span> : <span style={{ color: 'var(--text3)', fontSize: 12 }}>no pattern</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: '10px 14px', fontWeight: 600, fontSize: 13 }}>Recently imported</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            {processed.map(p => (
              <tr key={p.id}>
                <td style={cell}>{p.filename}</td>
                <td style={{ ...cell, fontSize: 12 }}>{p.upload_type}</td>
                <td style={cell}>{p.status === 'ok' ? <span style={{ color: '#16794a' }}>✓ {p.rows_saved} rows</span> : <span style={{ color: '#dc2626' }}>✕ {p.detail}</span>}</td>
                <td style={{ ...cell, fontSize: 11, color: 'var(--text3)' }}>{p.processed_at ? new Date(p.processed_at).toLocaleString() : ''}</td>
              </tr>
            ))}
            {processed.length === 0 && <tr><td style={{ ...cell, color: 'var(--text3)', textAlign: 'center', padding: 24 }} colSpan={4}>Nothing imported yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
