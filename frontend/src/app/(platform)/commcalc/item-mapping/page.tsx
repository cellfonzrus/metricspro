'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'

// The "SU sheet": each sales item (SKU / description) → type (accessory|phone|other|unclassified)
// + phone model. Seeds from the Product Catalog; auto-grows as new items appear in sales. item_type
// drives whether a sales line counts as an accessory on the Accessory Flags report.
type Item = {
  id: string; item_key: string; sku: string | null; item_desc: string | null
  department: string | null; category: string | null; item_type: string
  device_model: string | null; source: string | null
}
const TYPES = ['accessory', 'phone', 'other', 'unclassified']
const TYPE_COLOR: Record<string, string> = { accessory: '#b45309', phone: '#2563eb', other: '#6b7280', unclassified: '#b42318' }
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }

export default function ItemMappingPage() {
  const [items, setItems] = useState<Item[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [search, setSearch] = useState('')
  const [typeF, setTypeF] = useState('')
  const [loading, setLoading] = useState(true)
  const [ready, setReady] = useState(true)
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    api('/api/v1/commcalc/item-mapping').then((r: any) => {
      setItems(r.items || []); setCounts(r.counts || {}); setReady(r.ready !== false)
      if (r.ready === false) setMsg('Run migration 041 to enable item mapping.')
    }).catch((e: any) => setMsg('Load failed: ' + (e?.message || e))).finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const setItem = (id: string, patch: Partial<Item>) => setItems(its => its.map(i => i.id === id ? { ...i, ...patch } : i))

  async function save(i: Item) {
    setMsg('')
    try {
      await api('/api/v1/commcalc/item-mapping', { method: 'POST', body: JSON.stringify({
        item_key: i.item_key, sku: i.sku, item_desc: i.item_desc,
        item_type: i.item_type, device_model: i.device_model,
        department: i.department, category: i.category }) })
      setMsg(`Saved ${i.item_desc || i.sku || i.item_key}`)
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
    try { const r = await api('/api/v1/commcalc/item-mapping/seed-from-catalog', { method: 'POST', body: '{}' }); setMsg(`Seeded ${r.seeded} of ${r.catalog_rows} catalog items.`); load() }
    catch (e: any) { setMsg('Seed failed: ' + (e?.message || e)) }
  }

  const filtered = items.filter(i =>
    (!typeF || i.item_type === typeF) &&
    (!search || `${i.item_desc || ''} ${i.sku || ''} ${i.device_model || ''}`.toLowerCase().includes(search.toLowerCase())))

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧩 Item / Model Mapping</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Classify each item (accessory vs phone) and set its phone model. Drives the Accessory Flags report. New items seen in sales land here as "unclassified".
        </p>
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

      <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Item', 'SKU', 'Dept / Cat', 'Type', 'Phone model', 'Source', ''].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {filtered.map(i => (
              <tr key={i.id}>
                <td style={{ ...cell, maxWidth: 280 }}>{i.item_desc || <span style={{ color: 'var(--text3)' }}>{i.item_key}</span>}</td>
                <td style={cell}>{i.sku || '—'}</td>
                <td style={{ ...cell, fontSize: 11, color: 'var(--text3)', whiteSpace: 'nowrap' }}>{[i.department, i.category].filter(Boolean).join(' / ') || '—'}</td>
                <td style={cell}>
                  <select style={{ ...sel, color: TYPE_COLOR[i.item_type] || undefined, fontWeight: 600 }} value={i.item_type} onChange={e => setItem(i.id, { item_type: e.target.value })}>
                    {TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </td>
                <td style={cell}><input style={{ ...sel, width: 180 }} value={i.device_model || ''} placeholder="—" onChange={e => setItem(i.id, { device_model: e.target.value })} /></td>
                <td style={{ ...cell, fontSize: 11, color: 'var(--text3)' }}>{i.source || '—'}</td>
                <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                  <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 8px' }} onClick={() => save(i)}>💾</button>{' '}
                  <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 8px', color: '#dc2626' }} onClick={() => del(i)}>🗑</button>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>{loading ? 'Loading…' : ready ? 'No items. Upload a Product Catalog and seed, or load the Accessory Flags report to auto-populate.' : 'Run migration 041 first.'}</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
