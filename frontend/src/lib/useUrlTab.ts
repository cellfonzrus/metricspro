'use client'
// ── ONE HOME — a page's tab lives in the URL (`?tab=<key>`) (owner 2026-10-02, index §19.40) ─────────────
// Owner: *"make Employees & Pay its own menu item"*. Employees & Pay is a TAB of /hr, and /hr kept its tab in
// `useState` only — so no menu entry, link or notice could open it. Four other pages (Notify, Helpdesk
// Settings, Training, Payables) WERE deep-linkable, each with its own copy of "read ?tab= from
// window.location once, on mount". That copy has a defect of its own: a link to `?tab=x` followed while the
// page is already open is a same-route navigation, the page does not remount, and the tab never changes —
// the menu entry would have looked dead whenever the reader was already on /hr. So there is ONE reader:
//
//   const [tab, setTab] = useUrlTab(HR_TABS, 'comp')
//
// • the tab is DERIVED from `useSearchParams` (reactive): the sidebar, a ScreenLink, an attention notice or
//   Back/Forward all switch it, with or without a remount;
// • an unknown or missing key is the page's default — a stale link degrades to the page, never to a blank;
// • `setTab` writes the URL through the native History API (which Next's router integrates with — see
//   node_modules/next/dist/docs/01-app/01-getting-started/04-linking-and-navigating.md, "Native History
//   API"), so a tab is bookmarkable, shareable and on the Back stack; the default tab drops the parameter so
//   the bare page URL stays canonical;
// • the keys are a literal array the page declares — harness_nav_deep_link_lock.py reads it to prove every
//   `?tab=` link in NAV, ScreenLink, a hub tile or a backend notice names a tab its page really has.
//
// `useSearchParams` makes a prerendered page render client-side up to the nearest <Suspense>; every page
// that calls this wraps its body in one (the lock checks), so the build never fails on a missing boundary.
// A page that guards unsaved edits checks its guard BEFORE calling setTab (the HR page's confirmDiscard).
import { useCallback } from 'react'
import { usePathname, useSearchParams } from 'next/navigation'
// The pure meaning of `?tab=` (parse + build) lives in lib/urlTab.ts so the DB-free proof drives it under Node.
import { TAB_PARAM, parseTab, tabHref } from '@/lib/urlTab'

export function useUrlTab<T extends string>(keys: readonly T[], fallback: T): [T, (next: T) => void] {
  const pathname = usePathname()
  const params = useSearchParams()
  const tab = parseTab(params.get(TAB_PARAM), keys, fallback)
  const setTab = useCallback((next: T) => {
    if (next === tab) return
    window.history.pushState(null, '', tabHref(pathname, params.toString(), next, fallback))
  }, [tab, pathname, params, fallback])
  return [tab, setTab]
}
