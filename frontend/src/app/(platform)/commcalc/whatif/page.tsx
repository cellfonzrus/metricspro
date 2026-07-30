'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { isSuperAdmin } from '@/lib/rbac'
import { TrendChart } from '@/components/TrendChart'

const card = { padding: 18, borderRadius: 12 } as const
const num = (v: any) => { const n = parseFloat(String(v).replace(/[^0-9.\-]/g, '')); return isFinite(n) ? n : 0 }

type Carrier = { id: string; name: string; code?: string; is_default?: boolean }

function TabBar({ tab, setTab }: { tab: string; setTab: (t: string) => void }) {
  const tabs = [['mix', '🎯 Employee Payout'], ['byod', '📶 BYOD → Residuals'], ['corr', '🔗 Accessories ↔ BYOD ↔ Revenue'], ['carrier', '💵 Company Payout / Carrier Income']]
  return (
    <div style={{ display: 'flex', gap: 6, marginBottom: 20, flexWrap: 'wrap' }}>
      {tabs.map(([k, label]) => (
        <button key={k} onClick={() => setTab(k)}
          style={{ padding: '8px 14px', borderRadius: 9, fontSize: 13, fontWeight: 600, cursor: 'pointer',
                   border: '1px solid var(--border)', background: tab === k ? 'var(--accent)' : 'var(--surface)',
                   color: tab === k ? '#fff' : 'var(--text2)' }}>{label}</button>
      ))}
    </div>
  )
}

