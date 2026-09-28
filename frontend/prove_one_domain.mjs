// Proof harness — ONE DOMAIN (owner 2026-09-28, index §40): "we should use metricspro.tech/xxxxx as
// customer facing and mask all urls".
//
// This harness does NOT re-implement the routing. It loads the REAL `next.config.ts` through Next's
// OWN config loader (`transpileConfig`, the one `next build` uses), validates the routes with Next's
// OWN `loadCustomRoutes` (which exits non-zero on an invalid route), and evaluates each request with
// Next's OWN matchers (`getPathMatch`, `matchHas`, `prepareDestination`, `getRedirectStatus`). If the
// config stops loading, or a route stops validating, the harness fails instead of testing a copy.
//
// SECTIONS
//   A. canonical host  — a PRODUCTION request on the platform host is 308'd to the canonical site with
//                        the same path and query; the canonical host, a tenant's custom domain and a
//                        look-alike host are left alone
//   B. previews        — VERCEL_ENV=preview installs no redirect; a PR preview keeps its own URL
//   C. localhost       — `next dev` (no VERCEL_ENV) installs no redirect
//   D. safety gates    — production with no explicit NEXT_PUBLIC_SITE_URL, or a canonical site that is
//                        itself a platform host, installs no redirect (no lock-out, no loop)
//   E. rewrites        — /api/v1/* and /health are proxied to BACKEND_ORIGIN, falling back to
//                        NEXT_PUBLIC_API_URL, then localhost:8000; a page path is not
//   F. CSP connect-src — no backend host once a direct origin on the customer domain is configured
//   G. the one home    — browser calls are same-origin; uploads and the long endpoints are DIRECT;
//                        the server uses the real origin; nothing private is rendered
//   N. negative controls — the evaluator is not vacuous
//
// Run:  node frontend/prove_one_domain.mjs     (Node >= 22.18; needs frontend/node_modules; no network)

import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const require_ = createRequire(join(HERE, 'package.json'))
const { transpileConfig } = require_('next/dist/build/next-config-ts/transpile-config.js')
const loadCustomRoutes = require_('next/dist/lib/load-custom-routes.js').default
const { getPathMatch } = require_('next/dist/shared/lib/router/utils/path-match.js')
const { matchHas, prepareDestination } = require_('next/dist/shared/lib/router/utils/prepare-destination.js')
const { getRedirectStatus, modifyRouteRegex } = require_('next/dist/lib/redirect-status.js')

let pass = 0, fail = 0
function check(name, cond, detail) {
  if (cond) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail !== undefined ? `  -> ${JSON.stringify(detail)}` : ''}`) }
}

const ENV_KEYS = ['VERCEL_ENV', 'NEXT_PUBLIC_VERCEL_ENV', 'NEXT_PUBLIC_SITE_URL', 'NEXT_PUBLIC_API_URL',
                  'NEXT_PUBLIC_API_DIRECT_ORIGIN', 'BACKEND_ORIGIN']
const RAILWAY = 'https://metricspro-production.up.railway.app'      // test fixture only
const PROD_ALIAS = 'metricspro-five.vercel.app'                     // test fixture only

// Load the real next.config.ts under an environment, exactly as `next build` would.
async function loadConfig(env) {
  const saved = Object.fromEntries(ENV_KEYS.map(k => [k, process.env[k]]))
  for (const k of ENV_KEYS) { if (env[k] === undefined) delete process.env[k]; else process.env[k] = env[k] }
  // The config's own modules read process.env at load: drop them from the require cache so each
  // scenario loads them fresh under its own environment.
  for (const k of Object.keys(require_.cache)) if (/[\\/](apiBase|site-routing)\.ts$/.test(k)) delete require_.cache[k]
  try {
    const mod = await transpileConfig({ nextConfigPath: join(HERE, 'next.config.ts'), dir: HERE })
    const cfg = mod && mod.default ? mod.default : mod   // the loader hands back the module; Next unwraps default
    const routes = await loadCustomRoutes(cfg)          // validates every route; exits 1 if invalid
    const headers = await cfg.headers()
    return { cfg, routes, headers }
  } finally {
    for (const k of ENV_KEYS) { if (saved[k] === undefined) delete process.env[k]; else process.env[k] = saved[k] }
  }
}

const originOf = d => `${d.protocol}//${d.hostname}${d.port ? `:${d.port}` : ''}`

