'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

const DEFAULTS = {
  upgrade_flat: 20, premium_flat: 5, byod_flat: 3, byod_extra_spiff: 0,
  trade_in_spiff: 20, acima_spiff: 25, acc_rate: 0.10, setup_fee_rate: 0.10,
  kpi_atu_target: 55, kpi_protect_target: 80, kpi_boostapp_target: 65,
  kpi_familyplan_target: 45, kpi_byod_target: 35, kpi_tmr3_target: 70, kpi_aal_target: 5,
  tier_100_min_kpis: 7, tier_75_min_kpis: 5, tier_75_pct: 0.75, tier_50_pct: 0.50,
  straight_line: false, acc_target_enabled: false, acc_target_pct: 0.10, custom_spiffs: [],
}

const RATE_TYPE_LABELS: Record<string, string> = {
  pct_mrc: '% of MRC',
  flat: '$ Flat',
  tiered: 'Tiered',
}

const COMP_TYPE_OPTIONS = [
  'NAB','MI','SSLB','BRB','DUPGB','ISDFB','ATUMI','SIMCR','BYOD_SPIFF','TRADE_IN','PLUG',
]

function toApiPeriod(label: string): string {
  const months = ['January','February','March','April','May','June','July','August','September','October','November','December']
  const [mon, yr] = label.split(' ')
  const m = months.indexOf(mon) + 1
  return `${yr}-${String(m).padStart(2, '0')}`
}

function parsePromoPrice(sampleDesc: string): string {
  const m = sampleDesc.match(/\$(\d+(?:\.\d+)?)$/)
  return m ? `$${m[1]}` : '—'
}

