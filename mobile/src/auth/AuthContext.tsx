import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'

import { supabase } from '@/api/supabase'
import { onSessionInvalid } from '@/api/client'
import { getMe, getMyTenants, type MePayload, type TenantMembership } from '@/api/core'
import {
  clearAuxAuth,
  load2faToken,
  loadActiveOrg,
  setActiveOrg as persistActiveOrg,
} from './tokens'
import { loadQueue, flushQueue } from '@/offline/queue'

// ── Auth provider ────────────────────────────────────────────────────────────────────────────────
// Single source of truth for "who is signed in and what can they do". Wraps Supabase auth, resolves
// /core/me (profile + permissions for the active tenant), and holds the multi-tenant list. The module
// registry and route guard read `me.permissions` from here.
type Status = 'loading' | 'signedOut' | 'signedIn'

type AuthValue = {
  status: Status
  me: MePayload | null
  tenants: TenantMembership[]
  activeOrg: string | null
  signIn: (email: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  refreshMe: () => Promise<void>
  switchTenant: (orgId: string) => Promise<void>
  hasPermission: (key: string) => boolean
}

const AuthContext = createContext<AuthValue | null>(null)

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within <AuthProvider>')
  return ctx
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>('loading')
  const [me, setMe] = useState<MePayload | null>(null)
  const [tenants, setTenants] = useState<TenantMembership[]>([])
  const [activeOrg, setActiveOrgState] = useState<string | null>(null)
  const bootstrapped = useRef(false)

  const refreshMe = useCallback(async () => {
    try {
      const payload = await getMe()
      setMe(payload)
    } catch {
      // A failure here (network) leaves the previous me in place; the route guard handles auth loss.
    }
  }, [])

  const loadTenants = useCallback(async () => {
    try {
      const { tenants: t } = await getMyTenants()
      setTenants(t ?? [])
    } catch {
      setTenants([]) // older backend / single tenant
    }
  }, [])

  const onSignedIn = useCallback(async () => {
    await Promise.all([loadActiveOrg().then(setActiveOrgState), load2faToken()])
    await Promise.all([refreshMe(), loadTenants()])
    setStatus('signedIn')
    void flushQueue() // send anything queued while offline / signed out
  }, [refreshMe, loadTenants])

  const signIn = useCallback(
    async (email: string, password: string) => {
      const { error } = await supabase.auth.signInWithPassword({ email: email.trim(), password })
      if (error) throw new Error(error.message)
      // onAuthStateChange fires SIGNED_IN → onSignedIn(); nothing else needed here.
    },
    [],
  )

  const signOut = useCallback(async () => {
    await supabase.auth.signOut().catch(() => {})
    await clearAuxAuth()
    setMe(null)
    setTenants([])
    setActiveOrgState(null)
    setStatus('signedOut')
  }, [])

  const switchTenant = useCallback(
    async (orgId: string) => {
      await persistActiveOrg(orgId)
      setActiveOrgState(orgId)
      await refreshMe()
    },
    [refreshMe],
  )

  const hasPermission = useCallback(
    (key: string) => {
      const perms = me?.permissions ?? {}
      // scope 'all' is org-wide admin and implies fine-grained keys (mirrors the backend convention).
      if ((perms as any).scope === 'all') return true
      return Boolean((perms as any)[key])
    },
    [me],
  )

  // Bootstrap: load offline queue, then subscribe to Supabase auth state.
  useEffect(() => {
    if (bootstrapped.current) return
    bootstrapped.current = true

    void loadQueue()

    const { data: sub } = supabase.auth.onAuthStateChange((event, session) => {
      if (session) {
        void onSignedIn()
      } else if (event === 'SIGNED_OUT') {
        setMe(null)
        setStatus('signedOut')
      }
    })

    // Initial session check (onAuthStateChange also fires INITIAL_SESSION on newer supabase-js, but
    // we resolve it explicitly so `status` never sticks on 'loading' if no event arrives).
    supabase.auth.getSession().then(({ data }) => {
      if (data.session) void onSignedIn()
      else setStatus('signedOut')
    })

    // A live token rejected by the backend → force a clean sign-out.
    const unsub = onSessionInvalid(() => {
      void signOut()
    })

    return () => {
      sub.subscription.unsubscribe()
      unsub()
    }
  }, [onSignedIn, signOut])

  const value = useMemo<AuthValue>(
    () => ({ status, me, tenants, activeOrg, signIn, signOut, refreshMe, switchTenant, hasPermission }),
    [status, me, tenants, activeOrg, signIn, signOut, refreshMe, switchTenant, hasPermission],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
