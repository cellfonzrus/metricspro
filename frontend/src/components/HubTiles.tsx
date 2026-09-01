'use client'
// Collapsed master tiles for the Payroll (/payroll) and Workforce (/storeops) hub dashboards
// (Phase W2.1, owner feedback 2026-09-01: "the master tile should hide the interior tiles").
//
// Each master tile renders as ONE card — icon + title + one-line desc + a subtle count of the pages
// inside. The interior links stay HIDDEN until the tile is clicked, then expand IN PLACE inside the
// card (accordion; the chevron rotates). Toggles are INDEPENDENT per tile — opening one never closes
// another — and nothing is persisted: every visit starts collapsed. A tile with exactly ONE interior
// link is a plain <Link> that navigates DIRECTLY (no pointless expand step). Multi-link tiles use a
// real <button> header (Enter/Space toggle, aria-expanded) so the accordion is keyboard accessible.
// Styling stays in the hubs' existing card language: className="card", var(--border)/var(--text2|3).
import { useState } from 'react'
import Link from 'next/link'

export type HubItem = { href: string; icon: string; label: string; desc: string }
export type HubGroup = { title: string; icon: string; desc: string; items: HubItem[] }

const chevron = (open: boolean) => (
  <span aria-hidden style={{ fontSize: 10, color: 'var(--text3)', display: 'inline-block', flexShrink: 0,
    transition: 'transform 0.15s ease', transform: open ? 'rotate(90deg)' : 'none', marginTop: 4 }}>▶</span>
)

function TileHead({ g, open }: { g: HubGroup; open?: boolean }) {
  const n = g.items.length
  return (
    <>
      <div style={{ fontSize: 24, lineHeight: 1, flexShrink: 0 }}>{g.icon}</div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 14.5, fontWeight: 700, marginBottom: 2, color: 'var(--text1, inherit)' }}>{g.title}</div>
        <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.4 }}>{g.desc}</div>
        <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 5 }}>
          {n === 1 ? 'Open →' : `${n} pages`}
        </div>
      </div>
      {n > 1 && chevron(!!open)}
    </>
  )
}

function Tile({ g }: { g: HubGroup }) {
  // Per-tile expand state — deliberately plain useState (owner spec: persist nothing).
  const [open, setOpen] = useState(false)

  // Single-link master tile → the tile IS the link; clicking navigates directly.
  if (g.items.length === 1) {
    return (
      <Link href={g.items[0].href} className="card" style={{
        padding: 14, display: 'flex', gap: 12, alignItems: 'flex-start',
        textDecoration: 'none', color: 'inherit', border: '1px solid var(--border)',
      }}>
        <TileHead g={g} />
      </Link>
    )
  }

  return (
    <div className="card" style={{ border: '1px solid var(--border)', padding: 0, overflow: 'hidden' }}>
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        style={{ width: '100%', padding: 14, display: 'flex', gap: 12, alignItems: 'flex-start',
          textAlign: 'left', background: 'none', border: 'none', cursor: 'pointer',
          font: 'inherit', color: 'inherit' }}>
        <TileHead g={g} open={open} />
      </button>
      {open && (
        <div style={{ borderTop: '1px solid var(--border)', padding: '6px 8px 8px' }}>
          {g.items.map(it => (
            <Link key={it.href} href={it.href} style={{
              display: 'flex', gap: 10, alignItems: 'flex-start', padding: '8px 8px',
              borderRadius: 8, textDecoration: 'none', color: 'inherit',
            }}>
              <span style={{ fontSize: 16, lineHeight: 1.2, flexShrink: 0 }}>{it.icon}</span>
              <span style={{ minWidth: 0 }}>
                <span style={{ display: 'block', fontSize: 13, fontWeight: 600 }}>{it.label}</span>
                <span style={{ display: 'block', fontSize: 11.5, color: 'var(--text3)', lineHeight: 1.4 }}>{it.desc}</span>
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

export default function HubTiles({ groups }: { groups: HubGroup[] }) {
  // alignItems:'start' so one expanded tile grows alone instead of stretching its whole grid row.
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
      gap: 12, alignItems: 'start' }}>
      {groups.map(g => <Tile key={g.title} g={g} />)}
    </div>
  )
}
