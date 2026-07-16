'use client'
// ReportShell — the universal report/dashboard frame. Give it the SAME `columns` (ExportColumn[])
// and `rows` a page already builds for export, and it adds, for free and identically everywhere:
//   • a filter bar — quick By-rep / By-store / By-date (from→to) / By-month dropdowns (auto-detected
//     from the columns), PLUS "＋ Add filter" so the user can filter on ANY column (contains / = / ≥ / ≤);
//   • "Group by ▸ <column>" — pick any column to group rows under, with money subtotals + a grand total;
//   • Export to Excel / PDF / Print (lib/export) and 📤 Send to a rep via email / WhatsApp (lib/send-report),
//     both operating on the CURRENTLY FILTERED rows.
// Filtering/grouping is client-side over the rows the page already loaded — no backend change to adopt.
import { Fragment, useMemo, useState } from 'react'
import { ExportButtons, type ExportColumn, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { useColumnResize, ResizeHandle } from '@/lib/col-resize'
import { computeTotalRow } from '@/lib/report-totals'

type Col = ExportColumn
type Filter = { field: string; op: string; value: string }

const REP_RE = /\b(rep|salesperson|sales person|employee|advocate|associate|agent|sold ?by|closer|user ?login|username)\b/i
const STORE_RE = /\b(store|location|address|door|branch|site|market|dealer)\b/i
const DATE_RE = /\b(date|day)\b/i
const MONTH_RE = /\b(month|period)\b/i

const key = (c: Col) => c.field || c.header
const isMoney = (c: Col) => !!c.money || c.type === 'money'
function colType(c: Col): 'text' | 'money' | 'number' | 'date' {
  if (c.type) return c.type
  if (c.money) return 'money'
  return 'text'
}
function detectRole(c: Col): 'rep' | 'store' | 'date' | 'month' | null {
  if (c.role) return c.role
  const t = colType(c)
  if (t === 'date') return 'date'
  const h = c.header || ''
  if (MONTH_RE.test(h)) return 'month'
  if (DATE_RE.test(h)) return 'date'
  if (REP_RE.test(h)) return 'rep'
  if (STORE_RE.test(h)) return 'store'
  return null
}
const str = (v: any) => (v == null ? '' : String(v))
const num = (v: any) => { const n = Number(String(v).replace(/[^0-9.-]/g, '')); return isNaN(n) ? null : n }
const money = (n: any) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(n) || 0)
// A date column's value normalized to YYYY-MM-DD (best-effort; leaves non-dates as-is).
function ymd(v: any): string {
  const s = str(v)
  const m = s.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/)
  if (m) return `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`
  const d = new Date(s)
  if (!isNaN(d.getTime())) return d.toISOString().slice(0, 10)
  return s
}

