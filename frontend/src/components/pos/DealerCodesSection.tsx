'use client'
// POS module — Phase 2: Dealer Codes section (ported from the standalone pos-system app's
// app/settings/page.tsx dealer-codes CRUD). Store dimension changed from location_id uuid
// to store_code TEXT (null = org-wide); data access rewired from direct Supabase to the
// FastAPI /pos router (GET/POST /api/v1/pos/dealer-codes, PATCH /dealer-codes/{id}).
// POST does not take is_active, so a new code created unchecked is POSTed then PATCHed.
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { friendlyError, storeLabel } from '@/components/pos/PosConfigSection'
import type { PosStore } from '@/components/pos/PosConfigSection'

interface DealerCode { id: string; code: string; carrier: string | null; store_code: string | null; is_active: boolean; created_at?: string }
interface DealerCodeForm { id: string | null; code: string; carrier: string; store_code: string; is_active: boolean }

const emptyDealerForm: DealerCodeForm = { id: null, code: '', carrier: '', store_code: '', is_active: true }

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', outline: 'none' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const th: React.CSSProperties = { textAlign: 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '8px 14px', fontSize: 13, borderBottom: '1px solid var(--border)' }
const errorBox: React.CSSProperties = { margin: '12px 16px', border: '1px solid #dc2626', color: '#dc2626', borderRadius: 8, padding: '10px 14px', fontSize: 12 }

interface Props {
  stores: PosStore[]
}

export default function DealerCodesSection({ stores }: Props) {
  const [dealerCodes, setDealerCodes] = useState<DealerCode[]>([])
  const [loading, setLoading] = useState(true)
  const [form, setForm] = useState<DealerCodeForm | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    loadDealerCodes().then(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function loadDealerCodes() {
    try {
      const r = await api('/api/v1/pos/dealer-codes')
      setDealerCodes((r.dealer_codes || []) as DealerCode[])
    } catch (err) {
      setError(friendlyError(err, 'Could not load dealer codes'))
    }
  }

  async function saveDealerCode() {
    if (!form) return
    const code = form.code.trim()
    if (!code) { setError('Dealer code is required.'); return }
    setSaving(true)
    setError('')
    try {
      // store_code '' → org-wide (the router nulls empty strings).
      const payload = { code, carrier: form.carrier.trim(), store_code: form.store_code }
      if (form.id) {
        await api(`/api/v1/pos/dealer-codes/${form.id}`, { method: 'PATCH', body: JSON.stringify({ ...payload, is_active: form.is_active }) })
      } else {
        const r = await api('/api/v1/pos/dealer-codes', { method: 'POST', body: JSON.stringify(payload) })
        // POST always creates active; honor an unchecked Active box with a follow-up PATCH.
        if (!form.is_active && r.dealer_code?.id) {
          await api(`/api/v1/pos/dealer-codes/${r.dealer_code.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: false }) })
        }
      }
      setForm(null)
      await loadDealerCodes()
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      if (/duplicate|unique|23505|already exists/i.test(msg)) setError(`A dealer code "${code}" already exists — edit that one instead.`)
      else setError(friendlyError(err, 'Could not save dealer code'))
    } finally {
      setSaving(false)
    }
  }

  async function toggleDealerCode(dc: DealerCode) {
    if (dc.is_active && !confirm(`Deactivate dealer code "${dc.code}"?`)) return
    setError('')
    try {
      await api(`/api/v1/pos/dealer-codes/${dc.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !dc.is_active }) })
      await loadDealerCodes()
    } catch (err) {
      setError(friendlyError(err, 'Could not update dealer code'))
    }
  }

  const storeName = (code: string | null) => {
    if (!code) return 'All stores'
    const s = stores.find(x => x.store_code === code)
    return s ? storeLabel(s) : code
  }

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, marginBottom: 16, overflow: 'hidden' }}>
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>🏷️ Dealer Codes</div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>Carrier dealer codes used on activations — org-wide or per store</div>
        </div>
        {!form && (
          <button className="btn btn-primary" onClick={() => { setError(''); setForm({ ...emptyDealerForm }) }}>+ Add Dealer Code</button>
        )}
      </div>

      {/* Dealer code form */}
      {form && (
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 10 }}>{form.id ? 'Edit Dealer Code' : 'New Dealer Code'}</div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <label style={label}>Code</label>
              <input value={form.code} onChange={e => setForm({ ...form, code: e.target.value })} placeholder="e.g. VZW-100482" style={{ ...input, width: 180 }} />
            </div>
            <div>
              <label style={label}>Carrier (optional)</label>
              <input value={form.carrier} onChange={e => setForm({ ...form, carrier: e.target.value })} placeholder="e.g. Verizon" style={{ ...input, width: 150 }} />
            </div>
            <div>
              <label style={label}>Store</label>
              <select value={form.store_code} onChange={e => setForm({ ...form, store_code: e.target.value })} style={{ ...input, width: 220 }}>
                <option value="">All stores (org-wide)</option>
                {stores.map(s => <option key={s.store_code} value={s.store_code}>{storeLabel(s)}</option>)}
              </select>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, paddingBottom: 8, cursor: 'pointer' }}>
              <input type="checkbox" checked={form.is_active} onChange={e => setForm({ ...form, is_active: e.target.checked })} />
              Active
            </label>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-primary" onClick={saveDealerCode} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
              <button className="btn btn-secondary" onClick={() => { setForm(null); setError('') }}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {error && <div style={errorBox}>{error}</div>}

      {/* Dealer codes table */}
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 30 }}><div className="spinner" /></div>
      ) : dealerCodes.length === 0 ? (
        <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>No dealer codes yet — add the codes your carriers assigned you.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--surface2)' }}>
                <th style={th}>Code</th><th style={th}>Carrier</th><th style={th}>Store</th><th style={th}>Status</th><th style={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {dealerCodes.map(dc => (
                <tr key={dc.id} style={{ opacity: dc.is_active ? 1 : 0.5 }}>
                  <td style={{ ...td, fontWeight: 600, color: '#2980b9' }}>{dc.code}</td>
                  <td style={td}>{dc.carrier || '—'}</td>
                  <td style={{ ...td, color: dc.store_code ? 'var(--text)' : '#f39c12' }}>{storeName(dc.store_code)}</td>
                  <td style={td}><span style={{ color: dc.is_active ? '#16a34a' : '#dc2626', fontWeight: 600 }}>{dc.is_active ? 'Active' : 'Inactive'}</span></td>
                  <td style={td}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }}
                        onClick={() => { setError(''); setForm({ id: dc.id, code: dc.code, carrier: dc.carrier || '', store_code: dc.store_code || '', is_active: dc.is_active }) }}>Edit</button>
                      <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px', color: dc.is_active ? '#dc2626' : '#16a34a' }}
                        onClick={() => toggleDealerCode(dc)}>{dc.is_active ? 'Deactivate' : 'Reactivate'}</button>
                    </div>
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
