'use client'
import { useState, useEffect, useCallback, Fragment } from 'react'
import { api } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

// Denied-Appeal Commission Recovery. A denied appeal is RECOVERABLE when the line paid/activated after
// the denial → claim the commission back from the carrier (within the claw-back window). Rebuild the
// ledger, review the buckets, and roll recoverable devices into a weekly claim with rebuttals.
const TONE: Record<string, string> = {
  recoverable: 'badge-green', expired: 'badge-red', not_recoverable: 'badge-slate', needs_data: 'badge-amber',
}
const money = (n: any) => '$' + (Number(n) || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export default function RecoveryPage() {
  const [data, setData] = useState<any>(null)
  const [status, setStatus] = useState('recoverable')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [cfg, setCfg] = useState<any>(null)
  const [cfgOpen, setCfgOpen] = useState(false)
  const [claims, setClaims] = useState<any[]>([])
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [recipEmails, setRecipEmails] = useState('')
  const [recipWa, setRecipWa] = useState('')

  const load = useCallback((st: string) => {
    api(`/api/v1/recovery/ledger?status=${encodeURIComponent(st)}`).then(setData).catch((e: any) => setMsg(String(e?.message || e)))
    api('/api/v1/recovery/claims').then((r: any) => setClaims(r.claims || [])).catch(() => {})
  }, [])
  useEffect(() => {
    load(status)
    api('/api/v1/recovery/config').then((r: any) => {
      setCfg(r.config)
      const rc = r.config?.recipients || []
      setRecipEmails(rc.filter((x: any) => x.email).map((x: any) => x.email).join(', '))
      setRecipWa(rc.filter((x: any) => x.whatsapp).map((x: any) => x.whatsapp).join(', '))
    }).catch(() => {})
  }, [load, status])

  async function sendClaim(id: string) {
    setBusy('send-' + id); setMsg('')
    try { const r = await api(`/api/v1/recovery/claims/${id}/send`, { method: 'POST', body: '{}' }); setMsg(r.count ? `Sent to ${r.delivered.join(', ')}.` : 'No recipients configured — add them in ⚙️ Settings.') }
    catch (e: any) { setMsg('Send failed: ' + (e?.message || e)) } finally { setBusy('') }
  }

  async function run(kind: 'rebuild' | 'claim') {
    setBusy(kind); setMsg('')
    try {
      const r = await api(`/api/v1/recovery/${kind}`, { method: 'POST' })
      if (kind === 'rebuild') setMsg(`Rebuilt: ${r.summary?.recoverable || 0} recoverable (${money(r.summary?.recoverable_amount)}), ${r.summary?.expired || 0} expired, ${r.summary?.needs_data || 0} need data.`)
      else setMsg(r.claim ? `Claim created: ${r.claim.device_count} devices, ${money(r.claim.total_amount)}.` : (r.message || 'No new recoverable devices.'))
      load(status)
    } catch (e: any) { setMsg('Failed: ' + (e?.message || e) + ' — has migration 098 been run?') }
    finally { setBusy('') }
  }

  async function saveCfg() {
    setBusy('cfg')
    const recipients = [
      ...recipEmails.split(',').map(s => s.trim()).filter(Boolean).map(email => ({ email })),
      ...recipWa.split(',').map(s => s.trim()).filter(Boolean).map(whatsapp => ({ whatsapp })),
    ]
    try { const r = await api('/api/v1/recovery/config', { method: 'PUT', body: JSON.stringify({ ...cfg, recipients }) }); setCfg(r.config); setMsg('Settings saved.') }
    catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) } finally { setBusy('') }
  }

  const recoveryRows: any[] = data?.rows || []
  const recoveryCols: ExportColumn[] = [
    { header: 'Store', field: 'store', role: 'store', get: (r: any) => r.store },
    { header: 'Device', field: 'device_model', get: (r: any) => r.device_model },
    { header: 'IMEI', field: 'imei', get: (r: any) => r.imei },
    { header: 'MDN', field: 'mdn', get: (r: any) => r.mdn },
    { header: 'Denied', field: 'denied_date', get: (r: any) => r.denied_date },
    { header: 'Owed', field: 'owed_amount', money: true, get: (r: any) => r.owed_amount || 0 },
    { header: 'Evidence', field: 'evidence', get: (r: any) => r.evidence ? `${r.evidence.type} ${r.evidence.date || ''}`.trim() : '' },
  ]

  const b = data?.buckets || {}
  const tiles = [
    { k: 'recoverable', label: 'Recoverable', sub: money(b.recoverable?.owed) },
    { k: 'expired', label: 'Expired (missed window)', sub: money(b.expired?.owed) },
    { k: 'not_recoverable', label: 'Not recoverable', sub: `${b.not_recoverable?.count || 0} devices` },
    { k: 'needs_data', label: 'Needs payment data', sub: `${b.needs_data?.count || 0} devices` },
  ]
  const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 12, background: 'var(--surface)', padding: 16, marginBottom: 16 }
  const cell: React.CSSProperties = { padding: '7px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 800, margin: 0 }}>💰 Appeal Recovery — Commission Claw-back</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Denied appeals whose line later paid or activated → claim the commission back before the {cfg?.clawback_window_days || 45}-day window closes.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-secondary" disabled={!!busy} onClick={() => run('rebuild')}>{busy === 'rebuild' ? '…' : '↻ Rebuild'}</button>
          <button className="btn btn-secondary" onClick={() => setCfgOpen(v => !v)}>⚙️ Settings</button>
        </div>
      </div>
      {msg && <div style={{ ...card, background: 'var(--surface2, #f6f7f9)', fontSize: 13 }}>{msg}</div>}

      {cfgOpen && cfg && (
        <div style={card}>
          <div style={{ fontWeight: 700, marginBottom: 10 }}>Recovery settings</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
            <label style={{ fontSize: 12 }}>Claw-back window (days)
              <input type="number" value={cfg.clawback_window_days} onChange={e => setCfg({ ...cfg, clawback_window_days: +e.target.value })}
                style={{ width: '100%', padding: 6, marginTop: 3, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)' }} /></label>
            <label style={{ fontSize: 12 }}>Claim look-back (days)
              <input type="number" value={cfg.lookback_days} onChange={e => setCfg({ ...cfg, lookback_days: +e.target.value })}
                style={{ width: '100%', padding: 6, marginTop: 3, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)' }} /></label>
            <label style={{ fontSize: 12 }}>Evidence
              <select value={cfg.evidence_mode} onChange={e => setCfg({ ...cfg, evidence_mode: e.target.value })}
                style={{ width: '100%', padding: 6, marginTop: 3, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)' }}>
                <option value="payment_or_active">Payment OR active status</option>
                <option value="payment_only">Payment only</option>
                <option value="any">Any (also a later re-sale)</option>
              </select></label>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 12 }}>
            <label style={{ fontSize: 12 }}>Weekly claim → recipient emails (comma-separated)
              <input value={recipEmails} onChange={e => setRecipEmails(e.target.value)} placeholder="ops@company.com, manager@company.com"
                style={{ width: '100%', padding: 6, marginTop: 3, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)' }} /></label>
            <label style={{ fontSize: 12 }}>…and WhatsApp #s (comma-separated)
              <input value={recipWa} onChange={e => setRecipWa(e.target.value)} placeholder="15551234567, 15559876543"
                style={{ width: '100%', padding: 6, marginTop: 3, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)' }} /></label>
          </div>
          <div style={{ display: 'flex', gap: 12, marginTop: 10, fontSize: 13, alignItems: 'center', flexWrap: 'wrap' }}>
            <label><input type="checkbox" checked={!!cfg.enabled} onChange={e => setCfg({ ...cfg, enabled: e.target.checked })} /> Weekly auto-claim</label>
            <label>on&nbsp;
              <select value={cfg.weekly_day_of_week} onChange={e => setCfg({ ...cfg, weekly_day_of_week: +e.target.value })}
                style={{ padding: 5, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)' }}>
                {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((d, i) => <option key={i} value={i}>{d}</option>)}
              </select></label>
            <label>at&nbsp;
              <input type="number" min={0} max={23} value={cfg.weekly_hour} onChange={e => setCfg({ ...cfg, weekly_hour: +e.target.value })}
                style={{ width: 60, padding: 5, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)' }} />:00 UTC</label>
            <label style={{ marginLeft: 12 }}><input type="checkbox" checked={!!cfg.match_mdn} onChange={e => setCfg({ ...cfg, match_mdn: e.target.checked })} /> Match phone #</label>
            <label><input type="checkbox" checked={!!cfg.match_imei} onChange={e => setCfg({ ...cfg, match_imei: e.target.checked })} /> Match IMEI</label>
            <button className="btn btn-primary" style={{ marginLeft: 'auto' }} disabled={busy === 'cfg'} onClick={saveCfg}>Save settings</button>
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
        {tiles.map(t => (
          <button key={t.k} onClick={() => setStatus(t.k)} style={{ ...card, margin: 0, cursor: 'pointer', textAlign: 'left',
            outline: status === t.k ? '2px solid var(--accent, #2563eb)' : 'none' }}>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t.label}</div>
            <div style={{ fontSize: 20, fontWeight: 800 }}>{b[t.k]?.count || 0}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{t.sub}</div>
          </button>
        ))}
      </div>

      <div style={card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div style={{ fontWeight: 700 }}>Devices — <span style={{ textTransform: 'capitalize' }}>{status.replace('_', ' ')}</span> ({recoveryRows.length})</div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            {recoveryRows.length > 0 && <ReportExportBar title={`Appeal Recovery — ${status.replace('_', ' ')}`} filename={`appeal_recovery_${status}`} columns={recoveryCols} rows={recoveryRows} />}
            {status === 'recoverable' && <button className="btn btn-primary" disabled={!!busy || !recoveryRows.length} onClick={() => run('claim')}>{busy === 'claim' ? '…' : '📄 Generate claim'}</button>}
          </div>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11 }}>
              <th style={{ padding: 6 }}>Store</th><th style={{ padding: 6 }}>Device</th><th style={{ padding: 6 }}>IMEI</th>
              <th style={{ padding: 6 }}>MDN</th><th style={{ padding: 6 }}>Denied</th><th style={{ padding: 6, textAlign: 'right' }}>Owed</th><th style={{ padding: 6 }}>Evidence</th></tr></thead>
            <tbody>
              {(data?.rows || []).map((r: any) => (
                <Fragment key={r.id}>
                  <tr onClick={() => setOpen(o => ({ ...o, [r.id]: !o[r.id] }))} style={{ cursor: r.rebuttal ? 'pointer' : 'default' }}>
                    <td style={cell}>{r.store}</td>
                    <td style={cell}>{r.device_model}</td>
                    <td style={cell}>{r.imei}</td>
                    <td style={cell}>{r.mdn}</td>
                    <td style={cell}>{r.denied_date}</td>
                    <td style={{ ...cell, textAlign: 'right' }}>{money(r.owed_amount)}</td>
                    <td style={cell}>{r.evidence ? `${r.evidence.type} ${r.evidence.date || ''}` : '—'}</td>
                  </tr>
                  {open[r.id] && r.rebuttal && (
                    <tr><td colSpan={7} style={{ ...cell, background: 'var(--surface2, #f6f7f9)', color: 'var(--text2)' }}>📝 {r.rebuttal}</td></tr>
                  )}
                </Fragment>
              ))}
              {!(data?.rows?.length) && <tr><td colSpan={7} style={{ ...cell, color: 'var(--text3)' }}>Nothing here. Run ↻ Rebuild after loading ePay payment/MI data.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Claim batches</div>
        {!claims.length && <div style={{ color: 'var(--text3)', fontSize: 13 }}>No claims yet. Generate one from the Recoverable bucket.</div>}
        {claims.map(c => (
          <div key={c.id} style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '7px 0', borderTop: '1px solid var(--border)', fontSize: 13 }}>
            <span className={`badge ${c.status === 'paid' ? 'badge-green' : c.status === 'rejected' ? 'badge-red' : 'badge-blue'}`} style={{ textTransform: 'capitalize', minWidth: 80, textAlign: 'center' }}>{c.status}</span>
            <div style={{ flex: 1 }}>{c.period_label} · {c.device_count} devices · {money(c.total_amount)}</div>
            <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 8px' }} disabled={busy === 'send-' + c.id} onClick={() => sendClaim(c.id)}>{busy === 'send-' + c.id ? '…' : '📤 Send claim'}</button>
            <span style={{ color: 'var(--text3)', fontSize: 12 }}>{String(c.created_at || '').slice(0, 10)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
