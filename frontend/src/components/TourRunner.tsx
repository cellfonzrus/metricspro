'use client'
// ── TOUR RUNNER — the guided walk-through overlay (owner directive 2026-08-04) ───────────────────
// Spotlights a real control on the real page, explains it in one card, and steps forward. The page
// underneath stays fully interactive: the dimming layer is pointer-events:none, so this is a guided
// SIMULATION the user actually performs, not a lightbox they watch.
//
// WHERE IT LIVES, AND WHY. It is rendered by HelpPanel, which is mounted in the platform LAYOUT — so
// it exists on every page and SURVIVES client-side navigation. That is what lets one tour walk a user
// across /closing/verify → /closing/envelope-payout without a global provider and WITHOUT editing
// layout.tsx (a SHARED file). Nothing else in the app knows this component exists.
//
// RESILIENCE RULES (this walks pages other module agents own, whose markup moves between waves):
//   • any anchor that fails to resolve falls back to a centered card — a moved button downgrades ONE
//     step and never breaks the tour;
//   • the anchor is re-resolved on a slow poll, so a late-rendering page, a scroll or a resize is
//     picked up without a single event listener on the host page;
//   • every failure path (missing tour, un-run migration, offline) ends the tour silently.
// It never clicks, types or submits for the user.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import {
  TOUR_START_EVENT, Tour, TourStep, fetchTour, markTourDone, resolveAnchor, samePage,
} from '@/lib/tours'
import { isSafeHref } from '@/lib/safe-url'

type Rect = { top: number; left: number; width: number; height: number }
const PAD = 6                 // spotlight padding around the anchor
const CARD_W = 360
const POLL_MS = 200           // re-resolve cadence: covers late render, scroll, resize and re-layout

function rectOf(el: HTMLElement): Rect {
  const r = el.getBoundingClientRect()
  return { top: r.top, left: r.left, width: r.width, height: r.height }
}
function sameRect(a: Rect | null, b: Rect | null) {
  if (!a || !b) return a === b
  return Math.abs(a.top - b.top) < 1 && Math.abs(a.left - b.left) < 1
    && Math.abs(a.width - b.width) < 1 && Math.abs(a.height - b.height) < 1
}

