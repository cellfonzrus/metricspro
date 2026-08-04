// ── WHAT'S NEW — new features + improvements for ADMIN STAFF (mig 721) ──────────────────────────
// Owner directive 2026-08-04: "like we have the warnings for the admin who logs in, there should be 2
// more areas new features and improvements and keep them logged somewhere only for admin staff."
//
// The login popup already shows WARNINGS. This adds the two other panes beside them, and a permanent
// log at /admin/whats-new. Same audience, same gate — a rep never sees any of it.
//
// FAIL-SILENT by contract, exactly like the attention feed: an un-run migration, a 403 or an offline
// client leaves the extra tabs invisible. It can never break the popup or the page it sits on.
import { api } from '@/lib/client'

export type ReleaseNote = {
  id?: string
  org_id?: string
  slug: string
  category: 'new_feature' | 'improvement' | 'fix'
  module?: string | null
  title: string
  body?: string | null
  status?: 'shipped' | 'in_progress'
  deep_link?: string | null
  released_at: string        // YYYY-MM-DD
  is_published?: boolean
  is_seed?: boolean
  updated_by?: string | null
}

export type NoteCounts = { new_feature: number; improvement: number; fix: number; total: number }

export type WhatsNewPayload = {
  entries: ReleaseNote[]
  unseen: ReleaseNote[]
  counts: NoteCounts
  unseen_counts: NoteCounts
  ready: boolean
  can_edit: boolean
  is_super?: boolean
  hint?: string
}

export const CATEGORY_LABEL: Record<string, string> = {
  new_feature: 'New features',
  improvement: 'Improvements',
  fix: 'Fixes',
}
export const CATEGORY_ICON: Record<string, string> = {
  new_feature: '✨', improvement: '⬆️', fix: '🔧',
}
export const CATEGORY_ORDER: ReleaseNote['category'][] = ['new_feature', 'improvement', 'fix']

// ── "Last time I looked" watermark (v1 = localStorage, per browser) ──────────────────────────────
// Keyed per acting tenant so a super-admin switching tenants gets that tenant's unseen set.
// UPGRADE PATH (documented in mig 721): add core.release_note_seen (org_id, user_id, last_seen_at) +
// a two-route read/write and swap these two helpers. Nothing else in the feature changes.
const SEEN_KEY = 'mp_whats_new_seen_'

export function lastSeen(org?: string | null): string {
  try { return window.localStorage.getItem(SEEN_KEY + (org || 'default')) || '' } catch { return '' }
}
export function markSeen(org?: string | null, upTo?: string) {
  try {
    const d = upTo || new Date().toISOString().slice(0, 10)
    window.localStorage.setItem(SEEN_KEY + (org || 'default'), d)
  } catch { /* private mode — the tabs simply keep showing as unseen */ }
}

// ── API ──────────────────────────────────────────────────────────────────────────────────────────
const EMPTY_COUNTS: NoteCounts = { new_feature: 0, improvement: 0, fix: 0, total: 0 }
export const EMPTY_PAYLOAD: WhatsNewPayload = {
  entries: [], unseen: [], counts: EMPTY_COUNTS, unseen_counts: EMPTY_COUNTS,
  ready: false, can_edit: false,
}

export async function fetchWhatsNew(opts?: {
  since?: string; category?: string; module?: string; from?: string; to?: string
}): Promise<WhatsNewPayload> {
  const qs = new URLSearchParams()
  if (opts?.since) qs.set('since', opts.since)
  if (opts?.category) qs.set('category', opts.category)
  if (opts?.module) qs.set('module', opts.module)
  if (opts?.from) qs.set('from_date', opts.from)
  if (opts?.to) qs.set('to_date', opts.to)
  try {
    // Explicit /api/v1 prefix — a bare path passes a curl check and 404s silently in the UI.
    const r = await api(`/api/v1/core/whats-new${qs.toString() ? '?' + qs : ''}`)
    return {
      entries: r?.entries || [], unseen: r?.unseen || [],
      counts: r?.counts || EMPTY_COUNTS, unseen_counts: r?.unseen_counts || EMPTY_COUNTS,
      ready: !!r?.ready, can_edit: !!r?.can_edit, is_super: !!r?.is_super, hint: r?.hint,
    }
  } catch {
    return EMPTY_PAYLOAD     // FAIL-SILENT: 403 / un-run migration / offline → the tabs stay hidden
  }
}
