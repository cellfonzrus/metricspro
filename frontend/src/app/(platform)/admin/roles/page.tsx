'use client'
import { useState, useEffect, Fragment } from 'react'
import { api } from '@/lib/client'

const MODULES: { key: string; label: string }[] = [
  { key: 'commissions', label: 'Commissions' },
  { key: 'targets', label: 'Targets' },
  { key: 'asset', label: 'Asset' },
  { key: 'vip', label: 'VIP' },
  { key: 'accounts', label: 'Accounts' },
  { key: 'storeops', label: 'StoreOps' },
  { key: 'notify', label: 'Notify' },
  { key: 'helpdesk', label: 'Helpdesk' },
  { key: 'admin', label: 'Admin (role mgmt)' },
]
const SCOPES = [
  { v: 'all', l: 'All stores (company-wide)' },
  { v: 'market', l: 'Their market(s)' },
  { v: 'store', l: 'Their store' },
  { v: 'self', l: 'Only their own data' },
]
// Employee Dashboard widgets this role can see on their own dashboard (default on).
const EMP_WIDGETS = [
  { k: 'schedule', label: 'Schedule' }, { k: 'timeoff', label: 'Request time off' },
  { k: 'hours', label: 'Hours worked' }, { k: 'commission', label: 'Commission earned' },
  { k: 'targets', label: 'Targets' }, { k: 'report_card', label: 'Report card' },
  { k: 'commission_tracking', label: 'Commission tracking' }, { k: 'flags', label: 'Flags' },
  { k: 'chargebacks', label: 'Chargebacks' },
]

