'use client'
// Help-text visibility — the production "clean look" gate (owner 2026-08-28: "gate the comments on top of
// every menu … toggled off, but give access to roles which are approved to see the comments").
//
// Two independent conditions must BOTH be true for a page's explanatory text to render:
//   1. canSee  — the signed-in user's ROLE is approved to see help at all (admin / owner). Everyone else
//                never sees the comments, and never even sees the toggle. This is the role gate.
//   2. enabled — an approved user has turned help ON (persisted per browser; default OFF, so the app is
//                clean out of the box even for admins until they opt in).
// PageIntro reads `show = canSee && enabled`. The top-bar toggle (rendered only when canSee) flips `enabled`.
import { createContext, useContext, useEffect, useState, useCallback, ReactNode } from 'react'
import { useAuth } from '@/lib/auth-context'

// Roles approved to reveal the on-page help. Kept deliberately narrow for the production look; can later be
// made a per-tenant setting without changing any call site.
const HELP_ROLES = new Set(['admin', 'owner'])
const LS_KEY = 'mp.helpText'

type HelpCtx = { canSee: boolean; enabled: boolean; show: boolean; toggle: () => void; setEnabled: (v: boolean) => void }
const Ctx = createContext<HelpCtx>({ canSee: false, enabled: false, show: false, toggle: () => {}, setEnabled: () => {} })

export function HelpProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const canSee = HELP_ROLES.has(String((user as any)?.role || '').toLowerCase())
  const [enabled, setEnabledState] = useState(false)

  useEffect(() => {
    try { setEnabledState(localStorage.getItem(LS_KEY) === '1') } catch { /* private mode / blocked */ }
  }, [])

  const setEnabled = useCallback((v: boolean) => {
    setEnabledState(v)
    try { localStorage.setItem(LS_KEY, v ? '1' : '0') } catch { /* ignore */ }
  }, [])
  const toggle = useCallback(() => setEnabled(!enabled), [enabled, setEnabled])

  const show = canSee && enabled
  return <Ctx.Provider value={{ canSee, enabled, show, toggle, setEnabled }}>{children}</Ctx.Provider>
}

export const useHelp = () => useContext(Ctx)
