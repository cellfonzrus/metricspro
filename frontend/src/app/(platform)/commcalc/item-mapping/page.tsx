'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'
import EntityPicker from '@/components/EntityPicker'

// The "SU sheet": each sales item (SKU / description) → type (accessory|phone|other|unclassified)
// + phone model. Seeds from the Product Catalog; auto-grows as new items appear in sales. item_type
// drives whether a sales line counts as an accessory on the Accessory Flags report.
// Phone model is REQUIRED when type=phone, and is selectable+typable from the model catalogue.
type Item = {
  id: string; item_key: string; sku: string | null; item_desc: string | null
  department: string | null; category: string | null; item_type: string
  device_model: string | null; source: string | null
}
const TYPES = ['accessory', 'phone', 'rebate', 'other', 'unclassified']
const TYPE_COLOR: Record<string, string> = { accessory: '#b45309', phone: '#2563eb', rebate: '#7c3aed', other: '#6b7280', unclassified: '#b42318' }
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }

export default function ItemMappingPage() {
  const [items, setItems] = useState<Item[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [models, setModels] = useState<string[]>([])
  const [registry, setRegistry] = useState<{ id: string; model: string }[]>([])
  const [picked, setPicked] = useState<Set<string>>(new Set())   // selected item_keys (bulk)
  const [bulkType, setBulkType] = useState('')
  const [bulkModel, setBulkModel] = useState('')
  const [newModel, setNewModel] = useState('')
  const [showCatalogue, setShowCatalogue] = useState(false)
  const [search, setSearch] = useState('')
  const [typeF, setTypeF] = useState('')
  const [loading, setLoading] = useState(true)
  const [ready, setReady] = useState(true)
  const [msg, setMsg] = useState('')

  const loadModels = useCallback(() => {
    api('/api/v1/commcalc/device-models').then((r: any) => { setModels(r.models || []); setRegistry(r.registry || []) }).catch(() => {})
  }, [])
  const load = useCallback(() => {
    setLoading(true)
    api('/api/v1/commcalc/item-mapping').then((r: any) => {
      setItems(r.items || []); setCounts(r.counts || {}); setReady(r.ready !== false)
      if (r.ready === false) setMsg('Run migration 041 to enable item mapping.')
    }).catch((e: any) => setMsg('Load failed: ' + (e?.message || e))).finally(() => setLoading(false))
  }, [])
  useEffect(() => { load(); loadModels() }, [load, loadModels])

  const setItem = (id: string, patch: Partial<Item>) => setItems(its => its.map(i => i.id === id ? { ...i, ...patch } : i))

  async function save(i: Item) {
    setMsg('')
    if (i.item_type === 'phone' && !(i.device_model || '').trim()) { setMsg(`⚠️ Phone model is required for "${i.item_desc || i.sku || i.item_key}".`); return }
    try {
      await api('/api/v1/commcalc/item-mapping', { method: 'POST', body: JSON.stringify({
        item_key: i.item_key, sku: i.sku, item_desc: i.item_desc,
        item_type: i.item_type, device_model: i.device_model,
        department: i.department, category: i.category }) })
      setMsg(`Saved ${i.item_desc || i.sku || i.item_key}`)
      if ((i.device_model || '').trim() && !models.includes(i.device_model!.trim())) loadModels()
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
  }
  async function del(i: Item) {
    if (!confirm(`Delete mapping for ${i.item_desc || i.sku || i.item_key}?`)) return
    try { await api(`/api/v1/commcalc/item-mapping/${i.id}`, { method: 'DELETE' }); setItems(its => its.filter(x => x.id !== i.id)) }
    catch (e: any) { setMsg('Delete failed: ' + (e?.message || e)) }
  }
  async function seed() {
    if (!confirm('Seed item types + phone models from the Product Catalog upload? (Your manually-classified items are kept.)')) return
    setMsg('Seeding…')
    try { const r = await api('/api/v1/commcalc/item-mapping/seed-from-catalog', { method: 'POST', body: '{}' }); setMsg(`Seeded ${r.seeded} of ${r.catalog_rows} catalog items.`); load(); loadModels() }
    catch (e: any) { setMsg('Seed failed: ' + (e?.message || e)) }
  }

  // ── bulk ──────────────────────────────────────────────────────────────────────────────────
  const filtered = items.filter(i =>
    (!typeF || i.item_type === typeF) &&
    (!search || `${i.item_desc || ''} ${i.sku || ''} ${i.device_model || ''}`.toLowerCase().includes(search.toLowerCase())))
  const allPicked = filtered.length > 0 && filtered.every(i => picked.has(i.item_key))
  const toggle = (k: string) => setPicked(p => { const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n })
  const toggleAll = () => setPicked(p => { const n = new Set(p); if (allPicked) filtered.forEach(i => n.delete(i.item_key)); else filtered.forEach(i => n.add(i.item_key)); return n })
  async function applyBulk() {
    if (picked.size === 0) { setMsg('Select some items first.'); return }
    if (!bulkType && !bulkModel.trim()) { setMsg('Choose a type and/or a phone model to apply.'); return }
    if (bulkType === 'phone' && !bulkModel.trim()) { setMsg('⚠️ Phone model is required when setting type to phone.'); return }
    try {
      const r: any = await api('/api/v1/commcalc/item-mapping/bulk', { method: 'POST', body: JSON.stringify({ item_keys: [...picked], item_type: bulkType || undefined, device_model: bulkModel.trim() || undefined }) })
      setMsg(`✅ Applied to ${r.updated} item${r.updated === 1 ? '' : 's'}.`); setPicked(new Set()); setBulkType(''); setBulkModel(''); load(); loadModels()
    } catch (e: any) { setMsg('Bulk failed: ' + (e?.message || e)) }
  }

  // ── phone-model catalogue ───────────────────────────────────────────────────────────────────
  async function addModel() {
    const m = newModel.trim(); if (!m) return
    try { await api('/api/v1/commcalc/device-models', { method: 'POST', body: JSON.stringify({ model: m }) }); setNewModel(''); setMsg(`✅ Added model "${m}".`); loadModels() }
    catch (e: any) { setMsg('Add model failed: ' + (e?.message || e)) }
  }
  async function delModel(r: { id: string; model: string }) {
    if (!confirm(`Remove "${r.model}" from the model catalogue? (Items already using it keep it.)`)) return
    try { await api(`/api/v1/commcalc/device-models/${r.id}`, { method: 'DELETE' }); loadModels() } catch (e: any) { setMsg('Remove failed: ' + (e?.message || e)) }
  }

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧩 Item / Model Mapping</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Classify each item (accessory vs phone) and set its phone model. Drives the Accessory Flags report. Select multiple rows to map them together. Phone model is required when type is &quot;phone&quot;.
        </p>
      </div>

      {/* Phone-model catalogue */}
      <div className="card" style={{ padding: 12, marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }} onClick={() => setShowCatalogue(s => !s)}>
          <b style={{ fontSize: 14 }}>📱 Phone model catalogue</b>
          <span style={{ fontSize: 12, color: 'var(--text3)' }}>{models.length} models · {registry.length} added manually</span>
          <span style={{ marginLeft: 'auto', fontSize: 12 }}>{showCatalogue ? '▲' : '▼'}</span>
        </div>
        {showCatalogue && (
          <div style={{ marginTop: 10 }}>
            <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
              <input style={{ ...sel, width: 260 }} placeholder="Add a phone model, e.g. iPhone 16 128GB" value={newModel} onChange={e => setNewModel(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') addModel() }} />
              <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={addModel}>+ Add model</button>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {registry.map(r => (
                <span key={r.id} className="badge" style={{ fontSize: 12, background: 'var(--surface2)', padding: '4px 8px', borderRadius: 6 }}>
                  {r.model} <button onClick={() => delModel(r)} style={{ marginLeft: 4, border: 'none', background: 'none', cursor: 'pointer', color: '#dc2626' }}>✕</button>
                </span>
              ))}
              {registry.length === 0 && <span style={{ fontSize: 12, color: 'var(--text3)' }}>No manually-added models yet — the combobox also lists models already used on phones.</span>}
            </div>
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <input className="input" placeholder="Search item / sku / model…" value={search} onChange={e => setSearch(e.target.value)} style={{ ...sel, width: 240 }} />
        <select style={sel} value={typeF} onChange={e => setTypeF(e.target.value)}>
          <option value="">All types</option>{TYPES.map(t => <option key={t} value={t}>{t} {counts[t] ? `(${counts[t]})` : ''}</option>)}
        </select>
        <button className="btn" onClick={seed}>📥 Seed from Product Catalog</button>
        <div style={{ flex: 1 }} />
        {Object.entries(counts).map(([t, n]) => <span key={t} className="badge" style={{ fontSize: 11, background: TYPE_COLOR[t] || '#888', color: '#fff' }}>{t}: {n}</span>)}
        {msg && <span style={{ fontSize: 13, width: '100%' }}>{msg}</span>}
      </div>

      {/* Bulk action bar */}
      {picked.size > 0 && (
        <div className="card" style={{ padding: 10, marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', borderLeft: '4px solid #2563eb' }}>
          <b style={{ fontSize: 13 }}>{picked.size} selected →</b>
          <span style={{ fontSize: 12 }}>set type</span>
          <select style={sel} value={bulkType} onChange={e => setBulkType(e.target.value)}>
            <option value="">(keep)</option>{TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <span style={{ fontSize: 12 }}>set model</span>
          <EntityPicker
            options={(() => { const o = models.map(m => ({ id: m, label: m })); if (bulkModel && !o.some(x => x.id === bulkModel)) o.unshift({ id: bulkModel, label: bulkModel }); return o })()}
            value={bulkModel || null} allowCreate width={200}
            onChange={v => setBulkModel(v || '')} onCreate={v => setBulkModel(v)}
            placeholder={bulkType === 'phone' ? 'required for phone' : '(keep)'} ariaLabel="Set phone model" />
          <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={applyBulk}>Apply to {picked.size}</button>
          <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={() => setPicked(new Set())}>Clear</button>
        </div>
      )}

      <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 960 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            <th style={{ padding: '8px', width: 28 }}><input type="checkbox" checked={allPicked} onChange={toggleAll} /></th>
            {['Item', 'SKU', 'Dept / Cat', 'Type', 'Phone model', 'Source', ''].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {filtered.map(i => {
              const needsModel = i.item_type === 'phone' && !(i.device_model || '').trim()
              return (
                <tr key={i.id} style={picked.has(i.item_key) ? { background: 'var(--surface2)' } : undefined}>
                  <td style={{ ...cell, textAlign: 'center' }}><input type="checkbox" checked={picked.has(i.item_key)} onChange={() => toggle(i.item_key)} /></td>
                  <td style={{ ...cell, maxWidth: 280 }}>{i.item_desc || <span style={{ color: 'var(--text3)' }}>{i.item_key}</span>}</td>
                  <td style={cell}>{i.sku || '—'}</td>
                  <td style={{ ...cell, fontSize: 11, color: 'var(--text3)', whiteSpace: 'nowrap' }}>{[i.department, i.category].filter(Boolean).join(' / ') || '—'}</td>
                  <td style={cell}>
                    <select style={{ ...sel, color: TYPE_COLOR[i.item_type] || undefined, fontWeight: 600 }} value={i.item_type} onChange={e => setItem(i.id, { item_type: e.target.value })}>
                      {TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </td>
                  <td style={cell}>
                    <EntityPicker
                      options={(() => { const o = models.map(m => ({ id: m, label: m })); if (i.device_model && !o.some(x => x.id === i.device_model)) o.unshift({ id: i.device_model, label: i.device_model }); return o })()}
                      value={i.device_model || null} allowCreate width={200}
                      onChange={v => setItem(i.id, { device_model: v || '' })} onCreate={v => setItem(i.id, { device_model: v })}
                      placeholder={needsModel ? 'required' : '—'} ariaLabel="Phone model" />
                  </td>
                  <td style={{ ...cell, fontSize: 11, color: 'var(--text3)' }}>{i.source || '—'}</td>
                  <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                    <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 8px' }} onClick={() => save(i)}>💾</button>{' '}
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 8px', color: '#dc2626' }} onClick={() => del(i)}>🗑</button>
                  </td>
                </tr>
              )
            })}
            {filtered.length === 0 && <tr><td colSpan={8} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>{loading ? 'Loading…' : ready ? 'No items. Upload a Product Catalog and seed, or load the Accessory Flags report to auto-populate.' : 'Run migration 041 first.'}</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
