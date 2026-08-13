import { createClient } from '@supabase/supabase-js'

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL!
const SUPABASE_ANON = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON)

// ── Tenant scoping (SaaS P2 — GATED OFF by default) ─────────────────────────────────────────────
// When multi-tenant is enabled, every API call is scoped to the LOGGED-IN user's org instead of the
// hardcoded house org. AuthProvider pushes the user's org_id here after /core/me resolves. Gated
// behind a localStorage flag (`mp_multi_tenant`=='1'), default OFF, until the cross-tenant ISOLATION
// TEST passes — so today this is a complete NO-OP: scopeOrg() returns the path unchanged and every
// call still uses the house org_id the caller already put in the URL. P3 hardens this server-side.
let _sessionOrgId: string | null = null
export function setSessionOrgId(id: string | null | undefined) { _sessionOrgId = id || null }
function multiTenantOn(): boolean {
  try { return typeof window !== 'undefined' && window.localStorage.getItem('mp_multi_tenant') === '1' }
  catch { return false }
}
function scopeOrg(path: string): string {
  if (!multiTenantOn() || !_sessionOrgId) return path
  return /[?&]org_id=/.test(path)
    ? path.replace(/([?&]org_id=)[^&]*/, `$1${encodeURIComponent(_sessionOrgId)}`)
    : path
}

// ── Active tenant (multi-tenant login switcher, platform-core-9) ─────────────────────────────────
// A login may belong to >1 tenant. The chosen ("active") tenant is persisted in localStorage and sent
// on EVERY request as the `x-active-org` header. The header is UNTRUSTED: the tenant middleware honors
// it only when it names a tenant the login is a member of, else it falls back to the login's default
// membership. Single-tenant logins never set it and are entirely unaffected. Kept in localStorage (not
// a signed cookie) because the server re-verifies membership on every request — the header is a hint,
// not the authority.
const ACTIVE_ORG_KEY = 'mp_active_org'
export function getActiveOrg(): string | null {
  try {
    if (typeof window === 'undefined') return null
    // While viewing the app as an employee the acting tenant is the one PINNED IN THE GRANT, not the
    // admin's own switcher choice. The backend enforces this regardless (it overrides both the org_id
    // query param and x-active-org from the grant); mirroring it here keeps the client-side cache
    // namespace and any org-less URL pointing at the same tenant the server will use.
    const imp = getImpersonation()
    if (imp?.org_id) return imp.org_id
    return window.localStorage.getItem(ACTIVE_ORG_KEY) || null
  } catch { return null }
}
export function setActiveOrg(id: string | null | undefined) {
  try {
    if (typeof window === 'undefined') return
    if (id) window.localStorage.setItem(ACTIVE_ORG_KEY, id)
    else window.localStorage.removeItem(ACTIVE_ORG_KEY)
  } catch { /* ignore */ }
}
export function activeOrgHeader(): Record<string, string> {
  const o = getActiveOrg()
  return o ? { 'x-active-org': o } : {}
}

// ── Admin "view as employee" (impersonation, owner directive 2026-08-06) ─────────────────────────
// An admin with the (default-deny) `impersonate` role permission can enter the app AS an employee to
// reproduce a bug. What the browser holds is NOT a "pretend" flag: it is a SERVER-MINTED, HMAC-signed,
// DB-anchored grant (see backend app/core/impersonation.py). It is worthless on its own — the backend
// re-verifies the signature, the expiry, the DB session row AND that the request's own Supabase token
// belongs to the admin the grant was issued to, on every single request. Editing this localStorage
// entry by hand buys nothing; deleting it simply exits.
//
//   x-impersonate         — the grant. Sent on EVERY api()/apiUpload() call while a session is open…
//   x-impersonate-reauth  — …plus the single-use unlock the EMPLOYEE's own password produced, which
//                           the backend consumes on a clock-in / clock-out.
//
// EXEMPT PREFIX: never sent to /api/v1/core/impersonation/*. That console (stop / reauth / status /
// audit) must run as the REAL admin, and the backend refuses the whole prefix for an impersonated
// request — so attaching the header there would lock the admin out of their own exit button.
export type ImpersonationState = {
  grant: string; session_id: string; org_id: string
  target_name?: string | null; target_email?: string | null; target_role?: string | null
  expires_at?: string | null
}
const IMP_KEY = 'mp_impersonation'
const IMP_REAUTH_KEY = 'mp_impersonation_reauth'
const IMP_EXEMPT_RE = /^\/api\/v1\/core\/impersonation(\/|$|\?)/

