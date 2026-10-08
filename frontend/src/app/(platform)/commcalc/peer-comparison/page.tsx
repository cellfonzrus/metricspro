'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { MultiSelect } from '@/lib/multiselect'

// PEER SALES COMPARISON — owner directive 2026-10-08: compare stores that have SIMILAR BILL PAYMENTS,
// because bill payments measure the people walking through the door. Inside a traffic band the
// comparison is fair, so a gap in boxes sold is a gap in SELLING rather than in footfall — "if one can
// do why not the other".
//
// The backend does ALL of the math and all of the honesty (see commcalc/peer_comparison.py): every
// column is a roll-up of the one shared sales aggregation, so these numbers are the same ones the
// Sales Report shows. This page renders and never computes — in particular it never fills a blank with
// a 0, because a blank here means "cannot be answered for this tenant", which the caveats explain.

const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
function thisMonth() { return new Date().toISOString().slice(0, 7) }

// A number the backend could not answer comes back null. It renders as an em dash with a reason on
// hover — NEVER as 0, which a manager would read as a performance result.
function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return '—'
  return digits ? v.toFixed(digits) : String(v)
}
// How far behind the band median, as a colour. Ahead of median is not celebrated — the point of the
// report is the gap, and colouring every leader green turns the table into a scoreboard.
function gapColor(behind: boolean | undefined): string {
  return behind ? 'var(--red, #dc2626)' : 'var(--text1)'
}

const SEV_STYLE: Record<string, { bg: string; fg: string; icon: string }> = {
  cannot_answer: { bg: '#e0e7ff', fg: '#3730a3', icon: '◻︎' },
  understated: { bg: '#fef3c7', fg: '#92400e', icon: '⚠︎' },
  partial: { bg: '#fef3c7', fg: '#92400e', icon: '⚠︎' },
}

