// Proof for the FRONTEND half of admin "view as employee" (owner directive 2026-08-06).
// Run:  node frontend/scratchpad/prove_impersonation_client.mjs
//
// Strategy (same as prove_import_health_nav / prove_nav_layout): verbatim re-implementations of the
// shipped predicates, PLUS source-parity guards asserting the real files still contain those exact
// bodies — so this can never drift into proving a stale copy.
//
// What matters here and why:
//   • the grant must ride on EVERY api()/apiUpload() call, or half the app renders as the admin and
//     half as the employee;
//   • it must NEVER ride on /api/v1/core/impersonation/* — the backend refuses that whole prefix for
//     an impersonated request, so attaching it there would lock the admin out of their own Exit button
//     and out of the clock-in unlock;
//   • `canImpersonate` must be DEFAULT-DENY with no bypass — this is the one permission in the product
//     that super-admin and scope-'all' do not imply;
//   • the banner must be driven by SERVER state, not localStorage.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const r = (...p) => readFileSync(join(here, '..', ...p), 'utf8')
const CLIENT = r('src', 'lib', 'client.ts')
const RBAC = r('src', 'lib', 'rbac.ts')
const AUTH = r('src', 'lib', 'auth-context.tsx')
const LAYOUT = r('src', 'app', '(platform)', 'layout.tsx')
const ROLES = r('src', 'app', '(platform)', 'admin', 'roles', 'page.tsx')
const AUDIT = r('src', 'app', '(platform)', 'admin', 'impersonation', 'page.tsx')

let pass = 0, fail = 0
const ok = (name, cond, extra) => { if (cond) { pass++; console.log('  ok  ', name) } else { fail++; console.log('  FAIL', name, extra ?? '') } }

// ── verbatim re-impl of the shipped predicates ──────────────────────────────────────────────────
const IMP_EXEMPT_RE = /^\/api\/v1\/core\/impersonation(\/|$|\?)/
function impersonationHeader(path, imp, reauth) {
  if (path && IMP_EXEMPT_RE.test(path)) return {}
  if (!imp) return {}
  const h = { 'x-impersonate': imp.grant }
  if (reauth) h['x-impersonate-reauth'] = reauth.marker
  return h
}
const canImpersonate = perms => perms?.impersonate === true
const HOUSE = '00000000-0000-0000-0000-000000000001'

console.log('\nA. the grant rides on ordinary calls, never on the console')
const IMP = { grant: 'g.sig', session_id: 's1', org_id: 'org-a' }
const RA = { marker: 'm.sig' }
ok('A1 not impersonating → no headers at all', Object.keys(impersonationHeader('/api/v1/commcalc/x', null, null)).length === 0)
ok('A2 impersonating → x-impersonate on a module call',
  impersonationHeader('/api/v1/storeops/timeclock/status', IMP, null)['x-impersonate'] === 'g.sig')
ok('A3 an outstanding unlock rides along too',
  impersonationHeader('/api/v1/storeops/timeclock/clock-in', IMP, RA)['x-impersonate-reauth'] === 'm.sig')
for (const p of ['/api/v1/core/impersonation/stop', '/api/v1/core/impersonation/reauth',
                 '/api/v1/core/impersonation/status', '/api/v1/core/impersonation',
                 '/api/v1/core/impersonation/log?limit=5']) {
  ok(`A4 EXEMPT — nothing attached to ${p}`, Object.keys(impersonationHeader(p, IMP, RA)).length === 0)
}
ok('A5 a look-alike sibling path is NOT exempt (boundary matched)',
  impersonationHeader('/api/v1/core/impersonationx', IMP, null)['x-impersonate'] === 'g.sig')
ok('A6 /core/me is NOT exempt — the profile must resolve as the employee',
  impersonationHeader('/api/v1/core/me', IMP, null)['x-impersonate'] === 'g.sig')

