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
  try { return typeof window !== 'undefined' ? (window.localStorage.getItem(ACTIVE_ORG_KEY) || null) : null }
  catch { return null }
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

// ── org_id APPEND (super-admin house-default hole — NEEDS CORE from mod-commission, 2026-07-14) ────
// The tenant middleware does NOT rewrite org_id for a super-admin (their client-supplied org_id is
// honored — that is what makes cross-tenant admin work). But ~17 commcalc/report READ pages call api()
// with NO org_id in the URL, so for a super-admin those fall through to the backend's `org_id = ORG_ID`
// default = the HOUSE org → a super-admin "acting as" a tenant via the switcher sees HOUSE data
// mislabeled (or empty-looking pages once client-side store filters apply). scopeOrg() only SUBSTITUTES
// an org_id already present — it never APPENDS one, so it can't fix an org-less URL.
//
// Fix: when a request carries NO org_id AND an active tenant is known (`mp_active_org`, maintained by
// the switcher / auth-context after /core/me resolves), APPEND `org_id=<active>` to the query string.
//   • Normal user  → HARMLESS: the middleware overrides org_id with their VERIFIED membership regardless
//                     of what the client sends, so the appended value is discarded server-side.
//   • Super-admin   → CLOSES the hole: their bypass trusts the client org_id, so the switcher's active
//                     org now drives org-less reads (the intended "acting as tenant" behavior).
//   • URL already carries org_id (e.g. /admin/billing?org_id=<specific tenant>, or a page that hardcodes
//     org_id=ORG_ID) → LEFT UNTOUCHED: append never double-adds and never overrides a deliberate
//     cross-tenant admin query. Those keep scopeOrg's existing substitute behavior.
//   • Before /core/me resolves (no active org known yet) → getActiveOrg() is null → append nothing.
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
// Compose substitute-then-append: scopeOrg rewrites an existing org_id (legacy P2, gated), appendActiveOrg
// adds one when absent. They are mutually exclusive by construction (one acts iff org_id present, the other
// iff absent), so they never conflict.
function withOrgScope(path: string): string {
  return appendActiveOrg(scopeOrg(path))
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
    headers: { 'Content-Type': 'application/json', ...authHeader, ...activeOrgHeader(), ...twofaHeader(), ...opts.headers },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
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
    headers: { ...authHeader, ...activeOrgHeader(), ...twofaHeader() } })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(errMsg(err, res.status))
  }
  return res.json()
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
