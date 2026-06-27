'use client'
import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { supabase } from './client'
import type { Permissions } from './rbac'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export type AppUser = {
  id: string; auth_id: string; email: string; full_name: string | null
  role: string | null; role_display?: string | null
  market: string | null; store_code: string | null; store_codes: string[] | null
  employee_id: string | null; is_active: boolean; must_reset_password: boolean
  super_admin?: boolean; org_id?: string | null
}

type AuthState = {
  loading: boolean
  session: any | null
  user: AppUser | null
  permissions: Permissions
  provisioned: boolean
  active: boolean
  token: string | null
  signOut: () => Promise<void>
  refresh: () => Promise<void>
}

const Ctx = createContext<AuthState>({
  loading: true, session: null, user: null, permissions: {}, provisioned: false,
  active: false, token: null, signOut: async () => {}, refresh: async () => {},
})

export const useAuth = () => useContext(Ctx)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true)
  const [session, setSession] = useState<any | null>(null)
  const [user, setUser] = useState<AppUser | null>(null)
  const [permissions, setPermissions] = useState<Permissions>({})
  const [provisioned, setProvisioned] = useState(false)
  const [active, setActive] = useState(false)

  const loadProfile = useCallback(async (sess: any | null) => {
    if (!sess?.access_token) {
      setUser(null); setPermissions({}); setProvisioned(false); setActive(false)
      return
    }
    try {
      const res = await fetch(`${API_URL}/api/v1/core/me`, {
        headers: { Authorization: `Bearer ${sess.access_token}` },
      })
      if (!res.ok) throw new Error(String(res.status))
      const d = await res.json()
      setUser(d.user || null)
      setPermissions(d.permissions || {})
      setProvisioned(!!d.provisioned)
      setActive(d.active !== false)
    } catch {
      setUser(null); setPermissions({}); setProvisioned(false); setActive(false)
    }
  }, [])

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
    setSession(null); setUser(null); setPermissions({}); setProvisioned(false); setActive(false)
  }, [])

  const refresh = useCallback(async () => {
    const { data } = await supabase.auth.getSession()
    setSession(data.session)
    await loadProfile(data.session)
  }, [loadProfile])

  return (
    <Ctx.Provider value={{
      loading, session, user, permissions, provisioned, active,
      token: session?.access_token || null, signOut, refresh,
    }}>
      {children}
    </Ctx.Provider>
  )
}
