'use client'
import React from 'react'
import { DrillTileLinks, type DrillRow } from '@/components/DrillDownPanel'

// A TILE THAT CAN BE OPENED — one home for the closing day-recons' stat tile (§CORE-17.3c).
//
// The ePay bank-deposit recon and the accessory recon each carried a byte-identical `Stat`, and
// adding the drill-down affordance would have meant editing both the same way — the sibling defect
// the house rules forbid, in the smallest possible form. One component, imported by both.
export function DrillStat({ label, value, color, drills, drill }: {
  label: string
  value: string
  color?: string
  /** The figures on this tile that have rows behind them, from `useDrillTile().dl(...)`. */
  drills?: { key: string; label: string }[]
  drill?: { open: string | null; toggle: (k: string) => void }
}) {
  return <div className="card" style={{ padding: '14px 16px' }}>
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: color || 'var(--text1)' }}>{value}</div>
    {/* A tile the server served no panel for offers no link, rather than a link that opens nothing. */}
    {drill && drills && drills.length ? <DrillTileLinks drills={drills} open={drill.open} onToggle={drill.toggle} /> : null}
  </div>
}

// THE COLUMNS FOR A STORE-DAY DRILL ROW — the client side of `closing/day_recon_drilldown._row_fields`.
// The server declares what identifies a row in ONE function; this is the ONE place that renders it, so
// a field added there is added here rather than in each page's own column list.
export const STORE_DRILL_COLUMNS = [
  { header: 'Store code', get: (r: DrillRow) => String(r.fields?.store_code || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.store_code || '') },
  { header: 'People', get: (r: DrillRow) => String(r.fields?.rep_names || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.rep_names || '') },
]
