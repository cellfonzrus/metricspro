'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api, apiUpload } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { usePeriod } from '@/lib/period-context'

// Commission Import Wizard (SAP-style, self-extending). Upload ANY carrier's commission file, then tell the
// system which column is which category — 1st/2nd/3rd-month commission, spiff, rebate, residual, margins, a
// bounty, anything. If the file has a category we don't have yet, create it right here: the system adds a real
// column to commcalc.carrier_commission on the fly (no SQL) and remembers the mapping as a reusable template.
// Engine: GET/POST /commission-fields + POST /commission-import/{analyze,commit}. carrier_commission only —
// the live Boost calc is never touched.

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const IGNORE = '__ignore__'

type Field = { target_field: string; label: string; kind: string; is_amount?: boolean; source?: string }
type NewField = { tempId: string; label: string; kind: string; data_type: string; is_amount: boolean; month_index: number | null }

const KIND_LABEL: Record<string, string> = {
  identity: 'Identity (rep / store / date …)', comm_month: 'Monthly commission', spiff: 'Spiff',
  rebate: 'Rebate', residual: 'Residual', margin: 'Margin', fee: 'Fee', bounty: 'Bounty', other: 'Other',
}

export default function CommissionImportWizard() {
  const { period, setPeriod, periods } = usePeriod()
  const [carriers, setCarriers] = useState<any[]>([])
  const [carrierId, setCarrierId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [step, setStep] = useState<1 | 2>(1)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const [headers, setHeaders] = useState<string[]>([])
  const [samples, setSamples] = useState<Record<string, string[]>>({})
  const [rowCount, setRowCount] = useState(0)
  const [fields, setFields] = useState<Field[]>([])          // catalog (defaults + tenant), from analyze
  const [newFields, setNewFields] = useState<NewField[]>([]) // categories created this session
  const [mapping, setMapping] = useState<Record<string, string>>({}) // header -> target_field | IGNORE | tempId

  // new-category mini-form
  const [nfOpen, setNfOpen] = useState(false)
  const [nf, setNf] = useState<NewField>({ tempId: '', label: '', kind: 'spiff', data_type: 'number', is_amount: true, month_index: null })

  useEffect(() => { apiCached('/api/v1/commcalc/carriers', LOOKUP).then((r: any) => setCarriers(r || [])).catch(() => {}) }, [])

  async function analyze(f: File) {
    setBusy(true); setMsg(''); setFile(f)
    try {
      const fd = new FormData(); fd.append('file', f); fd.append('report_key', 'carrier_commission')
      if (carrierId) fd.append('carrier_id', carrierId)
      const r: any = await apiUpload('/api/v1/commcalc/commission-import/analyze', fd)
      setHeaders(r.headers || []); setSamples(r.samples || {}); setRowCount(r.row_count || 0)
      setFields(r.fields || []); setNewFields([])
      // prefill: saved template first, then auto-suggestions (header -> target_field)
      const m: Record<string, string> = {}
      for (const [tf, src] of Object.entries(r.saved_mapping || {})) { if (src) m[String(src)] = tf }
      for (const s of (r.suggestions || [])) { if (s.suggested_source && !m[s.suggested_source]) m[s.suggested_source] = s.target_field }
      setMapping(m); setStep(2)
      setMsg(`Read ${r.row_count} rows × ${(r.headers || []).length} columns.`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  function addNewCategory() {
    const label = nf.label.trim()
    if (!label) { setMsg('❌ Give the new category a name.'); return }
    const tempId = 'new::' + label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
    if (newFields.some(x => x.tempId === tempId) || fields.some(f => f.target_field === tempId.replace('new::', ''))) {
      setMsg('❌ A category with that name already exists.'); return
    }
    setNewFields([...newFields, { ...nf, label, tempId }])
    setNfOpen(false); setNf({ tempId: '', label: '', kind: 'spiff', data_type: 'number', is_amount: true, month_index: null })
    setMsg(`➕ Added category “${label}” — now pick it for the right column below.`)
  }

  async function commit() {
    if (!file) { setMsg('❌ Re-upload the file first.'); return }
    // which session-new categories are actually used?
    const usedNew = newFields.filter(n => Object.values(mapping).includes(n.tempId))
    const newPayload = usedNew.map(n => ({
      label: n.label, kind: n.kind, data_type: n.data_type, is_amount: n.is_amount,
      month_index: n.month_index, target_field: n.tempId.replace('new::', ''),
    }))
    const maps = Object.entries(mapping)
      .filter(([, tf]) => tf && tf !== IGNORE)
      .map(([src, tf]) => {
        const isNew = tf.startsWith('new::')
        const targetField = isNew ? tf.replace('new::', '') : tf
        const fdef = fields.find(f => f.target_field === tf)
        const ndef = newFields.find(n => n.tempId === tf)
        const transform = ndef ? (ndef.data_type === 'number' ? 'number' : ndef.data_type) : fdefTransform(fdef)
        return { source_header: src, target_field: targetField, transform }
      })
    if (!maps.length) { setMsg('❌ Map at least one column to a category.'); return }
    setBusy(true); setMsg('')
    try {
      const fd = new FormData()
      fd.append('file', file); fd.append('report_key', 'carrier_commission'); fd.append('period', period)
      if (carrierId) fd.append('carrier_id', carrierId)
      fd.append('new_fields', JSON.stringify(newPayload)); fd.append('mappings', JSON.stringify(maps))
      fd.append('save_template', 'true')
      const r: any = await apiUpload('/api/v1/commcalc/commission-import/commit', fd)
      const nc = (r.new_categories || []).length
      setMsg(`✅ Loaded ${r.saved} rows into carrier_commission for ${period}.` +
             (nc ? ` Created ${nc} new categor${nc === 1 ? 'y' : 'ies'}.` : '') +
             ` Saved a reusable template (${r.template_rows} columns).`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  function fdefTransform(f?: Field): string {
    if (!f) return 'text'
    if (f.kind === 'identity') return f.target_field.includes('date') ? 'date10' : (['imei', 'mdn'].includes(f.target_field) ? 'mdn' : 'text')
    return 'number'
  }

  // dropdown options: catalog fields + session-new, grouped by kind
  const allOptions: { value: string; label: string; kind: string }[] = [
    ...fields.map(f => ({ value: f.target_field, label: f.label + (f.is_amount ? ' 💲' : ''), kind: f.kind || 'other' })),
    ...newFields.map(n => ({ value: n.tempId, label: '🆕 ' + n.label + (n.is_amount ? ' 💲' : ''), kind: n.kind })),
  ]
  const kindsOrder = ['identity', 'comm_month', 'spiff', 'rebate', 'residual', 'margin', 'fee', 'bounty', 'other']
  const mappedCount = Object.values(mapping).filter(v => v && v !== IGNORE).length

  return (
    <div style={{ maxWidth: 1000 }}>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🪄 Commission Import Wizard</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Upload <b>any</b> carrier&apos;s commission file and map each column to a category. Missing a category
          (a 7th-month spiff, a new bounty)? Create it here — the system adds the column automatically and
          remembers your mapping for next time. Loads into <code>carrier_commission</code>; the live Boost calc is
          never touched. Need to eyeball the raw file first? <Link href="/commcalc/carrier-comm-file">Carrier Comm File → Table</Link>.
        </p>
      </div>

      {/* controls */}
      <div className="card" style={{ padding: 16, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <select style={sel} value={period} onChange={e => setPeriod(e.target.value)}>
          {periods.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <select style={sel} value={carrierId} onChange={e => setCarrierId(e.target.value)}>
          <option value="">All carriers (global template)</option>
          {carriers.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <label className="btn btn-primary" style={{ cursor: 'pointer', margin: 0 }}>
          {busy ? '⏳ Working…' : (step === 1 ? '⬆️ Upload commission file' : '🔁 Upload a different file')}
          <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} disabled={busy}
            onChange={e => { const f = e.target.files?.[0]; if (f) analyze(f); e.currentTarget.value = '' }} />
        </label>
        {file && <span style={{ fontSize: 12, color: 'var(--text3)' }}>{file.name}</span>}
        {msg && <span style={{ fontSize: 13, marginLeft: 'auto', maxWidth: 460 }}>{msg}</span>}
      </div>

      {step === 2 && (
        <>
          <div className="card" style={{ padding: 12, marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <b style={{ fontSize: 14 }}>{headers.length} columns · {rowCount} rows · {mappedCount} mapped</b>
            <button className="btn btn-secondary" style={{ marginLeft: 'auto' }} onClick={() => setNfOpen(o => !o)}>
              {nfOpen ? '✕ Close' : '➕ New category'}
            </button>
            <button className="btn btn-primary" disabled={busy || !mappedCount} onClick={commit}>✅ Map & Load</button>
          </div>

          {nfOpen && (
            <div className="card" style={{ padding: 14, marginBottom: 12, display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap', background: 'var(--surface2)' }}>
              <div><div style={lbl}>Category name</div><input style={sel} value={nf.label} placeholder="e.g. New Activation Bounty" onChange={e => setNf({ ...nf, label: e.target.value })} /></div>
              <div><div style={lbl}>Kind</div>
                <select style={sel} value={nf.kind} onChange={e => setNf({ ...nf, kind: e.target.value, is_amount: e.target.value !== 'identity' })}>
                  {kindsOrder.map(k => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
                </select>
              </div>
              <div><div style={lbl}>Data type</div>
                <select style={sel} value={nf.data_type} onChange={e => setNf({ ...nf, data_type: e.target.value })}>
                  <option value="number">Number (amount)</option><option value="text">Text</option>
                  <option value="int">Integer</option><option value="date10">Date</option>
                </select>
              </div>
              {(nf.kind === 'comm_month' || nf.kind === 'spiff') && (
                <div><div style={lbl}>Payout month #</div>
                  <input style={{ ...sel, width: 70 }} type="number" min={1} value={nf.month_index ?? ''} onChange={e => setNf({ ...nf, month_index: e.target.value ? parseInt(e.target.value) : null })} /></div>
              )}
              <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13, paddingBottom: 6 }}>
                <input type="checkbox" checked={nf.is_amount} onChange={e => setNf({ ...nf, is_amount: e.target.checked })} />
                Counts toward total commission
              </label>
              <button className="btn btn-primary" onClick={addNewCategory}>Add</button>
            </div>
          )}

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', fontSize: 13, width: '100%' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                <th style={th}>Column in your file</th><th style={th}>Sample values</th><th style={th}>Maps to category</th>
              </tr></thead>
              <tbody>
                {headers.map(h => (
                  <tr key={h} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ ...td, fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</td>
                    <td style={{ ...td, color: 'var(--text3)', fontSize: 12, maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {(samples[h] || []).join(', ') || '—'}
                    </td>
                    <td style={td}>
                      <select style={{ ...sel, minWidth: 230, borderColor: mapping[h] && mapping[h] !== IGNORE ? 'var(--accent, #2563eb)' : 'var(--border)' }}
                        value={mapping[h] || ''} onChange={e => setMapping({ ...mapping, [h]: e.target.value })}>
                        <option value="">— choose —</option>
                        <option value={IGNORE}>🚫 Ignore this column</option>
                        {kindsOrder.filter(k => allOptions.some(o => o.kind === k)).map(k => (
                          <optgroup key={k} label={KIND_LABEL[k]}>
                            {allOptions.filter(o => o.kind === k).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                          </optgroup>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p style={{ color: 'var(--text3)', fontSize: 12, marginTop: 8 }}>
            💲 = amount column (summed into the rep&apos;s total statement commission). Unmapped columns are skipped.
            Your mapping is saved as a template for this carrier, so next month is one click.
          </p>
        </>
      )}
    </div>
  )
}

const lbl: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', marginBottom: 3, fontWeight: 600 }
const th: React.CSSProperties = { padding: '8px 12px', textAlign: 'left', fontWeight: 700, fontSize: 12, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '7px 12px', verticalAlign: 'middle' }
