'use client'
// POS module — Dealer Codes.
//
// 2026-08-10: THE IMPORT THE OWNER ASKED FOR HAD NO BUTTON. Owner report 2026-08-09: "dealer codes
// are not being pulled from the reports". The answer shipped the same evening as
// GET /pos/dealer-codes/sync-preview + POST /pos/dealer-codes/sync-from-reports, with the source
// column configured per carrier on commcalc.carrier (migration 293) — Boost's Salesforce ID, Total's
// Account ID. `grep` across frontend/src on 2026-08-10 found ZERO callers of either endpoint, so from
// the settings screen nothing had changed: still a hand-typing form. Measured the same morning:
// GET sync-preview on Luxelink offers 20 Account IDs and pos.dealer_codes holds 0. This panel is the
// missing half.
//
// PREVIEW BEFORE IMPORT, ALWAYS. The preview and the import share one resolver on the server, so what
// is shown is what lands. An unmapped carrier is reported as unconfigured and skipped rather than
// guessed at — seeding a tenant's POS with the wrong identifier is worse than seeding none — and this
// panel says so in those words instead of showing an empty result.
//
// PICK-DON'T-TYPE (RULE THREE): Carrier was a free-text box ("e.g. Verizon"), which is exactly the
// spelling-drift failure the rule exists to stop — a dealer code filed under "Total" and another
// under "Total Wireless" are two carriers as far as every join is concerned. It is now a dropdown
// over the tenant's own commcalc.carrier list.
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { friendlyError, storeLabel } from '@/components/pos/PosConfigSection'
import type { PosStore } from '@/components/pos/PosConfigSection'

interface DealerCode {
  id: string; code: string; carrier: string | null; store_code: string | null
  description?: string | null; is_active: boolean; created_at?: string
}
interface DealerCodeForm {
  id: string | null; code: string; carrier: string; store_code: string
  description: string; is_active: boolean
}
/** One carrier's line in GET /pos/dealer-codes/sync-preview. */
interface SyncCarrier {
  carrier: string | null
  configured: boolean
  hint?: string
  error?: string
  label?: string | null
  source?: string
  name_source?: string | null
  found?: number
  new?: number
  sample?: { code: string; description: string | null }[]
}
interface Carrier { id: string; name: string; code: string | null }

