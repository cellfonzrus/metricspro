'use client'
// POS module — the admin's Receipt Template editor. MetricsPro keeps a single org-default template
// (auto-created by GET /api/v1/pos/receipt-template), so the per-carrier list and the sample-image
// upload are skipped.
//
// THE PREVIEW IS THE REAL RENDERER (§POS-02, owner 2026-10-17). It used to be a hand-drawn imitation
// of a till slip in this file — with a store's name hard-coded into it — so an admin could not tell
// from it whether a change had worked, and a tenant printing in its POS's own layout saw a preview of
// something else entirely. It now POSTs the template being edited to /pos/receipt-template/preview
// and shows what the ONE renderer returns for a sample sale.
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { friendlyError } from '@/components/pos/PosConfigSection'
import { SetupNotice } from '@/lib/setupNotice'

interface ReceiptTemplate {
  id: string
  name: string
  layout?: string | null
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

interface LayoutOption { value: string; label: string }

interface ReceiptForm {
  name: string
  layout: string
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
    layout: t.layout || 'thermal',
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
  // What the server says about this org: the layouts it may choose (the second one naming its own
  // POS), a sentence when the stored choice is not what will print, and the name a customer sees a
  // receipt come FROM — so the admin can see onboarding named the business.
  const [layoutOptions, setLayoutOptions] = useState<LayoutOption[]>([])
  const [layoutNote, setLayoutNote] = useState('')
  const [fromName, setFromName] = useState('')
  const [fromNameSource, setFromNameSource] = useState('')
  const [layoutReady, setLayoutReady] = useState(true)
  const [previewHtml, setPreviewHtml] = useState('')

  useEffect(() => {
    (async () => {
      try {
        const r = await api('/api/v1/pos/receipt-template')
        const t = r.template as ReceiptTemplate
        setTemplate(t)
        setForm(toForm(t))
        setLayoutOptions((r.layout_options || []) as LayoutOption[])
        setLayoutNote(String(r.layout_note || ''))
        setFromName(String(r.from_name || ''))
        setFromNameSource(String(r.from_name_source || ''))
        setLayoutReady(r.layout_column_ready !== false)
      } catch (err) {
        setRtError(friendlyError(err, 'Could not load the receipt template'))
      }
      setLoading(false)
    })()
  }, [])

  const dirty = !!template && !!form && JSON.stringify(form) !== JSON.stringify(toForm(template))

  // The preview follows the draft, debounced so typing a footer is one render and not one per key.
  useEffect(() => {
    if (!form) return
    const t = setTimeout(() => {
      api('/api/v1/pos/receipt-template/preview', {
        method: 'POST',
        body: JSON.stringify({
          template: {
            name: form.name,
            header_text: form.header_text,
            footer_text: form.footer_text,
            show_store_name: form.show_store_name,
            show_customer: form.show_customer,
            show_employee: form.show_employee,
            show_serials: form.show_serials,
            show_tax_breakdown: form.show_tax_breakdown,
            show_discounts: form.show_discounts,
            paper_width_mm: form.paper_width_mm,
            font_size_px: form.font_size_px,
            layout: form.layout,
          },
        }),
      })
        .then(r => { setPreviewHtml(String(r?.html || '')); setLayoutNote(String(r?.layout_note || '')) })
        .catch(() => setPreviewHtml(''))
    }, 400)
    return () => clearTimeout(t)
  }, [form])

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
        // Only sent when the column exists, so a tenant that has not applied migration 1089 can
        // still save everything else rather than being blocked by a field it cannot store.
        ...(layoutReady ? { layout: form.layout } : {}),
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
              <label style={label}>Receipt layout</label>
              <select value={form.layout} onChange={e => setForm({ ...form, layout: e.target.value })}
                disabled={!layoutReady} style={{ ...input, width: '100%', boxSizing: 'border-box' }}>
                {(layoutOptions.length ? layoutOptions : [{ value: 'thermal', label: 'Till slip (58 / 80 mm thermal)' }])
                  .map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              {!layoutReady && (
                // §19.36: the migration name is the PLATFORM's detail, so it rides the one sanctioned
                // carrier (`detail=`) and a tenant reads the plain sentence instead.
                <div style={{ fontSize: 11, color: 'var(--amber)', marginTop: 4, lineHeight: 1.5 }}>
                  <SetupNotice lead="The layout choice is not available yet; everything else on this form saves normally."
                               detail="1089_pos_receipt_layout.sql" />
                </div>
              )}
              {layoutNote && (
                <div style={{ fontSize: 11, color: 'var(--amber)', marginTop: 4, lineHeight: 1.5 }}>{layoutNote}</div>
              )}
            </div>
            <div style={{ marginBottom: 10, fontSize: 12, color: 'var(--text2)', lineHeight: 1.5 }}>
              Emailed receipts come from <strong style={{ color: 'var(--text)' }}>{fromName || 'the platform name'}</strong>
              {fromNameSource === 'onboarding' && ' — the company name set up during onboarding.'}
              {fromNameSource === 'receipt branding' && ' — the company name set in POS Configuration.'}
              {fromNameSource === 'not set' && ' — no company name is set yet, so set one in POS Configuration above.'}
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

          {/* LIVE PREVIEW — the real renderer's output for a sample sale, not an imitation. */}
          <div style={{ flex: '0 0 auto' }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text2)', marginBottom: 6, textTransform: 'uppercase' }}>
              Live preview {form.layout === 'thermal' ? `(${form.paper_width_mm}mm)` : '(your POS\u2019s layout)'}
            </div>
            {previewHtml ? (
              <iframe title="Receipt preview" srcDoc={previewHtml}
                style={{
                  width: form.layout === 'thermal' ? (form.paper_width_mm === 58 ? 236 : 316) : 640,
                  height: form.layout === 'thermal' ? 520 : 700,
                  background: '#fdfdf8', border: 'none', borderRadius: 4,
                  boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
                }} />
            ) : (
              <div style={{
                width: form.paper_width_mm === 58 ? 236 : 316, height: 200, borderRadius: 4,
                border: '1px dashed var(--border)', display: 'flex', alignItems: 'center',
                justifyContent: 'center', color: 'var(--text3)', fontSize: 12, textAlign: 'center', padding: 12,
              }}>
                The preview will appear here once the receipt renders.
              </div>
            )}
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6, maxWidth: 316, lineHeight: 1.5 }}>
              A sample sale, printed exactly as the register prints a real one.
            </div>
          </div>

        </div>
      )}
    </div>
  )
}
