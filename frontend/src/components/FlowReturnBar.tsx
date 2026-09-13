'use client'
// THE WAY BACK, rendered once for the whole application.
//
// Owner 2026-09-13: "now since it brought me from the implementation wizard once im done here it
// shoudl take me back there, if im a new tenant i dont know how to navaagte."
//
// Mounted in (platform)/layout.tsx beside the other platform-level bars, so EVERY destination of
// EVERY hand-off gets the way back — no link site opts in, and a flow that ships next month is
// covered the day it exists. The decision is entirely in lib/flow-return.ts (PURE, proven in
// frontend/prove_flow_return.mjs); this file only reads the path, persists the trail, and paints.
//
// The visual is deliberately the SAME amber bar pos/layout.tsx already uses for "← Back to setup" —
// a second, differently-styled way of saying the same thing is how a UI stops being learnable.
//
// FAIL-SILENT: sessionStorage throws in private mode and can come back empty at any time. Every
// access is guarded and the bar simply does not render — a navigation aid must never break a page.
import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useAuth } from '@/lib/auth-context'
import { nextTrail, returnOffer, offerIsStale, type FlowTrail } from '@/lib/flow-return'

const KEY = 'mp_flow_trail'

function readTrail(): FlowTrail {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return null
    const d = JSON.parse(raw)
    return d && typeof d.path === 'string' ? { path: d.path, org: d.org ?? null } : null
  } catch { return null }
}

function writeTrail(t: FlowTrail) {
  try {
    if (t) sessionStorage.setItem(KEY, JSON.stringify(t))
    else sessionStorage.removeItem(KEY)
  } catch { /* private mode — the bar is simply absent this session */ }
}

export default function FlowReturnBar() {
  const pathname = usePathname()
  const { activeOrg } = useAuth()
  // Remember the flow as we pass through it. The reducer is PURE, so the trail is simply DERIVED
  // per navigation rather than mirrored into React state — which also keeps the effect below doing
  // the one thing an effect is for: syncing an external system (sessionStorage), never setState.
  // Dismissing CLEARS the trail rather than hiding a bar that is still armed, so "stop offering
  // this" survives the next navigation instead of springing back. `hidden` only covers the render
  // between the click and that navigation; it is set from an event, never from an effect.
  const [hidden, setHidden] = useState(false)
  const trail: FlowTrail = useMemo(() => {
    if (typeof window === 'undefined') return null      // no storage on the server ⇒ render nothing
    return nextTrail(readTrail(), pathname, activeOrg)
  }, [pathname, activeOrg])

  useEffect(() => { writeTrail(trail) }, [trail])

  const offer = returnOffer(trail, pathname, activeOrg)
  if (!offer || hidden) return null
  const stale = offerIsStale(offer)

  return (
    <div style={{ background: stale ? '#fef2f2' : '#fffbeb',
      borderBottom: `1px solid ${stale ? '#fecaca' : '#fde68a'}`, padding: '9px 24px',
      display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', fontSize: 13,
      color: stale ? '#991b1b' : '#92400e' }}>
      <span aria-hidden>{stale ? '⚠️' : '🧭'}</span>
      <span>{offer.note}</span>
      <span style={{ flex: 1 }} />
      {stale ? (
        <span style={{ fontWeight: 700 }}>{offer.label}</span>
      ) : (
        <Link href={offer.href} style={{ fontWeight: 700, color: '#92400e',
          border: '1px solid #fbbf24', borderRadius: 8, padding: '5px 12px',
          textDecoration: 'none', whiteSpace: 'nowrap' }}>
          {offer.label}
        </Link>
      )}
      <button onClick={() => { writeTrail(null); setHidden(true) }}
        title="Stop offering the way back to that setup flow"
        style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 16,
          color: 'inherit', opacity: 0.65, lineHeight: 1, padding: '0 2px' }}>×</button>
    </div>
  )
}
