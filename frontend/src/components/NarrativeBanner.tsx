'use client'
// NarrativeBanner — a plain-English summary rendered above a report (owner 2026-08-29 modernization
// track). It fetches a report's `…/narrative` endpoint, which returns a DETERMINISTIC headline + bullets
// computed from the same numbers the report shows (no LLM, no API key, cannot contradict the grid). The
// tone ('up' | 'down' | 'flat') keys the accent so a good move reads green and a bad one red. Reusable:
// point it at any narrative endpoint URL; it renders nothing until that endpoint says `available`.
import { useState, useEffect } from 'react'
import { api } from '@/lib/client'

type Narrative = {
  available?: boolean
  tone?: 'up' | 'down' | 'flat'
  headline?: string
  bullets?: string[]
}

// Tone → (accent, tint, glyph). Kept close to the app's semantic palette; flat is a quiet neutral so an
// unchanged month doesn't shout.
const TONE = {
  up:   { accent: 'var(--green)', tint: 'color-mix(in srgb, var(--green) 8%, var(--surface))', glyph: '▲' },
  down: { accent: 'var(--red)',   tint: 'color-mix(in srgb, var(--red) 8%, var(--surface))',   glyph: '▼' },
  flat: { accent: 'var(--text3)', tint: 'var(--surface2)',                                       glyph: '•' },
} as const

export default function NarrativeBanner({ url }: { url: string | null }) {
  const [n, setN] = useState<Narrative | null>(null)

  useEffect(() => {
    let live = true
    setN(null)
    if (!url) return
    api(url).then((d) => { if (live) setN(d) }).catch(() => { if (live) setN(null) })
    return () => { live = false }
  }, [url])

  if (!n?.available || !n.headline) return null
  const tone = TONE[n.tone || 'flat']

  return (
    <div role="status" style={{
      background: tone.tint, border: '1px solid var(--border)', borderLeft: `3px solid ${tone.accent}`,
      borderRadius: 'var(--radius, 12px)', padding: '12px 16px', marginBottom: 14, boxShadow: 'var(--shadow-xs)',
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 9 }}>
        <span aria-hidden style={{ color: tone.accent, fontSize: 12, lineHeight: 1.5, flexShrink: 0 }}>{tone.glyph}</span>
        <p style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--text)', letterSpacing: '-0.01em', lineHeight: 1.45 }}>
          {n.headline}
        </p>
      </div>
      {n.bullets && n.bullets.length > 0 && (
        <ul style={{ margin: '7px 0 0', padding: '0 0 0 21px', display: 'flex', flexDirection: 'column', gap: 3 }}>
          {n.bullets.map((b, i) => (
            <li key={i} style={{ fontSize: 12.5, color: 'var(--text2)', lineHeight: 1.5 }}>{b}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
