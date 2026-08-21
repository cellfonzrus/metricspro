// Vision shared types + helpers. Kept here (rather than re-declared per page) so the camera,
// heat-map and behavior shapes cannot drift between the live wall, the heat map, the coaching board
// and settings. Styling reuses lib/referral's visual language so the module does not look bolted on.
import type { CSSProperties } from 'react'

export const cell: CSSProperties = { padding: '7px 12px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
export const th: CSSProperties = { ...cell, textAlign: 'left', fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)', fontWeight: 600 }
export const panel: CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }
export const btn: CSSProperties = { padding: '7px 14px', borderRadius: 7, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13, cursor: 'pointer' }
export const btnPrimary: CSSProperties = { ...btn, background: '#2563eb', borderColor: '#2563eb', color: '#fff', fontWeight: 600 }

// MIRROR of backend app/modules/vision/config.py DEFAULT_CONFIG. Keep in sync.
export interface VisionConfig {
  available: boolean
  enabled: boolean
  live_view_enabled: boolean
  traffic_enabled: boolean
  heatmap_enabled: boolean
  audio_analytics_enabled: boolean
  behavior_scoring_enabled: boolean
  audio_kill_switch: boolean
  audio_consent_mode: 'required' | 'off'
  grid_cols: number
  grid_rows: number
  min_visit_seconds: number
  max_visit_seconds: number
  stream_max_minutes: number
  presence_retention_days: number
  visit_retention_days: number
  transcript_retention_days: number
  heat_retention_days: number
  score_retention_days: number
  enabled_at?: string | null
  enabled_by?: string | null
  can_edit?: boolean
}

export interface Camera {
  id: string
  device_name: string
  device_type: string | null
  display_name: string | null
  label: string | null
  store_code: string | null
  room: string | null
  stream_protocol: 'webrtc' | 'rtsp'
  supports_audio: boolean
  analytics_enabled: boolean
  audio_enabled: boolean
  is_entrance: boolean
  enabled: boolean
  status: 'online' | 'offline' | 'unknown'
  last_seen_at: string | null
  structure_id?: string | null
  structure_name?: string | null
}

// A "home" in the Google Home app. One Google account can own several; only the ones a company
// connects contribute cameras. MIRROR of GET /api/v1/vision/structures.
export interface VisionHome {
  structure_id: string
  structure_name: string
  assigned: boolean
  enabled: boolean
  default_store_code: string | null
  claimed_by_another_company: boolean
}

export interface TrafficHour { hour: number; in: number; out: number; net: number }
export interface TrafficSummary {
  total_in: number; total_out: number; drift: number
  hourly: TrafficHour[]
  peak_hour: number | null; peak_hour_in: number
  customers: number
  avg_dwell_seconds: number | null; median_dwell_seconds: number | null
}

export interface HeatPayload {
  grid_cols: number; grid_rows: number
  matrix: number[][]
  max: number; p95: number
  total_person_seconds: number; occupied_cells: number
  hot_cells: { cell_x: number; cell_y: number; occupancy: number }[]
  dead_zones: { cell_x: number; cell_y: number }[]
}

export interface BehaviorEmployee {
  employee_id: string
  name: string
  days: number
  segments: number
  interactions: number
  talk_seconds: number
  greeted: number
  missed_greetings: number
  greet_rate: number | null
  score: number
  rule_hits: Record<string, number>
  coaching: { rule_key: string; label: string; coverage: number; gap?: number; severity?: string }[]
  series: { local_date: string; score: number; interactions: number }[]
}

// Turn an api() failure into something an operator can act on.
//
// THE CASE THIS EXISTS FOR: when the backend has not been redeployed but the frontend has, every
// Vision endpoint 404s and FastAPI answers {"detail":"Not Found"} — which the page then displayed
// verbatim. "Not found" reads like a missing page and sends the reader hunting through navigation,
// when the real state is "the server does not have this module yet, wait for the deploy". That cost
// a real support round trip on the day this shipped.
export function visionError(e: any): string {
  const status = e?.status
  const msg = e?.message || String(e || 'Request failed')
  if (status === 404) {
    return 'The Vision module is not on the API server yet. The app has been updated but the '
      + 'backend deploy has not finished — wait for it to complete, then reload this page.'
  }
  if (status === 503) {
    return 'The API is not reachable right now. If a deploy is in progress, wait for it to finish '
      + 'and reload.'
  }
  return msg
}

export function cameraName(c: Camera): string {
  return c.label || c.display_name || c.device_name.split('/').pop() || 'Camera'
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const s = Math.round(seconds)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function hourLabel(h: number): string {
  if (h === 0) return '12a'
  if (h === 12) return '12p'
  return h < 12 ? `${h}a` : `${h - 12}p`
}

// The heat ramp. Normalising by the MAX alone makes every store look identical — one scorching cell
// at the register and everything else black — so the ramp is clipped at the 95th percentile that the
// backend returns alongside the max. The register still reads hottest; the difference between the
// accessory wall and the empty corner stays visible, which is the point of the map.
export function heatColor(value: number, ceiling: number): string {
  if (!ceiling || value <= 0) return 'transparent'
  const t = Math.min(1, value / ceiling)
  // blue (cold) -> cyan -> green -> amber -> red (hot), with alpha carrying the low end so an empty
  // cell fades into the floor plan rather than painting it navy.
  const stops: [number, number, number][] = [[37, 99, 235], [6, 182, 212], [22, 163, 74], [245, 158, 11], [220, 38, 38]]
  const pos = t * (stops.length - 1)
  const i = Math.min(stops.length - 2, Math.floor(pos))
  const f = pos - i
  const c = stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * f))
  return `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${0.25 + 0.75 * t})`
}

export function today(): string {
  return new Date().toISOString().slice(0, 10)
}

export function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

// ── Google link: two independent steps, two independent gates ──────────────────────────────────
// The ids and the secret are NOT one action, and treating them as one is what lost the project id.
// A single button saved all three at once and stayed disabled until all three boxes were filled, so
// an operator who had the project id and client id but not the secret to hand could press nothing —
// and nothing reached the server. The ids then vanished with the page, which reads exactly like a
// save that silently failed.
//
// So: saving the ids asks only for the ids. Authorizing asks for the secret on top. The secret is
// never echoed back into the form — the operator types it when they authorize, every time.
export interface GoogleLinkState {
  linked?: boolean
  project_id?: string | null
  client_id?: string | null
  has_secret?: boolean
}

export interface GoogleLinkForm { project: string; clientId: string; secret: string }

const saved = (typed: string, stored: string | null | undefined) =>
  (typed || '').trim() || (stored || '').trim()

/** '' when the project id and client id can be saved; otherwise the one still missing. */
export function idsBlocker(google: GoogleLinkState, form: GoogleLinkForm): string {
  if (!saved(form.project, google.project_id)) return 'Enter the Device Access project id.'
  if (!saved(form.clientId, google.client_id)) return 'Enter the OAuth client id.'
  return ''
}

/** '' when the consent url can be requested. Needs the ids AND a secret typed right now. */
export function authorizeBlocker(google: GoogleLinkState, form: GoogleLinkForm): string {
  const ids = idsBlocker(google, form)
  if (ids) return ids
  if (!(form.secret || '').trim()) return 'Enter the OAuth client secret to authorize.'
  return ''
}

/** What Google put on the URL when it sent the operator back here. Exactly one of the three is set. */
export function oauthReturn(search: string): { code: string; error: string; none: boolean } {
  const q = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search)
  const code = (q.get('code') || '').trim()
  const error = (q.get('error') || '').trim()
  // `code` wins: Google never sends both, and preferring it keeps a stale `error` from an earlier
  // attempt left in the URL from discarding a good authorization.
  if (code) return { code, error: '', none: false }
  if (error) return { code: '', error, none: false }
  return { code: '', error: '', none: true }
}

// ── Camera sync: say what happened, including when nothing did ──────────────────────────────────
// Sync has three genuinely different "no cameras appeared" outcomes and they need different actions
// from the operator. Collapsing them into one line — or into no line, which is what happened —
// leaves someone staring at a button that looks broken while the request succeeded.
export interface SyncResult {
  found?: number
  added?: number
  updated?: number
  skipped?: number
  skipped_homes?: Record<string, number>
}

export function syncMessage(r: SyncResult): string {
  const found = r.found || 0
  const skipped = r.skipped || 0
  const kept = found - skipped
  const where = Object.entries(r.skipped_homes || {})
    .map(([home, n]) => `${n} in ${home}`).join(', ')

  // Google had nothing to give us. Almost always the wrong Google account, or devices not ticked on
  // the consent screen — neither of which we can see from here, so name both.
  if (!found) {
    return 'Google returned no cameras for this account. Check that you authorized the Google '
      + 'account that owns the store cameras, and that you ticked the cameras themselves on the '
      + 'consent screen — an account can link successfully while sharing nothing.'
  }
  // Cameras exist but every one sits in a home this company has not claimed. This is the fail-closed
  // path from migration 901 doing its job, not a failure.
  if (!kept) {
    return `Google returned ${found} camera(s), and all of them are in homes this company has not `
      + `connected yet (${where}). Connect the right home in section 3b below, then sync again.`
  }
  return `Synced ${kept} camera(s): ${r.added || 0} new, ${r.updated || 0} updated.`
    + (skipped ? ` Skipped ${where} — connect the home in section 3b to include them.` : '')
}

// ── Stores: pick one, never type one ────────────────────────────────────────────────────────────
// A store code typed by hand does not fail loudly. It saves, and then that camera's customers are
// counted against a store that does not exist — the traffic simply never appears in a report and
// nothing anywhere says why. Assigning from the company's real store list removes the whole class.
export interface StoreOption { code: string; label: string }

interface StoreRow {
  store_code?: string | null
  address?: string | null
  name?: string | null
  is_active?: boolean
}

/** The active, usable stores out of whatever /storeops/stores returned, in a stable order. */
export function storeOptions(rows: StoreRow[] | { stores?: StoreRow[] } | null | undefined): StoreOption[] {
  const list: StoreRow[] = Array.isArray(rows) ? rows : (rows?.stores || [])
  const seen = new Set<string>()
  const out: StoreOption[] = []
  for (const r of list) {
    const code = String(r?.store_code || '').trim()
    // A closed store must not gain new cameras, the same rule the HR pages apply to new hires.
    if (!code || r?.is_active === false || seen.has(code)) continue
    seen.add(code)
    const name = String(r?.address || r?.name || '').trim()
    out.push({ code, label: name && name !== code ? `${code} — ${name}` : code })
  }
  return out.sort((a, b) => a.code.localeCompare(b.code))
}

/**
 * The options to show for a field currently holding `value`.
 *
 * A camera assigned to a store that has since closed — or to a code typed before this dropdown
 * existed — must still show what it is set to. Dropping it would silently re-label the camera as
 * unassigned the next time anyone opened the page, and the first sign would be missing traffic.
 */
export function withCurrent(stores: StoreOption[], value?: string | null): StoreOption[] {
  const cur = String(value || '').trim()
  if (!cur || stores.some(s => s.code === cur)) return stores
  return [...stores, { code: cur, label: `${cur} — not in the store list` }]
}

// One registered edge analyzer, as /vision/edge-agents lists it. The secret is never in here — it
// exists in plaintext exactly once, in the response to registering or rotating.
export interface EdgeAgent {
  id: string
  agent_key: string
  label?: string | null
  store_code?: string | null
  enabled?: boolean
  version?: string | null
  last_seen_at?: string | null
  last_ingest_at?: string | null
  online?: boolean
  // Registered, but the machine has not yet traded its enrollment code for a secret.
  awaiting_enrollment?: boolean
}

/**
 * The commit this bundle was BUILT from, or '' when the platform did not say.
 *
 * "Is the page I am looking at the code we just shipped?" was unanswerable three separate times
 * while debugging live video, and each time we guessed. The backend answers it at /health; this is
 * the same answer for the bundle. Inlined at build time on purpose — a cached page reports the
 * commit it was BUILT from, not the one currently deployed, which is exactly what makes a stale
 * bundle visible. When this and /health disagree, one side has not finished deploying or the
 * browser is holding an old page.
 *
 * Vercel injects this automatically; the `env` key in next.config would also work but is marked
 * legacy in this Next version, and no config is needed for a variable the platform already exports.
 * Blank means the platform did not provide it (system environment variables switched off in the
 * project settings) — blank is not a failure, it just means no stamp.
 */
export function buildSha(): string {
  return (process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA || '').slice(0, 7)
}
