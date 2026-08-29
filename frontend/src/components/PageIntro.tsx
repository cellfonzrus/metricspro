'use client'
// PageIntro — the standard page header. Renders a clean <h1> always; the explanatory `help` text renders
// ONLY when an approved role has help turned on (see help-context). For approved users with help off, a
// small ⓘ affordance appears so they can flip it on inline; everyone else just sees the title. This is how
// the app reads as a finished product by default while the built-in guidance stays one click away for
// admins/owners.
import { ReactNode } from 'react'
import { useHelp } from '@/lib/help-context'

export default function PageIntro({ title, help, right }: { title: ReactNode; help?: ReactNode; right?: ReactNode }) {
  const { show, canSee, toggle } = useHelp()
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 21, fontWeight: 700, margin: 0, letterSpacing: '-0.01em' }}>{title}</h1>
        {canSee && help && (
          <button onClick={toggle} title={show ? 'Hide help' : 'Show help'} aria-label="Toggle help text"
            style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--text3)',
              fontSize: 13, lineHeight: 1, padding: 2, borderRadius: 4 }}>ⓘ</button>
        )}
        {right && <div style={{ marginLeft: 'auto' }}>{right}</div>}
      </div>
      {show && help && (
        <p style={{ color: 'var(--text2)', fontSize: 13, margin: '6px 0 0', maxWidth: 820, lineHeight: 1.5 }}>{help}</p>
      )}
    </div>
  )
}
