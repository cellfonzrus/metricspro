// Site routing policy that next.config.ts installs — the canonical-host redirect and the browser's
// connect-src (owner 2026-09-28, index §40). Pure, no imports, erasable TypeScript: next.config.ts
// imports it, and `prove_one_domain.mjs` loads this very file to prove the redirect behaviour.
//
// It lives OUTSIDE src/ on purpose: it is the one place allowed to name the hosting platform's own
// hostnames. `backend/harness_one_domain_lock.py` fails the build on any such literal under src/.

export interface SiteRoutingEnv {
  /** Vercel's deployment environment, available at BUILD time: production | preview | development. */
  VERCEL_ENV?: string
  /** The customer-facing origin, when it is EXPLICITLY configured for this build. */
  NEXT_PUBLIC_SITE_URL?: string
}

export interface HostRedirect {
  source: string
  has: { type: 'host'; value: string }[]
  destination: string
  permanent: true
}

/** The platform's own hostnames (the production alias, deployment URLs, previews). Anchored by Next
 *  (`^…$`), matched against the Host header with the port removed and lower-cased. */
export const PLATFORM_HOST_RE = '(?:[a-z0-9-]+\\.)*vercel\\.app'

function clean(v: string | undefined | null): string {
  return String(v || '').trim().replace(/\/+$/, '')
}

/** A 308 from any platform hostname to the canonical site — same path, same query (Next passes the
 *  query through on a redirect). Returns [] — no redirect at all — unless ALL of:
 *    · this is a PRODUCTION build (previews keep their own URLs; PR previews must keep working;
 *      `next dev` on localhost has no VERCEL_ENV at all);
 *    · the canonical site is EXPLICITLY configured (NEXT_PUBLIC_SITE_URL) — sending every login to a
 *      host that is not yet serving this app would lock everyone out, so turning the redirect on is a
 *      deliberate act (set the variable once the domain is attached), never a code default;
 *    · the canonical site is not itself a platform hostname (that would loop). */
export function canonicalHostRedirects(env: SiteRoutingEnv): HostRedirect[] {
  if (env.VERCEL_ENV !== 'production') return []
  const site = clean(env.NEXT_PUBLIC_SITE_URL)
  if (!/^https:\/\/[^/]+$/.test(site)) return []
  const host = site.slice('https://'.length).toLowerCase()
  if (new RegExp(`^${PLATFORM_HOST_RE}$`).test(host)) return []
  return [{
    source: '/:path*',
    has: [{ type: 'host', value: PLATFORM_HOST_RE }],
    destination: `${site}/:path*`,
    permanent: true,
  }]
}

/** The CSP connect-src list. The browser's API traffic is same-origin ('self'); the only other API
 *  origin it may reach is the DIRECT-class origin, and only when there is one — so the backend's own
 *  hostname disappears from the policy the moment a customer-domain direct origin is configured. */
export function connectSrc(directOrigin: string): string {
  const parts = ["'self'", 'https://*.supabase.co', 'wss://*.supabase.co']
  const d = clean(directOrigin)
  if (d) parts.push(d)
  return `connect-src ${parts.join(' ')}`
}
