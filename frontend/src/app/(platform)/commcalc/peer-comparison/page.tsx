'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import DrillDownPanel, {
  DrillTileLinks, useDrillTile, type DrillExtraColumn, type DrillPanel, type DrillRow,
} from '@/components/DrillDownPanel'
import StandardFilterBar from '@/components/StandardFilterBar'
import StatTile from '@/components/StatTile'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import type { StoreOpt } from '@/lib/market-store-cascade'

// PEER SALES COMPARISON — owner directive 2026-10-08: compare stores that have SIMILAR BILL PAYMENTS,
// because bill payments measure the people walking through the door. Inside a traffic band the
// comparison is fair, so a gap in boxes sold is a gap in SELLING rather than in footfall — "if one can
// do why not the other".
//
// The backend does ALL of the math and all of the honesty (see commcalc/peer_comparison.py): every
// column is a roll-up of the one shared sales aggregation, so these numbers are the same ones the
// Sales Report shows. This page renders and never computes — in particular it never fills a blank with
// a 0, because a blank here means "cannot be answered for this tenant", which the caveats explain.
//
// WHERE THE MEASURED FIGURES CAME FROM (index §CORE-17.3u). The server serves one panel per COUNT
// this page prints — the lagging count in the headline card, and each traffic band's store count on
// its tile — from commcalc/sales_outcome_drilldown.py, folded over the SAME per-store rows the table
// below prints, out of the same sales relations.
//
// THE MARKET / STORE PICKER IS A SUBJECT, NOT A FILTER (owner ask 2026-11-12, index §59.10). Picking
// NJ does NOT narrow the table to NJ: it would destroy the comparison, because every band would then
// hold NJ stores only and the band median would become NJ's own — the screen would tell the DM he is
// average. So the pick is a SPOTLIGHT: the tiles at the top hold the selection against the OTHER
// stores in its own traffic band(s), and the table below keeps every store in scope. The peer median
// on those tiles EXCLUDES the selection's own stores for the same reason; the per-store band median
// in the table still includes the store, and the two carry different names.
//
// THIS PAGE IS SERVER-FOLDED AND IS DELIBERATELY *NOT* NARROWED. Every filter on it — the month, the
// ranked metric, the band cut-offs and the market / store subject — is a QUERY PARAMETER (see `load`
// below),
// so the server folds again on every change and each panel follows its figure by construction. There
// is no browser fold here and `lib/drill-narrow` must not appear in this file;
// `harness_batch21_drilldown.py` §G fails the build in BOTH directions if that changes.
//
// WHAT IS REFUSED A PANEL, AND WHY. Boxes per bill payment, accessory $ per box and family plan % are
// RATES (§CORE-17.3h). The band's best, median and leader are order statistics over the band, not a
// fold of it, so no row list adds up to them. The gap columns are a store's distance from its band
// median — a subtraction composed at the TOTAL, which does not drill (§CORE-17.3k). Bill payments and
// the new/port/BYOD/upgrade/swap/tablet parts are counts of DISTINCT transactions, which is why the
// payload itself says the parts are not a partition: a row list under them would be re-counting
// transactions this page never summed. The two COUNTS above are what remains, and they are served.

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

// The columns a peer store row carries beside its count, in the producer's own words.
const PEER_DRILL_COLUMNS: DrillExtraColumn[] = [
  { header: 'Market', get: (r: DrillRow) => String(r.fields?.market ?? '—'),
    sortValue: (r: DrillRow) => String(r.fields?.market ?? '') },
  { header: 'Bill payments', get: (r: DrillRow) => String(r.fields?.['bill payments'] ?? '—'),
    sortValue: (r: DrillRow) => Number(r.fields?.['bill payments'] ?? 0) },
  { header: 'Boxes', get: (r: DrillRow) => String(r.fields?.boxes ?? '—'),
    sortValue: (r: DrillRow) => Number(r.fields?.boxes ?? 0) },
  { header: 'Peers in band', get: (r: DrillRow) => String(r.fields?.['peers in band'] ?? '—'),
    sortValue: (r: DrillRow) => Number(r.fields?.['peers in band'] ?? 0) },
]

