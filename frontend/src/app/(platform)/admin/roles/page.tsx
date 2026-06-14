'use client'
import { useState, useEffect } from 'react'
import { api } from '@/lib/client'

const MODULES: { key: string; label: string }[] = [
  { key: 'commissions', label: 'Commissions' },
  { key: 'targets', label: 'Targets' },
  { key: 'asset', label: 'Asset' },
  { key: 'vip', label: 'VIP' },
  { key: 'storeops', label: 'StoreOps' },
  { key: 'notify', label: 'Notify' },
  { key: 'admin', label: 'Admin (role mgmt)' },
]
const SCOPES = [
  { v: 'all', l: 'All stores (company-wide)' },
  { v: 'market', l: 'Their market(s)' },
  { v: 'store', l: 'Their store' },
  { v: 'self', l: 'Only their own data' },
]

type Role = { id: number; name: string; display_name: string; permissions: any }
type Emp = {
  id: number; employee_id: string | null; name: string; home_store: string | null
  email: string | null; role: string | null; is_active: boolean
  app_role: string | null; has_login: boolean; app_market: string | null; app_store: string | null
}

export default function RolesAdminPage() {
  const [tab, setTab] = useState<'roles' | 'people'>('people')
  const [roles, setRoles] = useState<Role[]>([])
  const [emps, setEmps] = useState<Emp[]>([])
  const [withEmail, setWithEmail] = useState(0)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [tempPw, setTempPw] = useState<Record<string, string>>({})
  const [search, setSearch] = useState('')
  const [enforce, setEnforce] = useState<boolean | null>(null)

  async function loadAll() {
    setLoading(true)
    try {
      const cfg = await api('/api/v1/core/auth-config')
      setEnforce(!!cfg.rbac_enabled)
      const r = await api('/api/v1/core/roles')
      setRoles(r.roles || [])
      const e = await api('/api/v1/core/employees')
      setEmps(e.employees || [])
      setWithEmail(e.with_email || 0)
    } catch (err: any) { setMsg('Load failed: ' + (err?.message || err)) }
    setLoading(false)
  }
  useEffect(() => { loadAll() }, [])

  async function toggleEnforce() {
    const next = !enforce
    const warn = next
      ? 'Turn ON login enforcement?\n\nEveryone will be required to sign in. Make sure all active users have a role + login first, or they will be locked out.'
      : 'Turn OFF login enforcement?\n\nThe app becomes open to anyone with the URL again.'
    if (!confirm(warn)) return
    try {
      await api('/api/v1/core/auth-config', { method: 'PUT', body: JSON.stringify({ rbac_enabled: next }) })
      setEnforce(next)
      setMsg(next ? 'Login enforcement is ON.' : 'Login enforcement is OFF.')
    } catch (e: any) { setMsg('Could not change enforcement: ' + (e?.message || e)) }
  }

  const loginCount = emps.filter(e => e.has_login).length

  // ---- roles editing ----
  function setPerm(rid: number, fn: (p: any) => any) {
    setRoles(rs => rs.map(r => r.id === rid ? { ...r, permissions: fn({ ...(r.permissions || {}) }) } : r))
  }
  async function saveRole(r: Role) {
    setMsg('')
    try {
      await api(`/api/v1/core/roles/${r.id}`, { method: 'PUT', body: JSON.stringify({ display_name: r.display_name, permissions: r.permissions }) })
      setMsg(`Saved ${r.display_name}`)
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
  }

  // ---- people editing ----
  function setEmp(id: number, patch: Partial<Emp>) {
    setEmps(es => es.map(e => e.id === id ? { ...e, ...patch } : e))
  }
  async function assign(e: Emp) {
    if (!e.email) { setMsg(`${e.name} has no email — add one in Employees first.`); return }
    setMsg('')
    try {
      await api('/api/v1/core/users/assign', { method: 'POST', body: JSON.stringify({
        email: e.email, full_name: e.name, role: e.app_role || 'sales_rep',
        market: e.app_market || null, store_code: e.app_store || e.home_store || null,
        employee_id: e.employee_id,
      }) })
      setMsg(`Assigned ${e.name} → ${e.app_role || 'sales_rep'}`)
    } catch (err: any) { setMsg('Assign failed: ' + (err?.message || err)) }
  }
  async function createLogin(e: Emp) {
    if (!e.email) return
    setMsg('')
    try {
      await assign(e)
      const res = await api('/api/v1/core/users/create-login', { method: 'POST', body: JSON.stringify({ email: e.email }) })
      setTempPw(p => ({ ...p, [e.email!]: res.temp_password }))
      setEmp(e.id, { has_login: true })
      setMsg(`Login created for ${e.name}`)
    } catch (err: any) { setMsg('Create-login failed: ' + (err?.message || err)) }
  }
  async function provisionAll() {
    if (!confirm('Create logins for every employee who has an email + a role assigned and no login yet?')) return
    setMsg('Provisioning…')
    try {
      const res = await api('/api/v1/core/users/bulk-provision', { method: 'POST', body: JSON.stringify({}) })
      const pw: Record<string, string> = {}
      for (const r of res.results || []) if (r.ok) pw[r.email] = r.temp_password
      setTempPw(p => ({ ...p, ...pw }))
      setMsg(`Provisioned ${res.created} logins (${res.skipped} skipped).`)
      await loadAll()
    } catch (e: any) { setMsg('Bulk provision failed: ' + (e?.message || e)) }
  }

  const sel = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const filtered = emps.filter(e => !search ||
    `${e.name} ${e.home_store || ''} ${e.email || ''} ${e.app_role || ''}`.toLowerCase().includes(search.toLowerCase()))
  const tempList = Object.entries(tempPw)

  return (
    <div>
      <div style={{ marginBottom: 18 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔐 Roles &amp; Access</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Define what each role can see, assign a role to every employee, and create their logins.
        </p>
      </div>

      {/* Master switch */}
      <div className="card" style={{ padding: '14px 18px', marginBottom: 18, display: 'flex',
        alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap',
        borderLeft: `5px solid ${enforce ? '#059669' : '#d97706'}`,
        background: enforce ? '#f0fdf4' : '#fffbeb' }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700 }}>
            {enforce == null ? 'Login enforcement: …' : enforce ? '🔒 Login enforcement is ON' : '🔓 Login enforcement is OFF (app open)'}
          </div>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 2 }}>
            {enforce
              ? 'Everyone must sign in. Users without a role/login are locked out.'
              : `Assign roles + create logins below, then turn this ON to require sign-in. ${loginCount} employee logins created so far.`}
          </div>
        </div>
        <button className="btn" onClick={toggleEnforce} disabled={enforce == null}
          style={{ background: enforce ? '#dc2626' : '#059669', color: '#fff', fontWeight: 600 }}>
          {enforce ? 'Turn OFF enforcement' : 'Turn ON enforcement'}
        </button>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 18 }}>
        {(['people', 'roles'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)} style={{ padding: '7px 16px', borderRadius: 8,
            border: '1px solid var(--border)', fontSize: 13, fontWeight: 600, cursor: 'pointer',
            background: tab === t ? 'var(--accent)' : 'var(--surface)', color: tab === t ? '#fff' : 'var(--text2)' }}>
            {t === 'people' ? '👥 Assign people' : '⚙️ Role permissions'}
          </button>
        ))}
        {msg && <span style={{ fontSize: 13, alignSelf: 'center', marginLeft: 8 }}>{msg}</span>}
      </div>

      {loading ? <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div> : tab === 'roles' ? (
        <div style={{ display: 'grid', gap: 16 }}>
          {roles.map(r => {
            const p = r.permissions || {}
            const mods = p.modules || {}
            return (
              <div key={r.id} className="card" style={{ padding: 18 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                  <div style={{ fontWeight: 700, fontSize: 15 }}>{r.display_name} <span style={{ color: 'var(--text3)', fontWeight: 400, fontSize: 12 }}>({r.name})</span></div>
                  <button className="btn btn-primary" onClick={() => saveRole(r)}>💾 Save</button>
                </div>
                <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6 }}>Modules</div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 18px' }}>
                      {MODULES.map(m => (
                        <label key={m.key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                          <input type="checkbox" checked={!!mods[m.key]}
                            onChange={ev => setPerm(r.id, pp => ({ ...pp, modules: { ...(pp.modules || {}), [m.key]: ev.target.checked } }))} />
                          {m.label}
                        </label>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6 }}>Data scope</div>
                    <select style={sel} value={p.scope || 'all'} onChange={ev => setPerm(r.id, pp => ({ ...pp, scope: ev.target.value }))}>
                      {SCOPES.map(s => <option key={s.v} value={s.v}>{s.l}</option>)}
                    </select>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', margin: '12px 0 6px' }}>Landing page</div>
                    <input style={{ ...sel, width: 200 }} value={p.home || ''} placeholder="/commcalc"
                      onChange={ev => setPerm(r.id, pp => ({ ...pp, home: ev.target.value }))} />
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
            <input className="input" placeholder="Search name / store / email…" value={search} onChange={e => setSearch(e.target.value)}
              style={{ ...sel, width: 260 }} />
            <span style={{ fontSize: 13, color: 'var(--text3)' }}>{emps.length} employees · {withEmail} with email</span>
            <div style={{ flex: 1 }} />
            <button className="btn btn-primary" onClick={provisionAll}>⚡ Provision all assigned</button>
          </div>

          {tempList.length > 0 && (
            <div className="card" style={{ padding: 14, marginBottom: 16, background: '#fffbeb', border: '1px solid #fde68a' }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: '#92400e', marginBottom: 6 }}>
                🔑 Temporary passwords — hand these out; users reset on first sign-in
              </div>
              <div style={{ maxHeight: 180, overflowY: 'auto', fontFamily: 'monospace', fontSize: 12 }}>
                {tempList.map(([em, pw]) => <div key={em}>{em} → <strong>{pw}</strong></div>)}
              </div>
            </div>
          )}

          <div className="card" style={{ padding: 0 }}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 880 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Employee', 'Email', 'Role', 'Market', 'Store', 'Login', ''].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontSize: 11, fontWeight: 600,
                        color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((e, i) => (
                    <tr key={e.id} style={{ borderTop: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
                      <td style={{ padding: '8px 12px', fontSize: 13, fontWeight: 500 }}>{e.name}<div style={{ fontSize: 11, color: 'var(--text3)' }}>{e.home_store || '—'}</div></td>
                      <td style={{ padding: '8px 12px', fontSize: 12, color: e.email ? 'var(--text2)' : '#dc2626' }}>{e.email || 'no email'}</td>
                      <td style={{ padding: '8px 12px' }}>
                        <select style={sel} value={e.app_role || ''} onChange={ev => setEmp(e.id, { app_role: ev.target.value })}>
                          <option value="">— none —</option>
                          {roles.map(r => <option key={r.id} value={r.name}>{r.display_name}</option>)}
                        </select>
                      </td>
                      <td style={{ padding: '8px 12px' }}>
                        <input style={{ ...sel, width: 90 }} value={e.app_market || ''} placeholder="—"
                          onChange={ev => setEmp(e.id, { app_market: ev.target.value })} />
                      </td>
                      <td style={{ padding: '8px 12px' }}>
                        <input style={{ ...sel, width: 110 }} value={e.app_store || ''} placeholder={e.home_store || '—'}
                          onChange={ev => setEmp(e.id, { app_store: ev.target.value })} />
                      </td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>
                        {e.has_login ? <span className="badge badge-blue" style={{ fontSize: 11 }}>✓ has login</span> : <span style={{ color: 'var(--text3)' }}>—</span>}
                      </td>
                      <td style={{ padding: '8px 12px', whiteSpace: 'nowrap' }}>
                        <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => assign(e)}>Save</button>{' '}
                        {e.email && <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => createLogin(e)}>
                          {e.has_login ? 'Reset pw' : 'Create login'}</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
