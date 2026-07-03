'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

// DM cash pickup — see the day's cash envelopes, check off the ones collected with a note, confirm.
// On confirm, the assigned recipient gets an email + WhatsApp summary.
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const inp: React.CSSProperties = { ...sel, width: '100%' }
const cell: React.CSSProperties = { padding: '8px 10px', borderTop: '1px solid var(--border)', fontSize: 13, verticalAlign: 'middle' }

export default function CashPickupPage() {
  const { user, permissions } = useAuth()
  const [date, setDate] = useState(localToday())
  const [market, setMarket] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [sel_, setSel] = useState<Record<string, boolean>>({})
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [cfg, setCfg] = useState<any>(null)
  const [cfgOpen, setCfgOpen] = useState(false)
  const [cfgMsg, setCfgMsg] = useState('')

  useEffect(() => { if (user?.market && permissions?.scope === 'market') setMarket(user.market) }, [user, permissions])
  useEffect(() => { api('/api/v1/closing/pickup-config').then(setCfg).catch(() => setCfg({})) }, [])

  const load = useCallback(() => {
    if (!date) return
    setLoading(true); setSel({}); setNotes({})
    api(`/api/v1/closing/pickups?date=${date}${market ? `&market=${encodeURIComponent(market)}` : ''}`)
      .then(setData).catch(console.error).finally(() => setLoading(false))
  }, [date, market])
  useEffect(() => { load() }, [load])

  const envelopes: any[] = data?.envelopes || []
  const key = (e: any) => `${e.store_code || ''}|${e.employee_name || ''}`
  const ready = envelopes.filter(e => !e.picked_up)
  const selectedKeys = ready.filter(e => sel_[key(e)])
  const selTotal = selectedKeys.reduce((s, e) => s + (e.cash || 0), 0)

  async function confirm() {
    if (!selectedKeys.length) { setMsg('Select at least one envelope.'); return }
    setBusy(true); setMsg('')
    try {
      const r = await api('/api/v1/closing/pickup', { method: 'POST', body: JSON.stringify({
        date, picked_up_by: user?.full_name || 'DM',
        items: selectedKeys.map(e => ({ store_code: e.store_code, store_name: e.store_name, employee_name: e.employee_name, amount: e.cash, note: notes[key(e)] || '' })),
      }) })
      const n = (r.notify || []) as any[]
      const sent = n.filter(x => x.ok).map(x => x.channel)
      const failed = n.filter(x => !x.ok)
      setMsg(`✅ ${r.count} envelope(s) picked up (${fmt(r.total)}).` +
        (sent.length ? ` Notified: ${sent.join(', ')}.` : '') +
        (failed.length ? ` ⚠️ ${failed.map(f => `${f.channel}: ${f.detail}`).join('; ')}` : ''))
      load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false) }
  }

  async function saveCfg() {
    setCfgMsg('')
    try { const r = await api('/api/v1/closing/pickup-config', { method: 'PUT', body: JSON.stringify(cfg) }); setCfg(r); setCfgMsg('✅ Saved.') }
    catch (e: any) { setCfgMsg('❌ ' + (e?.message || e)) }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💵 Cash Pickup</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Check off each cash envelope you collected, add a note, and confirm. The assigned recipient is notified by email + WhatsApp.</p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      {/* Recipient config */}
      <div className="card" style={{ padding: 14, marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }} onClick={() => setCfgOpen(o => !o)}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>🔔 Pickup notification recipient {cfg?.recipient_email || cfg?.recipient_whatsapp ? '' : '— not set'}</span>
          <span style={{ fontSize: 12, color: 'var(--text3)' }}>{cfgOpen ? '▾' : '▸'}</span>
        </div>
        {cfgOpen && cfg && (
          <div style={{ marginTop: 12, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <L t="Name"><input style={{ ...inp, width: 160 }} value={cfg.recipient_name || ''} onChange={e => setCfg({ ...cfg, recipient_name: e.target.value })} /></L>
            <L t={`Email${cfg.email_configured ? '' : ' (server not configured)'}`}><input style={{ ...inp, width: 220 }} value={cfg.recipient_email || ''} onChange={e => setCfg({ ...cfg, recipient_email: e.target.value })} placeholder="name@company.com" /></L>
            <L t={`WhatsApp${cfg.whatsapp_configured ? '' : ' (server not configured)'}`}><input style={{ ...inp, width: 180 }} value={cfg.recipient_whatsapp || ''} onChange={e => setCfg({ ...cfg, recipient_whatsapp: e.target.value })} placeholder="5162330422 or +1516…" /></L>
            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 5 }}><input type="checkbox" checked={cfg.notify_email !== false} onChange={e => setCfg({ ...cfg, notify_email: e.target.checked })} /> email</label>
            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 5 }}><input type="checkbox" checked={cfg.notify_whatsapp !== false} onChange={e => setCfg({ ...cfg, notify_whatsapp: e.target.checked })} /> whatsapp</label>
            <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={saveCfg}>Save</button>
            {cfgMsg && <span style={{ fontSize: 12 }}>{cfgMsg}</span>}
          </div>
        )}
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <input type="date" style={sel} value={date} onChange={e => setDate(e.target.value)} />
        {market && <span style={{ fontSize: 12, color: 'var(--text3)' }}>Market: {market}</span>}
        {data && <span style={{ fontSize: 13, color: 'var(--text2)' }}>{data.ready} ready · {data.collected} collected</span>}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : envelopes.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>No cash envelopes for {date}.</div>
      ) : (
        <>
          <div className="card table-wrapper" style={{ padding: 0 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['', 'Store', 'Rep', 'Cash', 'Envelope', 'Note / status'].map((h, i) =>
                  <th key={i} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {envelopes.map(e => {
                  const k = key(e); const done = e.picked_up
                  return (
                    <tr key={k} style={{ background: done ? 'var(--surface2)' : undefined }}>
                      <td style={cell}>{done ? '✅' : <input type="checkbox" checked={!!sel_[k]} onChange={ev => setSel(s => ({ ...s, [k]: ev.target.checked }))} />}</td>
                      <td style={cell}>{e.store_name || e.store_code || '—'}</td>
                      <td style={cell}>{e.employee_name || '—'}</td>
                      <td style={{ ...cell, fontWeight: 600 }}>{fmt(e.cash)}</td>
                      <td style={cell}>{(e.envelope_url || e.envelope_picture) ? <a href={e.envelope_url || e.envelope_picture} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>📷 view</a> : '—'}</td>
                      <td style={cell}>
                        {done
                          ? <span style={{ fontSize: 12, color: 'var(--text3)' }}>by {e.picked_up_by} · {e.picked_up_at ? new Date(e.picked_up_at).toLocaleString() : ''}{e.note ? ` · ${e.note}` : ''}</span>
                          : <input style={{ ...inp, minWidth: 200 }} placeholder="Note (optional)" value={notes[k] || ''} onChange={ev => setNotes(n => ({ ...n, [k]: ev.target.value }))} />}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 14, flexWrap: 'wrap' }}>
            <button className="btn btn-primary" style={{ fontSize: 14 }} disabled={busy || !selectedKeys.length} onClick={confirm}>
              {busy ? '⏳ Confirming…' : `✅ Confirm pickup (${selectedKeys.length} · ${fmt(selTotal)})`}
            </button>
            {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
          </div>
        </>
      )}
    </div>
  )
}

const L = ({ t, children }: { t: string; children: React.ReactNode }) => (
  <label style={{ fontSize: 11, color: 'var(--text3)' }}><div style={{ marginBottom: 3 }}>{t}</div>{children}</label>
)
