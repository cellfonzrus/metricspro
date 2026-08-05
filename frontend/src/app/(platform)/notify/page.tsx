'use client'
// Notify management — recipients, recurring subscriptions, and send history.
// On-demand sending lives on each report page via <SendReportButton>; this page
// manages saved recipients and the scheduled subscriptions that pg_cron fires.
import { useState, useEffect, useCallback } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import PhoneInput from '@/components/PhoneInput'

type Report = { key: string; label: string; filters: string[] }
type Saved = { id: string; name: string | null; email: string | null; phone: string | null }
type Emp = { name: string; email: string | null; phone: string | null; store: string | null }
type Sub = {
  id: string; name: string | null; report_key: string; filters: any; channels: string[]; formats: string[]
  recipient_ids: string[]; ad_hoc_emails: string[]; ad_hoc_phones: string[]
  frequency: string; day_of_week: number | null; day_of_month: number | null; hour: number
  timezone: string; is_active: boolean; next_run_at: string | null; last_run_at: string | null
}
type LogRow = {
  id: string; report_key: string; channel: string; target: string; status: string
  error: string | null; triggered_by: string | null; created_at: string
  delivery_status?: string | null; delivery_error?: string | null; delivery_updated_at?: string | null
  delivery_route?: string | null
}
// GET /notify/health — configuration truth (no network calls, no secrets). The whatsapp_* keys make the
// 2026-08-05 silent-failure class visible from inside the app instead of only in the Meta dashboard.
type Health = {
  email_configured: boolean; whatsapp_configured: boolean; from_email?: string | null
  whatsapp_template?: string | null; whatsapp_template_lang?: string | null
  whatsapp_graph_version?: string | null; whatsapp_doc_header?: boolean
  whatsapp_verify_token_set?: boolean; whatsapp_app_secret_set?: boolean
  whatsapp_webhook_ready?: boolean; whatsapp_window_tracking?: boolean
  whatsapp_window_hours?: number; whatsapp_freeform_when_unknown?: boolean
  whatsapp_webhook_url?: string
}

const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
// Placeholder hints for the RELATIVE date filters. A recurring schedule must not carry a frozen
// date: blank (or 'current'/'last') is resolved server-side on every run — see report_registry.
const FILTER_HINT: Record<string, string> = {
  period: ' (e.g. June 2026 / current / last)',
  thursday: ' — billing Friday (blank = current week / last)',
}
const card: React.CSSProperties = { background: 'var(--surface,#fff)', border: '1px solid var(--border,#e5e7eb)', borderRadius: 12, padding: 16, marginBottom: 16 }
const inp: React.CSSProperties = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border,#ddd)', fontSize: 13 }
const th: React.CSSProperties = { textAlign: 'left', padding: '8px 12px', fontSize: 12, color: '#666', borderBottom: '1px solid var(--border,#eee)' }
const td: React.CSSProperties = { padding: '8px 12px', fontSize: 13, borderBottom: '1px solid var(--border,#f3f4f6)' }
const d10 = (s: string | null) => (s ? String(s).slice(0, 16).replace('T', ' ') : '—')

type NotifyTab = 'recipients' | 'subs' | 'log' | 'settings'
const NOTIFY_TABS: NotifyTab[] = ['recipients', 'subs', 'log', 'settings']

// Deep-linkable tab (?tab=subs|log|recipients|settings): the admin-attention items for notify link
// straight to the tab that FIXES them, so "Review schedules" lands on Subscriptions rather than making
// the admin hunt for it. Read from window.location on mount (not useSearchParams) so the page keeps its
// current static-render behaviour and needs no Suspense boundary. Unknown value → today's default.
function initialTab(): NotifyTab {
  try {
    const t = new URLSearchParams(window.location.search).get('tab') as NotifyTab | null
    if (t && NOTIFY_TABS.includes(t)) return t
  } catch { /* SSR / no window → default */ }
  return 'subs'
}