type Role = { id: number; name: string; display_name: string; permissions: any }
type Emp = {
  id: number; employee_id: string | null; name: string; home_store: string | null
  email: string | null; role: string | null; is_active: boolean
  phone?: string | null; pay_rate?: number | null
  app_role: string | null; has_login: boolean; app_market: string | null; app_store: string | null
  widget_overrides?: Record<string, boolean> | null
  manual?: boolean
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
  const [np, setNp] = useState({ name: '', email: '', role: '', market: '', store: '' })
  const [upBusy, setUpBusy] = useState(false)
  const [upWithLogins, setUpWithLogins] = useState(false)
  const [widgetEmp, setWidgetEmp] = useState<number | null>(null)  // row with the widget editor open
  const [editEmp, setEditEmp] = useState<number | null>(null)      // row with the edit/remove editor open

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
    setMsg('')
    try {
      // Persist an inline email edit to the StoreOps roster. Real employees only
      // (id < 0 = a manually-added app_user with no employee row to update).
      if (e.id > 0) {
        await api(`/api/v1/storeops/employees/${e.id}`, { method: 'PATCH',
          body: JSON.stringify({ email: (e.email || '').trim() || null }) })
      }
      if (!e.email) { setMsg(`Saved ${e.name}. Add an email above to assign a role / create a login.`); return }
      await api('/api/v1/core/users/assign', { method: 'POST', body: JSON.stringify({
        email: e.email, full_name: e.name, role: e.app_role || 'sales_rep',
        market: e.app_market || null, store_code: e.app_store || e.home_store || null,
        employee_id: e.employee_id,
      }) })
      setMsg(`Saved ${e.name} → ${e.app_role || 'sales_rep'}`)
    } catch (err: any) { setMsg('Save failed: ' + (err?.message || err)) }
  }
  // ---- per-employee widget overrides (#1b) ----
  // The role's default visibility for a widget (default on if the role doesn't list it).
  function roleWidgetDefault(roleName: string | null, k: string): boolean {
    const r = roles.find(x => x.name === roleName)
    const ew = (r?.permissions || {}).employee_widgets || {}
    return ew[k] !== false
  }
  // Effective shown state for a widget on this employee = override if set, else role default.
  function widgetEffective(e: Emp, k: string): boolean {
    const ov = e.widget_overrides || {}
    return k in ov ? !!ov[k] : roleWidgetDefault(e.app_role, k)
  }
  function toggleWidget(e: Emp, k: string, val: boolean) {
    const ov = { ...(e.widget_overrides || {}) }
    if (val === roleWidgetDefault(e.app_role, k)) delete ov[k]  // back to role default -> drop the override
    else ov[k] = val
    setEmp(e.id, { widget_overrides: Object.keys(ov).length ? ov : null })
  }
  async function saveWidgets(e: Emp) {
    setMsg('')
    try {
      await api('/api/v1/core/employee-widgets', { method: 'PUT', body: JSON.stringify({
        employee_id: e.employee_id, email: e.email, widget_overrides: e.widget_overrides || null,
      }) })
      const n = Object.keys(e.widget_overrides || {}).length
      setMsg(`Saved widgets for ${e.name}${n ? ` (${n} override${n > 1 ? 's' : ''})` : ' (all inherit role)'}`)
      setWidgetEmp(null)
    } catch (err: any) { setMsg('Widget save failed: ' + (err?.message || err)) }
  }
  function resetWidgets(e: Emp) { setEmp(e.id, { widget_overrides: null }) }

  // ---- edit employee details + remove (delete / deactivate) ----
  async function saveDetails(e: Emp) {
    setMsg('')
    try {
      await api(`/api/v1/storeops/employees/${e.id}`, { method: 'PATCH', body: JSON.stringify({
        name: e.name, home_store: e.home_store, role: e.role,
        pay_rate: e.pay_rate == null || (e.pay_rate as any) === '' ? null : Number(e.pay_rate),
        phone: e.phone || null, is_active: !!e.is_active,
      }) })
      setMsg(`Saved ${e.name}`)
    } catch (err: any) { setMsg('Save failed: ' + (err?.message || err)) }
  }

  async function removeEmp(e: Emp, mode: 'delete' | 'deactivate') {
    const ok = confirm(mode === 'delete'
      ? `⚠️ Permanently delete ${e.name}?\n\nRemoves the employee, their role assignment, AND their login everywhere (StoreOps + Roles). Historical records keyed by name (commissions, closing) are kept. This cannot be undone.`
      : `Deactivate ${e.name}?\n\nMarks them inactive and revokes their login. Reversible.`)
    if (!ok) return
    setMsg('')
    try {
      const r = await api('/api/v1/core/employees/purge', { method: 'POST', body: JSON.stringify({
        employee_pk: e.id, email: e.email, employee_id: e.employee_id, mode,
      }) })
      setEditEmp(null)
      const auth = r?.login?.auth_deleted ? `, ${r.login.auth_deleted} login` : ''
      setMsg(`${mode === 'delete' ? 'Deleted' : 'Deactivated'} ${e.name}${auth}`)
      await loadAll()
    } catch (err: any) { setMsg(`${mode === 'delete' ? 'Delete' : 'Deactivate'} failed: ` + (err?.message || err)) }
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

  // ---- add people (single + bulk upload) ----
  async function addPerson() {
    const email = np.email.trim().toLowerCase()
    if (!email || !email.includes('@')) { setMsg('Enter a valid email for the new person.'); return }
    if (!np.role) { setMsg('Pick a role for the new person.'); return }
    setMsg('')
    try {
      await api('/api/v1/core/users/assign', { method: 'POST', body: JSON.stringify({
        email, full_name: np.name || null, role: np.role,
        market: np.market || null, store_code: np.store || null,
      }) })
      setMsg(`Added ${np.name || email} → ${np.role}`)
      setNp({ name: '', email: '', role: '', market: '', store: '' })
      await loadAll()
    } catch (err: any) { setMsg('Add failed: ' + (err?.message || err)) }
  }

  async function downloadTemplate() {
    const XLSX = await import('xlsx')
    const ws = XLSX.utils.aoa_to_sheet([
      ['full_name', 'email', 'role', 'market', 'store_code'],
      ['Jane Doe', 'jane@example.com', 'sales_rep', '', ''],
      ['John Smith', 'john@example.com', 'store_manager', '', ''],
    ])
    ws['!cols'] = [{ wch: 22 }, { wch: 28 }, { wch: 16 }, { wch: 12 }, { wch: 14 }]
    const rs = XLSX.utils.aoa_to_sheet([['Valid values for the "role" column:'],
      ...roles.map(r => [`${r.name}   (${r.display_name})`])])
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Employees')
    XLSX.utils.book_append_sheet(wb, rs, 'Roles')
    XLSX.writeFile(wb, 'employee-upload-template.xlsx')
  }

  async function handleUpload(file: File) {
    setUpBusy(true); setMsg('Reading sheet…')
    try {
      const XLSX = await import('xlsx')
      const wb = XLSX.read(await file.arrayBuffer())
      const raw: any[] = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { defval: '' })
      const pick = (r: any, keys: string[]) => {
        for (const k of Object.keys(r)) if (keys.includes(k.trim().toLowerCase())) return String(r[k]).trim()
        return ''
      }
      const valid = new Set(roles.map(r => r.name))
      const users: any[] = []
      const localErr: string[] = []
      raw.forEach((r, i) => {
        const email = pick(r, ['email', 'e-mail']).toLowerCase()
        if (!email) return // skip blank rows
        let role = pick(r, ['role'])
        if (role && !valid.has(role)) {
          const norm = role.toLowerCase().replace(/\s+/g, '_')
          const m = roles.find(rr => rr.name === norm || rr.display_name.toLowerCase() === role.toLowerCase())
          if (m) role = m.name
          else { localErr.push(`Row ${i + 2}: unknown role "${role}"`); return }
        }
        users.push({ email, full_name: pick(r, ['full_name', 'name', 'full name']), role,
          market: pick(r, ['market']), store_code: pick(r, ['store_code', 'store']) })
      })
      if (!users.length) { setMsg('No valid rows found. ' + localErr.join('; ')); setUpBusy(false); return }
      const res = await api('/api/v1/core/users/bulk-assign', { method: 'POST', body: JSON.stringify({ users }) })
      const errs = [...localErr, ...((res.errors || []).map((e: any) => `Row ${e.row}: ${e.error}`))]
      if (upWithLogins) {
        const prov = await api('/api/v1/core/users/bulk-provision', { method: 'POST', body: JSON.stringify({}) })
        const pw: Record<string, string> = {}
        for (const r of prov.results || []) if (r.ok) pw[r.email] = r.temp_password
        setTempPw(p => ({ ...p, ...pw }))
      }
      setMsg(`Uploaded: ${res.assigned} assigned${errs.length ? ` · ${errs.length} skipped` : ''}.${errs.length ? ' ' + errs.slice(0, 4).join('; ') : ''}`)
      await loadAll()
    } catch (err: any) { setMsg('Upload failed: ' + (err?.message || err)) }
    setUpBusy(false)
  }

  const sel = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const lbl: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 3, fontSize: 11, color: 'var(--text2)', fontWeight: 600 }
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
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6 }}>Employee dashboard widgets</div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 18px' }}>
                      {EMP_WIDGETS.map(wd => {
                        const ew = p.employee_widgets || {}
                        return (
                          <label key={wd.k} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                            <input type="checkbox" checked={ew[wd.k] !== false}
                              onChange={ev => setPerm(r.id, pp => ({ ...pp, employee_widgets: { ...(pp.employee_widgets || {}), [wd.k]: ev.target.checked } }))} />
                            {wd.label}
                          </label>
                        )
                      })}
                    </div>
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

          {/* Add people: single + bulk sheet upload */}
          <div className="card" style={{ padding: 14, marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>➕ Add people (not on the StoreOps roster)</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
              <input style={{ ...sel, width: 150 }} placeholder="Full name" value={np.name} onChange={e => setNp(v => ({ ...v, name: e.target.value }))} />
              <input style={{ ...sel, width: 190 }} placeholder="Email *" value={np.email} onChange={e => setNp(v => ({ ...v, email: e.target.value }))} />
              <select style={sel} value={np.role} onChange={e => setNp(v => ({ ...v, role: e.target.value }))}>
                <option value="">— role * —</option>
                {roles.map(r => <option key={r.id} value={r.name}>{r.display_name}</option>)}
              </select>
              <input style={{ ...sel, width: 90 }} placeholder="Market" value={np.market} onChange={e => setNp(v => ({ ...v, market: e.target.value }))} />
              <input style={{ ...sel, width: 110 }} placeholder="Store" value={np.store} onChange={e => setNp(v => ({ ...v, store: e.target.value }))} />
              <button className="btn btn-primary" onClick={addPerson}>➕ Add</button>
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', borderTop: '1px solid var(--border)', paddingTop: 10 }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>Bulk:</span>
              <button className="btn" onClick={downloadTemplate}>⬇️ Download template</button>
              <label className="btn" style={{ cursor: upBusy ? 'default' : 'pointer', margin: 0 }}>
                {upBusy ? '⏳ Uploading…' : '⬆️ Upload employee sheet'}
                <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} disabled={upBusy}
                  onChange={e => { const f = e.target.files?.[0]; if (f) handleUpload(f); e.currentTarget.value = '' }} />
              </label>
              <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 5 }}>
                <input type="checkbox" checked={upWithLogins} onChange={e => setUpWithLogins(e.target.checked)} />
                also create logins
              </label>
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>
                Columns: full_name, email, role, market, store_code · roles: {roles.map(r => r.name).join(', ')}
              </span>
            </div>
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
                  {filtered.map((e, i) => {
                    const ovCount = Object.keys(e.widget_overrides || {}).length
                    return (
                    <Fragment key={e.id}>
                    <tr style={{ borderTop: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
                      <td style={{ padding: '8px 12px', fontSize: 13, fontWeight: 500 }}>{e.name}{e.manual && <span className="badge" style={{ fontSize: 10, marginLeft: 6 }}>added</span>}<div style={{ fontSize: 11, color: 'var(--text3)' }}>{e.manual ? '✋ manual user' : (e.home_store || '—')}</div></td>
                      <td style={{ padding: '8px 12px' }}>
                        {e.id > 0
                          ? <input style={{ ...sel, width: 200 }} type="email" value={e.email || ''} placeholder="add email…"
                              onChange={ev => setEmp(e.id, { email: ev.target.value })} />
                          : <span style={{ fontSize: 12, color: e.email ? 'var(--text2)' : '#dc2626' }}>{e.email || 'no email'}</span>}
                      </td>
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
                          {e.has_login ? 'Reset pw' : 'Create login'}</button>}{' '}
                        {e.app_role && <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} title="Per-person dashboard widgets"
                          onClick={() => setWidgetEmp(widgetEmp === e.id ? null : e.id)}>
                          🎛️ Widgets{ovCount ? ` (${ovCount})` : ''}</button>}{' '}
                        <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} title="Edit details / remove this person"
                          onClick={() => setEditEmp(editEmp === e.id ? null : e.id)}>✏️ Edit</button>
                      </td>
                    </tr>
                    {widgetEmp === e.id && (
                      <tr style={{ background: '#f8fafc' }}>
                        <td colSpan={7} style={{ padding: '10px 16px', borderTop: '1px dashed var(--border)' }}>
                          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>
                            Dashboard widgets for {e.name} <span style={{ fontWeight: 400, color: 'var(--text3)' }}>
                              — overrides the {roles.find(r => r.name === e.app_role)?.display_name || e.app_role} role default per person</span>
                          </div>
                          <div style={{ display: 'flex', gap: '6px 18px', flexWrap: 'wrap', marginBottom: 8 }}>
                            {EMP_WIDGETS.map(wd => {
                              const overridden = e.widget_overrides && wd.k in e.widget_overrides
                              return (
                                <label key={wd.k} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 13,
                                  padding: '2px 7px', borderRadius: 6, border: '1px solid var(--border)',
                                  background: overridden ? '#fef9c3' : 'var(--surface)' }}>
                                  <input type="checkbox" checked={widgetEffective(e, wd.k)}
                                    onChange={ev => toggleWidget(e, wd.k, ev.target.checked)} />
                                  {wd.label}{overridden ? ' •' : ''}
                                </label>
                              )
                            })}
                          </div>
                          <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 12px' }} onClick={() => saveWidgets(e)}>💾 Save widgets</button>{' '}
                          <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => resetWidgets(e)}>↺ Reset to role default</button>
                          <span style={{ fontSize: 11, color: 'var(--text3)', marginLeft: 10 }}>• = overridden for this person</span>
                        </td>
                      </tr>
                    )}
                    {editEmp === e.id && (
                      <tr style={{ background: '#f8fafc' }}>
                        <td colSpan={7} style={{ padding: '12px 16px', borderTop: '1px dashed var(--border)' }}>
                          {e.id > 0 ? (
                            <>
                              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>Edit {e.name}</div>
                              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 10 }}>
                                <label style={lbl}>Full name<input style={{ ...sel, width: 160 }} value={e.name || ''} onChange={ev => setEmp(e.id, { name: ev.target.value })} /></label>
                                <label style={lbl}>Home store<input style={{ ...sel, width: 120 }} value={e.home_store || ''} onChange={ev => setEmp(e.id, { home_store: ev.target.value })} /></label>
                                <label style={lbl}>Job title<input style={{ ...sel, width: 130 }} value={e.role || ''} placeholder="Sales Rep" onChange={ev => setEmp(e.id, { role: ev.target.value })} /></label>
                                <label style={lbl}>Pay $/hr<input type="number" style={{ ...sel, width: 80 }} value={e.pay_rate ?? ''} onChange={ev => setEmp(e.id, { pay_rate: ev.target.value === '' ? null : (ev.target.value as any) })} /></label>
                                <label style={lbl}>Phone<input style={{ ...sel, width: 150 }} value={e.phone || ''} placeholder="5162330422" onChange={ev => setEmp(e.id, { phone: ev.target.value })} /></label>
                                <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 13, paddingBottom: 6 }}>
                                  <input type="checkbox" checked={!!e.is_active} onChange={ev => setEmp(e.id, { is_active: ev.target.checked })} />Active
                                </label>
                              </div>
                              <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 12px' }} onClick={() => saveDetails(e)}>💾 Save details</button>{' '}
                              <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => removeEmp(e, 'deactivate')}>🚫 Deactivate (revoke login)</button>{' '}
                              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px', color: '#dc2626' }} onClick={() => removeEmp(e, 'delete')}>🗑 Delete permanently</button>
                              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>
                                Delete removes the employee + role assignment + login everywhere. History keyed by name (commissions, closing) is kept.
                              </div>
                            </>
                          ) : (
                            <>
                              <div style={{ fontSize: 12, marginBottom: 8 }}>✋ Manually-added Roles user — no StoreOps roster row to edit.</div>
                              <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => removeEmp(e, 'deactivate')}>🚫 Deactivate (revoke login)</button>{' '}
                              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px', color: '#dc2626' }} onClick={() => removeEmp(e, 'delete')}>🗑 Delete permanently</button>
                            </>
                          )}
                        </td>
                      </tr>
                    )}
                    </Fragment>
                  )})}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
