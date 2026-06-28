'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'
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

  const load = useCallback(() => {
    api('/api/v1/core/tenants').then((d: any) => setTenants(d.tenants || [])).catch(e => setErr(e?.message || 'Failed to load'))
  }, [])
  useEffect(() => { if (isSuper) load() }, [isSuper, load])

  async function addTenant() {
    if (!np.name.trim() || !np.admin_email.trim()) { setErr('Company name and admin email are required.'); return }
    setBusy(true); setErr(''); setCreated(null)
    try {
      const r = await api('/api/v1/core/tenants', { method: 'POST', body: JSON.stringify(np) })
      setCreated(r); setNp({ name: '', admin_email: '', admin_name: '', temp_password: '' }); load()
    } catch (e: any) { setErr(e?.message || 'Could not create company') } finally { setBusy(false) }
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
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>🏢 Companies (Tenants)</h1>
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

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {tenants.length === 0 ? <div style={{ padding: 20, color: 'var(--text3)' }}>No companies yet.</div>
          : tenants.map(t => (
            <div key={t.org_id} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '11px 14px', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 600, flex: 1, minWidth: 160 }}>{t.name}{!t.is_active && <span style={{ color: '#b45309', fontSize: 12 }}> · inactive</span>}</span>
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>{t.users} user{t.users === 1 ? '' : 's'} · {t.logins} login{t.logins === 1 ? '' : 's'}</span>
              <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text3)' }}>{t.org_id.slice(0, 8)}…</span>
              <button className="btn btn-sm" onClick={() => rename(t)}>Rename</button>
              <button className="btn btn-sm" onClick={() => toggleActive(t)}>{t.is_active ? 'Deactivate' : 'Activate'}</button>
            </div>
          ))}
      </div>
    </div>
  )
}
