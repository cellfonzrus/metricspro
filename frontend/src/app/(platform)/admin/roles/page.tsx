'use client'
import { useState, useEffect, Fragment } from 'react'
import { api } from '@/lib/client'
import { REPORT_AREAS, DATA_GRANTS, NAV, reportAreaForPath, canSeeItem, navBlockReason,
         schedulingReach, type Permissions } from '@/lib/rbac'
import { ExportButtons } from '@/lib/export'

// One-click templates for the roles most tenants need but the base 4 don't include.
const ROLE_TEMPLATES: { name: string; display: string; permissions: any }[] = [
  { name: 'district_manager', display: 'District Manager', permissions: {
    modules: { commissions: true, targets: true, asset: true, storeops: true, notify: true, helpdesk: true, hr: true },
    scope: 'market', home: '/commcalc/targets' } },
  { name: 'hr', display: 'HR', permissions: {
    modules: { hr: true, storeops: true, helpdesk: true, notify: true },
    scope: 'all', home: '/hr/people' } },
]

const MODULES: { key: string; label: string }[] = [
  { key: 'commissions', label: 'Commissions' },
  { key: 'targets', label: 'Targets' },
  { key: 'asset', label: 'Asset' },
  { key: 'vip', label: 'Distributors' },
  { key: 'accounts', label: 'Accounts' },
  { key: 'storeops', label: 'StoreOps' },
  { key: 'hr', label: 'HR (salary + comp)' },
  { key: 'notify', label: 'Notify' },
  { key: 'helpdesk', label: 'Helpdesk' },
  { key: 'support', label: 'Tech Support (cross-tenant console)' },
  { key: 'admin', label: 'Admin (role mgmt)' },
]
const SCOPES = [
  { v: 'all', l: 'All stores (company-wide)' },
  { v: 'market', l: 'Their market(s)' },
  { v: 'store', l: 'Their store' },
  { v: 'self', l: 'Only their own data' },
]
// SCHEDULING reach — separate from the REPORTING scope above. See backend app/core/scope.py.
const SCHEDULING_REACHES = [
  { v: 'org', l: 'Any employee in the company' },
  { v: 'span', l: 'Only employees in their stores' },
]
// Employee Dashboard widgets this role can see on their own dashboard (default on).
const EMP_WIDGETS = [
  { k: 'schedule', label: 'Schedule' }, { k: 'timeoff', label: 'Request time off' },
  { k: 'hours', label: 'Hours worked' }, { k: 'commission', label: 'Commission earned' },
  { k: 'targets', label: 'Targets' }, { k: 'report_card', label: 'Report card' },
  { k: 'commission_tracking', label: 'Commission tracking' }, { k: 'flags', label: 'Flags' },
  { k: 'chargebacks', label: 'Chargebacks' }, { k: 'device_history', label: 'Device history' },
]

// ── Access state: role-assigned vs login-exists, at a glance (auth-ux hardening 2026-08-03) ───────
// Creating a login and assigning a role are TWO separate steps on this page, and the grid used to
// show only the login half. A person with a login but no role signs in successfully and then hits
// the Guard's "Account not set up" wall — which is exactly what happened to the TEST DM on
// 2026-08-03 and looked to everyone like a broken app. Both halves are now one chip.
// Reads NOTHING new: app_role / login_status / has_login all already come from /core/employees
// (own-tenant only — no cross-tenant signal is introduced here).
type Access = { key: 'login_no_role' | 'active' | 'invited' | 'role_only' | 'none'
                label: string; bg: string; fg: string; title: string }
function accessState(e: Emp): Access {
  const hasRole = !!(e.app_role || '').trim()
  const activeLogin = e.login_status === 'active'
  const invited = e.login_status === 'invited' || (!e.login_status && !!e.has_login)
  const hasLogin = activeLogin || invited || !!e.has_login
  if (hasLogin && !hasRole) return { key: 'login_no_role', label: '⚠ login · NO ROLE', bg: '#fee2e2', fg: '#991b1b',
    title: 'This login exists but no role is assigned — when they sign in they see "Account not set up". Pick a role in the Role column, then press Save.' }
  if (activeLogin) return { key: 'active', label: '✓ active', bg: '#dbeafe', fg: '#1e40af',
    title: 'Role assigned and this person has signed in.' }
  if (invited) return { key: 'invited', label: '⏳ invited', bg: '#fef3c7', fg: '#92400e',
    title: 'Role assigned and an access code was issued — waiting for their first sign-in.' }
  if (hasRole) return { key: 'role_only', label: '● role · no login', bg: 'var(--surface2)', fg: 'var(--text2)',
    title: 'Role assigned, but no login has been created yet — press "Create login".' }
  return { key: 'none', label: '— not set up', bg: 'transparent', fg: 'var(--text3)',
    title: 'No role assigned and no login created yet.' }
}

