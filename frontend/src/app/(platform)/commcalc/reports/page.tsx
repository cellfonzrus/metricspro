'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { SendReportButton } from '@/lib/send-report'
import { useAuth } from '@/lib/auth-context'
import { carrierMode } from '@/lib/rbac'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, isStandardFilterActive, type StandardFilterValue } from '@/lib/standard-filters'

interface Rep {
  epay_salesperson: string
  storeops_name: string
  store: string
  tier: number
  kpis_met: number
  total_kpis: number
  premium_acts: number
  byod_acts: number
  upgrade_acts: number
  premium_comm: number
  byod_comm: number
  upgrade_comm: number
  acc_comm: number
  setup_fee_comm: number
  trade_in_comm: number
  acima_comm: number
  subtotal: number
  total_payout: number
  residual_installment_comm?: number   // multi-month / Total-carrier installment pay (mig 057/078)
  carrier_statement_comm?: number
  plan_comm?: number                    // configurable Commission Plan pay (non-Boost carriers, mig 059)
  plan_name?: string
  final_payout?: number                 // total_payout − chargeback_items deducted − ops chargebacks (backend)
  chargeback_deduction?: number
  ops_chargeback_deduction?: number     // POSTED ops-accountability chargebacks (retail-ops), commission-applied (net of overflow)
  ops_chargeback_lines?: {
    label: string; amount: number; reason: string; incident_date: string; store: string; status: string
    gross_amount?: number; covered_amount?: number | null; remainder?: number
    overflow?: 'payroll' | 'next_cycle' | null; overflow_period?: string | null
  }[]
}

const TABS = [
  { id: 'breakdown', label: '👥 Rep Breakdown' },
  { id: 'individual', label: '📄 Individual Rep' },
  { id: 'compensation', label: '💰 Compensation by Line' },
]

