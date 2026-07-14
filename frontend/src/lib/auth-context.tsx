'use client'
import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { supabase, setSessionOrgId, getActiveOrg, setActiveOrg } from './client'
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
  signOut: () => Promise<void>
  refresh: () => Promise<void>
}

const Ctx = createContext<AuthState>({
  loading: true, session: null, user: null, permissions: {}, carriers: [], provisioned: false,
  active: false, tenant: null, token: null, tenants: [], activeOrg: null, needsTenantChoice: false,
  switchTenant: async () => {}, signOut: async () => {}, refresh: async () => {},
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

  const resetProfile = useCallback(() => {
    setUser(null); setPermissions({}); setProvisioned(false); setActive(false)
    setTenant(null); setCarriers([]); setSessionOrgId(null)
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
    setActiveOrg(null)
    resetProfile(); setSession(null); setTenants([]); setActiveOrgState(null); setNeedsTenantChoice(false)
  }, [resetProfile])

  const refresh = useCallback(async () => {
    const { data } = await supabase.auth.getSession()
    setSession(data.session)
    await loadProfile(data.session)
  }, [loadProfile])

  return (
    <Ctx.Provider value={{
      loading, session, user, permissions, carriers, provisioned, active, tenant,
      token: session?.access_token || null, tenants, activeOrg, needsTenantChoice,
      switchTenant, signOut, refresh,
    }}>
      {children}
    </Ctx.Provider>
  )
}
