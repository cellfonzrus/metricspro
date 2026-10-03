// ONE HOME — where the backend is, and which address a caller uses to reach it (owner 2026-09-28,
// index §40). Owner: "we should use metricspro.tech/xxxxx as customer facing and mask all urls".
//
// THE DEFECT THIS REPLACES. Fifteen files each read `process.env.NEXT_PUBLIC_API_URL` themselves and
// built `${API_URL}/api/v1/...`. So the browser called the backend host DIRECTLY — its name sat in the
// network tab, in the JS bundle, and in the CSP — and "where is the API" had fifteen answers that could
// each drift. This file is now the ONLY reader of the backend-location env vars; every caller asks it.
// `backend/harness_one_domain_lock.py` fails the build if any other frontend file reads them again.
//
// THE MODEL — two route classes, decided HERE from the path, never at the call site:
//
//   proxy  (the default) — the browser calls its OWN origin (`/api/v1/...`, relative). next.config.ts
//          rewrites those paths to the backend server-side (see `apiRewrites`), so the browser only
//          ever talks to the site's own host.
//   direct — calls that must NOT ride the platform proxy: multipart uploads (sales workbooks run to
//          tens of MB) and the handful of synchronous endpoints that can run past the proxy's 120 s
//          response limit (Chromium portal logins, full ledger rebuilds). They go to
//          `NEXT_PUBLIC_API_DIRECT_ORIGIN` — meant to be a subdomain of the customer domain
//          (e.g. https://api.metricspro.tech, a CNAME to the backend host). Until that is configured
//          they fall back to NEXT_PUBLIC_API_URL (the pre-2026-09-28 behaviour), so nothing breaks
//          with no dashboard change — but the backend host stays visible for THOSE calls until then.
//          On a Vercel PREVIEW with no direct origin configured, direct calls go same-origin too
//          (a preview is not an allowed CORS origin of the production API, by design).
//
// SERVER-SIDE code (no `window`) calls the backend by its real origin: `BACKEND_ORIGIN` (server-only,
// never inlined into the bundle), falling back to NEXT_PUBLIC_API_URL so an existing deploy keeps
// working with no dashboard change.
//
// Pure module: no imports, erasable TypeScript only (next.config.ts and the node proof load it as is).

export type RouteClass = 'proxy' | 'direct'

export interface ApiEnv {
  NEXT_PUBLIC_API_URL?: string
  NEXT_PUBLIC_API_DIRECT_ORIGIN?: string
  NEXT_PUBLIC_SITE_URL?: string
  NEXT_PUBLIC_VERCEL_ENV?: string
  BACKEND_ORIGIN?: string
}

// Each read is a LITERAL `process.env.NAME` so Next inlines the NEXT_PUBLIC_ ones at build time.
// BACKEND_ORIGIN has no NEXT_PUBLIC_ prefix, so it is undefined in the browser bundle by construction.
export const API_ENV: ApiEnv = {
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
  NEXT_PUBLIC_API_DIRECT_ORIGIN: process.env.NEXT_PUBLIC_API_DIRECT_ORIGIN,
  NEXT_PUBLIC_SITE_URL: process.env.NEXT_PUBLIC_SITE_URL,
  NEXT_PUBLIC_VERCEL_ENV: process.env.NEXT_PUBLIC_VERCEL_ENV,
  BACKEND_ORIGIN: process.env.BACKEND_ORIGIN,
}

/** The customer-facing app site when nothing is configured (robots, metadata) — app.metricspro.tech, owner
 *  2026-09-28 (the apex serves the marketing site). The canonical-host redirect never uses this default. */
export const DEFAULT_SITE_URL = 'https://app.metricspro.tech'
/** Local development backend when nothing is configured. */
export const LOCAL_BACKEND = 'http://localhost:8000'

/** Every backend path prefix the site proxies. next.config.ts builds its rewrites from THIS list; a
 *  backend path the browser needs that is not here would 404 on the site's own host. */
export const BACKEND_PATH_PREFIXES: readonly string[] = ['/api/v1', '/health']