export default function ReportsPage() {
  const { period } = usePeriod()
  const { carriers } = useAuth()
  const isBoost = carrierMode(carriers) === 'boost'   // non-Boost carriers pay via plans, not KPI tiers
  const [reps, setReps] = useState<Rep[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('breakdown')
  const [selectedRep, setSelectedRep] = useState('')
  // RULE FIVE (§3d) standard filter — period stays global (usePeriod), so the bar renders store(s)/market/
  // rep(s) multi. Options come from the already-org-scoped rep rows (pick-don't-type); `market` is stamped
  // on each row by the backend (store_mapping resolver).
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [cfg, setCfg] = useState<any>({})
  const [chargebacks, setChargebacks] = useState<any[]>([])
  const [drillComp, setDrillComp] = useState<string | null>(null)   // clicked commission component
  const [drillData, setDrillData] = useState<any>(null)
  const [drillBusy, setDrillBusy] = useState(false)

  useEffect(() => {
    api(`/api/v1/commcalc/commissions/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(setReps).catch(console.error).finally(() => setLoading(false))
    api(`/api/v1/commcalc/config/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(setCfg).catch(console.error)
    api(`/api/v1/commcalc/chargebacks/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(setChargebacks).catch(console.error)
  }, [period])

  async function toggleChargeback(itemId: string, deduct: boolean) {
    setChargebacks(cbs => cbs.map(c => c.id === itemId ? { ...c, deduct } : c))
    try {
      await api(`/api/v1/commcalc/chargebacks/${itemId}?org_id=${ORG_ID}`, {
        method: 'PUT', body: JSON.stringify({ deduct }),
      })
      // Refresh commissions so payout reflects the change
      const updated = await api(`/api/v1/commcalc/commissions/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      setReps(updated)
    } catch (e) { console.error(e) }
  }

  const repList  = useMemo(() => [...new Set(reps.map(r => r.epay_salesperson))].sort(), [reps])   // Individual-rep tab picker
  // Standard-bar options straight from the loaded (org-scoped) rows — stores/markets/reps present.
  const acc = { store: (r: Rep) => r.store, market: (r: Rep) => (r as any).market, rep: (r: Rep) => r.epay_salesperson }
  const opts = useMemo(() => optionsFromRows(reps, acc), [reps])   // eslint-disable-line react-hooks/exhaustive-deps
  // FILTERED set drives the breakdown + compensation tables, the header tiles, AND the CSV export (WYSIWYG).
  const filtered = useMemo(() => filterRows(reps, filt, acc), [reps, filt])   // eslint-disable-line react-hooks/exhaustive-deps
  const totalPayout = filtered.reduce((s, r) => s + (r.total_payout || 0), 0)
  const filterActive = isStandardFilterActive(filt)

  const currentRep = reps.find(r => r.epay_salesperson === selectedRep) || reps[0]
  // Show the Installment column only when a rep actually has multi-month / Total-carrier pay (keeps the
  // Boost view unchanged). residual_installment_comm + carrier_statement_comm are already inside Payout.
  const instOf = (r: Rep) => (r.residual_installment_comm || 0) + (r.carrier_statement_comm || 0)
  const hasInstallment = filtered.some(r => instOf(r) !== 0)

  const COMP_LABEL: Record<string, string> = { premium: 'Premium Activations', byod: 'BYOD Activations', upgrade: 'Device Upgrades', accessories: 'Accessories', setup: 'Setup Fees', acima: 'ACIMA Lease' }
  function openDrill(comp: string) {
    setDrillComp(comp)
    const rep = currentRep?.storeops_name || currentRep?.epay_salesperson || ''
    if (drillData && drillData._rep === rep) return   // already loaded for this rep+period
    setDrillData(null); setDrillBusy(true)
    api(`/api/v1/commcalc/commission-drill?org_id=${ORG_ID}&period=${encodeURIComponent(period)}&rep=${encodeURIComponent(rep)}`)
      .then((d: any) => setDrillData({ ...d, _rep: rep }))
      .catch(e => setDrillData({ error: String(e?.message || e), _rep: rep }))
      .finally(() => setDrillBusy(false))
  }
  const drillStyle = { cursor: 'pointer' } as React.CSSProperties

  function TierBadge({ tier }: { tier: number }) {
    const pct = Math.round((tier || 0) * 100)
    const cls = pct >= 100 ? 'badge-green' : pct >= 75 ? 'badge-amber' : 'badge-red'
    return <span className={`badge ${cls}`}>{pct}%</span>
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Rep Commission Report</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · {filtered.length}{filterActive ? ` of ${reps.length}` : ''} reps · Total: <strong style={{ color: 'var(--accent)' }}>{fmt(totalPayout)}</strong>
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-secondary" onClick={() => {
            // WYSIWYG (§3c): export the FILTERED rows, exactly what the standard bar is showing.
            const csv = ['Rep,Store,Tier,KPIs,PA,BA,UA,Acc GP,Subtotal,Payout']
            filtered.forEach(r => csv.push(`"${r.epay_salesperson}","${r.store}",${Math.round(r.tier*100)}%,${r.kpis_met}/${r.total_kpis},${r.premium_acts},${r.byod_acts},${r.upgrade_acts},${r.acc_comm?.toFixed(2)},${r.subtotal?.toFixed(2)},${r.total_payout?.toFixed(2)}`))
            const a = document.createElement('a'); a.href = 'data:text/csv,' + encodeURIComponent(csv.join('\n'))
            a.download = `commissions-${period.replace(' ','-')}.csv`; a.click()
          }}>
            📥 CSV
          </button>
          <SendReportButton reportKey="commissions" filters={{ period }} />
        </div>
      </div>

      {/* RULE FIVE (§3d) standard bar — ABOVE the tabs so the active filter (which drives the always-visible
          header total AND both the Breakdown and Compensation tables) is always visible + clearable, on any tab. */}
      <StandardFilterBar value={filt} onChange={setFilt} show={{ period: false }}
        storeOptions={opts.stores} marketOptions={opts.markets} repOptions={opts.reps}
        repLabel="Reps…"
        right={<span style={{ fontSize: 13, color: 'var(--text2)', alignSelf: 'center' }}>{filtered.length} of {reps.length} rows</span>} />

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, background: 'var(--surface2)', padding: 4, borderRadius: 10, width: 'fit-content' }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} className="btn" style={{
            background: tab === t.id ? 'white' : 'transparent',
            color: tab === t.id ? 'var(--accent)' : 'var(--text2)',
            boxShadow: tab === t.id ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
            fontSize: 13,
          }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Rep Breakdown */}
      {tab === 'breakdown' && (
        <div>
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Rep</th>
                  <th>Store</th>
                  <th>Tier</th>
                  <th>KPIs</th>
                  <th>PA</th><th>BA</th><th>UA</th>
                  <th style={{ textAlign: 'right' }}>ACC GP</th>
                  <th style={{ textAlign: 'right' }}>ACIMA</th>
                  {hasInstallment && <th style={{ textAlign: 'right' }} title="Multi-month / Total-carrier installment pay (already inside Payout)">Installment</th>}
                  <th style={{ textAlign: 'right' }}>Subtotal</th>
                  <th style={{ textAlign: 'right' }}>Payout</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={hasInstallment ? 12 : 11} style={{ textAlign: 'center', padding: 40 }}>
                    <div className="spinner" style={{ margin: '0 auto' }} />
                  </td></tr>
                ) : filtered.length === 0 ? (
                  <tr><td colSpan={hasInstallment ? 12 : 11} style={{ textAlign: 'center', color: 'var(--text3)', padding: 40 }}>
                    No data. Upload files and run calculation.
                  </td></tr>
                ) : filtered.map((r, i) => (
                  <tr key={i}>
                    <td>
                      <button
                        style={{ background: 'none', border: 'none', cursor: 'pointer', fontWeight: 500,
                          color: 'var(--accent)', textDecoration: 'underline', fontSize: 13, padding: 0 }}
                        onClick={() => { setSelectedRep(r.epay_salesperson); setTab('individual') }}
                      >
                        {r.storeops_name || r.epay_salesperson}
                      </button>
                      {' '}
                      <a href={`/commcalc/commission-explain?rep=${encodeURIComponent(r.storeops_name || r.epay_salesperson)}`}
                        title="How was this commission calculated? (plan + multi-month drill-down)"
                        style={{ fontSize: 11, textDecoration: 'none' }}>🔬</a>
                    </td>
                    <td style={{ color: 'var(--text3)', fontSize: 12 }}>{r.store?.substring(0, 25)}</td>
                    <td><TierBadge tier={r.tier} /></td>
                    <td style={{ fontSize: 12 }}>{r.kpis_met}/{r.total_kpis}</td>
                    <td>{r.premium_acts}</td>
                    <td>{r.byod_acts}</td>
                    <td>{r.upgrade_acts}</td>
                    <td style={{ textAlign: 'right' }}>{fmt(r.acc_comm)}</td>
                    <td style={{ textAlign: 'right', color: r.acima_comm > 0 ? '#7c3aed' : 'var(--text3)' }}>
                      {r.acima_comm > 0 ? fmt(r.acima_comm) : '—'}
                    </td>
                    {hasInstallment && <td style={{ textAlign: 'right', color: instOf(r) ? '#0369a1' : 'var(--text3)' }}>{instOf(r) ? fmt(instOf(r)) : '—'}</td>}
                    <td style={{ textAlign: 'right' }}>{fmt(r.subtotal)}</td>
                    <td style={{ textAlign: 'right', fontWeight: 700, color: 'var(--accent)' }}>{fmt(r.total_payout)}</td>
                  </tr>
                ))}
              </tbody>
              {filtered.length > 0 && (
                <tfoot>
                  <tr style={{ background: 'var(--surface2)', fontWeight: 700 }}>
                    <td colSpan={9} style={{ textAlign: 'right', paddingRight: 8, color: 'var(--text2)' }}>Total:</td>
                    {hasInstallment && <td style={{ textAlign: 'right', color: '#0369a1' }}>{fmt(filtered.reduce((s, r) => s + instOf(r), 0))}</td>}
                    <td style={{ textAlign: 'right', color: 'var(--text2)' }}>{fmt(filtered.reduce((s, r) => s + (r.subtotal || 0), 0))}</td>
                    <td style={{ textAlign: 'right', color: 'var(--accent)' }}>
                      {fmt(filtered.reduce((s, r) => s + r.total_payout, 0))}
                    </td>
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        </div>
      )}

      {/* Individual Rep */}
      {tab === 'individual' && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
            <select className="select" value={selectedRep} onChange={e => setSelectedRep(e.target.value)}>
              <option value="">Select rep...</option>
              {repList.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
            {currentRep && (
              <a className="btn btn-secondary" style={{ textDecoration: 'none' }}
                href={`/commcalc/commission-explain?rep=${encodeURIComponent(currentRep.storeops_name || currentRep.epay_salesperson)}`}
                title="Plan + multi-month drill-down: which assignment, per-rule lines, installment gates & MA cross-reference">
                🔬 How was this calculated?
              </a>
            )}
          </div>

          {currentRep ? (
            <div>
              {/* Summary cards */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 20 }}>
                <div className="card" style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--accent)' }}>{fmt(currentRep.total_payout)}</div>
                  <div style={{ color: 'var(--text2)', fontSize: 12 }}>Total Payout</div>
                </div>
                <div className="card" style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 28, fontWeight: 700 }}>{fmt(currentRep.subtotal)}</div>
                  <div style={{ color: 'var(--text2)', fontSize: 12 }}>Subtotal (pre-tier)</div>
                </div>
                {isBoost ? (
                  <div className="card" style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 28, fontWeight: 700 }}>
                      <span style={{ color: currentRep.tier >= 1 ? '#16a34a' : currentRep.tier >= 0.75 ? '#d97706' : '#dc2626' }}>
                        {Math.round((currentRep.tier || 0) * 100)}%
                      </span>
                    </div>
                    <div style={{ color: 'var(--text2)', fontSize: 12 }}>Tier Multiplier · {currentRep.kpis_met}/{currentRep.total_kpis} KPIs</div>
                  </div>
                ) : (
                  <div className="card" style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 16, fontWeight: 700, marginTop: 6 }}>{currentRep.plan_name || '— no plan —'}</div>
                    <div style={{ color: 'var(--text2)', fontSize: 12 }}>Commission Plan</div>
                  </div>
                )}
              </div>

              {/* Non-Boost carriers: pay comes from the assigned Commission Plan, not Boost line items */}
              {!isBoost && (
                <div className="card" style={{ padding: 16, marginBottom: 16 }}>
                  <div style={{ fontWeight: 600, marginBottom: 10 }}>Plan‑based Payout</div>
                  <table>
                    <tbody>
                      <tr><td>Commission Plan</td><td style={{ textAlign: 'right', fontWeight: 600 }}>{currentRep.plan_name || '— none assigned —'}</td></tr>
                      <tr><td>Plan commission</td><td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.plan_comm ?? 0)}</td></tr>
                      {(currentRep.residual_installment_comm || 0) > 0 && (
                        <tr><td>Multi‑month installments</td><td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.residual_installment_comm || 0)}</td></tr>
                      )}
                      <tr style={{ fontWeight: 700 }}><td>Total Payout</td><td style={{ textAlign: 'right', color: 'var(--accent)' }}>{fmt(currentRep.total_payout)}</td></tr>
                    </tbody>
                  </table>
                  {!currentRep.plan_name && (
                    <div style={{ fontSize: 12, color: '#dc2626', marginTop: 8 }}>
                      No plan assigned to this rep — they calculate to $0. Assign one on{' '}
                      <a href="/commcalc/commission-plans" style={{ color: 'var(--accent)' }}>Commission Plans</a>.
                    </div>
                  )}
                </div>
              )}

              {/* Line items table (Boost KPI‑tier breakdown) */}
              {isBoost && (
              <div className="card" style={{ padding: 0 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th style={{ textAlign: 'right' }}>Count</th>
                      <th style={{ textAlign: 'right' }}>Rate</th>
                      <th style={{ textAlign: 'right' }}>Commission</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="rs-clickable" style={drillStyle} onClick={() => openDrill('premium')}>
                      <td>🔍 Premium Activations</td>
                      <td style={{ textAlign: 'right' }}>{currentRep.premium_acts}</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>{fmt(cfg.premium_flat || 0)}/act</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.premium_comm)}</td>
                    </tr>
                    <tr className="rs-clickable" style={drillStyle} onClick={() => openDrill('byod')}>
                      <td>🔍 BYOD Activations</td>
                      <td style={{ textAlign: 'right' }}>{currentRep.byod_acts}</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>{fmt((cfg.byod_flat || 0) + (cfg.byod_extra_spiff || 0))}/act</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.byod_comm)}</td>
                    </tr>
                    <tr className="rs-clickable" style={drillStyle} onClick={() => openDrill('upgrade')}>
                      <td>🔍 Device Upgrades</td>
                      <td style={{ textAlign: 'right' }}>{currentRep.upgrade_acts}</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>{fmt(cfg.upgrade_flat || 0)}/act</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.upgrade_comm)}</td>
                    </tr>
                    <tr className="rs-clickable" style={drillStyle} onClick={() => openDrill('accessories')}>
                      <td>🔍 Accessories (10% GP)</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>GP</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>10% GP</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.acc_comm)}</td>
                    </tr>
                    <tr className="rs-clickable" style={drillStyle} onClick={() => openDrill('setup')}>
                      <td>🔍 Setup Fees (10% GP)</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>GP</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>10% GP</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.setup_fee_comm)}</td>
                    </tr>
                    <tr>
                      <td>Trade-In SPIFF</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>—</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>{fmt(cfg.trade_in_spiff || 0)}/trade</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.trade_in_comm)}</td>
                    </tr>
                    {(currentRep.acima_comm || 0) > 0 && (
                      <tr className="rs-clickable" style={drillStyle} onClick={() => openDrill('acima')}>
                        <td>🔍 ACIMA Lease SPIFF</td>
                        <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>
                          {Math.round((currentRep.acima_comm || 0) / (cfg.acima_spiff || 25))} txns
                        </td>
                        <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>{fmt(cfg.acima_spiff || 25)} each</td>
                        <td style={{ textAlign: 'right', fontWeight: 600, color: '#7c3aed' }}>{fmt(currentRep.acima_comm)}</td>
                      </tr>
                    )}
                    <tr style={{ background: 'var(--surface2)', fontWeight: 700 }}>
                      <td colSpan={3}>Subtotal</td>
                      <td style={{ textAlign: 'right' }}>{fmt(currentRep.subtotal)}</td>
                    </tr>
                    <tr style={{ fontWeight: 700 }}>
                      <td colSpan={3}>× {Math.round((currentRep.tier || 0) * 100)}% Tier</td>
                      <td style={{ textAlign: 'right', color: 'var(--accent)' }}>{fmt(currentRep.total_payout)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              )}

              {/* Chargeback review */}
              {(() => {
                const repCbs = chargebacks.filter(cb => cb.epay_salesperson === currentRep.epay_salesperson)
                if (!repCbs.length) return null
                const deducted = repCbs.filter(c => c.deduct).reduce((s, c) => s + (c.amount || 0), 0)
                return (
                  <div className="card" style={{ padding: 0, marginTop: 20, border: '1px solid #fca5a5' }}>
                    <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 600, background: '#fef2f2', color: '#991b1b' }}>
                      ⚠️ Potential Chargebacks — {repCbs.length} items · Toggle to deduct from payout
                    </div>
                    <table>
                      <thead>
                        <tr>
                          <th style={{ textAlign: 'left' }}>Source</th>
                          <th style={{ textAlign: 'left' }}>Description</th>
                          <th style={{ textAlign: 'left' }}>MDN/IMEI</th>
                          <th style={{ textAlign: 'right' }}>Amount</th>
                          <th style={{ textAlign: 'center' }}>Deduct?</th>
                        </tr>
                      </thead>
                      <tbody>
                        {repCbs.map(cb => (
                          <tr key={cb.id} style={{ background: cb.deduct ? '#fef2f2' : undefined }}>
                            <td style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text3)' }}>{cb.source}</td>
                            <td style={{ fontSize: 12 }}>{cb.description}</td>
                            <td style={{ fontSize: 11, color: 'var(--text3)' }}>{cb.mdn || cb.imei || '—'}</td>
                            <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(cb.amount)}</td>
                            <td style={{ textAlign: 'center' }}>
                              <input type="checkbox" checked={!!cb.deduct}
                                onChange={e => toggleChargeback(cb.id, e.target.checked)}
                                style={{ width: 18, height: 18, cursor: 'pointer' }} />
                            </td>
                          </tr>
                        ))}
                        <tr style={{ fontWeight: 700, background: 'var(--surface2)' }}>
                          <td colSpan={3}>Total Deducted</td>
                          <td style={{ textAlign: 'right', color: 'var(--red)' }}>−{fmt(deducted)}</td>
                          <td></td>
                        </tr>
                        <tr style={{ fontWeight: 700 }}>
                          <td colSpan={3}>Final Payout (after all deductions)</td>
                          <td style={{ textAlign: 'right', color: 'var(--accent)' }}>{fmt((currentRep.total_payout || 0) - deducted - (currentRep.ops_chargeback_deduction || 0))}</td>
                          <td></td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                )
              })()}

              {/* Ops-accountability chargebacks (POSTED, read-only) — retail-ops' commcalc.ops_chargeback,
                  commission-applied. Deducted from this person's commission for the period. Posting/waiving
                  happens on the DM Verify page (retail-ops), NOT here — this is a read-only statement line. */}
              {(() => {
                const lines = currentRep.ops_chargeback_lines || []
                if (!lines.length) return null
                const opsTotal = currentRep.ops_chargeback_deduction ?? lines.reduce((s, l) => s + (l.amount || 0), 0)
                const hasCbItems = chargebacks.some(cb => cb.epay_salesperson === currentRep.epay_salesperson)
                return (
                  <div className="card" style={{ padding: 0, marginTop: 20, border: '1px solid #fca5a5' }}>
                    <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 600, background: '#fef2f2', color: '#991b1b' }}>
                      🔻 Ops Accountability Chargebacks — {lines.length} POSTED · deducted from commission
                    </div>
                    <table>
                      <thead>
                        <tr>
                          <th style={{ textAlign: 'left' }}>Ops chargeback</th>
                          <th style={{ textAlign: 'center' }}>Status</th>
                          <th style={{ textAlign: 'right' }}>Amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {lines.map((l, i) => (
                          <tr key={i}>
                            <td style={{ fontSize: 12 }}>{l.label}</td>
                            <td style={{ textAlign: 'center' }}>
                              <span className="badge badge-red" style={{ textTransform: 'uppercase', fontSize: 10 }}>{l.status || 'posted'}</span>
                            </td>
                            <td style={{ textAlign: 'right', fontWeight: 600 }}>−{fmt(l.amount)}</td>
                          </tr>
                        ))}
                        <tr style={{ fontWeight: 700, background: 'var(--surface2)' }}>
                          <td colSpan={2}>Total ops chargebacks deducted</td>
                          <td style={{ textAlign: 'right', color: 'var(--red)' }}>−{fmt(opsTotal)}</td>
                        </tr>
                        {!hasCbItems && (
                          <tr style={{ fontWeight: 700 }}>
                            <td colSpan={2}>Final Payout (after ops chargebacks)</td>
                            <td style={{ textAlign: 'right', color: 'var(--accent)' }}>{fmt(currentRep.final_payout ?? ((currentRep.total_payout || 0) - opsTotal))}</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                    <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--text3)' }}>
                      Posted or waived by management on the DM Verify page — read-only here.
                    </div>
                  </div>
                )
              })()}
            </div>
          ) : (
            <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
              Select a rep to view their commission breakdown
            </div>
          )}
        </div>
      )}

      {/* Compensation by Line */}
      {tab === 'compensation' && (
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Rep</th>
                <th style={{ textAlign: 'right' }}>Premium</th>
                <th style={{ textAlign: 'right' }}>BYOD</th>
                <th style={{ textAlign: 'right' }}>Upgrades</th>
                <th style={{ textAlign: 'right' }}>Accessories</th>
                <th style={{ textAlign: 'right' }}>Setup Fees</th>
                <th style={{ textAlign: 'right' }}>Trade-Ins</th>
                <th style={{ textAlign: 'right' }}>ACIMA</th>
                <th style={{ textAlign: 'right' }}>Subtotal</th>
                <th style={{ textAlign: 'right' }}>Payout</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 500 }}>{r.storeops_name || r.epay_salesperson}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.premium_comm)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.byod_comm)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.upgrade_comm)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.acc_comm)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.setup_fee_comm)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.trade_in_comm)}</td>
                  <td style={{ textAlign: 'right', color: r.acima_comm > 0 ? '#7c3aed' : 'var(--text3)' }}>
                    {r.acima_comm > 0 ? fmt(r.acima_comm) : '—'}
                  </td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.subtotal)}</td>
                  <td style={{ textAlign: 'right', fontWeight: 700, color: 'var(--accent)' }}>{fmt(r.total_payout)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Commission component drill-down — the exact transactions behind a paid-out line */}
      {drillComp && (() => {
        const b = drillData && !drillData.error ? drillData[drillComp] : null
        const moneyBucket = drillComp === 'accessories' || drillComp === 'setup'
        return (
          <div onClick={() => setDrillComp(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
            <div onClick={e => e.stopPropagation()} style={{ background: 'var(--surface)', borderRadius: 12, padding: 20, width: 'min(900px,97vw)', maxHeight: '88vh', overflowY: 'auto', boxShadow: '0 12px 40px rgba(0,0,0,0.25)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 8 }}>
                <div>
                  <div style={{ fontSize: 16, fontWeight: 700 }}>{COMP_LABEL[drillComp]} · {currentRep?.storeops_name || currentRep?.epay_salesperson}</div>
                  <div style={{ fontSize: 12, color: 'var(--text3)' }}>{period}{b ? ` · ${moneyBucket ? `${b.count} line${b.count === 1 ? '' : 's'} · ${fmt(b.sales)} sales · ${fmt(b.gp)} GP` : `${b.count} transaction${b.count === 1 ? '' : 's'}`}` : ''}{drillData?.source === 'daily_sales_feed' ? ' · source: daily feed' : ''}</div>
                </div>
                <button className="btn btn-secondary" style={{ padding: '2px 10px' }} onClick={() => setDrillComp(null)}>✕</button>
              </div>
              {drillBusy ? (
                <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>Loading transactions…</div>
              ) : drillData?.error ? (
                <div style={{ padding: 20, color: '#dc2626', fontSize: 13 }}>❌ {drillData.error}</div>
              ) : !b || b.items.length === 0 ? (
                <div style={{ padding: 20, color: 'var(--text3)', fontSize: 13 }}>No transactions found for this component in {period}.</div>
              ) : (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead><tr style={{ background: 'var(--surface2)' }}>
                      {['Date', 'Trans ID', 'Product', drillComp === 'acima' ? 'Tender' : 'Contract', 'MDN', 'Price', 'GP'].map(h =>
                        <th key={h} style={{ textAlign: h === 'Price' || h === 'GP' ? 'right' : 'left', padding: '5px 8px', fontSize: 10, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }}>{h}</th>)}
                    </tr></thead>
                    <tbody>
                      {b.items.map((it: any, i: number) => (
                        <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                          <td style={{ padding: '5px 8px', whiteSpace: 'nowrap' }}>{it.date}</td>
                          <td style={{ padding: '5px 8px', fontFamily: 'monospace' }}>{it.trans_id}</td>
                          <td style={{ padding: '5px 8px' }}>{it.product || '—'}</td>
                          <td style={{ padding: '5px 8px' }}>{drillComp === 'acima' ? (it.tender_type || '—') : (it.contract_type || '—')}</td>
                          <td style={{ padding: '5px 8px' }}>{it.mdn || '—'}</td>
                          <td style={{ padding: '5px 8px', textAlign: 'right' }}>{fmt(it.ext_price)}</td>
                          <td style={{ padding: '5px 8px', textAlign: 'right' }}>{fmt(it.gp)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )
      })()}
    </div>
  )
}
