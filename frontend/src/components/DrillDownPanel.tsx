'use client'
import { useMemo, useState } from 'react'
import { fmt } from '@/lib/client'
import { sourceLabel } from '@/lib/sourceLabels'
import { SortableTh, useTableSort } from '@/components/SortableTh'

// THE ROWS BEHIND A FIGURE, AND WHERE THEY CAME FROM — the ONE renderer (owner ask 2026-10-24,
// index §CORE-17). Server twin: `backend/app/modules/core/report_drilldown.py::panel`.
//
// Owner, verbatim: "cash variance at employee should have the drill down from wheter the data is
// cming from , update in index thta drilldown is needed for every report geenerated from any data
// which comming from within the platforma as a defalut".
//
// WHY THIS IS A COMPONENT AND NOT A BLOCK OF JSX IN ONE PAGE: the audit that came with the ask found
// 132 report pages and 73 of them summarise a figure with no way to the rows behind it. A per-page
// drill-down would be 73 shapes, 73 tie-outs and 73 different answers to "where is this from" — the
// sibling-derivation defect the index rules forbid, in display form. Every report that gains a
// drill-down renders it here.
//
// IT NAMES NO TABLE. A `source` arrives as the stored relation's real name and is printed through
// `sourceLabel()`, the one home for a feed's plain name (§19.38, owner: "hide database names from
// the users"). `harness_carrier_vocab_guard` §INFRA fails the build on a table name reaching copy.
//
// IT STATES A DISAGREEMENT. The server's `tie_out` says whether the rows add up to the figure; this
// renders that sentence as-is, including when they do NOT. A drill-down that quietly showed rows
// summing to something else would be worse than none: it would look like proof.
//
// ITS HEADERS SORT, through `useTableSort` and nothing of its own (owner directive 2026-08-10, "sort
// function by clicking on the header for all reports"; locked by `harness_table_sort_lock.py`). A
// drill-down is where someone hunts for the one row that explains the figure, so sorting matters more
// here than on a rollup, and being the ONE renderer means wiring it once covers every report that
// gains a drill-down. Sorting reorders the rows the server already sent — the tie-out is computed on
// the server over ALL of them, so no order can change whether the evidence adds up.

export type DrillOrigin = {
  table?: string | null; schema?: string | null; column?: string | null
  keys?: Record<string, string | number | null> | null; note?: string | null; traceable?: boolean
}
export type DrillRow = {
  label?: string; subject?: string | null; amount?: number | null; present?: boolean
  in_figure?: boolean; measured?: boolean; reason?: string | null; reason_label?: string | null
  origins?: DrillOrigin[]; sources?: string[]; fields?: Record<string, unknown> | null
  /** The natural key of the REPORT ROW this drill row explains (§CORE-17.3d) — what lets a screen
   *  that filters its own rows narrow the panel to the same selection its tile is folded over. */
  row_key?: Record<string, string | number | null> | null
}
export type DrillPanel = {
  schema?: number
  figure?: { label?: string; amount?: number; unit?: string }
  subject?: string | null; subject_kind?: string | null
  rows?: DrillRow[]; rows_shown?: number; rows_total_count?: number; capped?: boolean
  tie_out?: {
    figure?: number; rows_total?: number; difference?: number; ties?: boolean | null
    counted_rows?: number; unmeasured_rows?: number; shown_not_counted?: number; words?: string
    /** Set by `narrowPanel` when the figure is the SELECTION'S own total, so it cannot disagree with
     *  the rows below it — a sum that was composed, not one that was checked (§CORE-17.3d). */
    by_construction?: boolean
    /** The server's verdict over the whole range it was asked for, kept beside a narrowed one
     *  because that comparison WAS a check. */
    whole_range?: DrillPanel['tie_out'] | null
  }
  sources?: { table: string; rows: number }[]
  untraceable_origins?: number
  note?: string | null
}

/** A report's own extra column. `sortValue` is what its header sorts BY — a formatted cell is a
 *  React node and cannot be compared, so a column without one is explicitly `disabled` rather than
 *  silently sorting on nothing. */
export type DrillExtraColumn = {
  header: string
  get: (r: DrillRow) => React.ReactNode
  sortValue?: (r: DrillRow) => string | number | null
}

