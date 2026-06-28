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
  const res = await fetch(`${API_URL}${scopeOrg(path)}`, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...authHeader, ...opts.headers },
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
  const res = await fetch(`${API_URL}${scopeOrg(path)}`, { method: 'POST', body: form, headers: authHeader })
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
