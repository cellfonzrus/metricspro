'use client'
// The React half of click-a-header sorting (OWNER DIRECTIVE 2026-08-10, "sort function by clicking on
// the header for all reports"). The comparison rules live framework-free in `@/lib/table-sort`; this file
// is only the hook + the <th>. Two pieces so a page needs one import and three lines:
//
//   const { sorted, sort, toggle } = useTableSort(rows, getCell)
//   <SortableTh field="amount" sort={sort} onSort={toggle} style={th}>$</SortableTh>
//   {sorted.map(...)}                      // totals row appended AFTER, so it never gets sorted away
//
// Accessibility: the header is a real button with `aria-sort`, so the sort is reachable by keyboard and
// announced by a screen reader — a click-only affordance would make every report keyboard-hostile.
import { useCallback, useMemo, useState } from 'react'
import { nextSort, sortIndicator, sortRows, type SortState } from '@/lib/table-sort'

export type { SortState } from '@/lib/table-sort'

/** Sort state + the sorted view of `rows`. `get(row, field)` reads a cell — pass a STABLE function
 *  (module scope or useCallback), otherwise the memo recomputes on every render. `initial` seeds the
 *  report's preferred starting sort; null (the default) means "the order the report already produced". */
export function useTableSort<T>(
  rows: T[], get: (row: T, field: string) => any, initial: SortState = null,
) {
  const [sort, setSort] = useState<SortState>(initial)
  const sorted = useMemo(() => sortRows(rows || [], sort, get), [rows, sort, get])
  const toggle = useCallback((field: string) => setSort(cur => nextSort(cur, field)), [])
  return { sort, setSort, toggle, sorted }
}

/** A sortable table header cell. Renders the caller's own <th> styling untouched and appends the
 *  ▲/▼/↕ affordance, so retrofitting an existing table is a tag swap, not a restyle. */
export function SortableTh({
  field, sort, onSort, children, style, title, disabled = false, after,
}: {
  field: string
  sort: SortState
  onSort: (field: string) => void
  children?: React.ReactNode
  style?: React.CSSProperties
  title?: string
  /** Opt a column out (e.g. a checkbox / actions column that has nothing to compare). */
  disabled?: boolean
  /** Header content that must stay OUTSIDE the sort button — a column-resize grip, a menu, anything with
   *  its own pointer handling. Nesting those inside the button would make every drag also fire a sort. */
  after?: React.ReactNode
}) {
  const on = !!sort && sort.field === field
  const ariaSort: 'ascending' | 'descending' | 'none' = on ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : 'none'
  if (disabled) return <th style={style} title={title}>{children}{after}</th>
  return (
    <th style={style} title={title || 'Click to sort'} aria-sort={ariaSort}>
      <button type="button" onClick={() => onSort(field)}
        style={{
          font: 'inherit', color: 'inherit', background: 'none', border: 'none', padding: 0, margin: 0,
          cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
          textAlign: 'inherit' as any, whiteSpace: 'inherit' as any,
        }}>
        {children}
        <span aria-hidden style={{ fontSize: 9, opacity: on ? 0.95 : 0.35 }}>{sortIndicator(sort, field)}</span>
      </button>
      {after}
    </th>
  )
}

export default SortableTh
