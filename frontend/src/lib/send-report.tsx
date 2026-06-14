'use client'
// Send-report button + modal — sits beside <ExportButtons> on every report page.
// The backend (report_registry + render) owns file generation, so the button only
// passes report_key + the page's current filters; it never builds an ExportPayload.
import { useState, useEffect, useCallback } from 'react'
import { api, ORG_ID } from '@/lib/client'

type Emp = { name: string; email: string | null; phone: string | null; store: string | null }
type Saved = { id: string; name: string | null; email: string | null; phone: string | null }

const overlay: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1000,
  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
}
const modal: React.CSSProperties = {
  background: 'var(--surface,#fff)', borderRadius: 12, padding: 20, width: 'min(560px,96vw)',
  maxHeight: '88vh', overflowY: 'auto', boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
}
const chk: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }

export function SendReportButton({ reportKey, filters, compact }: {
  reportKey: string
  filters: Record<string, any>
  compact?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [employees, setEmployees] = useState<Emp[]>([])
  const [saved, setSaved] = useState<Saved[]>([])
  const [health, setHealth] = useState<{ email_configured: boolean; whatsapp_configured: boolean } | null>(null)
  const [picked, setPicked] = useState<Record<string, boolean>>({})  // key = email|phone token
  const [manualEmails, setManualEmails] = useState('')
  const [manualPhones, setManualPhones] = useState('')
  const [chEmail, setChEmail] = useState(true)
  const [chWhatsapp, setChWhatsapp] = useState(false)
  const [fmtXlsx, setFmtXlsx] = useState(true)
  const [fmtPdf, setFmtPdf] = useState(true)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string>('')

  const load = useCallback(async () => {
    try {
      const [r, h] = await Promise.all([
        api(`/api/v1/notify/recipients?org_id=${ORG_ID}`),
        api(`/api/v1/notify/health`),
      ])
      setEmployees(r.employees || [])
      setSaved(r.saved || [])
      setHealth(h)
    } catch (e: any) { setResult('Could not load recipients: ' + (e?.message || e)) }
  }, [])

  useEffect(() => { if (open) load() }, [open, load])

  function collect() {
    const emails = new Set<string>()
    const phones = new Set<string>()
    employees.forEach(e => {
      if (picked['e:' + e.email] && e.email) emails.add(e.email)
      if (picked['p:' + e.phone] && e.phone) phones.add(e.phone)
    })
    saved.forEach(s => {
      if (picked['se:' + s.id]) { if (s.email) emails.add(s.email); if (s.phone) phones.add(s.phone) }
    })
    manualEmails.split(/[,\s;]+/).map(x => x.trim()).filter(Boolean).forEach(x => emails.add(x))
    manualPhones.split(/[,\s;]+/).map(x => x.trim()).filter(Boolean).forEach(x => phones.add(x))
    return { emails: [...emails], phones: [...phones] }
  }

  async function send() {
    const channels = [chEmail && 'email', chWhatsapp && 'whatsapp'].filter(Boolean)
    const formats = [fmtXlsx && 'xlsx', fmtPdf && 'pdf'].filter(Boolean)
    const { emails, phones } = collect()
    if (!channels.length) { setResult('Pick at least one channel.'); return }
    if (!formats.length) { setResult('Pick at least one format.'); return }
    if (chEmail && !emails.length) { setResult('Email selected but no email recipients.'); return }
    if (chWhatsapp && !phones.length) { setResult('WhatsApp selected but no phone recipients.'); return }
    setBusy(true); setResult('')
    try {
      const res = await api(`/api/v1/notify/send?org_id=${ORG_ID}`, {
        method: 'POST',
        body: JSON.stringify({ report_key: reportKey, filters, channels, formats, emails, phones, message }),
      })
      setResult(`✓ Sent: ${res.sent} ok, ${res.failed} failed.`)
    } catch (e: any) {
      setResult('Send failed: ' + (e?.message || e))
    } finally { setBusy(false) }
  }

  const btnStyle: React.CSSProperties = { fontSize: compact ? 12 : 13, padding: compact ? '5px 10px' : '6px 12px' }

  return (
    <>
      <button className="btn btn-secondary" style={btnStyle} onClick={() => setOpen(true)}>📤 Send</button>
      {open && (
        <div style={overlay} onClick={() => !busy && setOpen(false)}>
          <div style={modal} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <h3 style={{ margin: 0 }}>Send report</h3>
              <button className="btn btn-secondary" style={{ padding: '2px 8px' }} onClick={() => setOpen(false)}>✕</button>
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted,#888)', marginBottom: 12 }}>
              Report: <b>{reportKey}</b>{Object.keys(filters || {}).length ? ` · filters: ${JSON.stringify(filters)}` : ''}
            </div>

            <div style={{ display: 'flex', gap: 20, marginBottom: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 4 }}>Channels</div>
                <label style={chk}><input type="checkbox" checked={chEmail} onChange={e => setChEmail(e.target.checked)} /> Email
                  {health && !health.email_configured && <span style={{ color: '#c00', fontSize: 11 }}> (not configured)</span>}</label>
                <label style={chk}><input type="checkbox" checked={chWhatsapp} onChange={e => setChWhatsapp(e.target.checked)} /> WhatsApp
                  {health && !health.whatsapp_configured && <span style={{ color: '#c00', fontSize: 11 }}> (not configured)</span>}</label>
              </div>
              <div>
                <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 4 }}>Formats</div>
                <label style={chk}><input type="checkbox" checked={fmtXlsx} onChange={e => setFmtXlsx(e.target.checked)} /> Excel (.xlsx)</label>
                <label style={chk}><input type="checkbox" checked={fmtPdf} onChange={e => setFmtPdf(e.target.checked)} /> PDF</label>
              </div>
            </div>

            <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 4 }}>Recipients</div>
            <div style={{ maxHeight: 180, overflowY: 'auto', border: '1px solid var(--border,#ddd)', borderRadius: 8, padding: 8, marginBottom: 8 }}>
              {saved.length > 0 && <div style={{ fontSize: 11, color: '#888', margin: '2px 0' }}>Saved</div>}
              {saved.map(s => (
                <label key={s.id} style={chk}>
                  <input type="checkbox" checked={!!picked['se:' + s.id]} onChange={e => setPicked(p => ({ ...p, ['se:' + s.id]: e.target.checked }))} />
                  {s.name || s.email || s.phone} <span style={{ color: '#999', fontSize: 11 }}>{s.email}{s.phone ? ` · ${s.phone}` : ''}</span>
                </label>
              ))}
              <div style={{ fontSize: 11, color: '#888', margin: '4px 0 2px' }}>Employees</div>
              {employees.length === 0 && <div style={{ fontSize: 12, color: '#999' }}>No employees with email/phone on file.</div>}
              {employees.map((e, i) => (
                <div key={i} style={{ display: 'flex', gap: 12 }}>
                  {e.email && <label style={chk}><input type="checkbox" checked={!!picked['e:' + e.email]} onChange={ev => setPicked(p => ({ ...p, ['e:' + e.email]: ev.target.checked }))} /> ✉ {e.name}</label>}
                  {e.phone && <label style={chk}><input type="checkbox" checked={!!picked['p:' + e.phone]} onChange={ev => setPicked(p => ({ ...p, ['p:' + e.phone]: ev.target.checked }))} /> 💬 {e.phone}</label>}
                </div>
              ))}
            </div>

            <input placeholder="Add emails (comma-separated)" value={manualEmails} onChange={e => setManualEmails(e.target.value)}
              style={{ width: '100%', padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border,#ddd)', fontSize: 13, marginBottom: 6 }} />
            <input placeholder="Add WhatsApp phones, E.164 e.g. +1813… (comma-separated)" value={manualPhones} onChange={e => setManualPhones(e.target.value)}
              style={{ width: '100%', padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border,#ddd)', fontSize: 13, marginBottom: 6 }} />
            <textarea placeholder="Optional message…" value={message} onChange={e => setMessage(e.target.value)}
              style={{ width: '100%', padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border,#ddd)', fontSize: 13, minHeight: 48, marginBottom: 10 }} />

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 12, color: result.startsWith('✓') ? 'green' : '#c00' }}>{result}</span>
              <button className="btn btn-primary" disabled={busy} onClick={send}>{busy ? '⏳ Sending…' : 'Send now'}</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
