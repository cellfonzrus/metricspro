'use client'
// POS — Customer Special Order: HQ MANAGEMENT (catalog + vendor connectors). Owner directive 2026-08-19.
//
// HQ-ONLY. Every call on this page is gated server-side by the `pos_special_order_admin` permission,
// which store roles do not hold — that permission is exactly what keeps the vendor (Amazon) hidden
// from stores. This is the ONE surface where the vendor linkage is visible.
//
// Two tabs:
//   • Catalog  — the special-order items customers can buy, each with its HIDDEN vendor linkage
//                (vendor SKU / URL / cost). The store-facing picker never sees the linkage.
//   • Vendors  — the plug-and-play connector registry: 'manual' (HQ fulfills), 'outbound_api' (we call
//                their API), or 'inbound_api' (they call ours with a token shown once here).
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'

interface VendorLink {
  vendor: string | null; vendor_sku: string | null; vendor_url: string | null
  vendor_cost: number | null; lead_time_days: number | null; notes: string | null; is_active?: boolean
}
interface CatalogRow {
  id: string; product_code: number | string | null; short_name: string; full_name: string | null
  system_category: string | null; cost: number; retail_price: number; is_taxable: boolean
  is_active: boolean; vendor: VendorLink | null
}
interface SysCat { id: string; name: string; is_active: boolean }
interface Connector {
  id: string; vendor_key: string; display_name: string | null; integration_mode: string
  api_base_url: string | null; credential_ref: string | null; is_active: boolean
  config: Record<string, any> | null
}

