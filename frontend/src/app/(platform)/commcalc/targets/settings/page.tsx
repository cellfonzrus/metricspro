'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

interface TargetRow {
  store_code: string
  address?: string
  market?: string
  activations_monthly: number
  upgrades_monthly: number
  accessories_monthly: number
  byod_pct: number | null
  notes?: string | null
  _seeded?: boolean
}

export default function TargetSettingsPage() {
  const { period } = usePeriod()
  const [rows, setRows] = useState<TargetRow[]>([])
  const [byodDefault, setByodDefault] = useState(35)
  const [loading, setLoading] = useState(true)
  const [savingCode, setSavingCode] = useState<string | null>(null)
  const [savedCode, setSavedCode] = useState<string | null>(null)

  useEffect(() => { load() }, [period])

  async function load() {
    setLoading(true)
    try {
      const d = await api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      setRows(d.targets || [])
      setByodDefault(d.byod_pct_default ?? 35)
    } catch (e) { console.error(e) }
    setLoading(false)
  }

  function update(code: string, field: keyof TargetRow, value: number | string) {
    setRows(rs => rs.map(r => r.store_code === code ? { ...r, [field]: value } : r))
  }

  async function save(row: TargetRow) {
    setSavingCode(row.store_code)
    try {
      await api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}?org_id=${ORG_ID}`, {
        method: 'PUT',
        body: JSON.stringify({
          store_code: row.store_code,
          activations_monthly: Number(row.activations_monthly) || 0,
          upgrades_monthly: Number(row.upgrades_monthly) || 0,
          accessories_monthly: Number(row.accessories_monthly) || 0,
          byod_pct: row.byod_pct === null || row.byod_pct === undefined || (row.byod_pct as any) === '' ? null : Number(row.byod_pct),
          notes: row.notes || null,
          updated_by: 'web',
        }),
      })
      setRows(rs => rs.map(r => r.store_code === row.store_code ? { ...r, _seeded: false } : r))
      setSavedCode(row.store_code)
      setTimeout(() => setSavedCode(null), 3000)
    } catch (e: any) { alert(e.message) }
    setSavingCode(null)
  }

  function byodCount(r: TargetRow): number {
    const pct = (r.byod_pct === null || r.byod_pct === undefined) ? byodDefault : Number(r.byod_pct)
    return Math.round((Number(r.activations_monthly) || 0) * pct / 100)
  }

  const th: React.CSSProperties = { textAlign: 'left', padding: '10px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }
  const td: React.CSSProperties = { padding: '8px 14px', fontSize: 13 }

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Target Settings</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          {period} · Monthly targets per store. Set at the start of the month — the engine reverse-calculates
          per-day and per-rep targets from the StoreOps schedule.
        </p>
      </div>

      <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 10, padding: '10px 16px', marginBottom: 20, fontSize: 13, color: '#1e40af' }}>
        💡 <strong>Activations</strong> = premium + BYOD acts (count). <strong>Upgrades</strong> = upgrade acts (count).
        <strong> Accessories</strong> = monthly GP ($, seeded from the store's StoreOps monthly target).
        <strong> BYOD %</strong> = share of activations expected to be BYOD (blank = KPI default {byodDefault}%).
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>Loading…</div>
      ) : rows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
          No stores found. Add stores in StoreOps first.
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 880 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                {['Store', 'Activations /mo', 'Upgrades /mo', 'Accessories $/mo', 'BYOD %', 'BYOD target', ''].map(h => (
                  <th key={h} style={th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.store_code} style={{ borderBottom: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
                  <td style={td}>
                    <div style={{ fontWeight: 600 }}>{r.address || r.store_code}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                      {r.store_code}{r.market ? ` · ${r.market}` : ''}
                      {r._seeded && <span style={{ color: '#b45309', marginLeft: 6 }}>· not yet saved</span>}
                    </div>
                  </td>
                  <td style={td}>
                    <input className="input" type="number" min="0" style={{ width: 90 }}
                      value={r.activations_monthly ?? 0}
                      onChange={e => update(r.store_code, 'activations_monthly', parseFloat(e.target.value) || 0)} />
                  </td>
                  <td style={td}>
                    <input className="input" type="number" min="0" style={{ width: 90 }}
                      value={r.upgrades_monthly ?? 0}
                      onChange={e => update(r.store_code, 'upgrades_monthly', parseFloat(e.target.value) || 0)} />
                  </td>
                  <td style={td}>
                    <input className="input" type="number" min="0" step="0.01" style={{ width: 110 }}
                      value={r.accessories_monthly ?? 0}
                      onChange={e => update(r.store_code, 'accessories_monthly', parseFloat(e.target.value) || 0)} />
                  </td>
                  <td style={td}>
                    <input className="input" type="number" min="0" max="100" style={{ width: 70 }}
                      placeholder={String(byodDefault)}
                      value={r.byod_pct ?? ''}
                      onChange={e => update(r.store_code, 'byod_pct', e.target.value === '' ? ('' as any) : (parseFloat(e.target.value) || 0))} />
                  </td>
                  <td style={{ ...td, color: 'var(--text2)' }}>{byodCount(r)} acts</td>
                  <td style={td}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 12px' }}
                        onClick={() => save(r)} disabled={savingCode === r.store_code}>
                        {savingCode === r.store_code ? '…' : 'Save'}
                      </button>
                      {savedCode === r.store_code && <span style={{ color: 'var(--green)', fontSize: 12 }}>✅</span>}
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
