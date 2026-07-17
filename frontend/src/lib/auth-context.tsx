'use client'
import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { supabase, setSessionOrgId, getActiveOrg, setActiveOrg, set2faToken } from './client'
import type { Permissions, CarrierRef } from './rbac'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export type AppUser = {
  id: string; auth_id: string; email: string; full_name: string | null
  role: string | null; role_display?: string | null
  market: string | null; store_code: string | null; store_codes: string[] | null
  employee_id: string | null; is_active: boolean; must_reset_password: boolean
  super_admin?: boolean; org_id?: string | null
}

export type TenantInfo = {
  org_id?: string; name?: string; setup_complete: boolean
  pay_period?: { work_week_start_dow: number; pay_period_type: string; payday_dow: number; payday_weeks_after: number }
}

// One membership of a login (mig 706): which tenant + this login's role IN that tenant.
export type TenantMembership = {
  org_id: string; name: string; slug?: string | null
  role: string | null; role_display?: string | null
  is_default: boolean; super_admin: boolean; is_active: boolean
}

// A pending account-link invite addressed to THIS login's email (platform-core-11). Shows ONLY the
// inviting tenant's name (zero cross-tenant disclosure). The user chooses connect / disable / not-now.
export type PendingConnection = { org_id: string; tenant_name: string; invited_at?: string | null }

// 2FA gate for the active tenant/user (auth-hardening). required && !verified ⇒ show the OTP screen.
export type TwoFactorState = {
  required: boolean; verified: boolean; mode?: string; user_channels?: string[]
}
export type PasswordPolicy = {
  min_length: number; max_length: number
  require_upper: boolean; require_lower: boolean; require_digit: boolean; require_special: boolean
}

type AuthState = {
  loading: boolean
  session: any | null
  user: AppUser | null
  permissions: Permissions
  carriers: CarrierRef[]
  provisioned: boolean
  active: boolean
  tenant: TenantInfo | null
  token: string | null
  // Multi-tenant login switcher (platform-core-9):
  tenants: TenantMembership[]        // every tenant this login belongs to (>1 ⇒ show a picker/switcher)
  activeOrg: string | null           // the tenant currently being acted as
  needsTenantChoice: boolean         // >1 membership and none chosen yet ⇒ show the picker
  switchTenant: (orgId: string) => Promise<void>
  // Consent-based account linking (platform-core-11):
  pendingConnections: PendingConnection[]                       // unresolved invites to this login's email
  connectTenant: (orgId: string, code: string) => Promise<void> // attach the tenant onto this login
  disableAndSwitch: (orgId: string, code: string) => Promise<any> // disable old login, take a fresh one
  dismissPending: (orgId: string) => void                       // "not now" — proceed, invite stays pending
  // Two-factor authentication (auth-hardening):
  twofa: TwoFactorState                                         // required/verified for the active tenant
  needs2fa: boolean                                            // required && !verified ⇒ OTP screen
  passwordPolicy: PasswordPolicy | null                        // active tenant policy (client-side hints)
  startTwoFactor: (channel?: string) => Promise<any>           // request an OTP over a channel
  verifyTwoFactor: (code: string, remember?: boolean) => Promise<void> // verify + store the marker
  signOut: () => Promise<void>
  refresh: () => Promise<void>
}

const Ctx = createContext<AuthState>({
  loading: true, session: null, user: null, permissions: {}, carriers: [], provisioned: false,
  active: false, tenant: null, token: null, tenants: [], activeOrg: null, needsTenantChoice: false,
  switchTenant: async () => {}, pendingConnections: [], connectTenant: async () => {},
  disableAndSwitch: async () => ({}), dismissPending: () => {},
  twofa: { required: false, verified: true }, needs2fa: false, passwordPolicy: null,
  startTwoFactor: async () => ({}), verifyTwoFactor: async () => {},
  signOut: async () => {}, refresh: async () => {},
})

