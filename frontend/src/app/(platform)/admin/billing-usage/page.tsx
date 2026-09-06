'use client'
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/client'

// BILLING — USAGE, PRICING & STATEMENTS (owner directives 2026-09-05, migs 972-975).
//
//   "For every tenant ai usage counter needs to be built and a cost assigned at the super admin
//    level, the cost for the tenant will be cost of the super admin / platform per token paid plus %
//    or flat margin assigned by the super admin"
//   "it should bill each call on all modules, nothing is for free, and have an itemized statement for
//    the tenant for a clear visibility including their monthly fee… the billing engine should list all
//    the modules and an option to assign price against them, a drop down menu to assign what kind of
//    plan could belong to like free, starter, premium etc"
//
// THREE HONESTY RULES THIS SCREEN ENFORCES VISUALLY, because a billing screen that looks tidy while
// under-charging is worse than one that looks messy and is right:
//   1. UNPRICED is not $0 and not free. An unpriced module/plan cell is amber and says "not priced";
//      an unpriced statement line shows its usage, is excluded from the total, and the statement is
//      badged INCOMPLETE — not sendable as an invoice.
//   2. Platform-initiated calls (our crons, sweeps, webhooks) are SHOWN next to billable ones and
//      never charged. The tenant should be able to see what we did on their behalf for free.
//   3. Every figure is computed server-side. This page renders; it decides nothing.

type Cell = { mode: string; unit_price: string | null; priced: boolean; included?: boolean; effective_date?: string }
type GridRow = { module: string; label: string; plans: Record<string, Cell> }
type Grid = {
  ok: boolean; plans: { key: string; name: string; price: number; cycle: string; currency: string; is_public: boolean }[]
  modules: GridRow[]; unpriced_cells: number; note: string; modes: string[]; source: string
}
type Line = {
  kind: string; module: string | null; label: string; calls?: number; billable_calls?: number
  system_calls?: number; mode: string; unit_price: string | null; amount: number | null
  priced: boolean; note: string; suppressed?: boolean
}
type Statement = {
  ok: boolean; org_id: string; tenant_name?: string; period_start: string; period_end: string
  plan_key: string; plan_name?: string; currency: string; lines: Line[]; total_usd: number
  billable_calls: number; total_calls: number; complete: boolean; complete_note: string
  unpriced: { label: string; note: string }[]; rounding: string; recomputed: boolean
  status?: string; closed_at?: string
}
type Overview = {
  ok: boolean; period_start: string; period_end: string
  tenants: { org_id: string; name: string; plan_key?: string; total_usd?: number; billable_calls?: number
             ai_billable_usd?: number; ai_platform_cost_usd?: number; complete?: boolean
             unpriced_lines?: number; error?: string }[]
  billable_total_usd: number; incomplete_tenants: number; note: string
  ai_totals: { platform_cost_usd: number; billable_usd: number; margin_usd: number; coverage_complete: boolean; note: string }
  coverage: { sites_total: number; sites_metered: number; complete: boolean; note: string }
}

const money = (v: number | null | undefined, cur = 'USD') =>
  v === null || v === undefined ? '—' : `${cur === 'USD' ? '$' : ''}${Number(v).toFixed(2)}`
const num = (v: number | null | undefined) => (v === null || v === undefined ? '—' : Number(v).toLocaleString())

const AMBER = { bg: '#fffbeb', color: '#92400e', border: '#fde68a' }
const GREEN = { bg: '#f0fdf4', color: '#166534', border: '#bbf7d0' }

function Badge({ ok, okText, badText }: { ok: boolean; okText: string; badText: string }) {
  const s = ok ? GREEN : AMBER
  return <span style={{
    background: s.bg, color: s.color, border: `1px solid ${s.border}`, borderRadius: 999,
    padding: '2px 10px', fontSize: 11.5, fontWeight: 700, whiteSpace: 'nowrap',
  }}>{ok ? okText : badText}</span>
}

