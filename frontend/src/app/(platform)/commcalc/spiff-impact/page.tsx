'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { MultiSelect } from '@/lib/multiselect'

// SPIFF IMPACT — owner ask 2026-10-08: *"create a report for management review to assess the affect of
// a certain spiff on the overall commisison payout revenue for the store and what % does that help to
// increase the profitablity and then report whoich stores are lakcing those sales in terms of % sales
// which are contriuting to that profitability"*.
//
// The backend does ALL of the math and all of the honesty (see commcalc/spiff_impact.py): the dollars
// are the carrier statement classified exactly as the P&L classifies it, the profit is the stored
// per-store P&L, and "behind its traffic band" is the peer comparison's own verdict. This page renders
// and never computes — in particular it never fills a blank with a 0, because a blank here means
// "cannot be answered", which the caveats explain, and a 0 would read as a result.

const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
function thisMonth() { return new Date().toISOString().slice(0, 7) }

// A figure the backend could not answer comes back null. Em dash, never 0.
function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return '—'
  return digits ? v.toFixed(digits) : String(v)
}
// §58 hands a period-rename twin back as a (description, category) PAIR, so rendering it raw would
// put a JSON array in front of a manager. Name the description — the thing he would recognise.
function twinName(v: any): string {
  if (Array.isArray(v)) return v.length ? String(v[0]) : ''
  return v === null || v === undefined ? '' : String(v)
}
function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(digits)}%`
}

const SEV_STYLE: Record<string, { bg: string; fg: string; icon: string }> = {
  different_line: { bg: '#fee2e2', fg: '#991b1b', icon: '⚑' },
  not_declared: { bg: '#e0e7ff', fg: '#3730a3', icon: '◻︎' },
  different_cohort: { bg: '#fef3c7', fg: '#92400e', icon: '⚠︎' },
  unresolved_identity: { bg: '#fef3c7', fg: '#92400e', icon: '⚠︎' },
  no_snapshot: { bg: '#fef3c7', fg: '#92400e', icon: '⚠︎' },
  as_computed: { bg: 'var(--surface2)', fg: 'var(--text2)', icon: '🕑' },
  loss_base: { bg: 'var(--surface2)', fg: 'var(--text2)', icon: 'ℹ︎' },
}
const CAVEAT_TITLE: Record<string, string> = {
  different_line: 'This pay type is not commission',
  not_declared: 'Not declared by this org',
  different_cohort: 'Paid units are an instalment',
  unresolved_identity: 'Store identity',
  no_snapshot: 'Profit not computed',
  as_computed: 'Profit figures are a snapshot',
  loss_base: 'No profit base to lift',
}

export default function SpiffImpactPage() {
  const [period, setPeriod] = useState(thisMonth())
  const [spiff, setSpiff] = useState('')          // '' = let the backend open on the largest spiff
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [bands, setBands] = useState('')          // '' = the tenant's / house traffic-band cuts
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    const qs = new URLSearchParams({ period })
    if (spiff) qs.set('spiff', spiff)
    if (selMarkets.length) qs.set('markets', selMarkets.join(','))
    if (bands.trim()) qs.set('bands', bands.trim())
    api(`/api/v1/commcalc/spiff-impact?${qs.toString()}${orgParam()}`)
      .then(setData)
      .catch(e => setData({ error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [period, spiff, selMarkets, bands])
  useEffect(() => { load() }, [load])

  const rows: any[] = data?.rows || []
  const options: any[] = data?.options || []
  const lagging: any[] = data?.lagging || []
  const caveats: any[] = data?.caveats || []
  const estate: any = data?.estate || {}
  const entry: any = data?.selected_entry || null

  const cols = useMemo<ExportColumn[]>(() => ([
    { header: 'Traffic band', get: r => r.band_label || 'Not banded' },
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Market', get: r => r.market || '—' },
    { header: 'Spiff $', get: r => r.spiff_amount, money: true },
    { header: 'Paid units', get: r => r.spiff_units, align: 'right' },
    { header: `${data?.pl_line === data?.commission_line ? 'Commission' : 'Line'} $ (all carrier)`, get: r => r.line_total, money: true },
    { header: 'Share of that line', get: r => pct(r.share_of_line_pct, 2), align: 'right' },
    { header: 'Share of all carrier $', get: r => pct(r.share_of_carrier_pct, 2), align: 'right' },
    { header: 'Net profit', get: r => (r.net_income === null || r.net_income === undefined ? '—' : r.net_income), money: true },
    { header: 'Net profit without it', get: r => (r.net_income_ex_spiff === null || r.net_income_ex_spiff === undefined ? '—' : r.net_income_ex_spiff), money: true },
    { header: 'Profit lift', get: r => pct(r.profit_lift_pct, 1), align: 'right' },
    { header: '% of revenue', get: r => pct(r.margin_points, 2), align: 'right' },
    { header: 'Bill payments', get: r => num(r.billpay_txns), align: 'right' },
    { header: 'Boxes', get: r => num(r.boxes), align: 'right' },
    { header: 'Units per 100 boxes', get: r => num(r.spiff_per_100_boxes, 1), align: 'right' },
    { header: 'Behind band median', get: r => (r.gaps?.behind_median ? 'yes' : r.gaps ? 'no' : '—') },
    { header: 'Gap to band median', get: r => num(r.gaps?.gap_to_median, 1), align: 'right' },
    { header: 'Band best', get: r => num(r.gaps?.band_best, 1), align: 'right' },
  ]), [data?.pl_line, data?.commission_line])

  const marketOpts: string[] = data?.markets || []

  const Tile = ({ label, value, sub, warn }: { label: string; value: string; sub?: string; warn?: boolean }) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 190 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 3, color: warn ? '#92400e' : 'var(--text1)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🎁 Spiff Impact</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Pick one of the carrier’s pay types and see, per store, <b>what it is worth to the commission
          payout revenue</b>, <b>by how much it raises net profit</b> (profit with it against profit
          without it), and <b>which stores are not earning it</b> on the boxes they sell — ranked inside
          each store’s own bill-payment traffic band, so the comparison is sell-through and not footfall.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Month{' '}
          <input type="month" style={sel} value={period.length === 7 ? period : thisMonth()} onChange={e => setPeriod(e.target.value)} />
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }} title="Every pay type the carrier paid in this month, biggest first. The list comes from the statement itself, so a pay type the carrier invented this quarter is here as soon as it lands.">
          Spiff / pay type{' '}
          <select style={{ ...sel, maxWidth: 360 }} value={spiff || data?.selected || ''} onChange={e => setSpiff(e.target.value)}>
            {options.map(o => (
              <option key={o.type} value={o.type}>
                {o.type} — {fmt(o.amount)}{o.component ? ` · ${o.component.toLowerCase()}` : ''}{o.declared ? '' : ' · not declared'}
              </option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }} title="Bill-payment cut-offs that define the traffic bands — the lower bound of each band, comma separated. Blank uses this tenant's configured bands.">
          Band cut-offs{' '}
          <input style={{ ...sel, width: 120 }} placeholder={(data?.band_cuts || []).join(', ') || 'default'}
                 value={bands} onChange={e => setBands(e.target.value)} />
        </label>
        {marketOpts.length > 0 && <MultiSelect allLabel="All markets" width={150} value={selMarkets} options={marketOpts} onChange={setSelMarkets} />}
        {(selMarkets.length > 0 || bands.trim() !== '' || spiff !== '') &&
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setSelMarkets([]); setBands(''); setSpiff('') }}>Clear</button>}
      </div>

      {data?.error &&
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: '#fee2e2', color: '#991b1b', fontSize: 13 }}>
          <b>❌ Spiff Impact could not be built.</b> {data.error}
        </div>}

      {/* The report never silently chooses its own subject. */}
      {!loading && !data?.error && data?.selection_basis && data.selection_basis !== 'requested' &&
        <div className="card" style={{ padding: '10px 14px', marginBottom: 12, background: 'var(--surface2)', fontSize: 13, color: 'var(--text2)' }}>
          {data.selection_basis === 'requested_not_found'
            ? <><b>That pay type is not on this month’s statement.</b> Pick one from the list — it is built from the statement itself.</>
            : data.selection_basis === 'none_available'
              ? <><b>No pay type to assess.</b> {data?.note}</>
              : <>Showing <b>{data.selected}</b> — the largest pay type of its kind on this month’s statement, chosen because none was picked. Use the dropdown for any other.</>}
        </div>}

      {/* WHAT THIS TABLE CANNOT ANSWER, above the numbers: every one of these reads as a plausible
          performance figure, and a manager must not coach a rep on a classification or a setting. */}
      {!loading && caveats.length > 0 &&
        <div style={{ marginBottom: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {caveats.map((c, i) => {
            const s = SEV_STYLE[c.severity] || SEV_STYLE.loss_base
            return (
              <div key={i} className="card" style={{ padding: '10px 14px', background: s.bg, color: s.fg, fontSize: 13 }}>
                <b>{s.icon} {CAVEAT_TITLE[c.severity] || c.column}</b> {c.message}
              </div>
            )
          })}
        </div>}

      {!loading && !data?.error && rows.length > 0 &&
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
          <Tile label="This spiff paid" value={fmt(estate.spiff_amount || 0)}
                sub={`${(estate.spiff_units || 0).toLocaleString()} paid units · ${estate.stores || 0} stores`} />
          <Tile label={data?.pl_line === data?.commission_line ? 'Share of commission revenue' : 'Share of its own P&L line'}
                value={pct(estate.share_of_line_pct, 2)}
                sub={`of ${fmt(estate.line_total || 0)} on ${data?.pl_line || '—'}`}
                warn={data?.pl_line !== data?.commission_line} />
          <Tile label="Net profit lift" value={pct(estate.profit_lift_pct, 1)}
                sub={estate.profit_lift_pct === null
                  ? 'no positive profit base — see below'
                  : `${fmt(estate.net_income || 0)} with it vs ${fmt(estate.net_income_ex_spiff || 0)} without`}
                warn={estate.profit_lift_pct === null} />
          <Tile label="Worth, as % of revenue" value={pct(estate.margin_points, 2)}
                sub={`of ${fmt(estate.revenue || 0)} revenue`} />
          <Tile label="Paid units per 100 boxes" value={num(estate.spiff_per_100_boxes, 1)}
                sub={`${(estate.boxes || 0).toLocaleString()} boxes sold`} />
        </div>}

      {!loading && !data?.error && entry &&
        <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12 }}>
          <b>{entry.type}</b> — classified <b>{String(entry.component || 'unresolved').toLowerCase()}</b>,
          booking to <b>{entry.pl_line}</b>{entry.declared
            ? ' on this org’s own declared pay category'
            : entry.inferred && twinName(entry.twin_of)
              ? ` on an inference from this org’s earlier declaration of “${twinName(entry.twin_of)}”`
              : ' on the platform’s keyword fallback'}.
          {data?.lift_note && <> · {data.lift_note}</>}
        </div>}

      {/* THE POINT OF THE REPORT — who is not earning it on the sales they make. The sentence is the
          peer comparison's own (peer_comparison.prompt_sentence), so this screen, the peer screen and
          the action plan all say the same thing about the same store. */}
      {!loading && lagging.length > 0 &&
        <div className="card" style={{ padding: '14px 16px', marginBottom: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
            🎯 {lagging.length} store{lagging.length === 1 ? '' : 's'} earning less of this than their own traffic band
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
            {lagging.map((l, i) => (
              <div key={i} style={{ fontSize: 13, display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <span style={{ fontWeight: 700, minWidth: 92 }}>{l.store}</span>
                <span style={{ color: 'var(--text2)' }}>
                  {l.prompt} <b>{fmt(l.spiff_amount || 0)}</b> earned on this pay type.
                </span>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 9 }}>
            Ranked on <b>{data?.metric_label}</b> against each store’s own band median. A store alone in
            its band is never listed — it has no peer to be behind.
          </div>
        </div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data?.error && rows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          {data?.note || 'No carrier pay types on this month’s statement.'}
        </div>
      ) : !data?.error ? (
        <ReportShell
          title={`Spiff Impact — ${data?.selected || ''} · ${data?.period || period}`}
          subtitle={`${rows.length} stores${selMarkets.length ? ' · filtered' : ''} · profit from the stored per-store P&L`}
          filename={`spiff-impact-${data?.period || period}`}
          columns={cols}
          rows={rows}
          totals
          stickyHeader
          defaultGroupBy="Traffic band"
          collapsibleGroups
          groupPersistKey="spiff-impact:groupBy"
        />
      ) : null}

      {!loading && rows.length > 0 && data?.basis_note &&
        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>ℹ︎ {data.basis_note}</div>}

      {/* A comparison that did not run is not the same as no store being behind. */}
      {!loading && data?.peer_meta && data.peer_meta.ran === false &&
        <div className="card" style={{ padding: '10px 14px', marginTop: 14, background: '#fef3c7', color: '#92400e', fontSize: 13 }}>
          <b>⚠︎ The traffic-band comparison did not run</b>, so the boxes, the bands and the “behind its
          band” columns are blank for every store. That is not a finding of nobody lagging.
          {data.peer_meta.error && <> ({data.peer_meta.error})</>}
        </div>}
    </div>
  )
}
