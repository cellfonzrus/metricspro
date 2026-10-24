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

// THE PICKUP SCREENS' STAT TILE — one home for the other closing tile shape (§CORE-17.3e).
//
// `/closing/pickup` and `/closing/billpay-pickup` are one mechanism wearing two screens (owner
// directive 2026-09-02: "the same process same wiring"), and each carried a byte-identical `Stat`
// of its own. Adding the drill affordance would have meant editing both the same way — the sibling
// defect in its smallest form, which is how it reappeared here after `DrillStat` above fixed it for
// the day recons. It is a separate component from `DrillStat` rather than a flag on it because the
// tile really is different (a larger figure, an accent rule, its own minimum width); what must not
// be different is how many copies of it exist.
export function DrillPickupStat({ label, value, sub, accent, color, drills, drill }: {
  label: string
  value: string
  sub?: string
  accent?: boolean
  color?: string
  /** The figures on this tile that have rows behind them, from `useDrillTile().dl(...)`. */
  drills?: { key: string; label: string }[]
  drill?: { open: string | null; toggle: (k: string) => void }
}) {
  return <div className="card" style={{ padding: '12px 16px', minWidth: 150, flex: '0 1 auto', borderTop: accent ? '3px solid var(--accent)' : undefined }}>
    <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
    <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2, color: color || undefined }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
    {/* A figure the server served no panel for offers no link, rather than one that opens nothing. */}
    {drill && drills && drills.length ? <DrillTileLinks drills={drills} open={drill.open} onToggle={drill.toggle} /> : null}
  </div>
}

// THE COLUMNS FOR AN ENVELOPE DRILL ROW — the client side of `closing/pickup_drilldown._row_fields`,
// shared by both pickup screens for the same reason the tile is.
export const ENVELOPE_DRILL_COLUMNS = [
  { header: 'Store code', get: (r: DrillRow) => String(r.fields?.store_code || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.store_code || '') },
  { header: 'Filed by', get: (r: DrillRow) => String(r.fields?.employee_name || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.employee_name || '') },
  { header: 'Envelope', get: (r: DrillRow) => String(r.fields?.state || '—'),
    sortValue: (r: DrillRow) => String(r.fields?.state || '') },
]
