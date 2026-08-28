import { ENV, HOUSE_ORG_ID } from '@/config/env'
import { getActiveOrg, get2faToken } from '@/auth/tokens'
import { currentAccessToken, supabase } from './supabase'

// ── API client ───────────────────────────────────────────────────────────────────────────────────
// The native twin of frontend/src/lib/client.ts. Every request carries:
//   • Authorization: Bearer <supabase access token>  — identity + tenant derivation server-side
//   • x-active-org   — the acting tenant for a multi-tenant login (a hint; server re-verifies)
//   • x-2fa-token    — the signed OTP proof (inert unless the tenant requires 2FA)
// It also appends org_id to org-less URLs for a super-admin acting as another tenant, exactly like
// the web client, so read pages don't silently fall back to the house org.
//
// SECURITY: we never log the token or full URLs with tokens; error bodies are surfaced as readable
// strings; a 401 with the middleware's exact "authentication required" string is turned into a typed
// AuthError so the shell can bounce to sign-in instead of painting a screen of red.

export class ApiError extends Error {
  status: number
  code?: string
  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}
export class AuthError extends ApiError {
  constructor(message: string, status = 401, code?: string) {
    super(message, status, code)
    this.name = 'AuthError'
  }
}

const DEAD_SESSION_DETAIL = 'authentication required' // verbatim tenant_middleware._reject_401

// Subscribers notified when a live token is rejected — the shell signs the user out / shows a card.
type Listener = () => void
const sessionInvalidListeners = new Set<Listener>()
export function onSessionInvalid(cb: Listener): () => void {
  sessionInvalidListeners.add(cb)
  return () => sessionInvalidListeners.delete(cb)
}
function fireSessionInvalid() {
  for (const cb of Array.from(sessionInvalidListeners)) {
    try {
      cb()
    } catch {
      /* a listener must never break a request */
    }
  }
}

function appendActiveOrg(path: string): string {
  const active = getActiveOrg()
  if (!active) return path
  if (/[?&]org_id=/.test(path)) return path
  const sep = path.includes('?') ? '&' : '?'
  return `${path}${sep}org_id=${encodeURIComponent(active)}`
}

// Substitute the literal house org_id with the active tenant (super-admin acting-as-tenant). A
// specific non-house org_id (a deliberate cross-tenant query) is never rewritten. No-op for a normal
// user — the server overrides org_id from their verified membership regardless.
function substituteHouseOrg(path: string): string {
  const active = getActiveOrg()
  if (!active || active === HOUSE_ORG_ID) return path
  return path.replace(/([?&]org_id=)([^&#]*)/, (full, pfx, val) =>
    decodeURIComponent(val) === HOUSE_ORG_ID ? `${pfx}${encodeURIComponent(active)}` : full,
  )
}

function withOrgScope(path: string): string {
  return appendActiveOrg(substituteHouseOrg(path))
}

async function authHeaders(): Promise<Record<string, string>> {
  const h: Record<string, string> = {}
  const tok = await currentAccessToken()
  if (tok) h.Authorization = `Bearer ${tok}`
  const org = getActiveOrg()
  if (org) h['x-active-org'] = org
  const twofa = get2faToken()
  if (twofa) h['x-2fa-token'] = twofa
  return h
}

// Render a FastAPI error body (string | array of validation errors | object) as a readable string.
function errMsg(detail: unknown, status: number): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail))
    return detail
      .map((e: any) => e?.msg || (typeof e === 'string' ? e : JSON.stringify(e)))
      .join('; ')
  if (detail && typeof detail === 'object')
    return (detail as any).msg || (detail as any).message || JSON.stringify(detail)
  return `API error ${status}`
}

const DEFAULT_TIMEOUT_MS = 20000

type ApiOptions = RequestInit & { timeoutMs?: number }

async function request<T>(path: string, opts: ApiOptions = {}): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...init } = opts
  const headers = await authHeaders()
  const hadToken = !!headers.Authorization

  // Per-request timeout so a dead network fails fast instead of hanging the UI. The offline queue
  // (for mutations) and react-query retry (for reads) handle the recovery.
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  let res: Response
  try {
    res = await fetch(`${ENV.apiUrl}${withOrgScope(path)}`, {
      ...init,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...headers,
        ...(init.headers as Record<string, string> | undefined),
      },
    })
  } catch (e: any) {
    clearTimeout(timer)
    if (e?.name === 'AbortError') throw new ApiError('Request timed out', 0)
    throw new ApiError('Network unavailable', 0)
  }
  clearTimeout(timer)

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    const detail = (body as any)?.detail
    const code = (body as any)?.code
    const message = errMsg(detail, res.status)
    if (
      res.status === 401 &&
      hadToken &&
      typeof detail === 'string' &&
      detail.trim().toLowerCase() === DEAD_SESSION_DETAIL
    ) {
      fireSessionInvalid()
      throw new AuthError(message, 401, code)
    }
    throw new ApiError(message, res.status, code)
  }
  // 204 / empty body
  const text = await res.text()
  return (text ? JSON.parse(text) : null) as T
}

export const api = {
  get: <T>(path: string, opts?: ApiOptions) => request<T>(path, { ...opts, method: 'GET' }),
  post: <T>(path: string, body?: unknown, opts?: ApiOptions) =>
    request<T>(path, { ...opts, method: 'POST', body: body != null ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown, opts?: ApiOptions) =>
    request<T>(path, { ...opts, method: 'PUT', body: body != null ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown, opts?: ApiOptions) =>
    request<T>(path, { ...opts, method: 'PATCH', body: body != null ? JSON.stringify(body) : undefined }),
  del: <T>(path: string, opts?: ApiOptions) => request<T>(path, { ...opts, method: 'DELETE' }),
}

/** Authed GET that returns the raw TEXT body (not JSON) — for a server-rendered HTML response such as
 *  the receipt reprint, which is then handed to expo-print. Carries the same auth/org headers as api. */
export async function apiGetText(path: string, opts: ApiOptions = {}): Promise<string> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS } = opts
  const headers = await authHeaders()
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let res: Response
  try {
    res = await fetch(`${ENV.apiUrl}${withOrgScope(path)}`, { signal: controller.signal, headers })
  } catch (e: any) {
    clearTimeout(timer)
    throw new ApiError(e?.name === 'AbortError' ? 'Request timed out' : 'Network unavailable', 0)
  }
  clearTimeout(timer)
  if (!res.ok) throw new ApiError(`API error ${res.status}`, res.status)
  return res.text()
}

/** Force a token refresh (used before a critical mutation to avoid a mid-write expiry). */
export async function refreshSession(): Promise<void> {
  await supabase.auth.refreshSession().catch(() => {})
}