export function getImpersonation(): ImpersonationState | null {
  try {
    if (typeof window === 'undefined') return null
    const raw = window.localStorage.getItem(IMP_KEY)
    if (!raw) return null
    const v = JSON.parse(raw)
    return v && v.grant && v.session_id ? v as ImpersonationState : null
  } catch { return null }
}
export function setImpersonation(v: ImpersonationState | null) {
  try {
    if (typeof window === 'undefined') return
    if (v) window.localStorage.setItem(IMP_KEY, JSON.stringify(v))
    else { window.localStorage.removeItem(IMP_KEY); window.localStorage.removeItem(IMP_REAUTH_KEY) }
  } catch { /* ignore */ }
}
/** The single-use "the employee just typed their password" unlock, if one is outstanding. */
export function getImpersonationReauth(): { marker: string; expires_at?: string } | null {
  try {
    if (typeof window === 'undefined') return null
    const raw = window.localStorage.getItem(IMP_REAUTH_KEY)
    if (!raw) return null
    const v = JSON.parse(raw)
    if (!v?.marker) return null
    if (v.expires_at && Date.parse(v.expires_at) <= Date.now()) return null   // expired → gone
    return v
  } catch { return null }
}
export function setImpersonationReauth(v: { marker: string; expires_at?: string } | null) {
  try {
    if (typeof window === 'undefined') return
    if (v) window.localStorage.setItem(IMP_REAUTH_KEY, JSON.stringify(v))
    else window.localStorage.removeItem(IMP_REAUTH_KEY)
  } catch { /* ignore */ }
}
export function impersonationHeader(path?: string): Record<string, string> {
  if (path && IMP_EXEMPT_RE.test(path)) return {}
  const imp = getImpersonation()
  if (!imp) return {}
  const h: Record<string, string> = { 'x-impersonate': imp.grant }
  const ra = getImpersonationReauth()
  if (ra) h['x-impersonate-reauth'] = ra.marker
  return h
}

// The impersonated session died server-side (exited elsewhere, expired, employee deactivated, the
// tenant switched it off). The backend answers 401 with code `impersonation_invalid`. Detect it at
// the ONE choke point, drop the local grant and notify the shell so it returns the admin to their own
// account instead of showing a page full of red errors. Single-shot latch, same shape as the
// dead-session detector above.
let _impInvalid = false
const _impInvalidCbs = new Set<() => void>()
export function onImpersonationInvalid(cb: () => void): () => void {
  _impInvalidCbs.add(cb)
  if (_impInvalid) { try { cb() } catch { /* never break a request */ } }
  return () => { _impInvalidCbs.delete(cb) }
}
export function clearImpersonationInvalid() { _impInvalid = false }
function markImpersonationInvalid(err: any) {
  const code = err?.code
  if (code !== 'impersonation_invalid') return      // `impersonation_unavailable` (503) = retryable
  setImpersonation(null)
  if (_impInvalid) return
  _impInvalid = true
  for (const cb of Array.from(_impInvalidCbs)) { try { cb() } catch { /* ignore */ } }
}

// ── 2FA verified-session marker (auth-hardening) ───────────────────────────────────────────────────
// After passing the sign-in OTP the backend mints a stateless signed marker; the client presents it on
// EVERY request as x-2fa-token. tenant_middleware enforces it only when the tenant policy requires 2FA
// (default OFF), so this header is inert for every tenant that hasn't turned 2FA on. Kept in
// localStorage — the server re-verifies the HMAC/expiry each request, so the header is a proof, not a
// trust. Cleared on sign-out.
const TWOFA_KEY = 'mp_2fa_token'
export function get2faToken(): string | null {
  try { return typeof window !== 'undefined' ? (window.localStorage.getItem(TWOFA_KEY) || null) : null }
  catch { return null }
}
export function set2faToken(tok: string | null | undefined) {
  try {
    if (typeof window === 'undefined') return
    if (tok) window.localStorage.setItem(TWOFA_KEY, tok)
    else window.localStorage.removeItem(TWOFA_KEY)
  } catch { /* ignore */ }
}
export function twofaHeader(): Record<string, string> {
  const t = get2faToken()
  return t ? { 'x-2fa-token': t } : {}
}