export default function SettingsPage() {
  const { period } = usePeriod()
  const apiPeriod = toApiPeriod(period)

  const [cfg, setCfg] = useState<any>(DEFAULTS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [activeTab, setActiveTab] = useState<'rates'|'kpi'|'tier'|'stores'|'comprates'|'topphones'>('comprates')
  const [storeList, setStoreList] = useState<any[]>([])
  const [storeSaving, setStoreSaving] = useState<string | null>(null)
  const [aliases, setAliases] = useState<any[]>([])
  const [newAlias, setNewAlias] = useState({ alias: '', store_code: '' })
  const [aliasSaving, setAliasSaving] = useState(false)

  // Boost comp rates state
  const [compRates, setCompRates] = useState<any[]>([])
  const [compLoading, setCompLoading] = useState(false)
  const [editingRate, setEditingRate] = useState<Record<number, string>>({})
  const [savingRate, setSavingRate] = useState<number | null>(null)
  const [showAddRate, setShowAddRate] = useState(false)
  const [newRate, setNewRate] = useState({
    comp_type: 'NAB', rate_type: 'pct_mrc', value: '', plan_category: 'phone',
    duration_months: '', effective_date: new Date().toISOString().slice(0,10), notes: ''
  })

  // Top phones state
  const [topSellers, setTopSellers] = useState<any[]>([])
  const [topLoading, setTopLoading] = useState(false)

  useEffect(() => {
    api(`/api/v1/commcalc/stores?org_id=${ORG_ID}`)
      .then(setStoreList).catch(console.error)
    api(`/api/v1/commcalc/config/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(data => { if (data && Object.keys(data).length > 0) setCfg({ ...DEFAULTS, ...data }) })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [period])

  useEffect(() => {
    if (activeTab === 'comprates') loadCompRates()
    if (activeTab === 'topphones') loadTopSellers()
    if (activeTab === 'stores') loadAliases()
  }, [activeTab, period])

  async function loadAliases() {
    try {
      const d = await api(`/api/v1/commcalc/store-aliases?org_id=${ORG_ID}`)
      setAliases(d.aliases || [])
    } catch (e) { console.error(e) }
  }
  async function addAlias() {
    const alias = newAlias.alias.trim(), store_code = newAlias.store_code.trim()
    if (!alias || !store_code) return
    setAliasSaving(true)
    try {
      await api(`/api/v1/commcalc/store-aliases?org_id=${ORG_ID}`, {
        method: 'POST', body: JSON.stringify({ alias, store_code }),
      })
      setNewAlias({ alias: '', store_code: '' })
      await loadAliases()
    } catch (e) { console.error(e) } finally { setAliasSaving(false) }
  }
  async function deleteAlias(id: string) {
    try {
      await api(`/api/v1/commcalc/store-aliases/${id}?org_id=${ORG_ID}`, { method: 'DELETE' })
      setAliases(list => list.filter(a => a.id !== id))
    } catch (e) { console.error(e) }
  }

  async function loadCompRates() {
    setCompLoading(true)
    try {
      const d = await api(`/api/v1/commcalc/comp-rates?org_id=${ORG_ID}`)
      setCompRates(d)
    } catch(e) { console.error(e) }
    setCompLoading(false)
  }

  async function loadTopSellers() {
    setTopLoading(true)
    try {
      const d = await api(`/api/v1/commcalc/top-sellers/${apiPeriod}?org_id=${ORG_ID}`)
      setTopSellers(d.top_sellers || [])
    } catch(e) { console.error(e) }
    setTopLoading(false)
  }

  async function saveRate(rate: any) {
    setSavingRate(rate.id)
    try {
      const newVal = parseFloat(editingRate[rate.id] ?? String(rate.value))
      const { id: _id, org_id: _org, ...rateClean } = rate
      await api(`/api/v1/commcalc/comp-rates?org_id=${ORG_ID}`, {
        method: 'POST',
        body: JSON.stringify({ ...rateClean, value: newVal })
      })
      setCompRates(prev => prev.map(r => r.id === rate.id ? { ...r, value: newVal } : r))
      setEditingRate(prev => { const n = {...prev}; delete n[rate.id]; return n })
    } catch(e: any) { alert(e.message) }
    setSavingRate(null)
  }

  async function deleteRate(id: number) {
    if (!confirm('Delete this comp rate?')) return
    try {
      await api(`/api/v1/commcalc/comp-rates/${id}`, { method: 'DELETE' })
      setCompRates(prev => prev.filter(r => r.id !== id))
    } catch(e: any) { alert(e.message) }
  }

  async function addRate() {
    try {
      await api(`/api/v1/commcalc/comp-rates?org_id=${ORG_ID}`, {
        method: 'POST',
        body: JSON.stringify({
          ...newRate,
          value: parseFloat(newRate.value as string) || 0,
          duration_months: newRate.duration_months ? parseInt(newRate.duration_months as string) : null,
        })
      })
      setShowAddRate(false)
      setNewRate({ comp_type: 'NAB', rate_type: 'pct_mrc', value: '', plan_category: 'phone', duration_months: '', effective_date: new Date().toISOString().slice(0,10), notes: '' })
      await loadCompRates()
    } catch(e: any) { alert(e.message) }
  }

  async function save() {
    setSaving(true)
    try {
      await api(`/api/v1/commcalc/config/${encodeURIComponent(period)}?org_id=${ORG_ID}`, {
        method: 'PUT', body: JSON.stringify(cfg),
      })
      await api(`/api/v1/commcalc/calculate/${encodeURIComponent(period)}?org_id=${ORG_ID}`, {
        method: 'POST',
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 5000)
    } catch (e: any) { alert(e.message) }
    setSaving(false)
  }

  async function saveStoreMarket(storeId: string, market: string) {
    setStoreSaving(storeId)
    try {
      await api(`/api/v1/commcalc/stores/${storeId}?org_id=${ORG_ID}`, {
        method: 'PUT', body: JSON.stringify({ market }),
      })
      setStoreList(list => list.map(s => s.id === storeId ? { ...s, market } : s))
    } catch (e: any) { alert(e.message) }
    setStoreSaving(null)
  }

  function Field({ label, field, prefix = '', suffix = '' }: { label: string; field: string; prefix?: string; suffix?: string }) {
    return (
      <div>
        <label style={{ display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--text2)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          {label}
        </label>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {prefix && <span style={{ color: 'var(--text3)', fontSize: 14 }}>{prefix}</span>}
          <input
            className="input"
            type="number"
            step="0.01"
            value={cfg[field] ?? ''}
            onChange={e => setCfg((c: any) => ({ ...c, [field]: parseFloat(e.target.value) || 0 }))}
            style={{ width: 100 }}
          />
          {suffix && <span style={{ color: 'var(--text2)', fontSize: 13 }}>{suffix}</span>}
        </div>
      </div>
    )
  }

  // Group comp rates by comp_type for display
  const groupedRates = compRates.reduce((acc, r) => {
    if (!acc[r.comp_type]) acc[r.comp_type] = []
    acc[r.comp_type].push(r)
    return acc
  }, {} as Record<string, any[]>)

  const tabs = [
    { key: 'comprates', label: '💰 Carrier Comp Rates' },
    { key: 'topphones', label: '📱 Top Phones' },
    { key: 'rates', label: '⚙️ Pay Settings' },
    { key: 'kpi', label: '🎯 KPI Targets' },
    { key: 'tier', label: '📊 Tier Structure' },
    { key: 'stores', label: '🏪 Stores & Markets' },
  ] as const

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Commission Settings</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · Settings saved per period
          </p>
        </div>
        {['rates','kpi','tier','stores'].includes(activeTab) && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {saved && <span style={{ color: 'var(--green)', fontSize: 13 }}>✅ Saved & recalculating…</span>}
            <button className="btn btn-primary" onClick={save} disabled={saving}>
              {saving ? '...' : '💾 Save Settings'}
            </button>
          </div>
        )}
      </div>

      {/* Classification settings pointer — the accessory / box / bill-payment / contract-type definition
          lives in the Sales Report ⚙️ modal (kept there for easy access); it's permission-gated
          ('Classification settings'). Exposed here in the settings hub per owner directive 2026-07-18. */}
      <a href="/commcalc/sales-report" style={{ textDecoration: 'none' }}>
        <div className="card" style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, cursor: 'pointer' }}>
          <div>
            <div style={{ fontWeight: 600, fontSize: 14 }}>🏷️ Classification settings</div>
            <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
              Define what counts as an accessory, a device &ldquo;box&rdquo;, a bill payment, a device set-up fee, and how Contract Type maps to activation buckets. Opens on the Sales Report (⚙️ Classification settings). Requires the <b>Classification settings</b> permission to edit.
            </div>
          </div>
          <span style={{ color: 'var(--accent)', fontSize: 13, whiteSpace: 'nowrap' }}>Open →</span>
        </div>
      </a>

      {/* Tab switcher */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, background: 'var(--surface2)', padding: 4, borderRadius: 10, width: 'fit-content', flexWrap: 'wrap' }}>
        {tabs.map(t => (
          <button key={t.key} onClick={() => setActiveTab(t.key)} className="btn" style={{
            background: activeTab === t.key ? 'white' : 'transparent',
            color: activeTab === t.key ? 'var(--accent)' : 'var(--text2)',
            boxShadow: activeTab === t.key ? '0 1px 3px rgba(0,0,0,0.1)' : 'none', fontSize: 13,
          }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* ── BOOST COMP RATES TAB ── */}
      {activeTab === 'comprates' && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <p style={{ margin: 0, fontSize: 13, color: 'var(--text2)' }}>
              Carrier bounty/comp rates used by the discrepancy engine. Changes take effect on next run.
            </p>
            <button className="btn btn-primary" onClick={() => setShowAddRate(v => !v)} style={{ fontSize: 13 }}>
              {showAddRate ? '✕ Cancel' : '+ Add Rate'}
            </button>
          </div>

          {showAddRate && (
            <div className="card" style={{ marginBottom: 16, background: '#f0fdf4', border: '1px solid #86efac' }}>
              <div style={{ fontWeight: 600, marginBottom: 12, fontSize: 14 }}>New Comp Rate</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 12 }}>
                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>COMP TYPE</label>
                  <select className="input" value={newRate.comp_type} onChange={e => setNewRate(r => ({...r, comp_type: e.target.value}))}>
                    {COMP_TYPE_OPTIONS.map(t => <option key={t}>{t}</option>)}
                  </select>
                </div>
                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>RATE TYPE</label>
                  <select className="input" value={newRate.rate_type} onChange={e => setNewRate(r => ({...r, rate_type: e.target.value}))}>
                    <option value="pct_mrc">% of MRC</option>
                    <option value="flat">$ Flat</option>
                    <option value="tiered">Tiered</option>
                  </select>
                </div>
                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>VALUE</label>
                  <input className="input" type="number" step="0.001" placeholder={newRate.rate_type === 'pct_mrc' ? '0.20' : '2.50'} value={newRate.value} onChange={e => setNewRate(r => ({...r, value: e.target.value}))} />
                </div>
                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>PLAN CATEGORY</label>
                  <select className="input" value={newRate.plan_category} onChange={e => setNewRate(r => ({...r, plan_category: e.target.value}))}>
                    <option value="phone">phone</option>
                    <option value="tablet">tablet</option>
                    <option value="all">all</option>
                  </select>
                </div>
                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>DURATION (months)</label>
                  <input className="input" type="number" placeholder="6 or blank=ongoing" value={newRate.duration_months} onChange={e => setNewRate(r => ({...r, duration_months: e.target.value}))} />
                </div>
                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>EFFECTIVE DATE</label>
                  <input className="input" type="date" value={newRate.effective_date} onChange={e => setNewRate(r => ({...r, effective_date: e.target.value}))} />
                </div>
                <div style={{ gridColumn: 'span 2' }}>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>NOTES</label>
                  <input className="input" style={{ width: '100%' }} placeholder="e.g. BR Phone M1-M6" value={newRate.notes} onChange={e => setNewRate(r => ({...r, notes: e.target.value}))} />
                </div>
              </div>
              <button className="btn btn-primary" onClick={addRate} style={{ fontSize: 13 }}>Save Rate</button>
            </div>
          )}

          {compLoading ? (
            <div style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>Loading…</div>
          ) : (
            <div className="card" style={{ padding: 0 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                    {['Comp Type','Category','Rate Type','Value','Duration','Eff. Date','Notes',''].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '10px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {compRates.map((r, i) => {
                    const isEditing = editingRate[r.id] !== undefined
                    const displayVal = r.rate_type === 'pct_mrc'
                      ? `${(r.value * 100).toFixed(1)}%`
                      : `$${r.value.toFixed(2)}`
                    return (
                      <tr key={r.id} style={{ borderBottom: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                        <td style={{ padding: '10px 14px' }}>
                          <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--accent)' }}>{r.comp_type}</span>
                        </td>
                        <td style={{ padding: '10px 14px', fontSize: 12, color: 'var(--text2)' }}>{r.plan_category}</td>
                        <td style={{ padding: '10px 14px', fontSize: 12, color: 'var(--text2)' }}>{RATE_TYPE_LABELS[r.rate_type] || r.rate_type}</td>
                        <td style={{ padding: '10px 14px' }}>
                          {isEditing ? (
                            <input
                              className="input"
                              type="number"
                              step="0.001"
                              style={{ width: 80 }}
                              value={editingRate[r.id]}
                              onChange={e => setEditingRate(prev => ({...prev, [r.id]: e.target.value}))}
                              autoFocus
                            />
                          ) : (
                            <span
                              style={{ fontWeight: 600, fontSize: 13, cursor: 'pointer', borderBottom: '1px dashed var(--border)' }}
                              onClick={() => setEditingRate(prev => ({...prev, [r.id]: String(r.value)}))}
                              title="Click to edit"
                            >
                              {displayVal}
                            </span>
                          )}
                        </td>
                        <td style={{ padding: '10px 14px', fontSize: 12, color: 'var(--text2)' }}>
                          {r.duration_months ? `${r.duration_months}mo` : 'Ongoing'}
                        </td>
                        <td style={{ padding: '10px 14px', fontSize: 12, color: 'var(--text2)' }}>{r.effective_date}</td>
                        <td style={{ padding: '10px 14px', fontSize: 12, color: 'var(--text3)', maxWidth: 180 }}>{r.notes}</td>
                        <td style={{ padding: '10px 14px' }}>
                          <div style={{ display: 'flex', gap: 6 }}>
                            {isEditing ? (
                              <button
                                className="btn btn-primary"
                                style={{ fontSize: 11, padding: '4px 10px' }}
                                onClick={() => saveRate(r)}
                                disabled={savingRate === r.id}
                              >
                                {savingRate === r.id ? '…' : 'Save'}
                              </button>
                            ) : null}
                            <button
                              className="btn"
                              style={{ fontSize: 11, padding: '4px 10px', color: '#dc2626' }}
                              onClick={() => deleteRate(r.id)}
                            >
                              🗑
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
            💡 Click any value to edit inline. Engine always picks the rate with the latest effective_date on or before the activation date.
          </p>
        </div>
      )}

      {/* ── TOP PHONES TAB ── */}
      {activeTab === 'topphones' && (
        <div>
          <p style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 16 }}>
            Top-selling devices for <strong>{period}</strong> by activation volume. Promo price parsed from commission statement description.
          </p>

          {topLoading ? (
            <div style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>Loading…</div>
          ) : topSellers.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
              No sales data for {period}. Upload sales data first.
            </div>
          ) : (
            <div className="card" style={{ padding: 0 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                    {['#','Model','Units Sold','Promo Price (from stmt)','Full Description'].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '10px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {topSellers.map((s, i) => {
                    const promoPrice = parsePromoPrice(s.sample_desc)
                    const isAccessory = s.model.toLowerCase().includes('protect') || s.model.toLowerCase().includes('applecare')
                    return (
                      <tr key={s.model} style={{ borderBottom: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)', opacity: isAccessory ? 0.6 : 1 }}>
                        <td style={{ padding: '12px 14px', fontWeight: 700, color: 'var(--text3)', fontSize: 14 }}>
                          {i + 1}
                        </td>
                        <td style={{ padding: '12px 14px' }}>
                          <div style={{ fontWeight: 600, fontSize: 13 }}>{s.model}</div>
                          {isAccessory && <div style={{ fontSize: 11, color: 'var(--text3)' }}>insurance/accessory — no rebate</div>}
                        </td>
                        <td style={{ padding: '12px 14px' }}>
                          <span style={{ fontWeight: 700, fontSize: 15, color: 'var(--accent)' }}>{s.units}</span>
                          <span style={{ fontSize: 12, color: 'var(--text3)', marginLeft: 4 }}>units</span>
                        </td>
                        <td style={{ padding: '12px 14px' }}>
                          <span style={{ fontWeight: 600, fontSize: 14, color: promoPrice === '—' ? 'var(--text3)' : '#059669' }}>
                            {promoPrice}
                          </span>
                        </td>
                        <td style={{ padding: '12px 14px', fontSize: 12, color: 'var(--text3)', maxWidth: 300 }}>
                          {s.sample_desc}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          <div style={{ marginTop: 16, background: '#fefce8', border: '1px solid #fde047', borderRadius: 10, padding: '12px 16px', fontSize: 13, color: '#92400e' }}>
            <strong>📋 Hotsheet upload coming soon.</strong> Once enabled, you'll set the official SRP and promo price per device here, and the discrepancy engine will flag any rebate paid at the wrong price.
          </div>
        </div>
      )}

      {/* ── ORIGINAL TABS ── */}
      {activeTab === 'rates' && (
        <div>
          <div style={{ background: '#fefce8', border: '1px solid #fde047', borderRadius: 10, padding: '10px 16px', marginBottom: 20, fontSize: 13, color: '#92400e' }}>
            💡 After saving, go to Dashboard and click <strong>Run Calculation</strong> to apply new rates.
          </div>
          <div className="card">
            <div style={{ fontWeight: 600, marginBottom: 16 }}>Per-Transaction Flat Rates</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 20 }}>
              <Field label="Premium Activation" field="premium_flat" prefix="$" suffix="per act" />
              <Field label="BYOD Activation" field="byod_flat" prefix="$" suffix="per act" />
              <Field label="Device Upgrade" field="upgrade_flat" prefix="$" suffix="per act" />
              <Field label="Trade-In SPIFF" field="trade_in_spiff" prefix="$" suffix="per trade" />
              <Field label="ACIMA Financing SPIFF" field="acima_spiff" prefix="$" suffix="per txn" />
              <Field label="Additional BYOD SPIFF" field="byod_extra_spiff" prefix="$" suffix="per act" />
            </div>
            <div style={{ fontWeight: 600, margin: '24px 0 16px' }}>GP-Based Rates</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 20 }}>
              <Field label="Accessories (Ondigo dept)" field="acc_rate" suffix="= 10% of GP" />
              <Field label="Device Setup Fees" field="setup_fee_rate" suffix="= 10% of GP" />
            </div>
          </div>
        </div>
      )}

      {activeTab === 'kpi' && (
        <div className="card">
          <div style={{ fontWeight: 600, marginBottom: 16 }}>KPI Targets (minimum % to pass)</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 20 }}>
            <Field label="ATU %" field="kpi_atu_target" suffix="% target" />
            <Field label="Carrier Protect %" field="kpi_protect_target" suffix="% target" />
            <Field label="Carrier App %" field="kpi_boostapp_target" suffix="% target" />
            <Field label="Family Plan %" field="kpi_familyplan_target" suffix="% target" />
            <Field label="BYOD %" field="kpi_byod_target" suffix="% target" />
            <Field label="3MR %" field="kpi_tmr3_target" suffix="% target" />
            <Field label="AAL Conversion %" field="kpi_aal_target" suffix="% target" />
          </div>
        </div>
      )}

      {activeTab === 'tier' && (
        <div className="card">
          <div style={{ fontWeight: 600, marginBottom: 16 }}>Tier Structure</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 20 }}>
            <Field label="KPIs needed for 100% tier" field="tier_100_min_kpis" suffix="of 7 KPIs" />
            <Field label="KPIs needed for 75% tier" field="tier_75_min_kpis" suffix="of 7 KPIs" />
            <Field label="75% tier multiplier" field="tier_75_pct" suffix="(0.75 = 75%)" />
            <Field label="50% tier multiplier" field="tier_50_pct" suffix="(0.50 = 50%)" />
          </div>
          <div style={{ marginTop: 20 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
              <input type="checkbox" checked={!!cfg.straight_line}
                onChange={e => setCfg((c: any) => ({ ...c, straight_line: e.target.checked }))} />
              <span style={{ fontSize: 14 }}>Straight-line mode (no tier multiplier — everyone pays 100%)</span>
            </label>
          </div>
        </div>
      )}

      {activeTab === 'stores' && (
        <div className="card" style={{ padding: 0 }}>
          <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600 }}>
            Store Markets — {storeList.length} stores
          </div>
          <table style={{ width: '100%' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', padding: '8px 18px' }}>Store</th>
                <th style={{ textAlign: 'left', padding: '8px 18px' }}>Code</th>
                <th style={{ textAlign: 'left', padding: '8px 18px' }}>Market</th>
              </tr>
            </thead>
            <tbody>
              {storeList.map(s => (
                <tr key={s.id}>
                  <td style={{ padding: '8px 18px', fontSize: 13 }}>{s.store_address}</td>
                  <td style={{ padding: '8px 18px', fontSize: 12, color: 'var(--text3)' }}>{s.store_code}</td>
                  <td style={{ padding: '8px 18px' }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <input
                        className="input"
                        style={{ width: 120 }}
                        defaultValue={s.market || ''}
                        placeholder="e.g. NYC"
                        onBlur={e => {
                          const v = e.target.value.trim()
                          if (v !== (s.market || '')) saveStoreMarket(s.id, v)
                        }}
                      />
                      {storeSaving === s.id && <div className="spinner" style={{ width: 14, height: 14 }} />}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {activeTab === 'stores' && (
        <div className="card" style={{ padding: 0, marginTop: 20 }}>
          <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600 }}>
            Alternate store names ({aliases.length}) — sales-file aliases
          </div>
          <div style={{ padding: '10px 18px', fontSize: 12, color: 'var(--text3)' }}>
            When the B2B daily-sales file spells a store differently than its mapping above
            (e.g. <em>“3 Palisade Ave Yonkers”</em> vs <em>“3 Palisade Ave”</em>), its Daily-Targets sales
            won’t attach and the store reads 0 achieved. Map that exact sales-file spelling to the store’s
            code here. <strong>Needs migration 023_store_aliases.sql.</strong>
          </div>
          <table style={{ width: '100%' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', padding: '8px 18px' }}>Sales-file name</th>
                <th style={{ textAlign: 'left', padding: '8px 18px' }}>→ Store code</th>
                <th style={{ padding: '8px 18px' }}></th>
              </tr>
            </thead>
            <tbody>
              {aliases.map(a => (
                <tr key={a.id}>
                  <td style={{ padding: '8px 18px', fontSize: 13 }}>{a.alias}</td>
                  <td style={{ padding: '8px 18px', fontSize: 12, color: 'var(--text3)' }}>{a.store_code}</td>
                  <td style={{ padding: '8px 18px', textAlign: 'right' }}>
                    <button className="btn btn-secondary" style={{ padding: '3px 10px', fontSize: 12 }} onClick={() => deleteAlias(a.id)}>Remove</button>
                  </td>
                </tr>
              ))}
              <tr>
                <td style={{ padding: '8px 18px' }}>
                  <input className="input" style={{ width: '100%', maxWidth: 320 }} placeholder="exact name as in the sales file"
                    value={newAlias.alias} onChange={e => setNewAlias(n => ({ ...n, alias: e.target.value }))} />
                </td>
                <td style={{ padding: '8px 18px' }}>
                  <select className="input" value={newAlias.store_code} onChange={e => setNewAlias(n => ({ ...n, store_code: e.target.value }))}>
                    <option value="">— pick store —</option>
                    {storeList.map(s => <option key={s.id} value={s.store_code}>{s.store_code} · {s.store_address}</option>)}
                  </select>
                </td>
                <td style={{ padding: '8px 18px', textAlign: 'right' }}>
                  <button className="btn btn-primary" style={{ padding: '4px 12px', fontSize: 12 }}
                    disabled={aliasSaving || !newAlias.alias.trim() || !newAlias.store_code} onClick={addAlias}>
                    {aliasSaving ? 'Adding…' : 'Add alias'}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