const emptyDealerForm: DealerCodeForm = { id: null, code: '', carrier: '', store_code: '', description: '', is_active: true }

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
  const [carriers, setCarriers] = useState<Carrier[]>([])
  const [loading, setLoading] = useState(true)
  const [form, setForm] = useState<DealerCodeForm | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  // Import-from-reports panel
  const [preview, setPreview] = useState<SyncCarrier[] | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [importing, setImporting] = useState(false)
  const [syncMsg, setSyncMsg] = useState('')

  const loadDealerCodes = useCallback(async () => {
    try {
      const r = await api('/api/v1/pos/dealer-codes')
      setDealerCodes((r.dealer_codes || []) as DealerCode[])
    } catch (err) {
      setError(friendlyError(err, 'Could not load dealer codes'))
    }
  }, [])

  useEffect(() => {
    loadDealerCodes().then(() => setLoading(false))
    apiCached('/api/v1/commcalc/carriers', LOOKUP)
      .then((d: any) => setCarriers(Array.isArray(d) ? d : []))
      .catch(() => setCarriers([]))
  }, [loadDealerCodes])

  async function runPreview() {
    setPreviewing(true); setSyncMsg(''); setError('')
    try {
      const r = await api('/api/v1/pos/dealer-codes/sync-preview')
      setPreview((r.carriers || []) as SyncCarrier[])
    } catch (err) {
      setError(friendlyError(err, 'Could not read the carrier report data'))
    } finally { setPreviewing(false) }
  }

  async function runImport() {
    const total = (preview || []).reduce((n, c) => n + (c.new || 0), 0)
    if (!confirm(`Import ${total} dealer code${total === 1 ? '' : 's'} from your carrier reports?\n\nExisting codes are left exactly as they are — nothing you have edited by hand is touched.`)) return
    setImporting(true); setSyncMsg(''); setError('')
    try {
      const r = await api('/api/v1/pos/dealer-codes/sync-from-reports', { method: 'POST' })
      setSyncMsg(`Imported ${r.inserted || 0} dealer code${(r.inserted || 0) === 1 ? '' : 's'}. Attach each one to its store below.`)
      setPreview((r.carriers || []) as SyncCarrier[])
      await loadDealerCodes()
    } catch (err) {
      setError(friendlyError(err, 'Could not import the dealer codes'))
    } finally { setImporting(false) }
  }

  async function saveDealerCode() {
    if (!form) return
    const code = form.code.trim()
    if (!code) { setError('Dealer code is required.'); return }
    setSaving(true)
    setError('')
    try {
      // store_code '' → org-wide (the router nulls empty strings).
      const payload = {
        code, carrier: form.carrier.trim(), store_code: form.store_code,
        description: form.description.trim(),
      }
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

  const unattached = dealerCodes.filter(d => d.is_active !== false && !d.store_code).length
  const newTotal = (preview || []).reduce((n, c) => n + (c.new || 0), 0)

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, marginBottom: 16, overflow: 'hidden' }}>
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>🏷️ Dealer Codes</div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
            The account number the carrier knows each door by — used on activations. Import them from
            your carrier reports, then attach each one to its store.
          </div>
        </div>
        {!form && (
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-secondary" onClick={runPreview} disabled={previewing}>
              {previewing ? 'Checking…' : '⬇️ Pull from reports'}
            </button>
            <button className="btn btn-primary" onClick={() => { setError(''); setForm({ ...emptyDealerForm }) }}>+ Add Dealer Code</button>
          </div>
        )}
      </div>

      {/* ── Import from reports ───────────────────────────────────────────────────────────────── */}
      {preview && (
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>What your carrier reports contain</div>
          {preview.length === 0 && (
            <div style={{ fontSize: 12.5, color: 'var(--text2)' }}>
              No carriers are attached to this company yet. Add one on the Carriers page first — the
              dealer code means something different for each carrier, so there is nothing to read until
              then.
            </div>
          )}
          {preview.map((c, i) => (
            <div key={i} style={{ fontSize: 12.5, padding: '6px 0', borderTop: i ? '1px solid var(--border)' : undefined }}>
              <b>{c.carrier || 'Unnamed carrier'}</b>
              {!c.configured && (
                <span style={{ color: '#b45309' }}> — not mapped yet. {c.hint || 'Set this carrier’s dealer-code source on the Carriers page.'} Nothing was guessed at.</span>
              )}
              {c.configured && c.error && <span style={{ color: '#dc2626' }}> — could not read {c.source}: {c.error}</span>}
              {c.configured && !c.error && (
                <>
                  <span style={{ color: 'var(--text2)' }}>
                    {' '}— {c.found ?? 0} {c.label || 'code'}{(c.found ?? 0) === 1 ? '' : 's'} in {c.source}
                    {c.name_source ? `, named from ${c.name_source}` : ''}
                    {' · '}<b style={{ color: (c.new ?? 0) > 0 ? '#16a34a' : 'var(--text3)' }}>{c.new ?? 0} new</b>
                  </span>
                  {(c.sample || []).length > 0 && (
                    <div style={{ marginTop: 4, color: 'var(--text3)', fontSize: 12 }}>
                      e.g. {(c.sample || []).map(s => s.description ? `${s.code} (${s.description})` : s.code).join(' · ')}
                    </div>
                  )}
                </>
              )}
            </div>
          ))}
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
            <button className="btn btn-primary" onClick={runImport} disabled={importing || newTotal === 0}>
              {importing ? 'Importing…' : newTotal > 0 ? `Import ${newTotal} new code${newTotal === 1 ? '' : 's'}` : 'Nothing new to import'}
            </button>
            <button className="btn btn-secondary" onClick={() => { setPreview(null); setSyncMsg('') }}>Close</button>
            {syncMsg && <span style={{ fontSize: 12, color: '#16a34a' }}>{syncMsg}</span>}
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 6 }}>
            Importing only ADDS codes that are missing. A code you have already edited — its store, its
            label, or a deactivation — is never overwritten, so this is safe to run again after each
            new report lands.
          </div>
        </div>
      )}

      {unattached > 0 && !preview && (
        <div style={{ margin: '12px 16px', border: '1px solid #fbbf24', background: '#fffbeb', borderRadius: 8, padding: '9px 14px', fontSize: 12.5, color: '#92400e' }}>
          {unattached} dealer code{unattached === 1 ? ' is' : 's are'} not attached to a store yet — they
          show as “All stores”, so a rep at any door can pick them. Edit each one and set its store.
        </div>
      )}

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
              <label style={label}>Carrier</label>
              <select value={form.carrier} onChange={e => setForm({ ...form, carrier: e.target.value })} style={{ ...input, width: 180 }}>
                <option value="">— none —</option>
                {carriers.map(c => <option key={c.id} value={c.name}>{c.name}</option>)}
                {/* An existing code whose carrier predates the carrier list must stay selectable. */}
                {form.carrier && !carriers.some(c => c.name === form.carrier) && (
                  <option value={form.carrier}>{form.carrier} (not in your carrier list)</option>
                )}
              </select>
            </div>
            <div>
              <label style={label}>Label (optional)</label>
              <input value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="what the carrier calls it" style={{ ...input, width: 210 }} />
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
        <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>
          No dealer codes yet — use <b>Pull from reports</b> above to take them from your carrier data,
          or add them by hand.
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--surface2)' }}>
                <th style={th}>Code</th><th style={th}>Label</th><th style={th}>Carrier</th><th style={th}>Store</th><th style={th}>Status</th><th style={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {dealerCodes.map(dc => (
                <tr key={dc.id} style={{ opacity: dc.is_active ? 1 : 0.5 }}>
                  <td style={{ ...td, fontWeight: 600, color: '#2980b9' }}>{dc.code}</td>
                  <td style={{ ...td, color: 'var(--text2)' }}>{dc.description || '—'}</td>
                  <td style={td}>{dc.carrier || '—'}</td>
                  <td style={{ ...td, color: dc.store_code ? 'var(--text)' : '#f39c12' }}>{storeName(dc.store_code)}</td>
                  <td style={td}><span style={{ color: dc.is_active ? '#16a34a' : '#dc2626', fontWeight: 600 }}>{dc.is_active ? 'Active' : 'Inactive'}</span></td>
                  <td style={td}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }}
                        onClick={() => { setError(''); setForm({ id: dc.id, code: dc.code, carrier: dc.carrier || '', store_code: dc.store_code || '', description: dc.description || '', is_active: dc.is_active }) }}>Edit</button>
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
