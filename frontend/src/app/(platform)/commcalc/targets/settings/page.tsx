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
  _seed_basis?: Record<string, string>   // per-category: 'stretch' | 'carry' | 'new'
  _prior_period?: string
}

export default function TargetSettingsPage() {
  const { period } = usePeriod()
  const [rows, setRows] = useState<TargetRow[]>([])
  const [byodDefault, setByodDefault] = useState(35)
  const [loading, setLoading] = useState(true)
  const [savingCode, setSavingCode] = useState<string | null>(null)
  const [savedCode, setSavedCode] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [market, setMarket] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulk, setBulk] = useState<any>({ activations_monthly: '', upgrades_monthly: '', accessories_monthly: '', byod_pct: '' })
  const [savingAll, setSavingAll] = useState(false)
  const [bulkMsg, setBulkMsg] = useState('')
  const [rolling, setRolling] = useState(false)
  const [rollMsg, setRollMsg] = useState('')

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

  async function putTarget(row: TargetRow) {
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
  }

  async function save(row: TargetRow) {
    setSavingCode(row.store_code)
    try {
      await putTarget(row)
      setRows(rs => rs.map(r => r.store_code === row.store_code ? { ...r, _seeded: false } : r))
      setSavedCode(row.store_code)
      setTimeout(() => setSavedCode(null), 3000)
    } catch (e: any) { alert(e.message) }
    setSavingCode(null)
  }

  // Persist the month-over-month carry-forward for this period: prior target carried forward, or
  // +10% where last month's target was hit. overwrite=false → only fills stores with no row yet.
  async function rollForward(overwrite: boolean) {
    if (overwrite && !confirm('Overwrite EVERY store for this month with last month’s carry-forward — including targets you edited by hand?')) return
    setRolling(true); setRollMsg('')
    try {
      const d = await api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/roll-forward?org_id=${ORG_ID}`, {
        method: 'POST', body: JSON.stringify({ overwrite }),
      })
      setRollMsg(`Rolled forward from ${d.prior_period}: saved ${d.written} store(s)${d.skipped ? `, skipped ${d.skipped}` : ''}.`)
      await load()
    } catch (e: any) { setRollMsg(e.message || 'Roll-forward failed') }
    setRolling(false)
  }

  function toggleSel(code: string) {
    setSelected(s => { const n = new Set(s); n.has(code) ? n.delete(code) : n.add(code); return n })
  }
  function selectAllFiltered(on: boolean, codes: string[]) {
    setSelected(on ? new Set(codes) : new Set())
  }
  // Apply the common values (only the filled fields) to the chosen stores.
  function applyBulk(codes: string[]) {
    const targets = selected.size ? selected : new Set(codes)  // none selected → all filtered
    setRows(rs => rs.map(r => {
      if (!targets.has(r.store_code)) return r
      const u: any = { ...r }
      if (bulk.activations_monthly !== '') u.activations_monthly = Number(bulk.activations_monthly) || 0
      if (bulk.upgrades_monthly !== '') u.upgrades_monthly = Number(bulk.upgrades_monthly) || 0
      if (bulk.accessories_monthly !== '') u.accessories_monthly = Number(bulk.accessories_monthly) || 0
      if (bulk.byod_pct !== '') u.byod_pct = Number(bulk.byod_pct) || 0
      return u
    }))
    setBulkMsg(`Applied to ${targets.size} store(s). Review, then "Save selected".`)
  }
  async function saveSelected(codes: string[]) {
    const targetCodes = selected.size ? selected : new Set(codes)
    const toSave = rows.filter(r => targetCodes.has(r.store_code))
    if (!toSave.length) { setBulkMsg('No stores selected.'); return }
    setSavingAll(true); setBulkMsg('Saving…')
    let ok = 0, fail = 0
    for (const row of toSave) { try { await putTarget(row); ok++ } catch { fail++ } }
    setRows(rs => rs.map(r => targetCodes.has(r.store_code) ? { ...r, _seeded: false } : r))
    setSavingAll(false)
    setBulkMsg(`Saved ${ok} store(s)${fail ? ` · ${fail} failed` : ''}.`)
  }

  function byodCount(r: TargetRow): number {
    const pct = (r.byod_pct === null || r.byod_pct === undefined) ? byodDefault : Number(r.byod_pct)
    return Math.round((Number(r.activations_monthly) || 0) * pct / 100)
  }

  const th: React.CSSProperties = { textAlign: 'left', padding: '10px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }
  const td: React.CSSProperties = { padding: '8px 14px', fontSize: 13 }

  const markets = Array.from(new Set(rows.map(r => r.market).filter(Boolean))).sort() as string[]
  const filtered = rows.filter(r => (!market || r.market === market) &&
    (!search || `${r.address || ''} ${r.store_code} ${r.market || ''}`.toLowerCase().includes(search.toLowerCase())))
  const filteredCodes = filtered.map(r => r.store_code)

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

      {/* Month-over-month carry-forward */}
      <div className="card" style={{ padding: '12px 16px', marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 260, fontSize: 13, color: 'var(--text2)' }}>
          🔁 <strong>Carry forward month over month.</strong> Stores without a target for {period} are pre-filled from
          last month — a category the store <strong>hit</strong> becomes <strong>+10% (stretch)</strong>, one it missed
          carries the same number forward. Click below to save these for all stores.
        </div>
        <button className="btn btn-primary" onClick={() => rollForward(false)} disabled={rolling}>
          {rolling ? 'Rolling…' : '🔁 Roll forward from last month'}
        </button>
        <button className="btn" onClick={() => rollForward(true)} disabled={rolling} title="Overwrites every store, including hand-edited targets">
          Overwrite all
        </button>
        {rollMsg && <span style={{ fontSize: 12, color: 'var(--text2)', flexBasis: '100%' }}>{rollMsg}</span>}
      </div>

      {/* Store filter + bulk apply */}
      {!loading && rows.length > 0 && (
        <div className="card" style={{ padding: 14, marginBottom: 16 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>Filter:</span>
            <select className="input" value={market} onChange={e => setMarket(e.target.value)}>
              <option value="">All markets</option>
              {markets.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
            <input className="input" placeholder="Search store…" value={search} onChange={e => setSearch(e.target.value)} style={{ width: 200 }} />
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>{filtered.length} shown · {selected.size} selected</span>
          </div>
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>Set common target →</span>
            <input className="input" type="number" placeholder="Acts/mo" value={bulk.activations_monthly} onChange={e => setBulk({ ...bulk, activations_monthly: e.target.value })} style={{ width: 90 }} />
            <input className="input" type="number" placeholder="Upg/mo" value={bulk.upgrades_monthly} onChange={e => setBulk({ ...bulk, upgrades_monthly: e.target.value })} style={{ width: 90 }} />
            <input className="input" type="number" placeholder="Acc $/mo" value={bulk.accessories_monthly} onChange={e => setBulk({ ...bulk, accessories_monthly: e.target.value })} style={{ width: 100 }} />
            <input className="input" type="number" placeholder="BYOD %" value={bulk.byod_pct} onChange={e => setBulk({ ...bulk, byod_pct: e.target.value })} style={{ width: 80 }} />
            <button className="btn" onClick={() => applyBulk(filteredCodes)}>Apply to {selected.size || filtered.length}</button>
            <button className="btn btn-primary" onClick={() => saveSelected(filteredCodes)} disabled={savingAll}>{savingAll ? 'Saving…' : `💾 Save ${selected.size || filtered.length}`}</button>
            {bulkMsg && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{bulkMsg}</span>}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>Only filled boxes are applied. With none checked, Apply/Save hit all {filtered.length} filtered stores.</div>
        </div>
      )}

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
                <th style={th}><input type="checkbox" checked={filtered.length > 0 && filtered.every(r => selected.has(r.store_code))} onChange={e => selectAllFiltered(e.target.checked, filteredCodes)} /></th>
                {['Store', 'Activations /mo', 'Upgrades /mo', 'Accessories $/mo', 'BYOD %', 'BYOD target', ''].map(h => (
                  <th key={h} style={th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((r, i) => (
                <tr key={r.store_code} style={{ borderBottom: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
                  <td style={td}><input type="checkbox" checked={selected.has(r.store_code)} onChange={() => toggleSel(r.store_code)} /></td>
                  <td style={td}>
                    <div style={{ fontWeight: 600 }}>{r.address || r.store_code}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                      {r.store_code}{r.market ? ` · ${r.market}` : ''}
                      {r._seeded && <span style={{ color: '#b45309', marginLeft: 6 }}>· not yet saved</span>}
                      {r._seeded && r._seed_basis && (
                        Object.values(r._seed_basis).includes('stretch')
                          ? <span style={{ color: 'var(--green)', marginLeft: 6 }}>· 🔼 +10% stretch (hit {r._prior_period})</span>
                          : Object.values(r._seed_basis).includes('carry')
                            ? <span style={{ color: 'var(--text3)', marginLeft: 6 }}>· ↔ carried from {r._prior_period}</span>
                            : null
                      )}
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
