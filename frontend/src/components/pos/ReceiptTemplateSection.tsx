'use client'
// POS module — Phase 1: Receipt Template editor (ported from the standalone pos-system
// app's app/settings/page.tsx receipt-templates section). MetricsPro keeps a single
// org-default template (auto-created by GET /api/v1/pos/receipt-template), so the
// per-carrier list and the sample-image upload (storage bucket not ported) are skipped.
// The live thermal-style preview is ported as-is.
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { friendlyError } from '@/components/pos/PosConfigSection'

interface ReceiptTemplate {
  id: string
  name: string
  header_text: string | null
  footer_text: string | null
  show_store_name: boolean
  show_customer: boolean
  show_employee: boolean
  show_serials: boolean
  show_tax_breakdown: boolean
  show_discounts: boolean
  paper_width_mm: number
  font_size_px: number
}

interface ReceiptForm {
  name: string
  header_text: string
  footer_text: string
  show_store_name: boolean
  show_customer: boolean
  show_employee: boolean
  show_serials: boolean
  show_tax_breakdown: boolean
  show_discounts: boolean
  paper_width_mm: number
  font_size_px: number
}

function toForm(t: ReceiptTemplate): ReceiptForm {
  return {
    name: t.name || '',
    header_text: t.header_text || '',
    footer_text: t.footer_text || '',
    show_store_name: t.show_store_name,
    show_customer: t.show_customer,
    show_employee: t.show_employee,
    show_serials: t.show_serials,
    show_tax_breakdown: t.show_tax_breakdown,
    show_discounts: t.show_discounts,
    paper_width_mm: t.paper_width_mm === 58 ? 58 : 80,
    font_size_px: t.font_size_px || 11,
  }
}

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', outline: 'none' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const errorBox: React.CSSProperties = { margin: '12px 16px', border: '1px solid #dc2626', color: '#dc2626', borderRadius: 8, padding: '10px 14px', fontSize: 12 }