// ───────────────────────── Carrier picker (RULE THREE — pick, don't type) ─────────────────────────
function CarrierPicker({ carriers, carrierId, setCarrierId, mode }:
  { carriers: Carrier[]; carrierId: string; setCarrierId: (v: string) => void; mode: string }) {
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
      <label style={{ fontSize: 13, color: 'var(--text2)', fontWeight: 600 }}>Carrier:</label>
      <select value={carrierId} onChange={e => setCarrierId(e.target.value)}
        style={{ padding: '7px 12px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 14, fontWeight: 600, background: 'var(--surface)', minWidth: 200 }}>
        {carriers.length === 0 && <option value="">(no carriers configured)</option>}
        {carriers.map(c => <option key={c.id} value={c.id}>{c.name}{c.is_default ? ' — default' : ''}</option>)}
      </select>
      <span style={{ fontSize: 12, padding: '3px 9px', borderRadius: 20, fontWeight: 600,
        background: mode === 'boost' ? '#eef2ff' : '#ecfdf5', color: mode === 'boost' ? '#4338ca' : '#047857' }}>
        {mode === 'boost' ? 'Boost engine (rates)' : 'Commission-Plan engine'}
      </span>
    </div>
  )
}

// ───────────────────────── Tab 1: Employee-payout template (carrier-agnostic) ─────────────────────────
function ActivationMix({ carrierId }: { carrierId: string }) {
  const [period, setPeriod] = useState('')
  const [data, setData] = useState<any>(null)
  const [qty, setQty] = useState<Record<string, number>>({})
  const [rate, setRate] = useState<Record<string, number>>({})
  const [tier, setTier] = useState(1)

  useEffect(() => { setData(null); load1(period) }, [carrierId])
  useEffect(() => { if (period) load1(period) }, [period])
  function load1(p: string) {
    api(`/api/v1/commcalc/whatif/activation-baseline?org_id=${ORG_ID}&carrier_id=${carrierId}&period=${encodeURIComponent(p || 'auto')}`).then(load).catch(console.error)
  }

  function load(d: any) {
    if (!period && d.periods?.length) { setPeriod(d.periods[0]); return }
    setData(d)
    const comps = d.template?.components || []
    const q: Record<string, number> = {}, r: Record<string, number> = {}
    comps.forEach((c: any) => { q[c.key] = num(c.qty); r[c.key] = num(c.rate) })
    setQty(q); setRate(r)
    setTier(num(d.template?.tier?.baseline) || 1)
  }

  if (!data) return <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div>

  const tpl = data.template || {}
  const comps: any[] = tpl.components || []
  // Empty state — mirror the R1 refusal (never silently $0)
  if (tpl.empty) return (
    <div>
      <PeriodBar period={period} setPeriod={setPeriod} periods={data.periods} />
      <div className="card" style={{ ...card, borderLeft: '3px solid #f59e0b', background: '#fffbeb' }}>
        <div style={{ fontWeight: 700, color: '#92400e', marginBottom: 6 }}>No pay source configured for {data.carrier?.name || 'this carrier'}</div>
        <p style={{ color: '#92400e', fontSize: 13, margin: '0 0 12px' }}>{tpl.reason}</p>
        <a href={tpl.configure_url || '/commcalc/commission-plans'}
          style={{ display: 'inline-block', padding: '8px 14px', borderRadius: 8, background: 'var(--accent)', color: '#fff', fontSize: 13, fontWeight: 600, textDecoration: 'none' }}>
          Configure commission plans →</a>
      </div>
    </div>
  )

  const rowComm = (k: string) => num(qty[k]) * num(rate[k])
  const subtotal = comps.reduce((s, c) => s + rowComm(c.key), 0)
  const payout = subtotal * num(tier)
  const baseSub = data?.actuals?.subtotal || 0
  const basePay = data?.actuals?.total_payout || 0
  const inp = { width: 92, padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', textAlign: 'right' as const }
  const tierOpts: any[] = tpl.tier?.options || [{ label: '×1.00', value: 1 }]

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <PeriodBar period={period} setPeriod={setPeriod} periods={data.periods} />
        <div style={{ flex: 1 }} />
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Tier:</label>
        {tierOpts.map((o: any) => (
          <button key={String(o.label)} onClick={() => setTier(num(o.value))}
            style={{ padding: '5px 10px', borderRadius: 7, fontSize: 12, cursor: 'pointer', border: '1px solid var(--border)',
                     background: num(tier) === num(o.value) ? 'var(--accent)' : 'var(--surface)', color: num(tier) === num(o.value) ? '#fff' : 'var(--text2)' }}>{o.label}</button>
        ))}
      </div>

      <div className="card" style={{ padding: 0, marginBottom: 18 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Pay component', 'Quantity', 'Rate', 'Projected $', 'Current $'].map(h =>
              <th key={h} style={{ textAlign: h === 'Pay component' ? 'left' : 'right', padding: '9px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {comps.map((c, i) => (
              <tr key={c.key} style={{ borderTop: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
                <td style={{ padding: '8px 14px', fontSize: 13, fontWeight: 500 }}>{c.label}
                  <span style={{ color: 'var(--text3)', fontWeight: 400, fontSize: 11 }}> · {c.unit}{c.plan_name ? ` · ${c.plan_name}` : ''}</span></td>
                <td style={{ padding: '8px 14px', textAlign: 'right' }}>
                  <input style={inp} value={qty[c.key] ?? 0} onChange={e => setQty({ ...qty, [c.key]: num(e.target.value) })} />
                </td>
                <td style={{ padding: '8px 14px', textAlign: 'right' }}>
                  <span style={{ color: 'var(--text3)', fontSize: 12 }}>{c.kind === 'pct' ? '' : '$'}</span>
                  <input style={{ ...inp, width: 74 }} value={rate[c.key] ?? 0} onChange={e => setRate({ ...rate, [c.key]: num(e.target.value) })} />
                  <span style={{ color: 'var(--text3)', fontSize: 12 }}>{c.kind === 'pct' ? ' ×' : ''}</span>
                </td>
                <td style={{ padding: '8px 14px', textAlign: 'right', fontSize: 13, fontWeight: 700 }}>{fmt(rowComm(c.key))}</td>
                <td style={{ padding: '8px 14px', textAlign: 'right', fontSize: 12, color: 'var(--text3)' }}>{fmt(num(c.current_comm))}</td>
              </tr>
            ))}
            {comps.length === 0 && <tr><td colSpan={5} style={{ padding: 20, color: 'var(--text3)', fontSize: 13 }}>No pay components for this carrier/period.</td></tr>}
          </tbody>
        </table>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14 }}>
        <Stat label="Projected subtotal (pre-tier)" value={fmt(subtotal)} sub={`vs current ${fmt(baseSub)}`} />
        <Stat label={`Projected payout (× ${(+num(tier)).toFixed(2)} tier)`} value={fmt(payout)} color="var(--accent)" sub={`vs current ${fmt(basePay)}`} />
        <Stat label="Delta vs current" value={(payout - basePay >= 0 ? '+' : '') + fmt(payout - basePay)} color={payout - basePay >= 0 ? '#059669' : '#dc2626'} sub={`${data.actuals?.reps ?? 0} reps in baseline`} />
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 14 }}>
        {tpl.source_kind === 'boost_rates'
          ? <>Rates pre-filled from your <b>payout_config</b> for {period}; quantities from actual <b>rep_commissions</b>. Payout = Σ(qty × rate) × tier — the exact engine formula. Accessory / Setup rates are a fraction of sales $ (0.10 = 10%).</>
          : <>Components auto-populated from <b>{data.carrier?.name}</b>'s configured Commission Plans / rules / tiers + payout schedules; baseline quantities from the read-only plan preview for {period}. Payout = Σ(qty × rate) × tier.</>}
      </p>
    </div>
  )
}

function PeriodBar({ period, setPeriod, periods }: { period: string; setPeriod: (v: string) => void; periods: string[] }) {
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
      <label style={{ fontSize: 13, color: 'var(--text2)' }}>Baseline period:</label>
      <select value={period} onChange={e => setPeriod(e.target.value)}
        style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
        {(periods || []).map((p: string) => <option key={p} value={p}>{p}</option>)}
      </select>
    </div>
  )
}