// THE SUBJECT PAYLOAD, as the server serves it (§59.10). Typed rather than read through `any`: these
// are the figures a manager is held to account on, and a renamed field must fail the type-check here
// rather than render an empty tile that reads as "nothing to answer for".
type FocusBand = {
  band: number
  label: string | null
  subject_stores: number
  peer_stores: number
  subject_median: number | null
  peer_median: number | null
  peer_best: number | null
  peer_leader: string | null
  gap_to_peer_median: number | null
  gap_to_peer_best: number | null
  comparable: boolean
  verdict: 'behind' | 'ahead' | 'level' | 'not_measured'
  note: string | null
}
type FocusSubject = {
  kind: 'market' | 'store' | 'estate'
  label: string
  stores: string[]
  markets: string[]
  unknown: string[]
}
type FocusAccountable = {
  dms: { name: string; stores: string[] }[]
  no_dm: string[]
  stores: number
  error: string | null
  note: string | null
}
type FocusPayload = {
  subject: FocusSubject
  metric: string
  metric_label: string
  higher_is_better: boolean
  measurable: boolean
  reason: string | null
  stores_in_view: number
  peer_stores: number
  not_banded: string[]
  not_banded_reason: string | null
  gap_median: number | null
  verdict: 'behind' | 'ahead' | 'level' | 'not_measured'
  behind: { store: string; prompt?: string }[]
  behind_count: number
  bands: FocusBand[]
  peer_basis: string
  composite_basis: string
  sentence: string
  accountable?: FocusAccountable
}

const SEV_STYLE: Record<string, { bg: string; fg: string; icon: string }> = {
  cannot_answer: { bg: 'var(--c-bg-e0e7ff)', fg: 'var(--c-fg-3730a3)', icon: '◻︎' },
  understated: { bg: 'var(--c-bg-fef3c7)', fg: 'var(--c-fg-92400e)', icon: '⚠︎' },
  partial: { bg: 'var(--c-bg-fef3c7)', fg: 'var(--c-fg-92400e)', icon: '⚠︎' },
}