export default function NotifyPage() {
  const [tab, setTab] = useState<NotifyTab>('subs')
  useEffect(() => { setTab(initialTab()) }, [])
  const [reports, setReports] = useState<Report[]>([])
  const [saved, setSaved] = useState<Saved[]>([])
  const [employees, setEmployees] = useState<Emp[]>([])
  const [subs, setSubs] = useState<Sub[]>([])
  const [log, setLog] = useState<LogRow[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [msg, setMsg] = useState('')

  const reload = useCallback(async () => {
    try {
      const [rep, rec, s, l, h] = await Promise.all([
        api('/api/v1/notify/reports'),
        api(`/api/v1/notify/recipients?org_id=${ORG_ID}`),
        api(`/api/v1/notify/subscriptions?org_id=${ORG_ID}`),
        api(`/api/v1/notify/send-log?org_id=${ORG_ID}`),
        api('/api/v1/notify/health'),
      ])
      setReports(rep.reports || [])
      setSaved(rec.saved || []); setEmployees(rec.employees || [])
      setSubs(s || []); setLog(l || []); setHealth(h)
    } catch (e: any) { setMsg('Load error: ' + (e?.message || e)) }
  }, [])
  useEffect(() => { reload() }, [reload])

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <h1 style={{ marginTop: 0 }}>📤 Notify</h1>
      {health && (!health.email_configured || !health.whatsapp_configured) && (
        <div style={{ ...card, background: '#fff8e1', borderColor: '#f0d68a', fontSize: 13 }}>
          {!health.email_configured && <div>⚠️ Email (Resend) not configured — set RESEND_API_KEY + NOTIFY_FROM_EMAIL on Railway.</div>}
          {!health.whatsapp_configured && <div>⚠️ WhatsApp not configured — set WHATSAPP_* env vars + approve the Meta template.</div>}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {(['subs', 'recipients', 'log', 'settings'] as const).map(t => (
          <button key={t} className={`btn ${tab === t ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setTab(t)}>
            {t === 'subs' ? 'Subscriptions' : t === 'recipients' ? 'Recipients' : t === 'log' ? 'Send History' : 'Settings'}
          </button>
        ))}
      </div>
      {msg && <div style={{ ...card, fontSize: 13 }}>{msg}</div>}

      {tab === 'recipients' && <Recipients saved={saved} employees={employees} onChange={reload} setMsg={setMsg} />}
      {tab === 'subs' && <Subscriptions reports={reports} saved={saved} subs={subs} onChange={reload} setMsg={setMsg} />}
      {tab === 'log' && <SendLog log={log} />}
      {tab === 'settings' && <NotifySettings setMsg={setMsg} health={health} />}
    </div>
  )
}

function NotifySettings({ setMsg, health }: { setMsg: (s: string) => void; health: Health | null }) {
  const [days, setDays] = useState<number>(7)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    (async () => {
      try {
        const r = await api(`/api/v1/notify/settings?org_id=${ORG_ID}`)
        setDays(Number(r?.download_link_expiry_days ?? 7))
      } catch (e: any) { setMsg('Load settings error: ' + (e?.message || e)) }
      finally { setLoaded(true) }
    })()
  }, [setMsg])
  async function save() {
    setBusy(true)
    try {
      const r = await api(`/api/v1/notify/settings?org_id=${ORG_ID}`, {
        method: 'PUT', body: JSON.stringify({ download_link_expiry_days: days }),
      })
      setDays(Number(r?.download_link_expiry_days ?? days))
      setMsg('✓ Settings saved.')
    } catch (e: any) { setMsg('Save error: ' + (e?.message || e)) }
    finally { setBusy(false) }
  }
  return (
    <>
    <div style={card}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>WhatsApp / download links</div>
      <div style={{ fontSize: 12, color: 'var(--muted,#888)', marginBottom: 12 }}>
        When WhatsApp can only deliver a link (a report sent outside the recipient's 24h window with no
        document-header template), the link is a no-login direct download of the file. Set how long that
        link stays valid.
      </div>
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
        Download link expiry (days)
        <input type="number" min={1} max={90} value={loaded ? days : ''} disabled={!loaded}
          onChange={e => setDays(Math.max(1, Math.min(90, Number(e.target.value) || 1)))}
          style={{ ...inp, width: 90 }} />
      </label>
      <div style={{ marginTop: 12 }}>
        <button className="btn btn-primary" disabled={busy || !loaded} onClick={save}>
          {busy ? '⏳ Saving…' : 'Save'}
        </button>
      </div>
    </div>
    <WhatsAppDelivery health={health} />
    </>
  )
}

// ── WhatsApp delivery diagnostics ─────────────────────────────────────────────────────────────────
// Owner incident 2026-08-05: reports were logged as 'sent' with real Meta message ids and NOTHING was
// delivered. Everything needed to spot that from inside the app now lives here: which account we send
// as, whether the delivery-status callback is actually wired, and whether the 24h-window evidence that
// lets us attach the real file is being collected.
function Row({ good, label, detail }: { good: boolean; label: string; detail?: string }) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 13, padding: '3px 0' }}>
      <span style={{ color: good ? 'green' : '#b45309' }}>{good ? '✓' : '⚠'}</span>
      <span style={{ minWidth: 250 }}>{label}</span>
      <span style={{ color: 'var(--muted,#888)', fontSize: 12 }}>{detail || ''}</span>
    </div>
  )
}

function WhatsAppDelivery({ health }: { health: Health | null }) {
  const [acct, setAcct] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  async function check() {
    setBusy(true); setErr(''); setAcct(null)
    try {
      setAcct(await api(`/api/v1/notify/whatsapp-account?org_id=${ORG_ID}`))
    } catch (e: any) {
      setErr(String(e?.message || e))
    } finally { setBusy(false) }
  }
  if (!health) return null
  const h = health
  return (
    <div style={card}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>WhatsApp delivery health</div>
      <div style={{ fontSize: 12, color: 'var(--muted,#888)', marginBottom: 10 }}>
        WhatsApp only lets a business send a FREE-FORM message (the one that attaches the actual file)
        within 24 hours of the recipient messaging you. Outside that, only an approved template arrives.
        Meta sometimes accepts an out-of-window message and drops it silently — these checks tell you
        whether we can see that happening.
      </div>
      <Row good={!!h.whatsapp_configured} label="WhatsApp credentials configured"
        detail={h.whatsapp_configured ? `template “${h.whatsapp_template || '—'}” (${h.whatsapp_template_lang || 'en'})` : 'set the WHATSAPP_* variables on the server'} />
      <Row good={!!h.whatsapp_webhook_ready} label="Delivery-status callback wired"
        detail={h.whatsapp_webhook_ready
          ? 'delivered / read / failed are recorded on each send'
          : `missing ${!h.whatsapp_verify_token_set ? 'verify token' : ''}${!h.whatsapp_verify_token_set && !h.whatsapp_app_secret_set ? ' + ' : ''}${!h.whatsapp_app_secret_set ? 'app secret' : ''} — sends will show “sent” with no proof of delivery`} />
      <Row good={!!h.whatsapp_window_tracking} label="24-hour window tracking"
        detail={h.whatsapp_window_tracking
          ? `on — the real file is attached to anyone who messaged us in the last ${h.whatsapp_window_hours ?? 23}h`
          : 'off (migration 723 not run) — every report goes out as an approved template link, which always arrives'} />
      <Row good={!!h.whatsapp_doc_header} label="Document-header template approved"
        detail={h.whatsapp_doc_header
          ? 'the real file attaches even to a cold recipient'
          : 'not configured — cold recipients get a no-login download link instead of the file'} />
      {h.whatsapp_freeform_when_unknown && (
        <Row good={false} label="Override: free-form allowed with no window evidence"
          detail="this re-enables the silent-drop failure mode — turn it off unless you are deliberately testing" />
      )}
      {h.whatsapp_webhook_url && (
        <div style={{ fontSize: 11, color: 'var(--muted,#888)', marginTop: 8 }}>
          Callback URL to set in Meta → WhatsApp → Configuration:{' '}
          <code style={{ fontSize: 11 }}>{h.whatsapp_webhook_url}</code>
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <button className="btn btn-secondary" disabled={busy} onClick={check}>
          {busy ? '⏳ Checking…' : 'Which account am I sending as?'}
        </button>
      </div>
      {err && <div style={{ fontSize: 12, color: '#c00', marginTop: 8 }}>{err}</div>}
      {acct && !acct.ok && (
        <div style={{ fontSize: 12, color: '#c00', marginTop: 8 }}>Could not read the account: {acct.error}</div>
      )}
      {acct && acct.ok && (
        <div style={{ fontSize: 13, marginTop: 8, lineHeight: 1.7 }}>
          <div><b>Number:</b> {acct.display_phone_number || '—'} &nbsp;<b>Name:</b> {acct.verified_name || '—'}</div>
          <div><b>Quality:</b> {acct.quality_rating || '—'} &nbsp;<b>Number status:</b> {acct.code_verification_status || '—'} / {acct.name_status || '—'}</div>
          <div><b>Phone number ID:</b> <code style={{ fontSize: 11 }}>{acct.phone_number_id}</code> — this must match the one shown in the Meta dashboard.</div>
          <div style={{ color: 'var(--muted,#888)', fontSize: 12 }}>{acct.app_mode_note}</div>
        </div>
      )}
    </div>
  )
}

function Recipients({ saved, employees, onChange, setMsg }: {
  saved: Saved[]; employees: Emp[]; onChange: () => void; setMsg: (s: string) => void
}) {
  const { defaultCc } = useAuth()
  const [form, setForm] = useState({ name: '', email: '', phone: '' })
  async function add(body: any) {
    try { await api(`/api/v1/notify/recipients?org_id=${ORG_ID}`, { method: 'POST', body: JSON.stringify(body) }); onChange() }
    catch (e: any) { setMsg('Add failed: ' + (e?.message || e)) }
  }
  async function del(id: string) {
    try { await api(`/api/v1/notify/recipients/${id}?org_id=${ORG_ID}`, { method: 'DELETE' }); onChange() }
    catch (e: any) { setMsg('Delete failed: ' + (e?.message || e)) }
  }
  return (
    <>
      <div style={card}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Add recipient</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input style={inp} placeholder="Name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
          <input style={inp} placeholder="Email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} />
          <PhoneInput value={form.phone} defaultCc={defaultCc} onChange={v => setForm({ ...form, phone: v })} placeholder="Phone (optional)" style={{ minWidth: 240 }} />
          <button className="btn btn-primary" onClick={() => { add(form); setForm({ name: '', email: '', phone: '' }) }}>Add</button>
        </div>
      </div>
      <div style={card}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Saved recipients</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Name</th><th style={th}>Email</th><th style={th}>Phone</th><th style={th}></th></tr></thead>
          <tbody>
            {saved.map(s => (
              <tr key={s.id}><td style={td}>{s.name}</td><td style={td}>{s.email}</td><td style={td}>{s.phone}</td>
                <td style={td}><button className="btn btn-secondary" style={{ padding: '2px 8px' }} onClick={() => del(s.id)}>Delete</button></td></tr>
            ))}
            {saved.length === 0 && <tr><td style={td} colSpan={4}>None yet.</td></tr>}
          </tbody>
        </table>
      </div>
      <div style={card}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Add from employees</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Name</th><th style={th}>Email</th><th style={th}>Phone</th><th style={th}></th></tr></thead>
          <tbody>
            {employees.map((e, i) => (
              <tr key={i}><td style={td}>{e.name}</td><td style={td}>{e.email}</td><td style={td}>{e.phone}</td>
                <td style={td}><button className="btn btn-secondary" style={{ padding: '2px 8px' }}
                  onClick={() => add({ name: e.name, email: e.email, phone: e.phone })}>+ Save</button></td></tr>
            ))}
            {employees.length === 0 && <tr><td style={td} colSpan={4}>No employees with email/phone.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}

function Subscriptions({ reports, saved, subs, onChange, setMsg }: {
  reports: Report[]; saved: Saved[]; subs: Sub[]; onChange: () => void; setMsg: (s: string) => void
}) {
  const [reportKey, setReportKey] = useState('')
  const [name, setName] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [channels, setChannels] = useState<string[]>(['email'])
  const [formats, setFormats] = useState<string[]>(['xlsx', 'pdf'])
  const [recipientIds, setRecipientIds] = useState<string[]>([])
  const [adHocEmails, setAdHocEmails] = useState('')
  const [adHocPhones, setAdHocPhones] = useState('')
  const [frequency, setFrequency] = useState('weekly')
  const [dow, setDow] = useState(0)
  const [dom, setDom] = useState(1)
  const [hour, setHour] = useState(8)
  const [tz, setTz] = useState('America/New_York')

  const rep = reports.find(r => r.key === reportKey)
  const toggle = (arr: string[], v: string, set: (x: string[]) => void) =>
    set(arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v])

  async function create() {
    if (!reportKey) { setMsg('Pick a report.'); return }
    const body: any = {
      name: name || rep?.label, report_key: reportKey,
      filters: Object.fromEntries(Object.entries(filters).filter(([, v]) => v !== '')),
      channels, formats, recipient_ids: recipientIds,
      ad_hoc_emails: adHocEmails.split(/[,\s;]+/).map(x => x.trim()).filter(Boolean),
      ad_hoc_phones: adHocPhones.split(/[,\s;]+/).map(x => x.trim()).filter(Boolean),
      frequency, hour, timezone: tz,
      day_of_week: frequency === 'weekly' ? dow : null,
      day_of_month: frequency === 'monthly' ? dom : null,
      is_active: true,
    }
    try { await api(`/api/v1/notify/subscriptions?org_id=${ORG_ID}`, { method: 'POST', body: JSON.stringify(body) }); onChange(); setMsg('✓ Subscription created.') }
    catch (e: any) { setMsg('Create failed: ' + (e?.message || e)) }
  }
  async function toggleActive(s: Sub) {
    try { await api(`/api/v1/notify/subscriptions/${s.id}?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify({ ...s, is_active: !s.is_active }) }); onChange() }
    catch (e: any) { setMsg('Update failed: ' + (e?.message || e)) }
  }
  async function del(id: string) {
    try { await api(`/api/v1/notify/subscriptions/${id}?org_id=${ORG_ID}`, { method: 'DELETE' }); onChange() }
    catch (e: any) { setMsg('Delete failed: ' + (e?.message || e)) }
  }

  return (
    <>
      <div style={card}>
        <div style={{ fontWeight: 600, marginBottom: 10 }}>New subscription</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
          <select style={inp} value={reportKey} onChange={e => { setReportKey(e.target.value); setFilters({}) }}>
            <option value="">— pick a report —</option>
            {reports.map(r => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>
          <input style={inp} placeholder="Name (optional)" value={name} onChange={e => setName(e.target.value)} />
        </div>

        {rep && rep.filters.length > 0 && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10, alignItems: 'center' }}>
            {rep.filters.map(f => (
              <input key={f} style={inp} placeholder={f + (FILTER_HINT[f] || '')}
                value={filters[f] || ''} onChange={e => setFilters({ ...filters, [f]: e.target.value })} />
            ))}
            {rep.filters.some(f => FILTER_HINT[f]) && (
              <span style={{ fontSize: 12, color: '#888' }}>
                Leave a date filter blank on a recurring schedule — it resolves to the current period every run.
              </span>
            )}
          </div>
        )}

        <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 10 }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 600 }}>Channels</div>
            <label style={{ fontSize: 13, marginRight: 10 }}><input type="checkbox" checked={channels.includes('email')} onChange={() => toggle(channels, 'email', setChannels)} /> Email</label>
            <label style={{ fontSize: 13 }}><input type="checkbox" checked={channels.includes('whatsapp')} onChange={() => toggle(channels, 'whatsapp', setChannels)} /> WhatsApp</label>
          </div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 600 }}>Formats</div>
            <label style={{ fontSize: 13, marginRight: 10 }}><input type="checkbox" checked={formats.includes('xlsx')} onChange={() => toggle(formats, 'xlsx', setFormats)} /> Excel</label>
            <label style={{ fontSize: 13 }}><input type="checkbox" checked={formats.includes('pdf')} onChange={() => toggle(formats, 'pdf', setFormats)} /> PDF</label>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
          <span style={{ fontSize: 12, fontWeight: 600 }}>Schedule</span>
          <select style={inp} value={frequency} onChange={e => setFrequency(e.target.value)}>
            <option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option>
          </select>
          {frequency === 'weekly' && <select style={inp} value={dow} onChange={e => setDow(+e.target.value)}>{DOW.map((d, i) => <option key={i} value={i}>{d}</option>)}</select>}
          {frequency === 'monthly' && <input style={{ ...inp, width: 70 }} type="number" min={1} max={31} value={dom} onChange={e => setDom(+e.target.value)} title="day of month" />}
          <label style={{ fontSize: 13 }}>at <input style={{ ...inp, width: 60 }} type="number" min={0} max={23} value={hour} onChange={e => setHour(+e.target.value)} />:00</label>
          <input style={{ ...inp, width: 160 }} value={tz} onChange={e => setTz(e.target.value)} title="timezone" />
        </div>

        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>Recipients (saved)</div>
          {saved.length === 0 && <div style={{ fontSize: 12, color: '#999' }}>Add recipients in the Recipients tab first.</div>}
          {saved.map(s => (
            <label key={s.id} style={{ fontSize: 13, marginRight: 12 }}>
              <input type="checkbox" checked={recipientIds.includes(s.id)} onChange={() => toggle(recipientIds, s.id, setRecipientIds)} /> {s.name || s.email || s.phone}
            </label>
          ))}
        </div>
        <input style={{ ...inp, width: '100%', marginBottom: 6 }} placeholder="Extra emails (comma-separated)" value={adHocEmails} onChange={e => setAdHocEmails(e.target.value)} />
        <input style={{ ...inp, width: '100%', marginBottom: 10 }} placeholder="Extra WhatsApp phones (comma-separated)" value={adHocPhones} onChange={e => setAdHocPhones(e.target.value)} />
        <button className="btn btn-primary" onClick={create}>Create subscription</button>
      </div>

      <div style={card}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Active subscriptions</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Name</th><th style={th}>Report</th><th style={th}>Schedule</th><th style={th}>Channels</th><th style={th}>Next run</th><th style={th}>Active</th><th style={th}></th></tr></thead>
          <tbody>
            {subs.map(s => (
              <tr key={s.id}>
                <td style={td}>{s.name}</td>
                <td style={td}>{s.report_key}</td>
                <td style={td}>{s.frequency}{s.frequency === 'weekly' ? ` ${DOW[s.day_of_week ?? 0]}` : s.frequency === 'monthly' ? ` d${s.day_of_month}` : ''} @{s.hour}:00</td>
                <td style={td}>{(s.channels || []).join(', ')}</td>
                <td style={td}>{d10(s.next_run_at)}</td>
                <td style={td}><button className="btn btn-secondary" style={{ padding: '2px 8px' }} onClick={() => toggleActive(s)}>{s.is_active ? '✓ on' : 'off'}</button></td>
                <td style={td}><button className="btn btn-secondary" style={{ padding: '2px 8px' }} onClick={() => del(s.id)}>Delete</button></td>
              </tr>
            ))}
            {subs.length === 0 && <tr><td style={td} colSpan={7}>No subscriptions yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}

