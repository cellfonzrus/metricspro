'use client'
import { useState, useEffect } from 'react'
import { api } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

// HR · People — the single front door to ADD a person. Creates the StoreOps roster row (+ a stable
// employee_id), and — if a role/scope + email is given — assigns the RBAC role and (optionally)
// provisions a login, all in one POST /hr/employees. The person then appears everywhere they're
// needed (scheduling, payroll, org, commissions) because those key off employee_id.

type Role = { id: number; name: string; display_name: string }
type Store = { store_code: string; address?: string; market?: string }

const sel: React.CSSProperties = { padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const lbl: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, fontWeight: 600, color: 'var(--text2)' }

export default function HRPeoplePage() {
  const [roles, setRoles] = useState<Role[]>([])
  const [stores, setStores] = useState<Store[]>([])
  const [people, setPeople] = useState<any[]>([])
  const [msg, setMsg] = useState('')
  const [tempPw, setTempPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [bulkMsg, setBulkMsg] = useState('')
  const [mode, setMode] = useState<'new' | 'existing'>('new')                 // new hire vs invite existing
  const [ex, setEx] = useState({ employee_id: '', method: 'link', dob: '' })  // existing-employee invite
  const [f, setF] = useState<any>({ name: '', email: '', phone: '', home_store: '', role_title: '',
    pay_rate: '', role_name: '', market: '', store_codes: [] as string[], create_login: false,
    dob: '', send_invite: true, invite_method: 'link' })

  const markets = Array.from(new Set(stores.map(s => (s.market || '').trim()).filter(Boolean))).sort()

  async function loadAll() {
    try {
      const r = await api('/api/v1/core/roles'); setRoles(r.roles || [])
      // 2026-07-25 fix: a new hire can only be assigned to an ACTIVE store (a closed store should
      // never gain new employees/hours).
      const st = await api('/api/v1/storeops/stores'); setStores((st || []).filter((s: any) => s.store_code && s.is_active !== false))
      const e = await api('/api/v1/hr/employees'); setPeople((e.employees || []).filter((p: any) => p.is_active !== false))
    } catch (err: any) { setMsg('Load failed: ' + (err?.message || err)) }
  }
  useEffect(() => { loadAll() }, [])

  const set = (patch: any) => setF((v: any) => ({ ...v, ...patch }))
  const toggleStore = (code: string) => set({ store_codes: f.store_codes.includes(code) ? f.store_codes.filter((c: string) => c !== code) : [...f.store_codes, code] })

  async function create() {
    if (!f.name.trim()) { setMsg('Name is required.'); return }
    if (f.create_login && !f.email.trim()) { setMsg('A login needs an email.'); return }
    setBusy(true); setMsg(''); setTempPw('')
    try {
      const r = await api('/api/v1/hr/employees', { method: 'POST', body: JSON.stringify({
        name: f.name.trim(), email: f.email.trim() || null, phone: f.phone.trim() || null,
        home_store: f.home_store || null, role: f.role_title.trim() || null,   // job title -> employees.role
        pay_rate: f.pay_rate === '' ? null : Number(f.pay_rate),
        role_name: f.role_name || null, market: f.market || null,
        store_codes: f.store_codes.length ? f.store_codes : null,
        store_code: f.store_codes[0] || f.home_store || null,
        create_login: !!f.create_login,
        send_invite: !!f.send_invite,
        invite_method: f.invite_method,
        dob: f.dob || null,
      }) })
      const inv = r.invite
      const invMsg = inv ? (inv.ok ? (inv.emailed ? ' · onboarding invite emailed ✉️' : ` · invite ready (${inv.email_note || 'send manually'})`) : ` · invite issue: ${inv.error || 'failed'}`) : ''
      setMsg(`✅ Saved ${f.name}${r.assigned_role ? ` → ${r.assigned_role}` : ''}${invMsg}${r.note ? ` — ${r.note}` : ''}`)
      if (r.login?.temp_password) setTempPw(`${f.email.trim()} → ${r.login.temp_password}`)
      else if (inv?.temp_password) setTempPw(`${inv.email} → ${inv.temp_password} (portal login)`)
      setF({ name: '', email: '', phone: '', home_store: '', role_title: '', pay_rate: '', role_name: '', market: '', store_codes: [], create_login: false, dob: '', send_invite: true, invite_method: 'link' })
      loadAll()
    } catch (err: any) { setMsg('❌ ' + (err?.message || err)) } finally { setBusy(false) }
  }

  async function sendExistingInvite() {
    if (!ex.employee_id) { setMsg('Pick an employee.'); return }
    setBusy(true); setMsg(''); setTempPw('')
    try {
      const body: any = { method: ex.method, send_email: true }
      if (ex.method === 'link') body.dob = ex.dob || null
      const r = await api(`/api/v1/hr/onboarding/employee/${ex.employee_id}/invite`, { method: 'POST', body: JSON.stringify(body) })
      setMsg(r.emailed ? `✅ Invite emailed to ${r.email || 'employee'}` : `✅ Invite ready${r.email_note ? ' — ' + r.email_note : ''}`)
      if (r.temp_password) setTempPw(`${r.email} → ${r.temp_password} (portal login)`)
      setEx({ employee_id: '', method: 'link', dob: '' }); loadAll()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  async function bulkInvite(employee_ids?: string[]) {
    const n = employee_ids ? employee_ids.length : 'all incomplete'
    if (!confirm(`Send a portal-login onboarding invite to ${n} employee(s)? Each gets an email with a temp password.`)) return
    setBulkMsg('Sending…')
    try {
      const body: any = { method: 'login', send_email: true }
      if (employee_ids) body.employee_ids = employee_ids; else body.all_incomplete = true
      const r = await api('/api/v1/hr/onboarding/invite-bulk', { method: 'POST', body: JSON.stringify(body) })
      setBulkMsg(`✅ Invited ${r.invited}/${r.total} · emailed ${r.emailed}${r.emailed < r.invited ? ' (some emails not configured — see per-employee tracker for links)' : ''}`)
      loadAll()
    } catch (e: any) { setBulkMsg('❌ ' + (e?.message || e)) }
  }

  // RULE FOUR (§3c): export the roster list as shown — email/store/role/login/onboarding-link status
  // only, none of it Fernet-encrypted intake PII (that lives on the onboarding detail page, HR-only).
  const cols: ExportColumn[] = [
    { header: 'Name', field: 'name', role: 'rep', get: (p: any) => p.name || '' },
    { header: 'Email', field: 'email', get: (p: any) => p.email || '' },
    { header: 'Store', field: 'store', role: 'store', get: (p: any) => p.app_store || p.home_store || '' },
    { header: 'App Role', field: 'app_role', get: (p: any) => p.app_role || '' },
    { header: 'Login', field: 'has_login', get: (p: any) => p.has_login ? 'Yes' : 'No' },
  ]

  return (
    <div style={{ maxWidth: 920 }}>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧑‍💼 HR · People</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Add an employee once here — they appear everywhere (scheduling, payroll, org, commissions).
          Give an email + role to also grant app access; tick “create login” to provision it now.
        </p>
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          {(['new', 'existing'] as const).map(m => (
            <button key={m} onClick={() => { setMode(m); setMsg('') }} style={{ padding: '6px 12px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, fontWeight: 600, cursor: 'pointer', background: mode === m ? 'var(--accent)' : 'var(--surface)', color: mode === m ? '#fff' : 'var(--text2)' }}>
              {m === 'new' ? '➕ New employee' : '✉️ Existing employee'}
            </button>
          ))}
          <span style={{ fontSize: 12, color: 'var(--text3)' }}>{mode === 'new'
            ? 'Create a person + optionally send their onboarding invite.'
            : 'Invite someone already on the roster to fill in / update their info.'}</span>
        </div>
        {mode === 'new' ? (<>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 12 }}>
          <label style={lbl}>Full name *<input style={sel} value={f.name} onChange={e => set({ name: e.target.value })} /></label>
          <label style={lbl}>Email<input style={sel} type="email" value={f.email} placeholder="for role / login" onChange={e => set({ email: e.target.value })} /></label>
          <label style={lbl}>Phone<input style={sel} value={f.phone} onChange={e => set({ phone: e.target.value })} /></label>
          <label style={lbl}>Date of birth<input style={sel} type="date" value={f.dob} onChange={e => set({ dob: e.target.value })} /><span style={{ fontWeight: 400, fontSize: 11, color: 'var(--text3)' }}>gates the emailed link</span></label>
          <label style={lbl}>Home store
            <select style={sel} value={f.home_store} onChange={e => set({ home_store: e.target.value })}>
              <option value="">—</option>
              {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.store_code}{s.address ? ` — ${s.address.substring(0, 22)}` : ''}</option>)}
            </select>
          </label>
          <label style={lbl}>Job title<input style={sel} value={f.role_title} placeholder="Sales Rep" onChange={e => set({ role_title: e.target.value })} /></label>
          <label style={lbl}>Pay $/hr<input style={sel} type="number" value={f.pay_rate} onChange={e => set({ pay_rate: e.target.value })} /></label>
          <label style={lbl}>App role
            <select style={sel} value={f.role_name} onChange={e => set({ role_name: e.target.value })}>
              <option value="">— none —</option>
              {roles.map(r => <option key={r.id} value={r.name}>{r.display_name}</option>)}
            </select>
          </label>
          <label style={lbl}>Market
            <select style={sel} value={f.market} onChange={e => set({ market: e.target.value })}>
              <option value="">—</option>
              {markets.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
        </div>
        <div style={{ marginTop: 12 }}>
          <div style={{ ...lbl, marginBottom: 6 }}>Stores covered (floaters can cover several)</div>
          <div style={{ display: 'flex', gap: '6px 14px', flexWrap: 'wrap', maxHeight: 120, overflowY: 'auto', padding: 8, border: '1px solid var(--border)', borderRadius: 8 }}>
            {stores.length === 0 && <span style={{ fontSize: 12, color: 'var(--text3)' }}>No stores found</span>}
            {stores.map(s => (
              <label key={s.store_code} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                <input type="checkbox" checked={f.store_codes.includes(s.store_code)} onChange={() => toggleStore(s.store_code)} /> {s.store_code}
              </label>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginTop: 14, flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <input type="checkbox" checked={f.send_invite} onChange={e => set({ send_invite: e.target.checked })} /> Send onboarding invite
          </label>
          {f.send_invite && (
            <select style={sel} value={f.invite_method} onChange={e => set({ invite_method: e.target.value })}>
              <option value="link">Email a no-login link (DOB gate)</option>
              <option value="login">Create a portal login + email password</option>
            </select>
          )}
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <input type="checkbox" checked={f.create_login} onChange={e => set({ create_login: e.target.checked })} /> Full login now (skips invite)
          </label>
          <button className="btn btn-primary" disabled={busy} onClick={create}>{busy ? 'Saving…' : '➕ Add person'}</button>
          {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
        </div>
        </>) : (
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <label style={lbl}>Existing employee
              <select style={{ ...sel, minWidth: 230 }} value={ex.employee_id} onChange={e => setEx(v => ({ ...v, employee_id: e.target.value }))}>
                <option value="">— pick a person —</option>
                {people.filter((p: any) => p.employee_id).map((p: any) => <option key={p.employee_id} value={p.employee_id}>{p.name}{p.email ? ` · ${p.email}` : ''}</option>)}
              </select>
            </label>
            <label style={lbl}>Invite method
              <select style={sel} value={ex.method} onChange={e => setEx(v => ({ ...v, method: e.target.value }))}>
                <option value="link">Email a no-login link (DOB gate)</option>
                <option value="login">Create/refresh portal login + email password</option>
              </select>
            </label>
            {ex.method === 'link' && <label style={lbl}>Date of birth<input style={sel} type="date" value={ex.dob} onChange={e => setEx(v => ({ ...v, dob: e.target.value }))} /></label>}
            <button className="btn btn-primary" disabled={busy} onClick={sendExistingInvite}>{busy ? 'Sending…' : '✉️ Send invite'}</button>
            {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
          </div>
        )}
        {tempPw && (
          <div style={{ marginTop: 10, padding: 10, background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, fontFamily: 'monospace', fontSize: 12 }}>
            🔑 Temp password (hand out; user resets on first sign-in): <strong>{tempPw}</strong>
          </div>
        )}
      </div>

      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 12, borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 700, fontSize: 13 }}>People ({people.length})</span>
          <div style={{ flex: 1 }} />
          {bulkMsg && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{bulkMsg}</span>}
          <ReportExportBar title="HR People" columns={cols} rows={people} />
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => bulkInvite()} title="Email a portal login + password to every employee who hasn't finished onboarding">📨 Invite all for onboarding</button>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Name', 'Email', 'Store', 'App role', 'Login', 'Onboarding'].map(h => <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {people.map((p: any, i: number) => (
                <tr key={p.id ?? i} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '7px 12px', fontSize: 13, fontWeight: 500 }}>{p.name}</td>
                  <td style={{ padding: '7px 12px', fontSize: 12, color: 'var(--text2)' }}>{p.email || '—'}</td>
                  <td style={{ padding: '7px 12px', fontSize: 12 }}>{p.app_store || p.home_store || '—'}</td>
                  <td style={{ padding: '7px 12px', fontSize: 12 }}>{p.app_role || '—'}</td>
                  <td style={{ padding: '7px 12px', fontSize: 12 }}>{p.has_login ? '✓' : '—'}</td>
                  <td style={{ padding: '7px 12px', fontSize: 12 }}>{p.employee_id
                    ? <a href={`/hr/onboarding/${p.employee_id}`} style={{ color: 'var(--accent,#2563eb)' }}>Open →</a>
                    : '—'}</td>
                </tr>
              ))}
              {people.length === 0 && <tr><td colSpan={6} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No people yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