// ── org_id SCOPING: substitute the house constant + append when absent (cross-tenant leak class) ────
// The tenant middleware does NOT rewrite org_id for a super-admin — their client-supplied org_id is
// honored, which is exactly what makes cross-tenant "acting as a tenant" admin work (see
// tenant_middleware.py: super_admin ⇒ no rewrite). Two consequences the client must handle so a
// super-admin acting as another tenant via the switcher never reads/writes the HOUSE org by mistake:
//
//   1) APPEND (org-less URLs): many report/read pages call api() with NO org_id, so a super-admin acting
//      as a tenant falls through to the backend's `org_id = ORG_ID` default = the HOUSE org and sees
//      HOUSE data mislabeled. Fix: when a URL carries NO org_id and an active tenant is known, APPEND
//      `org_id=<active>` (appendActiveOrg).
//   2) SUBSTITUTE (the leak CLASS — many pages hardcode `org_id=${ORG_ID}`): those pages pin the LITERAL
//      house org_id into the query, so appendActiveOrg's "already scoped → leave it" guard would keep a
//      super-admin acting as another tenant reading/writing HOUSE data (the "Cellfonz stores under
//      LuxeLink" forecasting leak). Fix: when a URL carries `org_id=<the house constant>` and an active
//      tenant that is NOT house is known, SUBSTITUTE the active org for the house constant
//      (substituteHouseOrg). This makes the remaining per-page `org_id=${ORG_ID}` removals hygiene, not
//      leak fixes — module agents can deduplicate that effort.
//
// In both cases:
//   • Normal user  → HARMLESS: the middleware overrides org_id with their VERIFIED membership regardless
//                     of what the client sends, so the client value is discarded server-side.
//   • Super-admin   → CLOSES the hole: their bypass trusts the client org_id, so the switcher's active
//                     org now drives both org-less AND house-pinned reads/writes ("acting as tenant").
//   • House session → active org IS the house constant ⇒ SUBSTITUTE is a no-op and APPEND re-adds house
//                     (== the page's own default): byte-equivalent to today for every house-org user.
//   • Deliberate cross-tenant admin query (`?org_id=<a SPECIFIC non-house tenant>`) → LEFT UNTOUCHED:
//     substituteHouseOrg only rewrites the house LITERAL, never a specific tenant id; append never fires
//     when org_id is present. So an intentional foreign-tenant query always wins.
//   • Before /core/me resolves (no active org yet) → getActiveOrg() is null ⇒ both are no-ops.
//
// LIMITATION (documented, not a leak): only the QUERY STRING is rewritten here. A POST/PUT/PATCH BODY
// that hardcodes ORG_ID is NOT rewritten — but the middleware still rewrites the org_id QUERY PARAM for
// every NORMAL user, and org_id must be a query param (AGENT_CONTRACT §2), so a contract-compliant write
// routes correctly. Any handler still reading org from a request body is a mis-file bug to fix at the
// module, not here.
// A fragment (rare for API paths) is carried through so org_id lands in the query, not the hash.
function appendActiveOrg(path: string): string {
  const active = getActiveOrg()
  if (!active) return path
  const hashIdx = path.indexOf('#')
  const base = hashIdx >= 0 ? path.slice(0, hashIdx) : path
  const frag = hashIdx >= 0 ? path.slice(hashIdx) : ''
  if (/[?&]org_id=/.test(base)) return path            // already scoped → don't double-add / don't fight it
  const sep = base.includes('?') ? '&' : '?'
  return `${base}${sep}org_id=${encodeURIComponent(active)}${frag}`
}
// Substitute the LITERAL house org_id in the query string with the active tenant. Only the house constant
// is rewritten; a specific non-house org_id (a deliberate cross-tenant admin query) is never touched. No-op
// when no active org is known or the active org IS the house org. `active` is a PARAMETER (not read from
// storage) so the rule is a pure, testable function; substituteHouseOrg is the thin storage-backed wrapper.
function subHouseOrgWith(path: string, active: string | null): string {
  if (!active || active === ORG_ID) return path
  return path.replace(/([?&]org_id=)([^&#]*)/, (full, pfx, val) =>
    decodeURIComponent(val) === ORG_ID ? `${pfx}${encodeURIComponent(active)}` : full)
}
function substituteHouseOrg(path: string): string {
  return subHouseOrgWith(path, getActiveOrg())
}
// Compose scope → substitute-house → append. scopeOrg rewrites an existing org_id (legacy P2, gated OFF);
// substituteHouseOrg swaps the house LITERAL for the active tenant; appendActiveOrg adds one when absent.
// substituteHouseOrg (acts iff org_id present) and appendActiveOrg (acts iff org_id absent) are mutually
// exclusive by construction, so they never conflict or double-add.
function withOrgScope(path: string): string {
  return appendActiveOrg(substituteHouseOrg(scopeOrg(path)))
}

// ── DEAD CLIENT SESSION detection (auth-ux hardening 2026-08-03) ────────────────────────────────
// Live incident (house org, 2026-08-03): a browser held a stale/invalid Supabase session. The shell
// rendered (the client "had" a session) but EVERY module call came back 401
// {"detail":"authentication required"} from tenant_middleware._reject_401 — the user experienced it
// as "all modules are broken" instead of "you need to sign in again".
//
// api()/apiUpload() are the ONE choke point every page goes through, so the mismatch is detected
// here and surfaced ONCE by the platform Guard (auth-context subscribes; layout.tsx renders the
// single "session expired" card and routes to sign-in). Design constraints, all enforced below:
//
//   • FIRES ONLY when the client BELIEVED it had a session — i.e. a bearer token was actually
//     attached to the failing request. No token ⇒ never fires, which is exactly what keeps the
//     anonymous kiosk and the "login enforcement OFF" (open-app) mode untouched.
//   • EXACT-MATCH on the middleware's own detail string. A 2FA challenge (`two-factor
//     authentication required`), a handler-level 401, or any other message does NOT trigger it.
//   • NEVER on the auth/bootstrap endpoints themselves — a 401 from those IS the login flow
//     talking, and reacting to it is how you build a redirect storm.
//   • NEVER on a public / token-authenticated browser route (login, signup, onboarding, kiosk,
//     privacy): those pages authenticate by their own link token or not at all.
//   • SINGLE-SHOT: the first detection latches the flag; the 30 other in-flight calls on the page
//     see the latch and do nothing. Cleared only by an explicit sign-out.
//   • api()'s THROWN ERROR IS UNCHANGED for every status including 401 (same errMsg(), same
//     `new Error(...)`) — 210 files consume that contract and none of them may change behaviour.
const DEAD_SESSION_DETAIL = 'authentication required'   // verbatim tenant_middleware.py::_reject_401

// API paths whose own 401 must never be read as "the session died" (they ARE the sign-in path).
const AUTH_FLOW_PATH_RE =
  /\/api\/v1\/core\/(auth-config|auth\/|bootstrap|me(\/|$|\?)|my-tenants|pending-connections|connect-tenant|disable-and-switch|signup|password-policy)/
// Browser routes that are public or authenticate by their own token — never bounce these.
const PUBLIC_ROUTE_RE = /^\/(login|signup|onboard|portal|privacy)(\/|$)/

let _sessionInvalid = false
const _sessionInvalidCbs = new Set<() => void>()

export function isSessionInvalid(): boolean { return _sessionInvalid }

/** Clear the latch. Called by AuthProvider.signOut() — the ONLY way out of the expired state. */
export function clearSessionInvalid() { _sessionInvalid = false }

/** Subscribe to the dead-session signal. Fires immediately if it already happened (so a component
 *  that mounts after the fact still sees it). Returns an unsubscribe fn. */
export function onSessionInvalid(cb: () => void): () => void {
  _sessionInvalidCbs.add(cb)
  if (_sessionInvalid) { try { cb() } catch { /* a listener must never break a request */ } }
  return () => { _sessionInvalidCbs.delete(cb) }
}

// ── AMBIGUOUS TENANT (2026-08-09) ───────────────────────────────────────────────────────────────
// A login belonging to >1 company that has not said which one it is acting in gets 409
// `tenant_choice_required` from tenant_middleware._reject_tenant_choice, instead of the old silent
// answer-as-the-oldest-tenant. This latch mirrors the dead-session one directly below: DETECT ONLY —
// it never changes what api() throws — and it fires once, not once per in-flight request.
// The session is NOT dead here, so the session-invalid latch is deliberately left alone: the fix is to
// pick a company, not to sign out.
const TENANT_CHOICE_CODE = 'tenant_choice_required'
let _tenantChoiceRequired = false
const _tenantChoiceCbs = new Set<() => void>()

export function isTenantChoiceRequired(): boolean { return _tenantChoiceRequired }

/** Cleared by AuthProvider once a company has actually been chosen. */
export function clearTenantChoiceRequired() { _tenantChoiceRequired = false }

/** Subscribe to "this login must pick a company". Fires immediately if it already happened, so a
 *  component mounting after the fact still sees it. Returns an unsubscribe fn. */
export function onTenantChoiceRequired(cb: () => void): () => void {
  _tenantChoiceCbs.add(cb)
  if (_tenantChoiceRequired) { try { cb() } catch { /* a listener must never break a request */ } }
  return () => { _tenantChoiceCbs.delete(cb) }
}

function markTenantChoiceRequired(err: unknown) {
  if ((err as any)?.code !== TENANT_CHOICE_CODE) return
  // A stale saved choice is exactly how this state is reached (the header named a company this login
  // no longer belongs to, or none was saved at all). Drop it so the picker starts clean.
  setActiveOrg(null)
  if (_tenantChoiceRequired) return                                // latch: notify once, not N times
  _tenantChoiceRequired = true
  for (const cb of Array.from(_tenantChoiceCbs)) {
    try { cb() } catch { /* never let a listener break the failing request */ }
  }
}

function markSessionInvalid(path: string, detail: unknown, hadToken: boolean) {
  if (!hadToken) return                                            // no session believed → not our case
  if (typeof detail !== 'string') return
  if (detail.trim().toLowerCase() !== DEAD_SESSION_DETAIL) return  // exact middleware string only
  if (AUTH_FLOW_PATH_RE.test(path)) return                         // no storms off the login path
  if (typeof window === 'undefined') return
  if (PUBLIC_ROUTE_RE.test(window.location.pathname)) return       // public / token-auth pages
  if (_sessionInvalid) return                                      // latch: notify once, not N times
  _sessionInvalid = true
  for (const cb of Array.from(_sessionInvalidCbs)) {
    try { cb() } catch { /* never let a listener break the failing request */ }
  }
}

// Render a FastAPI error body as a readable string. `detail` may be a string, an ARRAY of
// validation errors (422 → [{loc,msg,...}]), or an object — coercing those with `+`/template
// strings is what produced the "[object Object]" error users saw on upload.
function errMsg(err: any, status?: number): string {
  const d = err?.detail ?? err
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((e: any) => e?.msg || (typeof e === 'string' ? e : JSON.stringify(e))).join('; ')
  if (d && typeof d === 'object') return d.msg || d.message || JSON.stringify(d)
  return String(d ?? (status ? `API error ${status}` : 'Request failed'))
}

async function bearer(): Promise<Record<string, string>> {
  try {
    const { data } = await supabase.auth.getSession()
    const tok = data?.session?.access_token
    if (tok) return { Authorization: `Bearer ${tok}` }
  } catch { /* not signed in / open mode */ }
  return {}
}

// API client for FastAPI backend. Attaches the Supabase session token so the backend can identify
// the caller for span-scoped reads (Phase 5). The backend ignores it while RBAC enforcement is off.
// An explicit Authorization in opts.headers still wins (spread last).
export async function api(path: string, opts: RequestInit = {}) {
  const authHeader = await bearer()
  const res = await fetch(`${API_URL}${withOrgScope(path)}`, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...authHeader, ...activeOrgHeader(), ...twofaHeader(),
               ...impersonationHeader(path), ...opts.headers },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    // Detect-only: never changes what is thrown (see DEAD CLIENT SESSION block above).
    if (res.status === 401) markSessionInvalid(path, (err as any)?.detail, !!authHeader.Authorization)
    if (res.status === 401 || res.status === 403) markImpersonationInvalid(err)
    if (res.status === 409) markTenantChoiceRequired(err)
    throw new Error(errMsg(err, res.status))
  }
  return res.json()
}

// Multipart upload (FormData) — the JSON `api()` helper above forces a JSON content-type,
// which breaks file uploads. Let the browser set the multipart boundary itself. Sends the auth
// token too (needed once enforcement is on).
export async function apiUpload(path: string, form: FormData) {
  const authHeader = await bearer()
  const res = await fetch(`${API_URL}${withOrgScope(path)}`, { method: 'POST', body: form,
    headers: { ...authHeader, ...activeOrgHeader(), ...twofaHeader(), ...impersonationHeader(path) } })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    if (res.status === 401) markSessionInvalid(path, (err as any)?.detail, !!authHeader.Authorization)
    if (res.status === 401 || res.status === 403) markImpersonationInvalid(err)
    if (res.status === 409) markTenantChoiceRequired(err)
    throw new Error(errMsg(err, res.status))
  }
  return res.json()
}