/** Stable empty default, so `useMemo` below is not defeated by a fresh `[]` on every render. */
const NO_EXTRA: DrillExtraColumn[] = []

/** The built-in columns' sort fields. Prefixed so a report's own header can never collide with one. */
const F_ROW = '_row', F_AMOUNT = '_amount', F_COUNTED = '_counted', F_ORIGIN = '_origin'

/** One cell reader for every column. A row with no figure returns null, which `compareValues` sinks
 *  to the bottom in BOTH directions — "not measured" must never sort as $0.00. */
const cellReader = (extra: DrillExtraColumn[]) => (r: DrillRow, field: string) => {
  if (field === F_ROW) return r.label
  if (field === F_AMOUNT) return r.measured && r.present ? r.amount : null
  if (field === F_COUNTED) return r.in_figure ? 'yes' : ''
  if (field === F_ORIGIN) {
    return (r.origins || []).filter(o => o && o.table).map(o => sourceLabel(o.table!)).join(' · ')
  }
  const c = extra.find(x => x.header === field)
  return c && c.sortValue ? c.sortValue(r) : null
}

const num = (v: number | null | undefined, unit?: string) =>
  v == null ? '—' : unit === 'count' ? String(v) : unit === 'hours' ? `${v} hrs` : fmt(v)

const wrap: React.CSSProperties = {
  background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8,
  padding: '12px 14px', margin: '4px 0 10px',
}
const th: React.CSSProperties = {
  textAlign: 'left', padding: '5px 8px', borderBottom: '1px solid var(--border)',
  fontSize: 11, color: 'var(--text2)', whiteSpace: 'nowrap',
}
const td: React.CSSProperties = { padding: '5px 8px', borderBottom: '1px solid var(--border)', fontSize: 12 }
const chip: React.CSSProperties = {
  display: 'inline-block', padding: '2px 8px', borderRadius: 10, fontSize: 11,
  background: 'var(--surface)', border: '1px solid var(--border)', marginRight: 6, marginTop: 4,
}

/** The sources line — "where is the data coming from", which is the whole of the owner's ask. */
export function DrillSources({ panel }: { panel: DrillPanel }) {
  const srcs = panel.sources || []
  if (!srcs.length && !panel.untraceable_origins) return null
  return (
    <div style={{ marginTop: 2 }}>
      <span style={{ fontSize: 11, color: 'var(--text2)' }}>Built from:</span>{' '}
      {srcs.map(s => (
        <span key={s.table} style={chip} title={s.table}>
          {sourceLabel(s.table)} · {s.rows} row{s.rows === 1 ? '' : 's'}
        </span>
      ))}
      {/* A provenance gap is REPORTED, never tidied away (CLAUDE.md). */}
      {!!panel.untraceable_origins && (
        <span style={{ ...chip, color: 'var(--warn, var(--c-fg-b45309))' }}>
          {panel.untraceable_origins} figure(s) could not be traced to a source
        </span>
      )}
    </div>
  )
}

/** One row's origins, as the sentence a human can act on: which report, which row, which column. */
function OriginCell({ origins }: { origins?: DrillOrigin[] }) {
  const os = (origins || []).filter(o => o && o.table)
  if (!os.length) return <span style={{ color: 'var(--text3)' }}>nothing recorded to trace</span>
  return (
    <>
      {os.map((o, i) => (
        <div key={i} style={{ fontSize: 11, lineHeight: 1.45 }}>
          <strong>{sourceLabel(o.table!)}</strong>
          {o.note ? <span style={{ color: 'var(--text2)' }}> — {o.note}</span> : null}
          {o.keys && Object.keys(o.keys).length ? (
            <div style={{ color: 'var(--text3)' }}>
              {Object.entries(o.keys).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(' · ')}
            </div>
          ) : null}
        </div>
      ))}
    </>
  )
}

/** The tie-out sentence. `ties === null` is "could not be checked", which is NOT a pass — and a
 *  `by_construction` pass is not a check either: it is a sum the screen composed for the selection
 *  you are viewing, so it gets its own mark rather than the tick a real comparison earns
 *  (§CORE-17.3d). Printing ✅ for both would be reassurance nobody tested. */
