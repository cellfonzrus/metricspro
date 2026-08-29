'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '8px 10px', borderBottom: '1px solid var(--border)' }
const CATS = ['appearance', 'facilities', 'security', 'supplies', 'accessories', 'general']

export default function VisitChecklistSettingsPage() {
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [draft, setDraft] = useState({ label: '', category: 'general', sort_order: 200 })
  const [busy, setBusy] = useState(false)

  function load() {
    setLoading(true)
    api('/api/v1/storevisit/checklist-items?include_inactive=true')
      .then(d => setItems(d || [])).catch(console.error).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  async function add() {
    if (!draft.label.trim()) { alert('Enter a label.'); return }
    setBusy(true)
    try {
      await api('/api/v1/storevisit/checklist-items', { method: 'POST', body: JSON.stringify(draft) })
      setDraft({ label: '', category: 'general', sort_order: 200 })
      load()
    } catch (e: any) { alert('Add failed: ' + (e?.message || e)) }
    finally { setBusy(false) }
  }

  async function patch(id: string, updates: any) {
    // optimistic
    setItems(list => list.map(it => it.id === id ? { ...it, ...updates } : it))
    try { await api(`/api/v1/storevisit/checklist-items/${id}`, { method: 'PATCH', body: JSON.stringify(updates) }) }
    catch (e: any) { alert('Update failed: ' + (e?.message || e)); load() }
  }

  async function toggleActive(it: any) {
    if (it.is_active) {
      if (!confirm(`Deactivate "${it.label}"? It will stop appearing on new visits (existing visits keep it).`)) return
      await api(`/api/v1/storevisit/checklist-items/${it.id}`, { method: 'DELETE' })
    } else {
      await api(`/api/v1/storevisit/checklist-items/${it.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: true }) })
    }
    load()
  }

  return (
    <div style={{ maxWidth: 800 }}>
      <Link href="/storeops/visits" style={{ fontSize: 13, color: 'var(--accent)' }}>← Store visits</Link>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 4px' }}>🧾 Visit Checklist</h1>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 18px' }}>
        Configure the inspection items district managers check on every store visit. Add, reorder, or deactivate items.
      </p>

      <div className="card" style={{ padding: 16, marginBottom: 18 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', marginBottom: 8 }}>Add an item</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input style={{ ...sel, flex: '3 1 240px' }} placeholder="Item label (e.g. Demo phones charged)" value={draft.label} onChange={e => setDraft(d => ({ ...d, label: e.target.value }))} />
          <select style={sel} value={draft.category} onChange={e => setDraft(d => ({ ...d, category: e.target.value }))}>
            {CATS.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <input type="number" style={{ ...sel, width: 90 }} value={draft.sort_order} onChange={e => setDraft(d => ({ ...d, sort_order: Number(e.target.value) || 200 }))} title="Sort order" />
          <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={busy} onClick={add}>＋ Add</button>
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="table-wrapper">
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Item', 'Category', 'Sort', 'Active', ''].map(h =>
                <th key={h} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {items.map(it => (
                <tr key={it.id} style={{ opacity: it.is_active ? 1 : 0.5 }}>
                  <td style={cell}>
                    <input style={{ ...sel, width: '100%', border: '1px solid transparent', background: 'transparent' }}
                      defaultValue={it.label} onBlur={e => { if (e.target.value !== it.label) patch(it.id, { label: e.target.value }) }} />
                  </td>
                  <td style={cell}>
                    <select style={sel} value={it.category || 'general'} onChange={e => patch(it.id, { category: e.target.value })}>
                      {CATS.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </td>
                  <td style={cell}>
                    <input type="number" style={{ ...sel, width: 70 }} defaultValue={it.sort_order}
                      onBlur={e => { const n = Number(e.target.value); if (n !== it.sort_order) patch(it.id, { sort_order: n }) }} />
                  </td>
                  <td style={cell}>{it.is_active ? '✅' : '—'}</td>
                  <td style={cell}>
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => toggleActive(it)}>
                      {it.is_active ? 'Deactivate' : 'Reactivate'}
                    </button>
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
