'use client'
// THE VOID & RETURN REGISTER (owner ask 2026-10-05: "Make the voids visible and track able").
//
// THIS IS THE LIST THAT DID NOT EXIST. `is_voided` is dereferenced about a dozen times in the backend
// and every one of them does the same thing: EXCLUDE the line so it does not pay. Correct for pay,
// correct for gross profit — and the whole of the platform's relationship with a void. There was no
// void count, no void rate, no void list, by rep or by store or at all. A rep whose voids tripled
// looked identical to a rep who sold less.
//
// Nothing here changes what pays. The classification comes from GET /commcalc/watchdog/voids, which
// reads the pay path's OWN rule (gp_report.countable_sale_skip_reason) a second way: the pay path
// asks "does this count?", this page asks "what did we throw away?". One rule, two readings, so the
// two can never disagree about what a void is.
//
// Why this is a static route and not /watchdog/voids: that segment is the Voids AREA page, which
// lists the findings above the thresholds. This is the full register — every reversed line, including
// the ones no rule flagged.
import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import PageIntro from '@/components/PageIntro'
import StatTile from '@/components/StatTile'
import { ExportButtons, type ExportPayload } from '@/lib/export'
import { SortableTh, useTableSort } from '@/components/SortableTh'

const KIND_LABEL: Record<string, string> = {
  voided: 'Voided',
  return: 'Returned',
  unattributed: 'Nobody named',
}
const KIND_COLOR: Record<string, string> = {
  voided: '#dc2626', return: '#d97706', unattributed: '#2563eb',
}

type VoidLine = {
  kind: string; store: string; salesperson: string
  trans_id: string; trans_date: string; product_desc: string
  imei: string; mdn: string; amount: number
  tender_type: string; contract_type: string
}
type RepAgg = {
  countable: number; voided: number; return: number; unattributed: number
  amount_voided: number; amount_returned: number
  void_share: number | null; return_share: number | null
}
type Register = {
  period?: string
  has_feed?: boolean
  total_lines?: number
  counts?: Record<string, number>
  amounts?: Record<string, number>
  void_share?: number | null
  return_share?: number | null
  by_rep?: Record<string, RepAgg>
  by_store?: Record<string, RepAgg>
  lines?: VoidLine[]
}

const th: React.CSSProperties = { padding: '6px 8px' }
const thRight: React.CSSProperties = { padding: '6px 8px', textAlign: 'right' }

// THE accessors for the two tables below, at module scope so `useTableSort`'s memo is not rebuilt on
// every render. Both return raw numbers and raw nulls rather than the formatted cell: a share of null
// means "no denominator", and the shared comparator sinks it instead of reading it as 0%.
type AggRow = RepAgg & { name: string }
const lineCell = (l: VoidLine, field: string): any => {
  switch (field) {
    case 'kind': return KIND_LABEL[l.kind] || l.kind
    case 'date': return l.trans_date || ''
    case 'store': return l.store || ''
    case 'person': return l.salesperson || ''
    case 'trans': return l.trans_id || ''
    case 'item': return l.product_desc || ''
    case 'device': return l.imei || l.mdn || ''
    case 'amount': return l.amount ?? null
    default: return ''
  }
}
const aggCell = (r: AggRow, field: string): any => {
  switch (field) {
    case 'name': return r.name || ''
    case 'countable': return r.countable ?? null
    case 'voided': return r.voided ?? null
    case 'void_share': return r.void_share ?? null
    case 'amount_voided': return r.amount_voided ?? null
    case 'returned': return r.return ?? null
    case 'return_share': return r.return_share ?? null
    default: return ''
  }
}

