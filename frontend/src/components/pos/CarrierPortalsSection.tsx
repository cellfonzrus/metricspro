'use client'
// POS module — Phase 2: Carrier Portals section (ported from the standalone pos-system app's
// app/settings/CarrierPortalsSection.tsx). Data access rewired from direct Supabase to the
// FastAPI /pos router (GET/POST /api/v1/pos/carrier-portals, PATCH/DELETE /carrier-portals/{id}).
// One portal per carrier (unique index) — duplicate saves get a friendly message. POST does not
// take is_active, so a new portal created unchecked is POSTed then PATCHed.
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { friendlyError } from '@/components/pos/PosConfigSection'

interface CarrierPortal { id: string; carrier: string; url: string; is_active: boolean; sort_order: number }
interface PortalForm { id: string | null; carrier: string; url: string; sort_order: string; is_active: boolean }

const emptyPortalForm: PortalForm = { id: null, carrier: '', url: '', sort_order: '0', is_active: true }

function shortUrl(url: string): string {
  const stripped = url.replace(/^https?:\/\//, '')
  return stripped.length > 48 ? `${stripped.slice(0, 48)}…` : stripped
}

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', outline: 'none' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const th: React.CSSProperties = { textAlign: 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '8px 14px', fontSize: 13, borderBottom: '1px solid var(--border)' }
const errorBox: React.CSSProperties = { margin: '12px 16px', border: '1px solid #dc2626', color: '#dc2626', borderRadius: 8, padding: '10px 14px', fontSize: 12 }

export default function CarrierPortalsSection() {
  const [portals, setPortals] = useState<CarrierPortal[]>([])
  const [loading, setLoading] = useState(true)
  const [form, setForm] = useState<PortalForm | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    loadPortals().then(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function loadPortals() {
    try {
      const r = await api('/api/v1/pos/carrier-portals')
      setPortals((r.carrier_portals || []) as CarrierPortal[])
    } catch (err) {
      setError(friendlyError(err, 'Could not load carrier portals'))
    }
  }

  async function savePortal() {
    if (!form) return
    const carrier = form.carrier.trim()
    const url = form.url.trim()
    const sortOrder = Math.round(Number(form.sort_order))
    if (!carrier) { setError('Carrier name is required.'); return }
    if (!url) { setError('Portal URL is required.'); return }
    if (!/^https?:\/\//.test(url)) { setError('The URL must start with https:// (or http://).'); return }
    if (form.sort_order.trim() === '' || !Number.isFinite(sortOrder)) { setError('Sort order must be a number (lower shows first).'); return }
    setSaving(true)
    setError('')
    try {
      if (form.id) {
        await api(`/api/v1/pos/carrier-portals/${form.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ carrier, url, sort_order: sortOrder, is_active: form.is_active }),
        })
      } else {
        const r = await api('/api/v1/pos/carrier-portals', {
          method: 'POST',
          body: JSON.stringify({ carrier, url, sort_order: sortOrder }),
        })
        // POST always creates active; honor an unchecked Active box with a follow-up PATCH.
        if (!form.is_active && r.carrier_portal?.id) {
          await api(`/api/v1/pos/carrier-portals/${r.carrier_portal.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: false }) })
        }
      }
      setForm(null)
      await loadPortals()
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      if (/duplicate|unique|23505|already exists/i.test(msg)) setError(`A portal for "${carrier}" already exists — edit that one instead.`)
      else setError(friendlyError(err, 'Could not save carrier portal'))
    } finally {
      setSaving(false)
    }
  }

  async function togglePortal(p: CarrierPortal) {
    if (p.is_active && !confirm(`Deactivate the "${p.carrier}" portal? It will disappear from the Activations quick links.`)) return
    setError('')
    try {
      await api(`/api/v1/pos/carrier-portals/${p.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !p.is_active }) })
      await loadPortals()
    } catch (err) {
      setError(friendlyError(err, 'Could not update carrier portal'))
    }
  }

  async function deletePortal(p: CarrierPortal) {
    if (!confirm(`Delete the "${p.carrier}" portal? This cannot be undone.`)) return
    setError('')
    try {
      await api(`/api/v1/pos/carrier-portals/${p.id}`, { method: 'DELETE' })
      await loadPortals()
    } catch (err) {
      setError(friendlyError(err, 'Could not delete carrier portal'))
    }
  }

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, marginBottom: 16, overflow: 'hidden' }}>
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>🔗 Carrier Portals</div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>Dealer web portal links — these feed the Credit Check quick links in Activations</div>
        </div>
        {!form && (
          <button className="btn btn-primary" onClick={() => { setError(''); setForm({ ...emptyPortalForm }) }}>+ Add Portal</button>
        )}
      </div>

      {/* Portal form */}
      {form && (
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 10 }}>{form.id ? 'Edit Portal' : 'New Portal'}</div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <label style={label}>Carrier</label>
              <input value={form.carrier} onChange={e => setForm({ ...form, carrier: e.target.value })} placeholder="e.g. T-Mobile" style={{ ...input, width: 160 }} />
            </div>
            <div>
              <label style={label}>Portal URL</label>
              <input value={form.url} onChange={e => setForm({ ...form, url: e.target.value })} placeholder="https://dealer.t-mobile.com/..." style={{ ...input, width: 320 }} />
            </div>
            <div>
              <label style={label}>Sort</label>
              <input type="number" value={form.sort_order} onChange={e => setForm({ ...form, sort_order: e.target.value })} style={{ ...input, width: 70 }} />
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, paddingBottom: 8, cursor: 'pointer' }}>
              <input type="checkbox" checked={form.is_active} onChange={e => setForm({ ...form, is_active: e.target.checked })} />
              Active
            </label>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-primary" onClick={savePortal} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
              <button className="btn btn-secondary" onClick={() => { setForm(null); setError('') }}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {error && <div style={errorBox}>{error}</div>}

      {/* Portals table */}
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 30 }}><div className="spinner" /></div>
      ) : portals.length === 0 ? (
        <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>No carrier portals yet — add your dealer login links (T-Mobile, Metro, Cricket, …).</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--surface2)' }}>
                <th style={th}>Carrier</th><th style={th}>URL</th><th style={th}>Sort</th><th style={th}>Status</th><th style={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {portals.map(p => (
                <tr key={p.id} style={{ opacity: p.is_active ? 1 : 0.5 }}>
                  <td style={{ ...td, fontWeight: 600 }}>{p.carrier}</td>
                  <td style={td}>
                    <a href={p.url} target="_blank" rel="noopener noreferrer" title={p.url} style={{ color: '#2980b9', textDecoration: 'none' }}>{shortUrl(p.url)}</a>
                  </td>
                  <td style={{ ...td, color: 'var(--text2)' }}>{p.sort_order}</td>
                  <td style={td}><span style={{ color: p.is_active ? '#16a34a' : '#dc2626', fontWeight: 600 }}>{p.is_active ? 'Active' : 'Inactive'}</span></td>
                  <td style={td}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }}
                        onClick={() => { setError(''); setForm({ id: p.id, carrier: p.carrier, url: p.url, sort_order: String(p.sort_order), is_active: p.is_active }) }}>Edit</button>
                      <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px', color: p.is_active ? '#dc2626' : '#16a34a' }}
                        onClick={() => togglePortal(p)}>{p.is_active ? 'Deactivate' : 'Reactivate'}</button>
                      <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px', color: '#dc2626' }}
                        onClick={() => deletePortal(p)}>Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)' }}>
        💡 Active portals appear as quick links on the Activations credit-check flow. One portal per carrier.
      </div>
    </div>
  )
}
