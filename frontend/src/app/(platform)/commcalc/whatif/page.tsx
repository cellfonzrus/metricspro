'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { isSuperAdmin } from '@/lib/rbac'
import { TrendChart } from '@/components/TrendChart'
import { WHATIF_GRANTS, RestrictedWhatIf, useWhatIfAccess, type WhatIfTabKey } from './_components/WhatIfGate'

const card = { padding: 18, borderRadius: 12 } as const
const num = (v: any) => { const n = parseFloat(String(v).replace(/[^0-9.\-]/g, '')); return isFinite(n) ? n : 0 }

type Carrier = { id: string; name: string; code?: string; is_default?: boolean }

export const WHATIF_TABS: [WhatIfTabKey, string][] = [
  ['mix', '🎯 Employee Payout'], ['byod', '📶 BYOD → Residuals'],
  ['corr', '🔗 Accessories ↔ BYOD ↔ Revenue'], ['carrier', '💵 Company Payout / Carrier Income'],
]

// Each tab is its OWN default-closed report (owner 2026-08-03). An ungranted tab stays visible but
// disabled + 🔒 — hiding it entirely would make "the page looks different for me" unexplainable, and
// the button carries the exact permission name in its tooltip so an admin knows what to grant.
function TabBar({ tab, setTab, allowed }:
  { tab: WhatIfTabKey; setTab: (t: WhatIfTabKey) => void; allowed: Record<string, boolean> }) {
  return (
    <div style={{ display: 'flex', gap: 6, marginBottom: 20, flexWrap: 'wrap' }}>
      {WHATIF_TABS.map(([k, label]) => {
        const key = WHATIF_GRANTS[k]
        const ok = !!allowed[key]
        return (
          <button key={k} onClick={() => ok && setTab(k)} disabled={!ok}
            title={ok ? undefined : `Restricted — needs the '${key}' permission on your role.`}
            style={{ padding: '8px 14px', borderRadius: 9, fontSize: 13, fontWeight: 600,
                     cursor: ok ? 'pointer' : 'not-allowed', opacity: ok ? 1 : 0.5,
                     border: '1px solid var(--border)', background: tab === k ? 'var(--accent)' : 'var(--surface)',
                     color: tab === k ? '#fff' : 'var(--text2)' }}>{ok ? label : `🔒 ${label}`}</button>
        )
      })}
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
        {mode === 'boost' ? 'Boost engine (rates)' : 'Incentive-Plan engine'}
      </span>
    </div>
  )
}

// Default assumed sale price of one accessory (OWNER 2026-08-10: "make that $34.99 by default and
// changeable by the user"). A starting assumption for the simulator only — nothing pays off it.
const DEFAULT_ITEM_PRICE = 34.99