/** DIRECT routes — regex sources matched against the path WITHOUT its query string. Kept as plain
 *  strings (JS- and Python-compatible) because the lock reads them to prove that every backend
 *  endpoint that launches a browser (`require_browser_service()`) is listed here. Multipart uploads
 *  are direct by construction (`apiUpload` asks for 'direct'); they are not listed. */
export const DIRECT_ROUTES: readonly string[] = [
  // Synchronous full rebuild of the per-IMEI payable ledger ("may run long on a full rebuild").
  '^/api/v1/payables/rebuild$',
  // Chromium-driven portal work: proxied by the API to the sweeps worker with a 180 s budget.
  '^/api/v1/commcalc/epay/sweep/(run-now|discover-reports|run-due)$',
  '^/api/v1/commcalc/data-sources/sweep/run-due$',
  '^/api/v1/commcalc/data-sources/[^/]+/(run|login/start|login/verify|live-login/[^/]+)$',
  '^/api/v1/supply/vendors/[^/]+/catalog/read$',
  '^/api/v1/supply/orders/[^/]+/(open-session|capture|submit)$',
]

const DIRECT_RES = DIRECT_ROUTES.map(s => new RegExp(s))

function clean(v: string | undefined | null): string {
  return String(v || '').trim().replace(/\/+$/, '')
}

/** The customer-facing site origin. */
export function siteUrl(env: ApiEnv = API_ENV): string {
  return clean(env.NEXT_PUBLIC_SITE_URL) || DEFAULT_SITE_URL
}

/** The backend's real origin — SERVER-SIDE ONLY (rewrites, server code). Never rendered. */
export function backendOrigin(env: ApiEnv = API_ENV): string {
  return clean(env.BACKEND_ORIGIN) || clean(env.NEXT_PUBLIC_API_URL) || LOCAL_BACKEND
}

/** Where a BROWSER sends a direct-class call. '' means same-origin (through the proxy). */
export function directOrigin(env: ApiEnv = API_ENV): string {
  const explicit = clean(env.NEXT_PUBLIC_API_DIRECT_ORIGIN)
  if (explicit) return explicit
  if (env.NEXT_PUBLIC_VERCEL_ENV === 'preview') return ''
  return clean(env.NEXT_PUBLIC_API_URL)
}

/** Which class a backend path belongs to (query string ignored). */
export function routeClass(path: string): RouteClass {
  const p = String(path || '').split('?')[0]
  return DIRECT_RES.some(re => re.test(p)) ? 'direct' : 'proxy'
}

/** Pure resolver behind apiUrl — `browser` says which side of the wire we are on. */
export function resolveApiUrl(path: string, env: ApiEnv, browser: boolean, cls?: RouteClass): string {
  const p = path.startsWith('/') ? path : `/${path}`
  if (!browser) return backendOrigin(env) + p
  return ((cls || routeClass(p)) === 'direct' ? directOrigin(env) : '') + p
}

/** THE way to address the backend from the frontend: `fetch(apiUrl('/api/v1/...'))`. */
export function apiUrl(path: string, cls?: RouteClass): string {
  return resolveApiUrl(path, API_ENV, typeof window !== 'undefined', cls)
}

/** An ABSOLUTE backend URL meant to leave the page — put in an export, shown to an operator to paste
 *  into another system. Never the private backend origin: the direct origin when one applies, else
 *  the site's own origin (which proxies). During server rendering the configured site stands in for
 *  the browser's origin, so nothing private is ever rendered into HTML. */
export function absoluteApiUrl(path: string, cls?: RouteClass): string {
  const p = path.startsWith('/') ? path : `/${path}`
  const browser = typeof window !== 'undefined'
  const direct = (cls || routeClass(p)) === 'direct' ? directOrigin(API_ENV) : ''
  if (direct) return direct + p
  return (browser ? window.location.origin : siteUrl(API_ENV)) + p
}

/** A host that only exists on the build machine — a backend origin pointing at one of these can never
 *  be reached from a browser. Used by productionConfigProblems(); not a security boundary. */
