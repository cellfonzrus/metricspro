'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { TrendChart } from '@/components/TrendChart'

const card = { padding: 18, borderRadius: 12 } as const
const num = (v: any) => { const n = parseFloat(String(v).replace(/[^0-9.\-]/g, '')); return isFinite(n) ? n : 0 }

function TabBar({ tab, setTab }: { tab: string; setTab: (t: string) => void }) {
  const tabs = [['mix', '🎯 Activation Mix → Commission'], ['byod', '📶 BYOD → Residuals'], ['corr', '🔗 Accessories ↔ BYOD ↔ Revenue']]
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

// ───────────────────────── Tab 1: Activation-mix commission projector ─────────────────────────
function ActivationMix() {
  const [period, setPeriod] = useState('')
  const [data, setData] = useState<any>(null)
  const [qty, setQty] = useState<Record<string, number>>({})
  const [rate, setRate] = useState<Record<string, number>>({})
  const [tier, setTier] = useState(1)

  useEffect(() => { api(`/api/v1/commcalc/whatif/activation-baseline?org_id=${ORG_ID}&period=${encodeURIComponent(period || 'auto')}`)
    .then(load).catch(console.error) // first call: period '' -> backend picks nothing; we set from periods below
  }, [])
  useEffect(() => { if (period) api(`/api/v1/commcalc/whatif/activation-baseline?org_id=${ORG_ID}&period=${encodeURIComponent(period)}`).then(load).catch(console.error) }, [period])

  function load(d: any) {
    if (!period && d.periods?.length) { setPeriod(d.periods[0]); return }
    setData(d)
    const a = d.actuals, r = d.rates
    setQty({ premium: a.premium_acts, byod: a.byod_acts, upgrade: a.upgrade_acts, trade: a.trade_ins, acima: a.acima_count, acc: a.acc_sales, setup: a.setup_sales })
    setRate({ premium: r.premium_flat, byod: num(r.byod_flat) + num(r.byod_extra_spiff), upgrade: r.upgrade_flat, trade: r.trade_in_spiff, acima: r.acima_spiff, acc: r.acc_rate, setup: r.setup_fee_rate })
    setTier(num(a.avg_tier) || 1)
  }

  const ROWS = [
    { key: 'premium', label: 'Premium / New Activation', kind: 'flat', unit: 'acts' },
    { key: 'byod', label: 'BYOD Activation', kind: 'flat', unit: 'acts' },
    { key: 'upgrade', label: 'Upgrade', kind: 'flat', unit: 'acts' },
    { key: 'trade', label: 'Trade-In', kind: 'flat', unit: 'units' },
    { key: 'acima', label: 'ACIMA Lease', kind: 'flat', unit: 'units' },
    { key: 'acc', label: 'Accessory Sales', kind: 'pct', unit: '$ sales' },
    { key: 'setup', label: 'Setup Fees', kind: 'pct', unit: '$ sales' },
  ]
  const rowComm = (k: string) => num(qty[k]) * num(rate[k])
  const subtotal = ROWS.reduce((s, r) => s + rowComm(r.key), 0)
  const payout = subtotal * num(tier)
  const baseSub = data?.actuals?.subtotal || 0
  const basePay = data?.actuals?.total_payout || 0
  const inp = { width: 92, padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', textAlign: 'right' as const }

  if (!data) return <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div>
  return (
    <div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Baseline period:</label>
        <select value={period} onChange={e => setPeriod(e.target.value)}
          style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
          {(data.periods || []).map((p: string) => <option key={p} value={p}>{p}</option>)}
        </select>
        <div style={{ flex: 1 }} />
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>KPI tier:</label>
        {[['Actual', num(data.actuals.avg_tier) || 1], ['100%', 1], ['75%', 0.75], ['50%', 0.5]].map(([lbl, v]) => (
          <button key={String(lbl)} onClick={() => setTier(num(v))}
            style={{ padding: '5px 10px', borderRadius: 7, fontSize: 12, cursor: 'pointer', border: '1px solid var(--border)',
                     background: num(tier) === num(v) ? 'var(--accent)' : 'var(--surface)', color: num(tier) === num(v) ? '#fff' : 'var(--text2)' }}>{lbl}</button>
        ))}
      </div>

      <div className="card" style={{ padding: 0, marginBottom: 18 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Activation / item', 'Quantity', 'Commission rate', 'Projected $', 'Current $'].map(h =>
              <th key={h} style={{ textAlign: h === 'Activation / item' ? 'left' : 'right', padding: '9px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {ROWS.map((r, i) => {
              const cur = data.actuals
              const baseQty = { premium: cur.premium_acts, byod: cur.byod_acts, upgrade: cur.upgrade_acts, trade: cur.trade_ins, acima: cur.acima_count, acc: cur.acc_sales, setup: cur.setup_sales }[r.key] || 0
              const curComm = num(baseQty) * num(rate[r.key])
              return (
                <tr key={r.key} style={{ borderTop: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
                  <td style={{ padding: '8px 14px', fontSize: 13, fontWeight: 500 }}>{r.label}<span style={{ color: 'var(--text3)', fontWeight: 400, fontSize: 11 }}> · {r.unit}</span></td>
                  <td style={{ padding: '8px 14px', textAlign: 'right' }}>
                    <input style={inp} value={qty[r.key] ?? 0} onChange={e => setQty({ ...qty, [r.key]: num(e.target.value) })} />
                  </td>
                  <td style={{ padding: '8px 14px', textAlign: 'right' }}>
                    <span style={{ color: 'var(--text3)', fontSize: 12 }}>{r.kind === 'pct' ? '' : '$'}</span>
                    <input style={{ ...inp, width: 74 }} value={rate[r.key] ?? 0} onChange={e => setRate({ ...rate, [r.key]: num(e.target.value) })} />
                    <span style={{ color: 'var(--text3)', fontSize: 12 }}>{r.kind === 'pct' ? ' ×' : ''}</span>
                  </td>
                  <td style={{ padding: '8px 14px', textAlign: 'right', fontSize: 13, fontWeight: 700 }}>{fmt(rowComm(r.key))}</td>
                  <td style={{ padding: '8px 14px', textAlign: 'right', fontSize: 12, color: 'var(--text3)' }}>{fmt(curComm)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14 }}>
        <Stat label="Projected subtotal (pre-tier)" value={fmt(subtotal)} sub={`vs current ${fmt(baseSub)}`} />
        <Stat label={`Projected payout (× ${(+num(tier)).toFixed(2)} tier)`} value={fmt(payout)} color="var(--accent)" sub={`vs current ${fmt(basePay)}`} />
        <Stat label="Delta vs current" value={(payout - basePay >= 0 ? '+' : '') + fmt(payout - basePay)} color={payout - basePay >= 0 ? '#059669' : '#dc2626'} sub={`${data.actuals.reps} reps in baseline`} />
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 14 }}>
        Rates pre-filled from your <b>payout_config</b> for {period}; quantities from actual <b>rep_commissions</b>. Payout = Σ(qty × rate) × tier — the exact engine formula.
        Accessory / Setup rates are a fraction of sales $ (0.10 = 10%).
      </p>
    </div>
  )
}

// ───────────────────────── Tab 2: BYOD → residuals ─────────────────────────
function ByodResidual() {
  const [months, setMonths] = useState(6)
  const [data, setData] = useState<any>(null)
  const [byod, setByod] = useState(0)
  const [perSub, setPerSub] = useState(0)
  const [active, setActive] = useState(12)

  useEffect(() => { api(`/api/v1/commcalc/whatif/byod-residual?org_id=${ORG_ID}&months=${months}`).then((d: any) => {
    setData(d); setPerSub(num(d.byod_specific?.avg_residual_per_byod_sub) || num(d.avg_residual_per_sub)); setByod(num(d.latest?.byod_acts) || 0)
  }).catch(console.error) }, [months])

  if (!data) return <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div>
  if (data.note) return <div className="card" style={{ ...card }}><p style={{ color: 'var(--text2)' }}>{data.note}</p></div>
  const projected = num(byod) * num(perSub) * num(active)
  const perExtra = num(perSub) * num(active)
  return (
    <div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Residual window:</label>
        <select value={months} onChange={e => setMonths(Number(e.target.value))}
          style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
          {Array.from({ length: 12 }, (_, i) => i + 1).map(m => <option key={m} value={m}>{m} month{m > 1 ? 's' : ''}</option>)}
        </select>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{data.months} month(s) with data</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14, marginBottom: 18 }}>
        <Stat label={`Total residual (${data.months} mo, MI+ATU)`} value={fmt(data.total_residual)} />
        <Stat label="Avg residual / subscriber / mo" value={fmt(data.avg_residual_per_sub)} color="#059669" />
        <Stat label="Total paid subscribers" value={(data.total_subs || 0).toLocaleString()} />
      </div>

      {data.byod_specific && (
        <div className="card" style={{ padding: 16, marginBottom: 18, borderLeft: '3px solid var(--accent)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 10 }}>
            📶 BYOD‑specific residual — measured by joining BYOD activations → subscribers ({data.byod_specific.period})
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14 }}>
            <Stat label="Residual / BYOD sub / mo" value={fmt(data.byod_specific.avg_residual_per_byod_sub)} color="var(--accent)" sub={`vs ${fmt(data.byod_specific.avg_residual_per_other_sub)} for non‑BYOD subs`} />
            <Stat label="BYOD subs earning residual" value={(data.byod_specific.byod_subs_with_residual || 0).toLocaleString()} sub={`${Math.round((data.byod_specific.match_rate || 0) * 100)}% of BYOD activations matched to residual`} />
            <Stat label="BYOD residual this month" value={fmt(data.byod_specific.byod_residual_month)} color="#059669" />
          </div>
        </div>
      )}

      <div className="card" style={{ padding: 16, marginBottom: 18 }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Residual &amp; BYOD trend
          <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}> — {data.months}-month window, month over month</span></div>
        <TrendChart height={300} leftMoney rightMoney={false}
          data={(data.series || []).map((s: any) => ({ name: s.period, residual: s.residual, byod_acts: s.byod_acts, per_sub: s.per_sub }))}
          series={[
            { key: 'residual', name: 'Residual (MI+ATU)', type: 'bar', axis: 'left', money: true, color: '#2e75b6' },
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
          <thead><tr style={{ background: 'var(--surface2)' }}>{['Period', 'Residual (MI+ATU)', 'Subs', 'Residual/sub', 'BYOD acts'].map(h =>
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
      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 14 }}>
        Residual = MI + ATU per active subscriber (recurring, seeded by each activation). The what‑if defaults to the measured BYOD‑specific residual/sub when available (else the company average) — override to model a different assumption. Historical residual can include subscribers from before our data window; going forward every logged transaction adds a residual stream. BYOD acts come from paid rep_commissions.
      </p>
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
  // least-squares trend
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
        r ranges −1…+1. A positive Accessory↔BYOD r means stores that do more BYOD also sell more accessories. Correlation ≠ causation — use it to spot where the two move together and where total revenue follows.
      </p>
    </div>
  )
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
  const [tab, setTab] = useState('mix')
  return (
    <div>
      <div style={{ marginBottom: 18 }}>
        <a href="/commcalc" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Commissions</a>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>What‑If / Scenario Analysis</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Model an ideal activation mix, BYOD's residual impact, and how accessories & BYOD move total revenue.</p>
      </div>
      <TabBar tab={tab} setTab={setTab} />
      {tab === 'mix' && <ActivationMix />}
      {tab === 'byod' && <ByodResidual />}
      {tab === 'corr' && <AccessoryByod />}
    </div>
  )
}