type Role = { id: number; name: string; display_name: string; permissions: any }
type Emp = {
  id: number; employee_id: string | null; name: string; home_store: string | null
  email: string | null; role: string | null; is_active: boolean
  phone?: string | null; pay_rate?: number | null
  app_role: string | null; has_login: boolean; login_status?: '' | 'invited' | 'active'
  app_market: string | null; app_store: string | null
  app_store_codes?: string[] | null   // floaters cover several stores (app_users.store_codes)
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
  const [revealed, setRevealed] = useState<Record<string, any>>({})
  const [search, setSearch] = useState('')
  const [enforce, setEnforce] = useState<boolean | null>(null)
  const [np, setNp] = useState({ name: '', email: '', role: '', market: '', store: '' })
  const [upBusy, setUpBusy] = useState(false)
  const [upWithLogins, setUpWithLogins] = useState(false)
  const [widgetEmp, setWidgetEmp] = useState<number | null>(null)  // row with the widget editor open
  const [editEmp, setEditEmp] = useState<number | null>(null)      // row with the edit/remove editor open
  const [markets, setMarkets] = useState<string[]>([])             // distinct markets → checkbox picker
  const [stores, setStores] = useState<{ code: string; label: string }[]>([])  // store dropdown source
  const [newRole, setNewRole] = useState({ name: '', display: '' })            // add-a-role form
  const [settingAreas, setSettingAreas] = useState<{ key: string; label: string }[]>([])  // per-setting edit toggles
  const [scopePrev, setScopePrev] = useState<Record<string, any>>({})   // email → /core/scope-preview result

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
      try {
        // GRANT universe — deliberately GET /core/markets and NOT GET /storeops/stores.
        // /storeops/stores is (a) SPAN-SCOPED, so whoever hands out grants could only offer the
        // markets they personally cover, and (b) sourced from storeops.stores.market ALONE, while
        // the tenant's real market vocabulary is the UNION of storeops.stores.market and
        // commcalc.store_mapping.market. Owner, 2026-08-03: "the option to select PA from the roles
        // and config is not there" — PA existed only in commcalc.store_mapping. /core/markets is
        // that union, and it is THE SAME source the backend uses to RESOLVE a market grant into its
        // member stores, so this picker can never offer a market the resolver cannot bind.
        const gu = await api('/api/v1/core/markets')
        setMarkets((gu?.markets || []) as string[])
        setStores(((gu?.stores || []) as any[])
          .map((s: any) => ({ code: String(s.store_code || '').trim(), label: `${s.store_code}${s.address ? ' — ' + String(s.address).substring(0, 26) : ''}` }))
          .filter((s: any) => s.code)
          .sort((a: any, b: any) => a.code.localeCompare(b.code)))
      } catch { /* stores/markets are best-effort */ }
      try {
        const sa = await api('/api/v1/core/setting-areas')
        setSettingAreas(sa.areas || [])
      } catch { /* setting areas are best-effort */ }
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
  // Effective report access for a role+area: explicit `reports` config wins; else default by scope
  // (company-wide 'all' keeps reports, market/store/self get none) — mirrors hasReport() in rbac.ts.
  function reportChecked(p: any, key: string): boolean {
    const r = p.reports
    if (r && Object.keys(r).length) return !!r[key]
    return (p.scope || 'all') === 'all'
  }
  // Effective visibility of a single nav function for a role. This used to be a hand-copied version
  // of canSeeItem that had DRIFTED — it ignored the item's `scopes` tier and the super-admin bypass,
  // so the checkbox could read "granted" for a function the sidebar would never render (owner,
  // 2026-08-03: "KPI Metrics is allowed for the DM role but doesn't show"). It now calls the REAL
  // gate from rbac.ts, so what this page shows is what the user gets, permanently — no copy to drift.
  function fnEffective(p: any, item: any): boolean {
    return canSeeItem(p as Permissions, item)
  }
  // WHY a function is hidden, for the hint next to the checkbox. Same source as the sidebar.
  function fnBlocked(p: any, item: any) {
    return navBlockReason(p as Permissions, item)
  }
  async function saveRole(r: Role) {
    setMsg('')
    try {
      await api(`/api/v1/core/roles/${r.id}`, { method: 'PUT', body: JSON.stringify({ display_name: r.display_name, permissions: r.permissions }) })
      setMsg(`Saved ${r.display_name}`)
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
  }
  async function addRole(name: string, display_name: string, permissions: any) {
    const n = name.trim(); if (!n) { setMsg('Enter a role name.'); return }
    if (roles.some(r => r.name === n.toLowerCase().replace(/\s+/g, '_'))) { setMsg(`Role "${n}" already exists.`); return }
    setMsg('')
    try {
      await api('/api/v1/core/roles', { method: 'POST', body: JSON.stringify({ name: n, display_name: display_name || undefined, permissions: permissions || { scope: 'store', modules: {} } }) })
      setNewRole({ name: '', display: '' }); setTab('roles')
      await loadAll()
      setMsg(`Added role "${display_name || n}" — set its permissions below, then Save.`)
    } catch (e: any) { setMsg('Add role failed: ' + (e?.message || e)) }
  }
  async function deleteRole(r: Role) {
    if (!confirm(`Delete the "${r.display_name}" role? (You can't delete a role that's still assigned to someone.)`)) return
    setMsg('')
    try { await api(`/api/v1/core/roles/${r.id}`, { method: 'DELETE' }); await loadAll(); setMsg(`Deleted ${r.display_name}`) }
    catch (e: any) { setMsg('Delete failed: ' + (e?.message || e)) }
  }

  // "What does this person's grant ACTUALLY resolve to?" — the answer to "I gave the DM 3 markets,
  // why do they see everything?" without logging in as them. Read-only diagnostic; changes nothing.
  // Crucially it separates the two answers (reporting stores vs scheduling reach) and calls out a
  // market that resolved to NOTHING, which previously failed silently.
  async function previewAccess(e: Emp) {
    const email = (e.email || '').trim().toLowerCase()
    if (!email) { setMsg(`${e.name} has no email — nothing to preview.`); return }
    if (scopePrev[email]) { setScopePrev(s => { const n = { ...s }; delete n[email]; return n }); return }
    try {
      const r = await api(`/api/v1/core/scope-preview?email=${encodeURIComponent(email)}`)
      setScopePrev(s => ({ ...s, [email]: r }))
    } catch (err: any) { setMsg('Access preview failed: ' + (err?.message || err)) }
  }

  // ---- people editing ----
  function setEmp(id: number, patch: Partial<Emp>) {
    setEmps(es => es.map(e => e.id === id ? { ...e, ...patch } : e))
  }
  // "— none —" in the Role column does NOT mean "no role": /users/assign falls back to the default
  // sales_rep role, silently. Make that explicit before it happens — a login created in the
  // no-role-picked state is the direct cause of the "Account not set up" wall.
  function confirmNoRole(e: Emp, what: 'save' | 'login'): boolean {
    if ((e.app_role || '').trim()) return true
    return confirm(
      `${e.name} has no role selected.\n\n` +
      (what === 'login'
        ? 'Creating a login now assigns the default "Sales Rep" role. If that role is wrong, the '
          + 'person can sign in but will land on an empty menu — or, if the role assignment does not '
          + 'stick, on the "Account not set up" screen.\n\n'
        : 'Saving now assigns the default "Sales Rep" role.\n\n') +
      'Pick the right role in the Role column first if that is not what you want.\n\nContinue anyway?')
  }

  async function assign(e: Emp, opts: { skipRoleConfirm?: boolean } = {}) {
    if (!opts.skipRoleConfirm && !confirmNoRole(e, 'save')) return
    setMsg('')
    try {
      // Persist an inline email edit to the StoreOps roster. Real employees only
      // (id < 0 = a manually-added app_user with no employee row to update).
      if (e.id > 0) {
        await api(`/api/v1/storeops/employees/${e.id}`, { method: 'PATCH',
          body: JSON.stringify({ email: (e.email || '').trim() || null }) })
      }
      if (!e.email) {
        // A role assignment IS a login row (app_users keyed on email) — so an email is required.
        // Make that explicit instead of a near-silent "Saved" that looks like the role stuck.
        setMsg(`⚠️ ${e.name} needs an email before a role can be assigned — enter one in the Email column, then Save.${e.app_role ? ` (role "${e.app_role}" not yet applied)` : ''}`)
        return
      }
      // Floaters cover several stores: send the full set (store_codes) + a primary (store_code).
      const codes = (e.app_store_codes && e.app_store_codes.length)
        ? e.app_store_codes
        : (e.app_store ? [e.app_store] : (e.home_store ? [e.home_store] : []))
      await api('/api/v1/core/users/assign', { method: 'POST', body: JSON.stringify({
        email: e.email, full_name: e.name, role: e.app_role || 'sales_rep',
        market: e.app_market || null,
        store_code: codes[0] || e.home_store || null,   // primary store (unchanged contract)
        store_codes: codes,                             // full set for floaters
        employee_id: e.employee_id,
      }) })
      // Reflect the server-side default back into the grid so the Role column and the access chip
      // stop saying "none" for someone who now genuinely has the sales_rep role.
      if (!(e.app_role || '').trim()) setEmp(e.id, { app_role: 'sales_rep' })
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
    const hadNoRole = !(e.app_role || '').trim()
    if (!confirmNoRole(e, 'login')) return    // asked ONCE here; assign() below skips its own prompt
    setMsg('')
    try {
      await assign(e, { skipRoleConfirm: true })
      const res = await api('/api/v1/core/users/create-login', { method: 'POST', body: JSON.stringify({ email: e.email }) })
      setTempPw(p => ({ ...p, [e.email!]: res.access_code ?? res.temp_password }))
      // Optimistic UNIFORM state — "invited" until the user completes access, identical for a fresh
      // login and a pending cross-tenant invite (no enumeration signal).
      setEmp(e.id, { login_status: 'invited' })
      const deliv = res.delivery_status === 'sent'
        ? ' — the access code was emailed to them.'
        : (res.delivery_status === 'failed' ? ' — ⚠️ we could NOT email the code (hand it over below).' : '')
      // Creation-time warning: a login made with no role picked is exactly how a user lands on
      // "Account not set up". Say so here, while the admin is still on the row.
      const roleWarn = hadNoRole
        ? ' ⚠️ No role was picked — the default "Sales Rep" role was applied. Set the correct role in the'
          + ' Role column and press Save, or this person will sign in to the wrong menu.'
        : ''
      setMsg(`Access set up for ${e.name}${deliv}${roleWarn}`)
    } catch (err: any) { setMsg('Create-login failed: ' + (err?.message || err)) }
  }

  // RESEND the invite/access code (newest-wins) + re-email it. Rate-limited server-side (5/hr).
  async function resendInvite(e: Emp) {
    if (!e.email) return
    setMsg('')
    try {
      const res = await api('/api/v1/core/users/resend-invite', { method: 'POST', body: JSON.stringify({ email: e.email }) })
      setTempPw(p => ({ ...p, [e.email!]: res.access_code ?? res.temp_password }))
      setEmp(e.id, { login_status: 'invited' })
      const deliv = res.delivery_status === 'sent'
        ? 'emailed the new code' : '⚠️ could NOT email the code — hand it over below'
      setMsg(`Resent to ${e.email} — ${deliv}.`)
    } catch (err: any) { setMsg('Resend failed: ' + (err?.message || err)) }
  }

  // REVEAL the current active invite/access code (super-admin / own-tenant admin) for troubleshooting.
  // Server-side gated + audited; NEVER exposes other tenants an email belongs to.
  async function revealCode(e: Emp) {
    if (!e.email) return
    setMsg('')
    try {
      const res = await api('/api/v1/core/users/reveal-code', { method: 'POST', body: JSON.stringify({ email: e.email }) })
      setRevealed(r => ({ ...r, [e.email!]: res }))
      if (!res.code_available) setMsg(res.hint || 'No stored code — use Resend to issue a new one.')
    } catch (err: any) { setMsg('Reveal failed: ' + (err?.message || err)) }
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
  // People who CAN sign in but have no role — they hit the Guard's "Account not set up" wall.
  // Counted over the whole roster (not `filtered`), so a search box can't hide the problem.
  const loginNoRole = emps.filter(e => accessState(e).key === 'login_no_role')

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
          {/* Add a role */}
          <div className="card" style={{ padding: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>➕ Add a role</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <input style={{ ...sel, width: 170 }} placeholder="Role name (e.g. district_manager)" value={newRole.name}
                onChange={e => setNewRole(v => ({ ...v, name: e.target.value }))} />
              <input style={{ ...sel, width: 170 }} placeholder="Display name (e.g. District Manager)" value={newRole.display}
                onChange={e => setNewRole(v => ({ ...v, display: e.target.value }))} />
              <button className="btn btn-primary" onClick={() => addRole(newRole.name, newRole.display, { scope: 'store', modules: {} })}>Add custom role</button>
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>then set its permissions below</span>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Quick add:</span>
              {ROLE_TEMPLATES.filter(t => !roles.some(r => r.name === t.name)).map(t => (
                <button key={t.name} className="btn" style={{ fontSize: 12 }} onClick={() => addRole(t.name, t.display, t.permissions)}>＋ {t.display}</button>
              ))}
              {ROLE_TEMPLATES.every(t => roles.some(r => r.name === t.name)) && <span style={{ fontSize: 12, color: 'var(--text3)' }}>DM &amp; HR already exist ✓</span>}
            </div>
          </div>
          {roles.map(r => {
            const p = r.permissions || {}
            const mods = p.modules || {}
            return (
              <div key={r.id} className="card" style={{ padding: 18 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                  <div style={{ fontWeight: 700, fontSize: 15 }}>
                    <input value={r.display_name} onChange={e => setRoles(rs => rs.map(x => x.id === r.id ? { ...x, display_name: e.target.value } : x))}
                      style={{ ...sel, fontWeight: 700, fontSize: 15, width: 200 }} />
                    <span style={{ color: 'var(--text3)', fontWeight: 400, fontSize: 12, marginLeft: 6 }}>({r.name})</span>
                  </div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    {r.name !== 'admin' && <button className="btn btn-secondary" style={{ color: '#dc2626' }} onClick={() => deleteRole(r)}>🗑 Delete</button>}
                    <button className="btn btn-primary" onClick={() => saveRole(r)}>💾 Save</button>
                  </div>
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
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6 }}>Reports
                      <span style={{ fontWeight: 400, color: 'var(--text3)' }}> — separate from the module</span></div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 18px' }}>
                      {REPORT_AREAS.map(a => (
                        <label key={a.key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                          <input type="checkbox" checked={reportChecked(p, a.key)}
                            onChange={ev => setPerm(r.id, pp => {
                              const cur = REPORT_AREAS.reduce((acc, x) => ({ ...acc, [x.key]: reportChecked(pp, x.key) }), {} as Record<string, boolean>)
                              return { ...pp, reports: { ...cur, [a.key]: ev.target.checked } }
                            })} />
                          {a.label}
                        </label>
                      ))}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6, maxWidth: 220 }}>
                      Market/store managers default to <b>no</b> reports; company-wide roles keep them. Set explicitly here to override.
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6 }}>Data visibility</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6, maxWidth: 240 }}>
                      Sensitive data gated by its own grant, separate from the module. Company-wide roles (scope =
                      whole company) see it by default — grant a specific key to a scoped manager, or untick to lock it.
                    </div>
                    <div style={{ display: 'grid', gap: 2 }}>
                      {DATA_GRANTS.map(d => {
                        const dv = (p.data || {})[d.key]
                        const adminDefault = (p.scope || 'all') === 'all'
                        const checked = dv === undefined ? adminDefault : !!dv
                        return (
                          <label key={d.key} title={d.help || ''} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                            <input type="checkbox" checked={checked}
                              onChange={ev => setPerm(r.id, pp => ({ ...pp, data: { ...(pp.data || {}), [d.key]: ev.target.checked } }))} />
                            {d.label}
                          </label>
                        )
                      })}
                      {DATA_GRANTS.length === 0 && <span style={{ fontSize: 12, color: 'var(--text3)' }}>—</span>}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6 }}>Reporting scope
                      <span style={{ fontWeight: 400, color: 'var(--text3)' }}> — whose numbers</span></div>
                    <select style={sel} value={p.scope || 'all'} onChange={ev => setPerm(r.id, pp => ({ ...pp, scope: ev.target.value }))}>
                      {SCOPES.map(s => <option key={s.v} value={s.v}>{s.l}</option>)}
                    </select>
                    {/* Scheduling reach is DELIBERATELY separate from the reporting scope. One
                        grant set used to answer both "whose numbers may they see" (narrow) and
                        "whom may they schedule" (wide — employees move around), which forced
                        operators to grant every store just so a DM could schedule a borrowed rep,
                        silently widening their reporting too. Default 'org' = exactly what the app
                        does today. */}
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', margin: '12px 0 6px' }}>Scheduling reach
                      <span style={{ fontWeight: 400, color: 'var(--text3)' }}> — whom they can schedule</span></div>
                    <select style={sel} value={schedulingReach(p as Permissions)}
                      onChange={ev => setPerm(r.id, pp => ({ ...pp, scheduling_reach: ev.target.value }))}>
                      {SCHEDULING_REACHES.map(s => <option key={s.v} value={s.v}>{s.l}</option>)}
                    </select>
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6, maxWidth: 220 }}>
                      {schedulingReach(p as Permissions) === 'org'
                        ? 'Can put ANY employee in the company on a shift, while reports stay limited to the stores above. Use this instead of granting all stores.'
                        : 'Can only schedule employees inside their reporting stores.'}
                    </div>
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
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6 }}>Settings editing</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6, maxWidth: 240 }}>
                      Which settings this role may change. Company admins (scope = whole company) edit all by
                      default — grant a specific setting to a manager, or untick to lock it for this role.
                    </div>
                    <div style={{ display: 'grid', gap: 2 }}>
                      {settingAreas.map(a => {
                        const sv = (p.settings || {})[a.key]
                        const adminDefault = (p.scope || 'all') === 'all'
                        const checked = sv === undefined ? adminDefault : !!sv
                        return (
                          <label key={a.key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                            <input type="checkbox" checked={checked}
                              onChange={ev => setPerm(r.id, pp => ({ ...pp, settings: { ...(pp.settings || {}), [a.key]: ev.target.checked } }))} />
                            {a.label}
                          </label>
                        )
                      })}
                      {settingAreas.length === 0 && <span style={{ fontSize: 12, color: 'var(--text3)' }}>—</span>}
                    </div>
                  </div>
                </div>
                {/* per-function (fine-grained) access — every nav function individually grant/deny */}
                <details style={{ marginTop: 14 }}>
                  <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 700, color: 'var(--text2)' }}>
                    Functions — grant/deny every individual screen ({Object.keys(p.pages || {}).length} override{Object.keys(p.pages || {}).length === 1 ? '' : 's'})
                  </summary>
                  <div style={{ marginTop: 8, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 12 }}>
                    {NAV.map(g => (
                      <div key={g.group}>
                        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 4 }}>{g.group}</div>
                        {g.items.map(it => {
                          // WHY it's hidden, inline. "It's ticked in Roles but the tab isn't there"
                          // was untraceable before: four different gates can hide one function and
                          // nothing on screen said which. Ticking the box always grants (rbac.ts
                          // now lets an explicit grant lift the scope tier too).
                          const blocked = fnBlocked(p, it)
                          return (
                            <div key={it.href} style={{ padding: '2px 0' }}>
                              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                                <input type="checkbox" checked={fnEffective(p, it)}
                                  onChange={ev => setPerm(r.id, pp => ({ ...pp, pages: { ...(pp.pages || {}), [it.href]: ev.target.checked } }))} />
                                <span>{it.icon} {it.label}</span>
                              </label>
                              {blocked && (
                                <div title={blocked.detail}
                                  style={{ fontSize: 10, color: 'var(--text3)', marginLeft: 22, lineHeight: 1.3 }}>
                                  hidden — {blocked.gate === 'module' ? `"${it.module}" module off`
                                    : blocked.gate === 'report' ? 'report area off'
                                    : blocked.gate === 'scope' ? `scope (${(it.scopes || []).join('/') || 'management'})`
                                    : 'denied here'}
                                </div>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    ))}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>
                    Unchecked = hidden for this role even if the module is on. These per-function settings override the module/report toggles above.
                  </div>
                </details>
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
            <ExportButtons compact payload={() => ({
              title: 'Employees — Roles & Access', subtitle: `${filtered.length} employees`, filename: 'employees',
              sheets: [{ name: 'Employees', rows: filtered, columns: [
                { header: 'Name', get: (e: Emp) => e.name },
                { header: 'Employee ID', get: (e: Emp) => e.employee_id || '' },
                { header: 'Email', get: (e: Emp) => e.email || '' },
                { header: 'Home store', get: (e: Emp) => e.home_store || '' },
                { header: 'Job title', get: (e: Emp) => e.role || '' },
                { header: 'Pay $/hr', get: (e: Emp) => e.pay_rate ?? '', money: true },
                { header: 'App role', get: (e: Emp) => e.app_role || '' },
                { header: 'Market', get: (e: Emp) => e.app_market || '' },
                { header: 'Store(s)', get: (e: Emp) => (e.app_store_codes && e.app_store_codes.length ? e.app_store_codes.join(', ') : (e.app_store || '')) },
                { header: 'Login', get: (e: Emp) => e.login_status === 'active' ? 'Active' : ((e.login_status === 'invited' || e.has_login) ? 'Invited' : 'No') },
                // RULE FOUR — what you see exports: the same access chip the grid shows.
                { header: 'Access', get: (e: Emp) => accessState(e).label.replace(/^[^A-Za-z]+/, '').trim() || 'not set up' },
                { header: 'Active', get: (e: Emp) => e.is_active ? 'Yes' : 'No' },
              ] }],
            })} />
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
              <MarketPicker value={np.market} markets={markets} onChange={v => setNp(x => ({ ...x, market: v }))} />
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

          {/* Login-without-role warning (auth-ux hardening 2026-08-03). These people sign in fine and
              then hit "Account not set up" — from their side it looks like the whole app is broken.
              Own-tenant data only; no names are exposed beyond this admin's own roster. */}
          {loginNoRole.length > 0 && (
            <div className="card" style={{ padding: 14, marginBottom: 16, background: '#fef2f2',
              border: '1px solid #fecaca', borderLeft: '5px solid #dc2626' }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: '#991b1b', marginBottom: 4 }}>
                ⚠️ {loginNoRole.length} {loginNoRole.length === 1 ? 'person has' : 'people have'} a login but NO role assigned
              </div>
              <div style={{ fontSize: 12.5, color: '#7f1d1d' }}>
                They can sign in, but the app shows them <b>&ldquo;Account not set up&rdquo;</b> until a role is
                assigned — it looks to them like every module is broken. Pick a role in the <b>Role</b>
                column for each, then press <b>Save</b>:{' '}
                <span style={{ fontWeight: 600 }}>{loginNoRole.map(e => e.name).join(', ')}</span>
              </div>
            </div>
          )}

          {tempList.length > 0 && (
            <div className="card" style={{ padding: 14, marginBottom: 16, background: '#fffbeb', border: '1px solid #fde68a' }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: '#92400e', marginBottom: 6 }}>
                🔑 Access codes — hand each to the person. New users set a password on first sign-in;
                anyone who already uses MetricsPro signs in with their existing password and enters this
                code to connect this company.
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
                    {['Employee', 'Email', 'Role', 'Market', 'Store', 'Access', ''].map(h => (
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
                          ? <input
                              style={{ ...sel, width: 200, ...(e.app_role && !(e.email || '').trim() ? { borderColor: '#dc2626', background: '#fef2f2' } : {}) }}
                              type="email" value={e.email || ''}
                              placeholder={e.app_role && !(e.email || '').trim() ? '✉️ email required for this role' : 'add email…'}
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
                        <MarketPicker value={e.app_market || ''} markets={markets} onChange={v => setEmp(e.id, { app_market: v })} />
                      </td>
                      <td style={{ padding: '8px 12px' }}>
                        <StorePicker
                          value={(e.app_store_codes && e.app_store_codes.length) ? e.app_store_codes : (e.app_store ? [e.app_store] : [])}
                          stores={stores}
                          placeholder={e.home_store || 'Store(s)…'}
                          onChange={codes => setEmp(e.id, { app_store_codes: codes, app_store: codes[0] || null })} />
                      </td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>
                        {/* Access = role-assigned AND login-exists in ONE chip (auth-ux hardening).
                            Uniform status (platform-core-11) is preserved: a pending invite and a
                            just-created login that hasn't signed in both still read "invited" — the
                            roster never reveals whether an email exists in another tenant. */}
                        {(() => { const a = accessState(e); return (
                          <span className="badge" title={a.title}
                            style={{ fontSize: 11, background: a.bg, color: a.fg, whiteSpace: 'nowrap',
                              fontWeight: a.key === 'login_no_role' ? 700 : 600 }}>{a.label}</span>
                        ) })()}
                      </td>
                      <td style={{ padding: '8px 12px', whiteSpace: 'nowrap' }}>
                        <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => assign(e)}>Save</button>{' '}
                        {e.email && <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => createLogin(e)}>
                          {e.has_login ? 'Reset pw' : 'Create login'}</button>}{' '}
                        {e.email && e.login_status === 'invited' && (
                          <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} title="Re-email the access/invite code (rate-limited)"
                            onClick={() => resendInvite(e)}>📧 Resend</button>)}{' '}
                        {e.email && (e.login_status === 'invited' || e.has_login) && (
                          <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} title="Reveal the current invite/access code (audited)"
                            onClick={() => revealCode(e)}>👁 Reveal</button>)}{' '}
                        {e.app_role && <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} title="Per-person dashboard widgets"
                          onClick={() => setWidgetEmp(widgetEmp === e.id ? null : e.id)}>
                          🎛️ Widgets{ovCount ? ` (${ovCount})` : ''}</button>}{' '}
                        <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} title="Edit details / remove this person"
                          onClick={() => setEditEmp(editEmp === e.id ? null : e.id)}>✏️ Edit</button>{' '}
                        {e.email && e.app_role && (
                          <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }}
                            title="What do this person's grants ACTUALLY resolve to? (reporting stores vs scheduling reach)"
                            onClick={() => previewAccess(e)}>🔍 Access</button>)}
                      </td>
                    </tr>
                    {e.email && scopePrev[(e.email || '').toLowerCase()] && (() => {
                      const sp = scopePrev[(e.email || '').toLowerCase()]
                      return (
                        <tr style={{ background: '#f5f3ff' }}>
                          <td colSpan={7} style={{ padding: '10px 16px', borderTop: '1px dashed var(--border)', fontSize: 12 }}>
                            <div style={{ display: 'grid', gap: 6 }}>
                              <div><strong>Reporting</strong> (whose numbers they see) —{' '}
                                {sp.reporting?.unrestricted
                                  ? <span style={{ color: '#b45309', fontWeight: 700 }}>EVERY store (role scope = all stores)</span>
                                  : <span><strong>{(sp.reporting?.stores || []).length}</strong> store(s): {(sp.reporting?.stores || []).join(', ') || '— none —'}</span>}
                              </div>
                              <div><strong>Scheduling</strong> (whom they can put on a shift) —{' '}
                                {sp.scheduling?.reach === 'org'
                                  ? 'ANY employee in the company'
                                  : 'only employees in the stores above'}
                              </div>
                              <div style={{ color: 'var(--text3)' }}>
                                Markets granted: {(sp.granted_markets || []).join(', ') || '—'}
                                {(sp.pinned_stores || []).length > 0 && <> · Stores pinned: {sp.pinned_stores.join(', ')}</>}
                                {(sp.org_unit_stores || []).length > 0 && <> · Org-unit stores: {sp.org_unit_stores.join(', ')}</>}
                              </div>
                              {(sp.unresolved_markets || []).length > 0 && (
                                <div style={{ color: '#b91c1c', fontWeight: 600 }}>
                                  ⚠ These markets matched NO store and grant nothing: {sp.unresolved_markets.join(', ')}
                                  <span style={{ fontWeight: 400 }}> — the tenant knows: {(sp.org_markets || []).join(', ') || '(none)'}</span>
                                </div>
                              )}
                            </div>
                            <button className="btn" style={{ fontSize: 11, padding: '2px 8px', marginTop: 8 }}
                              onClick={() => previewAccess(e)}>Hide</button>
                          </td>
                        </tr>
                      )
                    })()}
                    {e.email && revealed[e.email] && (
                      <tr style={{ background: '#f0f9ff' }}>
                        <td colSpan={7} style={{ padding: '10px 16px', borderTop: '1px dashed var(--border)', fontSize: 12 }}>
                          {revealed[e.email].code_available ? (
                            <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'center' }}>
                              <span>Access code: <strong style={{ fontFamily: 'monospace' }}>{revealed[e.email].access_code}</strong></span>
                              <span>Status: <strong>{revealed[e.email].status}</strong></span>
                              {revealed[e.email].expires_at && <span>Expires: {String(revealed[e.email].expires_at).slice(0, 10)}</span>}
                              <span>Delivery: <strong style={{ color: revealed[e.email].delivery_status === 'failed' ? '#dc2626' : '#059669' }}>
                                {revealed[e.email].delivery_status || 'not attempted'}</strong></span>
                              {(revealed[e.email].resent_count || 0) > 0 && <span>Resent ×{revealed[e.email].resent_count}</span>}
                            </div>
                          ) : (
                            <span style={{ color: 'var(--text2)' }}>{revealed[e.email].hint}</span>
                          )}
                          <button className="btn" style={{ fontSize: 11, padding: '2px 8px', marginLeft: 12 }}
                            onClick={() => setRevealed(r => { const n = { ...r }; delete n[e.email!]; return n })}>Hide</button>
                        </td>
                      </tr>
                    )}
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

