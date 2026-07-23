'use client'
// Manage Vendors — the per-org vendor/supplier roster (commcalc.po_vendor) that every PO's vendor picker
// (RULE THREE) draws from. Writes are admin-gated server-side (asset_purchase_orders settings area); the
// frontend also disables the form for a non-admin as a UX nicety, not the enforcement boundary.
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import PoNav from '../_shared/PoNav'

type Vendor = {
  id: string; name: string; contact_name: string | null; email: string | null
  phone: string | null; terms: string | null; notes: string | null; is_active: boolean
}

const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 12, padding: 16, background: 'var(--surface)', marginBottom: 16 }
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, width: '100%' }
const th: React.CSSProperties = { textAlign: 'left', padding: '7px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 13 }

const EMPTY = { name: '', contact_name: '', email: '', phone: '', terms: '', notes: '' }

export default function ManageVendorsPage() {
  const { user, permissions } = useAuth()
  const isAdmin = !!(user?.super_admin || (permissions as any)?.scope === 'all' || (user?.role || '').toLowerCase() === 'admin')

  const [vendors, setVendors] = useState<Vendor[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [form, setForm] = useState(EMPTY)
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api('/api/v1/asset/po/vendors?active_only=false')
      setVendors(d.rows || [])
      if (d.migrated === false) setMsg(d.note || 'Purchase Orders migration pending.')
    } catch (e: any) { setMsg('Could not load vendors: ' + (e?.message || e)) }
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  async function createVendor() {
    if (!form.name.trim()) { setMsg('Vendor name is required.'); return }
    setSaving(true); setMsg('')
    try {
      await api('/api/v1/asset/po/vendors', { method: 'POST', body: JSON.stringify(form) })
      setForm(EMPTY)
      load()
    } catch (e: any) { setMsg('Could not create vendor: ' + (e?.message || e)) }
    setSaving(false)
  }

  async function toggleActive(v: Vendor) {
    try {
      await api(`/api/v1/asset/po/vendors/${v.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !v.is_active }) })
      load()
    } catch (e: any) { setMsg('Could not update vendor: ' + (e?.message || e)) }
  }

  return (
    <div style={{ padding: 20, maxWidth: 1000, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, marginBottom: 4 }}>🏭 Manage Vendors</h1>
      <PoNav active="/commcalc/asset/purchase-orders/vendors" />
      {msg && <div style={{ ...card, background: 'var(--surface2)', fontSize: 13 }}>{msg}</div>}

      <div style={card}>
        <h3 style={{ fontSize: 15, marginBottom: 10 }}>Add a vendor</h3>
        {!isAdmin && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>Admin only — the form below is view-only for your role.</div>}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10 }}>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Name*
            <input style={sel} disabled={!isAdmin} value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} /></label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Contact name
            <input style={sel} disabled={!isAdmin} value={form.contact_name} onChange={e => setForm(f => ({ ...f, contact_name: e.target.value }))} /></label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Email
            <input style={sel} disabled={!isAdmin} value={form.email} onChange={e => setForm(f => ({ ...f, email: e.target.value }))} /></label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Phone
            <input style={sel} disabled={!isAdmin} value={form.phone} onChange={e => setForm(f => ({ ...f, phone: e.target.value }))} /></label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Terms
            <input style={sel} disabled={!isAdmin} placeholder="e.g. Net 30" value={form.terms} onChange={e => setForm(f => ({ ...f, terms: e.target.value }))} /></label>
        </div>
        <label style={{ fontSize: 12, color: 'var(--text2)', display: 'block', marginTop: 10 }}>Notes
          <textarea style={{ ...sel, minHeight: 50 }} disabled={!isAdmin} value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} /></label>
        <button className="btn btn-primary" style={{ marginTop: 10 }} disabled={!isAdmin || saving} onClick={createVendor}>
          {saving ? 'Saving…' : '+ Add vendor'}
        </button>
      </div>

      <div style={card}>
        <h3 style={{ fontSize: 15, marginBottom: 10 }}>Vendors</h3>
        {loading ? <p>Loading…</p> : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={th}>Name</th><th style={th}>Contact</th><th style={th}>Email</th><th style={th}>Phone</th><th style={th}>Terms</th><th style={th}>Status</th><th style={th} /></tr></thead>
            <tbody>
              {vendors.map(v => (
                <tr key={v.id}>
                  <td style={td}>{v.name}</td>
                  <td style={td}>{v.contact_name || '—'}</td>
                  <td style={td}>{v.email || '—'}</td>
                  <td style={td}>{v.phone || '—'}</td>
                  <td style={td}>{v.terms || '—'}</td>
                  <td style={td}>{v.is_active ? 'Active' : 'Inactive'}</td>
                  <td style={td}>
                    <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={!isAdmin} onClick={() => toggleActive(v)}>
                      {v.is_active ? 'Deactivate' : 'Reactivate'}
                    </button>
                  </td>
                </tr>
              ))}
              {vendors.length === 0 && <tr><td style={{ ...td, textAlign: 'center', color: 'var(--text3)' }} colSpan={7}>No vendors yet — add one above.</td></tr>}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