// ───────────────────────── Tab 2: BYOD → residuals (carrier-agnostic) ─────────────────────────
function ByodResidual({ carrierId }: { carrierId: string }) {
  const [months, setMonths] = useState(6)
  const [data, setData] = useState<any>(null)
  const [err, setErr] = useState<string>('')
  const [byod, setByod] = useState(0)
  const [perSub, setPerSub] = useState(0)
  const [active, setActive] = useState(12)

  useEffect(() => {
    setData(null); setErr('')
    api(`/api/v1/commcalc/whatif/byod-residual?org_id=${ORG_ID}&carrier_id=${carrierId}&months=${months}`).then((d: any) => {
      setData(d); setPerSub(num(d.byod_specific?.avg_residual_per_byod_sub) || num(d.avg_residual_per_sub)); setByod(num(d.latest?.byod_acts) || 0)
    }).catch((e) => setErr(String(e?.message || e) || 'restricted'))
  }, [months, carrierId])

  if (err) return <RestrictedNote what="BYOD residual" />
  if (!data) return <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div>
  if (data.note && !(data.series || []).length) return <div className="card" style={{ ...card }}><p style={{ color: 'var(--text2)' }}>{data.note}</p></div>
  const projected = num(byod) * num(perSub) * num(active)
  const perExtra = num(perSub) * num(active)
  const srcLabel = data.residual_source === 'ma_daily_tx' ? 'MA Daily Tx (residual orders) + MA Commission' : 'MI + ATU'
  return (
    <div>
      {data.residual_field_warning && <NoteBanner tone="warn" text={`⚠ ${data.residual_field_warning}`} />}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Residual window:</label>
        <select value={months} onChange={e => setMonths(Number(e.target.value))}
          style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
          {Array.from({ length: 12 }, (_, i) => i + 1).map(m => <option key={m} value={m}>{m} month{m > 1 ? 's' : ''}</option>)}
        </select>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{(data.months || []).length} month(s) with data · source: {srcLabel}</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14, marginBottom: 18 }}>
        <Stat label={`Total residual (${srcLabel})`} value={fmt(data.total_residual)} />
        <Stat label="Avg residual / subscriber / mo" value={fmt(data.avg_residual_per_sub)} color="#059669" />
        <Stat label="Total paid subscribers" value={(data.total_subs || 0).toLocaleString()} />
      </div>

      {data.byod_specific && (
        <div className="card" style={{ padding: 16, marginBottom: 18, borderLeft: '3px solid var(--accent)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 10 }}>
            📶 BYOD‑specific residual ({data.byod_specific.period})
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14 }}>
            <Stat label="Residual / BYOD sub" value={fmt(data.byod_specific.avg_residual_per_byod_sub)} color="var(--accent)" sub={`vs ${fmt(data.byod_specific.avg_residual_per_other_sub)} for non‑BYOD`} />
            <Stat label="BYOD subs earning residual" value={(data.byod_specific.byod_subs_with_residual || 0).toLocaleString()} sub={`${Math.round((data.byod_specific.match_rate || 0) * 100)}% matched`} />
            <Stat label="BYOD residual" value={fmt(data.byod_specific.byod_residual_month)} color="#059669" />
          </div>
          {data.byod_specific.note && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>{data.byod_specific.note}</div>}
        </div>
      )}

      {data.retail_cost?.available && (
        <div className="card" style={{ padding: 14, marginBottom: 18, fontSize: 13, color: 'var(--text2)' }}>
          🧾 Retail cost of activated products (raw_ma_pr_activation): <b>{fmt(data.retail_cost.total_retail_cost)}</b> across {data.retail_cost.lines} line(s).
        </div>
      )}

      <div className="card" style={{ padding: 16, marginBottom: 18 }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Residual &amp; BYOD trend</div>
        <TrendChart height={300} leftMoney rightMoney={false}
          data={(data.series || []).map((s: any) => ({ name: s.period, residual: s.residual, byod_acts: s.byod_acts, per_sub: s.per_sub }))}
          series={[
            { key: 'residual', name: 'Residual', type: 'bar', axis: 'left', money: true, color: '#2e75b6' },
            { key: 'byod_acts', name: 'BYOD activations', type: 'line', axis: 'right', money: false, color: '#f59e0b' },
          ]} />
      </div>

      <div className="card" style={{ padding: 18, marginBottom: 18 }}>
        <div style={{ fontWeight: 700, marginBottom: 12 }}>What‑if: BYOD → residual contribution</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14, marginBottom: 14 }}>
          <Field label="BYOD activations (per month)" value={byod} onChange={setByod} />
          <Field label="Residual / BYOD sub / mo ($)" value={perSub} onChange={setPerSub} />
          <Field label="Months a sub stays active" value={active} onChange={setActive} />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: 14 }}>
          <Stat label="Residual these BYOD subs generate" value={fmt(projected)} color="var(--accent)" sub={`${byod} subs × ${fmt(perSub)}/mo × ${active} mo`} />
          <Stat label="Each +1 BYOD/mo adds" value={fmt(perExtra)} color="#059669" sub={`over ${active} months of residual`} />
        </div>
      </div>

      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 13 }}>Residual vs BYOD by month</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>{['Period', 'Residual', 'Subs', 'Residual/sub', 'BYOD acts'].map(h =>
            <th key={h} style={{ textAlign: h === 'Period' ? 'left' : 'right', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}</tr></thead>
          <tbody>{(data.series || []).map((s: any, i: number) => (
            <tr key={s.period} style={{ borderTop: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
              <td style={{ padding: '8px 14px', fontSize: 13 }}>{s.period}</td>
              <td style={{ padding: '8px 14px', fontSize: 13, textAlign: 'right', fontWeight: 600 }}>{fmt(s.residual)}</td>
              <td style={{ padding: '8px 14px', fontSize: 13, textAlign: 'right' }}>{(s.subs || 0).toLocaleString()}</td>
              <td style={{ padding: '8px 14px', fontSize: 13, textAlign: 'right' }}>{fmt(s.per_sub)}</td>
              <td style={{ padding: '8px 14px', fontSize: 13, textAlign: 'right', color: 'var(--accent)' }}>{(s.byod_acts || 0).toLocaleString()}</td>
            </tr>))}</tbody>
        </table>
      </div>
    </div>
  )
}

// ───────────────────────── Tab 3: Accessory ↔ BYOD ↔ revenue ─────────────────────────
function corrLabel(r: number | null) {
  if (r == null) return { txt: 'n/a (need ≥3 points)', color: 'var(--text3)' }
  const a = Math.abs(r), strength = a >= 0.7 ? 'strong' : a >= 0.4 ? 'moderate' : a >= 0.2 ? 'weak' : 'negligible'
  return { txt: `${strength} ${r > 0 ? 'positive' : 'negative'} · r = ${r}`, color: a >= 0.4 ? (r > 0 ? '#059669' : '#dc2626') : 'var(--text2)' }
}

function Scatter({ points, xKey, yKey, xLabel, yLabel }: { points: any[]; xKey: string; yKey: string; xLabel: string; yLabel: string }) {
  const W = 560, H = 320, PAD = 52
  const xs = points.map(p => p[xKey]), ys = points.map(p => p[yKey])
  if (!points.length) return <div style={{ padding: 30, color: 'var(--text3)' }}>No data points.</div>
  const xmin = Math.min(...xs, 0), xmax = Math.max(...xs, 1), ymin = Math.min(...ys, 0), ymax = Math.max(...ys, 1)
  const sx = (v: number) => PAD + (v - xmin) / (xmax - xmin || 1) * (W - PAD - 14)
  const sy = (v: number) => H - PAD - (v - ymin) / (ymax - ymin || 1) * (H - PAD - 14)
  const n = points.length, mx = xs.reduce((a, b) => a + b, 0) / n, my = ys.reduce((a, b) => a + b, 0) / n
  const sxx = xs.reduce((a, b) => a + (b - mx) ** 2, 0), sxy = points.reduce((a, p) => a + (p[xKey] - mx) * (p[yKey] - my), 0)
  const slope = sxx ? sxy / sxx : 0, intercept = my - slope * mx
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxWidth: W, height: 'auto' }}>
      <line x1={PAD} y1={H - PAD} x2={W - 8} y2={H - PAD} stroke="var(--border)" />
      <line x1={PAD} y1={12} x2={PAD} y2={H - PAD} stroke="var(--border)" />
      <text x={(W + PAD) / 2} y={H - 14} textAnchor="middle" fontSize="11" fill="var(--text3)">{xLabel}</text>
      <text x={16} y={(H - PAD) / 2} textAnchor="middle" fontSize="11" fill="var(--text3)" transform={`rotate(-90 16 ${(H - PAD) / 2})`}>{yLabel}</text>
      {sxx > 0 && <line x1={sx(xmin)} y1={sy(intercept + slope * xmin)} x2={sx(xmax)} y2={sy(intercept + slope * xmax)} stroke="var(--accent)" strokeWidth="2" strokeDasharray="5 4" opacity="0.8" />}
      {points.map((p, i) => <circle key={i} cx={sx(p[xKey])} cy={sy(p[yKey])} r="4" fill="#6366f1" opacity="0.6"><title>{p.store} · {p.period}: {xLabel} {p[xKey]}, {yLabel} {fmt(p[yKey])}</title></circle>)}
    </svg>
  )
}

