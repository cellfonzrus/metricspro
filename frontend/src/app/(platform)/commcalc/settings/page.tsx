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

export default function SettingsPage() {
  const { period } = usePeriod()
  const [cfg, setCfg] = useState<any>(DEFAULTS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [activeTab, setActiveTab] = useState<'rates'|'kpi'|'tier'|'stores'>('rates')
  const [storeList, setStoreList] = useState<any[]>([])
  const [storeSaving, setStoreSaving] = useState<string | null>(null)

  useEffect(() => {
    api(`/api/v1/commcalc/stores?org_id=${ORG_ID}`)
      .then(setStoreList).catch(console.error)
    api(`/api/v1/commcalc/config/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(data => { if (data && Object.keys(data).length > 0) setCfg({ ...DEFAULTS, ...data }) })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [period])

  async function save() {
    setSaving(true)
    try {
      await api(`/api/v1/commcalc/config/${encodeURIComponent(period)}?org_id=${ORG_ID}`, {
        method: 'PUT', body: JSON.stringify(cfg),
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
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

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Commission Settings</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · Settings saved per period
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {saved && <span style={{ color: 'var(--green)', fontSize: 13 }}>✅ Saved</span>}
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? '...' : '💾 Save Settings'}
          </button>
        </div>
      </div>

      <div style={{ background: '#fefce8', border: '1px solid #fde047', borderRadius: 10, padding: '10px 16px', marginBottom: 20, fontSize: 13, color: '#92400e' }}>
        💡 After saving, go to Dashboard and click <strong>Run Calculation</strong> to apply new rates.
      </div>

      {/* Tab switcher */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, background: 'var(--surface2)', padding: 4, borderRadius: 10, width: 'fit-content' }}>
        {(['rates', 'kpi', 'tier', 'stores'] as const).map(t => (
          <button key={t} onClick={() => setActiveTab(t)} className="btn" style={{
            background: activeTab === t ? 'white' : 'transparent',
            color: activeTab === t ? 'var(--accent)' : 'var(--text2)',
            boxShadow: activeTab === t ? '0 1px 3px rgba(0,0,0,0.1)' : 'none', fontSize: 13,
          }}>
            {t === 'rates' ? '💰 Commission Rates' : t === 'kpi' ? '🎯 KPI Targets' : t === 'tier' ? '📊 Tier Structure' : '🏪 Stores & Markets'}
          </button>
        ))}
      </div>

      {activeTab === 'rates' && (
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
      )}

      {activeTab === 'kpi' && (
        <div className="card">
          <div style={{ fontWeight: 600, marginBottom: 16 }}>KPI Targets (minimum % to pass)</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 20 }}>
            <Field label="ATU %" field="kpi_atu_target" suffix="% target" />
            <Field label="Boost Protect %" field="kpi_protect_target" suffix="% target" />
            <Field label="Boost App %" field="kpi_boostapp_target" suffix="% target" />
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
    </div>
  )
}
