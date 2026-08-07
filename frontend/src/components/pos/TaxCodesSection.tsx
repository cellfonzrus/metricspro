'use client'
// POS module — Phase 1: Sales Tax section (ported from the standalone pos-system app's
// app/settings/page.tsx). Tax codes rewired from direct Supabase to the FastAPI /pos
// router (store dimension is store_code TEXT, null = org-wide fallback). The tax rule —
// org_settings.tax_applied_on in the standalone app — is now the `tax_applied_on` key
// in the pos_settings config engine, saved here at org scope for one-click parity.
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { resolvePosConfig } from '@/lib/pos-config'
import type { PosSettingRow } from '@/lib/pos-config'
import { friendlyError, storeLabel } from '@/components/pos/PosConfigSection'
import type { PosStore } from '@/components/pos/PosConfigSection'

interface TaxCode { id: string; name: string; rate: number; store_code: string | null; is_active: boolean; created_at?: string }
interface TaxCodeForm { id: string | null; name: string; rate: string; store_code: string; is_active: boolean }

type TaxRule = 'pre_discount' | 'post_discount'

const emptyTaxForm: TaxCodeForm = { id: null, name: '', rate: '', store_code: '', is_active: true }

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', outline: 'none' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const th: React.CSSProperties = { textAlign: 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '8px 14px', fontSize: 13, borderBottom: '1px solid var(--border)' }
const errorBox: React.CSSProperties = { margin: '12px 16px', border: '1px solid #dc2626', color: '#dc2626', borderRadius: 8, padding: '10px 14px', fontSize: 12 }

interface Props {
  stores: PosStore[]
  /** All pos_settings rows (owned by the page) — used to read the effective org tax rule. */
  rows: PosSettingRow[]
  /** Called after the tax rule is saved so the page re-fetches settings rows (keeps the config engine coherent). */
  onSettingsChanged: () => Promise<void>
}