// A share is null when there is no denominator, and that is NOT 0%. "—" is the honest rendering.
function pct(v: number | null | undefined) {
  return v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`
}

export default function VoidRegisterPage() {
  const { period } = usePeriod()
  const [data, setData] = useState<Register | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [fKind, setFKind] = useState('')
  const [fRep, setFRep] = useState('')
  const [fStore, setFStore] = useState('')
  const [view, setView] = useState<'lines' | 'rep' | 'store'>('lines')

  useEffect(() => {
    setLoading(true)
    setErr('')
    api(`/api/v1/commcalc/watchdog/voids?period=${encodeURIComponent(period || '')}`)
      .then((d: Register) => setData(d))
      .catch(e => { setErr(e?.message || String(e)); setData(null) })
      .finally(() => setLoading(false))
  }, [period])

  const lines = data?.lines || []
  const rows = useMemo(() => lines.filter(l =>
    (!fKind || l.kind === fKind)
    && (!fRep || (l.salesperson || '').toLowerCase().includes(fRep.toLowerCase()))
    && (!fStore || (l.store || '').toLowerCase().includes(fStore.toLowerCase()))
  ), [lines, fKind, fRep, fStore])

  const repRows = useMemo(() => Object.entries(data?.by_rep || {})
    .map(([name, a]) => ({ name, ...a }))
    .filter(r => r.voided || r.return || r.unattributed)
    .sort((a, b) => (b.void_share ?? -1) - (a.void_share ?? -1)), [data])

  const storeRows = useMemo(() => Object.entries(data?.by_store || {})
    .map(([name, a]) => ({ name, ...a }))
    .filter(r => r.voided || r.return || r.unattributed)
    .sort((a, b) => (b.void_share ?? -1) - (a.void_share ?? -1)), [data])

  // Click-a-header sorting on both tables through the ONE comparison home (lib/table-sort via
  // useTableSort) — owner directive 2026-08-10. The aggregate table keeps its own default order
  // (worst void rate first) until a header is clicked: `sort === null` returns the rows untouched.
  const lineSort = useTableSort(rows, lineCell)
  const aggSort = useTableSort<AggRow>(view === 'rep' ? repRows : storeRows, aggCell)

  const buildPayload = (): ExportPayload => ({
    title: 'Void & Return Register',
    subtitle: data?.period || period || '',
    filename: `void-register-${(data?.period || period || '').replace(/\s+/g, '-')}`,
    sheets: [
      {
        name: 'Lines',
        columns: [
          { header: 'Kind', get: (l: VoidLine) => KIND_LABEL[l.kind] || l.kind },
          { header: 'Date', get: (l: VoidLine) => l.trans_date || '', type: 'date' },
          { header: 'Store', get: (l: VoidLine) => l.store || '', role: 'store' },
          { header: 'Person', get: (l: VoidLine) => l.salesperson || '', role: 'rep' },
          { header: 'Transaction', get: (l: VoidLine) => l.trans_id || '' },
          { header: 'Item', get: (l: VoidLine) => l.product_desc || '' },
          { header: 'Device', get: (l: VoidLine) => l.imei || '' },
          { header: 'Number', get: (l: VoidLine) => l.mdn || '' },
          { header: 'Amount', get: (l: VoidLine) => l.amount, money: true, align: 'right' },
          { header: 'Tender', get: (l: VoidLine) => l.tender_type || '' },
        ],
        rows: lineSort.sorted,   // export the lines in the order the manager sorted them
      },
      {
        name: 'By person',
        columns: [
          { header: 'Person', get: (r: any) => r.name, role: 'rep' },
          { header: 'Counted sales', get: (r: any) => r.countable, type: 'number' },
          { header: 'Voided', get: (r: any) => r.voided, type: 'number' },
          { header: 'Void rate', get: (r: any) => pct(r.void_share) },
          { header: 'Voided value', get: (r: any) => r.amount_voided, money: true, align: 'right' },
          { header: 'Returned', get: (r: any) => r.return, type: 'number' },
          { header: 'Return rate', get: (r: any) => pct(r.return_share) },
        ],
        rows: repRows,
      },
    ],
  })

  const c = data?.counts || {}
  const a = data?.amounts || {}

  return (
    <div>
      <PageIntro
        title="Void &amp; Return Register"
        right={<Link href="/watchdog" style={{ fontSize: 13, color: 'var(--blue, #2563eb)' }}>
          ← Management Watchdog
        </Link>}
        help={
          <>
            Every transaction that was reversed, returned, or rung with nobody named on it. These
            lines are already excluded from commission and gross profit — this is the first place they
            are counted rather than only thrown away. A rate is shown as “—”, never 0%, where there
            were no lines to measure.
          </>
        }
      />

      {err && (
        <div style={{ border: '1px solid var(--red, #dc2626)', borderRadius: 8, padding: '10px 12px',
                      marginBottom: 14, fontSize: 13, color: 'var(--text2)' }}>
          The register could not be loaded: {err}
        </div>
      )}

      {loading && !data && <p style={{ color: 'var(--text2)', fontSize: 13 }}>Loading…</p>}

      {data && data.has_feed === false && (
        <p style={{ fontSize: 13, color: 'var(--text2)' }}>
          No sales were received for {data.period || period}, so there is nothing to report — which is
          not the same as no voids. Once the feed lands for this period, every reversed line appears
          here.
        </p>
      )}

      {data && data.has_feed !== false && (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
            <StatTile label="Voided lines" value={c.voided ?? 0} accent={KIND_COLOR.voided}
                      sub={`${fmt(a.voided ?? 0)} of value`} />
            <StatTile label="Void rate" value={pct(data.void_share)}
                      sub="share of all lines received" />
            <StatTile label="Returned lines" value={c['return'] ?? 0} accent={KIND_COLOR['return']}
                      sub={`${fmt(a['return'] ?? 0)} of value`} />
            <StatTile label="Nobody named" value={c.unattributed ?? 0}
                      accent={KIND_COLOR.unattributed}
                      sub="cannot be coached or charged back" />
            <StatTile label="Counted sales" value={c.countable ?? 0}
                      sub="the lines that did stand" />
          </div>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center',
                        marginBottom: 12 }}>
            {(['lines', 'rep', 'store'] as const).map(v => (
              <button key={v} className={`btn ${view === v ? '' : 'btn-secondary'}`}
                      onClick={() => setView(v)}>
                {v === 'lines' ? 'Every line' : v === 'rep' ? 'By person' : 'By store'}
              </button>
            ))}
            {view === 'lines' && (
              <>
                <select value={fKind} onChange={e => setFKind(e.target.value)}>
                  <option value="">Voided, returned and unnamed</option>
                  <option value="voided">Voided only</option>
                  <option value="return">Returned only</option>
                  <option value="unattributed">Nobody named only</option>
                </select>
                <input placeholder="Person" value={fRep} onChange={e => setFRep(e.target.value)}
                       style={{ width: 130 }} />
                <input placeholder="Store" value={fStore} onChange={e => setFStore(e.target.value)}
                       style={{ width: 130 }} />
                <span style={{ fontSize: 12.5, color: 'var(--text2)' }}>
                  {rows.length} of {lines.length}
                </span>
              </>
            )}
            <span style={{ marginLeft: 'auto' }}>
              <ExportButtons payload={buildPayload} compact />
            </span>
          </div>

          {view === 'lines' && (
            lines.length === 0 ? (
              <p style={{ fontSize: 13, color: 'var(--text2)' }}>
                Nothing was voided or returned in {data.period || period}. The feed was received and
                carried {c.countable ?? 0} counted sale lines, so this is a measured clean period.
              </p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ textAlign: 'left', color: 'var(--text2)', fontSize: 11.5,
                                 textTransform: 'uppercase', letterSpacing: '0.4px' }}>
                      <SortableTh field="kind" sort={lineSort.sort} onSort={lineSort.toggle} style={th}>Kind</SortableTh>
                      <SortableTh field="date" sort={lineSort.sort} onSort={lineSort.toggle} style={th}>Date</SortableTh>
                      <SortableTh field="store" sort={lineSort.sort} onSort={lineSort.toggle} style={th}>Store</SortableTh>
                      <SortableTh field="person" sort={lineSort.sort} onSort={lineSort.toggle} style={th}>Person</SortableTh>
                      <SortableTh field="trans" sort={lineSort.sort} onSort={lineSort.toggle} style={th}>Transaction</SortableTh>
                      <SortableTh field="item" sort={lineSort.sort} onSort={lineSort.toggle} style={th}>Item</SortableTh>
                      <SortableTh field="device" sort={lineSort.sort} onSort={lineSort.toggle} style={th}>Device</SortableTh>
                      <SortableTh field="amount" sort={lineSort.sort} onSort={lineSort.toggle} style={thRight}>Amount</SortableTh>
                    </tr>
                  </thead>
                  <tbody>
                    {lineSort.sorted.map((l, i) => (
                      <tr key={`${l.trans_id}-${i}`} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '8px' }}>
                          <span style={{ fontSize: 10, fontWeight: 700,
                                         color: KIND_COLOR[l.kind] || '#64748b',
                                         background: (KIND_COLOR[l.kind] || '#64748b') + '18',
                                         padding: '2px 7px', borderRadius: 999,
                                         whiteSpace: 'nowrap' }}>
                            {KIND_LABEL[l.kind] || l.kind}
                          </span>
                        </td>
                        <td style={{ padding: '8px', whiteSpace: 'nowrap' }}>{l.trans_date || '—'}</td>
                        <td style={{ padding: '8px' }}>{l.store || '—'}</td>
                        <td style={{ padding: '8px' }}>{l.salesperson || '—'}</td>
                        <td style={{ padding: '8px' }}>{l.trans_id || '—'}</td>
                        <td style={{ padding: '8px', maxWidth: 260 }}>{l.product_desc || '—'}</td>
                        <td style={{ padding: '8px' }}>{l.imei || '—'}</td>
                        <td style={{ padding: '8px', textAlign: 'right',
                                     fontVariantNumeric: 'tabular-nums' }}>{fmt(l.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}

          {(view === 'rep' || view === 'store') && (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: 'var(--text2)', fontSize: 11.5,
                               textTransform: 'uppercase', letterSpacing: '0.4px' }}>
                    <SortableTh field="name" sort={aggSort.sort} onSort={aggSort.toggle} style={th}>
                      {view === 'rep' ? 'Person' : 'Store'}
                    </SortableTh>
                    <SortableTh field="countable" sort={aggSort.sort} onSort={aggSort.toggle} style={thRight}>Counted</SortableTh>
                    <SortableTh field="voided" sort={aggSort.sort} onSort={aggSort.toggle} style={thRight}>Voided</SortableTh>
                    <SortableTh field="void_share" sort={aggSort.sort} onSort={aggSort.toggle} style={thRight}>Void rate</SortableTh>
                    <SortableTh field="amount_voided" sort={aggSort.sort} onSort={aggSort.toggle} style={thRight}>Voided value</SortableTh>
                    <SortableTh field="returned" sort={aggSort.sort} onSort={aggSort.toggle} style={thRight}>Returned</SortableTh>
                    <SortableTh field="return_share" sort={aggSort.sort} onSort={aggSort.toggle} style={thRight}>Return rate</SortableTh>
                  </tr>
                </thead>
                <tbody>
                  {aggSort.sorted.map(r => (
                    <tr key={r.name} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '8px' }}>{r.name}</td>
                      <td style={{ padding: '8px', textAlign: 'right' }}>{r.countable}</td>
                      <td style={{ padding: '8px', textAlign: 'right' }}>{r.voided}</td>
                      <td style={{ padding: '8px', textAlign: 'right' }}>{pct(r.void_share)}</td>
                      <td style={{ padding: '8px', textAlign: 'right',
                                   fontVariantNumeric: 'tabular-nums' }}>
                        {fmt(r.amount_voided)}
                      </td>
                      <td style={{ padding: '8px', textAlign: 'right' }}>{r.return}</td>
                      <td style={{ padding: '8px', textAlign: 'right' }}>{pct(r.return_share)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!(view === 'rep' ? repRows : storeRows).length && (
                <p style={{ fontSize: 13, color: 'var(--text2)', marginTop: 10 }}>
                  Nobody voided or returned anything in this period.
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