export default function TourRunner() {
  const router = useRouter()
  const pathname = usePathname() || '/'
  const [tour, setTour] = useState<Tour | null>(null)
  const [steps, setSteps] = useState<TourStep[]>([])
  const [idx, setIdx] = useState(0)
  const [rect, setRect] = useState<Rect | null>(null)
  const [missing, setMissing] = useState(false)     // anchor could not be found (yet) → centered card
  const scrolledFor = useRef<number>(-1)
  const active = !!tour && steps.length > 0

  const step: TourStep | null = active ? steps[Math.min(idx, steps.length - 1)] : null

  const end = useCallback((completed: boolean) => {
    if (completed && tour?.slug) markTourDone(tour.slug)
    setTour(null); setSteps([]); setIdx(0); setRect(null); setMissing(false)
    scrolledFor.current = -1
  }, [tour])

  const begin = useCallback(async (slug: string, startAt = 0) => {
    const t = await fetchTour(slug)
    if (!t) return                       // no such tour / mig 720 un-run → silently do nothing
    setTour(t.tour); setSteps(t.steps)
    setIdx(Math.max(0, Math.min(startAt, t.steps.length - 1)))
    setRect(null); setMissing(false); scrolledFor.current = -1
  }, [])

  // ── Launch: the custom event any page can fire via startTour(slug) ─────────────────────────────
  useEffect(() => {
    const onStart = (e: Event) => {
      const d = (e as CustomEvent).detail || {}
      if (d.slug) begin(String(d.slug), Number(d.step) || 0)
    }
    window.addEventListener(TOUR_START_EVENT, onStart as EventListener)
    return () => window.removeEventListener(TOUR_START_EVENT, onStart as EventListener)
  }, [begin])

  // ── Deep link: ?tour=<slug> on ANY page (including a cold reload) ──────────────────────────────
  // Read straight off window.location instead of useSearchParams(), which would force every page under
  // this layout into a Suspense boundary at build time.
  useEffect(() => {
    if (active) return
    try {
      const sp = new URLSearchParams(window.location.search)
      const slug = sp.get('tour')
      if (!slug) return
      const stepParam = Number(sp.get('step') || 0)
      // Strip the params so a refresh (or a back-navigation) doesn't relaunch the tour forever.
      sp.delete('tour'); sp.delete('step')
      const qs = sp.toString()
      window.history.replaceState({}, '', window.location.pathname + (qs ? '?' + qs : ''))
      begin(slug, stepParam > 0 ? stepParam - 1 : 0)
    } catch { /* never break a page over a query string */ }
  }, [pathname, active, begin])

  // ── Navigate to the step's page when it isn't the page we're on ────────────────────────────────
  useEffect(() => {
    if (!step?.page_href) return
    // H6 (2026-08-05 audit): `page_href` is TENANT-WRITABLE config and this navigation is
    // AUTOMATIC — `?tour=<slug>` on any page launches the tour with no click, so a stored
    // `javascript:` value self-fires and steals the localStorage JWT + 2FA marker. Router.push of
    // an off-origin URL degrades to a location assignment, which executes it. Refuse to navigate;
    // the step still renders (as a centered card), matching every other 'anchor did not resolve'
    // degrade in this component. Sanitised on the write side too (core/safe_href.py).
    if (!isSafeHref(step.page_href)) return
    // EXACT page comparison, not a prefix: stepping BACK from /commcalc/daily-commission to a
    // /commcalc step must navigate back, which a prefix match would swallow.
    if (samePage(step.page_href, pathname)) return
    router.push(step.page_href)
  }, [step, pathname, router])

  // ── Locate + track the anchor (one slow poll covers late render, scroll and resize) ────────────
  useEffect(() => {
    if (!active || !step) return
    let alive = true
    const tick = () => {
      if (!alive) return
      const el = step.target ? resolveAnchor(step.target) : null
      if (!el) { setRect(r => (r === null ? r : null)); setMissing(true); return }
      setMissing(false)
      const next = rectOf(el)
      setRect(prev => (sameRect(prev, next) ? prev : next))
      if (scrolledFor.current !== idx) {
        scrolledFor.current = idx
        try { el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' }) } catch { /* older browsers */ }
      }
    }
    tick()
    const h = window.setInterval(tick, POLL_MS)
    return () => { alive = false; window.clearInterval(h) }
  }, [active, step, idx])

  // ── Keyboard: → next, ← back, Esc exit ─────────────────────────────────────────────────────────
  const next = useCallback(() => {
    setIdx(i => {
      if (i + 1 >= steps.length) { end(true); return i }
      scrolledFor.current = -1
      return i + 1
    })
  }, [steps.length, end])
  const back = useCallback(() => setIdx(i => { scrolledFor.current = -1; return Math.max(0, i - 1) }), [])

  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); end(false) }
      else if (e.key === 'ArrowRight') { e.preventDefault(); next() }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); back() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, next, back, end])

  // ── Card placement ────────────────────────────────────────────────────────────────────────────
  const cardPos = useMemo<React.CSSProperties>(() => {
    if (!rect) {
      return { top: '50%', left: '50%', transform: 'translate(-50%,-50%)', width: CARD_W }
    }
    const vw = typeof window !== 'undefined' ? window.innerWidth : 1200
    const vh = typeof window !== 'undefined' ? window.innerHeight : 800
    const below = rect.top + rect.height + PAD + 12
    const roomBelow = vh - (rect.top + rect.height)
    const placeBelow = roomBelow > 240 || rect.top < 240
    const top = placeBelow ? below : Math.max(12, rect.top - PAD - 12)
    const left = Math.min(Math.max(12, rect.left), Math.max(12, vw - CARD_W - 12))
    return placeBelow
      ? { top, left, width: CARD_W }
      : { top, left, width: CARD_W, transform: 'translateY(-100%)' }
  }, [rect])

  if (!active || !step) return null
  const total = steps.length
  const last = idx >= total - 1

  return (
    <div data-mp-tour="1">
      {/* Dimming + spotlight. pointer-events:none everywhere, so the page underneath stays usable —
          the user really does perform the step. */}
      <div style={{ position: 'fixed', inset: 0, zIndex: 1400, pointerEvents: 'none' }}>
        {rect ? (
          <div style={{
            position: 'fixed',
            top: rect.top - PAD, left: rect.left - PAD,
            width: rect.width + PAD * 2, height: rect.height + PAD * 2,
            borderRadius: 10, border: '2px solid #2563eb',
            boxShadow: '0 0 0 9999px rgba(15,23,42,0.55)', transition: 'all 140ms ease-out',
          }} />
        ) : (
          <div style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)' }} />
        )}
      </div>

      {/* The step card */}
      <div role="dialog" aria-live="polite" aria-label={`Walk-through step ${idx + 1} of ${total}`}
        style={{
          position: 'fixed', zIndex: 1401, background: 'white', borderRadius: 14,
          boxShadow: '0 18px 50px rgba(0,0,0,0.32)', padding: '16px 18px 14px',
          maxWidth: '92vw', ...cardPos,
        }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase',
            color: '#2563eb' }}>
            {tour?.title}
          </span>
          <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text3)' }}>{idx + 1} / {total}</span>
          <button onClick={() => end(false)} aria-label="Leave the walk-through"
            style={{ background: 'none', border: 'none', fontSize: 18, lineHeight: 1, cursor: 'pointer',
              color: 'var(--text3)' }}>×</button>
        </div>

        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 5 }}>{step.title}</div>
        <div style={{ fontSize: 13.5, lineHeight: 1.6, color: 'var(--text2)' }}>{step.body}</div>

        {missing && step.target && (
          <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 8, lineHeight: 1.5 }}>
            Looking for this on the page… if you can&apos;t see it, it may be hidden until you pick a
            store or a date, or your role may not have it.
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 14 }}>
          <div style={{ display: 'flex', gap: 4 }}>
            {steps.map((s, i) => (
              <span key={s.id || i} aria-hidden
                style={{ width: i === idx ? 16 : 6, height: 6, borderRadius: 4,
                  background: i === idx ? '#2563eb' : i < idx ? '#93c5fd' : 'var(--border)',
                  transition: 'width 120ms ease-out' }} />
            ))}
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
            {idx > 0 && (
              <button onClick={back} className="btn btn-secondary" style={{ fontSize: 12.5 }}>← Back</button>
            )}
            <button onClick={next} className="btn btn-primary" style={{ fontSize: 12.5 }}>
              {last ? 'Done' : 'Next →'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