function AccessoryByod() {
  const [months, setMonths] = useState(4)
  const [data, setData] = useState<any>(null)
  useEffect(() => { setData(null); api(`/api/v1/commcalc/whatif/accessory-byod?org_id=${ORG_ID}&months=${months}`).then(setData).catch(console.error) }, [months])
  if (!data) return <div style={{ padding: 40, color: 'var(--text3)' }}>Crunching store‑month sales… (may take a few seconds)</div>
  const c = data.correlation
  return (
    <div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16 }}>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Months:</label>
        {[3, 4, 6].map(m => <button key={m} onClick={() => setMonths(m)}
          style={{ padding: '5px 12px', borderRadius: 7, fontSize: 12, cursor: 'pointer', border: '1px solid var(--border)', background: months === m ? 'var(--accent)' : 'var(--surface)', color: months === m ? '#fff' : 'var(--text2)' }}>{m}</button>)}
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{data.n} store‑month points · {data.periods?.join(', ')}</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14, marginBottom: 18 }}>
        {[['Accessory ↔ BYOD', c.byod_vs_accessory], ['BYOD ↔ Total revenue', c.byod_vs_revenue], ['Accessory ↔ Total revenue', c.accessory_vs_revenue]].map(([lbl, r]) => {
          const cl = corrLabel(r as number | null)
          return <div key={lbl as string} className="card" style={{ ...card }}>
            <div style={{ fontSize: 12, color: 'var(--text2)', fontWeight: 600, marginBottom: 6 }}>{lbl}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: cl.color }}>{cl.txt}</div>
          </div>
        })}
      </div>
      <div className="card" style={{ padding: 18, marginBottom: 18 }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Accessory revenue vs BYOD activations <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>(each dot = one store, one month)</span></div>
        <Scatter points={data.points} xKey="byod" yKey="accessory_rev" xLabel="BYOD activations" yLabel="Accessory revenue ($)" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14 }}>
        <Stat label="Total BYOD (all store‑months)" value={(data.totals.byod || 0).toLocaleString()} />
        <Stat label="Total accessory revenue" value={fmt(data.totals.accessory_rev)} color="#059669" />
        <Stat label="Total revenue" value={fmt(data.totals.revenue)} color="var(--accent)" />
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 14 }}>
        r ranges −1…+1. A positive Accessory↔BYOD r means stores that do more BYOD also sell more accessories. Correlation ≠ causation.
      </p>
    </div>
  )
}

