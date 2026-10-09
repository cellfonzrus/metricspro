'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { MultiSelect } from '@/lib/multiselect'

// PRODUCT MIX & PORT DISCIPLINE — owner directive 2026-10-09: *"sales by each store as per the product
// sold, if the store is selling more of a particular phone at a cheaper price or free it could be that
// the sales person is just pushing cheaper phones or free phones and not trying to sell higher end
// devices which bring more accessory sales , the report should highlight the sales reps whose accessory
// per box is low and also who are porting in less numbers - the logic is built but not displayed that
// the ports are low. the system should co-relate the two and provide an action plan for the store whoa
// re lagging."*
//
// The backend does ALL of the math and all of the honesty (see commcalc/product_mix.py): the boxes,
// accessory dollars and ports are the shared sales aggregation every other screen reads, the price band
// is money cuts stated on the payload, the verdict needs both signals, and "this store is behind" is the
// Peer Sales Comparison's own verdict. This page RENDERS and never computes — in particular it never
// fills a blank with a 0: a blank means "cannot be answered", the caveats say why, and a 0 would read as
// a result somebody gets coached on.

const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
function thisMonth() { return new Date().toISOString().slice(0, 7) }

function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return '—'
  return digits ? v.toFixed(digits) : String(v)
}
// A SHARE the backend withheld is a dash. It withholds it when the window cannot support the claim
// (too few device lines), and showing 0% there would accuse a rep of selling nothing but cheap phones.
function share(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return '—'
  return `${(v * 100).toFixed(digits)}%`
}
function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(digits)}%`
}

const VERDICT_STYLE: Record<string, { bg: string; fg: string; icon: string; label: string }> = {
  both: { bg: '#fee2e2', fg: '#991b1b', icon: '⚑', label: 'Low on both' },
  accessory_only: { bg: '#fef3c7', fg: '#92400e', icon: '⚠︎', label: 'Low accessory per box' },
  port_only: { bg: '#fef3c7', fg: '#92400e', icon: '⚠︎', label: 'Low port share' },
  clear: { bg: 'var(--surface2)', fg: 'var(--text2)', icon: '✓', label: 'At or above the median' },
}
const CAVEAT_STYLE: Record<string, { bg: string; fg: string; icon: string }> = {
  cannot_answer: { bg: '#fee2e2', fg: '#991b1b', icon: '⚑' },
  understated: { bg: '#fef3c7', fg: '#92400e', icon: '⚠︎' },
  partial: { bg: 'var(--surface2)', fg: 'var(--text2)', icon: 'ℹ︎' },
}

export default function ProductMixPage() {
  const [period, setPeriod] = useState(thisMonth())
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [bands, setBands] = useState('')          // '' = the house / tenant price cuts
  const [onlyFlagged, setOnlyFlagged] = useState(false)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    const qs = new URLSearchParams({ period })
    if (selMarkets.length) qs.set('markets', selMarkets.join(','))
    if (bands.trim()) qs.set('bands', bands.trim())
    api(`/api/v1/commcalc/product-mix?${qs.toString()}${orgParam()}`)
      .then(setData)
      .catch(e => setData({ error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [period, selMarkets, bands])
  useEffect(() => { load() }, [load])

  const reps: any[] = data?.reps || []
  const flagged: any[] = data?.flagged || []
  const stores: any[] = data?.stores || []
  const models: any[] = data?.models || []
  const plan: any[] = data?.action_plan || []
  const caveats: any[] = data?.caveats || []
  const cors: any[] = data?.correlations || []
  const basis: any = data?.basis || {}
  const bandDefs: any[] = data?.bands || []

  const shown = useMemo(() => (onlyFlagged ? reps.filter(r => r.verdict === 'both') : reps), [reps, onlyFlagged])

  const repCols = useMemo<ExportColumn[]>(() => ([
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Rep', get: r => r.rep },
    { header: 'Verdict', get: r => (r.verdict ? (VERDICT_STYLE[r.verdict]?.label || r.verdict) : 'Not ranked') },
    { header: 'Boxes', get: r => num(r.boxes), align: 'right' },
    { header: 'Accessory $', get: r => r.accessory_revenue, money: true },
    { header: 'Accessory $ per box', get: r => num(r.accessory_per_box, 2), align: 'right' },
    { header: 'Port-ins', get: r => num(r.ports), align: 'right' },
    { header: 'Port share', get: r => share(r.port_share, 1), align: 'right' },
    { header: 'Devices sold', get: r => num(r.device_lines), align: 'right' },
    { header: 'Avg device price', get: r => (r.avg_device_price === null || r.avg_device_price === undefined ? '—' : r.avg_device_price), money: true },
    ...bandDefs.map(b => ({ header: b.label, get: (r: any) => num(r.bands?.[b.key]), align: 'right' as const })),
    { header: 'Cheap share', get: r => share(r.cheap_share, 0), align: 'right' },
    { header: 'Free share', get: r => share(r.free_share, 0), align: 'right' },
    { header: 'Most sold', get: r => (r.top_models?.[0]?.model || '—') },
    { header: 'Its avg price', get: r => (r.top_models?.[0]?.avg_price ?? '—') },
  ]), [bandDefs])

  const storeCols = useMemo<ExportColumn[]>(() => ([
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Reps', get: r => num(r.reps), align: 'right' },
    { header: 'Flagged reps', get: r => num(r.flagged_reps), align: 'right' },
    { header: 'Boxes', get: r => num(r.boxes), align: 'right' },
    { header: 'Accessory $', get: r => r.accessory_revenue, money: true },
    { header: 'Accessory $ per box', get: r => num(r.accessory_per_box, 2), align: 'right' },
    { header: 'Port-ins', get: r => num(r.ports), align: 'right' },
    { header: 'Port share (ours)', get: r => share(r.port_share, 1), align: 'right' },
    { header: 'Port % (carrier)', get: r => pct(r.carrier_port_pct, 1), align: 'right' },
    { header: 'Devices sold', get: r => num(r.device_lines), align: 'right' },
    { header: 'Cheap share', get: r => share(r.cheap_share, 0), align: 'right' },
  ]), [])

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
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📱 Product Mix &amp; Ports</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          What each store and rep actually sold — <b>which phone, at what price to the customer</b> — set
          against <b>accessory $ per box</b> and <b>how many of their boxes ported in</b>. A rep is
          flagged only when <b>both</b> are below the median, because a phone handed over at $0 closes the
          sale with nothing asked of the customer. The relationship is <b>measured every run</b>, not
          assumed.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Month{' '}
          <input type="month" style={sel} value={period.length === 7 ? period : thisMonth()} onChange={e => setPeriod(e.target.value)} />
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }} title="The customer-price cut-offs that define the bands — the upper bound of each band, comma separated. Blank uses the house cuts, which were measured from this estate's own device prices.">
          Price cut-offs{' '}
          <input style={{ ...sel, width: 140 }} placeholder={(data?.price_cuts || []).join(', ') || 'default'}
                 value={bands} onChange={e => setBands(e.target.value)} />
        </label>
        {marketOpts.length > 0 && <MultiSelect allLabel="All markets" width={150} value={selMarkets} options={marketOpts} onChange={setSelMarkets} />}
        <label style={{ fontSize: 12, color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: 5 }}>
          <input type="checkbox" checked={onlyFlagged} onChange={e => setOnlyFlagged(e.target.checked)} />
          Only reps low on both
        </label>
        {(selMarkets.length > 0 || bands.trim() !== '' || onlyFlagged) &&
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setSelMarkets([]); setBands(''); setOnlyFlagged(false) }}>Clear</button>}
      </div>

      {data?.error &&
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: '#fee2e2', color: '#991b1b', fontSize: 13 }}>
          <b>❌ Product Mix could not be built.</b> {data.error}
        </div>}

      {/* WHAT THIS TABLE CANNOT ANSWER, above the numbers. Every one of these reads as a plausible
          performance figure, and a manager must not coach a rep on a classification or a floor. */}
      {!loading && caveats.length > 0 &&
        <div style={{ marginBottom: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {caveats.map((c, i) => {
            const s = CAVEAT_STYLE[c.severity] || CAVEAT_STYLE.partial
            return (
              <div key={i} className="card" style={{ padding: '10px 14px', background: s.bg, color: s.fg, fontSize: 13 }}>
                <b>{s.icon} {c.column}</b> — {c.message}
              </div>
            )
          })}
        </div>}

      {!loading && !data?.error && reps.length > 0 &&
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
          <Tile label="Reps low on both" value={String(flagged.length)}
                sub={`of ${basis.ranked_reps || 0} ranked · ${basis.listed_reps || 0} listed`}
                warn={flagged.length > 0} />
          <Tile label="Accessory $ per box (median)" value={num(basis.accessory_per_box_median, 2)}
                sub="the bar a rep is measured against" />
          <Tile label="Port share (median)" value={share(basis.port_share_median, 1)}
                sub={basis.port_basis ? 'withheld — see below' : 'of their own boxes'}
                warn={!!basis.port_basis} />
          <Tile label="Cheap-device share (median)" value={share(basis.cheap_share_median, 0)}
                sub={`${bandDefs.filter(b => b.cheap).map(b => b.label.toLowerCase()).join(' + ')} bands`} />
        </div>}

      {/* THE OWNER'S CLAIM, MEASURED. It is a hypothesis, so the report states the coefficient rather
          than coaching from an assumption — and says plainly that a correlation is not a cause. */}
      {!loading && cors.length > 0 &&
        <div className="card" style={{ padding: '14px 16px', marginBottom: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>🔗 Does the cheap phone cost us the accessory sale?</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14 }}>
            {cors.map((c, i) => (
              <div key={i} style={{ fontSize: 13, minWidth: 250 }}>
                <span style={{ color: 'var(--text2)' }}>{c.label}</span>{' '}
                <b style={{ color: c.r === null ? 'var(--text3)' : c.r < 0 ? '#991b1b' : '#166534' }}>
                  {c.r === null ? 'not enough reps to say' : `r = ${c.r.toFixed(2)}`}
                </b>
                {c.r !== null && <span style={{ fontSize: 11, color: 'var(--text3)' }}> (n = {c.n})</span>}
              </div>
            ))}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 9 }}>{data?.correlation_caveat}</div>
        </div>}

      {/* THE ACTION PLAN. The store sentences are the Peer Sales Comparison's own, so this screen, the
          peer screen and the Daily Action Plan name the same stores by the same rule. */}
      {!loading && plan.length > 0 &&
        <div className="card" style={{ padding: '14px 16px', marginBottom: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
            🎯 Action plan — {plan.length} lagging store item{plan.length === 1 ? '' : 's'}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {plan.map((p, i) => (
              <div key={i} style={{ fontSize: 13 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  <span style={{ fontWeight: 700, minWidth: 92 }}>{p.store}</span>
                  <span style={{
                    fontSize: 11, padding: '1px 7px', borderRadius: 999, alignSelf: 'center',
                    background: p.severity === 'critical' ? '#fee2e2' : '#fef3c7',
                    color: p.severity === 'critical' ? '#991b1b' : '#92400e',
                  }}>{p.severity}</span>
                  <span style={{ color: 'var(--text2)' }}>{p.detail}</span>
                </div>
                {(p.reps || []).length > 0 &&
                  <ul style={{ margin: '5px 0 0 100px', padding: 0, listStyle: 'none', color: 'var(--text2)' }}>
                    {p.reps.map((r: any, j: number) => (
                      <li key={j} style={{ fontSize: 12, marginTop: 3 }}>• {r.prompt}</li>
                    ))}
                  </ul>}
              </div>
            ))}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 9 }}>{data?.action_plan_note}</div>
        </div>}

      {/* A plan that did not run is not the same as no store being behind. */}
      {!loading && data?.action_plan_error &&
        <div className="card" style={{ padding: '10px 14px', marginBottom: 14, background: '#fef3c7', color: '#92400e', fontSize: 13 }}>
          <b>⚠︎ The traffic-band comparison did not run</b>, so there is no store action plan below. That
          is not a finding of nobody lagging. ({data.action_plan_error})
        </div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data?.error && reps.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          {data?.note || 'No sales in this window.'}
        </div>
      ) : !data?.error ? (
        <>
          <ReportShell
            title={`Product Mix & Ports — by rep · ${data?.period || period}`}
            subtitle={`${shown.length} rep rows${onlyFlagged ? ' (low on both)' : ''} · ${basis.comparison || ''}`}
            filename={`product-mix-reps-${data?.period || period}`}
            columns={repCols}
            rows={shown}
            totals
            stickyHeader
            defaultGroupBy="Store"
            collapsibleGroups
            groupPersistKey="product-mix:groupBy"
          />

          <div style={{ marginTop: 22 }}>
            <ReportShell
              title={`Product Mix & Ports — by store · ${data?.period || period}`}
              subtitle={`${stores.length} stores · the carrier's own port % beside ours`}
              filename={`product-mix-stores-${data?.period || period}`}
              columns={storeCols}
              rows={stores}
              totals
              stickyHeader
            />
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>
              ℹ︎ {data?.carrier_port_meaning} It is reported <b>beside</b> ours, never merged into it:
              the two count different months and different denominators, and comparing them is the point.
              A blank means the carrier did not report that store, never 0%.
            </div>
          </div>

          {models.length > 0 &&
            <div className="card" style={{ padding: '14px 16px', marginTop: 22 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
                📦 What the estate is actually selling
              </div>
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
                    <th style={{ padding: '4px 6px' }}>Model</th>
                    <th style={{ padding: '4px 6px', textAlign: 'right' }}>Sold</th>
                    <th style={{ padding: '4px 6px', textAlign: 'right' }}>Stores</th>
                    <th style={{ padding: '4px 6px', textAlign: 'right' }}>Avg price to customer</th>
                    <th style={{ padding: '4px 6px', textAlign: 'right' }}>Given free</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((m, i) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '4px 6px' }}>{m.model}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right' }}>{m.lines}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right' }}>{m.stores}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right' }}>{m.avg_price === null ? '—' : fmt(m.avg_price)}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right' }}>{m.free_lines} ({share(m.free_share, 0)})</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data?.models_meta?.note &&
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>ℹ︎ {data.models_meta.note}</div>}
            </div>}

          <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 14 }}>
            ℹ︎ {data?.mix_not_a_partition}
          </div>
          {bandDefs.length > 0 &&
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>
              ℹ︎ Price bands in force: {bandDefs.map(b => `${b.label} (${b.range})`).join(' · ')}.
            </div>}
        </>
      ) : null}
    </div>
  )
}