const MODES = [
  { value: 'manual', label: 'Manual — HQ fulfills from the queue' },
  { value: 'outbound_api', label: 'Outbound API — we call their API' },
  { value: 'inbound_api', label: 'Inbound API — they call our API' },
]

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', width: '100%', outline: 'none', boxSizing: 'border-box' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const panel: React.CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }
const cell: React.CSSProperties = { padding: '7px 12px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
function money(n: number | null | undefined) { return `$${(Number(n) || 0).toFixed(2)}` }

const emptyItem = {
  short_name: '', full_name: '', system_category: 'Accessory', retail_price: '', is_taxable: true, is_active: true,
  vendor: 'amazon', vendor_sku: '', vendor_url: '', vendor_cost: '', lead_time_days: '', notes: '',
}
const emptyConnector = {
  vendor_key: '', display_name: '', integration_mode: 'manual', api_base_url: '', credential_ref: '',
  inbound_token: '', config: '', is_active: true,
}

export default function SpecialOrderManagePage() {
  const [tab, setTab] = useState<'catalog' | 'vendors'>('catalog')
  const [denied, setDenied] = useState(false)
  const [msg, setMsg] = useState('')

  // Catalog
  const [items, setItems] = useState<CatalogRow[]>([])
  const [sysCats, setSysCats] = useState<SysCat[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [itemForm, setItemForm] = useState<any>({ ...emptyItem })
  const [editItemId, setEditItemId] = useState<string | null>(null)
  const [showItemForm, setShowItemForm] = useState(false)
  const [savingItem, setSavingItem] = useState(false)

  // Connectors
  const [connectors, setConnectors] = useState<Connector[]>([])
  const [connForm, setConnForm] = useState<any>({ ...emptyConnector })
  const [editConnId, setEditConnId] = useState<string | null>(null)
  const [showConnForm, setShowConnForm] = useState(false)
  const [savingConn, setSavingConn] = useState(false)
  const [issuedToken, setIssuedToken] = useState('')

  async function loadCatalog() {
    setLoading(true); setMsg('')
    try {
      const p = new URLSearchParams(); if (search.trim()) p.set('search', search.trim())
      const r = await api(`/api/v1/pos/special-orders/catalog/admin?${p}`)
      setItems(r.items || []); setDenied(false)
    } catch (e: any) {
      if (/403|not allow/i.test(e?.message || '')) setDenied(true)
      else setMsg('Failed to load catalog: ' + (e?.message || e))
    }
    try { const s = await api('/api/v1/pos/system-categories'); setSysCats(s.system_categories || []) } catch { /* optional */ }
    setLoading(false)
  }
  async function loadConnectors() {
    try { const r = await api('/api/v1/pos/vendor-connectors'); setConnectors(r.connectors || []) }
    catch (e: any) { if (/403|not allow/i.test(e?.message || '')) setDenied(true) }
  }
  useEffect(() => { loadCatalog(); loadConnectors() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  function openNewItem() { setItemForm({ ...emptyItem }); setEditItemId(null); setShowItemForm(true) }
  function openEditItem(r: CatalogRow) {
    setItemForm({
      short_name: r.short_name || '', full_name: r.full_name || '',
      system_category: r.system_category || 'Accessory', retail_price: String(r.retail_price ?? ''),
      is_taxable: !!r.is_taxable, is_active: !!r.is_active,
      vendor: r.vendor?.vendor || 'amazon', vendor_sku: r.vendor?.vendor_sku || '',
      vendor_url: r.vendor?.vendor_url || '', vendor_cost: r.vendor?.vendor_cost != null ? String(r.vendor.vendor_cost) : '',
      lead_time_days: r.vendor?.lead_time_days != null ? String(r.vendor.lead_time_days) : '',
      notes: r.vendor?.notes || '',
    })
    setEditItemId(r.id); setShowItemForm(true)
  }
  async function saveItem() {
    setSavingItem(true); setMsg('')
    try {
      const body: any = {
        short_name: itemForm.short_name, full_name: itemForm.full_name || null,
        system_category: itemForm.system_category, retail_price: Number(itemForm.retail_price) || 0,
        is_taxable: !!itemForm.is_taxable, is_active: !!itemForm.is_active,
        // vendor linkage (HQ-only) — sent alongside; the backend upserts special_order_vendor
        vendor: itemForm.vendor || 'amazon', vendor_sku: itemForm.vendor_sku || null,
        vendor_url: itemForm.vendor_url || null,
        vendor_cost: itemForm.vendor_cost !== '' ? Number(itemForm.vendor_cost) : null,
        lead_time_days: itemForm.lead_time_days !== '' ? Number(itemForm.lead_time_days) : null,
        notes: itemForm.notes || null,
      }
      if (editItemId) await api(`/api/v1/pos/special-orders/catalog/${editItemId}`, { method: 'PATCH', body: JSON.stringify(body) })
      else await api('/api/v1/pos/special-orders/catalog', { method: 'POST', body: JSON.stringify(body) })
      setShowItemForm(false); await loadCatalog()
    } catch (e: any) { alert('Save failed: ' + (e?.message || e)) }
    setSavingItem(false)
  }

  function openNewConn() { setConnForm({ ...emptyConnector }); setEditConnId(null); setIssuedToken(''); setShowConnForm(true) }
  function openEditConn(c: Connector) {
    setConnForm({
      vendor_key: c.vendor_key, display_name: c.display_name || '', integration_mode: c.integration_mode,
      api_base_url: c.api_base_url || '', credential_ref: c.credential_ref || '', inbound_token: '',
      config: c.config ? JSON.stringify(c.config) : '', is_active: c.is_active,
    })
    setEditConnId(c.id); setIssuedToken(''); setShowConnForm(true)
  }
  async function saveConn() {
    setSavingConn(true); setMsg('')
    try {
      const body: any = {
        vendor_key: connForm.vendor_key.trim(), display_name: connForm.display_name || null,
        integration_mode: connForm.integration_mode, is_active: !!connForm.is_active,
      }
      if (connForm.integration_mode === 'outbound_api') {
        body.api_base_url = connForm.api_base_url || null
        body.credential_ref = connForm.credential_ref || null
      }
      if (connForm.inbound_token.trim()) body.inbound_token = connForm.inbound_token.trim()
      if (connForm.config.trim()) {
        try { body.config = JSON.parse(connForm.config) } catch { alert('Config must be valid JSON.'); setSavingConn(false); return }
      }
      const r = editConnId
        ? await api(`/api/v1/pos/vendor-connectors/${editConnId}`, { method: 'PATCH', body: JSON.stringify(body) })
        : await api('/api/v1/pos/vendor-connectors', { method: 'POST', body: JSON.stringify(body) })
      if (r.inbound_token) { setIssuedToken(r.inbound_token) } else { setShowConnForm(false) }
      await loadConnectors()
    } catch (e: any) { alert('Save failed: ' + (e?.message || e)) }
    setSavingConn(false)
  }

  if (denied) {
    return (
      <div style={{ ...panel, maxWidth: 560, margin: '40px auto', textAlign: 'center' }}>
        <div style={{ fontSize: 28, marginBottom: 8 }}>🔒</div>
        <h2 style={{ fontSize: 18, margin: '0 0 6px' }}>HQ access required</h2>
        <p style={{ color: 'var(--text2)', fontSize: 14 }}>
          Managing the special-order catalog and vendors needs the <b>pos_special_order_admin</b> permission.
          Ask an administrator to grant it in Roles &amp; Access.
        </p>
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🗂️ Special Orders — HQ Management</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Curate the catalog and the vendors behind it. HQ-only — not visible to stores.</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <a href="/pos/special-orders" className="btn btn-secondary" style={{ textDecoration: 'none' }}>Store view →</a>
          <button className={tab === 'catalog' ? 'btn btn-primary' : 'btn btn-secondary'} onClick={() => setTab('catalog')}>Catalog</button>
          <button className={tab === 'vendors' ? 'btn btn-primary' : 'btn btn-secondary'} onClick={() => setTab('vendors')}>Vendors</button>
        </div>
      </div>

      {msg && <div style={{ ...panel, marginBottom: 12, borderColor: '#dc2626', color: '#dc2626', fontSize: 13 }}>{msg}</div>}

      {tab === 'catalog' && (
        <div>
          <div style={{ ...panel, marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <input value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => e.key === 'Enter' && loadCatalog()}
              placeholder="Search catalog…" style={{ ...input, flex: 1, minWidth: 180 }} />
            <button className="btn btn-secondary" onClick={loadCatalog}>Search</button>
            <button className="btn btn-primary" onClick={openNewItem}>+ New item</button>
          </div>
          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
          ) : (
            <div className="table-wrapper" style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900, fontSize: 13 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Item', 'Category', 'Retail', 'Vendor', 'Vendor SKU', 'Vendor cost', 'Lead', 'Active', ''].map(h =>
                    <th key={h} style={{ textAlign: 'left', padding: 8, fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {items.map(r => (
                    <tr key={r.id} style={{ opacity: r.is_active ? 1 : 0.55 }}>
                      <td style={{ ...cell, fontWeight: 500 }}>{r.short_name}{r.full_name ? <span style={{ color: 'var(--text3)', fontWeight: 400 }}> — {r.full_name}</span> : ''}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{r.system_category || '—'}</td>
                      <td style={cell}>{money(r.retail_price)}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{r.vendor?.vendor || '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{r.vendor?.vendor_sku || '—'}</td>
                      <td style={cell}>{r.vendor?.vendor_cost != null ? money(r.vendor.vendor_cost) : '—'}</td>
                      <td style={{ ...cell, color: 'var(--text2)' }}>{r.vendor?.lead_time_days != null ? `${r.vendor.lead_time_days}d` : '—'}</td>
                      <td style={cell}>{r.is_active ? '✓' : '—'}</td>
                      <td style={cell}><button className="btn btn-secondary" style={{ padding: '3px 10px', fontSize: 12 }} onClick={() => openEditItem(r)}>Edit</button></td>
                    </tr>
                  ))}
                  {items.length === 0 && <tr><td colSpan={9} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>No special-order items yet. Click &ldquo;+ New item&rdquo;.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === 'vendors' && (
        <div>
          <div style={{ ...panel, marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
            <span style={{ fontSize: 13, color: 'var(--text2)' }}>Dropship vendors behind the catalog. Adding one is a data change — no code needed for manual/inbound vendors.</span>
            <button className="btn btn-primary" onClick={openNewConn}>+ New vendor</button>
          </div>
          <div className="table-wrapper" style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760, fontSize: 13 }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Vendor key', 'Display name', 'Mode', 'API base', 'Credential ref', 'Active', ''].map(h =>
                  <th key={h} style={{ textAlign: 'left', padding: 8, fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {connectors.map(c => (
                  <tr key={c.id} style={{ opacity: c.is_active ? 1 : 0.55 }}>
                    <td style={{ ...cell, fontWeight: 600 }}>{c.vendor_key}</td>
                    <td style={{ ...cell, color: 'var(--text2)' }}>{c.display_name || '—'}</td>
                    <td style={cell}>{MODES.find(m => m.value === c.integration_mode)?.value || c.integration_mode}</td>
                    <td style={{ ...cell, color: 'var(--text2)' }}>{c.api_base_url || '—'}</td>
                    <td style={{ ...cell, color: 'var(--text2)' }}>{c.credential_ref || '—'}</td>
                    <td style={cell}>{c.is_active ? '✓' : '—'}</td>
                    <td style={cell}><button className="btn btn-secondary" style={{ padding: '3px 10px', fontSize: 12 }} onClick={() => openEditConn(c)}>Edit</button></td>
                  </tr>
                ))}
                {connectors.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>No vendors yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Item modal */}
      {showItemForm && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 620, maxHeight: '92vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>{editItemId ? 'Edit special-order item' : 'New special-order item'}</b>
              <button onClick={() => setShowItemForm(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>
            <div style={{ padding: 20, overflowY: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div><label style={label}>Customer-facing name *</label><input value={itemForm.short_name} onChange={e => setItemForm((f: any) => ({ ...f, short_name: e.target.value }))} style={input} /></div>
                <div><label style={label}>Full name</label><input value={itemForm.full_name} onChange={e => setItemForm((f: any) => ({ ...f, full_name: e.target.value }))} style={input} /></div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div><label style={label}>Category</label>
                  <select value={itemForm.system_category} onChange={e => setItemForm((f: any) => ({ ...f, system_category: e.target.value }))} style={input}>
                    {(sysCats.filter(s => s.is_active).length ? sysCats.filter(s => s.is_active) : [{ id: '1', name: 'Accessory', is_active: true }]).map(s => <option key={s.id} value={s.name}>{s.name}</option>)}
                  </select>
                </div>
                <div><label style={label}>Retail price</label><input type="number" value={itemForm.retail_price} onChange={e => setItemForm((f: any) => ({ ...f, retail_price: e.target.value }))} style={input} placeholder="0.00" /></div>
              </div>
              <div style={{ display: 'flex', gap: 20 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}><input type="checkbox" checked={itemForm.is_taxable} onChange={e => setItemForm((f: any) => ({ ...f, is_taxable: e.target.checked }))} /> Taxable</label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}><input type="checkbox" checked={itemForm.is_active} onChange={e => setItemForm((f: any) => ({ ...f, is_active: e.target.checked }))} /> Active</label>
              </div>
              <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>🔒 Vendor linkage (HQ-only — never shown to stores)</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  <div><label style={label}>Vendor key</label><input value={itemForm.vendor} onChange={e => setItemForm((f: any) => ({ ...f, vendor: e.target.value }))} style={input} placeholder="amazon" /></div>
                  <div><label style={label}>Vendor SKU / ASIN</label><input value={itemForm.vendor_sku} onChange={e => setItemForm((f: any) => ({ ...f, vendor_sku: e.target.value }))} style={input} /></div>
                </div>
                <div style={{ marginTop: 10 }}><label style={label}>Vendor URL</label><input value={itemForm.vendor_url} onChange={e => setItemForm((f: any) => ({ ...f, vendor_url: e.target.value }))} style={input} /></div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 10 }}>
                  <div><label style={label}>Vendor cost (COGS basis)</label><input type="number" value={itemForm.vendor_cost} onChange={e => setItemForm((f: any) => ({ ...f, vendor_cost: e.target.value }))} style={input} placeholder="0.00" /></div>
                  <div><label style={label}>Lead time (days)</label><input type="number" value={itemForm.lead_time_days} onChange={e => setItemForm((f: any) => ({ ...f, lead_time_days: e.target.value }))} style={input} /></div>
                </div>
                <div style={{ marginTop: 10 }}><label style={label}>Notes</label><input value={itemForm.notes} onChange={e => setItemForm((f: any) => ({ ...f, notes: e.target.value }))} style={input} /></div>
              </div>
            </div>
            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setShowItemForm(false)}>Cancel</button>
              <button className="btn btn-primary" disabled={savingItem || !itemForm.short_name} onClick={saveItem}>{savingItem ? 'Saving…' : 'Save'}</button>
            </div>
          </div>
        </div>
      )}

      {/* Connector modal */}
      {showConnForm && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 560, maxHeight: '92vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>{editConnId ? 'Edit vendor connector' : 'New vendor connector'}</b>
              <button onClick={() => setShowConnForm(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>
            <div style={{ padding: 20, overflowY: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: 12 }}>
              {issuedToken ? (
                <div style={{ ...panel, borderColor: 'var(--green)' }}>
                  <div style={{ fontWeight: 700, marginBottom: 6 }}>🔑 Access token — copy it now</div>
                  <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>Hand this to the vendor. It is shown ONCE and cannot be retrieved again (only its hash is stored).</div>
                  <code style={{ display: 'block', wordBreak: 'break-all', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: 10, fontSize: 12 }}>{issuedToken}</code>
                </div>
              ) : (
                <>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    <div><label style={label}>Vendor key *</label><input value={connForm.vendor_key} onChange={e => setConnForm((f: any) => ({ ...f, vendor_key: e.target.value }))} style={input} placeholder="amazon" disabled={!!editConnId} /></div>
                    <div><label style={label}>Display name</label><input value={connForm.display_name} onChange={e => setConnForm((f: any) => ({ ...f, display_name: e.target.value }))} style={input} /></div>
                  </div>
                  <div><label style={label}>Integration mode</label>
                    <select value={connForm.integration_mode} onChange={e => setConnForm((f: any) => ({ ...f, integration_mode: e.target.value }))} style={input}>
                      {MODES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                    </select>
                  </div>
                  {connForm.integration_mode === 'outbound_api' && (
                    <>
                      <div><label style={label}>API base URL</label><input value={connForm.api_base_url} onChange={e => setConnForm((f: any) => ({ ...f, api_base_url: e.target.value }))} style={input} placeholder="https://api.vendor.com" /></div>
                      <div><label style={label}>Credential ref (env/secret NAME — not the key)</label><input value={connForm.credential_ref} onChange={e => setConnForm((f: any) => ({ ...f, credential_ref: e.target.value }))} style={input} placeholder="VENDOR_ACME_API_KEY" /></div>
                    </>
                  )}
                  {connForm.integration_mode === 'inbound_api' && (
                    <div><label style={label}>Access token {editConnId ? '(leave blank to keep, fill to rotate)' : '(shown once after save)'}</label>
                      <input value={connForm.inbound_token} onChange={e => setConnForm((f: any) => ({ ...f, inbound_token: e.target.value }))} style={input} placeholder="a long random secret" />
                    </div>
                  )}
                  <div><label style={label}>Config (JSON, optional)</label><input value={connForm.config} onChange={e => setConnForm((f: any) => ({ ...f, config: e.target.value }))} style={input} placeholder='{"place_path":"/orders"}' /></div>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}><input type="checkbox" checked={connForm.is_active} onChange={e => setConnForm((f: any) => ({ ...f, is_active: e.target.checked }))} /> Active</label>
                </>
              )}
            </div>
            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              {issuedToken
                ? <button className="btn btn-primary" onClick={() => { setIssuedToken(''); setShowConnForm(false) }}>Done</button>
                : <>
                    <button className="btn btn-secondary" onClick={() => setShowConnForm(false)}>Cancel</button>
                    <button className="btn btn-primary" disabled={savingConn || !connForm.vendor_key.trim()} onClick={saveConn}>{savingConn ? 'Saving…' : 'Save'}</button>
                  </>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