// ───────────────────────── Tab 4: Company payout / carrier income (carrier-agnostic) ─────────────────────────
function CarrierIncome({ carrierId }: { carrierId: string }) {
  const [trend, setTrend] = useState<any>(null)
  const [err, setErr] = useState('')
  const [period, setPeriod] = useState('')
  const [base, setBase] = useState<any>(null)

  useEffect(() => {
    setTrend(null); setErr(''); setPeriod('')
    api(`/api/v1/commcalc/whatif/carrier-income?org_id=${ORG_ID}&carrier_id=${carrierId}`).then((d: any) => {
      setTrend(d)
      const months: any[] = d.totals_by_month || []
      const withComp = months.filter(m => num(m.total_comp) > 0)
      const def = (withComp.length ? withComp[withComp.length - 1] : months[months.length - 1])?.period
      if (def) setPeriod(def)
    }).catch((e) => setErr(String(e?.message || e) || 'restricted'))
  }, [carrierId])
  useEffect(() => {
    if (!period) return
    api(`/api/v1/commcalc/whatif/activation-baseline?org_id=${ORG_ID}&carrier_id=${carrierId}&period=${encodeURIComponent(period)}`).then(setBase).catch(console.error)
  }, [period])

  if (err) return <RestrictedNote what="company payout / carrier income" />
  if (!trend) return <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div>
  const months: any[] = trend.totals_by_month || []
  if (!months.length) return <div className="card" style={{ ...card }}><p style={{ color: 'var(--text2)' }}>{trend.note || 'No carrier income data yet.'}</p></div>
  const cur = months.find(m => m.period === period) || months[months.length - 1] || {}
  const comp = cur.components || {}
  const isMA = trend.income_source === 'ma'
  const residual = isMA ? num(cur.residual_mi_atu) : num(cur.residual)
  const totalIncome = num(cur.total_comp) + residual
  const repPay = num(base?.actuals?.total_payout)
  const net = totalIncome - repPay
  const noComp = num(cur.total_comp) === 0

  const HEADINGS: [string, number, string][] = [
    ['Commission' + (isMA ? ' (M1–M6)' : ' (promo)'), num(comp.COMMISSION), '#2e75b6'],
    [isMA ? 'Rebate / bounty' : 'SPIFF / bounty', num(comp.SPIFF), '#7c3aed'],
    ['Reimbursement', num(comp.REIMBURSEMENT), '#0891b2'],
    [isMA ? 'Residual (Postpaid Residual Orders)' : 'Residual (MI + ATU)', residual, '#16a34a'],
    [isMA ? 'Airtime margin' : 'Unmapped', num(comp.UNMAPPED), '#f59e0b'],
  ]
  const chartData = months.map(m => ({ name: m.period, commission: num(m.components?.COMMISSION), spiff: num(m.components?.SPIFF), reimbursement: num(m.components?.REIMBURSEMENT), residual: isMA ? num(m.residual_mi_atu) : num(m.residual) }))

  return (
    <div>
      {trend.residual_field_warning && <NoteBanner tone="warn" text={`⚠ ${trend.residual_field_warning}`} />}
      {trend.data_note && <NoteBanner tone="info" text={trend.data_note} />}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Period:</label>
        <select value={period} onChange={e => setPeriod(e.target.value)}
          style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
          {months.map(m => <option key={m.period} value={m.period}>{m.period}{num(m.total_comp) > 0 ? '' : (m.comp_source_missing ? ' (no MA Commission rows)' : ' (comp not posted)')}</option>)}
        </select>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>What {trend.carrier?.name || 'the carrier'} pays the company — source: {isMA ? 'MA Commission + MA Daily Tx' : 'Comprehensive Comp + MI+ATU'}</span>
      </div>

      {noComp && <div className="card" style={{ padding: 12, marginBottom: 14, fontSize: 13, color: '#92400e', background: '#fffbeb', borderLeft: '3px solid #f59e0b' }}>
        {cur.comp_source_missing
          ? <>Carrier compensation for {period} reads $0 because <b>MA Commission Details has no rows for that month</b> ({cur.daily_tx_rows} MA Daily Tx row(s), 0 commission row(s)) — a data gap, not a calculation error. Showing residual only; pull that report for the month on Data&nbsp;Imports.</>
          : <>Carrier compensation for {period} isn’t posted yet — showing residual only. Pick a month with data for the full split.</>}
      </div>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14, marginBottom: 8 }}>
        {HEADINGS.map(([lbl, val, color]) => (
          <Stat key={lbl} label={lbl} value={fmt(val)} color={color as string}
            sub={totalIncome ? `${Math.round((num(val) / totalIncome) * 100)}% of income` : undefined} />
        ))}
        <Stat label="TOTAL carrier income" value={fmt(totalIncome)} color="var(--accent)" sub="comp + residual" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14, margin: '14px 0 18px' }}>
        <Stat label="We collect (carrier)" value={fmt(totalIncome)} color="#16a34a" />
        <Stat label="We pay reps" value={fmt(repPay)} color="#dc2626" sub={`${period} employee payout`} />
        <Stat label="Net to company" value={fmt(net)} color={net >= 0 ? '#059669' : '#dc2626'}
          sub={totalIncome ? `${Math.round((net / totalIncome) * 100)}% margin` : undefined} />
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 18 }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Carrier income by heading, month over month</div>
        <TrendChart height={320} leftMoney
          data={chartData}
          series={[
            { key: 'commission', name: 'Commission', type: 'bar', axis: 'left', money: true, color: '#2e75b6' },
            { key: 'spiff', name: isMA ? 'Rebate' : 'SPIFF', type: 'bar', axis: 'left', money: true, color: '#7c3aed' },
            { key: 'reimbursement', name: 'Reimbursement', type: 'bar', axis: 'left', money: true, color: '#0891b2' },
            { key: 'residual', name: 'Residual', type: 'line', axis: 'left', money: true, color: '#16a34a' },
          ]} />
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)' }}>
        Company payout / carrier income = what {trend.carrier?.name || 'the carrier / master-agent'} pays the company. Net = carrier income − employee payout ({period}). Source is resolved per carrier (⚙️ Sources).
      </p>
    </div>
  )
}

