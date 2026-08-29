'use client'
// POS module — Phase 0: Products & Services catalog (ported from the standalone pos-system app;
// data access rewired from direct Supabase to the FastAPI /pos router, mig 724).
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'

interface Product {
  id: string; product_code: number; upc: string | null
  short_name: string; full_name: string | null
  department_id: string | null; category_id: string | null
  department_name?: string | null; category_name?: string | null
  system_category: string | null; inventory_type: string; manufacturer: string | null
  cost: number; retail_price: number; msrp: number | null
  is_taxable: boolean; calculate_as_profit: boolean; body_style: string | null
  is_active: boolean; end_of_life: boolean; created_at: string
}
interface Department { id: string; short_name: string; full_name: string | null }
interface Category { id: string; name: string; department_id: string | null }
// Tenant-configurable since migration 745. The four originals are seeded as builtins (renameable
// and switch-off-able, never deletable) — a tenant selling tablets can now add "Tablet" instead of
// dropping everything into the catch-all "Regular", which is where 96 of 118 products had landed.
interface SysCat { id: string; name: string; sort_order: number; is_active: boolean; is_builtin: boolean }
const BODY_STYLES = ['Not Set', 'Bar', 'Flip', 'Slider', 'Tablet']

const emptyForm = {
  upc: '', short_name: '', full_name: '', department_id: '', category_id: '',
  system_category: 'Accessory', inventory_type: 'standard', manufacturer: '',
  cost: 0, retail_price: 0, is_taxable: true, calculate_as_profit: true,
  body_style: 'Not Set', is_active: true, end_of_life: false,
}

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', width: '100%', outline: 'none' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const cell: React.CSSProperties = { padding: '7px 12px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const panel: React.CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }

export default function PosProductsPage() {
  const [products, setProducts] = useState<Product[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [search, setSearch] = useState('')
  const [filterDept, setFilterDept] = useState('')
  const [filterSysCat, setFilterSysCat] = useState('')
  const [activeOnly, setActiveOnly] = useState(true)
  const [selected, setSelected] = useState<Product | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editMode, setEditMode] = useState(false)
  const [formData, setFormData] = useState({ ...emptyForm })
  const [formTab, setFormTab] = useState<'Details' | 'Summary'>('Details')
  const [saving, setSaving] = useState(false)
  const [showDeptCat, setShowDeptCat] = useState(false)
  const [newDeptName, setNewDeptName] = useState('')
  const [newCatName, setNewCatName] = useState('')
  const [newCatDept, setNewCatDept] = useState('')
  const [deptCatBusy, setDeptCatBusy] = useState(false)
  const [sysCats, setSysCats] = useState<SysCat[]>([])
  const [newSysCatName, setNewSysCatName] = useState('')

  const filteredCats = formData.department_id
    ? categories.filter(c => c.department_id === formData.department_id)
    : categories

  async function loadCatalog() {
    // The two reads are independent — kick both off together (parallel, not a waterfall). system-categories
    // keeps its own try/catch so its seed-on-first-read fallback behaviour is unchanged.
    const pCatalog = api('/api/v1/pos/catalog')
    const pSys = api('/api/v1/pos/system-categories')
    const c = await pCatalog
    setDepartments(c.departments || []); setCategories(c.categories || [])
    // Separate call: the endpoint SEEDS the four builtins on first read, so a tenant that has
    // never opened this page still gets a working dropdown rather than an empty one.
    try {
      const s = await pSys
      setSysCats(s.system_categories || [])
    } catch { setSysCats([]) }
  }

  // The picker only ever offers ACTIVE ones; a switched-off category stays on its existing
  // products (nothing is silently recategorised) but can't be chosen again.
  const activeSysCats = sysCats.filter(s => s.is_active)

  async function addSystemCategory() {
    if (!newSysCatName.trim()) return
    setDeptCatBusy(true); setMsg('')
    try { await api('/api/v1/pos/system-categories', { method: 'POST', body: JSON.stringify({ name: newSysCatName.trim() }) }); setNewSysCatName(''); await loadCatalog() }
    catch (err: any) { setMsg('Failed to add system category: ' + (err?.message || err)) }
    finally { setDeptCatBusy(false) }
  }

  async function renameSystemCategory(c: SysCat) {
    const name = prompt(`Rename system category:\n(every product using "${c.name}" moves with it)`, c.name)
    if (!name?.trim() || name.trim() === c.name) return
    setDeptCatBusy(true); setMsg('')
    try {
      const r = await api(`/api/v1/pos/system-categories/${c.id}`, { method: 'PATCH', body: JSON.stringify({ name: name.trim() }) })
      await loadCatalog(); await loadProducts()
      if (r?.products_moved) setMsg(`Renamed — ${r.products_moved} product(s) moved to "${name.trim()}".`)
    } catch (err: any) { setMsg('Failed to rename: ' + (err?.message || err)) }
    finally { setDeptCatBusy(false) }
  }

  async function toggleSystemCategory(c: SysCat) {
    setDeptCatBusy(true); setMsg('')
    try { await api(`/api/v1/pos/system-categories/${c.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !c.is_active }) }); await loadCatalog() }
    catch (err: any) { setMsg('Failed to update: ' + (err?.message || err)) }
    finally { setDeptCatBusy(false) }
  }

  async function loadProducts(q = { search, filterDept, filterSysCat, activeOnly }) {
    setLoading(true); setMsg('')
    try {
      const params = new URLSearchParams()
      if (q.search.trim()) params.set('search', q.search.trim())
      if (q.filterDept) params.set('department_id', q.filterDept)
      if (q.filterSysCat) params.set('system_category', q.filterSysCat)
      params.set('active_only', String(q.activeOnly))
      const r = await api(`/api/v1/pos/products?${params}`)
      setProducts(r.products || [])
    } catch (err: any) { setMsg('Load failed: ' + (err?.message || err)) }
    setLoading(false)
  }

  useEffect(() => { loadCatalog().catch(() => {}); loadProducts() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  async function saveProduct() {
    setSaving(true); setMsg('')
    const payload = { ...formData, body_style: formData.body_style === 'Not Set' ? null : formData.body_style }
    try {
      if (editMode && selected) await api(`/api/v1/pos/products/${selected.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
      else await api('/api/v1/pos/products', { method: 'POST', body: JSON.stringify(payload) })
      setShowForm(false); setEditMode(false)
      await loadProducts()
    } catch (err: any) { alert('Failed to save product: ' + (err?.message || err)) }
    setSaving(false)
  }

  async function deactivateSelected() {
    if (!selected || !confirm(`Deactivate ${selected.short_name}?`)) return
    try {
      await api(`/api/v1/pos/products/${selected.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: false }) })
      setSelected(null); await loadProducts()
    } catch (err: any) { alert('Failed: ' + (err?.message || err)) }
  }

  async function addDepartment() {
    if (!newDeptName.trim() || deptCatBusy) return
    setDeptCatBusy(true); setMsg('')
    try { await api('/api/v1/pos/departments', { method: 'POST', body: JSON.stringify({ short_name: newDeptName.trim() }) }); setNewDeptName(''); await loadCatalog() }
    catch (err: any) { setMsg('Failed to create department: ' + (err?.message || err)) }
    setDeptCatBusy(false)
  }

  async function renameDepartment(d: Department) {
    const name = prompt('Rename department:', d.short_name)
    if (!name || !name.trim() || name.trim() === d.short_name) return
    try { await api(`/api/v1/pos/departments/${d.id}`, { method: 'PATCH', body: JSON.stringify({ short_name: name.trim(), full_name: name.trim() }) }); await loadCatalog() }
    catch (err: any) { setMsg('Failed to rename: ' + (err?.message || err)) }
  }

  async function addCategory() {
    if (!newCatName.trim() || deptCatBusy) return
    setDeptCatBusy(true); setMsg('')
    try { await api('/api/v1/pos/categories', { method: 'POST', body: JSON.stringify({ name: newCatName.trim(), department_id: newCatDept || null }) }); setNewCatName(''); await loadCatalog() }
    catch (err: any) { setMsg('Failed to create category: ' + (err?.message || err)) }
    setDeptCatBusy(false)
  }

  async function renameCategory(c: Category) {
    const name = prompt('Rename category:', c.name)
    if (!name || !name.trim() || name.trim() === c.name) return
    try { await api(`/api/v1/pos/categories/${c.id}`, { method: 'PATCH', body: JSON.stringify({ name: name.trim() }) }); await loadCatalog() }
    catch (err: any) { setMsg('Failed to rename: ' + (err?.message || err)) }
  }

  function openNew() { setFormData({ ...emptyForm }); setFormTab('Details'); setEditMode(false); setShowForm(true) }
  function openEdit() {
    if (!selected) return
    setFormData({
      upc: selected.upc || '', short_name: selected.short_name || '', full_name: selected.full_name || '',
      department_id: selected.department_id || '', category_id: selected.category_id || '',
      system_category: selected.system_category || 'Accessory', inventory_type: selected.inventory_type || 'standard',
      manufacturer: selected.manufacturer || '', cost: selected.cost || 0, retail_price: selected.retail_price || 0,
      is_taxable: selected.is_taxable, calculate_as_profit: selected.calculate_as_profit,
      body_style: selected.body_style || 'Not Set', is_active: selected.is_active, end_of_life: selected.end_of_life,
    })
    setFormTab('Details'); setEditMode(true); setShowForm(true)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏷️ Products &amp; Services</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            POS catalog · {products.length} products{selected ? ` · selected: ${selected.short_name} (#${selected.product_code})` : ''}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {msg && <span style={{ fontSize: 13, color: '#dc2626' }}>{msg}</span>}
          <button className="btn btn-primary" onClick={openNew}>+ New</button>
          {selected && <button className="btn btn-secondary" onClick={openEdit}>View/Edit</button>}
          {selected && <button className="btn btn-secondary" style={{ color: '#dc2626' }} onClick={deactivateSelected}>Deactivate</button>}
          <button className="btn btn-secondary" onClick={() => setShowDeptCat(true)}>Depts / Categories</button>
        </div>
      </div>

      {/* Search & filters */}
      <div style={{ ...panel, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <input value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => e.key === 'Enter' && loadProducts()}
          placeholder="Search by name, UPC, description…" style={{ ...input, flex: 1, minWidth: 200 }} />
        <select value={filterDept} onChange={e => setFilterDept(e.target.value)} style={{ ...input, width: 170 }}>
          <option value="">Department: any</option>
          {departments.map(d => <option key={d.id} value={d.id}>{d.short_name}</option>)}
        </select>
        <select value={filterSysCat} onChange={e => setFilterSysCat(e.target.value)} style={{ ...input, width: 190 }}>
          <option value="">System category: any</option>
          {sysCats.map(c => <option key={c.id}>{c.name}</option>)}
        </select>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap' }}>
          <input type="checkbox" checked={activeOnly} onChange={e => setActiveOnly(e.target.checked)} />
          Active only
        </label>
        <button className="btn btn-primary" onClick={() => loadProducts()}>Search</button>
        <button className="btn btn-secondary" onClick={() => { setSearch(''); setFilterDept(''); setFilterSysCat(''); loadProducts({ search: '', filterDept: '', filterSysCat: '', activeOnly }) }}>Clear</button>
      </div>

      {/* Table */}
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="table-wrapper" style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1000, fontSize: 13 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Product #', 'UPC', 'Short Description', 'Department', 'Category', 'System Category', 'Retail', 'Cost', 'Taxable', 'Type', 'Active'].map(h =>
                <th key={h} style={{ textAlign: 'left', padding: 8, fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {products.map(p => (
                <tr key={p.id} onClick={() => setSelected(selected?.id === p.id ? null : p)}
                  style={{ cursor: 'pointer', background: selected?.id === p.id ? 'var(--surface2)' : 'transparent', opacity: p.is_active ? 1 : 0.55 }}>
                  <td style={{ ...cell, fontWeight: 600 }}>{p.product_code}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{p.upc || ''}</td>
                  <td style={{ ...cell, fontWeight: 500 }}>{p.short_name}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{p.department_name || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{p.category_name || ''}</td>
                  <td style={cell}>{p.system_category || ''}</td>
                  <td style={cell}>${Number(p.retail_price || 0).toFixed(2)}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>${Number(p.cost || 0).toFixed(2)}</td>
                  <td style={cell}>{p.is_taxable ? 'Yes' : 'No'}</td>
                  <td style={cell}>{p.inventory_type === 'serial' ? '📱 Serial' : '📦 Standard'}</td>
                  <td style={cell}>{p.is_active ? 'Yes' : 'No'}</td>
                </tr>
              ))}
              {products.length === 0 && (
                <tr><td colSpan={11} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                  No products found. Click “+ New” to add your first product.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Selected detail strip */}
      {selected && (
        <div style={{ ...panel, marginTop: 10, display: 'flex', gap: 24, flexWrap: 'wrap', fontSize: 13 }}>
          <span><span style={{ color: 'var(--text2)' }}>Product: </span><b>{selected.short_name}</b></span>
          <span><span style={{ color: 'var(--text2)' }}>Code: </span>#{selected.product_code}</span>
          <span><span style={{ color: 'var(--text2)' }}>Type: </span>{selected.inventory_type}</span>
          <span><span style={{ color: 'var(--text2)' }}>Cost: </span>${Number(selected.cost || 0).toFixed(2)}</span>
          <span><span style={{ color: 'var(--text2)' }}>Retail: </span>${Number(selected.retail_price || 0).toFixed(2)}</span>
          <span><span style={{ color: 'var(--text2)' }}>Margin: </span>
            {selected.cost > 0 && selected.retail_price > 0 ? `${(((selected.retail_price - selected.cost) / selected.retail_price) * 100).toFixed(1)}%` : 'N/A'}
          </span>
        </div>
      )}

      {/* Product form modal */}
      {showForm && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 780, maxHeight: '92vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>{editMode ? `Edit Product — ${selected?.short_name}` : 'New Product / Service'}</b>
              <button onClick={() => setShowForm(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>
            <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
              {(['Details', 'Summary'] as const).map(tab => (
                <button key={tab} onClick={() => setFormTab(tab)}
                  style={{ padding: '9px 16px', fontSize: 12, fontWeight: formTab === tab ? 700 : 400, color: formTab === tab ? 'var(--text)' : 'var(--text2)', background: formTab === tab ? 'var(--surface)' : 'transparent', border: 'none', borderBottom: formTab === tab ? '2px solid var(--accent, #3498db)' : '2px solid transparent', cursor: 'pointer' }}>
                  {tab}
                </button>
              ))}
            </div>

            <div style={{ padding: 20, overflowY: 'auto', flex: 1 }}>
              {formTab === 'Details' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div>
                    <label style={label}>Short Name *</label>
                    <input value={formData.short_name} onChange={e => setFormData(f => ({ ...f, short_name: e.target.value }))} style={input} placeholder="e.g. iPhone 15 Pro" />
                  </div>
                  <div>
                    <label style={label}>Full Name</label>
                    <input value={formData.full_name} onChange={e => setFormData(f => ({ ...f, full_name: e.target.value }))} style={input} placeholder="Full description" />
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    <div>
                      <label style={label}>UPC / Barcode</label>
                      <input value={formData.upc} onChange={e => setFormData(f => ({ ...f, upc: e.target.value }))} style={input} placeholder="Scan or type UPC" />
                    </div>
                    <div>
                      <label style={label}>Manufacturer / Provider</label>
                      <input value={formData.manufacturer} onChange={e => setFormData(f => ({ ...f, manufacturer: e.target.value }))} style={input} placeholder="e.g. Apple" />
                    </div>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    <div>
                      <label style={label}>Department</label>
                      <select value={formData.department_id} onChange={e => setFormData(f => ({ ...f, department_id: e.target.value, category_id: '' }))} style={input}>
                        <option value="">-- Select --</option>
                        {departments.map(d => <option key={d.id} value={d.id}>{d.short_name}</option>)}
                      </select>
                    </div>
                    <div>
                      <label style={label}>Category</label>
                      <select value={formData.category_id} onChange={e => setFormData(f => ({ ...f, category_id: e.target.value }))} style={input}>
                        <option value="">-- Select --</option>
                        {filteredCats.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                      </select>
                    </div>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
                    <div>
                      <label style={label}>System Category</label>
                      <select value={formData.system_category} onChange={e => setFormData(f => ({ ...f, system_category: e.target.value }))} style={input}>
                        {activeSysCats.map(c => <option key={c.id}>{c.name}</option>)}
                        {/* Keep an inactive value the product already carries selectable, or
                            opening the form would silently re-stamp it to the first option. */}
                        {formData.system_category && !activeSysCats.some(c => c.name === formData.system_category) && (
                          <option key="current">{formData.system_category}</option>
                        )}
                      </select>
                    </div>
                    <div>
                      <label style={label}>Inventory Type</label>
                      <select value={formData.inventory_type} onChange={e => setFormData(f => ({ ...f, inventory_type: e.target.value }))} style={input}>
                        <option value="standard">Standard (qty-based)</option>
                        <option value="serial">Serial Tracked (IMEI)</option>
                      </select>
                    </div>
                    <div>
                      <label style={label}>Body Style</label>
                      <select value={formData.body_style} onChange={e => setFormData(f => ({ ...f, body_style: e.target.value }))} style={input}>
                        {BODY_STYLES.map(s => <option key={s}>{s}</option>)}
                      </select>
                    </div>
                  </div>
                  <div style={panel}>
                    <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Pricing</div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                      <div>
                        <label style={label}>Cost</label>
                        <input type="number" step="0.01" value={formData.cost} onChange={e => setFormData(f => ({ ...f, cost: parseFloat(e.target.value) || 0 }))} style={input} />
                      </div>
                      <div>
                        <label style={label}>Retail Price</label>
                        <input type="number" step="0.01" value={formData.retail_price} onChange={e => setFormData(f => ({ ...f, retail_price: parseFloat(e.target.value) || 0 }))} style={input} />
                      </div>
                    </div>
                    {formData.cost > 0 && formData.retail_price > 0 && (
                      <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text2)' }}>
                        Margin: ${(formData.retail_price - formData.cost).toFixed(2)} ({(((formData.retail_price - formData.cost) / formData.retail_price) * 100).toFixed(1)}%)
                      </div>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                      <input type="checkbox" checked={formData.is_taxable} onChange={e => setFormData(f => ({ ...f, is_taxable: e.target.checked }))} />
                      Taxable
                    </label>
                    {([['Calculate as profit', 'calculate_as_profit'], ['Active', 'is_active'], ['End of life', 'end_of_life']] as const).map(([lbl, key]) => (
                      <label key={key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                        <input type="checkbox" checked={(formData as any)[key]} onChange={e => setFormData(f => ({ ...f, [key]: e.target.checked }))} />
                        {lbl}
                      </label>
                    ))}
                  </div>
                </div>
              )}

              {formTab === 'Summary' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {[
                    { l: 'Short Name', v: formData.short_name },
                    { l: 'UPC', v: formData.upc },
                    { l: 'Inventory Type', v: formData.inventory_type === 'serial' ? 'Serial Tracked' : 'Standard' },
                    { l: 'System Category', v: formData.system_category },
                    { l: 'Cost', v: `$${formData.cost?.toFixed(2)}` },
                    { l: 'Retail Price', v: `$${formData.retail_price?.toFixed(2)}` },
                    { l: 'Taxable', v: formData.is_taxable ? 'Yes' : 'No' },
                    { l: 'Active', v: formData.is_active ? 'Yes' : 'No' },
                  ].map(({ l, v }) => (
                    <div key={l} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--surface2)', borderRadius: 6, fontSize: 13 }}>
                      <span style={{ color: 'var(--text2)' }}>{l}</span>
                      <span style={{ fontWeight: 500 }}>{v || '—'}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setShowForm(false)}>Cancel</button>
              <button className="btn btn-primary" disabled={saving || !formData.short_name} onClick={saveProduct}>
                {saving ? 'Saving…' : 'Save & Close'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Departments & categories modal */}
      {showDeptCat && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 210, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 700, maxHeight: '85vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>Departments, Categories &amp; System Categories</b>
              <button onClick={() => setShowDeptCat(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>
            {msg && <div style={{ margin: '12px 20px 0', fontSize: 12, color: '#dc2626' }}>{msg}</div>}
            <div style={{ padding: 20, overflowY: 'auto', flex: 1, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              <div style={panel}>
                <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 10, textTransform: 'uppercase' }}>Departments</div>
                <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
                  <input value={newDeptName} onChange={e => setNewDeptName(e.target.value)} onKeyDown={e => e.key === 'Enter' && addDepartment()} placeholder="New department name" style={input} />
                  <button className="btn btn-primary" disabled={deptCatBusy || !newDeptName.trim()} onClick={addDepartment}>Add</button>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 300, overflowY: 'auto' }}>
                  {departments.length === 0 && <div style={{ fontSize: 12, color: 'var(--text3)', padding: 8 }}>No departments yet</div>}
                  {departments.map(d => (
                    <div key={d.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13 }}>
                      <span>{d.short_name}</span>
                      <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => renameDepartment(d)}>Rename</button>
                    </div>
                  ))}
                </div>
              </div>
              <div style={panel}>
                <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 10, textTransform: 'uppercase' }}>Categories</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 10 }}>
                  <select value={newCatDept} onChange={e => setNewCatDept(e.target.value)} style={input}>
                    <option value="">-- No department --</option>
                    {departments.map(d => <option key={d.id} value={d.id}>{d.short_name}</option>)}
                  </select>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <input value={newCatName} onChange={e => setNewCatName(e.target.value)} onKeyDown={e => e.key === 'Enter' && addCategory()} placeholder="New category name" style={input} />
                    <button className="btn btn-primary" disabled={deptCatBusy || !newCatName.trim()} onClick={addCategory}>Add</button>
                  </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 300, overflowY: 'auto' }}>
                  {categories.length === 0 && <div style={{ fontSize: 12, color: 'var(--text3)', padding: 8 }}>No categories yet</div>}
                  {categories.map(c => (
                    <div key={c.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13 }}>
                      <span>{c.name}<span style={{ color: 'var(--text3)', fontSize: 11 }}>{c.department_id ? ` — ${departments.find(d => d.id === c.department_id)?.short_name || ''}` : ''}</span></span>
                      <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => renameCategory(c)}>Rename</button>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div style={{ padding: '0 20px 20px' }}>
              <div style={panel}>
                <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 4, textTransform: 'uppercase' }}>System Categories</div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 10 }}>
                  The broad type a product is treated as. Built-in ones can be renamed or switched
                  off, not deleted. Renaming moves every product using it.
                </div>
                <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
                  <input value={newSysCatName} onChange={e => setNewSysCatName(e.target.value)} onKeyDown={e => e.key === 'Enter' && addSystemCategory()} placeholder="New system category (e.g. Tablet)" style={input} />
                  <button className="btn btn-primary" disabled={deptCatBusy || !newSysCatName.trim()} onClick={addSystemCategory}>Add</button>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 220, overflowY: 'auto' }}>
                  {sysCats.length === 0 && <div style={{ fontSize: 12, color: 'var(--text3)', padding: 8 }}>None configured</div>}
                  {sysCats.map(c => (
                    <div key={c.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13, opacity: c.is_active ? 1 : 0.55 }}>
                      <span>
                        {c.name}
                        {c.is_builtin && <span style={{ color: 'var(--text3)', fontSize: 11 }}> — built-in</span>}
                        {!c.is_active && <span style={{ color: 'var(--text3)', fontSize: 11 }}> — off</span>}
                      </span>
                      <span style={{ display: 'flex', gap: 6 }}>
                        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }} disabled={deptCatBusy} onClick={() => renameSystemCategory(c)}>Rename</button>
                        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }} disabled={deptCatBusy} onClick={() => toggleSystemCategory(c)}>{c.is_active ? 'Switch off' : 'Switch on'}</button>
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div style={{ padding: '12px 20px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setShowDeptCat(false)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