// Evaluate the redirects for one absolute request URL with Next's own matchers (same options as
// Next's router: strict, unnamed params removed, /_next excluded from redirects).
function redirectFor(redirects, url) {
  const u = new URL(url)
  const req = { headers: { host: u.host } }
  const query = Object.fromEntries(u.searchParams)
  for (const r of redirects) {
    if (r.internal) continue
    const params = getPathMatch(r.source, { removeUnnamedParams: true, strict: true,
      regexModifier: re => modifyRouteRegex(re, ['/_next']) })(u.pathname)
    if (!params) continue
    const hasParams = matchHas(req, query, r.has, r.missing)
    if (!hasParams) continue
    const { parsedDestination, newUrl } = prepareDestination({
      appendParamsToQuery: false, destination: r.destination, params: { ...params, ...hasParams }, query })
    // Next passes the request's query through on a redirect (docs: redirects, "query values … passed through").
    const qs = new URLSearchParams({ ...query, ...(parsedDestination.query || {}) }).toString()
    return { status: getRedirectStatus(r),
             location: `${originOf(parsedDestination)}${newUrl}${qs ? `?${qs}` : ''}` }
  }
  return null
}

function rewriteFor(rewrites, path) {
  const u = new URL(path, 'https://site.invalid')
  for (const r of [...rewrites.beforeFiles, ...rewrites.afterFiles, ...rewrites.fallback]) {
    const params = getPathMatch(r.source, { removeUnnamedParams: true, strict: true,
      regexModifier: re => modifyRouteRegex(re) })(u.pathname)
    if (!params) continue
    const { parsedDestination, newUrl } = prepareDestination({
      appendParamsToQuery: false, destination: r.destination, params, query: Object.fromEntries(u.searchParams) })
    return `${originOf(parsedDestination)}${newUrl}`
  }
  return null
}

const cspOf = headers => (headers[0].headers.find(h => h.key === 'Content-Security-Policy-Report-Only') || {}).value || ''

// ── A. canonical host ─────────────────────────────────────────────────────────────────────────────
console.log('A. canonical host (production)')
const prod = await loadConfig({ VERCEL_ENV: 'production', NEXT_PUBLIC_SITE_URL: 'https://metricspro.tech',
                                NEXT_PUBLIC_API_URL: RAILWAY })
const R = prod.routes.redirects
let r = redirectFor(R, `https://${PROD_ALIAS}/login?next=%2Fcommcalc&x=1`)
check('production alias /login?… -> 308', r && r.status === 308, r)
check('… to https://metricspro.tech/login with the same query', r && r.location === 'https://metricspro.tech/login?next=%2Fcommcalc&x=1', r)
r = redirectFor(R, `https://${PROD_ALIAS}/`)
// An empty path serialises as the bare origin; a browser requests that as `/` (URL spec).
check('production alias / (the bare root) -> https://metricspro.tech/', r && r.status === 308 && new URL(r.location).href === 'https://metricspro.tech/', r)
check('/_next assets on the alias are not redirected (Next excludes them)', redirectFor(R, `https://${PROD_ALIAS}/_next/static/x.js`) === null)
r = redirectFor(R, `https://${PROD_ALIAS}/commcalc/upload/wizard`)
check('deep path keeps its path', r && r.location === 'https://metricspro.tech/commcalc/upload/wizard', r)
r = redirectFor(R, `https://${PROD_ALIAS}:443/portal`)
check('host with a port still matches', r && r.location === 'https://metricspro.tech/portal', r)
r = redirectFor(R, 'https://metricspro-abc123-cellfonzrus.vercel.app/login')
check('a production deployment URL is also sent to the canonical site', r && r.location === 'https://metricspro.tech/login', r)
check('the canonical host itself is NOT redirected (no loop)', redirectFor(R, 'https://metricspro.tech/login') === null)
check("a tenant's custom domain is NOT redirected", redirectFor(R, 'https://reports.some-tenant.com/login') === null)
check('a look-alike host is NOT redirected (anchored match)', redirectFor(R, `https://${PROD_ALIAS}.evil.example/login`) === null)

