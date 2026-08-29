'use client'
// Help-text visibility — the production "clean look" gate (owner 2026-08-28: "gate the comments on top of
// every menu … toggled off, but give access to roles which are approved to see the comments").
//
// Two independent conditions must BOTH be true for a page's explanatory text to render:
//   1. canSee  — the signed-in user holds the MASTER ADMIN role (owner 2026-08-29: a single named
//                all-access role is the one approved to see the comments). Everyone else — including
//                ordinary admins/owners — never sees the comments, and never even sees the toggle.
//   2. enabled — a Master admin has turned help ON (persisted per browser; default OFF, so the app is
//                clean out of the box even for a Master admin until they opt in).
// PageIntro reads `show = canSee && enabled`. The top-bar toggle (rendered only when canSee) flips `enabled`.
import { createContext, useContext, useEffect, useState, useCallback, ReactNode } from 'react'
import { useAuth } from '@/lib/auth-context'
import { isMasterAdminRole } from '@/lib/rbac'

const LS_KEY = 'mp.helpText'

type HelpCtx = { canSee: boolean; enabled: boolean; show: boolean; toggle: () => void; setEnabled: (v: boolean) => void }
const Ctx = createContext<HelpCtx>({ canSee: false, enabled: false, show: false, toggle: () => {}, setEnabled: () => {} })

export function HelpProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const canSee = isMasterAdminRole((user as any)?.role, (user as any)?.role_display)
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
  // Mirror `show` onto <html data-help> so a single global CSS rule gates EVERY page's intro banner (the
  // legacy inline `.pg-note` paragraphs the sweep tagged, plus PageIntro) with no per-page JS. Default is
  // hidden (no attribute / 'off'); only an approved user who turned help on flips it to 'on'.
  useEffect(() => {
    try { document.documentElement.setAttribute('data-help', show ? 'on' : 'off') } catch { /* SSR */ }
  }, [show])

  return <Ctx.Provider value={{ canSee, enabled, show, toggle, setEnabled }}>{children}</Ctx.Provider>
}

export const useHelp = () => useContext(Ctx)