export default function PeerComparisonPage() {
  // ONE filter state for the shared bar (§3d): the month, and the market / store SUBJECT. The two
  // module-specific controls (the ranked metric, the band cut-offs) ride in the bar's `right` slot —
  // the core set is extended, never substituted.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter(thisMonth()))
  const [metric, setMetric] = useState('')
  const [bands, setBands] = useState('')          // '' = the tenant's / house cuts
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const period = filt.period && filt.period.length === 7 ? filt.period : thisMonth()
  const selMarkets = filt.markets
  const selStores = filt.stores

  const load = useCallback(() => {
    setLoading(true)
    const qs = new URLSearchParams({ period })
    // `focus_*`, NEVER `markets` — see the note at the top of this file. The subject is served as a
    // spotlight over the whole in-scope estate; sending it as the narrowing parameter would be the
    // defect the backend's own §59.10 header names.
    if (selMarkets.length) qs.set('focus_markets', selMarkets.join(','))
    if (selStores.length) qs.set('focus_stores', selStores.join(','))
    if (metric) qs.set('metric', metric)
    if (bands.trim()) qs.set('bands', bands.trim())
    api(`/api/v1/commcalc/peer-comparison?${qs.toString()}${orgParam()}`)
      .then(setData)
      .catch(e => setData({ error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [period, selMarkets, selStores, metric, bands])
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
  // The cascade roster, as the server composed it — one entry per in-scope store WITH its market, so
  // picking a market narrows the store dropdown to that market's stores. The page builds no roster of
  // its own: what you can pick is what you were served.
  const storeRoster: StoreOpt[] = useMemo(
    () => (data?.store_options || []).map((o: { store: string; market?: string }) =>
      ({ id: o.store, label: o.store, market: o.market || '' })),
    [data?.store_options])
  const focus: FocusPayload | null = data?.focus || null

  // The server's fold, printed as served. The shared offer gate carries NO extra condition here:
  // the figure and its panel came out of the same fold over the same rows, so a link appears for
  // exactly the figures a row list exists for.
  const peerPanels: Record<string, DrillPanel> = data?.drilldown || {}
  const peerDrill = useDrillTile(peerPanels)

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
        : <div style={{ fontSize: 11, color: 'var(--c-fg-92400e)', marginTop: 4 }}>{b.note}</div>}
      {/* The store COUNT on this tile opens; the best / median / leader beside it are order
          statistics over the band and never do. */}
      <DrillTileLinks drills={peerDrill.dl(`band.${b.band}`, 'which stores')}
                      open={peerDrill.open} onToggle={peerDrill.toggle} />
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

      {/* THE SHARED FILTER BAR (§3d, §CORE-27): the month and the market / store cascade, over the
          org-scoped roster the server composed. The two controls that belong to THIS report only —
          which measure to rank on, and the band cut-offs — ride in `right`, which extends the core
          set rather than replacing it. */}
      <StandardFilterBar
        value={filt} onChange={setFilt}
        periodMode="month"
        show={{ period: true, stores: true, markets: true, reps: false }}
        cascadeStores={storeRoster}
        marketOptions={marketOpts}
        storeLabel="Compare a store…"
        marketLabel="Compare a market…"
        right={
          <>
            <label style={{ fontSize: 12, color: 'var(--text2)' }} title="Which measure the comparison tiles, the lagging list and the gap columns rank on.">
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
            {bands.trim() !== '' &&
              <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setBands('')}>Clear bands</button>}
          </>
        }
      />
      <div style={{ fontSize: 11, color: 'var(--text3)', margin: '6px 0 12px' }}>
        Picking a market or a store does <b>not</b> hide the rest of the estate — it makes that the
        <b> subject</b> of the tiles below and holds it against the other stores in its own traffic
        band. Narrowing the table would make the selection its own band median, which is the one
        comparison this report must never show.
      </div>

      {data?.error &&
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: 'var(--c-bg-fee2e2)', color: 'var(--c-fg-991b1b)', fontSize: 13 }}>
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

      {/* ════ THE SUBJECT — the selected market or store against the stores it competes with ════
          Owner ask 2026-11-12 (index §59.10). Every figure here is the server's; this page prints it.
          The two COUNTS open to their store rows (§CORE-17.3u); the peer median, the peer best and
          the gaps do not, and the panel declaration states the ruling that refuses each. */}
      {!loading && !data?.error && focus && focus.subject?.kind !== 'estate' && <>
        <div className="stat-grid" style={{ marginBottom: 10 }}>
          <StatTile hero
            label={`${focus.subject?.label || 'Selection'} vs its traffic-band peers`}
            value={focus.gap_median === null || focus.gap_median === undefined
              ? '—'
              : `${focus.gap_median > 0 ? '−' : focus.gap_median < 0 ? '+' : ''}${Math.abs(focus.gap_median).toFixed(2)}`}
            sub={focus.sentence || focus.reason || ''}
            icon={focus.verdict === 'behind' ? '🎯' : focus.verdict === 'ahead' ? '🏆' : '⚖︎'}
            accent={focus.verdict === 'behind' ? 'var(--red, #dc2626)'
              : focus.verdict === 'ahead' ? 'var(--green, #16a34a)' : undefined}
            delta={focus.measurable
              ? { value: `${focus.metric_label} vs peer median`, dir: focus.verdict === 'behind' ? 'down' : focus.verdict === 'ahead' ? 'up' : 'flat' }
              : undefined} />
          <StatTile label="Stores in the comparison" value={num(focus.stores_in_view)}
            icon="🏬"
            sub={<>
              {focus.peer_stores} peer store{focus.peer_stores === 1 ? '' : 's'} in the same band
              {focus.not_banded?.length > 0 &&
                <> · <span title={focus.not_banded_reason || ''}>{focus.not_banded.length} with no traffic to band by</span></>}
              <DrillTileLinks drills={peerDrill.dl('focus.stores', 'which stores')}
                              open={peerDrill.open} onToggle={peerDrill.toggle} />
            </>} />
          <StatTile label="Behind their own band" value={num(focus.behind_count)}
            icon="⚠︎"
            accent={focus.behind_count > 0 ? 'var(--red, #dc2626)' : undefined}
            sub={<>
              of {focus.stores_in_view} compared, on {String(focus.metric_label || '').toLowerCase()}
              <DrillTileLinks drills={peerDrill.dl('focus.behind', 'which stores')}
                              open={peerDrill.open} onToggle={peerDrill.toggle} />
            </>} />
          {/* WHO ANSWERS FOR IT — the org tree's own answer. A manager it cannot name is said out
              loud rather than left blank: a blank reads as a market nobody has to answer for. */}
          <StatTile label="Accountable"
            value={focus.accountable?.dms?.length
              ? focus.accountable.dms.map(d => d.name).join(', ')
              : '—'}
            icon="👤"
            sub={focus.accountable?.note || (focus.accountable?.dms?.length
              ? `district manager${focus.accountable.dms.length === 1 ? '' : 's'} for the selected stores`
              : 'the org tree names nobody for this selection')} />
        </div>

        {/* THE COMPARISON, BAND BY BAND. Figures from different bands are never averaged together —
            different bands saw different numbers of people — so the table is per band and the
            headline above is a median of the gaps, not of the levels. */}
        {focus.bands?.length > 0 &&
          <div className="card" style={{ padding: '12px 16px', marginBottom: 18, overflowX: 'auto' }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
              {focus.subject?.label} inside each of its traffic bands
            </div>
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
                  <th style={{ padding: '4px 8px' }}>Traffic band</th>
                  <th style={{ padding: '4px 8px', textAlign: 'right' }}>Its stores</th>
                  <th style={{ padding: '4px 8px', textAlign: 'right' }}>Competing stores</th>
                  <th style={{ padding: '4px 8px', textAlign: 'right' }}>{focus.metric_label} — it</th>
                  <th style={{ padding: '4px 8px', textAlign: 'right' }}>peers</th>
                  <th style={{ padding: '4px 8px', textAlign: 'right' }}>gap</th>
                  <th style={{ padding: '4px 8px' }}>Best peer</th>
                </tr>
              </thead>
              <tbody>
                {focus.bands.map((b: FocusBand) => (
                  <tr key={b.band} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px 8px' }}>{b.label}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{b.subject_stores}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{b.peer_stores}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{num(b.subject_median, 2)}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{num(b.peer_median, 2)}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', color: gapColor(b.verdict === 'behind'), fontWeight: 600 }}>
                      {num(b.gap_to_peer_median, 2)}
                    </td>
                    <td style={{ padding: '6px 8px', color: 'var(--text2)' }}>
                      {b.comparable
                        ? <>{b.peer_leader || '—'}{b.peer_best !== null && b.peer_best !== undefined ? ` at ${b.peer_best.toFixed(2)}` : ''}</>
                        : <span style={{ color: 'var(--c-fg-92400e)' }}>{b.note}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>
              ℹ︎ {focus.peer_basis} {focus.composite_basis}
            </div>
          </div>}

        {!focus.measurable && focus.reason &&
          <div className="card" style={{ padding: '10px 14px', marginBottom: 18, background: 'var(--c-bg-fef3c7)', color: 'var(--c-fg-92400e)', fontSize: 13 }}>
            <b>⚠︎ This selection cannot be compared.</b> {focus.reason}
          </div>}

        {focus.subject?.unknown?.length > 0 &&
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 14 }}>
            Not found in this month’s data for your scope, so not part of the comparison:{' '}
            <b>{focus.subject.unknown.join(', ')}</b>.
          </div>}
      </>}

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
          <DrillTileLinks drills={peerDrill.dl('behind_median', 'every store weighed for this list')}
                          open={peerDrill.open} onToggle={peerDrill.toggle} />
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 9 }}>
            Ranked on <b>{gapMetrics.find(m => m.key === activeMetric)?.label || activeMetric}</b> against
            each store’s own band median. A store alone in its band is never listed — it has no peer to
            be behind.
          </div>
        </div>}

      {/* ONE renderer for whichever count is open — the band tiles and the lagging card both
          toggle into it, so the rows appear in one place rather than reflowing the tile strip. */}
      {peerDrill.panel && <DrillDownPanel panel={peerDrill.panel} extraColumns={PEER_DRILL_COLUMNS} />}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data?.error && rows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          {data?.note || 'No stores to compare for this month.'}
        </div>
      ) : !data?.error ? (
        <ReportShell
          title={`Peer Sales Comparison — ${data?.period || period}`}
          subtitle={`Stores grouped by bill-payment volume · ${rows.length} stores in ${(data?.bands || []).length} traffic bands${focus?.subject?.label ? ` · comparing ${focus.subject.label} against its band peers (the table is not narrowed)` : ''}`}
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
