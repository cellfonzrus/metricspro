'use client'
import { createContext, useContext, useEffect, useState, useCallback, useRef } from 'react'
import { createClient } from '@supabase/supabase-js'
import { supabase, setSessionOrgId, getActiveOrg, setActiveOrg, set2faToken, get2faToken,
         onSessionInvalid, clearSessionInvalid,
         onTenantChoiceRequired, clearTenantChoiceRequired,
         getImpersonation, setImpersonation, setImpersonationReauth, impersonationHeader,
         onImpersonationInvalid, clearImpersonationInvalid, type ImpersonationState } from './client'
import { setCacheIdentity } from './cache'
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
  // Free-trial state (mig 907), computed server-side in billing/trial.py::trial_view. NULL/absent for
  // any tenant carrying no plan stamp — every tenant that existed before 907, and every one created
  // while trials were switched off — which the UI reads as "say nothing about a trial".
  trial?: {
    status: 'trialing' | 'active' | 'trial_expired' | 'cancelled'
    days_left: number | null
    ends_at: string | null
    expired: boolean
  } | null
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

// Admin "view as employee" (owner directive 2026-08-06). `impersonation` is what the BROWSER holds
// (the server-minted grant + who it is for); `impersonationInfo` is what the SERVER said on /core/me,
// which is what the banner trusts — so the banner can never be suppressed by editing localStorage.
export type ImpersonationInfo = {
  active: boolean; session_id?: string; org_id?: string
  target_name?: string | null; target_email?: string | null; target_role?: string | null
  actor_email?: string | null; actor_name?: string | null
  started_at?: string | null; expires_at?: string | null; reason?: string | null
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
  // rbac_enabled when the ONE-call /core/bootstrap supplied it; null ⇒ unknown (older backend /
  // waterfall path) → the Guard keeps its own direct /core/auth-config fetch as the fallback.
  rbacEnabled: boolean | null
  // The client holds a session the BACKEND rejects (stale/invalid token → 401 "authentication
  // required" on module calls). client.ts detects it at the api() choke point; the Guard shows ONE
  // "session expired" card instead of every page erroring. See client.ts DEAD CLIENT SESSION block.
  sessionInvalid: boolean
  passwordPolicy: PasswordPolicy | null                        // active tenant policy (client-side hints)
  defaultCc: string                                            // tenant default phone country code ('+1')
  startTwoFactor: (channel?: string) => Promise<any>           // request an OTP over a channel
  verifyTwoFactor: (code: string, remember?: boolean) => Promise<void> // verify + store the marker
  // Admin "view as employee" (owner directive 2026-08-06):
  impersonation: ImpersonationState | null      // the grant this browser holds (null = not impersonating)
  impersonationInfo: ImpersonationInfo | null   // what the SERVER reported on /core/me — drives the banner
  startImpersonation: (targetAuthId: string, reason?: string) => Promise<void>
  stopImpersonation: () => Promise<void>
  /** Verify the EMPLOYEE's own password on a throwaway client → server-side proof → one unlock. */
  unlockClockPunch: (password: string) => Promise<{ valid_minutes: number }>
  signOut: () => Promise<void>
  refresh: () => Promise<void>
}

const Ctx = createContext<AuthState>({
  loading: true, session: null, user: null, permissions: {}, carriers: [], provisioned: false,
  active: false, tenant: null, token: null, tenants: [], activeOrg: null, needsTenantChoice: false,
  switchTenant: async () => {}, pendingConnections: [], connectTenant: async () => {},
  disableAndSwitch: async () => ({}), dismissPending: () => {},
  twofa: { required: false, verified: true }, needs2fa: false, rbacEnabled: null, sessionInvalid: false,
  passwordPolicy: null, defaultCc: '+1',
  startTwoFactor: async () => ({}), verifyTwoFactor: async () => {},
  impersonation: null, impersonationInfo: null,
  startImpersonation: async () => {}, stopImpersonation: async () => {},
  unlockClockPunch: async () => ({ valid_minutes: 0 }),
  signOut: async () => {}, refresh: async () => {},
})

export const useAuth = () => useContext(Ctx)