// Server-rendered FILE download (PDF/XLSX/…). Same choke point as api(): identical org-scoping and auth
// headers, identical dead-session / impersonation / tenant-choice latches and error contract — but it
// reads the response as BYTES and triggers a browser download instead of parsing JSON. Used for endpoints
// that return a file body (e.g. the commission-statement PDFs). `filename` overrides the server's
// Content-Disposition name when given.
export async function apiDownload(path: string, filename?: string) {
  const authHeader = await bearer()
  const res = await fetch(`${API_URL}${withOrgScope(path)}`, {
    headers: { ...authHeader, ...activeOrgHeader(), ...twofaHeader(), ...impersonationHeader(path) },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    if (res.status === 401) markSessionInvalid(path, (err as { detail?: unknown })?.detail, !!authHeader.Authorization)
    if (res.status === 401 || res.status === 403) markImpersonationInvalid(err)
    if (res.status === 409) markTenantChoiceRequired(err)
    throw new Error(errMsg(err, res.status))
  }
  const blob = await res.blob()
  // Fall back to the server's Content-Disposition filename, then a generic one.
  let name = filename
  if (!name) {
    const cd = res.headers.get('Content-Disposition') || ''
    const m = /filename="?([^"]+)"?/.exec(cd)
    name = m ? m[1] : 'download'
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const fmt = (n: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n || 0)

export const fmtN = (n: number, dec = 1) =>
  Number(n || 0).toFixed(dec)

export const ORG_ID = '00000000-0000-0000-0000-000000000001'

// Browser-local date (YYYY-MM-DD) so "today" on the targets pages tracks the
// store's wall clock, not the server's UTC date.
export const localToday = () => {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

// Parse a 'YYYY-MM-DD' string as a LOCAL date. Avoids the `new Date("2026-06-22")`
// UTC pitfall, which renders the previous day / wrong weekday for users west of UTC
// (the cause of "06/22 shows as Tuesday" and "time-off 6/22 displays as 6/21").
export const parseLocalDate = (iso: string) => {
  const [y, m, d] = String(iso || '').split('-').map(Number)
  return new Date(y || 1970, (m || 1) - 1, d || 1)
}

const _ymd = (d: Date) => {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

// Add `n` days to a 'YYYY-MM-DD' string, returning a 'YYYY-MM-DD' string (local-safe).
export const addDays = (iso: string, n: number) => {
  const d = parseLocalDate(iso); d.setDate(d.getDate() + n); return _ymd(d)
}

// Monday of the week containing `iso` (or today), as 'YYYY-MM-DD' (local-safe).
export const mondayOf = (iso?: string) => {
  const d = iso ? parseLocalDate(iso) : new Date()
  const day = d.getDay() // 0=Sun .. 6=Sat
  d.setDate(d.getDate() - (day === 0 ? 6 : day - 1))
  return _ymd(d)
}
