'use client'
// StatTile — the one bento KPI tile (2026-08-29 design polish). Replaces the per-page hand-rolled
// "emoji + big coloured number" cards. Restrained by design: the label is small and tracked, the value is
// a large near-black tabular figure, and COLOUR is spent on the accent hairline / icon chip / delta pill —
// never on the whole number, which is the thing that makes generated dashboards look generated. Everything
// is optional except `label` + `value`, so it drops in anywhere a metric needs to be shown.
import { ReactNode } from 'react'
import Link from 'next/link'

export type StatDelta = { value: ReactNode; dir?: 'up' | 'down' | 'flat' }

export default function StatTile({
  label, value, sub, icon, accent, delta, hero, href, onClick,
}: {
  label: ReactNode
  value: ReactNode
  sub?: ReactNode
  icon?: ReactNode
  accent?: string            // a CSS colour for the hairline + icon chip tint (e.g. 'var(--green)')
  delta?: StatDelta
  hero?: boolean             // spans two columns + larger figure, for the headline metric
  href?: string              // renders the tile as a link
  onClick?: () => void
}) {
  const cls = `stat${hero ? ' stat-hero' : ''}${(href || onClick) ? ' is-link' : ''}`
  const style = accent ? ({ ['--stat-accent' as any]: accent } as React.CSSProperties) : undefined

  const inner = (
    <>
      <div className="stat-top">
        <span className="stat-label">{label}</span>
        {icon != null && <span className="stat-icon" aria-hidden>{icon}</span>}
      </div>
      <div className="stat-value">{value}</div>
      {sub != null && <div className="stat-sub">{sub}</div>}
      {delta && (
        <span className={`stat-delta ${delta.dir || 'flat'}`}>
          {delta.dir === 'up' ? '▲' : delta.dir === 'down' ? '▼' : ''} {delta.value}
        </span>
      )}
    </>
  )

  if (href) return <Link href={href} className={cls} style={style}>{inner}</Link>
  if (onClick) return <button type="button" className={cls} style={{ ...style, textAlign: 'left', font: 'inherit' }} onClick={onClick}>{inner}</button>
  return <div className={cls} style={style}>{inner}</div>
}