/**
 * Whether a Supabase `onAuthStateChange` event should re-arm the loading splash and re-bootstrap the
 * profile. Extracted as a PURE function so the real decision is unit-testable (frontend/prove_nav_no_reload.mjs)
 * rather than re-implemented.
 *
 * Returns FALSE — no reload, no splash — for an event that carries the SAME already-loaded identity:
 *   • TOKEN_REFRESHED — the ~hourly background token auto-refresh.
 *   • SIGNED_IN / INITIAL_SESSION re-fire — supabase-js (auth-js GoTrueClient `_recoverAndRefresh`)
 *     emits a fresh SIGNED_IN on EVERY `visibilitychange`, i.e. every time the browser tab regains
 *     focus. This is the event that made the whole app flash "Loading…" on every tab switch.
 * Returns TRUE for a genuine transition — first load (`settledUid === undefined`), a real login or
 * tenant switch (identity changes), a sign-out (identity → null), or USER_UPDATED / PASSWORD_RECOVERY.
 */
export function authEventNeedsReload(
  event: string, uid: string | null, settledUid: string | null | undefined,
): boolean {
  if (event === 'TOKEN_REFRESHED') return false
  const sameIdentity = settledUid !== undefined && uid === settledUid
  if (sameIdentity && (event === 'SIGNED_IN' || event === 'INITIAL_SESSION')) return false
  return true
}

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
  const [defaultCc, setDefaultCc] = useState('+1')
  const [rbacEnabled, setRbacEnabled] = useState<boolean | null>(null)
  const [sessionInvalid, setSessionInvalid] = useState(false)
  const [impersonation, setImpersonationState] = useState<ImpersonationState | null>(null)
  const [impersonationInfo, setImpersonationInfo] = useState<ImpersonationInfo | null>(null)

  const resetProfile = useCallback(() => {
    setUser(null); setPermissions({}); setProvisioned(false); setActive(false)
    setTenant(null); setCarriers([]); setSessionOrgId(null)
    setTwofa({ required: false, verified: true }); setPasswordPolicy(null); setDefaultCc('+1')
    setImpersonationInfo(null)
  }, [])

  // Apply a /core/me-shaped payload to the profile state. Shared by loadMe (the direct fetch) and
  // the ONE-call bootstrap path, so both populate EXACTLY the same state the same way.
  const applyMe = useCallback((d: any) => {
    if (!d) { resetProfile(); return }
    setUser(d.user || null)
    setSessionOrgId(d.user?.org_id)
    setPermissions(d.permissions || {})
    setProvisioned(!!d.provisioned)
    setActive(d.active !== false)
    setTenant(d.tenant || null)
    setCarriers(d.carriers || [])
    setTwofa(d.twofa || { required: false, verified: true })
    setPasswordPolicy(d.password_policy || null)
    setDefaultCc(d.default_cc || '+1')
    // SERVER-declared impersonation state. The banner reads this, not localStorage, so the "you are
    // someone else right now" warning cannot be hidden by tampering with the browser.
    setImpersonationInfo(d.impersonation && d.impersonation.active ? d.impersonation : null)
    setImpersonationState(getImpersonation())
  }, [resetProfile])

  // Fetch /core/me for the given active tenant and populate the profile state. These two calls are
  // deliberately raw fetches (they run before/around the api() helper's own bootstrap), so the
  // impersonation grant has to be attached explicitly here — otherwise /core/me would answer for the
  // ADMIN while every other call answered for the employee.
  const loadMe = useCallback(async (token: string, orgId: string | null) => {
    try {
      const headers: Record<string, string> = { Authorization: `Bearer ${token}`,
                                                ...impersonationHeader('/api/v1/core/me') }
      if (orgId) headers['x-active-org'] = orgId
      const res = await fetch(`${API_URL}/api/v1/core/me`, { headers })
      if (!res.ok) throw new Error(String(res.status))
      applyMe(await res.json())
    } catch {
      resetProfile()
    }
  }, [applyMe, resetProfile])

  // ONE-call login bootstrap: GET /api/v1/core/bootstrap returns auth-config + my-tenants +
  // pending-connections + me in a single round trip (the old path paid 3 sequential calls here plus
  // the Guard's auth-config = 4 blocking round trips before first paint). Returns true when it fully
  // populated the state; false ⇒ the caller runs the legacy waterfall UNCHANGED (deploy-order safety:
  // Vercel and Railway deploy independently, so an older backend without /bootstrap must keep
  // working). The >1-membership picker decision below mirrors the waterfall path byte-for-byte.
  const tryBootstrap = useCallback(async (token: string): Promise<boolean> => {
    try {
      const stored = getActiveOrg()
      const headers: Record<string, string> = { Authorization: `Bearer ${token}`,
                                                ...impersonationHeader('/api/v1/core/bootstrap') }
      if (stored) headers['x-active-org'] = stored
      const t2fa = get2faToken()
      if (t2fa) headers['x-2fa-token'] = t2fa
      const res = await fetch(`${API_URL}/api/v1/core/bootstrap`, { headers })
      if (!res.ok) return false            // 404 = older backend; any error → waterfall decides
      const d = await res.json()
      if (typeof d?.rbac_enabled === 'boolean') setRbacEnabled(d.rbac_enabled)
      const mems: TenantMembership[] = d?.tenants?.tenants || []
      setTenants(mems)
      setPendingConnections(d?.pending?.pending || [])
      if (mems.length > 1) {
        const valid = mems.find(t => t.org_id === stored)
        if (!valid) {
          // Belongs to several tenants but hasn't chosen one → show the picker; don't load a profile yet.
          setNeedsTenantChoice(true); setActiveOrgState(null); resetProfile()
          return true
        }
        setNeedsTenantChoice(false); setActiveOrg(valid.org_id); setActiveOrgState(valid.org_id)
        if (d.me) applyMe(d.me)
        else await loadMe(token, valid.org_id)   // defensive — backend sends me for a valid choice
        return true
      }
      // 0 or 1 membership → no picker. 1 ⇒ that org; 0 ⇒ unprovisioned (me reports it).
      setNeedsTenantChoice(false)
      const only = mems.length === 1 ? mems[0].org_id : null
      setActiveOrg(only); setActiveOrgState(only)
      if (d.me) applyMe(d.me)
      else await loadMe(token, only)             // defensive — backend always sends me here
      return true
    } catch { return false }
  }, [applyMe, loadMe, resetProfile])

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
    // Fast path first: ONE bootstrap round trip. On any failure (older backend, network) fall
    // through to the legacy sequential waterfall below — that path is byte-for-byte unchanged.
    if (await tryBootstrap(token)) return
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
  }, [loadMe, resetProfile, tryBootstrap])

  // Switch the acting tenant (top-bar switcher / post-login picker). Persists the choice, then reloads
  // the profile for the new tenant. The server re-verifies membership from the x-active-org header, so a
  // stale/forged choice can never grant access to a tenant this login doesn't belong to.
  const switchTenant = useCallback(async (orgId: string) => {
    if (!session?.access_token) return
    setActiveOrg(orgId); setActiveOrgState(orgId); setNeedsTenantChoice(false)
    clearTenantChoiceRequired()   // a company has now been chosen; disarm the latch
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

  // ── Admin "view as employee" (owner directive 2026-08-06) ───────────────────────────────────────
  // The browser never invents an impersonation: it asks the server, which checks the (default-deny)
  // `impersonate` permission, checks the target is in a tenant the caller administers, writes the
  // immutable audit row FIRST and only then mints the signed grant. If that audit write fails, no
  // grant comes back and nothing happens — impersonation fails closed.
  const startImpersonation = useCallback(async (targetAuthId: string, reason?: string) => {
    if (!session?.access_token) throw new Error('not signed in')
    if (getImpersonation()) throw new Error('You are already viewing the app as someone else.')
    const org = getActiveOrg()
    const res = await fetch(`${API_URL}/api/v1/core/impersonation/start${org ? `?org_id=${encodeURIComponent(org)}` : ''}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json',
                 ...(org ? { 'x-active-org': org } : {}) },
      body: JSON.stringify({ target: targetAuthId, reason: reason || '' }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Could not start the session')
    const d = await res.json()
    setImpersonation({
      grant: d.grant, session_id: d.session?.id, org_id: d.session?.org_id,
      target_name: d.session?.target_name, target_email: d.session?.target_email,
      target_role: d.session?.target_role, expires_at: d.session?.expires_at,
    })
    // Hard reload: every page, every cached lookup and the whole nav must re-resolve as the employee.
    // (A soft refresh would leave components holding the admin's data.)
    if (typeof window !== 'undefined') window.location.href = '/'
  }, [session])

  // Exit. The grant is dropped LOCALLY FIRST so the very next request is already the admin's own,
  // then the server-side session is closed (which writes the immutable ended_at). Called WITHOUT the
  // x-impersonate header — the backend refuses that whole prefix for an impersonated request, which
  // is exactly what stops a borrowed identity from managing sessions.
  const stopImpersonation = useCallback(async () => {
    const imp = getImpersonation()
    setImpersonation(null); setImpersonationState(null); setImpersonationInfo(null)
    clearImpersonationInvalid()
    try {
      if (imp?.session_id && session?.access_token) {
        await fetch(`${API_URL}/api/v1/core/impersonation/stop`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: imp.session_id, reason: 'exit' }),
        })
      }
    } catch { /* the local grant is already gone — the server sweep/expiry closes the row regardless */ }
    if (typeof window !== 'undefined') window.location.href = '/'
  }, [session])

  // THE OWNER'S CARVE-OUT. Clock in / clock out is the one thing an admin may not do on someone's
  // behalf without that person's own password. The password is verified on a THROWAWAY anon Supabase
  // client (persistSession:false + its own storageKey, exactly like the kiosk manager override) so the
  // admin's live session is untouched; the resulting token is then re-verified SERVER-SIDE and must be
  // seconds old. The server returns a single-use unlock good for ONE punch.
  const unlockClockPunch = useCallback(async (password: string) => {
    const imp = getImpersonation()
    if (!imp) throw new Error('You are not viewing the app as anyone.')
    if (!session?.access_token) throw new Error('not signed in')
    const email = (imp.target_email || '').trim()
    if (!email) throw new Error('That employee has no email on file, so their password cannot be checked.')
    const tmp = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
      { auth: { persistSession: false, autoRefreshToken: false, storageKey: 'mp-impersonation-reauth' } })
    const { data, error } = await tmp.auth.signInWithPassword({ email, password })
    if (error || !data?.session?.access_token) throw new Error(error?.message || 'That password did not work.')
    const empToken = data.session.access_token
    try {
      const res = await fetch(`${API_URL}/api/v1/core/impersonation/reauth`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: imp.session_id, token: empToken }),
      })
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Could not unlock clock in/out')
      const d = await res.json()
      setImpersonationReauth({ marker: d.reauth, expires_at: d.expires_at })
      return { valid_minutes: Number(d.valid_minutes || 5) }
    } finally {
      try { await tmp.auth.signOut() } catch { /* throwaway */ }
    }
  }, [session])

  // The server ended the session out from under us (expired, exited in another tab, employee
  // deactivated, tenant turned it off). client.ts already dropped the grant; return to the admin's
  // own account rather than leaving a banner over a stream of errors.
  const impHandledRef = useRef(false)
  useEffect(() => onImpersonationInvalid(() => {
    if (impHandledRef.current) return
    impHandledRef.current = true
    setImpersonationState(null); setImpersonationInfo(null)
    if (typeof window !== 'undefined') window.location.href = '/'
  }), [])

  // The identity (auth user id) we last ran a full profile `settle` for. `undefined` until the first
  // load; then the current uid or null (signed out). Used to tell a genuine sign-in/-out transition
  // from a same-user event re-fire (the visibilitychange SIGNED_IN / background TOKEN_REFRESHED), so
  // the latter never re-arms `loading` and never re-bootstraps the profile.
  const settledUidRef = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    let mounted = true
    // HARD GUARANTEE that the loading splash is ALWAYS released — whatever loadProfile does: resolve,
    // reject, or hang. The SIGNED_IN handler below re-arms `loading` to true to close the "no access on
    // login until refresh" race, so a loadProfile that throws (transient bootstrap/network error) or
    // stalls (slow/unreachable backend, no fetch timeout) would otherwise strand the app on an INFINITE
    // spinner ("stuck on loading"). try/finally covers throw/reject; the timeout race is a safety valve
    // so a slow/hung profile load can never spin more than ~15s — the app then renders and the profile
    // state fills in if the call lands later. This releases the gate; it never blocks the profile load.
    const settle = async (sess: any) => {
      try {
        await Promise.race([
          loadProfile(sess),
          new Promise<void>(resolve => setTimeout(resolve, 15000)),
        ])
      } catch {
        /* loadProfile's own paths are guarded; this only catches an unexpected rejection */
      } finally {
        if (mounted) setLoading(false)
      }
    }
    supabase.auth.getSession().then(({ data }) => {
      if (!mounted) return
      setSession(data.session)
      settledUidRef.current = data.session?.user?.id ?? null
      settle(data.session)
    })
    const { data: sub } = supabase.auth.onAuthStateChange((event, sess) => {
      if (!mounted) return
      // Always mirror the live session (keeps the token/session state fresh for every listener).
      setSession(sess)
      const uid = sess?.user?.id ?? null
      // DO NOT flash the whole app into the loading splash — or re-run the heavy profile bootstrap —
      // for an event that carries the SAME already-loaded identity (see authEventNeedsReload): the
      // visibilitychange SIGNED_IN/INITIAL_SESSION re-fire supabase-js emits on EVERY tab focus, and
      // the ~hourly background TOKEN_REFRESHED. Re-arming `loading` on those is what made the entire
      // app "reload" (full-screen "Loading…" splash + a full /core/bootstrap round trip) on every tab
      // switch. The profile is already loaded, so there is nothing to reload; `refresh()` remains for
      // an explicit, deliberate re-read.
      if (!authEventNeedsReload(event, uid, settledUidRef.current)) return
      // A genuine transition (real login, tenant switch to a new session, sign-out → null identity,
      // USER_UPDATED / PASSWORD_RECOVERY). RE-ARM `loading` while the new session's profile resolves —
      // closes the "no access on login until refresh" race (a fresh SIGNED_IN otherwise left
      // loading=false with provisioned not yet populated, flashing a denied screen).
      settledUidRef.current = uid
      setLoading(true)
      settle(sess)
    })
    return () => { mounted = false; sub.subscription.unsubscribe() }
  }, [loadProfile])

  // ── Client-cache identity (nav-perf 2026-08-04) ─────────────────────────────────────────────────
  // lib/cache.ts namespaces every cached lookup by (user, acting org) so a cached roster can never be
  // served across tenants OR across two users of the same tenant (span-scoped endpoints return
  // different rows per caller). The namespace is published from here — i.e. from the values the
  // BACKEND resolved on /core/me, not from localStorage — and any change (login, tenant switch,
  // sign-out → null) bumps the cache epoch and purges the store. Before this fires there is NO
  // namespace, and cache.ts then degrades to plain uncached api() calls.
  useEffect(() => {
    setCacheIdentity(user ? (user.auth_id || user.id || null) : null,
                     user ? (activeOrg || user.org_id || null) : null)
  }, [user, activeOrg])

  // ── Dead client session (auth-ux hardening 2026-08-03) ──────────────────────────────────────────
  // client.ts latches this the first time a module call 401s with the middleware's "authentication
  // required" WHILE a bearer token was attached. React to it ONCE: drop the dead Supabase session
  // (so /login doesn't bounce straight back into the app on a session it still thinks is good) and
  // flip sessionInvalid, which the platform Guard renders as ONE "session expired" card.
  //
  // NOT armed when login enforcement is explicitly OFF (rbacEnabled === false): the app is open in
  // that mode and there is nothing to sign back in to. rbacEnabled === null (unknown / older
  // backend) stays armed — a clear "please sign in again" beats a page full of red errors.
  // handledRef makes the sign-out strictly once even though this effect re-subscribes when
  // rbacEnabled resolves.
  const handledRef = useRef(false)
  useEffect(() => {
    if (rbacEnabled === false) return
    return onSessionInvalid(() => {
      if (handledRef.current) return
      handledRef.current = true
      setSessionInvalid(true)
      // Best-effort: clear the dead client session. onAuthStateChange then resets the profile.
      supabase.auth.signOut().catch(() => { /* already gone / offline — the card still shows */ })
    })
  }, [rbacEnabled])

  // AMBIGUOUS TENANT (2026-08-09). The backend now answers 409 `tenant_choice_required` rather than
  // silently serving whichever company this login joined FIRST. client.ts has already dropped the stale
  // saved choice by the time this fires; raising the picker here means the user chooses immediately
  // instead of seeing an error and having to reload. Deliberately NOT tied to the dead-session path:
  // the session is valid, and signing them out would be the wrong remedy.
  useEffect(() => {
    return onTenantChoiceRequired(() => {
      setNeedsTenantChoice(true)
      setActiveOrgState(null)
      resetProfile()
    })
  }, [resetProfile])

  const signOut = useCallback(async () => {
    await supabase.auth.signOut()
    clearSessionInvalid(); handledRef.current = false; setSessionInvalid(false)
    // Signing out ALWAYS drops any impersonation grant: it is bound to this login, so leaving it in
    // storage could only ever produce confusing 401s for the next person on this browser.
    setImpersonation(null); setImpersonationState(null); setImpersonationInfo(null)
    clearImpersonationInvalid(); impHandledRef.current = false
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
      dismissPending, twofa, needs2fa, rbacEnabled, sessionInvalid,
      passwordPolicy, defaultCc, startTwoFactor, verifyTwoFactor,
      impersonation, impersonationInfo, startImpersonation, stopImpersonation, unlockClockPunch,
      signOut, refresh,
    }}>
      {children}
    </Ctx.Provider>
  )
}