// ── B. previews ───────────────────────────────────────────────────────────────────────────────────
console.log('B. preview deployments')
const prev = await loadConfig({ VERCEL_ENV: 'preview', NEXT_PUBLIC_VERCEL_ENV: 'preview',
                                NEXT_PUBLIC_SITE_URL: 'https://metricspro.tech', NEXT_PUBLIC_API_URL: RAILWAY })
check('VERCEL_ENV=preview installs no redirect', prev.routes.redirects.filter(x => !x.internal).length === 0, prev.routes.redirects)
check('a PR preview host keeps its URL', redirectFor(prev.routes.redirects, 'https://metricspro-git-feature-x-cellfonzrus.vercel.app/login') === null)

// ── C. localhost ──────────────────────────────────────────────────────────────────────────────────
console.log('C. localhost')
const local = await loadConfig({})
check('no VERCEL_ENV (next dev) installs no redirect', local.routes.redirects.filter(x => !x.internal).length === 0)
check('localhost:3000 is untouched', redirectFor(local.routes.redirects, 'http://localhost:3000/login') === null)

// ── D. safety gates ───────────────────────────────────────────────────────────────────────────────
console.log('D. safety gates')
const noSite = await loadConfig({ VERCEL_ENV: 'production', NEXT_PUBLIC_API_URL: RAILWAY })
check('production WITHOUT an explicit NEXT_PUBLIC_SITE_URL installs no redirect (no lock-out)',
      noSite.routes.redirects.filter(x => !x.internal).length === 0)
const loopSite = await loadConfig({ VERCEL_ENV: 'production', NEXT_PUBLIC_SITE_URL: `https://${PROD_ALIAS}` })
check('a canonical site that is itself a platform host installs no redirect (no loop)',
      loopSite.routes.redirects.filter(x => !x.internal).length === 0)

// ── E. rewrites ───────────────────────────────────────────────────────────────────────────────────
console.log('E. rewrites')
const W = prod.routes.rewrites
check('/api/v1/core/me -> backend (NEXT_PUBLIC_API_URL fallback)', rewriteFor(W, '/api/v1/core/me?org_id=1') === `${RAILWAY}/api/v1/core/me`, rewriteFor(W, '/api/v1/core/me'))
check('/api/v1/commcalc/calculate/July%202026 keeps its path', rewriteFor(W, '/api/v1/commcalc/calculate/July%202026') === `${RAILWAY}/api/v1/commcalc/calculate/July%202026`, rewriteFor(W, '/api/v1/commcalc/calculate/July%202026'))
check('/health -> backend /health', rewriteFor(W, '/health') === `${RAILWAY}/health`, rewriteFor(W, '/health'))
check('a page path (/login) is not proxied', rewriteFor(W, '/login') === null)
check('/api/v2/x is not proxied (only the paths the backend serves)', rewriteFor(W, '/api/v2/x') === null)
const own = await loadConfig({ BACKEND_ORIGIN: 'https://backend.internal.example', NEXT_PUBLIC_API_URL: RAILWAY })
check('BACKEND_ORIGIN (server-only) wins over NEXT_PUBLIC_API_URL', rewriteFor(own.routes.rewrites, '/api/v1/x') === 'https://backend.internal.example/api/v1/x', rewriteFor(own.routes.rewrites, '/api/v1/x'))
check('nothing configured -> localhost:8000', rewriteFor(local.routes.rewrites, '/api/v1/x') === 'http://localhost:8000/api/v1/x', rewriteFor(local.routes.rewrites, '/api/v1/x'))

// ── F. CSP connect-src ────────────────────────────────────────────────────────────────────────────
console.log('F. CSP connect-src')
const withApi = await loadConfig({ VERCEL_ENV: 'production', NEXT_PUBLIC_SITE_URL: 'https://metricspro.tech',
                                   NEXT_PUBLIC_API_URL: RAILWAY, NEXT_PUBLIC_API_DIRECT_ORIGIN: 'https://api.metricspro.tech' })
check('direct origin configured -> connect-src names it', cspOf(withApi.headers).includes("connect-src 'self' https://*.supabase.co wss://*.supabase.co https://api.metricspro.tech"), cspOf(withApi.headers))
check('direct origin configured -> no backend host in the CSP', !/railway/.test(cspOf(withApi.headers)))
check('no wildcard backend host in the CSP (the old *.up.railway.app is gone)', !cspOf(prod.headers).includes('*.up.railway.app'), cspOf(prod.headers))
check('preview -> connect-src is self + supabase only', /connect-src 'self' https:\/\/\*\.supabase\.co wss:\/\/\*\.supabase\.co(;|$)/.test(cspOf(prev.headers)), cspOf(prev.headers))

