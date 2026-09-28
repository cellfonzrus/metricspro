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

/** The customer-facing site when nothing is configured (robots, metadata, the canonical host). */
export const DEFAULT_SITE_URL = 'https://metricspro.tech'
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

/** The rewrites next.config.ts installs: every BACKEND_PATH_PREFIXES entry → the backend origin. */
export function apiRewrites(env: ApiEnv = API_ENV): { source: string; destination: string }[] {
  const origin = backendOrigin(env)
  // `/:path*` is zero-or-more segments, so '/health' itself matches as well as anything under it.
  return BACKEND_PATH_PREFIXES.map(prefix => ({
    source: `${prefix}/:path*`, destination: `${origin}${prefix}/:path*`,
  }))
}