// Page-level honesty banner. tone 'warn' (red) = the figures below are NOT trustworthy as configured
// (e.g. the residual $ column points at an invoice NUMBER); tone 'info' (amber) = the figures are right
// but a source month is missing, so a $0 is a data gap and not a calculation error.
function NoteBanner({ text, tone }: { text: string; tone: 'warn' | 'info' }) {
  const c = tone === 'warn' ? { bg: '#fef2f2', bd: '#dc2626', fg: '#991b1b' } : { bg: '#fffbeb', bd: '#f59e0b', fg: '#92400e' }
  return <div className="card" style={{ padding: 12, marginBottom: 14, fontSize: 13, lineHeight: 1.5, color: c.fg, background: c.bg, borderLeft: `3px solid ${c.bd}` }}>{text}</div>
}

function RestrictedNote({ what }: { what: string }) {
  return <div className="card" style={{ ...card, borderLeft: '3px solid #dc2626' }}>
    <div style={{ fontWeight: 700, marginBottom: 4 }}>🔒 Restricted</div>
    <p style={{ color: 'var(--text2)', fontSize: 13, margin: 0 }}>The {what} view is restricted for this tenant — you need the <b>carrier_residual</b> permission to view it.</p>
  </div>
}

// ───────────────────────── ⚙️ Sources (admin config, RULE TWO) ─────────────────────────
function SourcesPanel({ carrierId, onSaved }: { carrierId: string; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [cfg, setCfg] = useState<any>(null)
  const [form, setForm] = useState<any>({})
  const [msg, setMsg] = useState('')

  useEffect(() => {
    if (!open) return
    api(`/api/v1/commcalc/whatif/source-config?org_id=${ORG_ID}&carrier_id=${carrierId}`).then((d: any) => {
      setCfg(d); setForm({ ...d.resolved }); setMsg('')
    }).catch(e => setMsg(String(e?.message || e)))
  }, [open, carrierId])

  function save() {
    setMsg('Saving…')
    api(`/api/v1/commcalc/whatif/source-config?org_id=${ORG_ID}`, {
      method: 'PUT',
      body: JSON.stringify({ carrier_id: carrierId, carrier_mode: cfg?.carrier_mode, ...form }),
    }).then((r: any) => { setMsg(r?.ok ? 'Saved.' : (r?.hint || 'Save failed')); onSaved() }).catch(e => setMsg(String(e?.message || e)))
  }

  const opts = cfg?.options || {}
  // Human labels for values whose bare column name is misleading (e.g. merchant_invoice is an invoice
  // NUMBER, not money). Falls back to the raw value when the backend sends no label.
  const optLabels = cfg?.option_labels || {}
  const sel = (key: string, choices: string[]) => (
    <select value={form[key] ?? ''} onChange={e => setForm({ ...form, [key]: e.target.value })}
      style={{ padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)', maxWidth: '100%' }}>
      {(choices || []).map((c: string) => <option key={c} value={c}>{optLabels?.[key]?.[c] || c}</option>)}
    </select>
  )

  return (
    <div style={{ marginTop: 10 }}>
      <button onClick={() => setOpen(o => !o)} style={{ padding: '5px 10px', borderRadius: 7, fontSize: 12, cursor: 'pointer', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text2)' }}>
        ⚙️ Sources {open ? '▲' : '▼'}
      </button>
      {open && cfg && (
        <div className="card" style={{ padding: 16, marginTop: 8 }}>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
            Config-driven source selection for <b>{cfg.carrier?.name || 'this carrier'}</b> (mode: {cfg.carrier_mode}). Resolved from: {cfg.resolved?._resolved_from}. Editing saves a per-carrier override.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(220px, 1fr))', gap: 12 }}>
            <Row label="Residual source">{sel('residual_source', opts.residual_source)}</Row>
            <Row label="Income source">{sel('income_source', opts.income_source)}</Row>
            <Row label="Residual order type (MA)">
              <input value={form.residual_order_type ?? ''} placeholder="Postpaid Residual Order"
                onChange={e => setForm({ ...form, residual_order_type: e.target.value })}
                style={{ padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)', width: '100%' }} />
            </Row>
            <Row label="Residual $ column (MA)">{sel('residual_amount_field', opts.residual_amount_field)}</Row>
            <Row label="Residual sign">{sel('residual_sign', opts.residual_sign)}</Row>
            <Row label="Retail-cost source">{sel('retail_cost_source', opts.retail_cost_source)}</Row>
            {opts.ma_commission_sign && <Row label="MA commission sign (M1–M6 / rebate)">{sel('ma_commission_sign', opts.ma_commission_sign)}</Row>}
          </div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 14 }}>
            <button onClick={save} style={{ padding: '7px 14px', borderRadius: 8, background: 'var(--accent)', color: '#fff', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer' }}>Save override</button>
            {msg && <span style={{ fontSize: 12, color: 'var(--text3)' }}>{msg}</span>}
          </div>
        </div>
      )}
    </div>
  )
}

function Row({ label, children }: { label: string; children: any }) {
  return <div><div style={{ fontSize: 11, color: 'var(--text2)', fontWeight: 600, marginBottom: 4 }}>{label}</div>{children}</div>
}

// shared bits
function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return <div className="card" style={{ padding: '16px 18px' }}>
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
    <div style={{ fontSize: 22, fontWeight: 700, marginTop: 5, color: color || 'var(--text1)' }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>{sub}</div>}
  </div>
}
function Field({ label, value, onChange }: { label: string; value: number; onChange: (n: number) => void }) {
  return <div>
    <div style={{ fontSize: 11, color: 'var(--text2)', fontWeight: 600, marginBottom: 4 }}>{label}</div>
    <input value={value} onChange={e => onChange(num(e.target.value))}
      style={{ width: '100%', padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 14, background: 'var(--surface)' }} />
  </div>
}

export default function WhatIfPage() {
  const { permissions } = useAuth()
  const [tab, setTab] = useState('mix')
  const [carriers, setCarriers] = useState<Carrier[]>([])
  const [carrierId, setCarrierId] = useState('')
  const [mode, setMode] = useState('boost')
  const canEditSources = isSuperAdmin(permissions) || permissions?.scope === 'all'

  // load the org's carriers once (pick-don't-type source) + resolve default carrier + mode
  function loadCtx(cid: string) {
    api(`/api/v1/commcalc/whatif/source-config?org_id=${ORG_ID}&carrier_id=${cid}`).then((d: any) => {
      setCarriers(d.carriers || [])
      setMode(d.carrier_mode || 'boost')
      if (!cid && d.carrier?.id) setCarrierId(String(d.carrier.id))
    }).catch(console.error)
  }
  useEffect(() => { loadCtx('') }, [])
  useEffect(() => { if (carrierId) loadCtx(carrierId) }, [carrierId])

  return (
    <div>
      <div style={{ marginBottom: 18 }}>
        <a href="/commcalc" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Commissions</a>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>What‑If / Scenario Analysis</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Carrier-agnostic scenario modeling — employee payout, company payout / carrier income, and BYOD residuals — for any carrier.</p>
      </div>

      <div className="card" style={{ padding: 14, marginBottom: 18 }}>
        <CarrierPicker carriers={carriers} carrierId={carrierId} setCarrierId={setCarrierId} mode={mode} />
        {canEditSources && <SourcesPanel carrierId={carrierId} onSaved={() => loadCtx(carrierId)} />}
      </div>

      <TabBar tab={tab} setTab={setTab} />
      {tab === 'mix' && <ActivationMix carrierId={carrierId} />}
      {tab === 'byod' && <ByodResidual carrierId={carrierId} />}
      {tab === 'corr' && <AccessoryByod />}
      {tab === 'carrier' && <CarrierIncome carrierId={carrierId} />}
    </div>
  )
}