// Which rung of the WhatsApp ladder actually delivered, in plain English (mig 723 fills this in;
// pre-migration and non-WhatsApp rows stay '—').
function routeLabel(r?: string | null): string {
  if (r === 'template_doc') return 'file attached (template)'
  if (r === 'freeform_doc') return 'file attached (in-window)'
  if (r === 'template_link') return 'download link (template)'
  return '—'
}

function deliveryColor(s?: string | null): string {
  const v = (s || '').toLowerCase()
  if (v === 'delivered' || v === 'read') return 'green'
  if (v === 'failed') return '#c00'
  if (v === 'sent') return '#b45309'   // accepted by Meta but not confirmed delivered yet (or silently dropped)
  return '#64748b'
}

function SendLog({ log }: { log: LogRow[] }) {
  return (
    <div style={card}>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>Recent sends</div>
      <div style={{ fontSize: 11, color: '#64748b', marginBottom: 8 }}>
        “Status” is only what Meta ACCEPTED. “Delivery” is what actually happened on the handset
        (delivered/read = confirmed; <b>failed</b> shows Meta's own reason; a WhatsApp row stuck on
        <b> sent</b> with no delivery was accepted and never confirmed — that is the silent-drop case).
        “Sent as” says whether the recipient got the real file or a download link.
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead><tr><th style={th}>When</th><th style={th}>Report</th><th style={th}>Channel</th><th style={th}>Target</th><th style={th}>Status</th><th style={th}>Delivery</th><th style={th}>Sent as</th><th style={th}>By</th><th style={th}>Error</th></tr></thead>
        <tbody>
          {log.map(l => (
            <tr key={l.id}>
              <td style={td}>{d10(l.created_at)}</td><td style={td}>{l.report_key}</td><td style={td}>{l.channel}</td>
              <td style={td}>{l.target}</td>
              <td style={{ ...td, color: l.status === 'sent' ? 'green' : '#c00' }}>{l.status}</td>
              <td style={{ ...td, color: deliveryColor(l.delivery_status) }}>{l.delivery_status || '—'}</td>
              <td style={{ ...td, fontSize: 12 }}>{routeLabel(l.delivery_route)}</td>
              <td style={td}>{l.triggered_by}</td>
              <td style={{ ...td, color: '#c00', fontSize: 11 }}>{l.delivery_error || l.error}</td>
            </tr>
          ))}
          {log.length === 0 && <tr><td style={td} colSpan={9}>No sends yet.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}