// ───────────────────────── Tab 1: Employee-payout template (carrier-agnostic) ─────────────────────────
function ActivationMix({ carrierId }: { carrierId: string }) {
  const [period, setPeriod] = useState('')
  const [data, setData] = useState<any>(null)
  const [qty, setQty] = useState<Record<string, number>>({})
  const [rate, setRate] = useState<Record<string, number>>({})
  const [tier, setTier] = useState(1)
  // OWNER 2026-08-10: a %-of-sales component (accessory / set-up fee) pays a FRACTION OF DOLLARS, so a
  // quantity that is a COUNT projects to nothing — there was nowhere to say what an accessory sells
  // for. Two bases per row:
  //   • 'month'  — Quantity IS the monthly sales $ total (x1). The previous behaviour, still default,
  //                so every existing projection returns the exact same number as before.
  //   • 'item'   — Quantity is the NUMBER of accessories, priced at $/item below.
  const [basis, setBasis] = useState<Record<string, 'month' | 'item'>>({})
  const [price, setPrice] = useState<Record<string, number>>({})
  // OWNER 2026-08-10: "under each employee, what would I make — it should show their current numbers
  // by default and then they can change them." '' = the company total (the previous behaviour).
  const [rep, setRep] = useState('')

  useEffect(() => { setData(null); load1(period) }, [carrierId])
  useEffect(() => { if (period) load1(period) }, [period])
  // Switching employee RE-SEEDS every quantity from that person's own actuals — the whole point is to
  // start from their real numbers, so an edit made for one employee must not follow you to the next.
  useEffect(() => { if (period) load1(period) }, [rep])
  function load1(p: string) {
    api(`/api/v1/commcalc/whatif/activation-baseline?org_id=${ORG_ID}&carrier_id=${carrierId}&period=${encodeURIComponent(p || 'auto')}&rep=${encodeURIComponent(rep)}`).then(load).catch(console.error)
  }

  function load(d: any) {
    if (!period && d.periods?.length) { setPeriod(d.periods[0]); return }
    setData(d)
    const comps = d.template?.components || []
    const q: Record<string, number> = {}, r: Record<string, number> = {}
    const bs: Record<string, 'month' | 'item'> = {}, pr: Record<string, number> = {}
    comps.forEach((c: any) => {
      q[c.key] = num(c.qty); r[c.key] = num(c.rate)
      bs[c.key] = 'month'                       // unchanged default -> identical numbers to before
      // $/item seeds from the tenant's OWN average when the baseline supplies one, else the owner's
      // stated default. Editable either way — it is an assumption, and the user owns it.
      pr[c.key] = num(c.avg_item_price) || DEFAULT_ITEM_PRICE
    })
    setQty(q); setRate(r); setBasis(bs); setPrice(pr)
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
          Configure incentive plans →</a>
      </div>
    </div>
  )

  const isPct = (c: any) => c?.kind === 'pct'
  const byKey: Record<string, any> = Object.fromEntries(comps.map((c: any) => [c.key, c]))
  // per month  -> quantity is already the $ total, x1.
  // per accessory -> $ x number of accessories, then the rate applies to those dollars.
  const rowSalesBase = (k: string) =>
    (isPct(byKey[k]) && basis[k] === 'item') ? num(qty[k]) * num(price[k]) : num(qty[k])
  const rowComm = (k: string) => rowSalesBase(k) * num(rate[k])
  // Slider bounds per row, derived from that row's own baseline so one table can hold a 3-unit row and
  // a $40,000 row without either becoming undraggable.
  const sliderMax = (c: any) => {
    const base = num(c.qty)
    if (isPct(c) && basis[c.key] !== 'item') return Math.max(Math.ceil(base * 2), 1000)
    return Math.max(Math.ceil(base * 2), 20)
  }
  const sliderStep = (c: any) => (isPct(c) && basis[c.key] !== 'item' ? 25 : 1)
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
        {/* EMPLOYEE — pick-don't-type over the people who actually have pay in this period. */}
        {(data.reps || []).length > 0 && (
          <>
            <label style={{ fontSize: 13, color: 'var(--text2)' }}>Employee:</label>
            <select value={rep} onChange={e => setRep(e.target.value)}
              style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', maxWidth: 260 }}>
              <option value="">All employees (company total)</option>
              {(data.reps || []).map((r: any) => (
                <option key={r.id} value={r.id}>{r.label}{r.store ? ` · ${r.store}` : ''} — {fmt(r.current_payout)}</option>
              ))}
            </select>
          </>
        )}
        <div style={{ flex: 1 }} />
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Tier:</label>
        {tierOpts.map((o: any) => (
          <button key={String(o.label)} onClick={() => setTier(num(o.value))}
            style={{ padding: '5px 10px', borderRadius: 7, fontSize: 12, cursor: 'pointer', border: '1px solid var(--border)',
                     background: num(tier) === num(o.value) ? 'var(--accent)' : 'var(--surface)', color: num(tier) === num(o.value) ? '#fff' : 'var(--text2)' }}>{o.label}</button>
        ))}
      </div>

      {/* Shown only when the selected employee genuinely has no qualifying lines this period — a
          statement of fact (their quantities really are zero), not a refusal to compute. */}
      {data.rep_note && (
        <div className="card" style={{ padding: 12, marginBottom: 12, fontSize: 12.5, color: '#92400e', background: '#fffbeb', borderLeft: '3px solid #f59e0b' }}>
          {data.rep_note}
        </div>
      )}

      <div className="card" style={{ padding: 0, marginBottom: 18 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Pay component', 'Basis', 'Quantity', '$ / item', 'Rate', 'Projected $', 'Current $'].map(h =>
              <th key={h} style={{ textAlign: h === 'Pay component' ? 'left' : 'right', padding: '9px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {comps.map((c, i) => (
              <tr key={c.key} style={{ borderTop: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
                <td style={{ padding: '8px 14px', fontSize: 13, fontWeight: 500 }}>{c.label}
                  <span style={{ color: 'var(--text3)', fontWeight: 400, fontSize: 11 }}> · {c.unit}{c.plan_name ? ` · ${c.plan_name}` : ''}</span></td>
                {/* BASIS — only meaningful for a %-of-sales component; a flat $-per-unit rate is already
                    per item, so the cell says so rather than offering a choice that changes nothing. */}
                <td style={{ padding: '8px 14px', textAlign: 'right' }}>
                  {isPct(c) ? (
                    <select value={basis[c.key] || 'month'}
                      onChange={e => setBasis({ ...basis, [c.key]: e.target.value as 'month' | 'item' })}
                      style={{ ...inp, width: 128, textAlign: 'left' }}>
                      <option value="month">per month</option>
                      <option value="item">per accessory</option>
                    </select>
                  ) : <span style={{ color: 'var(--text3)', fontSize: 12 }}>per unit</span>}
                </td>
                {/* QUANTITY — the number, plus a slider to nudge it (owner 2026-08-10: "having a
                    slider to increase or decrease their numbers will be very helpful"). Both edit the
                    SAME state, so typing and dragging stay in step. The slider's ceiling is derived
                    from this row's own baseline (2x, with a floor so a baseline of 0 is still
                    draggable) — a fixed max would make a 3-unit row and a $40k row unusable in the
                    same table. The baseline is marked so it is obvious what you changed FROM. */}
                <td style={{ padding: '8px 14px', textAlign: 'right' }}>
                  <input style={inp} value={qty[c.key] ?? 0} onChange={e => setQty({ ...qty, [c.key]: num(e.target.value) })} />
                  <input type="range" aria-label={`Adjust ${c.label}`}
                    min={0} max={sliderMax(c)} step={sliderStep(c)}
                    value={Math.min(num(qty[c.key]), sliderMax(c))}
                    onChange={e => setQty({ ...qty, [c.key]: num(e.target.value) })}
                    style={{ display: 'block', width: 130, marginTop: 5, accentColor: 'var(--accent)', cursor: 'pointer' }} />
                  <div style={{ fontSize: 10.5, color: 'var(--text3)', marginTop: 2 }}>
                    {isPct(c) ? (basis[c.key] === 'item' ? 'accessories' : 'sales $') : 'units'}
                    {' · now '}{isPct(c) && basis[c.key] !== 'item' ? fmt(num(c.qty)) : num(c.qty).toLocaleString()}
                    {num(qty[c.key]) !== num(c.qty) && (
                      <button onClick={() => setQty({ ...qty, [c.key]: num(c.qty) })}
                        style={{ marginLeft: 5, background: 'none', border: 'none', padding: 0, color: 'var(--accent)', cursor: 'pointer', fontSize: 10.5 }}>reset</button>
                    )}
                  </div>
                </td>
                {/* $ / ITEM — editable, defaulted, and only active in per-accessory mode. In per-month
                    mode the quantity is already dollars, so a price would double-count; the cell shows
                    an em-dash for the same reason the money columns elsewhere do: not applicable is not
                    zero. */}
                <td style={{ padding: '8px 14px', textAlign: 'right' }}>
                  {isPct(c) && basis[c.key] === 'item' ? (
                    <>
                      <span style={{ color: 'var(--text3)', fontSize: 12 }}>$</span>
                      <input style={{ ...inp, width: 74 }} value={price[c.key] ?? DEFAULT_ITEM_PRICE}
                        onChange={e => setPrice({ ...price, [c.key]: num(e.target.value) })} />
                    </>
                  ) : <span style={{ color: 'var(--text3)', fontSize: 12 }}>—</span>}
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
            {comps.length === 0 && <tr><td colSpan={7} style={{ padding: 20, color: 'var(--text3)', fontSize: 13 }}>No pay components for this carrier/period.</td></tr>}
          </tbody>
        </table>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14 }}>
        <Stat label="Projected subtotal (pre-tier)" value={fmt(subtotal)} sub={`vs current ${fmt(baseSub)}`} />
        <Stat label={`Projected payout (× ${(+num(tier)).toFixed(2)} tier)`} value={fmt(payout)} color="var(--accent)" sub={`vs current ${fmt(basePay)}`} />
        <Stat label="Delta vs current" value={(payout - basePay >= 0 ? '+' : '') + fmt(payout - basePay)} color={payout - basePay >= 0 ? '#059669' : '#dc2626'} sub={rep ? `baseline: ${rep}` : `${data.actuals?.reps ?? 0} reps in baseline`} />
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 14 }}>
        {tpl.source_kind === 'boost_rates'
          ? <>Rates pre-filled from your <b>payout_config</b> for {period}; quantities from actual <b>rep_commissions</b>. Payout = Σ(qty × rate) × tier — the exact engine formula. Accessory / Setup rates are a fraction of sales $ (0.10 = 10%), which is why those rows carry a <b>basis</b>: <b>per month</b> treats the quantity as the month's sales $ (×1), <b>per accessory</b> treats it as a COUNT and multiplies by the $/item you set (default ${DEFAULT_ITEM_PRICE}).</>
          : <>Components auto-populated from <b>{data.carrier?.name}</b>'s configured Incentive Plans / rules / tiers + payout schedules; baseline quantities from the read-only plan preview for {period}. Payout = Σ(qty × rate) × tier.</>}
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
  // `income_source` is what is CONFIGURED; `income_source_effective` is what actually fed the numbers
  // (they differ only when the ledger is configured but unreadable — then a loud ledger_note explains it).
  const effSource = trend.income_source_effective || trend.income_source
  const isMA = effSource === 'ma' || effSource === 'ma_ledger'
  const isLedger = effSource === 'ma_ledger'
  const residual = isMA ? num(cur.residual_mi_atu) : num(cur.residual)
  const totalIncome = num(cur.total_comp) + residual
  const repPay = num(base?.actuals?.total_payout)
  const net = totalIncome - repPay
  const noComp = num(cur.total_comp) === 0

  const HEADINGS: [string, number, string][] = [
    [isLedger ? 'Commission' : 'Commission' + (isMA ? ' (M1–M6)' : ' (promo)'), num(comp.COMMISSION), '#2e75b6'],
    [isLedger ? 'Spiff' : (isMA ? 'Rebate / bounty' : 'SPIFF / bounty'), num(comp.SPIFF), '#7c3aed'],
    ...(isLedger ? [['Equipment rebate', num(comp.EQUIPMENT_REBATE), '#db2777'] as [string, number, string]] : []),
    ['Reimbursement', num(comp.REIMBURSEMENT), '#0891b2'],
    [isMA ? 'Residual (Postpaid Residual Orders)' : 'Residual (MI + ATU)', residual, '#16a34a'],
    [isMA ? 'Airtime margin' : 'Unmapped', num(comp.UNMAPPED), '#f59e0b'],
    ...(isLedger ? [['Unmapped payout (ledger)', num(comp.LEDGER_OTHER), '#b45309'] as [string, number, string]] : []),
  ]
  const chartData = months.map(m => ({ name: m.period, commission: num(m.components?.COMMISSION), spiff: num(m.components?.SPIFF), equipment_rebate: num(m.components?.EQUIPMENT_REBATE), reimbursement: num(m.components?.REIMBURSEMENT), residual: isMA ? num(m.residual_mi_atu) : num(m.residual) }))

  return (
    <div>
      {trend.residual_field_warning && <NoteBanner tone="warn" text={`⚠ ${trend.residual_field_warning}`} />}
      {trend.ledger_note && <NoteBanner tone="warn" text={`⚠ ${trend.ledger_note}`} />}
      {trend.data_note && <NoteBanner tone="info" text={trend.data_note} />}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Period:</label>
        <select value={period} onChange={e => setPeriod(e.target.value)}
          style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
          {months.map(m => <option key={m.period} value={m.period}>{m.period}{num(m.total_comp) > 0 ? '' : (m.comp_source_missing ? (isLedger ? ' (no ledger lines)' : ' (no MA Commission rows)') : ' (comp not posted)')}</option>)}
        </select>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>What {trend.carrier?.name || 'the carrier'} pays the company — source: {isLedger ? 'Commission Ledger (canonical) + MA Daily Tx' : isMA ? 'MA Commission Details + MA Daily Tx' : 'Comprehensive Comp + MI+ATU'}</span>
      </div>

      {noComp && <div className="card" style={{ padding: 12, marginBottom: 14, fontSize: 13, color: '#92400e', background: '#fffbeb', borderLeft: '3px solid #f59e0b' }}>
        {cur.comp_source_missing
          ? (isLedger
            ? <>Carrier compensation for {period} reads $0 because <b>the Commission Ledger has no lines for that month</b> ({cur.daily_tx_rows} MA Daily Tx row(s), 0 ledger line(s){num(cur.commission_rows) > 0 ? `, ${cur.commission_rows} raw MA Commission row(s) waiting to be synced` : ''}) — a data gap, not a calculation error. Showing residual only; {num(cur.commission_rows) > 0 ? 'refresh the ledger from MA data on the Commission Report page.' : 'pull MA Commission Details for the month on Data\u00a0Imports, then refresh the ledger.'}</>
            : <>Carrier compensation for {period} reads $0 because <b>MA Commission Details has no rows for that month</b> ({cur.daily_tx_rows} MA Daily Tx row(s), 0 commission row(s)) — a data gap, not a calculation error. Showing residual only; pull that report for the month on Data&nbsp;Imports.</>)
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
            { key: 'spiff', name: isLedger ? 'Spiff' : (isMA ? 'Rebate' : 'SPIFF'), type: 'bar', axis: 'left', money: true, color: '#7c3aed' },
            ...(isLedger ? [{ key: 'equipment_rebate', name: 'Equip. rebate', type: 'bar' as const, axis: 'left' as const, money: true, color: '#db2777' }] : []),
            { key: 'reimbursement', name: 'Reimbursement', type: 'bar', axis: 'left', money: true, color: '#0891b2' },
            { key: 'residual', name: 'Residual', type: 'line', axis: 'left', money: true, color: '#16a34a' },
          ]} />
      </div>
      <SourceSwap swap={trend.source_swap} />
      <ClassLegSwap swap={trend.class_swap} mode={trend.class_mode} note={trend.class_note} wiring={trend.class_wiring} />
      <p style={{ fontSize: 12, color: 'var(--text3)' }}>
        Company payout / carrier income = what {trend.carrier?.name || 'the carrier / master-agent'} pays the company. Net = carrier income − employee payout ({period}). Source is resolved per carrier (⚙️ Sources).
        {isLedger && <> Commission, Spiff and Equipment rebate come from the <b>canonical Commission Ledger</b> (origin-agnostic: file imports and MA-data refreshes both count, classified by your own category map). Residual and airtime margin come from MA Daily Tx — ledger residual lines are excluded here so the same dollars are never counted twice.</>}
      </p>
    </div>
  )
}

// ─── Product-class reconciliation: order-type legs vs the CONFIRMED MA product classes (mig 265) ───
// Same two-mode pattern as SourceSwap: rendered (collapsed) whichever mode is ACTIVE, so the dollar
// impact of selecting the residual/airtime legs by product class is auditable BEFORE it is switched on.
// Nothing here changes a number — the switch lives on /commcalc/ma-class-wiring.
function ClassLegSwap({ swap, mode, note, wiring }: { swap: any; mode?: string; note?: string | null; wiring?: any }) {
  const [open, setOpen] = useState(false)
  if (!swap || !(swap.by_month || []).length) return null
  const t = swap.totals || {}
  const rows: any[] = swap.by_month || []
  const active = mode === 'class'
  const th: React.CSSProperties = { textAlign: 'right', padding: '6px 8px', fontWeight: 700, whiteSpace: 'nowrap' }
  const td: React.CSSProperties = { textAlign: 'right', padding: '6px 8px', whiteSpace: 'nowrap' }
  const delta = (v: number) => <span style={{ color: v > 0 ? '#059669' : v < 0 ? '#dc2626' : 'var(--text3)' }}>{v > 0 ? '+' : ''}{fmt(v)}</span>
  const pending = wiring?.class_map?.ambiguous_pending || []
  return (
    <div className="card" style={{ padding: 14, marginBottom: 18 }}>
      <button onClick={() => setOpen(!open)} style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontWeight: 700, fontSize: 14, color: 'var(--text)' }}>
        {open ? '▾' : '▸'} Product-class reconciliation — order-type legs vs the confirmed MA product classes
        <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text3)', marginLeft: 8 }}>
          ({active ? 'classes ACTIVE' : 'order type ACTIVE'}; residual + airtime legs, total delta {fmt(num(t.delta_total))})
        </span>
      </button>
      {open && <div style={{ marginTop: 12 }}>
        {note && <p style={{ fontSize: 12, color: '#9a3412', background: '#fff7ed', border: '1px solid #fdba74', borderRadius: 8, padding: '8px 12px', lineHeight: 1.6, margin: '0 0 10px' }}>{note}</p>}
        <p style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6, margin: '0 0 10px' }}>{swap.note}</p>
        <p style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.6, margin: '0 0 12px' }}>
          <b>Old:</b> {swap.old_source}<br /><b>New:</b> {swap.new_source}<br />
          Change the switch and the class → leg map on <a href="/commcalc/ma-class-wiring" style={{ color: 'var(--accent,#2563eb)' }}>MA Product Class → Money</a>.
        </p>
        {!!pending.length && (
          <p style={{ fontSize: 12, color: '#b91c1c', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: '8px 12px', lineHeight: 1.6, margin: '0 0 12px' }}>
            {pending.length} product name(s) flagged AMBIGUOUS are still unconfirmed, so they classify nothing and their dollars sit in “not classified”: {pending.map((a2: any) => a2.product_name).join(' · ')}
          </p>
        )}
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead><tr style={{ borderBottom: '2px solid var(--border)' }}>
              <th style={{ ...th, textAlign: 'left' }}>Month</th>
              <th style={th}>Old residual</th><th style={th}>Old airtime</th>
              <th style={th}>New residual</th><th style={th}>New airtime</th>
              <th style={th}>Δ residual</th><th style={th}>Δ airtime</th><th style={th}>Δ total</th>
              <th style={th}>Left total (classified)</th><th style={th}>Left total (unclassified)</th>
            </tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.period} style={{ borderBottom: '1px solid var(--border)', opacity: r.on_payload === false ? 0.65 : 1 }}>
                  <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>{r.period}{r.on_payload === false ? ' *' : ''}</td>
                  <td style={td}>{fmt(num(r.old_residual))}</td><td style={td}>{fmt(num(r.old_airtime))}</td>
                  <td style={td}>{fmt(num(r.new_residual))}</td><td style={td}>{fmt(num(r.new_airtime))}</td>
                  <td style={td}>{delta(num(r.delta_residual))}</td><td style={td}>{delta(num(r.delta_airtime))}</td>
                  <td style={td}>{delta(num(r.delta_total))}</td>
                  <td style={td}>{fmt(num(r.class_excluded_discount))}</td>
                  <td style={{ ...td, color: num(r.class_unclassified_lines) ? '#b91c1c' : undefined }}>{fmt(num(r.class_unclassified_discount))}</td>
                </tr>
              ))}
              <tr style={{ fontWeight: 700, borderTop: '2px solid var(--border)' }}>
                <td style={{ padding: '6px 8px' }}>TOTAL</td>
                <td style={td}>{fmt(num(t.old_residual))}</td><td style={td}>{fmt(num(t.old_airtime))}</td>
                <td style={td}>{fmt(num(t.new_residual))}</td><td style={td}>{fmt(num(t.new_airtime))}</td>
                <td style={td}>{delta(num(t.delta_residual))}</td><td style={td}>{delta(num(t.delta_airtime))}</td>
                <td style={td}>{delta(num(t.delta_total))}</td>
                <td style={td}>{fmt(num(t.class_excluded_discount))}</td>
                <td style={td}>{fmt(num(t.class_unclassified_discount))}</td>
              </tr>
            </tbody>
          </table>
        </div>
        {!!(swap.by_class || []).length && (
          <div style={{ marginTop: 12, overflowX: 'auto' }}>
            <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text3)', fontWeight: 700, marginBottom: 6 }}>By product class</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead><tr style={{ borderBottom: '2px solid var(--border)' }}>
                <th style={{ ...th, textAlign: 'left' }}>Class</th><th style={{ ...th, textAlign: 'left' }}>Leg</th>
                <th style={th}>Lines</th><th style={th}>→ Residual</th><th style={th}>→ Airtime</th><th style={th}>Left the total</th>
              </tr></thead>
              <tbody>
                {(swap.by_class || []).map((c: any) => (
                  <tr key={c.product_class} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px 8px' }}>{c.product_class}</td>
                    <td style={{ padding: '6px 8px', color: 'var(--text3)' }}>{c.leg}</td>
                    <td style={td}>{c.lines}</td><td style={td}>{fmt(num(c.residual))}</td>
                    <td style={td}>{fmt(num(c.airtime))}</td><td style={td}>{fmt(num(c.excluded_discount))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p style={{ fontSize: 11, color: 'var(--text3)', marginTop: 10, lineHeight: 1.6 }}>
          Only CONFIRMED classifications count. Anything with no confirmed class — and any class mapped to “not carrier income” — leaves the total and is shown above in dollars, never dropped silently.
          {num(t.ledger_class_overlap_lines) > 0 && <> {t.ledger_class_overlap_lines} ledger line(s) totalling {fmt(num(t.ledger_class_overlap_total))} carry a residual-class label; they are excluded from the Commission heading because the Residual heading already counts those dollars.</>}
        </p>
      </div>}
    </div>
  )
}

// ─── Source-swap reconciliation: legacy raw_ma_commission vs the canonical Commission Ledger ───────
// Always rendered (collapsed) whenever the backend ships the comparison, whichever source is ACTIVE, so
// the dollar impact of the swap is auditable on the page itself — before it is switched on and after.
function SourceSwap({ swap }: { swap: any }) {
  const [open, setOpen] = useState(false)
  if (!swap || !(swap.by_month || []).length) return null
  const t = swap.totals || {}
  const rows: any[] = swap.by_month || []
  const isLedgerActive = swap.active === 'ma_ledger'
  const th: React.CSSProperties = { textAlign: 'right', padding: '6px 8px', fontWeight: 700, whiteSpace: 'nowrap' }
  const td: React.CSSProperties = { textAlign: 'right', padding: '6px 8px', whiteSpace: 'nowrap' }
  const delta = (v: number) => <span style={{ color: v > 0 ? '#059669' : v < 0 ? '#dc2626' : 'var(--text3)' }}>{v > 0 ? '+' : ''}{fmt(v)}</span>
  return (
    <div className="card" style={{ padding: 14, marginBottom: 18 }}>
      <button onClick={() => setOpen(!open)} style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontWeight: 700, fontSize: 14, color: 'var(--text)' }}>
        {open ? '▾' : '▸'} Source reconciliation — legacy MA Commission Details vs Commission Ledger
        <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text3)', marginLeft: 8 }}>
          ({isLedgerActive ? 'ledger ACTIVE' : 'legacy ACTIVE'}; Commission + Spiff legs only, total delta {fmt(num(t.delta_total))})
        </span>
      </button>
      {open && <div style={{ marginTop: 12 }}>
        <p style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6, margin: '0 0 10px' }}>{swap.note}</p>
        <p style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.6, margin: '0 0 12px' }}>
          <b>Old:</b> {swap.old_source}<br /><b>New:</b> {swap.new_source}<br />
          Residual and airtime margin are not compared — the swap does not touch them.
        </p>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead><tr style={{ borderBottom: '2px solid var(--border)' }}>
              <th style={{ ...th, textAlign: 'left' }}>Month</th>
              <th style={th}>Old commission</th><th style={th}>Old spiff</th><th style={th}>Old total</th>
              <th style={th}>New commission</th><th style={th}>New spiff</th><th style={th}>New equip. rebate</th>
              <th style={th}>New unmapped</th><th style={th}>New total</th><th style={th}>Δ</th>
              <th style={th}>MA rows</th><th style={th}>Ledger lines</th><th style={{ ...th, textAlign: 'left' }}>Ledger origin</th>
            </tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.period} style={{ borderBottom: '1px solid var(--border)', opacity: r.on_payload === false ? 0.65 : 1 }}>
                  <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>{r.period}{r.on_payload === false ? ' *' : ''}</td>
                  <td style={td}>{fmt(num(r.old_commission))}</td><td style={td}>{fmt(num(r.old_spiff))}</td><td style={td}>{fmt(num(r.old_total))}</td>
                  <td style={td}>{fmt(num(r.new_commission))}</td><td style={td}>{fmt(num(r.new_spiff))}</td><td style={td}>{fmt(num(r.new_equipment_rebate))}</td>
                  <td style={td}>{fmt(num(r.new_other))}</td><td style={td}>{fmt(num(r.new_total))}</td><td style={td}>{delta(num(r.delta_total))}</td>
                  <td style={td}>{r.commission_rows}</td><td style={td}>{r.ledger_lines}</td>
                  <td style={{ padding: '6px 8px', color: 'var(--text3)' }}>{(r.ledger_origins || []).join(', ') || '—'}</td>
                </tr>
              ))}
              <tr style={{ fontWeight: 700, borderTop: '2px solid var(--border)' }}>
                <td style={{ padding: '6px 8px' }}>TOTAL</td>
                <td style={td}>{fmt(num(t.old_commission))}</td><td style={td}>{fmt(num(t.old_spiff))}</td><td style={td}>{fmt(num(t.old_total))}</td>
                <td style={td}>{fmt(num(t.new_commission))}</td><td style={td}>{fmt(num(t.new_spiff))}</td><td style={td}>{fmt(num(t.new_equipment_rebate))}</td>
                <td style={td}>{fmt(num(t.new_other))}</td><td style={td}>{fmt(num(t.new_total))}</td><td style={td}>{delta(num(t.delta_total))}</td>
                <td style={td}>{t.commission_rows}</td><td style={td}>{t.ledger_lines}</td><td />
              </tr>
            </tbody>
          </table>
        </div>
        <p style={{ fontSize: 11, color: 'var(--text3)', marginTop: 10, lineHeight: 1.6 }}>
          * a month the ledger knows about that the MA tables do not cover — accounted for here, but not shown above while the legacy source is active.
          {num(t.residual_overlap_lines) > 0 && <> {t.residual_overlap_lines} ledger line(s) totalling {fmt(num(t.residual_overlap_total))} carry the configured residual order type; they are EXCLUDED from the new totals because the Residual heading already counts those dollars.</>}
          {num(t.new_other) !== 0 && <> “New unmapped” is real carrier money whose label no rule classifies yet — map those labels on Commission Categories to move it into a named bucket.</>}
        </p>
      </div>}
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
  const [tab, setTab] = useState<WhatIfTabKey>('mix')
  const [carriers, setCarriers] = useState<Carrier[]>([])
  const [carrierId, setCarrierId] = useState('')
  const [mode, setMode] = useState('boost')
  const canEditSources = isSuperAdmin(permissions) || permissions?.scope === 'all'
  const { allowed, ready } = useWhatIfAccess()
  const anyAllowed = WHATIF_TABS.some(([k]) => allowed[WHATIF_GRANTS[k]])
  const tabGrant = WHATIF_GRANTS[tab]
  const tabAllowed = !!allowed[tabGrant]

  // Land on the FIRST tab the caller may actually open, so a grantee of only one report doesn't open
  // to a lock note. Runs once access resolves; never overrides a deliberate click on an allowed tab.
  useEffect(() => {
    if (!ready || tabAllowed) return
    const first = WHATIF_TABS.find(([k]) => allowed[WHATIF_GRANTS[k]])
    if (first) setTab(first[0])
  }, [ready, tabAllowed, allowed])

  // load the org's carriers once (pick-don't-type source) + resolve default carrier + mode.
  // Gated read (any-one-of-four): a caller with no grant gets a 403 here — swallow it, the page
  // already renders its own lock note.
  function loadCtx(cid: string) {
    api(`/api/v1/commcalc/whatif/source-config?org_id=${ORG_ID}&carrier_id=${cid}`).then((d: any) => {
      setCarriers(d.carriers || [])
      setMode(d.carrier_mode || 'boost')
      if (!cid && d.carrier?.id) setCarrierId(String(d.carrier.id))
    }).catch(() => {})
  }
  useEffect(() => { if (ready && anyAllowed) loadCtx('') }, [ready, anyAllowed])
  useEffect(() => { if (carrierId && anyAllowed) loadCtx(carrierId) }, [carrierId, anyAllowed])

  if (!ready) return <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>

  return (
    <div>
      <div style={{ marginBottom: 18 }}>
        <a href="/commcalc" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Commissions</a>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>What‑If / Scenario Analysis</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Carrier-agnostic scenario modeling — employee payout, company payout / carrier income, and BYOD residuals — for any carrier.</p>
      </div>

      {!anyAllowed ? (
        <RestrictedWhatIf title="What-If / Scenario Analysis" grantKey="whatif_employee_payout" />
      ) : (
        <>
          <div className="card" style={{ padding: 14, marginBottom: 18 }}>
            <CarrierPicker carriers={carriers} carrierId={carrierId} setCarrierId={setCarrierId} mode={mode} />
            {canEditSources && <SourcesPanel carrierId={carrierId} onSaved={() => loadCtx(carrierId)} />}
          </div>

          <TabBar tab={tab} setTab={setTab} allowed={allowed} />
          {!tabAllowed ? (
            <RestrictedWhatIf
              title={(WHATIF_TABS.find(([k]) => k === tab) || [, 'This report'])[1] as string}
              grantKey={tabGrant} />
          ) : (
            <>
              {tab === 'mix' && <ActivationMix carrierId={carrierId} />}
              {tab === 'byod' && <ByodResidual carrierId={carrierId} />}
              {tab === 'corr' && <AccessoryByod />}
              {tab === 'carrier' && <CarrierIncome carrierId={carrierId} />}
            </>
          )}
        </>
      )}
    </div>
  )
}