export default function BillingUsagePage() {
  const [tab, setTab] = useState<'grid' | 'statement' | 'overview'>('overview')
  const [grid, setGrid] = useState<Grid | null>(null)
  const [ov, setOv] = useState<Overview | null>(null)
  const [stmt, setStmt] = useState<Statement | null>(null)
  const [org, setOrg] = useState('')
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [edit, setEdit] = useState<Record<string, { mode: string; price: string }>>({})

  const load = useCallback(async (which: string) => {
    setErr('')
    try {
      if (which === 'grid') setGrid(await api('/api/v1/billing/module-pricing'))
      if (which === 'overview') setOv(await api('/api/v1/billing/usage-overview'))
    } catch (e: any) { setErr(e?.message || String(e)) }
  }, [])

  useEffect(() => { load('overview'); load('grid') }, [load])

  const loadStatement = async (o: string) => {
    if (!o) return
    setErr(''); setBusy(true)
    try {
      setStmt(await api(`/api/v1/billing/statement?org_id=${encodeURIComponent(o)}`))
      setOrg(o); setTab('statement')
    } catch (e: any) { setErr(e?.message || String(e)) } finally { setBusy(false) }
  }

  const savePrice = async (planKey: string, moduleKey: string) => {
    const k = `${planKey}::${moduleKey}`
    const e = edit[k]; if (!e) return
    setBusy(true); setErr(''); setMsg('')
    try {
      const body: any = { plan_key: planKey, module_key: moduleKey, mode: e.mode }
      if (e.mode !== 'included') body.unit_price = Number(e.price)
      await api('/api/v1/billing/module-pricing', { method: 'PUT', body: JSON.stringify(body) })
      setMsg(`Saved ${moduleKey} on ${planKey}. It applies from today onward — closed statements are unaffected.`)
      setEdit(s => { const n = { ...s }; delete n[k]; return n })
      await load('grid')
    } catch (e2: any) { setErr(e2?.message || String(e2)) } finally { setBusy(false) }
  }

  const closeStatement = async () => {
    if (!org || !stmt) return
    const warn = stmt.complete ? '' :
      '\n\nWARNING: this statement has UNPRICED lines. Closing freezes it as incomplete.'
    if (!confirm(`Freeze the statement for ${stmt.tenant_name || org}?\n\nAfter this, changing a price or the monthly fee cannot alter these figures.${warn}`)) return
    setBusy(true)
    try {
      const r: any = await api(`/api/v1/billing/statement/close?org_id=${encodeURIComponent(org)}`, { method: 'POST' })
      setStmt(r); setMsg(r.warning || 'Statement closed and frozen.')
    } catch (e: any) { setErr(e?.message || String(e)) } finally { setBusy(false) }
  }

  const TabBtn = ({ id, label }: { id: any; label: string }) => (
    <button className="btn btn-sm" onClick={() => setTab(id)}
            style={{ fontWeight: tab === id ? 800 : 500, opacity: tab === id ? 1 : .7 }}>{label}</button>
  )

  return (
    <div style={{ maxWidth: 1240 }}>
      <h1 style={{ fontSize: 22, fontWeight: 800, marginBottom: 4 }}>💳 Billing — Usage &amp; Pricing</h1>
      <p className="pg-note" style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 14, maxWidth: 960 }}>
        What every tenant actually used, what it cost the platform, and what they are billed. Prices are
        set per <strong>plan × module</strong> and are effective-dated, so changing one never alters a
        statement that has already been closed. A module nobody has priced shows as
        <strong> not priced</strong> — it is excluded from the total rather than billed at $0.
      </p>

      <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
        <TabBtn id="overview" label="All tenants" />
        <TabBtn id="grid" label="Module pricing" />
        <TabBtn id="statement" label="Statement" />
      </div>

      {err && <div className="card" style={{ background: '#fef2f2', color: '#b91c1c', padding: 12, marginBottom: 12, fontSize: 13 }}>{err}</div>}
      {msg && <div className="card" style={{ background: '#f0fdf4', color: '#166534', padding: 12, marginBottom: 12, fontSize: 13 }}>{msg}</div>}

      {/* ── ALL TENANTS ─────────────────────────────────────────────────────────────────────── */}
      {tab === 'overview' && ov && (
        <>
          <div className="card" style={{ padding: 14, marginBottom: 12, display: 'flex', gap: 22, flexWrap: 'wrap' }}>
            <div><div style={{ fontSize: 11.5, color: 'var(--text3)' }}>BILLABLE THIS PERIOD</div>
              <div style={{ fontSize: 22, fontWeight: 800 }}>{money(ov.billable_total_usd)}</div></div>
            <div><div style={{ fontSize: 11.5, color: 'var(--text3)' }}>AI COST TO US</div>
              <div style={{ fontSize: 22, fontWeight: 800 }}>{money(ov.ai_totals?.platform_cost_usd)}</div></div>
            <div><div style={{ fontSize: 11.5, color: 'var(--text3)' }}>AI MARGIN</div>
              <div style={{ fontSize: 22, fontWeight: 800 }}>{money(ov.ai_totals?.margin_usd)}</div></div>
            <div style={{ flex: 1, minWidth: 240, alignSelf: 'center' }}>
              <Badge ok={ov.incomplete_tenants === 0} okText="ALL PRICED"
                     badText={`${ov.incomplete_tenants} INCOMPLETE`} />
              <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 6 }}>{ov.note}</div>
            </div>
          </div>

          {/* Metering coverage — the counter states what it does NOT see. */}
          {ov.coverage && !ov.coverage.complete && (
            <div className="card" style={{ ...AMBER, border: `1px solid ${AMBER.border}`, padding: 12, marginBottom: 12, fontSize: 12.5 }}>
              <strong>Metering is incomplete.</strong> {ov.coverage.note}
            </div>
          )}

          <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead><tr style={{ background: 'var(--bg2)' }}>
                {['Tenant', 'Plan', 'Billable calls', 'AI cost', 'AI billed', 'Total', '', ''].map((h, i) => (
                  <th key={i} style={{ textAlign: i >= 2 && i <= 5 ? 'right' : 'left', padding: '8px 10px', fontSize: 11.5, color: 'var(--text3)' }}>{h}</th>))}
              </tr></thead>
              <tbody>
                {ov.tenants.map(t => (
                  <tr key={t.org_id} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '8px 10px', fontWeight: 600 }}>{t.name || t.org_id}</td>
                    <td style={{ padding: '8px 10px' }}>{t.plan_key || <span style={{ color: AMBER.color }}>no plan</span>}</td>
                    <td style={{ padding: '8px 10px', textAlign: 'right' }}>{num(t.billable_calls)}</td>
                    <td style={{ padding: '8px 10px', textAlign: 'right' }}>{money(t.ai_platform_cost_usd)}</td>
                    <td style={{ padding: '8px 10px', textAlign: 'right' }}>{money(t.ai_billable_usd)}</td>
                    <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700 }}>{money(t.total_usd)}</td>
                    <td style={{ padding: '8px 10px' }}>
                      {t.error ? <span style={{ color: '#b91c1c', fontSize: 12 }}>{t.error}</span>
                        : <Badge ok={!!t.complete} okText="priced" badText={`${t.unpriced_lines} unpriced`} />}
                    </td>
                    <td style={{ padding: '8px 10px' }}>
                      <button className="btn btn-sm" onClick={() => loadStatement(t.org_id)}>Statement</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* ── MODULE PRICING GRID ──────────────────────────────────────────────────────────────── */}
      {tab === 'grid' && grid && (
        <>
          <div className="card" style={{ padding: 12, marginBottom: 12, fontSize: 12.5,
                                         ...(grid.unpriced_cells ? AMBER : GREEN),
                                         border: `1px solid ${grid.unpriced_cells ? AMBER.border : GREEN.border}` }}>
            <strong>{grid.unpriced_cells ? `${grid.unpriced_cells} unpriced combinations` : 'Fully priced'}</strong>
            {' — '}{grid.note}
            <div style={{ marginTop: 4, opacity: .85 }}>{grid.source}.</div>
          </div>

          <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
              <thead><tr style={{ background: 'var(--bg2)' }}>
                <th style={{ textAlign: 'left', padding: '8px 10px', fontSize: 11.5, color: 'var(--text3)' }}>MODULE</th>
                {grid.plans.map(p => (
                  <th key={p.key} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 11.5, color: 'var(--text3)' }}>
                    {p.name}<div style={{ fontWeight: 400, opacity: .8 }}>{money(p.price, p.currency)}/{p.cycle}</div>
                  </th>))}
              </tr></thead>
              <tbody>
                {grid.modules.map(m => (
                  <tr key={m.module} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '8px 10px', fontWeight: 600 }}>{m.label}
                      <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>{m.module}</div></td>
                    {grid.plans.map(p => {
                      const c = m.plans[p.key]
                      const k = `${p.key}::${m.module}`
                      const e = edit[k]
                      return (
                        <td key={p.key} style={{ padding: '6px 10px', background: c?.priced ? undefined : AMBER.bg }}>
                          {e ? (
                            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                              <select value={e.mode} onChange={ev => setEdit(s => ({ ...s, [k]: { ...e, mode: ev.target.value } }))}
                                      style={{ fontSize: 11.5 }}>
                                <option value="per_call">per call</option>
                                <option value="flat">flat</option>
                                <option value="included">included</option>
                              </select>
                              {e.mode !== 'included' && (
                                <input value={e.price} onChange={ev => setEdit(s => ({ ...s, [k]: { ...e, price: ev.target.value } }))}
                                       placeholder="0.00" style={{ width: 70, fontSize: 11.5 }} />)}
                              <button className="btn btn-sm" disabled={busy} onClick={() => savePrice(p.key, m.module)}>Save</button>
                            </div>
                          ) : (
                            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                              {c?.priced
                                ? <span>{c.included ? <em>included</em> : `${c.mode === 'flat' ? 'flat ' : ''}$${c.unit_price}${c.mode === 'per_call' ? '/call' : ''}`}</span>
                                : <span style={{ color: AMBER.color, fontWeight: 600 }}>not priced</span>}
                              <button className="btn btn-sm" style={{ opacity: .6 }}
                                      onClick={() => setEdit(s => ({ ...s, [k]: { mode: c?.mode && c.priced ? c.mode : 'per_call', price: c?.unit_price || '' } }))}>
                                {c?.priced ? 'edit' : 'set'}
                              </button>
                            </div>
                          )}
                        </td>)
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* ── ITEMIZED STATEMENT ───────────────────────────────────────────────────────────────── */}
      {tab === 'statement' && (
        <>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            <input value={org} onChange={e => setOrg(e.target.value)} placeholder="tenant org_id"
                   style={{ width: 340, fontSize: 13 }} />
            <button className="btn btn-sm" disabled={busy} onClick={() => loadStatement(org)}>Load statement</button>
            {stmt && stmt.recomputed !== false && (
              <button className="btn btn-sm" disabled={busy} onClick={closeStatement}>Freeze / close period</button>)}
          </div>

          {stmt && (
            <>
              <div className="card" style={{ padding: 14, marginBottom: 12 }}>
                <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
                  <div style={{ fontSize: 17, fontWeight: 800 }}>{stmt.tenant_name || stmt.org_id}</div>
                  <div style={{ fontSize: 13, color: 'var(--text2)' }}>
                    {stmt.period_start} → {stmt.period_end} · plan <strong>{stmt.plan_name || stmt.plan_key}</strong>
                  </div>
                  <Badge ok={stmt.complete} okText="COMPLETE" badText="INCOMPLETE — DO NOT SEND" />
                  {stmt.recomputed === false && <Badge ok okText="CLOSED / FROZEN" badText="" />}
                  <div style={{ marginLeft: 'auto', fontSize: 24, fontWeight: 800 }}>{money(stmt.total_usd, stmt.currency)}</div>
                </div>
                {!stmt.complete && (
                  <div style={{ marginTop: 8, fontSize: 12.5, color: AMBER.color }}>{stmt.complete_note}</div>)}
              </div>

              <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead><tr style={{ background: 'var(--bg2)' }}>
                    {['Item', 'Billable', 'Ours (free)', 'Rate', 'Amount'].map((h, i) => (
                      <th key={i} style={{ textAlign: i === 0 ? 'left' : 'right', padding: '8px 10px', fontSize: 11.5, color: 'var(--text3)' }}>{h}</th>))}
                  </tr></thead>
                  <tbody>
                    {stmt.lines.filter(l => !l.suppressed).map((l, i) => (
                      <tr key={i} style={{ borderTop: '1px solid var(--border)',
                                           background: l.priced ? undefined : AMBER.bg }}>
                        <td style={{ padding: '8px 10px' }}>
                          <div style={{ fontWeight: l.kind === 'plan_fee' ? 700 : 500 }}>{l.label}</div>
                          <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>{l.note}</div>
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right' }}>{num(l.billable_calls)}</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', color: 'var(--text3)' }}>{num(l.system_calls)}</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right' }}>{l.unit_price ? `$${l.unit_price}` : '—'}</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700 }}>
                          {l.amount === null ? <span style={{ color: AMBER.color }}>not priced</span> : money(l.amount, stmt.currency)}
                        </td>
                      </tr>
                    ))}
                    <tr style={{ borderTop: '2px solid var(--border)', background: 'var(--bg2)' }}>
                      <td style={{ padding: '10px', fontWeight: 800 }} colSpan={4}>Total</td>
                      <td style={{ padding: '10px', textAlign: 'right', fontWeight: 800, fontSize: 15 }}>
                        {money(stmt.total_usd, stmt.currency)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 8 }}>
                {stmt.rounding}. “Ours (free)” are calls the platform initiated — crons, sweeps and
                webhooks — counted for transparency and never charged.
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}