function isLocalHost(origin: string): boolean {
  const host = String(origin || '').replace(/^[a-z]+:\/\//i, '').split('/')[0].split(':')[0].toLowerCase()
  if (!host) return true
  if (host === 'localhost' || host.endsWith('.localhost') || host === '0.0.0.0' || host === '::1') return true
  if (/^127\./.test(host) || /^10\./.test(host) || /^192\.168\./.test(host)) return true
  if (/^172\.(1[6-9]|2[0-9]|3[01])\./.test(host)) return true
  return false
}

const BARE_HTTPS_ORIGIN = /^https:\/\/[A-Za-z0-9.-]+(?::\d{1,5})?$/

/** WHY THIS EXISTS (owner incident 2026-10-03, index §40.10). The move to app.metricspro.tech took the
 *  app off the air, and every fallback in this file is written to keep a build WORKING when a variable is
 *  missing — which means a production build with no backend address configured does not fail, it silently
 *  proxies every `/api/v1` and `/health` request to `http://localhost:8000` and ships. The deploy is green,
 *  the pages render, and nothing in the app can reach the API. Same shape for a malformed origin: the
 *  canonical-host redirect quietly does not install, or direct-class uploads quietly go nowhere.
 *
 *  THE CLASS: a development fallback must never be reachable by a PRODUCTION build. The fallbacks stay —
 *  they are right for `next dev` and for a preview — but a production build asserts the facts it cannot
 *  work without, and FAILS instead of going dark.
 *
 *  Pure: `next.config.ts` calls it at build time and throws on a non-empty result; the proof calls it
 *  directly, and `backend/harness_one_domain_lock.py` fails the build if the config stops calling it. */
export function productionConfigProblems(env: ApiEnv = API_ENV, production = false): string[] {
  if (!production) return []
  const problems: string[] = []

  const backend = backendOrigin(env)
  if (isLocalHost(backend)) {
    problems.push(
      `the backend origin resolves to ${backend || '(empty)'}, which no browser can reach: every ` +
      `${BACKEND_PATH_PREFIXES.join(' and ')} request is proxied there, so the whole app would load and ` +
      `then fail every API call. Set BACKEND_ORIGIN (preferred, server-only) or NEXT_PUBLIC_API_URL to ` +
      `the backend's https origin in the production environment and redeploy.`)
  }

  const site = clean(env.NEXT_PUBLIC_SITE_URL)
  if (site && !BARE_HTTPS_ORIGIN.test(site)) {
    problems.push(
      `NEXT_PUBLIC_SITE_URL is "${site}", which is not a bare https://host[:port]. The ` +
      `canonical-host redirect installs only for a bare origin, so it would silently not install and the ` +
      `site metadata would carry a wrong base.`)
  }

  const direct = clean(env.NEXT_PUBLIC_API_DIRECT_ORIGIN)
  if (direct && !BARE_HTTPS_ORIGIN.test(direct)) {
    problems.push(
      `NEXT_PUBLIC_API_DIRECT_ORIGIN is "${direct}", which is not a bare https://host[:port]. ` +
      `Direct-class calls (multipart uploads and the long synchronous endpoints) are built from it, so ` +
      `they would be sent to an address the browser cannot resolve.`)
  }

  return problems
}

/** The rewrites next.config.ts installs: every BACKEND_PATH_PREFIXES entry → the backend origin. */
export function apiRewrites(env: ApiEnv = API_ENV): { source: string; destination: string }[] {
  const origin = backendOrigin(env)
  // TWO rules per prefix: the bare prefix EXACTLY, then `/:path+` (one-or-more segments) for anything
  // under it. Never `/:path*`: with zero segments the platform's edge proxy forwards `${prefix}/` with a
  // trailing slash (live 2026-09-28: the site's /health reached the backend as /health/, which is not a
  // public route → 401, so the portal's API probe read "server down"). Next's local matcher drops the
  // empty segment, which is why only the live proxy showed it. Locked by prove_one_domain.mjs (E).
  return BACKEND_PATH_PREFIXES.flatMap(prefix => [
    { source: prefix, destination: `${origin}${prefix}` },
    { source: `${prefix}/:path+`, destination: `${origin}${prefix}/:path+` },
  ])
}
