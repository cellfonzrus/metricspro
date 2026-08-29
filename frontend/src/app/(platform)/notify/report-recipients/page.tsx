'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'

// Unified report → designated recipient routing (Theme 4). ONE page to set who each report is sent
// to (email/WhatsApp). Different reports route to different people. Any module's "send to the
// designated person" calls POST /notify/send-to-designated, which reads this config.
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '8px', borderTop: '1px solid var(--border)', fontSize: 13, verticalAlign: 'top' }

type Draft = { recipient_ids: string[]; ad_hoc_emails: string; ad_hoc_phones: string; channels: string[]; is_active: boolean; configured: boolean; label: string }

export default function ReportRecipientsPage() {
  const [reports, setReports] = useState<any[]>([])
  const [recipients, setRecipients] = useState<any[]>([])
  const [draft, setDraft] = useState<Record<string, Draft>>({})
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    api('/api/v1/notify/recipients').then((r: any) => setRecipients(r?.saved || [])).catch(() => {})
    api('/api/v1/notify/report-config').then((r: any) => {
      const list = r?.reports || []
      setReports(list)
      const d: Record<string, Draft> = {}
      for (const x of list) d[x.report_key] = {
        recipient_ids: x.recipient_ids || [], ad_hoc_emails: (x.ad_hoc_emails || []).join(', '),
        ad_hoc_phones: (x.ad_hoc_phones || []).join(', '), channels: x.channels || ['email'],
        is_active: x.is_active !== false, configured: x.configured, label: x.label,
      }
      setDraft(d)
    }).catch((e: any) => setMsg('Load failed: ' + (e?.message || e)))
  }, [])
  useEffect(() => { load() }, [load])

  const setRow = (rk: string, patch: Partial<Draft>) => setDraft(d => ({ ...d, [rk]: { ...d[rk], ...patch } }))
  const toggleChan = (rk: string, ch: string) => setDraft(d => { const cur = d[rk].channels; const next = cur.includes(ch) ? cur.filter(c => c !== ch) : [...cur, ch]; return { ...d, [rk]: { ...d[rk], channels: next } } })

  async function saveRow(rk: string) {
    const r = draft[rk]
    setBusy(rk)
    try {
      await api(`/api/v1/notify/report-config/${rk}`, { method: 'PUT', body: JSON.stringify({
        recipient_ids: r.recipient_ids,
        ad_hoc_emails: r.ad_hoc_emails.split(',').map(s => s.trim()).filter(Boolean),
        ad_hoc_phones: r.ad_hoc_phones.split(',').map(s => s.trim()).filter(Boolean),
        channels: r.channels, is_active: r.is_active }) })
      setRow(rk, { configured: true }); setMsg(`✅ Saved routing for ${r.label}.`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
  }
  async function sendNow(rk: string) {
    setBusy(rk)
    try {
      const res: any = await api('/api/v1/notify/send-to-designated', { method: 'POST', body: JSON.stringify({ report_key: rk }) })
      setMsg(res?.skipped ? `⚠️ ${draft[rk].label}: ${res.skipped}` : `✅ ${draft[rk].label}: sent ${res?.sent ?? 0}, failed ${res?.failed ?? 0}.`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
  }

  const recName = (r: any) => `${r.name || '—'}${r.email ? ` · ${r.email}` : ''}${r.phone ? ` · ${r.phone}` : ''}`

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📬 Report Recipients</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          One place to choose the designated person for every report — different reports can go to different people. Every &quot;send to the designated person&quot; in the app reads this routing.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <Link href="/notify" className="btn btn-secondary" style={{ fontSize: 13, textDecoration: 'none' }}>＋ Manage recipients</Link>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{recipients.length} saved recipient{recipients.length === 1 ? '' : 's'}. Add people there first, then assign them per report here.</span>
        {msg && <span style={{ fontSize: 13, marginLeft: 'auto' }}>{msg}</span>}
      </div>

      <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 920 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Report', 'Recipients', 'Also send to (ad-hoc)', 'Channels', 'On', ''].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {reports.map(rep => {
              const r = draft[rep.report_key]; if (!r) return null
              return (
                <tr key={rep.report_key}>
                  <td style={cell}><b>{r.label}</b>{r.configured && <div style={{ fontSize: 10, color: '#16794a' }}>routed</div>}<div style={{ fontSize: 10, color: 'var(--text3)' }}>{rep.report_key}</div></td>
                  <td style={cell}>
                    <select multiple value={r.recipient_ids} size={Math.min(4, Math.max(2, recipients.length))} style={{ ...sel, minWidth: 230, height: 'auto' }}
                      onChange={e => setRow(rep.report_key, { recipient_ids: Array.from(e.target.selectedOptions).map(o => o.value) })}>
                      {recipients.map(rc => <option key={rc.id} value={rc.id}>{recName(rc)}</option>)}
                    </select>
                    {recipients.length === 0 && <div style={{ fontSize: 11, color: 'var(--text3)' }}>No saved recipients yet.</div>}
                  </td>
                  <td style={cell}>
                    <input style={{ ...sel, width: 180 }} placeholder="email, email…" value={r.ad_hoc_emails} onChange={e => setRow(rep.report_key, { ad_hoc_emails: e.target.value })} />
                    <input style={{ ...sel, width: 180, marginTop: 4 }} placeholder="phone, phone…" value={r.ad_hoc_phones} onChange={e => setRow(rep.report_key, { ad_hoc_phones: e.target.value })} />
                  </td>
                  <td style={cell}>
                    <label style={{ display: 'block', fontSize: 12 }}><input type="checkbox" checked={r.channels.includes('email')} onChange={() => toggleChan(rep.report_key, 'email')} /> Email</label>
                    <label style={{ display: 'block', fontSize: 12 }}><input type="checkbox" checked={r.channels.includes('whatsapp')} onChange={() => toggleChan(rep.report_key, 'whatsapp')} /> WhatsApp</label>
                  </td>
                  <td style={cell}><input type="checkbox" checked={r.is_active} onChange={e => setRow(rep.report_key, { is_active: e.target.checked })} /></td>
                  <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                    <button className="btn btn-primary" style={{ fontSize: 12 }} disabled={busy === rep.report_key} onClick={() => saveRow(rep.report_key)}>Save</button>
                    <button className="btn btn-secondary" style={{ fontSize: 12, marginTop: 4 }} disabled={busy === rep.report_key} onClick={() => sendNow(rep.report_key)}>Send now</button>
                  </td>
                </tr>
              )
            })}
            {reports.length === 0 && <tr><td colSpan={6} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>Loading reports…</td></tr>}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
        Hold Ctrl/Cmd to select multiple recipients. &quot;Send now&quot; delivers the current period&apos;s report to its designated recipients immediately (the same engine the scheduled subscriptions use).
      </p>
    </div>
  )
}