export default function TaxCodesSection({ stores, rows, onSettingsChanged }: Props) {
  const [taxCodes, setTaxCodes] = useState<TaxCode[]>([])
  const [loading, setLoading] = useState(true)
  const [taxForm, setTaxForm] = useState<TaxCodeForm | null>(null)
  const [taxError, setTaxError] = useState('')
  const [taxSaving, setTaxSaving] = useState(false)

  // Tax rule (org-scope pos setting) — optimistic while the PUT + reload are in flight.
  const [pendingRule, setPendingRule] = useState<TaxRule | null>(null)
  const [ruleSaving, setRuleSaving] = useState(false)
  const [ruleError, setRuleError] = useState('')
  const [ruleSavedAt, setRuleSavedAt] = useState('')

  const savedRule = String(resolvePosConfig(rows, null).values.tax_applied_on) as TaxRule
  const taxRule: TaxRule = pendingRule ?? savedRule

  useEffect(() => {
    loadTaxCodes().then(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function loadTaxCodes() {
    try {
      const r = await api('/api/v1/pos/tax-codes')
      setTaxCodes((r.tax_codes || []) as TaxCode[])
    } catch (err) {
      setTaxError(friendlyError(err, 'Could not load tax codes'))
    }
  }

  async function saveTaxRule(rule: TaxRule) {
    if (ruleSaving || rule === taxRule) return
    setPendingRule(rule)
    setRuleSaving(true)
    setRuleError('')
    setRuleSavedAt('')
    try {
      await api('/api/v1/pos/settings', {
        method: 'PUT',
        body: JSON.stringify({ key: 'tax_applied_on', value: rule, store_code: null }),
      })
      await onSettingsChanged()
      setRuleSavedAt(new Date().toLocaleTimeString())
    } catch (err) {
      setRuleError(friendlyError(err, 'Could not save tax rule'))
    } finally {
      setPendingRule(null)
      setRuleSaving(false)
    }
  }

  async function saveTaxCode() {
    if (!taxForm) return
    const name = taxForm.name.trim()
    const rate = Number(taxForm.rate)
    if (!name) { setTaxError('Tax code name is required.'); return }
    if (taxForm.rate.trim() === '' || !Number.isFinite(rate)) { setTaxError('Rate must be a number (percent, e.g. 8.875).'); return }
    if (rate < 0 || rate > 30) { setTaxError('Rate must be between 0 and 30 percent.'); return }
    setTaxSaving(true)
    setTaxError('')
    try {
      const payload = { name, rate, store_code: taxForm.store_code || null, is_active: taxForm.is_active }
      if (taxForm.id) await api(`/api/v1/pos/tax-codes/${taxForm.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
      else await api('/api/v1/pos/tax-codes', { method: 'POST', body: JSON.stringify(payload) })
      setTaxForm(null)
      await loadTaxCodes()
    } catch (err) {
      setTaxError(friendlyError(err, 'Could not save tax code'))
    } finally {
      setTaxSaving(false)
    }
  }

  async function toggleTaxCode(tc: TaxCode) {
    if (tc.is_active && !confirm(`Deactivate tax code "${tc.name}"? Registers will stop using it.`)) return
    setTaxError('')
    try {
      await api(`/api/v1/pos/tax-codes/${tc.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !tc.is_active }) })
      await loadTaxCodes()
    } catch (err) {
      setTaxError(friendlyError(err, 'Could not update tax code'))
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
          <div style={{ fontSize: 15, fontWeight: 700 }}>💵 Sales Tax</div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>Tax calculation rule and per-store tax codes</div>
        </div>
        {!taxForm && (
          <button className="btn btn-primary" onClick={() => { setTaxError(''); setTaxForm({ ...emptyTaxForm }) }}>+ Add Tax Code</button>
        )}
      </div>

      {/* Org rule */}
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 8, textTransform: 'uppercase' }}>
          Discounted sales — what gets taxed
          {ruleSaving && <span style={{ color: '#f39c12', fontWeight: 400, marginLeft: 10, textTransform: 'none' }}>saving…</span>}
          {!ruleSaving && ruleSavedAt && <span style={{ color: '#16a34a', fontWeight: 400, marginLeft: 10, textTransform: 'none' }}>saved {ruleSavedAt}</span>}
        </div>
        {([
          { value: 'pre_discount' as TaxRule, label: 'Tax on price BEFORE discount', hint: 'Tax is computed on the original price, then the discount is applied.' },
          { value: 'post_discount' as TaxRule, label: 'Tax on price AFTER discount', hint: 'Tax is computed on the discounted price (most states). Default.' },
        ]).map(opt => (
          <label key={opt.value} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '6px 0', cursor: ruleSaving ? 'wait' : 'pointer' }}>
            <input type="radio" name="pos_tax_rule" checked={taxRule === opt.value} disabled={ruleSaving}
              onChange={() => saveTaxRule(opt.value)} style={{ marginTop: 2 }} />
            <span>
              <span style={{ fontSize: 13, fontWeight: taxRule === opt.value ? 700 : 400 }}>{opt.label}</span>
              <span style={{ display: 'block', fontSize: 12, color: 'var(--text2)' }}>{opt.hint}</span>
            </span>
          </label>
        ))}
        <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 6 }}>
          This rule is state-dependent — check the regulations for the states you operate in. Saved as the org-wide
          “Tax is applied on” setting (stores can override it in POS Configuration → Tax Calculation Rules).
        </div>
        {ruleError && <div style={{ ...errorBox, margin: '10px 0 0' }}>{ruleError}</div>}
      </div>

      {/* Tax code form */}
      {taxForm && (
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 10 }}>{taxForm.id ? 'Edit Tax Code' : 'New Tax Code'}</div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <label style={label}>Name</label>
              <input value={taxForm.name} onChange={e => setTaxForm({ ...taxForm, name: e.target.value })} placeholder="e.g. NY State + NYC" style={{ ...input, width: 200 }} />
            </div>
            <div>
              <label style={label}>Rate (%)</label>
              <input value={taxForm.rate} onChange={e => setTaxForm({ ...taxForm, rate: e.target.value })} placeholder="8.875" inputMode="decimal" style={{ ...input, width: 100 }} />
            </div>
            <div>
              <label style={label}>Store</label>
              <select value={taxForm.store_code} onChange={e => setTaxForm({ ...taxForm, store_code: e.target.value })} style={{ ...input, width: 220 }}>
                <option value="">All stores (org-wide default)</option>
                {stores.map(s => <option key={s.store_code} value={s.store_code}>{storeLabel(s)}</option>)}
              </select>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, paddingBottom: 8, cursor: 'pointer' }}>
              <input type="checkbox" checked={taxForm.is_active} onChange={e => setTaxForm({ ...taxForm, is_active: e.target.checked })} />
              Active
            </label>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-primary" onClick={saveTaxCode} disabled={taxSaving}>{taxSaving ? 'Saving…' : 'Save'}</button>
              <button className="btn btn-secondary" onClick={() => { setTaxForm(null); setTaxError('') }}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {taxError && <div style={errorBox}>{taxError}</div>}

      {/* Tax codes table */}
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 30 }}><div className="spinner" /></div>
      ) : taxCodes.length === 0 ? (
        <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>No tax codes yet — add one so registers can charge sales tax.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--surface2)' }}>
                <th style={th}>Name</th><th style={th}>Rate</th><th style={th}>Store</th><th style={th}>Status</th><th style={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {taxCodes.map(tc => (
                <tr key={tc.id} style={{ opacity: tc.is_active ? 1 : 0.5 }}>
                  <td style={{ ...td, fontWeight: 600 }}>{tc.name}</td>
                  <td style={{ ...td, color: '#16a34a', fontWeight: 700 }}>{Number(tc.rate).toFixed(3).replace(/\.?0+$/, '')}%</td>
                  <td style={{ ...td, color: tc.store_code ? 'var(--text)' : '#f39c12' }}>{storeName(tc.store_code)}</td>
                  <td style={td}><span style={{ color: tc.is_active ? '#16a34a' : '#dc2626', fontWeight: 600 }}>{tc.is_active ? 'Active' : 'Inactive'}</span></td>
                  <td style={td}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }}
                        onClick={() => { setTaxError(''); setTaxForm({ id: tc.id, name: tc.name, rate: String(tc.rate), store_code: tc.store_code || '', is_active: tc.is_active }) }}>Edit</button>
                      <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px', color: tc.is_active ? '#dc2626' : '#16a34a' }}
                        onClick={() => toggleTaxCode(tc)}>{tc.is_active ? 'Deactivate' : 'Reactivate'}</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)' }}>
        💡 The register uses the active store&apos;s tax code; if that store has none, it falls back to the org-wide default (&quot;All stores&quot;).
      </div>
    </div>
  )
}
