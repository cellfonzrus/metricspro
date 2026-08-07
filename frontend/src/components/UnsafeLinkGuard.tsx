'use client'
// ── UNSAFE-LINK GUARD — the app-wide net under H6 (stored XSS via `javascript:` URLs) ────────────
//
// Mounted ONCE in the root layout, so it covers every route: the platform shell, `/portal`,
// `/employee` and the public `/onboard/[token]` page. It exists because the tenant-writable URL
// fields that H6 named (`doc_url`, `template_url`, `sample_url`, `portal_url`, evidence photo URLs)
// are rendered by pages inside OTHER module agents' trees, which this agent may not edit
// (AGENT_CONTRACT §1). This one shared control closes them without a single cross-tree edit, and
// each owning agent still adopts `safeHref()` at its own render sites in its own wave — exactly the
// RULE THREE / RULE FOUR retrofit pattern.
//
// ⚠️ DELIBERATELY A DENY-LIST, and this is NOT the usual mistake.
// At a render site we use the strict ALLOW-list (`safeHref`), because we know what that field is for.
// Here we do not: this listener sees EVERY anchor in the product, including ones that legitimately
// use schemes an allow-list would kill — `blob:` (generated downloads) and `data:text/csv` (the
// commcalc CSV exports, which work by clicking a synthesised <a>). Blocking those would break real
// exports app-wide, which is precisely the regression class this package is meant to avoid. So the
// net blocks ONLY what is never legitimate:
//     javascript:   vbscript:   data:text/html   data:application/xhtml+xml
// Anything else passes through completely untouched — this handler cannot change where a real link
// goes, it can only cancel a script URL.
//
// Capture phase + `stopImmediatePropagation` so it wins over any page-level handler, and `passive`
// is deliberately NOT set (we must be able to `preventDefault`).
import { useEffect } from 'react'
import { canonicalizeForScheme } from '@/lib/safe-url'

const BLOCKED_SCHEMES = ['javascript:', 'vbscript:', 'livescript:', 'mocha:']
const BLOCKED_DATA = /^data:\s*(text\/html|application\/xhtml\+xml|image\/svg\+xml|text\/xml|application\/xml)/i

export function isBlockedNavigation(raw: unknown): boolean {
  if (raw == null) return false
  const probe = canonicalizeForScheme(String(raw)).trim().toLowerCase()
  if (!probe) return false
  if (BLOCKED_SCHEMES.some(s => probe.startsWith(s))) return true
  return BLOCKED_DATA.test(probe)
}

export default function UnsafeLinkGuard() {
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const t = e.target as Element | null
      if (!t || typeof (t as any).closest !== 'function') return
      const a = t.closest('a[href]') as HTMLAnchorElement | null
      if (!a) return
      // getAttribute, NOT `.href` — the DOM property already resolves/normalises and would hide the
      // raw scheme on some inputs.
      if (!isBlockedNavigation(a.getAttribute('href'))) return
      e.preventDefault()
      e.stopImmediatePropagation()
      // eslint-disable-next-line no-console
      console.warn('[MetricsPro] Blocked an unsafe link target. If you expected a page here, the ' +
        'configured link is invalid — fix it in the admin screen that owns it.')
    }
    document.addEventListener('click', onClick, true)
    return () => document.removeEventListener('click', onClick, true)
  }, [])
  return null
}