// ── G. the one home ───────────────────────────────────────────────────────────────────────────────
console.log('G. the one home (src/lib/apiBase.ts)')
const A = await import(pathToFileURL(join(HERE, 'src/lib/apiBase.ts')).href)
const prodEnv = { NEXT_PUBLIC_API_URL: RAILWAY, NEXT_PUBLIC_API_DIRECT_ORIGIN: 'https://api.metricspro.tech' }
check('browser, ordinary call -> relative (same origin)', A.resolveApiUrl('/api/v1/core/me', prodEnv, true) === '/api/v1/core/me')
check('browser, upload -> the direct origin', A.resolveApiUrl('/api/v1/commcalc/upload-mapped', prodEnv, true, 'direct') === 'https://api.metricspro.tech/api/v1/commcalc/upload-mapped')
check('browser, 2FA portal login (long) -> direct, query ignored for the match',
      A.resolveApiUrl('/api/v1/commcalc/data-sources/abc/login/verify?org_id=1', prodEnv, true) === 'https://api.metricspro.tech/api/v1/commcalc/data-sources/abc/login/verify?org_id=1')
check('browser, payables rebuild (long) -> direct', A.routeClass('/api/v1/payables/rebuild') === 'direct')
check('browser, Run Calculation is NOT direct (it returns at once and polls)', A.routeClass('/api/v1/commcalc/calculate/2026-07?org_id=1') === 'proxy')
check('browser, supply vendor catalog read -> direct', A.routeClass('/api/v1/supply/vendors/v1/catalog/read') === 'direct')
check('server side -> the real backend origin', A.resolveApiUrl('/api/v1/core/me', { ...prodEnv, BACKEND_ORIGIN: 'https://b.example' }, false) === 'https://b.example/api/v1/core/me')
check('no direct origin yet -> direct calls fall back to NEXT_PUBLIC_API_URL (pre-change behaviour)',
      A.resolveApiUrl('/api/v1/x', { NEXT_PUBLIC_API_URL: RAILWAY }, true, 'direct') === `${RAILWAY}/api/v1/x`)
check('preview with no direct origin -> direct calls stay same-origin',
      A.resolveApiUrl('/api/v1/x', { NEXT_PUBLIC_API_URL: RAILWAY, NEXT_PUBLIC_VERCEL_ENV: 'preview' }, true, 'direct') === '/api/v1/x')
check('trailing slashes in an origin are tolerated', A.backendOrigin({ BACKEND_ORIGIN: 'https://b.example//' }) === 'https://b.example')
check('siteUrl defaults to https://metricspro.tech', A.siteUrl({}) === 'https://metricspro.tech')
check('BACKEND_PATH_PREFIXES are exactly /api/v1 and /health', JSON.stringify(A.BACKEND_PATH_PREFIXES) === '["/api/v1","/health"]')
check('absoluteApiUrl during SSR never renders the backend origin (site stands in)',
      A.absoluteApiUrl('/api/v1/closing/envelope-view/x') === `${A.siteUrl()}/api/v1/closing/envelope-view/x`)

// ── N. negative controls ──────────────────────────────────────────────────────────────────────────
console.log('N. negative controls')
const unguarded = R.map(x => ({ ...x, has: undefined }))
check('control: the same rule WITHOUT its host condition would redirect a tenant domain (so A is not vacuous)',
      redirectFor(unguarded, 'https://reports.some-tenant.com/login') !== null)
const unanchored = R.map(x => ({ ...x, has: [{ type: 'host', value: '.*vercel\\.app.*' }] }))
check('control: an unanchored-looking host pattern WOULD catch the look-alike (so the anchoring test bites)',
      redirectFor(unanchored, `https://${PROD_ALIAS}.evil.example/login`) !== null)
check('control: the evaluator finds no rewrite in an empty list', rewriteFor({ beforeFiles: [], afterFiles: [], fallback: [] }, '/api/v1/x') === null)

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