// Multi-select store picker — assign one or many stores to a rep (floaters cover several).
// Emits a string[] of store_codes (what app_users.store_codes + the span resolver read), and
// is a real dropdown of valid stores so a wrong/typo store can't be entered.
function StorePicker({ value, stores, placeholder, onChange }:
  { value: string[]; stores: { code: string; label: string }[]; placeholder?: string; onChange: (v: string[]) => void }) {
  const [open, setOpen] = useState(false)
  const chosen = new Set(value || [])
  const toggle = (code: string) => {
    const next = new Set(chosen); next.has(code) ? next.delete(code) : next.add(code)
    onChange(Array.from(next))
  }
  const label = chosen.size === 0 ? (placeholder || 'Store(s)…')
    : chosen.size === 1 ? Array.from(chosen)[0] : `${chosen.size} stores`
  const btn: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', cursor: 'pointer', minWidth: 120, display: 'inline-flex', justifyContent: 'space-between', gap: 6, alignItems: 'center' }
  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button type="button" onClick={() => setOpen(o => !o)} style={btn}>
        <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 130 }}>{label}</span>
        <span style={{ fontSize: 10, opacity: 0.6 }}>▾</span>
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
          <div style={{ position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 41, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: 8, minWidth: 220, maxHeight: 320, overflowY: 'auto', boxShadow: '0 8px 24px rgba(0,0,0,0.18)' }}>
            {stores.length === 0 && <div style={{ fontSize: 12, color: 'var(--text3)', padding: 4 }}>No stores found</div>}
            {stores.map(s => (
              <label key={s.code} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 6px', fontSize: 13, cursor: 'pointer' }}>
                <input type="checkbox" checked={chosen.has(s.code)} onChange={() => toggle(s.code)} /> {s.label}
              </label>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// Checkbox market picker — assign one or many markets to a manager without touching the org tree.
// Stores the selection as a comma-separated string (what the span resolver already reads).
function MarketPicker({ value, markets, onChange }: { value: string; markets: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false)
  const chosen = new Set((value || '').split(',').map(s => s.trim()).filter(Boolean))
  const toggle = (m: string) => {
    const next = new Set(chosen); next.has(m) ? next.delete(m) : next.add(m)
    onChange(Array.from(next).join(', '))
  }
  const label = chosen.size === 0 ? 'Market(s)…' : chosen.size === 1 ? Array.from(chosen)[0] : `${chosen.size} markets`
  const btn: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', cursor: 'pointer', minWidth: 120, display: 'inline-flex', justifyContent: 'space-between', gap: 6, alignItems: 'center' }
  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button type="button" onClick={() => setOpen(o => !o)} style={btn}>
        <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 130 }}>{label}</span>
        <span style={{ fontSize: 10, opacity: 0.6 }}>▾</span>
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
          <div style={{ position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 41, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: 8, minWidth: 180, maxHeight: 300, overflowY: 'auto', boxShadow: '0 8px 24px rgba(0,0,0,0.18)' }}>
            {markets.length === 0 && <div style={{ fontSize: 12, color: 'var(--text3)', padding: 4 }}>No markets found</div>}
            {markets.map(m => (
              <label key={m} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 6px', fontSize: 13, cursor: 'pointer' }}>
                <input type="checkbox" checked={chosen.has(m)} onChange={() => toggle(m)} /> {m}
              </label>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
