'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'
import { apiCached, CONFIG } from '@/lib/cache'
import { useAuth } from '@/lib/auth-context'

// SaaS logins — super-admin onboarding: create a company (tenant) + provision its first admin login.
type Tenant = { org_id: string; name: string; slug: string | null; is_active: boolean; created_at: string; users: number; logins: number }

export default function TenantsAdmin() {
  const { user, loading } = useAuth()
  const isSuper = !!user?.super_admin
  const [tenants, setTenants] = useState<Tenant[]>([])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [np, setNp] = useState({ name: '', admin_email: '', admin_name: '', temp_password: '' })
  const [created, setCreated] = useState<any>(null)
  const [rp, setRp] = useState({ email: '', temp_password: '' })
  const [reset, setReset] = useState<any>(null)
  const [rpBusy, setRpBusy] = useState(false)
  const [taBusy, setTaBusy] = useState('')          // org_id currently resetting
  const [taReset, setTaReset] = useState<any>(null)  // last admin-reset result
  const [taPick, setTaPick] = useState<any>(null)    // {org_id, name, admins[]} when a tenant has >1 admin login
  const [supers, setSupers] = useState<any[]>([])    // platform super-admins (bypass tenant isolation)
  const [sa, setSa] = useState({ email: '', full_name: '', temp_password: '' })
  const [saBusy, setSaBusy] = useState(false)
  const [saCreated, setSaCreated] = useState<any>(null)

  // NAV-PERF 2026-08-04: MEASURED /core/tenants 368 ms + /core/super-admins 248 ms. Both are
  // slow-changing config. `cache` is true ONLY on mount — every reload after a WRITE on this page
  // (add tenant, rename, activate/deactivate, add/remove super-admin) passes false, so an operator
  // always sees their own change land. Locking yourself out of tenant admin by reading a stale list
  // is not an acceptable trade for 600 ms.
  const load = useCallback((cache = false) => {
    (cache ? apiCached('/api/v1/core/tenants', CONFIG) : api('/api/v1/core/tenants'))
      .then((d: any) => setTenants(d.tenants || [])).catch(e => setErr(e?.message || 'Failed to load'))
  }, [])
  const loadSupers = useCallback((cache = false) => {
    (cache ? apiCached('/api/v1/core/super-admins', CONFIG) : api('/api/v1/core/super-admins'))
      .then((d: any) => setSupers(d.super_admins || [])).catch(() => {})
  }, [])
  useEffect(() => { if (isSuper) { load(true); loadSupers(true) } }, [isSuper, load, loadSupers])

  async function addTenant() {
    if (!np.name.trim() || !np.admin_email.trim()) { setErr('Company name and admin email are required.'); return }
    setBusy(true); setErr(''); setCreated(null)
    try {
      const r = await api('/api/v1/core/tenants', { method: 'POST', body: JSON.stringify(np) })
      setCreated(r); setNp({ name: '', admin_email: '', admin_name: '', temp_password: '' }); load()
    } catch (e: any) { setErr(e?.message || 'Could not create company') } finally { setBusy(false) }
  }
  async function resetPassword() {
    if (!rp.email.trim()) { setErr('Enter the user\'s email to reset.'); return }
    setRpBusy(true); setErr(''); setReset(null)
    try {
      const r = await api('/api/v1/core/users/reset-password', { method: 'POST', body: JSON.stringify(rp) })
      setReset(r); setRp({ email: '', temp_password: '' })
    } catch (e: any) { setErr(e?.message || 'Could not reset password') } finally { setRpBusy(false) }
  }
  async function addSuper() {
    if (!sa.email.trim()) { setErr('Enter an email to make a platform super-admin.'); return }
    setSaBusy(true); setErr(''); setSaCreated(null)
    try {
      const r = await api('/api/v1/core/super-admins', { method: 'POST', body: JSON.stringify(sa) })
      setSaCreated(r); setSa({ email: '', full_name: '', temp_password: '' }); loadSupers()
    } catch (e: any) { setErr(e?.message || 'Could not create super-admin') } finally { setSaBusy(false) }
  }
  async function revokeSuper(email: string) {
    if (!confirm(`Remove platform (cross-tenant) access from ${email}? They stay a normal tenant user — their login is not deleted.`)) return
    setErr('')
    try {
      await api(`/api/v1/core/super-admins?email=${encodeURIComponent(email)}`, { method: 'DELETE' })
      loadSupers()
    } catch (e: any) { setErr(e?.message || 'Could not revoke') }
  }
  async function resetTenantAdmin(t: Tenant, email?: string) {
    setTaBusy(t.org_id); setErr(''); setTaReset(null)
    try {
      const r = await api(`/api/v1/core/tenants/${t.org_id}/reset-admin-password`, {
        method: 'POST', body: JSON.stringify(email ? { email } : {}) })
      if (r?.needs_email) { setTaPick(r); return }   // >1 admin login — ask which
      setTaPick(null); setTaReset(r)
    } catch (e: any) { setErr(e?.message || 'Could not reset the tenant admin password') }
    finally { setTaBusy('') }
  }
  async function rename(t: Tenant) {
    const name = prompt('Rename company:', t.name)
    if (!name?.trim() || name === t.name) return
    await api(`/api/v1/core/tenants/${t.org_id}`, { method: 'PATCH', body: JSON.stringify({ name: name.trim() }) }); load()
  }
  async function toggleActive(t: Tenant) {
    await api(`/api/v1/core/tenants/${t.org_id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !t.is_active }) }); load()
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--text3)' }}>Loading…</div>
  if (!isSuper) return (
    <div style={{ padding: 24, maxWidth: 560 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700 }}>🏢 Companies (Tenants)</h1>
      <div className="card" style={{ padding: 16, color: 'var(--text2)' }}>This page is for <b>super-admins</b> only — they onboard new companies onto MetricsPro. Ask a super-admin if you need access.</div>
    </div>
  )

  const inp = { padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 14 }
  const mtOn = typeof window !== 'undefined' && window.localStorage.getItem('mp_multi_tenant') === '1'
  return (
    <div style={{ padding: 24, maxWidth: 900 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>🏢 Companies (Tenants)</h1>
        <a href="/admin/billing" className="btn btn-sm">💳 Billing &amp; Plans →</a>
      </div>
      <div className="card" style={{ padding: 12, marginBottom: 14, background: mtOn ? '#fffbeb' : 'var(--surface)', borderColor: mtOn ? '#fde68a' : 'var(--border)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <b style={{ fontSize: 14 }}>Multi-tenant mode (this browser):</b>
          <button className={`btn btn-sm ${mtOn ? 'btn-primary' : ''}`}
            onClick={() => { window.localStorage.setItem('mp_multi_tenant', mtOn ? '0' : '1'); location.reload() }}>
            {mtOn ? 'ON — scoping API calls to your org' : 'OFF (house org)'}</button>
          <span style={{ fontSize: 12, color: 'var(--text3)' }}>
            Turning ON makes every API call use YOUR org_id (the isolation test). For full server-side
            enforcement set Railway env <code>MULTI_TENANT_ENFORCE=1</code>; for public signups set <code>SIGNUPS_OPEN=1</code>.
          </span>
        </div>
      </div>
      <p style={{ color: 'var(--text3)', fontSize: 13, marginTop: 0 }}>
        Onboard a company onto MetricsPro: this creates its own org, seeds its roles + modules, and provisions its first admin login.
        That admin then manages their own staff in Roles &amp; Access.
      </p>
      {err && <div className="card" style={{ borderColor: '#c0392b', color: '#c0392b', padding: 12, marginBottom: 12 }}>{err}</div>}

      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, marginBottom: 10 }}>➕ Add a company</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input style={{ ...inp, width: 200 }} placeholder="Company name *" value={np.name} onChange={e => setNp(v => ({ ...v, name: e.target.value }))} />
          <input style={{ ...inp, width: 220 }} placeholder="Admin email *" value={np.admin_email} onChange={e => setNp(v => ({ ...v, admin_email: e.target.value }))} />
          <input style={{ ...inp, width: 160 }} placeholder="Admin name" value={np.admin_name} onChange={e => setNp(v => ({ ...v, admin_name: e.target.value }))} />
          <input style={{ ...inp, width: 150 }} placeholder="Temp password (auto)" value={np.temp_password} onChange={e => setNp(v => ({ ...v, temp_password: e.target.value }))} />
          <button className="btn btn-primary" disabled={busy} onClick={addTenant}>{busy ? 'Creating…' : 'Create'}</button>
        </div>
        {created && (
          <div style={{ marginTop: 12, padding: 12, borderRadius: 8, background: '#f0fdf4', border: '1px solid #bbf7d0', fontSize: 14 }}>
            ✅ Created <b>{created.name}</b>. Admin login: <b>{created.admin_email}</b> · temp password: <code style={{ background: '#fff', padding: '2px 6px', borderRadius: 4 }}>{created.temp_password}</code>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>Hand this to the company admin — they reset it on first login. {created.auth_error ? `(login note: ${created.auth_error})` : ''}</div>
          </div>
        )}
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, marginBottom: 4 }}>🔑 Reset a user's password</div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
          Works for <b>any tenant's</b> user (Luxelink, etc.) by email. Sets a temp password and forces a change on next login.
          The account must already have a login (created in Roles &amp; Access).
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input style={{ ...inp, width: 240 }} placeholder="User email *" value={rp.email} onChange={e => setRp(v => ({ ...v, email: e.target.value }))} />
          <input style={{ ...inp, width: 170 }} placeholder="New temp password (auto)" value={rp.temp_password} onChange={e => setRp(v => ({ ...v, temp_password: e.target.value }))} />
          <button className="btn btn-primary" disabled={rpBusy} onClick={resetPassword}>{rpBusy ? 'Resetting…' : 'Reset password'}</button>
        </div>
        {reset && (
          <div style={{ marginTop: 12, padding: 12, borderRadius: 8, background: '#f0fdf4', border: '1px solid #bbf7d0', fontSize: 14 }}>
            ✅ Password reset for <b>{reset.email}</b> · temp password: <code style={{ background: '#fff', padding: '2px 6px', borderRadius: 4 }}>{reset.temp_password}</code>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>Hand this to the user — they'll be prompted to set a new password on next login.</div>
          </div>
        )}
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 16, borderColor: '#fca5a5' }}>
        <div style={{ fontWeight: 700, marginBottom: 4 }}>🛡️ Platform super-admins</div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
          These logins <b>bypass tenant isolation</b> and can see every tenant's data — keep the list to your internal operators only.
          A new email gets a login created (temp password shown once); an existing email is just elevated (its password is left unchanged).
          A row flagged <b style={{ color: '#c0392b' }}>in red</b> holds platform access with a non-admin role — almost always a mistake to fix.
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
          <input style={{ ...inp, width: 240 }} placeholder="Email *" value={sa.email} onChange={e => setSa(v => ({ ...v, email: e.target.value }))} />
          <input style={{ ...inp, width: 160 }} placeholder="Name" value={sa.full_name} onChange={e => setSa(v => ({ ...v, full_name: e.target.value }))} />
          <input style={{ ...inp, width: 150 }} placeholder="Temp password (auto)" value={sa.temp_password} onChange={e => setSa(v => ({ ...v, temp_password: e.target.value }))} />
          <button className="btn btn-primary" disabled={saBusy} onClick={addSuper}>{saBusy ? 'Saving…' : 'Add / elevate'}</button>
        </div>
        {saCreated && (
          <div style={{ marginBottom: 10, padding: 12, borderRadius: 8, background: '#f0fdf4', border: '1px solid #bbf7d0', fontSize: 14 }}>
            ✅ {saCreated.elevated ? 'Elevated' : 'Created'} <b>{saCreated.email}</b>
            {saCreated.temp_password && <> · temp password: <code style={{ background: '#fff', padding: '2px 6px', borderRadius: 4 }}>{saCreated.temp_password}</code></>}
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>{saCreated.temp_password ? "Hand this to them — they'll set a new password on first login." : 'Existing login elevated; their password is unchanged.'}</div>
          </div>
        )}
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
          {supers.length === 0 ? <div style={{ padding: 12, color: 'var(--text3)', fontSize: 13 }}>No super-admins.</div>
            : supers.map(s => {
              const odd = (s.role || '').toLowerCase() !== 'admin'
              return (
                <div key={s.id} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '9px 12px', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 600, flex: 1, minWidth: 180 }}>{s.email}{s.full_name ? <span style={{ color: 'var(--text3)', fontWeight: 400 }}> · {s.full_name}</span> : null}</span>
                  <span style={{ fontSize: 12, padding: '2px 8px', borderRadius: 999, background: odd ? '#fef2f2' : 'var(--surface)', color: odd ? '#c0392b' : 'var(--text3)', border: `1px solid ${odd ? '#fca5a5' : 'var(--border)'}` }}>{s.role || '—'}{odd ? ' ⚠' : ''}</span>
                  {!s.is_active && <span style={{ fontSize: 12, color: '#b45309' }}>inactive</span>}
                  <button className="btn btn-sm" onClick={() => revokeSuper(s.email)}>Revoke</button>
                </div>
              )
            })}
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {tenants.length === 0 ? <div style={{ padding: 20, color: 'var(--text3)' }}>No companies yet.</div>
          : tenants.map(t => (
            <div key={t.org_id} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '11px 14px', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 600, flex: 1, minWidth: 160 }}>{t.name}{!t.is_active && <span style={{ color: '#b45309', fontSize: 12 }}> · inactive</span>}</span>
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>{t.users} user{t.users === 1 ? '' : 's'} · {t.logins} login{t.logins === 1 ? '' : 's'}</span>
              <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text3)' }}>{t.org_id.slice(0, 8)}…</span>
              <button className="btn btn-sm" onClick={() => rename(t)}>Rename</button>
              <button className="btn btn-sm" onClick={() => toggleActive(t)}>{t.is_active ? 'Deactivate' : 'Activate'}</button>
              <button className="btn btn-sm" disabled={taBusy === t.org_id} title="Reset this company's admin login password"
                onClick={() => { if (confirm(`Reset the admin login password for ${t.name}? This locks out their current password immediately.`)) resetTenantAdmin(t) }}>
                {taBusy === t.org_id ? 'Resetting…' : '🔑 Reset admin'}</button>
            </div>
          ))}
      </div>

      {taPick && (
        <div className="card" style={{ padding: 16, marginTop: 14, borderColor: '#fde68a', background: '#fffbeb' }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>{taPick.tenant} has more than one admin login — pick which to reset:</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {(taPick.admins || []).map((a: any) => (
              <button key={a.email} className="btn btn-sm"
                onClick={() => resetTenantAdmin({ org_id: taPick.org_id, name: taPick.tenant } as Tenant, a.email)}>
                {a.full_name ? `${a.full_name} · ` : ''}{a.email}</button>
            ))}
            <button className="btn btn-sm" onClick={() => setTaPick(null)}>Cancel</button>
          </div>
        </div>
      )}
      {taReset?.ok && (
        <div className="card" style={{ marginTop: 14, padding: 12, borderRadius: 8, background: '#f0fdf4', borderColor: '#bbf7d0', fontSize: 14 }}>
          ✅ Admin login reset for <b>{taReset.tenant}</b> ({taReset.email}) · temp password: <code style={{ background: '#fff', padding: '2px 6px', borderRadius: 4 }}>{taReset.temp_password}</code>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>Hand this to the company admin who requested it — they'll be prompted to set a new password on next login.</div>
        </div>
      )}
    </div>
  )
}