export default function PeerComparisonPage() {
  const [period, setPeriod] = useState(thisMonth())
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [metric, setMetric] = useState('')
  const [bands, setBands] = useState('')          // '' = the tenant's / house cuts
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    const qs = new URLSearchParams({ period })
    if (selMarkets.length) qs.set('markets', selMarkets.join(','))
    if (metric) qs.set('metric', metric)
    if (bands.trim()) qs.set('bands', bands.trim())
    api(`/api/v1/commcalc/peer-comparison?${qs.toString()}${orgParam()}`)
      .then(setData)
      .catch(e => setData({ error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [period, selMarkets, metric, bands])
  useEffect(() => { load() }, [load])

  const rows: any[] = data?.rows || []
  const lagging: any[] = data?.lagging || []
  const caveats: any[] = data?.caveats || []
  const unbanded: any[] = data?.unbanded || []
  const parts: any[] = data?.box_parts || []
  const gapMetrics: any[] = data?.gap_metrics || []
  const activeMetric = metric || data?.default_gap_metric || 'boxes_per_billpay'

  // The table is GROUPED BY BAND, because a row only means anything beside its own peers. The band
  // label is the group key, so collapsing a band hides a comparison rather than a set of unrelated
  // stores.
  const cols = useMemo<ExportColumn[]>(() => {
    const base: ExportColumn[] = [
      { header: 'Traffic band', get: r => r.band_label },
      { header: 'Store', get: r => r.store, role: 'store' },
      { header: 'Market', get: r => r.market || '—' },
      { header: 'Bill payments', get: r => r.billpay_txns, align: 'right' },
      { header: 'Boxes', get: r => r.boxes, align: 'right' },
      { header: 'Boxes per bill payment', get: r => num(r.boxes_per_billpay, 2), align: 'right' },
    ]
    // the drill-down, in the owner's order, straight from the payload
    parts.forEach(p => base.push({ header: p.label, get: r => (r.parts || {})[p.key] ?? '—', align: 'right' }))
    base.push(
      { header: 'Add-a-line', get: r => num(r.aal), align: 'right' },
      { header: 'Family plan %', get: r => num(r.family_plan_pct, 2), align: 'right' },
      { header: 'Accessory $', get: r => r.accessory_revenue, money: true },
      { header: 'Accessory $ per box', get: r => num(r.accessory_per_box, 2), align: 'right' },
      { header: 'Peers in band', get: r => r.peers, align: 'right' },
      { header: 'Behind band median', get: r => ((r.gaps || {})[activeMetric]?.behind_median ? 'yes' : 'no') },
      { header: 'Gap to band median', get: r => num((r.gaps || {})[activeMetric]?.gap_to_median, 2), align: 'right' },
      { header: 'Gap to band best', get: r => num((r.gaps || {})[activeMetric]?.gap_to_best, 2), align: 'right' },
    )
    return base
  }, [parts, activeMetric])

  const marketOpts: string[] = data?.markets || []

  const BandTile = ({ b }: { b: any }) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 190, background: b.comparable ? 'var(--surface)' : 'var(--surface2)' }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{b.label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 3 }}>
        <div style={{ fontSize: 22, fontWeight: 700 }}>{b.stores}</div>
        <div style={{ fontSize: 12, color: 'var(--text2)' }}>store{b.stores === 1 ? '' : 's'}</div>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>
        {b.billpay_low.toLocaleString()}–{b.billpay_high.toLocaleString()} bill payments
      </div>
      {b.comparable
        ? <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 4 }}>
            Best <b>{b.boxes_best}</b> boxes · median <b>{b.boxes_median}</b> · leader <b>{b.leader}</b>
          </div>
        : <div style={{ fontSize: 11, color: '#92400e', marginTop: 4 }}>{b.note}</div>}
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏁 Peer Sales Comparison</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Stores grouped by <b>bill-payment volume</b> — the measure of how many people walk through the
          door — then compared on what they do with that traffic: <b>total boxes</b> with the
          new / port / BYOD / upgrade / swap / tablet split beside it, <b>add-a-line</b>,
          <b> family plan %</b>, <b>total accessory $</b> and <b>accessory $ per box</b>. Within a band
          the footfall is the same, so a gap is sell-through, not location.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Month{' '}
          <input type="month" style={sel} value={period.length === 7 ? period : thisMonth()} onChange={e => setPeriod(e.target.value)} />
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }} title="Which measure the lagging list and the gap columns rank on.">
          Compare on{' '}
          <select style={sel} value={activeMetric} onChange={e => setMetric(e.target.value)}>
            {gapMetrics.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }} title="Bill-payment cut-offs that define the bands — the lower bound of each band, comma separated. Blank uses this tenant's configured bands.">
          Band cut-offs{' '}
          <input style={{ ...sel, width: 130 }} placeholder={(data?.band_cuts || []).join(', ') || 'default'}
                 value={bands} onChange={e => setBands(e.target.value)} />
        </label>
        {marketOpts.length > 0 && <MultiSelect allLabel="All markets" width={150} value={selMarkets} options={marketOpts} onChange={setSelMarkets} />}
        {(selMarkets.length > 0 || bands.trim() !== '') &&
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setSelMarkets([]); setBands('') }}>Clear</button>}
      </div>

      {data?.error &&
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: '#fee2e2', color: '#991b1b', fontSize: 13 }}>
          <b>❌ Peer Sales Comparison could not be built.</b> {data.error}
        </div>}

      {/* WHAT THIS TABLE CANNOT ANSWER. Rendered ABOVE the numbers deliberately: each of these reads
          as a plausible performance figure, and a manager must not coach a rep on a setting. */}
      {!loading && caveats.length > 0 &&
        <div style={{ marginBottom: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {caveats.map((c, i) => {
            const s = SEV_STYLE[c.severity] || SEV_STYLE.partial
            return (
              <div key={i} className="card" style={{ padding: '10px 14px', background: s.bg, color: s.fg, fontSize: 13 }}>
                <b>{s.icon} {c.column === 'boxes' ? 'Boxes' : c.column === 'tablet' ? 'Tablet column' : 'Family plan %'}</b>{' '}
                {c.message}
              </div>
            )
          })}
        </div>}

      {!loading && !data?.error && data?.band_basis &&
        <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12 }}>
          <b>How stores are banded:</b> {data.band_basis}
          {data.kpi_feed && <> · {data.kpi_feed}</>}
        </div>}

      {/* the bands themselves */}
      {!loading && !data?.error && (data?.bands || []).length > 0 &&
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
          {data.bands.map((b: any) => <BandTile key={b.band} b={b} />)}
        </div>}

      {/* THE POINT OF THE REPORT — who is behind their own peers, and the sentence to coach with.
          The backend owns the sentence (peer_comparison.prompt_sentence) so the screen, the action
          plan and the report cards all say the same thing about the same store. */}
      {!loading && lagging.length > 0 &&
        <div className="card" style={{ padding: '14px 16px', marginBottom: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
            🎯 {lagging.length} store{lagging.length === 1 ? '' : 's'} behind their own traffic band
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
            {lagging.map((l, i) => (
              <div key={i} style={{ fontSize: 13, color: 'var(--text1)', display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <span style={{ fontWeight: 700, minWidth: 92 }}>{l.store}</span>
                <span style={{ color: 'var(--text2)' }}>{l.prompt}</span>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 9 }}>
            Ranked on <b>{gapMetrics.find(m => m.key === activeMetric)?.label || activeMetric}</b> against
            each store’s own band median. A store alone in its band is never listed — it has no peer to
            be behind.
          </div>
        </div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data?.error && rows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          {data?.note || 'No stores to compare for this month.'}
        </div>
      ) : !data?.error ? (
        <ReportShell
          title={`Peer Sales Comparison — ${data?.period || period}`}
          subtitle={`Stores grouped by bill-payment volume${selMarkets.length ? ' · filtered' : ''} · ${rows.length} stores in ${(data?.bands || []).length} traffic bands`}
          filename={`peer-comparison-${data?.period || period}`}
          columns={cols}
          rows={rows}
          totals
          stickyHeader
          defaultGroupBy="Traffic band"
          collapsibleGroups
          groupPersistKey="peer-comparison:groupBy"
        />
      ) : null}

      {/* The drill-down is NOT a partition of the box total, and saying so once under the table is
          cheaper than a manager re-adding the columns and reporting a discrepancy. */}
      {!loading && rows.length > 0 && data?.parts_are_not_a_partition &&
        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
          ℹ︎ {data.parts_are_not_a_partition}
        </div>}

      {/* Stores that cannot be banded. Shown, never dropped: a store missing from a comparison looks
          like a store with nothing to answer for. */}
      {!loading && unbanded.length > 0 &&
        <div className="card" style={{ padding: '14px 16px', marginTop: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>
            Not compared — {unbanded.length} store{unbanded.length === 1 ? '' : 's'} with no bill-payment traffic to band by
          </div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>{unbanded[0]?.reason}</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {unbanded.map((u, i) => (
              <span key={i} style={{ fontSize: 12, padding: '3px 9px', borderRadius: 11, background: 'var(--surface2)', color: 'var(--text2)' }}>
                {u.store} · {u.boxes} boxes · {fmt(u.accessory_revenue || 0)}
              </span>
            ))}
          </div>
        </div>}
    </div>
  )
}
