'use client'
import { useState, useEffect } from 'react'
import { api } from '@/lib/client'

// System access log viewer (super-admin only; the endpoint enforces it). Group by IP or actor to spot a
// scraper (many requests / distinct paths), or view raw rows with path, status, IP and GPS.
function twoDaysAgo(): string { const d = new Date(); d.setDate(d.getDate() - 2); return d.toISOString().slice(0, 10) }

export default function AccessLogPage() {
  const [group, setGroup] = useState<'ip' | 'actor' | 'raw'>('ip')
  const [dateFrom, setDateFrom] = useState(twoDaysAgo())
  const [anonOnly, setAnonOnly] = useState(false)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')
  const [blocks, setBlocks] = useState<any[]>([])

  async function load() {
    setLoading(true); setMsg('')
    try {
      const g = group === 'raw' ? '' : group
      const r: any = await api(`/api/v1/core/access-log?group=${g}&date_from=${dateFrom}&anonymous_only=${anonOnly}&limit=2000`)
      setData(r)
      if (r.ready === false) setMsg('Access log table not found — run migration 856.')
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)); setData(null) } finally { setLoading(false) }
  }
  async function loadBlocks() {
    try { const r: any = await api('/api/v1/core/ip-block'); setBlocks(r?.rows || []) } catch { /* mig 860 not run */ }
  }
  async function blockIp(ip: string) {
    if (!ip || ip === '(no-ip)') return
    const reason = window.prompt(`Block ${ip}? Optional reason:`, 'incident containment')
    if (reason === null) return
    try {
      await api('/api/v1/core/ip-block', { method: 'POST', body: JSON.stringify({ ip, reason }) })
      setMsg(`✅ Blocked ${ip}`); loadBlocks()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function unblockIp(ip: string) {
    try { await api('/api/v1/core/ip-block/remove', { method: 'POST', body: JSON.stringify({ ip }) }); setMsg(`Unblocked ${ip}`); loadBlocks() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function revokeAllSessions() {
    if (!window.confirm('Sign out ALL active sessions? Enforced only if session controls are on.')) return
    try { const r: any = await api('/api/v1/core/sessions/revoke', { method: 'POST', body: JSON.stringify({}) }); setMsg(`✅ Revoked ${r?.revoked ?? 0} session(s)${r?.enforced ? '' : ' — note: SESSION_ENFORCE is OFF, so this takes effect once enabled.'}`) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  useEffect(() => { load() }, [group, dateFrom, anonOnly]) // eslint-disable-line
  useEffect(() => { loadBlocks() }, []) // eslint-disable-line

  const rows: any[] = data?.rows || []
  const gmap = (lat: any, lng: any) => (lat != null && lng != null)
    ? <a href={`https://www.google.com/maps?q=${lat},${lng}`} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>{Number(lat).toFixed(4)},{Number(lng).toFixed(4)}</a>
    : <span style={{ color: 'var(--text3)' }}>—</span>

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Access Log</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Who accessed the system — path, status, IP, and GPS. Group by IP or user to spot a scraper.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        <div style={{ display: 'flex', background: 'var(--surface2)', padding: 3, borderRadius: 8, gap: 3 }}>
          {([['ip', 'By IP'], ['actor', 'By user'], ['raw', 'Raw']] as const).map(([k, l]) => (
            <button key={k} onClick={() => setGroup(k)} className="btn" style={{
              fontSize: 12.5, background: group === k ? 'white' : 'transparent',
              color: group === k ? 'var(--accent)' : 'var(--text2)', boxShadow: group === k ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
            }}>{l}</button>
          ))}
        </div>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Since <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} /></label>
        <label style={{ fontSize: 13, color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={anonOnly} onChange={e => setAnonOnly(e.target.checked)} /> Anonymous only
        </label>
        <button className="btn" disabled={loading} onClick={load}>{loading ? '…' : 'Refresh'}</button>
        <button className="btn" onClick={revokeAllSessions} style={{ marginLeft: 'auto', color: '#dc2626' }} title="Incident containment: sign out all active sessions">Revoke all sessions</button>
      </div>

      {msg && <div style={{ fontSize: 12.5, color: 'var(--text2)', background: 'var(--surface2)', borderRadius: 8, padding: '8px 12px', marginBottom: 12 }}>{msg}</div>}

      {blocks.length > 0 && (
        <div className="card" style={{ padding: '10px 14px', marginBottom: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 6 }}>Blocked IPs ({blocks.length})</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {blocks.map((b: any) => (
              <span key={b.ip} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: 'var(--surface2)', borderRadius: 6, padding: '3px 8px', fontSize: 12 }}>
                <span style={{ fontFamily: 'monospace' }}>{b.ip}</span>
                {b.expires_at && <span style={{ color: 'var(--text3)', fontSize: 10 }}>until {String(b.expires_at).replace('T', ' ').slice(0, 16)}</span>}
                <button onClick={() => unblockIp(b.ip)} style={{ border: 'none', background: 'none', color: '#dc2626', cursor: 'pointer', fontSize: 12 }} title="Unblock">✕</button>
              </span>
            ))}
          </div>
        </div>
      )}

      {data && (
        <div className="card" style={{ padding: 14, overflowX: 'auto' }}>
          {group !== 'raw' ? (
            <table style={{ width: '100%', fontSize: 12.5, borderCollapse: 'collapse' }}>
              <thead><tr>{[group === 'ip' ? 'IP' : 'User', 'Requests', 'Distinct paths', 'Anon', 'First seen', 'Last seen', 'GPS', 'User agent'].map((h, i) => <th key={h} style={{ textAlign: i === 0 ? 'left' : 'left', padding: '4px 12px 8px 0', color: 'var(--text3)' }}>{h}</th>)}</tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '4px 12px 4px 0', fontWeight: 600, fontFamily: 'monospace' }}>
                      {r.key}
                      {group === 'ip' && r.key && r.key !== '(no-ip)' && (
                        <button onClick={() => blockIp(r.key)} style={{ marginLeft: 8, border: '1px solid var(--border)', background: 'var(--surface2)', borderRadius: 5, padding: '1px 6px', fontSize: 10.5, color: '#dc2626', cursor: 'pointer' }} title="Block this IP">Block</button>
                      )}
                    </td>
                    <td style={{ padding: '4px 12px 4px 0', fontWeight: 700, color: r.requests > 500 ? '#dc2626' : 'var(--text)' }}>{r.requests}</td>
                    <td style={{ padding: '4px 12px 4px 0' }}>{r.distinct_paths}</td>
                    <td style={{ padding: '4px 12px 4px 0' }}>{r.anonymous ? <span className="badge badge-amber" style={{ fontSize: 10 }}>anon</span> : ''}</td>
                    <td style={{ padding: '4px 12px 4px 0', color: 'var(--text3)' }}>{String(r.first || '').replace('T', ' ').slice(0, 16)}</td>
                    <td style={{ padding: '4px 12px 4px 0', color: 'var(--text3)' }}>{String(r.last || '').replace('T', ' ').slice(0, 16)}</td>
                    <td style={{ padding: '4px 12px 4px 0' }}>{gmap(r.gps_lat, r.gps_lng)}</td>
                    <td style={{ padding: '4px 12px 4px 0', color: 'var(--text3)', maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.sample_ua}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={8} style={{ padding: 12, color: 'var(--text3)' }}>No access recorded in this window.</td></tr>}
              </tbody>
            </table>
          ) : (
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead><tr>{['Time', 'User', 'Method', 'Path', 'Status', 'IP', 'GPS'].map(h => <th key={h} style={{ textAlign: 'left', padding: '4px 12px 8px 0', color: 'var(--text3)' }}>{h}</th>)}</tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '3px 12px 3px 0', color: 'var(--text3)', whiteSpace: 'nowrap' }}>{String(r.created_at || '').replace('T', ' ').slice(0, 19)}</td>
                    <td style={{ padding: '3px 12px 3px 0' }}>{r.anonymous ? <span style={{ color: '#b45309' }}>anon</span> : (r.actor_email || r.actor_role || r.actor_auth_id || '—')}</td>
                    <td style={{ padding: '3px 12px 3px 0' }}>{r.method}</td>
                    <td style={{ padding: '3px 12px 3px 0', fontFamily: 'monospace', maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.path}</td>
                    <td style={{ padding: '3px 12px 3px 0', color: r.status >= 400 ? '#dc2626' : 'var(--text2)' }}>{r.status}</td>
                    <td style={{ padding: '3px 12px 3px 0', fontFamily: 'monospace' }}>
                      {r.ip}
                      {r.ip && <button onClick={() => blockIp(r.ip)} style={{ marginLeft: 6, border: '1px solid var(--border)', background: 'var(--surface2)', borderRadius: 5, padding: '0 5px', fontSize: 10, color: '#dc2626', cursor: 'pointer' }} title="Block this IP">Block</button>}
                    </td>
                    <td style={{ padding: '3px 12px 3px 0' }}>{gmap(r.gps_lat, r.gps_lng)}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={7} style={{ padding: 12, color: 'var(--text3)' }}>No access recorded in this window.</td></tr>}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}
