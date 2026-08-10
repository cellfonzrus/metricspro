// ── TRAINING TOURS — the in-house guided walk-through engine's shared brain ──────────────────────
// Owner directive 2026-08-04: "need to create simulation training videos for all modules to walk the
// users through". Phase 1 is an interactive SIMULATION: the app spotlights a real control on a real
// page, explains it in one short card, and steps forward. The user drives it, on their own tenant's
// data. Phase 2 (scaffold, backend only) exports the same step rows as a video recording script.
//
// NO EXTERNAL DEPENDENCY. No CDN, no tour library, no new package — this file plus TourRunner.tsx is
// the whole engine (~an overlay, an anchor resolver and a step machine). That is deliberate: a help
// system that pulls a third-party script is a help system that breaks the app when the CDN does.
//
// TOURS ARE DATA (RULE TWO): the steps come from core.training_tour[_step] via
// GET /api/v1/core/training/tours (explicit /api/v1 prefix — a bare path 404s silently in the UI).
// Nothing about a tour's content lives in this file.
import { api } from '@/lib/client'
import { safeHref } from '@/lib/safe-url'

export type TourStep = {
  id?: string
  step_order: number
  page_href?: string | null
  target?: string | null
  target_fragile?: boolean
  placement?: 'auto' | 'top' | 'bottom' | 'left' | 'right'
  title: string
  body: string
  narration?: string | null
  action_hint?: string | null
}

export type Tour = {
  id?: string
  org_id?: string
  slug: string
  title: string
  module?: string | null
  description?: string | null
  audience?: string
  start_href?: string | null
  est_minutes?: number | null
  sort_order?: number
  is_published?: boolean
  is_seed?: boolean
  step_count?: number
  is_tenant_override?: boolean
  updated_by?: string | null
}

// ── Anchor resolution ────────────────────────────────────────────────────────────────────────────
// A step's `target` is deliberately forgiving, because most tours walk pages owned by OTHER module
// agents whose markup moves between waves:
//     tour:<id>   → [data-tour-id="<id>"]  — the stable anchor (pages platform-core owns carry these)
//     text:<str>  → the tightest VISIBLE element whose text contains <str>, case-insensitive
//     css:<sel>   → a raw CSS selector
//     <sel>       → a bare CSS selector (starts with # . [ or a tag)
//     null / ''   → no anchor at all: the step renders as a centered card
// ANY anchor that fails to resolve degrades to the centered card. A moved button downgrades one step;
// it can never break a tour, and it can never break the page underneath.
const TEXT_CANDIDATES = 'button,a,label,h1,h2,h3,h4,th,summary,legend,li,div,span,td,p,strong,b'

function isVisible(el: Element): boolean {
  const r = el.getBoundingClientRect()
  if (r.width < 2 || r.height < 2) return false
  const cs = window.getComputedStyle(el)
  if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) return false
  return true
}

// Never anchor onto the tour's own chrome (or the help panel that launched it).
function inOwnChrome(el: Element): boolean {
  return !!el.closest('[data-mp-tour]')
}