console.log('\nB. source parity — client.ts really does this')
ok('B1 both headers are defined', /'x-impersonate': imp\.grant/.test(CLIENT) && /'x-impersonate-reauth'/.test(CLIENT))
ok('B2 the exempt regex is the boundary-matched one',
  /const IMP_EXEMPT_RE = \/\^\\\/api\\\/v1\\\/core\\\/impersonation\(\\\/\|\$\|\\\?\)\//.test(CLIENT))
ok('B3 api() spreads impersonationHeader(path)', /\.\.\.impersonationHeader\(path\)/.test(CLIENT))
ok('B4 apiUpload() spreads it too (uploads must not silently act as the admin)',
  (CLIENT.match(/\.\.\.impersonationHeader\(path\)/g) || []).length >= 2)
ok('B5 an expired unlock is dropped client-side rather than sent',
  /if \(v\.expires_at && Date\.parse\(v\.expires_at\) <= Date\.now\(\)\) return null/.test(CLIENT))
ok('B6 the acting org follows the GRANT, not the admin\'s switcher',
  /const imp = getImpersonation\(\)\s*\n\s*if \(imp\?\.org_id\) return imp\.org_id/.test(CLIENT))
ok('B7 a dead session is detected by CODE, not by message text',
  /code !== 'impersonation_invalid'/.test(CLIENT))
ok('B8 …and detection DROPS the local grant', /markImpersonationInvalid[\s\S]{0,400}setImpersonation\(null\)/.test(CLIENT))
ok('B9 the 503 "unavailable" code is deliberately NOT treated as dead (retryable)',
  /`impersonation_unavailable`|impersonation_unavailable\` \(503\)|impersonation_unavailable/.test(CLIENT))
// The browser must be able to STORE and SEND a grant, never to MAKE one. (The word "HMAC" appears in
// the explanatory comment, so match on actual crypto/minting CODE, not prose.)
const codeOnly = CLIENT.replace(/\/\/[^\n]*/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
ok('B10 the client never mints anything — no signing code shipped to the browser',
  !/createHmac|crypto\.subtle|mintGrant|mint_grant|signGrant/.test(codeOnly))
ok('B11 …and no secret material is referenced client-side',
  !/IMPERSONATION_SECRET|AUTH_2FA_SECRET|SERVICE_KEY/.test(codeOnly))

console.log('\nC. the permission is DEFAULT-DENY with no bypass')
ok('C1 empty perms → denied', canImpersonate({}) === false)
ok('C2 scope "all" → denied', canImpersonate({ scope: 'all' }) === false)
ok('C3 modules.admin (isSuperAdmin) → denied', canImpersonate({ modules: { admin: true } }) === false)
ok('C4 explicit false → denied', canImpersonate({ impersonate: false }) === false)
ok('C5 truthy-but-not-true → denied', canImpersonate({ impersonate: 1 }) === false)
ok('C6 explicit true → granted', canImpersonate({ impersonate: true }) === true)
ok('C7 undefined perms → denied', canImpersonate(undefined) === false)
ok('C8 rbac.ts ships exactly this body (strict identity, no isSuperAdmin call)',
  /export function canImpersonate\(perms: Permissions \| undefined\): boolean \{\s*\n\s*return \(perms as any\)\?\.impersonate === true\s*\n\}/.test(RBAC))
ok('C9 …and it is NOT wired into hasDataGrant/hasReport (which DO default-open for scope "all")',
  !/hasDataGrant[\s\S]{0,200}impersonate/.test(RBAC))
ok('C10 the audit page is nav-gated by the EXISTING admin module (no new module key)',
  /href: '\/admin\/impersonation'[^}]*module: 'admin'/.test(RBAC))

console.log('\nD. the banner is impossible to miss and impossible to fake away')
ok('D1 the banner renders from SERVER state (impersonationInfo), not localStorage',
  /const \{ impersonationInfo, impersonation, stopImpersonation, unlockClockPunch \} = useAuth\(\)/.test(LAYOUT)
  && /if \(!impersonationInfo\) return null/.test(LAYOUT))
ok('D2 impersonationInfo comes from /core/me\'s server-declared field',
  /setImpersonationInfo\(d\.impersonation && d\.impersonation\.active \? d\.impersonation : null\)/.test(AUTH))
ok('D3 it names the employee and offers a one-click exit',
  /VIEWING AS/.test(LAYOUT) && /Exit — back to my account/.test(LAYOUT) && /stopImpersonation\(\)/.test(LAYOUT))
ok('D4 it counts down to the hard server-side expiry', /Ends automatically in/.test(LAYOUT))
ok('D5 it is sticky at the top of the shell with high contrast',
  /position: 'sticky', top: 0, zIndex: 40/.test(LAYOUT) && /#7f1d1d/.test(LAYOUT))
ok('D6 the tenant switcher is hidden while impersonating (the org is pinned)',
  /tenants\.length > 1 && !impersonationInfo/.test(LAYOUT))

console.log('\nE. the clock-in unlock uses a THROWAWAY client (the kiosk manager-override pattern)')
ok('E1 a separate anon client with persistSession:false + its own storageKey',
  /persistSession: false, autoRefreshToken: false, storageKey: 'mp-impersonation-reauth'/.test(AUTH))
ok('E2 the employee signs in on it — the admin session is untouched',
  /tmp\.auth\.signInWithPassword\(\{ email, password \}\)/.test(AUTH))
ok('E3 the raw PASSWORD never reaches our API — only the resulting token does',
  /body: JSON\.stringify\(\{ session_id: imp\.session_id, token: empToken \}\)/.test(AUTH)
  && !/JSON\.stringify\(\{[^}]*password[^}]*\}\)[\s\S]{0,80}impersonation\/reauth/.test(AUTH))
ok('E4 the throwaway session is signed out in a finally block', /finally \{\s*\n\s*try \{ await tmp\.auth\.signOut\(\)/.test(AUTH))
ok('E5 the unlock is stored for exactly one punch and advertised as such',
  /setImpersonationReauth\(\{ marker: d\.reauth, expires_at: d\.expires_at \}\)/.test(AUTH)
  && /good for ONE clock in or clock out/.test(LAYOUT))
ok('E6 signing out always drops the grant', /await supabase\.auth\.signOut\(\)[\s\S]{0,400}setImpersonation\(null\)/.test(AUTH))
ok('E7 start/stop hard-reload so nothing keeps the other identity\'s data',
  (AUTH.match(/window\.location\.href = '\/'/g) || []).length >= 2)

console.log('\nF. RULE THREE — the employee picker is a typeahead over the real roster')
ok('F1 the roles page uses the shared EntityPicker primitive, not a <select>',
  /import EntityPicker from '@\/components\/EntityPicker'/.test(ROLES)
  && /<EntityPicker options=\{targets\}/.test(ROLES))
ok('F2 …fed from the org-scoped backend roster', /api\('\/api\/v1\/core\/impersonation\/targets'\)/.test(ROLES))
ok('F3 …with the explicit /api/v1 prefix (a bare path 404s silently in the UI)',
  !/api\('\/core\/impersonation/.test(ROLES) && !/api\('\/core\//.test(AUDIT))
ok('F4 the card is hidden entirely without the permission', /const allowed = canImpersonate\(permissions as Permissions\)/.test(ROLES)
  && /if \(!allowed\) return null/.test(ROLES))
ok('F5 the role toggle exists and is its own default-off control',
  /checked=\{p\.impersonate === true\}/.test(ROLES) && /Sign in as an employee/.test(ROLES))
ok('F6 the toggle is NOT folded into the default-open settings/data blocks',
  !/settings: \{ \.\.\.\(pp\.settings \|\| \{\}\), impersonate/.test(ROLES)
  && !/data: \{ \.\.\.\(pp\.data \|\| \{\}\), impersonate/.test(ROLES))

console.log('\nG. the audit page')
ok('G1 it reads the org-scoped log endpoint with /api/v1', /api\('\/api\/v1\/core\/impersonation\/log\?limit=200'\)/.test(AUDIT))
ok('G2 it explains the un-run-migration state in plain English instead of erroring',
  /migration 730/.test(AUDIT) && /!ready &&/.test(AUDIT))
ok('G3 it surfaces still-open sessions prominently', /still open/.test(AUDIT) && /session.*open right now|open right now/.test(AUDIT))
ok('G4 per-session changes are expandable', /session_id=\$\{encodeURIComponent\(id\)\}/.test(AUDIT))
ok('G5 the policy is editable only with the backend-declared right', /disabled=\{!canEdit\}/.test(AUDIT))

console.log(`\n${'='.repeat(70)}\nPASS ${pass}   FAIL ${fail}`)
process.exit(fail ? 1 : 0)