export function ReportShell({ title, subtitle, filename, columns, rows, compact, right, children, onRowClick, totals }: {
  title: string
  subtitle?: string
  filename?: string
  columns: Col[]
  rows: any[]
  compact?: boolean
  right?: React.ReactNode                   // extra toolbar content (page-specific controls)
  children?: React.ReactNode                // optional custom body ABOVE the table (charts, tiles…)
  onRowClick?: (row: any) => void           // makes each data row clickable (e.g. drill into a transaction)
  totals?: boolean                          // opt-in: a pinned TOTAL row (sum of money+numeric cols over
                                            // ALL filtered rows) — shown on screen AND in every export.
                                            // Off by default so other reports are unchanged.
}) {
  const cols = useMemo(() => columns.filter(Boolean), [columns])
  const byKey = useMemo(() => Object.fromEntries(cols.map(c => [key(c), c])), [cols])
  const cw = useColumnResize()   // auto-fit + user-resizable columns

  // Auto-detected quick-filter columns (first of each role).
  const repCol = useMemo(() => cols.find(c => detectRole(c) === 'rep'), [cols])
  const storeCol = useMemo(() => cols.find(c => detectRole(c) === 'store'), [cols])
  const dateCol = useMemo(() => cols.find(c => detectRole(c) === 'date'), [cols])
  const distinct = (c?: Col) => c ? Array.from(new Set(rows.map(r => str(c.get(r)).trim()).filter(Boolean))).sort() : []
  const repVals = useMemo(() => distinct(repCol), [repCol, rows])
  const storeVals = useMemo(() => distinct(storeCol), [storeCol, rows])

  const [rep, setRep] = useState('')
  const [store, setStore] = useState('')
  const [dFrom, setDFrom] = useState('')
  const [dTo, setDTo] = useState('')
  const [month, setMonth] = useState('')            // YYYY-MM
  const [custom, setCustom] = useState<Filter[]>([])
  const [groupBy, setGroupBy] = useState('')

  const monthsAvail = useMemo(() => dateCol
    ? Array.from(new Set(rows.map(r => ymd(dateCol.get(r)).slice(0, 7)).filter(s => /^\d{4}-\d{2}$/.test(s)))).sort()
    : [], [dateCol, rows])

  const filtered = useMemo(() => {
    return rows.filter(r => {
      if (rep && repCol && str(repCol.get(r)).trim() !== rep) return false
      if (store && storeCol && str(storeCol.get(r)).trim() !== store) return false
      if (dateCol && (dFrom || dTo || month)) {
        const d = ymd(dateCol.get(r))
        if (month && d.slice(0, 7) !== month) return false
        if (dFrom && d < dFrom) return false
        if (dTo && d > dTo) return false
      }
      for (const f of custom) {
        const c = byKey[f.field]; if (!c || !f.value) continue
        const raw = c.get(r)
        if (f.op === 'contains') { if (!str(raw).toLowerCase().includes(f.value.toLowerCase())) return false }
        else if (f.op === '=') { if (str(raw).toLowerCase() !== f.value.toLowerCase()) return false }
        else if (f.op === '≠') { if (str(raw).toLowerCase() === f.value.toLowerCase()) return false }
        else if (f.op === '≥') { const a = num(raw), b = num(f.value); if (a == null || b == null || a < b) return false }
        else if (f.op === '≤') { const a = num(raw), b = num(f.value); if (a == null || b == null || a > b) return false }
      }
      return true
    })
  }, [rows, rep, store, dFrom, dTo, month, custom, repCol, storeCol, dateCol, byKey])

  const activeCount = (rep ? 1 : 0) + (store ? 1 : 0) + (dFrom || dTo ? 1 : 0) + (month ? 1 : 0) + custom.filter(f => f.value).length
  const clearAll = () => { setRep(''); setStore(''); setDFrom(''); setDTo(''); setMonth(''); setCustom([]) }

  // Grouped view for the table + export (grand total row for money columns).
  const groupCol = groupBy ? byKey[groupBy] : undefined
  const groups = useMemo(() => {
    if (!groupCol) return null
    const m = new Map<string, any[]>()
    for (const r of filtered) { const g = str(groupCol.get(r)).trim() || '—'; (m.get(g) || m.set(g, []).get(g)!).push(r) }
    return Array.from(m.entries()).sort((a, b) => a[0].localeCompare(b[0]))
  }, [groupCol, filtered])

  const moneyCols = cols.filter(isMoney)
  const subtotal = (rs: any[], c: Col) => rs.reduce((s, r) => s + (Number(String(c.get(r)).replace(/[^0-9.-]/g, '')) || 0), 0)

  // Opt-in pinned TOTAL row. On-screen shows the GRAND total over every filtered row (across all
  // groups); each EXPORT sheet ends with a TOTAL row summing that sheet (= the same grand total when
  // ungrouped, = each group's subtotal per sheet when grouped) so what you see is what you export.
  const totalCells = useMemo(
    () => (totals && filtered.length ? computeTotalRow(cols, filtered) : null),
    [totals, cols, filtered],
  )
  // Append a synthetic TOTAL row to one export sheet. A sentinel row carries the precomputed cells;
  // each column's getter is wrapped to read them for that row and delegate to the original otherwise —
  // so export.tsx formats/serializes the total exactly like any other cell (no change to export.tsx).
  const TOTAL_ROW = '__rsTotal'
  const sheetWithTotal = (rs: any[]): { columns: Col[]; rows: any[] } => {
    if (!rs.length) return { columns: cols, rows: rs }   // never export a lone total-of-nothing row
    const cells = computeTotalRow(cols, rs)
    const wrapped = cols.map((c, i) => ({ ...c, get: (row: any) => (row && row[TOTAL_ROW]) ? row[TOTAL_ROW][i].raw : c.get(row) }))
    return { columns: wrapped, rows: [...rs, { [TOTAL_ROW]: cells }] }
  }

  const buildPayload = (): ExportPayload => {
    const mkSheet = (rs: any[]) => totals ? sheetWithTotal(rs) : { columns: cols, rows: rs }
    return {
      title, subtitle, filename: filename || title.replace(/[^\w]+/g, '_').toLowerCase(),
      sheets: groups
        ? groups.map(([g, rs]) => ({ name: g.slice(0, 28), ...mkSheet(rs) }))
        : [{ name: 'Report', ...mkSheet(filtered) }],
    }
  }

  const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)' }
  const cell: React.CSSProperties = { padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 13 }
  const th: React.CSSProperties = { textAlign: 'left', padding: '6px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', position: 'sticky', top: 0, background: 'var(--surface2)' }

  return (
    <div>
      <style>{`.rs-clickable:hover td{background:var(--surface2)}`}</style>
      {/* Toolbar */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        {repCol && (
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>👤{' '}
            <select style={sel} value={rep} onChange={e => setRep(e.target.value)}>
              <option value="">All {repCol.header.toLowerCase()}</option>
              {repVals.map(v => <option key={v} value={v}>{v}</option>)}
            </select></label>
        )}
        {storeCol && (
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>🏬{' '}
            <select style={sel} value={store} onChange={e => setStore(e.target.value)}>
              <option value="">All {storeCol.header.toLowerCase()}</option>
              {storeVals.map(v => <option key={v} value={v}>{v}</option>)}
            </select></label>
        )}
        {dateCol && monthsAvail.length > 0 && (
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>🗓️{' '}
            <select style={sel} value={month} onChange={e => setMonth(e.target.value)}>
              <option value="">All months</option>
              {monthsAvail.map(m => <option key={m} value={m}>{m}</option>)}
            </select></label>
        )}
        {dateCol && (
          <span style={{ fontSize: 12, color: 'var(--text2)', display: 'inline-flex', gap: 4, alignItems: 'center' }}>
            📅<input type="date" style={sel} value={dFrom} onChange={e => setDFrom(e.target.value)} title="from" />
            →<input type="date" style={sel} value={dTo} onChange={e => setDTo(e.target.value)} title="to" />
          </span>
        )}
        {/* Group by */}
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Group by{' '}
          <select style={sel} value={groupBy} onChange={e => setGroupBy(e.target.value)}>
            <option value="">— none —</option>
            {cols.map(c => <option key={key(c)} value={key(c)}>{c.header}</option>)}
          </select></label>
        {/* Add custom filter */}
        <button className="btn btn-secondary" style={{ fontSize: 12, padding: '5px 9px' }}
          onClick={() => setCustom(f => [...f, { field: key(cols[0]), op: 'contains', value: '' }])}>＋ Filter</button>
        {activeCount > 0 && <button className="btn btn-secondary" style={{ fontSize: 12, padding: '5px 9px', color: '#dc2626' }} onClick={clearAll}>Clear ({activeCount})</button>}
        <div style={{ flex: 1 }} />
        {right}
        <ExportButtons payload={buildPayload} compact />
        {/* Universal Send: the filtered/grouped file is rendered in-browser and delivered via
            /notify/send-file, so it works on every report without server-side registration. */}
        <SendReportButton exportPayload={buildPayload} title={title} compact />
      </div>

      {/* Custom filter rows */}
      {custom.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 10 }}>
          {custom.map((f, i) => (
            <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <select style={sel} value={f.field} onChange={e => setCustom(cs => cs.map((x, j) => j === i ? { ...x, field: e.target.value } : x))}>
                {cols.map(c => <option key={key(c)} value={key(c)}>{c.header}</option>)}
              </select>
              <select style={sel} value={f.op} onChange={e => setCustom(cs => cs.map((x, j) => j === i ? { ...x, op: e.target.value } : x))}>
                {['contains', '=', '≠', '≥', '≤'].map(o => <option key={o} value={o}>{o}</option>)}
              </select>
              <input style={{ ...sel, minWidth: 140 }} placeholder="value" value={f.value}
                onChange={e => setCustom(cs => cs.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} />
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 8px', color: '#dc2626' }} onClick={() => setCustom(cs => cs.filter((_, j) => j !== i))}>✕</button>
            </div>
          ))}
        </div>
      )}

      <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>
        {filtered.length.toLocaleString()} row{filtered.length === 1 ? '' : 's'}{filtered.length !== rows.length ? ` of ${rows.length.toLocaleString()}` : ''}{groupCol ? ` · grouped by ${groupCol.header}` : ''}
      </div>

      {children}

      {/* Table */}
      {cw.dirty && <div style={{ fontSize: 11, color: 'var(--text3)', margin: '0 0 4px' }}><button className="btn" style={{ padding: '2px 8px', fontSize: 11 }} onClick={cw.resetAll}>↺ Reset column widths</button> <span>drag a column edge to resize · double-click to auto-fit</span></div>}
      <div className="table-wrapper" style={{ maxHeight: '70vh', overflow: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'auto' }}>
          <colgroup>{cols.map(c => <col key={key(c)} style={{ width: cw.width(key(c)) }} />)}</colgroup>
          <thead><tr>{cols.map(c => <th key={key(c)} style={{ ...th, textAlign: isMoney(c) || c.align === 'right' ? 'right' : 'left', whiteSpace: 'nowrap', position: 'relative' }}>{c.header}<ResizeHandle onDown={e => cw.start(key(c), e)} onReset={() => cw.reset(key(c))} /></th>)}</tr></thead>
          <tbody>
            {!groups && filtered.map((r, i) => (
              <tr key={i} onClick={onRowClick ? () => onRowClick(r) : undefined} style={onRowClick ? { cursor: 'pointer' } : undefined}
                className={onRowClick ? 'rs-clickable' : undefined}>
                {cols.map(c => <td key={key(c)} style={{ ...cell, textAlign: isMoney(c) || c.align === 'right' ? 'right' : 'left' }}>{isMoney(c) ? money(c.get(r)) : str(c.get(r))}</td>)}</tr>
            ))}
            {groups && groups.map(([g, rs]) => (
              <Fragment key={g}>
                <tr style={{ background: 'var(--surface2)' }}>
                  <td style={{ ...cell, fontWeight: 700 }} colSpan={cols.length - moneyCols.length || 1}>{g} · {rs.length}</td>
                  {cols.filter(isMoney).map(c => <td key={'gs' + key(c)} style={{ ...cell, textAlign: 'right', fontWeight: 700 }}>{money(subtotal(rs, c))}</td>)}
                </tr>
                {rs.map((r, i) => (
                  <tr key={g + i} onClick={onRowClick ? () => onRowClick(r) : undefined} style={onRowClick ? { cursor: 'pointer' } : undefined}
                    className={onRowClick ? 'rs-clickable' : undefined}>
                    {cols.map(c => <td key={key(c)} style={{ ...cell, textAlign: isMoney(c) || c.align === 'right' ? 'right' : 'left' }}>{isMoney(c) ? money(c.get(r)) : str(c.get(r))}</td>)}</tr>
                ))}
              </Fragment>
            ))}
            {filtered.length === 0 && <tr><td style={{ ...cell, textAlign: 'center', color: 'var(--text3)', padding: 24 }} colSpan={cols.length}>No rows match the filters.</td></tr>}
          </tbody>
          {totalCells && (
            <tfoot><tr>
              {cols.map((c, i) => <td key={key(c)} style={{ ...cell, fontWeight: 700, textAlign: (isMoney(c) || c.align === 'right') ? 'right' : 'left', borderTop: '2px solid var(--text3)', position: 'sticky', bottom: 0, background: 'var(--surface2)' }}>{totalCells[i].text}</td>)}
            </tr></tfoot>
          )}
          {!totals && moneyCols.length > 0 && filtered.length > 0 && (
            <tfoot><tr style={{ borderTop: '2px solid var(--border)' }}>
              {cols.map((c, i) => <td key={key(c)} style={{ ...cell, fontWeight: 700, textAlign: isMoney(c) ? 'right' : 'left' }}>{isMoney(c) ? money(subtotal(filtered, c)) : (i === 0 ? 'Total' : '')}</td>)}
            </tr></tfoot>
          )}
        </table>
      </div>
    </div>
  )
}

export default ReportShell
