// ── SAFE HREF — H6 (stored XSS via `javascript:` URLs), 2026-08-05 security audit ────────────────
//
// WHY. Several config values are TENANT-WRITABLE and then rendered as link targets: a training
// tour's `page_href`/`start_href` (which TourRunner FOLLOWS AUTOMATICALLY via `?tour=<slug>` — no
// click needed), What's New `deep_link`, Import Health `deep_link`, portal-report `href`, HR
// onboarding `doc_url`/`template_url`/`sample_url`, connector `portal_url`, evidence photo URLs.
// The Supabase JWT **and** the 2FA marker live in `localStorage`, so one stored `javascript:` payload
// steals a full session — including a super-admin's.
//
// This is the RENDER-side layer. The write-side twin is `app/modules/core/safe_href.py`, which keeps
// the value out of the database in the first place. This layer additionally protects every row
// written BEFORE that shipped, and every field written by a module that has not adopted it yet.
//
// ALLOW-LIST, never a deny-list — `JaVaScRiPt:`, `java&#9;script:` and `\x01javascript:` all defeat a
// naive `startsWith('javascript:')`. Accepted:
//   · any reference with NO scheme — `/admin/roles`, `reports?tab=1`, `#top`, `?q=1`
//   · `http:` / `https:`
//   · `mailto:` / `tel:`
// Protocol-relative `//evil.tld/x` is rejected: no scheme, but it navigates off-site.
//
// NON-REWRITING. A safe href comes back byte-identical, so dropping `safeHref()` in front of an
// existing `<Link href={x}>` cannot change where any real link goes.

export const ALLOWED_SCHEMES = ['http', 'https', 'mailto', 'tel'] as const

const SCHEME_RE = /^([a-zA-Z][a-zA-Z0-9+.\-]*)[ \t\r\n]*:/
// Everything a browser ignores while resolving a scheme (C0 controls, space, DEL).
const IGNORED_RE = /[\u0000-\u0020\u007f]/g

function decodeEntities(s: string): string {
  return s
    .replace(/&#[xX]0*([0-9a-fA-F]{1,6});?/g, (m, h) => {
      const cp = parseInt(h, 16)
      return Number.isFinite(cp) && cp <= 0x10ffff ? String.fromCodePoint(cp) : m
    })
    .replace(/&#0*([0-9]{1,7});?/g, (m, d) => {
      const cp = parseInt(d, 10)
      return Number.isFinite(cp) && cp <= 0x10ffff ? String.fromCodePoint(cp) : m
    })
}

/** The string a BROWSER sees when deciding the scheme. Used for the DECISION only — never returned. */
export function canonicalizeForScheme(raw: string): string {
  return decodeEntities(String(raw)).replace(IGNORED_RE, '')
}

/** True when `value` is safe to use as a link target / navigation target. Empty ⇒ false. */
export function isSafeHref(value: unknown): boolean {
  if (value == null) return false
  const probe = canonicalizeForScheme(String(value)).trim()
  if (!probe) return false
  const m = SCHEME_RE.exec(probe)
  if (m) return (ALLOWED_SCHEMES as readonly string[]).includes(m[1].toLowerCase())
  // No scheme ⇒ a relative reference, which cannot be javascript:/data:/vbscript:.
  return !probe.startsWith('//')
}

/**
 * `value` unchanged when safe, else `fallback` (default `undefined`).
 * `<a href={safeHref(x)}>` with no fallback renders an anchor with NO href — visible, inert.
 */
export function safeHref<T extends string | undefined>(value: unknown, fallback?: T): string | T {
  return (isSafeHref(value) ? String(value) : fallback) as string | T
}

/** Same rule for an <img>/<iframe> style source: also permits `data:image/...` and `blob:`. */
export function isSafeMediaSrc(value: unknown): boolean {
  if (value == null) return false
  const probe = canonicalizeForScheme(String(value)).trim()
  if (!probe) return false
  const m = SCHEME_RE.exec(probe)
  if (!m) return !probe.startsWith('//')
  const scheme = m[1].toLowerCase()
  if (scheme === 'blob') return true
  // NOTE: svg is deliberately NOT allowed — the only real use here is a captured PNG chart.
  if (scheme === 'data') return /^data:image\/(png|jpe?g|gif|webp|bmp);/i.test(probe)
  return scheme === 'http' || scheme === 'https'
}
