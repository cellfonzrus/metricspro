'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID } from '@/lib/client'

const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function CompaniesPage() {
  const [companies, setCompanies] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [assign, setAssign] = useState<Record<string, string>>({})  // store_address -> company_id
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [newCo, setNewCo] = useState({ name: '', legal_name: '', ein: '' })
  const [search, setSearch] = useState('')
  const [market, setMarket] = useState('')
  const [bulkCo, setBulkCo] = useState('')
  const [coEdit, setCoEdit] = useState<Record<string, { name: string; legal_name: string; ein: string }>>({})
  const [coSaving, setCoSaving] = useState('')
  const [msStores, setMsStores] = useState<string[]>([])   // multi-select: stores to assign at once
  const [msCompany, setMsCompany] = useState('')           // multi-select: target company ('' = Default)

  function load() {
    setLoading(true)
    api(`/api/v1/account/stores?org_id=${ORG_ID}`).then((d: any) => {
      setCompanies(d.companies || [])
      setStores(d.stores || [])
      const a: Record<string, string> = {}
      ;(d.stores || []).forEach((s: any) => { if (s.company_id) a[s.store_address] = s.company_id })
      setAssign(a)
    }).catch(console.error).finally(() => setLoading(false))
    // load the full company records (with legal_name/ein) for the editor
    api(`/api/v1/account/companies?org_id=${ORG_ID}`).then((d: any) => {
      const cs = d.companies || []
      setCompanies(cs)
      setCoEdit(Object.fromEntries(cs.map((c: any) => [c.id,
        { name: c.name || '', legal_name: c.legal_name || '', ein: c.ein || '' }])))
    }).catch(() => {})
  }
  useEffect(() => { load() }, [])

  async function addCompany() {
    const name = newCo.name.trim()
    if (!name) return
    setMsg('')
    try {
      await api(`/api/v1/account/companies?org_id=${ORG_ID}`, { method: 'POST', body: JSON.stringify(newCo) })
      setNewCo({ name: '', legal_name: '', ein: '' }); load()
    } catch (e: any) { setMsg('Add failed: ' + (e?.message || e)) }
  }

  async function updateCompany(id: string) {
    const e = coEdit[id]
    if (!e || !e.name.trim()) { setMsg('Company name cannot be blank.'); return }
    setCoSaving(id); setMsg('')
    try {
      await api(`/api/v1/account/companies/${id}?org_id=${ORG_ID}`, {
        method: 'PATCH',
        body: JSON.stringify({ name: e.name.trim(), legal_name: e.legal_name.trim(), ein: e.ein.trim() }),
      })
      setMsg('Company updated.'); load()
    } catch (err: any) { setMsg('Update failed: ' + (err?.message || err)) }
    finally { setCoSaving('') }
  }

  const markets = Array.from(new Set(stores.map(s => s.market).filter(Boolean))).sort()
  const visible = stores.filter(s => (!market || s.market === market) &&
    (!search || s.store_address.toLowerCase().includes(search.toLowerCase())))

  function bulkApply() {
    if (!bulkCo) return
    setAssign(a => { const n = { ...a }; visible.forEach(s => { n[s.store_address] = bulkCo }); return n })
  }

  function assignMulti() {
    if (!msStores.length) return
    setAssign(a => { const n = { ...a }; msStores.forEach(sa => { n[sa] = msCompany }); return n })
    const coName = companies.find(c => c.id === msCompany)?.name || 'Default Company'
    setMsg(`Staged ${msStores.length} store${msStores.length > 1 ? 's' : ''} → ${coName}. Click “Save assignments” to persist.`)
    setMsStores([])
  }

  async function save() {
    setSaving(true); setMsg('')
    const assignments = stores.map(s => ({ store_address: s.store_address, company_id: assign[s.store_address] || null }))
    try {
      const r = await api(`/api/v1/account/companies/assign?org_id=${ORG_ID}`, { method: 'POST', body: JSON.stringify({ assignments }) })
      setMsg(`Saved ${r.saved} store assignments. Re-compute statements to apply.`)
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
    setSaving(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏢 Companies</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Legal entities + which store belongs to which company. P&amp;L and Balance Sheet roll up store → company → consolidated.</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{msg}</span>}
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : '💾 Save assignments'}</button>
        </div>
      </div>

      <div className="card" style={{ padding: 14, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Companies ({companies.length}) — edit name / legal name / EIN inline</div>
        <div style={{ display: 'grid', gap: 8, marginBottom: 12 }}>
          {companies.map(c => (
            <div key={c.id} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <input style={{ ...inp, width: 180 }} value={coEdit[c.id]?.name ?? ''} placeholder="Company name"
                onChange={e => setCoEdit(m => ({ ...m, [c.id]: { ...m[c.id], name: e.target.value } }))} />
              <input style={{ ...inp, width: 180 }} value={coEdit[c.id]?.legal_name ?? ''} placeholder="Legal name"
                onChange={e => setCoEdit(m => ({ ...m, [c.id]: { ...m[c.id], legal_name: e.target.value } }))} />
              <input style={{ ...inp, width: 120 }} value={coEdit[c.id]?.ein ?? ''} placeholder="EIN"
                onChange={e => setCoEdit(m => ({ ...m, [c.id]: { ...m[c.id], ein: e.target.value } }))} />
              <button className="btn" disabled={coSaving === c.id} onClick={() => updateCompany(c.id)}>{coSaving === c.id ? '…' : 'Save'}</button>
            </div>
          ))}
          {companies.length === 0 && <span style={{ fontSize: 12, color: 'var(--text3)' }}>No companies yet — add one below.</span>}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', borderTop: '1px solid var(--border)', paddingTop: 10 }}>
          <input style={{ ...inp, width: 180 }} placeholder="New company name" value={newCo.name} onChange={e => setNewCo({ ...newCo, name: e.target.value })} />
          <input style={{ ...inp, width: 180 }} placeholder="Legal name (optional)" value={newCo.legal_name} onChange={e => setNewCo({ ...newCo, legal_name: e.target.value })} />
          <input style={{ ...inp, width: 120 }} placeholder="EIN (optional)" value={newCo.ein} onChange={e => setNewCo({ ...newCo, ein: e.target.value })} />
          <button className="btn" onClick={addCompany}>＋ Add company</button>
        </div>
      </div>

      <div className="card" style={{ padding: 12, marginBottom: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <select style={inp} value={market} onChange={e => setMarket(e.target.value)}>
          <option value="">All markets</option>{markets.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <input style={{ ...inp, width: 200 }} placeholder="Find store…" value={search} onChange={e => setSearch(e.target.value)} />
        <span style={{ width: 1, height: 22, background: 'var(--border)' }} />
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>Set all {visible.length} shown to</span>
        <select style={inp} value={bulkCo} onChange={e => setBulkCo(e.target.value)}>
          <option value="">— company —</option>{companies.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <button className="btn" onClick={bulkApply}>Apply</button>
      </div>

      {/* Multi-select: pick one/many stores → assign to a company in one shot (RULE THREE: stores are
          chosen from the existing list — store_mapping ∪ the tenant's sales data — never typed). */}
      <div className="card" style={{ padding: 12, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Assign multiple stores at once</div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4 }}>Pick stores — Ctrl/Cmd-click for several (the market / find-store filter above narrows this list)</div>
            <select multiple value={msStores} style={{ ...inp, minWidth: 280, height: 170 }}
              onChange={e => setMsStores(Array.from(e.target.selectedOptions, o => o.value))}>
              {visible.map(s => <option key={s.store_address} value={s.store_address}>{s.store_address}{s.market ? ` · ${s.market}` : ''}</option>)}
            </select>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{msStores.length} selected · {visible.length} shown</div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingTop: 18 }}>
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>Assign selected to</span>
            <select style={inp} value={msCompany} onChange={e => setMsCompany(e.target.value)}>
              <option value="">— Default Company —</option>
              {companies.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <button className="btn btn-primary" onClick={assignMulti} disabled={!msStores.length}>
              {msStores.length ? `Assign ${msStores.length} store${msStores.length > 1 ? 's' : ''} →` : 'Assign →'}
            </button>
          </div>
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 16px' }}>Store address</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Market</th>
                <th style={{ textAlign: 'left', padding: '8px 16px' }}>Company</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(s => (
                <tr key={s.store_address} style={{ borderTop: '1px solid var(--border)', fontSize: 13 }}>
                  <td style={{ padding: '7px 16px' }}>{s.store_address}</td>
                  <td style={{ padding: '7px 12px', color: 'var(--text2)' }}>{s.market || '—'}</td>
                  <td style={{ padding: '7px 16px' }}>
                    <select style={{ ...inp, minWidth: 180 }} value={assign[s.store_address] || ''} onChange={e => setAssign(a => ({ ...a, [s.store_address]: e.target.value }))}>
                      <option value="">— Default Company —</option>
                      {companies.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
              {visible.length === 0 && <tr><td colSpan={3} style={{ padding: 30, textAlign: 'center', color: 'var(--text3)' }}>No stores match. Stores come from the store-mapping registry and from your sales data (raw_sales / daily feed).</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
