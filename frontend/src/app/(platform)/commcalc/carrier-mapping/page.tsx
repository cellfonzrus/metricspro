'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// Carrier category map (SaaS framework Phase 1): map each carrier's raw compensation category to a
// canonical component (RESIDUAL/COMMISSION/SPIFF/REIMBURSEMENT) — config-driven, no code. Unmapped
// categories from the comp data are surfaced for one-click mapping.
const COMPONENTS = ['RESIDUAL', 'COMMISSION', 'SPIFF', 'REIMBURSEMENT']
const MATCH = ['exact', 'prefix', 'contains', 'regex']
const COMP_COLOR: Record<string, string> = { RESIDUAL: '#2563eb', COMMISSION: '#16794a', SPIFF: '#b45309', REIMBURSEMENT: '#7c3aed', UNMAPPED: '#b42318' }
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }

export default function CarrierMappingPage() {
  const { period } = usePeriod()
  const [carriers, setCarriers] = useState<any[]>([])
  const [cid, setCid] = useState('')
  const [rules, setRules] = useState<any[]>([])
  const [comp, setComp] = useState<any>(null)
  const [unmapped, setUnmapped] = useState<any[]>([])
  const [nu, setNu] = useState<Record<string, string>>({})
  const [add, setAdd] = useState({ raw_category: '', match_type: 'contains', component: 'COMMISSION', subtype: '', priority: '100' })
  const [cAdd, setCAdd] = useState({ name: '', code: '', is_default: false })
  const [msg, setMsg] = useState('')

  const loadCarriers = useCallback((selectId?: string) => {
    api('/api/v1/commcalc/carriers').then((c: any) => {
      setCarriers(c || [])
      setCid(prev => selectId || prev || (c?.[0]?.id ?? ''))
    }).catch(() => {})
  }, [])
  useEffect(() => { loadCarriers() }, [loadCarriers])

  async function addCarrier() {
    const name = cAdd.name.trim()
    if (!name) { setMsg('Enter a carrier name.'); return }
    try {
      const r: any = await api('/api/v1/commcalc/carriers', { method: 'POST', body: JSON.stringify({ name, code: cAdd.code.trim() || undefined, is_default: cAdd.is_default }) })
      setMsg('✅ Carrier added.'); setCAdd({ name: '', code: '', is_default: false }); loadCarriers(r?.id)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function saveCarrier(c: any) {
    try { await api(`/api/v1/commcalc/carriers/${c.id}`, { method: 'PATCH', body: JSON.stringify({ name: c.name, code: c.code || '', is_default: !!c.is_default }) }); setMsg('✅ Saved.'); loadCarriers(c.id) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function delCarrier(c: any) {
    if (!window.confirm(`Delete carrier "${c.name}"? Its category rules are kept but will no longer be carrier-scoped.`)) return
    try { await api(`/api/v1/commcalc/carriers/${c.id}`, { method: 'DELETE' }); loadCarriers() } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  const setCarrier = (i: number, patch: any) => setCarriers(cs => cs.map((c, j) => j === i ? { ...c, ...patch } : c))

  const loadRules = useCallback(() => {
    if (!cid) return
    api(`/api/v1/commcalc/carrier-category-map?carrier_id=${cid}`).then(setRules).catch(() => {})
  }, [cid])
  const loadComp = useCallback(() => {
    if (!period) return
    api(`/api/v1/commcalc/comp-by-component?period=${encodeURIComponent(period)}${cid ? `&carrier_id=${cid}` : ''}`).then(setComp).catch(() => {})
    api(`/api/v1/commcalc/carrier-category-map/unmapped?period=${encodeURIComponent(period)}${cid ? `&carrier_id=${cid}` : ''}`).then((d: any) => setUnmapped(d?.unmapped || [])).catch(() => {})
  }, [period, cid])
  useEffect(() => { loadRules(); loadComp() }, [loadRules, loadComp])

  async function saveRule(r: any) {
    try { await api('/api/v1/commcalc/carrier-category-map', { method: 'POST', body: JSON.stringify({ ...r, carrier_id: cid }) }); setMsg('✅ Saved.'); loadRules(); loadComp() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function delRule(id: string) {
    try { await api(`/api/v1/commcalc/carrier-category-map/${id}`, { method: 'DELETE' }); loadRules(); loadComp() } catch {}
  }
  async function addRule() {
    if (!add.raw_category.trim()) { setMsg('Enter a category pattern.'); return }
    await saveRule({ ...add, priority: Number(add.priority) || 100 })
    setAdd({ raw_category: '', match_type: 'contains', component: 'COMMISSION', subtype: '', priority: '100' })
  }
  async function mapUnmapped(cat: string) {
    const c = nu[cat]; if (!c) return
    await saveRule({ raw_category: cat, match_type: 'exact', component: c, priority: 50 })
  }

  const setRule = (i: number, patch: any) => setRules(rs => rs.map((r, j) => j === i ? { ...r, ...patch } : r))
  const comps = comp?.components || {}

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📡 Carrier Mapping — Comp Report → 4 Components</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Manage your carriers, and sort each carrier's raw <b>compensation-report categories</b> into the 4
          canonical components that power the <b>Total Compensation</b> report. Config-driven — works for any
          carrier. Period: {period}.
        </p>
      </div>

      {/* How-to (plain-language, step by step) */}
      <details className="card" style={{ padding: 14, marginBottom: 16, background: 'var(--surface2)' }}>
        <summary style={{ cursor: 'pointer', fontWeight: 700, fontSize: 14 }}>📘 How to use this page (step by step)</summary>
        <ol style={{ margin: '10px 0 6px 18px', fontSize: 13, lineHeight: 1.7, color: 'var(--text2)' }}>
          <li><b>Add your carrier</b> in the Carriers table below (Name + optional short code) if it isn't listed.</li>
          <li>In <b>“Mapping rules for:”</b> pick the carrier you're mapping.</li>
          <li>Look at <b style={{ color: '#b42318' }}>⚠️ categories that need mapping</b> — these are the REAL category
            labels found in that carrier's comp data for {period}. For each, choose a component and click <b>Map</b>.
            (Fastest way — you only map what actually appears.)</li>
          <li>Or add a rule by hand in the <b>rules table</b>: type a <b>pattern</b>, choose how it should <b>match</b>,
            and the <b>component</b> it belongs to. Click <b>+ Add</b>.</li>
          <li>The <b>component totals</b> at the top update as you map. Anything left in <b>UNMAPPED</b> isn't counted —
            keep mapping until UNMAPPED is $0.</li>
        </ol>
        <div style={{ fontSize: 12.5, color: 'var(--text2)', lineHeight: 1.7 }}>
          <b>What the 4 components mean:</b>
          <div style={{ marginLeft: 4, marginTop: 2 }}>
            🔵 <b>RESIDUAL</b> — recurring monthly income (MI + ATU) you keep earning each month per active line.<br/>
            🟢 <b>COMMISSION</b> — the up-front payment for an activation / sale.<br/>
            🟠 <b>SPIFF</b> — bonuses / promos / incentives on top of commission.<br/>
            🟣 <b>REIMBURSEMENT</b> — money paid back to cover a cost (device, shipping, fees).
          </div>
          <div style={{ marginTop: 8 }}>
            <b>Match types:</b> <b>exact</b> = the label is exactly this · <b>prefix</b> = label starts with this ·
            <b>contains</b> = the text appears anywhere (most common, safest) · <b>regex</b> = advanced pattern.
            &nbsp;<b>Priority:</b> when two rules could match, the <b>lower number wins</b> — give specific rules a
            number below 100.
          </div>
          <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--border)' }}>
            ℹ️ <b>Not the same as “Category → Bucket Map”.</b> THIS page classifies the carrier's <b>comp / residual
            statement</b> for the <b>Total Compensation</b> report (4 components). To classify a carrier's
            <b> commission-file line items</b> for the <b>Commission Ledger</b> (5 buckets), use{' '}
            <a href="/commcalc/commission-category-map" style={{ color: 'var(--accent)' }}>Category → Bucket Map</a>.
          </div>
        </div>
      </details>

      {/* Carriers manager (add / edit / delete) */}
      <div className="card" style={{ padding: 14, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>📡 Carriers</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Name', 'Code', 'Default', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {carriers.map((c, i) => (
              <tr key={c.id}>
                <td style={cell}><input style={{ ...sel, width: '100%' }} value={c.name || ''} onChange={e => setCarrier(i, { name: e.target.value })} /></td>
                <td style={cell}><input style={{ ...sel, width: 110 }} placeholder="optional" value={c.code || ''} onChange={e => setCarrier(i, { code: e.target.value })} /></td>
                <td style={cell}><input type="radio" name="defcarrier" checked={!!c.is_default} onChange={() => setCarrier(i, { is_default: true })} /></td>
                <td style={cell}>
                  <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={() => saveCarrier(carriers[i])}>Save</button>
                  <button className="btn btn-secondary" style={{ fontSize: 12, marginLeft: 4 }} onClick={() => delCarrier(c)}>✕</button>
                </td>
              </tr>
            ))}
            <tr style={{ background: 'var(--surface2)' }}>
              <td style={cell}><input style={{ ...sel, width: '100%' }} placeholder="e.g. Cricket" value={cAdd.name} onChange={e => setCAdd({ ...cAdd, name: e.target.value })} /></td>
              <td style={cell}><input style={{ ...sel, width: 110 }} placeholder="code" value={cAdd.code} onChange={e => setCAdd({ ...cAdd, code: e.target.value })} /></td>
              <td style={cell}><input type="checkbox" checked={cAdd.is_default} onChange={e => setCAdd({ ...cAdd, is_default: e.target.checked })} /></td>
              <td style={cell}><button className="btn btn-primary" style={{ fontSize: 12 }} onClick={addCarrier}>+ Add carrier</button></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, color: 'var(--text2)' }}>Mapping rules for:</span>
        <select style={sel} value={cid} onChange={e => setCid(e.target.value)}>
          {carriers.map(c => <option key={c.id} value={c.id}>{c.name}{c.is_default ? ' (default)' : ''}</option>)}
          {carriers.length === 0 && <option value="">No carriers yet — add one above</option>}
        </select>
        {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
      </div>

      {/* Component totals (the payoff) */}
      {comp && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 12, marginBottom: 18 }}>
          {['RESIDUAL', 'COMMISSION', 'SPIFF', 'REIMBURSEMENT', 'UNMAPPED'].map(k => (
            <div key={k} className="card" style={{ padding: 12, borderTop: `3px solid ${COMP_COLOR[k]}` }}>
              <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{k}</div>
              <div style={{ fontSize: 18, fontWeight: 700, marginTop: 2 }}>{fmt(comps[k])}</div>
            </div>
          ))}
        </div>
      )}

      {/* Unmapped categories panel */}
      {unmapped.length > 0 && (
        <div className="card" style={{ padding: 14, marginBottom: 18, borderLeft: '4px solid #b42318' }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>⚠️ {unmapped.length} categories need mapping ({period})</div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              {unmapped.map((u: any) => (
                <tr key={u.category}>
                  <td style={cell}><b>{u.category}</b></td>
                  <td style={{ ...cell, textAlign: 'right', color: 'var(--text3)' }}>{fmt(u.amount)} · {u.count}×</td>
                  <td style={{ ...cell, textAlign: 'right' }}>
                    <select style={sel} value={nu[u.category] || ''} onChange={e => setNu(p => ({ ...p, [u.category]: e.target.value }))}>
                      <option value="">map to…</option>
                      {COMPONENTS.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                    <button className="btn btn-primary" style={{ fontSize: 12, marginLeft: 6 }} disabled={!nu[u.category]} onClick={() => mapUnmapped(u.category)}>Map</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Rules table */}
      <div className="card table-wrapper" style={{ padding: 0 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Category pattern', 'Match', 'Component', 'Subtype', 'Priority', 'Active', ''].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {rules.map((r, i) => (
              <tr key={r.id}>
                <td style={cell}><input style={{ ...sel, width: '100%' }} value={r.raw_category || ''} onChange={e => setRule(i, { raw_category: e.target.value })} /></td>
                <td style={cell}><select style={sel} value={r.match_type || 'exact'} onChange={e => setRule(i, { match_type: e.target.value })}>{MATCH.map(m => <option key={m} value={m}>{m}</option>)}</select></td>
                <td style={cell}><select style={{ ...sel, color: COMP_COLOR[r.component] }} value={r.component} onChange={e => setRule(i, { component: e.target.value })}>{COMPONENTS.map(c => <option key={c} value={c}>{c}</option>)}</select></td>
                <td style={cell}><input style={{ ...sel, width: 90 }} value={r.subtype || ''} onChange={e => setRule(i, { subtype: e.target.value })} /></td>
                <td style={cell}><input style={{ ...sel, width: 60 }} value={r.priority ?? 100} onChange={e => setRule(i, { priority: Number(e.target.value) || 0 })} /></td>
                <td style={cell}><input type="checkbox" checked={r.is_active !== false} onChange={e => setRule(i, { is_active: e.target.checked })} /></td>
                <td style={cell}>
                  <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={() => saveRule(r)}>Save</button>
                  <button className="btn btn-secondary" style={{ fontSize: 12, marginLeft: 4 }} onClick={() => delRule(r.id)}>✕</button>
                </td>
              </tr>
            ))}
            {/* add row */}
            <tr style={{ background: 'var(--surface2)' }}>
              <td style={cell}><input style={{ ...sel, width: '100%' }} placeholder="e.g. Promo" value={add.raw_category} onChange={e => setAdd({ ...add, raw_category: e.target.value })} /></td>
              <td style={cell}><select style={sel} value={add.match_type} onChange={e => setAdd({ ...add, match_type: e.target.value })}>{MATCH.map(m => <option key={m} value={m}>{m}</option>)}</select></td>
              <td style={cell}><select style={sel} value={add.component} onChange={e => setAdd({ ...add, component: e.target.value })}>{COMPONENTS.map(c => <option key={c} value={c}>{c}</option>)}</select></td>
              <td style={cell}><input style={{ ...sel, width: 90 }} placeholder="subtype" value={add.subtype} onChange={e => setAdd({ ...add, subtype: e.target.value })} /></td>
              <td style={cell}><input style={{ ...sel, width: 60 }} value={add.priority} onChange={e => setAdd({ ...add, priority: e.target.value })} /></td>
              <td style={cell}></td>
              <td style={cell}><button className="btn btn-primary" style={{ fontSize: 12 }} onClick={addRule}>+ Add</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}
