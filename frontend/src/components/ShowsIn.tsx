'use client'
// ── "THIS UPLOAD WILL SHOW IN: …" — one component, every upload surface ──────────────────────────
// Owner (2026-09-20): "need to know where the data is uploaded and it should be mentioned on the
// upload page where this upload will be reflected, with a link. If the user does not know and
// uploads the data it does no good."
//
// The answer is DERIVED, never listed here: the backend's report-kind payload carries `shows_in` per
// registry row (landing_identity.shows_in — registry row → landing table → the ONE consumers map),
// the page reaches it through useReportKinds().showsIn(...) and hands it to this component. Each
// consumer is a ScreenLink screen key, so the link is the sidebar's own href, self-gated by RBAC —
// no href is spelled here and no page keeps a consumers list of its own
// (backend/harness_landing_identity_lock.py fails the build on one).
import type { CSSProperties } from 'react'
import ScreenLink, { SCREENS, type ScreenKey } from '@/components/ScreenLink'
import type { ShowsIn as ShowsInPayload } from '@/lib/report-kinds'

const wrap: CSSProperties = { fontSize: 12, color: 'var(--text2)', margin: '4px 0 8px', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'baseline' }
const pill: CSSProperties = { background: 'var(--bg,rgba(37,99,235,.06))', border: '1px solid var(--border)', borderRadius: 999, padding: '1px 8px', whiteSpace: 'nowrap' }

function isScreenKey(k: string): k is ScreenKey {
  return Object.prototype.hasOwnProperty.call(SCREENS, k)
}

/**
 * Render "This upload will show in: [A] [B] …". `loaded` false → a quiet "working out where this
 * shows…" (never a guess); a kind with no consumers says so in the registry's own words.
 */
export default function ShowsIn({ info, loaded = true, lead = 'This upload will show in', compact = false, style }:
  { info: ShowsInPayload | null | undefined; loaded?: boolean; lead?: string; compact?: boolean; style?: CSSProperties }) {
  if (!loaded) return <div style={{ ...wrap, ...style }}>{lead}: working out where this shows…</div>
  if (!info) return null
  const cons = info.consumers || []
  if (!cons.length) {
    return <div style={{ ...wrap, ...style }}>{lead}: <span style={{ color: '#b45309' }}>{info.note || 'no report reads this yet'}</span></div>
  }
  const shown = compact ? cons.slice(0, 4) : cons
  return (
    <div style={{ ...wrap, ...style }} data-shows-in={info.table || ''}>
      <span>{lead}:</span>
      {shown.map(c => (
        <span key={c.screen} style={pill} title={[c.why || '', c.needs?.length ? `needs ${c.needs.join(' / ')}` : ''].filter(Boolean).join(' — ')}>
          {isScreenKey(c.screen) ? <ScreenLink to={c.screen}>{c.label}</ScreenLink> : c.label}
          {c.gate && <span title="this reader gates the landing: a file blank on every field it needs is refused" style={{ marginLeft: 3, color: 'var(--text3)' }}>*</span>}
        </span>
      ))}
      {compact && cons.length > shown.length && <span style={{ color: 'var(--text3)' }}>+{cons.length - shown.length} more</span>}
      {info.table && <span style={{ color: 'var(--text3)' }}>· lands in <code style={{ fontSize: 11 }}>{info.table}</code></span>}
    </div>
  )
}
