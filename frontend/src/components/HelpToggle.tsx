'use client'
// Global "Help text" toggle for the nav — the one control that reveals the gated on-page explanations across
// the whole app (owner 2026-08-29). Renders ONLY for approved roles (admin/owner); everyone else sees nothing
// and can never surface the comments. Flipping it sets <html data-help="on"> via help-context, which the
// global `.pg-note` CSS rule uses to show/hide every page's intro banner at once.
import { useHelp } from '@/lib/help-context'

export default function HelpToggle({ collapsed }: { collapsed?: boolean }) {
  const { canSee, enabled, toggle } = useHelp()
  if (!canSee) return null
  const on = enabled
  if (collapsed) {
    return (
      <button className="mp-icon-btn" onClick={toggle} title={on ? 'Hide help text' : 'Show help text'}
        aria-label="Toggle help text" style={{ margin: '0 auto 8px', fontSize: 13, opacity: on ? 1 : 0.55 }}>ⓘ</button>
    )
  }
  return (
    <div style={{ padding: '0 12px 10px' }}>
      <button onClick={toggle} title="Show/hide the explanatory text on each page"
        style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '5px 9px', borderRadius: 7,
          cursor: 'pointer', fontSize: 12, color: 'rgba(255,255,255,0.72)', background: on ? 'rgba(255,255,255,0.12)' : 'transparent',
          border: '1px solid rgba(255,255,255,0.14)' }}>
        <span aria-hidden>ⓘ</span>
        <span style={{ flex: 1, textAlign: 'left' }}>Help text</span>
        <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.04em',
          color: on ? '#86efac' : 'rgba(255,255,255,0.4)' }}>{on ? 'ON' : 'OFF'}</span>
      </button>
    </div>
  )
}