export const useAuth = () => useContext(Ctx)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true)
  const [session, setSession] = useState<any | null>(null)
  const [user, setUser] = useState<AppUser | null>(null)
  const [permissions, setPermissions] = useState<Permissions>({})
  const [provisioned, setProvisioned] = useState(false)
  const [active, setActive] = useState(false)
  const [tenant, setTenant] = useState<TenantInfo | null>(null)
  const [carriers, setCarriers] = useState<CarrierRef[]>([])
  const [tenants, setTenants] = useState<TenantMembership[]>([])
  const [activeOrg, setActiveOrgState] = useState<string | null>(null)
  const [needsTenantChoice, setNeedsTenantChoice] = useState(false)
  const [pendingConnections, setPendingConnections] = useState<PendingConnection[]>([])
  const [dismissed, setDismissed] = useState<string[]>([])
  const [twofa, setTwofa] = useState<TwoFactorState>({ required: false, verified: true })
  const [passwordPolicy, setPasswordPolicy] = useState<PasswordPolicy | null>(null)

  const resetProfile = useCallback(() => {
    setUser(null); setPermissions({}); setProvisioned(false); setActive(false)
    setTenant(null); setCarriers([]); setSessionOrgId(null)
    setTwofa({ required: false, verified: true }); setPasswordPolicy(null)
  }, [])

  // Fetch /core/me for the given active tenant and populate the profile state.
  const loadMe = useCallback(async (token: string, orgId: string | null) => {
    try {
      const headers: Record<string, string> = { Authorization: `Bearer ${token}` }
      if (orgId) headers['x-active-org'] = orgId
      const res = await fetch(`${API_URL}/api/v1/core/me`, { headers })
      if (!res.ok) throw new Error(String(res.status))
      const d = await res.json()
      setUser(d.user || null)
      setSessionOrgId(d.user?.org_id)
      setPermissions(d.permissions || {})
      setProvisioned(!!d.provisioned)
      setActive(d.active !== false)
      setTenant(d.tenant || null)
      setCarriers(d.carriers || [])
      setTwofa(d.twofa || { required: false, verified: true })
      setPasswordPolicy(d.password_policy || null)
    } catch {
      resetProfile()
    }
  }, [resetProfile])

  // Load the login's tenant memberships, resolve the active tenant (persisted choice → single →
  // picker), then load the profile for that tenant. A single-membership login (the norm, and every
  // mig-088 aliased login) never sees a picker; a login belonging to >1 tenant with no valid saved
  // choice pauses at needsTenantChoice until it picks.
  const loadProfile = useCallback(async (sess: any | null) => {
    if (!sess?.access_token) {
      resetProfile(); setTenants([]); setActiveOrgState(null); setNeedsTenantChoice(false)
      return
    }
    const token = sess.access_token
    let mems: TenantMembership[] = []
    try {
      const res = await fetch(`${API_URL}/api/v1/core/my-tenants`, { headers: { Authorization: `Bearer ${token}` } })
      if (res.ok) mems = (await res.json()).tenants || []
    } catch { mems = [] }
    setTenants(mems)

    // Any pending account-link invites addressed to this login's email (platform-core-11). Almost
    // always empty; when present, the login page shows a connect/disable prompt before entering the app.
    try {
      const pr = await fetch(`${API_URL}/api/v1/core/pending-connections`, { headers: { Authorization: `Bearer ${token}` } })
      setPendingConnections(pr.ok ? ((await pr.json()).pending || []) : [])
    } catch { setPendingConnections([]) }

    if (mems.length > 1) {
      const stored = getActiveOrg()
      const valid = mems.find(t => t.org_id === stored)
      if (!valid) {
        // Belongs to several tenants but hasn't chosen one → show the picker; don't load a profile yet.
        setNeedsTenantChoice(true); setActiveOrgState(null); resetProfile()
        return
      }
      setNeedsTenantChoice(false); setActiveOrg(valid.org_id); setActiveOrgState(valid.org_id)
      await loadMe(token, valid.org_id)
      return
    }

    // 0 or 1 membership → no picker. 1 ⇒ that org; 0 ⇒ unprovisioned (let /core/me report it).
    setNeedsTenantChoice(false)
    const only = mems.length === 1 ? mems[0].org_id : null
    setActiveOrg(only); setActiveOrgState(only)
    await loadMe(token, only)
  }, [loadMe, resetProfile])

  // Switch the acting tenant (top-bar switcher / post-login picker). Persists the choice, then reloads
  // the profile for the new tenant. The server re-verifies membership from the x-active-org header, so a
  // stale/forged choice can never grant access to a tenant this login doesn't belong to.
  const switchTenant = useCallback(async (orgId: string) => {
    if (!session?.access_token) return
    setActiveOrg(orgId); setActiveOrgState(orgId); setNeedsTenantChoice(false)
    await loadMe(session.access_token, orgId)
  }, [session, loadMe])

  // Accept a pending invite: attach the inviting tenant onto THIS login. Reloads the profile so the
  // new tenant appears in the top-bar switcher. The access code (from the admin) is the consent proof.
  const connectTenant = useCallback(async (orgId: string, code: string) => {
    if (!session?.access_token) return
    const res = await fetch(`${API_URL}/api/v1/core/connect-tenant`, {
      method: 'POST', headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ org_id: orgId, code }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Could not connect this company')
    setPendingConnections(p => p.filter(x => x.org_id !== orgId))
    await loadProfile(session)   // re-fetch memberships → the switcher now shows both tenants
  }, [session, loadProfile])

  // Disable the old login and take a fresh one for the inviting tenant. Returns the new credentials
  // + policy text so the caller can show them; the current session is banned, so the caller signs out.
  const disableAndSwitch = useCallback(async (orgId: string, code: string) => {
    if (!session?.access_token) throw new Error('not signed in')
    const res = await fetch(`${API_URL}/api/v1/core/disable-and-switch`, {
      method: 'POST', headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ org_id: orgId, code }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Could not switch logins')
    return res.json()
  }, [session])

  const dismissPending = useCallback((orgId: string) => {
    setDismissed(d => (d.includes(orgId) ? d : [...d, orgId]))
  }, [])

  // Request a 2FA OTP over a channel (email default). Returns {sent, channel, masked_dest, message}.
  const startTwoFactor = useCallback(async (channel?: string) => {
    if (!session?.access_token) throw new Error('not signed in')
    const headers: Record<string, string> = {
      Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' }
    if (activeOrg) headers['x-active-org'] = activeOrg
    const res = await fetch(`${API_URL}/api/v1/core/me/2fa/start`, {
      method: 'POST', headers, body: JSON.stringify({ channel }) })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Could not send a code')
    return res.json()
  }, [session, activeOrg])

  // Verify a 2FA code → store the signed marker (client.ts sends it as x-2fa-token) → reload profile so
  // twofa.verified flips true and the app opens.
  const verifyTwoFactor = useCallback(async (code: string, remember?: boolean) => {
    if (!session?.access_token) throw new Error('not signed in')
    const headers: Record<string, string> = {
      Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' }
    if (activeOrg) headers['x-active-org'] = activeOrg
    const res = await fetch(`${API_URL}/api/v1/core/me/2fa/verify`, {
      method: 'POST', headers, body: JSON.stringify({ code, remember: !!remember }) })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Invalid or expired code.')
    const d = await res.json()
    set2faToken(d.token || null)
    await loadMe(session.access_token, activeOrg)   // re-fetch /core/me → twofa.verified now true
  }, [session, activeOrg, loadMe])

  useEffect(() => {
    let mounted = true
    supabase.auth.getSession().then(async ({ data }) => {
      if (!mounted) return
      setSession(data.session)
      await loadProfile(data.session)
      setLoading(false)
    })
    const { data: sub } = supabase.auth.onAuthStateChange(async (_e, sess) => {
      if (!mounted) return
      setSession(sess)
      await loadProfile(sess)
      setLoading(false)
    })
    return () => { mounted = false; sub.subscription.unsubscribe() }
  }, [loadProfile])

  const signOut = useCallback(async () => {
    await supabase.auth.signOut()
    setActiveOrg(null); set2faToken(null)
    resetProfile(); setSession(null); setTenants([]); setActiveOrgState(null); setNeedsTenantChoice(false)
    setPendingConnections([]); setDismissed([])
  }, [resetProfile])

  const refresh = useCallback(async () => {
    const { data } = await supabase.auth.getSession()
    setSession(data.session)
    await loadProfile(data.session)
  }, [loadProfile])

  const visiblePending = pendingConnections.filter(p => !dismissed.includes(p.org_id))
  const needs2fa = !!twofa.required && !twofa.verified

  return (
    <Ctx.Provider value={{
      loading, session, user, permissions, carriers, provisioned, active, tenant,
      token: session?.access_token || null, tenants, activeOrg, needsTenantChoice,
      switchTenant, pendingConnections: visiblePending, connectTenant, disableAndSwitch,
      dismissPending, twofa, needs2fa, passwordPolicy, startTwoFactor, verifyTwoFactor,
      signOut, refresh,
    }}>
      {children}
    </Ctx.Provider>
  )
}
