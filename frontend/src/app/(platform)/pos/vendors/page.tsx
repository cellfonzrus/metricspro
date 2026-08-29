'use client'
// POS module — Phase 2: Vendors / Business Address Book (ported from the standalone pos-system app;
// data access rewired from direct Supabase to the FastAPI /pos router).
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'

interface Vendor {
  id: string; ban: string | null; legal_name: string; short_name: string | null
  business_type: string | null; street_one: string | null; street_two: string | null
  city: string | null; state: string | null; zip: string | null; country: string | null
  tax_id: string | null; contact_name: string | null; phone: string | null
  fax: string | null; email: string | null; website: string | null; is_active: boolean
}

const US_STATES = ['AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY']
// 'Sub Dealer' added 2026-08-09 — the owner named master AND sub dealers as trading partners a
// tenant must be able to record ("customers, vendors, manufacturer, master dealers, sub dealers if
// any"), and the list shipped without it, so a sub dealer could only be filed as a plain Vendor.
// The list itself is still a constant, which is a RULE TWO gap: it belongs in a config table with
// the other POS vocabularies. Tracked in the platform-core handoff rather than widened here, because
// the same constant is duplicated in the onboarding template registry and the two should move to
// config together, not one at a time.
const BUSINESS_TYPES = ['Vendor', 'Manufacturer', 'Master Dealer', 'Sub Dealer', 'Shipper', 'ePay carrier']
const SEARCH_BY_OPTIONS: { label: string; value: string }[] = [
  { label: 'Company Name', value: 'legal_name' },
  { label: 'Contact Name', value: 'contact_name' },
  { label: 'Phone', value: 'phone' },
  { label: 'Email', value: 'email' },
]

const emptyForm = {
  ban: '', legal_name: '', short_name: '', business_type: 'Vendor',
  street_one: '', street_two: '', city: '', state: 'NY', zip: '', country: 'USA',
  tax_id: '', contact_name: '', phone: '', fax: '', email: '', website: '', is_active: true,
}

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', width: '100%', outline: 'none' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const cell: React.CSSProperties = { padding: '7px 12px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const panel: React.CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }

export default function PosVendorsPage() {
  const [vendors, setVendors] = useState<Vendor[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [search, setSearch] = useState('')
  const [searchBy, setSearchBy] = useState('legal_name')
  const [businessType, setBusinessType] = useState('')
  const [showActive, setShowActive] = useState(true)
  const [selected, setSelected] = useState<Vendor | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editMode, setEditMode] = useState(false)
  const [formData, setFormData] = useState({ ...emptyForm })
  const [saving, setSaving] = useState(false)

  async function loadVendors(q = { search, searchBy, businessType, showActive }) {
    setLoading(true); setMsg('')
    try {
      const params = new URLSearchParams()
      if (q.search.trim()) { params.set('search', q.search.trim()); params.set('search_by', q.searchBy) }
      if (q.businessType) params.set('business_type', q.businessType)
      params.set('active_only', String(q.showActive))
      const r = await api(`/api/v1/pos/vendors?${params}`)
      setVendors(r.vendors || [])
    } catch (err: any) { setMsg('Failed to load vendors: ' + (err?.message || err)) }
    setLoading(false)
  }

  useEffect(() => { loadVendors() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  async function saveVendor() {
    setSaving(true); setMsg('')
    try {
      if (editMode && selected) await api(`/api/v1/pos/vendors/${selected.id}`, { method: 'PATCH', body: JSON.stringify(formData) })
      else await api('/api/v1/pos/vendors', { method: 'POST', body: JSON.stringify(formData) })
      setShowForm(false); setEditMode(false)
      await loadVendors()
    } catch (err: any) { alert('Failed to save vendor: ' + (err?.message || err)) }
    setSaving(false)
  }

  async function deleteSelected() {
    if (!selected || !confirm(`Delete ${selected.legal_name}?`)) return
    try {
      await api(`/api/v1/pos/vendors/${selected.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: false }) })
      setSelected(null); await loadVendors()
    } catch (err: any) { alert('Failed to delete vendor: ' + (err?.message || err)) }
  }

  function openNew() { setFormData({ ...emptyForm }); setEditMode(false); setShowForm(true) }
  function openEdit() {
    if (!selected) return
    setFormData({
      ban: selected.ban || '', legal_name: selected.legal_name, short_name: selected.short_name || '',
      business_type: selected.business_type || 'Vendor', street_one: selected.street_one || '', street_two: selected.street_two || '',
      city: selected.city || '', state: selected.state || 'NY', zip: selected.zip || '', country: selected.country || 'USA',
      tax_id: selected.tax_id || '', contact_name: selected.contact_name || '', phone: selected.phone || '',
      fax: selected.fax || '', email: selected.email || '', website: selected.website || '', is_active: selected.is_active,
    })
    setEditMode(true); setShowForm(true)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏭 Business Address Book — Vendors</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Records: {vendors.length}{selected ? ` · selected: ${selected.legal_name}` : ''}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {msg && <span style={{ fontSize: 13, color: '#dc2626' }}>{msg}</span>}
          <button className="btn btn-primary" onClick={openNew}>+ Create New</button>
          {selected && <button className="btn btn-secondary" onClick={openEdit}>View/Edit</button>}
          {selected && <button className="btn btn-secondary" style={{ color: '#dc2626' }} onClick={deleteSelected}>Delete</button>}
        </div>
      </div>

      {/* Search & filters */}
      <div style={{ ...panel, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: 'var(--text2)', whiteSpace: 'nowrap' }}>Search By</span>
        <select value={searchBy} onChange={e => setSearchBy(e.target.value)} style={{ ...input, width: 160 }}>
          {SEARCH_BY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <input value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => e.key === 'Enter' && loadVendors()}
          placeholder="*" style={{ ...input, flex: 1, minWidth: 160 }} />
        <span style={{ fontSize: 12, color: 'var(--text2)', whiteSpace: 'nowrap' }}>Business Type(s)</span>
        <select value={businessType} onChange={e => setBusinessType(e.target.value)} style={{ ...input, width: 160 }}>
          <option value="">&lt; Any &gt;</option>
          {BUSINESS_TYPES.map(t => <option key={t}>{t}</option>)}
        </select>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap' }}>
          <input type="checkbox" checked={showActive} onChange={e => setShowActive(e.target.checked)} />
          Show Active Only
        </label>
        <button className="btn btn-primary" onClick={() => loadVendors()}>Search</button>
      </div>

      {/* Table */}
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="table-wrapper" style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1200, fontSize: 13 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['BAN', 'Legal Name', 'Short Name', 'Street One', 'Street Two', 'City', 'State', 'Zip', 'Tax', 'Contact Name', 'Phone', 'Fax', 'E-mail', 'Website'].map(h =>
                <th key={h} style={{ textAlign: 'left', padding: 8, fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {vendors.map(v => (
                <tr key={v.id} onClick={() => setSelected(selected?.id === v.id ? null : v)}
                  style={{ cursor: 'pointer', background: selected?.id === v.id ? 'var(--surface2)' : 'transparent', opacity: v.is_active ? 1 : 0.55 }}>
                  <td style={cell}>{v.ban || ''}</td>
                  <td style={{ ...cell, fontWeight: 500 }}>{v.legal_name}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{v.short_name || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{v.street_one || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{v.street_two || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{v.city || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{v.state || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{v.zip || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{v.tax_id || ''}</td>
                  <td style={cell}>{v.contact_name || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{v.phone || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{v.fax || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{v.email || ''}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{v.website || ''}</td>
                </tr>
              ))}
              {vendors.length === 0 && (
                <tr><td colSpan={14} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                  No vendors found. Click &ldquo;+ Create New&rdquo; to add one.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Vendor form modal */}
      {showForm && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 640, maxHeight: '92vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>{editMode ? `Edit Vendor — ${selected?.legal_name}` : 'New Vendor / Manufacturer'}</b>
              <button onClick={() => setShowForm(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>

            <div style={{ padding: 20, overflowY: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <label style={label}>Company Short (Nick) Name</label>
                  <input value={formData.short_name} onChange={e => setFormData(f => ({ ...f, short_name: e.target.value }))} style={input} />
                </div>
                <div>
                  <label style={label}>Company Legal (Full) Name *</label>
                  <input value={formData.legal_name} onChange={e => setFormData(f => ({ ...f, legal_name: e.target.value }))} style={input} />
                </div>
              </div>
              <div>
                <label style={label}>Business Type(s)</label>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {BUSINESS_TYPES.map(t => (
                    <label key={t} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 13, color: formData.business_type === t ? 'var(--text)' : 'var(--text2)', cursor: 'pointer' }}>
                      <input type="radio" checked={formData.business_type === t} onChange={() => setFormData(f => ({ ...f, business_type: t }))} /> {t}
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <label style={label}>Address</label>
                <input value={formData.street_one} onChange={e => setFormData(f => ({ ...f, street_one: e.target.value }))} style={input} />
              </div>
              <div>
                <label style={label}>Address Line 2</label>
                <input value={formData.street_two} onChange={e => setFormData(f => ({ ...f, street_two: e.target.value }))} style={input} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 120px 80px', gap: 10 }}>
                <div>
                  <label style={label}>City</label>
                  <input value={formData.city} onChange={e => setFormData(f => ({ ...f, city: e.target.value }))} style={input} />
                </div>
                <div>
                  <label style={label}>State</label>
                  <select value={formData.state} onChange={e => setFormData(f => ({ ...f, state: e.target.value }))} style={input}>
                    {US_STATES.map(s => <option key={s}>{s}</option>)}
                  </select>
                </div>
                <div>
                  <label style={label}>Zip</label>
                  <input value={formData.zip} onChange={e => setFormData(f => ({ ...f, zip: e.target.value }))} style={input} />
                </div>
              </div>
              <div>
                <label style={label}>Country</label>
                <input value={formData.country} onChange={e => setFormData(f => ({ ...f, country: e.target.value }))} style={input} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <label style={label}>Tax ID</label>
                  <input value={formData.tax_id} onChange={e => setFormData(f => ({ ...f, tax_id: e.target.value }))} style={input} placeholder="__-_______" />
                </div>
                <div>
                  <label style={label}>Business Account #</label>
                  <input value={formData.ban} onChange={e => setFormData(f => ({ ...f, ban: e.target.value }))} style={input} />
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <label style={label}>Contact Name</label>
                  <input value={formData.contact_name} onChange={e => setFormData(f => ({ ...f, contact_name: e.target.value }))} style={input} />
                </div>
                <div>
                  <label style={label}>Web Site</label>
                  <input value={formData.website} onChange={e => setFormData(f => ({ ...f, website: e.target.value }))} style={input} />
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <label style={label}>Phone</label>
                  <input value={formData.phone} onChange={e => setFormData(f => ({ ...f, phone: e.target.value }))} style={input} placeholder="(___) ___-____" />
                </div>
                <div>
                  <label style={label}>Fax</label>
                  <input value={formData.fax} onChange={e => setFormData(f => ({ ...f, fax: e.target.value }))} style={input} placeholder="(___) ___-____" />
                </div>
              </div>
              <div>
                <label style={label}>E-mail</label>
                <input type="email" value={formData.email} onChange={e => setFormData(f => ({ ...f, email: e.target.value }))} style={input} />
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                <input type="checkbox" checked={formData.is_active} onChange={e => setFormData(f => ({ ...f, is_active: e.target.checked }))} />
                Active
              </label>
            </div>

            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setShowForm(false)}>Cancel</button>
              <button className="btn btn-primary" disabled={saving || !formData.legal_name} onClick={saveVendor}>
                {saving ? 'Saving…' : 'Save & Close'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