export default function ReceiptTemplateSection() {
  const [template, setTemplate] = useState<ReceiptTemplate | null>(null)
  const [form, setForm] = useState<ReceiptForm | null>(null)
  const [loading, setLoading] = useState(true)
  const [rtError, setRtError] = useState('')
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState('')

  useEffect(() => {
    (async () => {
      try {
        const r = await api('/api/v1/pos/receipt-template')
        const t = r.template as ReceiptTemplate
        setTemplate(t)
        setForm(toForm(t))
      } catch (err) {
        setRtError(friendlyError(err, 'Could not load the receipt template'))
      }
      setLoading(false)
    })()
  }, [])

  const dirty = !!template && !!form && JSON.stringify(form) !== JSON.stringify(toForm(template))

  async function saveTemplate() {
    if (!template || !form) return
    const name = form.name.trim()
    if (!name) { setRtError('Template name is required.'); return }
    const fontSize = Math.round(Number(form.font_size_px))
    if (!Number.isFinite(fontSize) || fontSize < 9 || fontSize > 16) { setRtError('Font size must be between 9 and 16 px.'); return }
    setSaving(true)
    setRtError('')
    setSavedAt('')
    try {
      const payload = {
        name,
        header_text: form.header_text.trim() || null,
        footer_text: form.footer_text.trim() || null,
        show_store_name: form.show_store_name,
        show_customer: form.show_customer,
        show_employee: form.show_employee,
        show_serials: form.show_serials,
        show_tax_breakdown: form.show_tax_breakdown,
        show_discounts: form.show_discounts,
        paper_width_mm: form.paper_width_mm === 58 ? 58 : 80,
        font_size_px: fontSize,
      }
      const r = await api(`/api/v1/pos/receipt-template/${template.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
      const t = r.template as ReceiptTemplate
      setTemplate(t)
      setForm(toForm(t))
      setSavedAt(new Date().toLocaleTimeString())
    } catch (err) {
      setRtError(friendlyError(err, 'Could not save the receipt template'))
    } finally {
      setSaving(false)
    }
  }

  function cancelEdits() {
    if (template) setForm(toForm(template))
    setRtError('')
  }

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, marginBottom: 16, overflow: 'hidden' }}>
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>🧾 Receipt Template</div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>The org&apos;s default receipt format — tune the settings until the preview matches your printer</div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {saving && <span style={{ fontSize: 12, color: '#f39c12' }}>saving…</span>}
          {!saving && savedAt && <span style={{ fontSize: 12, color: '#16a34a' }}>saved {savedAt}</span>}
          {dirty && <button className="btn btn-secondary" onClick={cancelEdits} disabled={saving}>Cancel</button>}
          <button className="btn btn-primary" onClick={saveTemplate} disabled={saving || !dirty || !form}>{saving ? 'Saving…' : 'Save Template'}</button>
        </div>
      </div>

      {rtError && <div style={errorBox}>{rtError}</div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>
      ) : !form ? (
        !rtError && <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>The receipt template could not be loaded.</div>
      ) : (
        <div style={{ padding: 16, display: 'flex', gap: 20, flexWrap: 'wrap' }}>

          {/* FORM */}
          <div style={{ flex: '1 1 300px', minWidth: 280 }}>
            <div style={{ marginBottom: 10 }}>
              <label style={label}>Template Name</label>
              <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="e.g. Default" style={{ ...input, width: '100%', boxSizing: 'border-box' }} />
            </div>
            <div style={{ marginBottom: 10 }}>
              <label style={label}>Header Text (printed above the store name)</label>
              <textarea value={form.header_text} onChange={e => setForm({ ...form, header_text: e.target.value })} rows={3}
                placeholder={'e.g. Authorized Verizon Retailer\nTCC'} style={{ ...input, width: '100%', boxSizing: 'border-box', resize: 'vertical', fontFamily: 'inherit' }} />
            </div>
            <div style={{ marginBottom: 10 }}>
              <label style={label}>Footer Text (printed at the bottom)</label>
              <textarea value={form.footer_text} onChange={e => setForm({ ...form, footer_text: e.target.value })} rows={3}
                placeholder="Return policy, thank-you note…" style={{ ...input, width: '100%', boxSizing: 'border-box', resize: 'vertical', fontFamily: 'inherit' }} />
            </div>
            <div style={{ marginBottom: 10, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
              {([
                ['show_store_name', 'Store name'],
                ['show_customer', 'Customer'],
                ['show_employee', 'Employee / rep'],
                ['show_serials', 'Serial numbers'],
                ['show_tax_breakdown', 'Tax breakdown'],
                ['show_discounts', 'Discounts'],
              ] as const).map(([key, lbl]) => (
                <label key={key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                  <input type="checkbox" checked={form[key]} onChange={e => setForm({ ...form, [key]: e.target.checked })} />
                  {lbl}
                </label>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 10 }}>
              <div>
                <label style={label}>Paper Width</label>
                <select value={form.paper_width_mm} onChange={e => setForm({ ...form, paper_width_mm: Number(e.target.value) })} style={{ ...input, width: 120 }}>
                  <option value={58}>58 mm</option>
                  <option value={80}>80 mm</option>
                </select>
              </div>
              <div>
                <label style={label}>Font Size (9–16 px)</label>
                <input type="number" min={9} max={16} value={form.font_size_px}
                  onChange={e => setForm({ ...form, font_size_px: Number(e.target.value) })} style={{ ...input, width: 90 }} />
              </div>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.5 }}>
              💡 Receipt branding (company name, address lines, disclaimers) comes from POS Configuration above — this template controls the layout.
            </div>
          </div>

          {/* LIVE PREVIEW */}
          <div style={{ flex: '0 0 auto' }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text2)', marginBottom: 6, textTransform: 'uppercase' }}>Live Preview ({form.paper_width_mm}mm)</div>
            <div style={{
              width: form.paper_width_mm === 58 ? 220 : 300,
              background: '#fdfdf8', color: '#111', borderRadius: 4, padding: '12px 10px',
              fontFamily: "'Courier New', monospace", fontSize: Math.min(16, Math.max(9, form.font_size_px || 11)),
              lineHeight: 1.35, boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
            }}>
              {form.header_text.trim() && (
                <div style={{ textAlign: 'center', whiteSpace: 'pre-wrap' }}>{form.header_text.trim()}</div>
              )}
              {form.show_store_name && (
                <div style={{ textAlign: 'center', fontWeight: 700 }}>
                  VERIZON @ 28TH ST TCC
                  <div style={{ fontWeight: 400 }}>2814 28th St, Queens NY</div>
                </div>
              )}
              <div style={{ borderTop: '1px dashed #555', margin: '6px 0' }} />
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>08/06/2026 2:41 PM</span><span>#4821</span></div>
              {form.show_customer && <div>Customer: John Smith</div>}
              {form.show_employee && <div>Rep: Sanjot S.</div>}
              <div style={{ borderTop: '1px dashed #555', margin: '6px 0' }} />
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>iPhone 15 Pro 128GB</span><span>999.00</span></div>
              {form.show_serials && <div style={{ paddingLeft: 8 }}>IMEI 358291470012345</div>}
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Clear Case</span><span>19.99</span></div>
              {form.show_discounts && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Promo Discount</span><span>-50.00</span></div>
              )}
              <div style={{ borderTop: '1px dashed #555', margin: '6px 0' }} />
              {form.show_tax_breakdown && (
                <>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Subtotal</span><span>{form.show_discounts ? '968.99' : '1018.99'}</span></div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Tax (8.875%)</span><span>{form.show_discounts ? '86.00' : '90.44'}</span></div>
                </>
              )}
              <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: 700 }}>
                <span>TOTAL</span><span>{form.show_discounts ? '1054.99' : '1109.43'}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>VISA ****4242</span><span>{form.show_discounts ? '1054.99' : '1109.43'}</span></div>
              <div style={{ borderTop: '1px dashed #555', margin: '6px 0' }} />
              {form.footer_text.trim() && (
                <div style={{ textAlign: 'center', whiteSpace: 'pre-wrap' }}>{form.footer_text.trim()}</div>
              )}
            </div>
          </div>

        </div>
      )}
    </div>
  )
}
