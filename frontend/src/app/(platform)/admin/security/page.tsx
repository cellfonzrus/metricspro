'use client'
import { useEffect, useState } from 'react'
import { api, ORG_ID } from '@/lib/client'
import EntityPicker from '@/components/EntityPicker'

// Client echo of the server password policy (server is authoritative). [] = OK.
function pwErrors(pw: string, p: any): string[] {
  const e: string[] = []
  if (pw.length > 128) return ['Password must be at most 128 characters.']
  if (!p) return e
  if (pw.length < p.min_length) e.push(`Use at least ${p.min_length} characters.`)
  if (pw.length > p.max_length) e.push(`Use at most ${p.max_length} characters.`)
  if (p.require_upper && !/[A-Z]/.test(pw)) e.push('Add an uppercase letter.')
  if (p.require_lower && !/[a-z]/.test(pw)) e.push('Add a lowercase letter.')
  if (p.require_digit && !/[0-9]/.test(pw)) e.push('Add a number.')
  if (p.require_special && !/[!@#$%^&*()\-_=+[\]{};:,.?/]/.test(pw)) e.push('Add a special character.')
  return e
}

export default function SecuritySettingsPage() {
  const [loaded, setLoaded] = useState(false)
  const [canEdit, setCanEdit] = useState(false)
  const [hardMax, setHardMax] = useState(128)
  const [channels, setChannels] = useState<any>({})
  const [pol, setPol] = useState<any>(null)         // password policy
  const [tw, setTw] = useState<any>(null)           // twofa policy
  const [roles, setRoles] = useState<{ name: string; display_name: string }[]>([])
  const [users, setUsers] = useState<{ id: string; label: string; sublabel?: string; email: string }[]>([])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  // admin-set-password card
  const [setEmail, setSetEmail] = useState<string | null>(null)
  const [setPw, setSetPw] = useState('')
  const [requireChange, setRequireChange] = useState(true)
  const [setMsg2, setSetMsg2] = useState('')

  async function load() {
    try {
      const s = await api('/api/v1/core/security-settings')
      setPol(s.password_policy); setTw(s.twofa_policy); setCanEdit(!!s.can_edit)
      setHardMax(s.hard_max || 128); setChannels(s.channels_status || {})
    } catch (e: any) { setMsg('Could not load security settings: ' + (e?.message || e)) }
    try {
      const r = await api('/api/v1/core/roles')
      setRoles((r.roles || []).map((x: any) => ({ name: x.name, display_name: x.display_name })))
    } catch { /* roles optional */ }
    try {
      const u = await api(`/api/v1/core/users?org_id=${ORG_ID}`)
      setUsers((u.users || []).filter((x: any) => x.email).map((x: any) => ({
        id: x.email, label: x.full_name || x.email, sublabel: x.email, email: x.email })))
    } catch { /* roster optional */ }
    setLoaded(true)
  }
  useEffect(() => { load() }, [])

  async function savePolicy() {
    setBusy(true); setMsg('')
    try {
      await api('/api/v1/core/security-settings', { method: 'PUT', body: JSON.stringify({ password_policy: pol }) })
      setMsg('Password policy saved.')
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
    finally { setBusy(false) }
  }
  async function saveTwofa() {
    setBusy(true); setMsg('')
    try {
      await api('/api/v1/core/security-settings', { method: 'PUT', body: JSON.stringify({ twofa_policy: tw }) })
      setMsg('2FA policy saved.')
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
    finally { setBusy(false) }
  }
  async function assignPassword() {
    if (!setEmail) { setSetMsg2('Pick an employee.'); return }
    const pe = pwErrors(setPw, pol)
    if (pe.length) { setSetMsg2(pe.join(' ')); return }
    setSetMsg2('')
    try {
      await api('/api/v1/core/users/set-password', { method: 'POST',
        body: JSON.stringify({ email: setEmail, password: setPw, require_change: requireChange }) })
      setSetMsg2(`Password set for ${setEmail}. Hand it over securely.`)
      setSetPw('')
    } catch (e: any) { setSetMsg2('Failed: ' + (e?.message || e)) }
  }

  if (!loaded) return <div style={{ padding: 20 }}>Loading…</div>
  const setPwErrs = setPw ? pwErrors(setPw, pol) : []
  const toggle = (k: string) => setPol((p: any) => ({ ...p, [k]: !p[k] }))
  const twChannel = (c: string) => setTw((t: any) => {
    const has = (t.channels || []).includes(c)
    return { ...t, channels: has ? t.channels.filter((x: string) => x !== c) : [...(t.channels || []), c] }
  })
  const twRole = (r: string) => setTw((t: any) => {
    const has = (t.required_roles || []).includes(r)
    return { ...t, required_roles: has ? t.required_roles.filter((x: string) => x !== r) : [...(t.required_roles || []), r] }
  })

  return (
    <div style={{ maxWidth: 780 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>🛡️ Security Settings</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 16px' }}>
        Per-company password rules, two-factor authentication, and admin-assigned passwords.
        {!canEdit && <strong style={{ color: '#b45309' }}> You have read-only access — ask an admin to make changes.</strong>}
      </p>
      {msg && <div style={{ margin: '0 0 14px', padding: 10, borderRadius: 8, background: '#f0f9ff', fontSize: 13 }}>{msg}</div>}

      {/* Password policy */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 10 }}>Password policy</div>
        {pol && (
          <>
            <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 10 }}>
              <label style={lbl}>Min length
                <input type="number" min={4} max={hardMax} value={pol.min_length} disabled={!canEdit}
                  onChange={e => setPol({ ...pol, min_length: Number(e.target.value) })} style={num} /></label>
              <label style={lbl}>Max length (≤{hardMax})
                <input type="number" min={pol.min_length} max={hardMax} value={pol.max_length} disabled={!canEdit}
                  onChange={e => setPol({ ...pol, max_length: Math.min(Number(e.target.value), hardMax) })} style={num} /></label>
            </div>
            <div style={{ display: 'flex', gap: '6px 18px', flexWrap: 'wrap', marginBottom: 12 }}>
              {[['require_upper', 'Uppercase'], ['require_lower', 'Lowercase'], ['require_digit', 'Number'], ['require_special', 'Special char']].map(([k, label]) => (
                <label key={k} style={chk}>
                  <input type="checkbox" checked={!!pol[k]} disabled={!canEdit} onChange={() => toggle(k)} /> {label}
                </label>
              ))}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
              Hard cap: passwords over {hardMax} characters are always rejected, regardless of these settings.
            </div>
            {canEdit && <button className="btn btn-primary" disabled={busy} onClick={savePolicy}>💾 Save password policy</button>}
          </>
        )}
      </div>

      {/* 2FA policy */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>Two-factor authentication</div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
          WhatsApp delivery {channels?.whatsapp?.confirmed ? 'is configured' : 'is NOT yet live-verified'} —
          email is confirmed working. Default is OFF (no lockout).
        </div>
        {tw && (
          <>
            <div style={{ display: 'flex', gap: 14, marginBottom: 12 }}>
              {['off', 'optional', 'required'].map(m => (
                <label key={m} style={{ ...chk, fontWeight: tw.mode === m ? 700 : 400 }}>
                  <input type="radio" name="twmode" checked={tw.mode === m} disabled={!canEdit}
                    onChange={() => setTw({ ...tw, mode: m })} /> {m}
                </label>
              ))}
            </div>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 4 }}>Allowed channels</div>
            <div style={{ display: 'flex', gap: 18, marginBottom: 12 }}>
              {['email', 'whatsapp'].map(c => (
                <label key={c} style={chk}>
                  <input type="checkbox" checked={(tw.channels || []).includes(c)} disabled={!canEdit} onChange={() => twChannel(c)} /> {c}
                </label>
              ))}
            </div>
            {tw.mode === 'required' && roles.length > 0 && (
              <>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 4 }}>
                  Required for roles <span style={{ fontWeight: 400, color: 'var(--text3)' }}>(none checked = everyone)</span>
                </div>
                <div style={{ display: 'flex', gap: '6px 16px', flexWrap: 'wrap', marginBottom: 12 }}>
                  {roles.map(r => (
                    <label key={r.name} style={chk}>
                      <input type="checkbox" checked={(tw.required_roles || []).includes(r.name)} disabled={!canEdit}
                        onChange={() => twRole(r.name)} /> {r.display_name}
                    </label>
                  ))}
                </div>
              </>
            )}
            {canEdit && <button className="btn btn-primary" disabled={busy} onClick={saveTwofa}>💾 Save 2FA policy</button>}
          </>
        )}
      </div>

      {/* Admin-assigned password */}
      {canEdit && (
        <div className="card" style={{ padding: 16 }}>
          <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>Set an employee's password</div>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>
            Set a specific password for anyone in this company. It must pass the policy above; they'll be asked to change it on next login (unless you turn that off).
          </div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
            <div style={{ minWidth: 240 }}>
              <EntityPicker options={users} value={setEmail} onChange={setSetEmail} placeholder="Pick an employee…" />
            </div>
            <div>
              <input type="text" value={setPw} onChange={e => setSetPw(e.target.value)} placeholder="New password" style={{ ...num, width: 200 }} />
              {setPwErrs.length > 0 && <ul style={{ margin: '6px 0 0', paddingLeft: 16, fontSize: 11, color: '#b45309' }}>{setPwErrs.map((x, i) => <li key={i}>{x}</li>)}</ul>}
            </div>
            <label style={{ ...chk, marginTop: 8 }}>
              <input type="checkbox" checked={requireChange} onChange={e => setRequireChange(e.target.checked)} /> Require change on next login
            </label>
            <button className="btn btn-primary" style={{ marginTop: 4 }} onClick={assignPassword}>Set password</button>
          </div>
          {setMsg2 && <div style={{ marginTop: 10, fontSize: 13, color: setMsg2.startsWith('Failed') ? '#dc2626' : '#059669' }}>{setMsg2}</div>}
        </div>
      )}
    </div>
  )
}

const lbl: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text2)', fontWeight: 600 }
const num: React.CSSProperties = { padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 14, width: 90 }
const chk: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }
