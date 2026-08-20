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