export function findByText(needle: string): HTMLElement | null {
  const want = needle.trim().toLowerCase()
  if (!want) return null
  let best: HTMLElement | null = null
  let bestScore = Number.POSITIVE_INFINITY
  const nodes = document.querySelectorAll<HTMLElement>(TEXT_CANDIDATES)
  nodes.forEach(el => {
    if (inOwnChrome(el)) return
    const txt = (el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase()
    if (!txt || !txt.includes(want)) return
    if (!isVisible(el)) return
    // Prefer the TIGHTEST match: fewest descendants, then the shortest text, then the smallest box.
    const r = el.getBoundingClientRect()
    const score = el.querySelectorAll('*').length * 1000 + Math.min(txt.length, 999) + r.width * r.height / 1e6
    if (score < bestScore) { bestScore = score; best = el }
  })
  return best
}

export function resolveAnchor(target?: string | null): HTMLElement | null {
  const t = (target || '').trim()
  if (!t) return null
  try {
    if (t.toLowerCase().startsWith('tour:')) {
      const id = t.slice(5).trim().replace(/"/g, '')
      const el = document.querySelector<HTMLElement>(`[data-tour-id="${id}"]`)
      return el && isVisible(el) ? el : null
    }
    if (t.toLowerCase().startsWith('text:')) return findByText(t.slice(5))
    const sel = t.toLowerCase().startsWith('css:') ? t.slice(4).trim() : t
    const el = document.querySelector<HTMLElement>(sel)
    return el && isVisible(el) && !inOwnChrome(el) ? el : null
  } catch {
    return null   // a malformed selector must never throw into the page
  }
}

// ── Page matching ────────────────────────────────────────────────────────────────────────────────
// Boundary-matched, never a sloppy startsWith: '/closing' matches '/closing' and '/closing/submit'
// but never '/closingx'.
export function samePage(a?: string | null, b?: string | null): boolean {
  const norm = (s?: string | null) => (String(s || '').split('?')[0].split('#')[0].replace(/\/+$/, '') || '/')
  const x = norm(a), y = norm(b)
  return x === y
}
export function pageMatches(href?: string | null, pathname?: string | null): boolean {
  const norm = (s?: string | null) => (String(s || '').split('?')[0].split('#')[0].replace(/\/+$/, '') || '/')
  const h = norm(href), p = norm(pathname)
  if (!href) return true          // a step with no page runs wherever the user already is
  return p === h || p.startsWith(h + '/')
}

// ── Completion tracking (v1 = localStorage) ──────────────────────────────────────────────────────
// UPGRADE PATH: this is per-browser, not per-user-per-tenant. When completion needs to be reportable
// ("which reps have done the closing walk-through?"), add core.training_progress (org_id, user_id,
// slug, completed_at) + POST /core/training/progress and have these two helpers read/write it, with
// localStorage kept as the offline fallback. Nothing else in the engine changes.
const DONE_PREFIX = 'mp_tour_done_'

export function markTourDone(slug: string) {
  try { window.localStorage.setItem(DONE_PREFIX + slug, new Date().toISOString()) } catch { /* private mode */ }
}
export function tourDoneAt(slug: string): string | null {
  try { return window.localStorage.getItem(DONE_PREFIX + slug) } catch { return null }
}
export function clearTourDone(slug: string) {
  try { window.localStorage.removeItem(DONE_PREFIX + slug) } catch { /* ignore */ }
}

// ── Launching ────────────────────────────────────────────────────────────────────────────────────
// The runner lives inside HelpPanel, which is mounted in the platform LAYOUT — so it is present on
// every page and, crucially, SURVIVES client-side navigation. That is what lets one tour walk the user
// across several pages without a global provider and without touching layout.tsx (a SHARED file).
export const TOUR_START_EVENT = 'mp-tour:start'

export function startTour(slug: string, opts?: { step?: number }) {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(TOUR_START_EVENT, { detail: { slug, step: opts?.step || 0 } }))
}

// ── API ──────────────────────────────────────────────────────────────────────────────────────────
export async function fetchTours(opts?: { path?: string; module?: string }):
  Promise<{ tours: Tour[]; ready: boolean; can_edit: boolean; hint?: string }> {
  const qs = new URLSearchParams()
  if (opts?.path) qs.set('path', opts.path)
  if (opts?.module) qs.set('module', opts.module)
  try {
    const r = await api(`/api/v1/core/training/tours${qs.toString() ? '?' + qs : ''}`)
    // H6: the Training Center renders `start_href` as a <Link> — scrub on arrival (see below).
    const tours: Tour[] = (r?.tours || []).map((t: Tour) => ({ ...t, start_href: safeHref(t.start_href) ?? null }))
    return { tours, ready: !!r?.ready, can_edit: !!r?.can_edit, hint: r?.hint }
  } catch {
    return { tours: [], ready: false, can_edit: false }   // FAIL-SILENT: help never breaks a page
  }
}

// H6 (2026-08-05 audit): tour hrefs are TENANT-WRITABLE config that the runner follows
// AUTOMATICALLY (`?tour=<slug>` needs no click), so an unsafe value is scrubbed the moment it
// arrives — one choke point for every consumer (the runner, the Training Center, the help panel).
// A scrubbed step keeps its title/body and simply renders where the user already is, which is the
// engine's existing 'anchor did not resolve' degrade. The write side rejects it too
// (app/modules/core/safe_href.py); this also covers rows written before that shipped.
export function sanitizeTourHrefs(t: { tour: Tour; steps: TourStep[] }): { tour: Tour; steps: TourStep[] } {
  return {
    tour: { ...t.tour, start_href: safeHref(t.tour.start_href) ?? null },
    steps: (t.steps || []).map(s => ({ ...s, page_href: safeHref(s.page_href) ?? null })),
  }
}

export async function fetchTour(slug: string): Promise<{ tour: Tour; steps: TourStep[] } | null> {
  try {
    const r = await api(`/api/v1/core/training/tours/${encodeURIComponent(slug)}`)
    if (!r?.tour || !Array.isArray(r?.steps) || !r.steps.length) return null
    return sanitizeTourHrefs({ tour: r.tour, steps: r.steps })
  } catch {
    return null
  }
}

// Group tours by module for the Training Center. Labels mirror the nav taxonomy; an unknown module
// key falls through to "Other" rather than disappearing.
export const MODULE_LABEL: Record<string, string> = {
  closing: 'Daily Closing',
  commissions: 'Commissions',
  storeops: 'StoreOps — people & hours',
  hr: 'HR / People',
  asset: 'Assets & Inventory',
  pos: 'Point of Sale',
  account: 'Finance & P&L',
  notify: 'Notifications',
  helpdesk: 'Helpdesk',
  admin: 'Administration',
  training: 'Training',
}
export const MODULE_ORDER = ['closing', 'commissions', 'pos', 'storeops', 'asset', 'account', 'hr',
  'notify', 'helpdesk', 'admin', 'training']

export function groupByModule(tours: Tour[]): { key: string; label: string; tours: Tour[] }[] {
  const seen = new Map<string, Tour[]>()
  tours.forEach(t => {
    const k = (t.module || '').trim() || 'other'
    if (!seen.has(k)) seen.set(k, [])
    seen.get(k)!.push(t)
  })
  const keys = [...MODULE_ORDER.filter(k => seen.has(k)), ...[...seen.keys()].filter(k => !MODULE_ORDER.includes(k))]
  return keys.map(k => ({ key: k, label: MODULE_LABEL[k] || (k === 'other' ? 'Other' : k), tours: seen.get(k)! }))
}

export const AUDIENCE_LABEL: Record<string, string> = {
  all: 'Everyone', rep: 'Sales reps', manager: 'Managers & DMs', admin: 'Administrators',
}