export function DrillTieOut({ panel }: { panel: DrillPanel }) {
  const t = panel.tie_out
  if (!t?.words) return null
  const icon = t.ties === false ? '⚠️' : t.by_construction ? '∑' : t.ties === true ? '✅' : '—'
  return (
    <div style={{ fontSize: 11, color: t.ties === false ? 'var(--danger, var(--c-fg-b91c1c))' : 'var(--text2)', marginTop: 6 }}>
      {icon} {t.words}
    </div>
  )
}

/**
 * The panel. `extraColumns` lets a report add its own columns off each row's `fields` without
 * forking the component — the shape stays one shape, and a report's own vocabulary stays its own.
 */
export default function DrillDownPanel({
  panel, extraColumns = NO_EXTRA, emptyNote,
}: {
  panel?: DrillPanel | null
  extraColumns?: DrillExtraColumn[]
  emptyNote?: string
}) {
  // Hooks run before the early return — a panel that is absent this render may be present the next.
  const rows = panel?.rows || []
  const cell = useMemo(() => cellReader(extraColumns), [extraColumns])
  const s = useTableSort(rows, cell)
  if (!panel) return <div style={{ ...wrap, color: 'var(--text2)', fontSize: 12 }}>{emptyNote || 'Nothing to show behind this figure.'}</div>
  const unit = panel.figure?.unit
  return (
    <div style={wrap}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'baseline' }}>
        <div style={{ fontWeight: 600, fontSize: 13 }}>
          {panel.subject ? `${panel.subject} — ` : ''}{panel.figure?.label}
        </div>
        <div style={{ fontSize: 15, fontWeight: 700 }}>{num(panel.figure?.amount, unit)}</div>
      </div>
      {panel.note ? <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 2 }}>{panel.note}</div> : null}
      <DrillSources panel={panel} />
      <DrillTieOut panel={panel} />
      {panel.capped && (
        <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 4 }}>
          Showing {panel.rows_shown} of {panel.rows_total_count} rows — export the report for all of them.
        </div>
      )}
      {rows.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 8 }}>
          {emptyNote || 'No rows were recorded behind this figure.'}
        </div>
      ) : (
        <div style={{ overflowX: 'auto', marginTop: 8 }}>
          <table style={{ borderCollapse: 'collapse', width: '100%' }}>
            <thead>
              <tr>
                <SortableTh field={F_ROW} sort={s.sort} onSort={s.toggle} style={th}>Row</SortableTh>
                <SortableTh field={F_AMOUNT} sort={s.sort} onSort={s.toggle} style={th}>
                  {unit === 'count' ? 'Count' : unit === 'hours' ? 'Hours' : 'Amount'}
                </SortableTh>
                <SortableTh field={F_COUNTED} sort={s.sort} onSort={s.toggle} style={th}>
                  Counted in the figure
                </SortableTh>
                {/* A column whose cell is a formatted node and gave no `sortValue` has nothing to
                    compare, so it says so rather than offering a header that does nothing. */}
                {extraColumns.map(c => (
                  <SortableTh key={c.header} field={c.header} sort={s.sort} onSort={s.toggle}
                    style={th} disabled={!c.sortValue}>{c.header}</SortableTh>
                ))}
                <SortableTh field={F_ORIGIN} sort={s.sort} onSort={s.toggle} style={th}>
                  Where this came from
                </SortableTh>
              </tr>
            </thead>
            <tbody>
              {s.sorted.map((r, i) => (
                <tr key={(r.label || '') + i} style={r.in_figure ? undefined : { opacity: 0.75 }}>
                  <td style={td}>{r.label}</td>
                  <td style={td} title={r.measured ? '' : (r.reason_label || '')}>
                    {/* A row nobody recorded shows the reason, never $0.00 — absence is not zero. */}
                    {r.measured && r.present ? num(r.amount, unit)
                      : <span style={{ color: 'var(--text3)' }}>not measured</span>}
                  </td>
                  <td style={td}>
                    {r.in_figure ? 'yes' : <span style={{ color: 'var(--text3)' }}>shown for context</span>}
                  </td>
                  {extraColumns.map(c => <td key={c.header} style={td}>{c.get(r)}</td>)}
                  <td style={td}><OriginCell origins={r.origins} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows.some(r => !r.measured && r.reason_label) && (
        <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 6 }}>
          A row reads “not measured” when the platform holds no figure for it, or holds two figures
          that are not about the same thing. That is reported rather than counted as zero.
        </div>
      )}
    </div>
  )
}

/** The little ▸/▾ a rollup row uses to open its panel. One affordance everywhere. */
export function DrillToggle({ open, onClick, label = 'rows behind this' }:
  { open: boolean; onClick: () => void; label?: string }) {
  return (
    <button type="button" onClick={onClick} aria-expanded={open}
      title={open ? 'Hide the rows behind this figure' : `Show the ${label}`}
      style={{
        background: 'none', border: 'none', cursor: 'pointer', color: 'var(--accent)',
        fontSize: 12, padding: 0, marginRight: 6,
      }}>
      {open ? '▾' : '▸'}
    </button>
  )
}

/** Open/closed state for a table of rollup rows, so a page does not hand-roll a Set. */
export function useDrillOpen() {
  const [open, setOpen] = useState<Record<string, boolean>>({})
  return {
    isOpen: (k: string) => !!open[k],
    toggle: (k: string) => setOpen(o => ({ ...o, [k]: !o[k] })),
  }
}

/**
 * Open/closed state for a row of TILES, where only one panel is open at a time.
 *
 * WHY SINGLE-OPEN, AND WHY IT IS HERE. The §CORE-17 audit found that most of the 93 reports owing a
 * drill-down owe it on a strip of summary tiles, and a tile's panel is a wide table: letting four of
 * them stand open stacks four wide tables above the report they belong to. One open panel, rendered
 * once below the strip, is the shape — and it lives here so the remaining reports inherit it instead
 * of each page deciding again.
 */
export function useDrillTile(
  panels: Record<string, DrillPanel> = {},
  gate?: (key: string, panel: DrillPanel) => boolean,
) {
  const [open, setOpen] = useState<string | null>(null)
  // THE OFFER RULE, ONE HOME (§CORE-17.3c). `dl` takes key/label pairs and keeps only the figures the
  // SERVER actually served a panel for, so a feed that never resolved for this range shows no link
  // rather than a link that opens an empty panel and reads like a bug. Every page that gained tile
  // drills had written this loop again locally; one of them had also bolted its own extra condition
  // on (the figure must agree with the tile to the cent, where a page narrows its rows after the
  // server folded them). That condition is now the optional `gate`, so there is one loop and the
  // per-page difference is a parameter rather than a second copy.
  const dl = (...pairs: string[]) => {
    const out: { key: string; label: string }[] = []
    for (let i = 0; i < pairs.length; i += 2) {
      const p = panels[pairs[i]]
      if (p && (!gate || gate(pairs[i], p))) out.push({ key: pairs[i], label: pairs[i + 1] })
    }
    return out
  }
  return {
    open,
    isOpen: (k: string) => open === k,
    toggle: (k: string) => setOpen(o => (o === k ? null : k)),
    close: () => setOpen(null),
    /** The panel currently open, or undefined. Saves every caller indexing `panels` again. */
    panel: open ? panels[open] : undefined,
    dl,
  }
}

/** The little buttons on a tile, one per figure it shows. A tile that carries a short AND an over
 *  figure offers both, because "which rows is this?" is a different question for each. */
export function DrillTileLinks({ drills, open, onToggle }: {
  drills: { key: string; label: string }[]
  open: string | null
  onToggle: (k: string) => void
}) {
  if (!drills.length) return null
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
      {drills.map(d => (
        <button key={d.key} type="button" onClick={() => onToggle(d.key)}
          aria-expanded={open === d.key}
          title={open === d.key ? 'Hide the rows behind this figure' : `Show the rows behind ${d.label}`}
          style={{
            background: 'none', border: 'none', padding: 0, cursor: 'pointer',
            color: 'var(--accent)', fontSize: 10.5, textDecoration: 'underline',
            fontWeight: open === d.key ? 700 : 400,
          }}>
          {open === d.key ? '▾' : '▸'} {d.label}
        </button>
      ))}
    </div>
  )
}
