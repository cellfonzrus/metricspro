'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'

// Custom Target Fields (C-Phase2) — define the canonical fields a report's columns map onto, for ANY
// report type. The built-in Boost reports (sales, comp_report, mi_report, payment_detail,
// carrier_commission) ship with defaults; this page lets a tenant ADD fields we never shipped (an
// expenses feed, a chart-of-accounts import, a product catalog), relabel a default, or add header
// aliases for better auto-detect — no code change. Backed by commcalc.target_field_registry (mig 070).
// NO schema DDL: a custom field on a built-in report is for relabel/alias/required; a brand-new field is
// meant for a NEW report type whose target_table you set on its report definition.

type Field = { target_field: string; label: string; transform: string; required?: boolean; default_source?: string; aliases?: string[]; source?: string }
const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const TRANSFORMS = ['text', 'number', 'int', 'date10', 'mdn', 'upper', 'lower', 'bool']

export default function TargetFieldsPage() {
  const [reportKeys, setReportKeys] = useState<string[]>([])
  const [reportKey, setReportKey] = useState('')
  const [fields, setFields] = useState<Field[]>([])
  const [ready, setReady] = useState(true)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')
  // new-field form
  const [nf, setNf] = useState({ label: '', transform: 'text', required: false, default_source: '', aliases: '', sort_order: '100' })

  async function loadKeys() {
    try {
      const d = await api('/api/v1/commcalc/target-fields')
      const keys: string[] = d?.report_keys || []
      setReportKeys(keys)
      setReady(d?.registry_ready !== false)
      if (!reportKey && keys.length) setReportKey(keys[0])
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
  }
  async function loadFields(rk: string) {
    if (!rk) return
    setLoading(true)
    try {
      const d = await api('/api/v1/commcalc/target-fields?report_key=' + encodeURIComponent(rk))
      setFields(d?.fields || [])
      setReady(d?.registry_ready !== false)
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
    setLoading(false)
  }
  useEffect(() => { loadKeys() }, [])           // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadFields(reportKey) }, [reportKey])  // eslint-disable-line react-hooks/exhaustive-deps

  function flash(m: string) { setMsg(m); setTimeout(() => setMsg(''), 3500) }

  async function addField() {
    const label = nf.label.trim()
    if (!reportKey) { flash('Pick a report first'); return }
    if (!label) { flash('Field label is required'); return }
    try {
      await api('/api/v1/commcalc/target-fields', { method: 'POST', body: JSON.stringify({
        report_key: reportKey, label, transform: nf.transform, required: nf.required,
        default_source: nf.default_source.trim(), aliases: nf.aliases, sort_order: Number(nf.sort_order) || 100,
      }) })
      flash(`Added "${label}"`)
      setNf({ label: '', transform: 'text', required: false, default_source: '', aliases: '', sort_order: '100' })
      loadFields(reportKey)
    } catch (e: any) { flash(e?.message || 'Save failed — is migration 070 applied?') }
  }
  async function removeField(tf: string) {
    try {
      await api(`/api/v1/commcalc/target-fields?report_key=${encodeURIComponent(reportKey)}&target_field=${encodeURIComponent(tf)}`, { method: 'DELETE' })
      flash(`Removed "${tf}"`)
      loadFields(reportKey)
    } catch (e: any) { flash(e?.message || 'Remove failed') }
  }

  // let the user introduce a brand-new report type by typing a key not in the list
  function addNewReportKey() {
    const k = prompt('New report key (lowercase, e.g. "expenses", "coa", "catalog"):')?.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
    if (!k) return
    if (!reportKeys.includes(k)) setReportKeys(p => [...p, k])
    setReportKey(k)
  }

  return (
    <div style={{ padding: 24, maxWidth: 960 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🧱 Custom Target Fields</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 8 }}>
        Define the canonical fields a report's columns map onto — for any report type. Built-in reports
        ship with defaults; add fields here to map a report we didn't ship, relabel a default, or add
        header aliases. These flow into the <b>Column Mapping</b> & <b>Import Wizard</b> auto-suggest.
      </p>
      {!ready && (
        <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>
          Run migration <code>070_target_field_registry.sql</code> to save custom fields. Until then the
          mapper uses the built-in defaults only.
        </div>
      )}
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Report</label>
        <select style={inp} value={reportKey} onChange={e => setReportKey(e.target.value)}>
          {reportKeys.map(k => <option key={k} value={k}>{k}</option>)}
        </select>
        <button onClick={addNewReportKey} style={{ ...inp, cursor: 'pointer' }}>＋ New report type</button>
      </div>

      {/* add-field form */}
      <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14, marginBottom: 18, background: 'var(--surface)' }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 10 }}>Add a custom field to <b>{reportKey || '—'}</b></div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div><div style={lbl}>Label *</div><input style={{ ...inp, width: 180 }} value={nf.label} placeholder="e.g. Vendor name" onChange={e => setNf({ ...nf, label: e.target.value })} /></div>
          <div><div style={lbl}>Type</div><select style={inp} value={nf.transform} onChange={e => setNf({ ...nf, transform: e.target.value })}>{TRANSFORMS.map(t => <option key={t} value={t}>{t}</option>)}</select></div>
          <div><div style={lbl}>Default source header</div><input style={{ ...inp, width: 160 }} value={nf.default_source} placeholder="exact header in the file" onChange={e => setNf({ ...nf, default_source: e.target.value })} /></div>
          <div><div style={lbl}>Aliases (comma-sep)</div><input style={{ ...inp, width: 180 }} value={nf.aliases} placeholder="synonym1, synonym2" onChange={e => setNf({ ...nf, aliases: e.target.value })} /></div>
          <div><div style={lbl}>Sort</div><input style={{ ...inp, width: 64 }} value={nf.sort_order} onChange={e => setNf({ ...nf, sort_order: e.target.value })} /></div>
          <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 5 }}><input type="checkbox" checked={nf.required} onChange={e => setNf({ ...nf, required: e.target.checked })} /> required</label>
          <button onClick={addField} style={{ ...inp, cursor: 'pointer', fontWeight: 700, background: 'var(--accent, #2563eb)', color: '#fff', border: 'none' }}>Add field</button>
        </div>
      </div>

      {loading ? <div style={{ color: 'var(--text3)' }}>Loading…</div> : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
              <th style={{ padding: '6px 8px' }}>Field</th>
              <th style={{ padding: '6px 8px' }}>Label</th>
              <th style={{ padding: '6px 8px' }}>Type</th>
              <th style={{ padding: '6px 8px' }}>Default source / aliases</th>
              <th style={{ padding: '6px 8px' }}>Req</th>
              <th style={{ padding: '6px 8px' }}>Source</th>
              <th style={{ padding: '6px 8px' }}></th>
            </tr>
          </thead>
          <tbody>
            {fields.length === 0 ? (
              <tr><td colSpan={7} style={{ padding: 14, color: 'var(--text3)' }}>No fields yet — add one above.</td></tr>
            ) : fields.map(f => (
              <tr key={f.target_field} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '7px 8px', fontFamily: 'monospace' }}>{f.target_field}</td>
                <td style={{ padding: '7px 8px', fontWeight: 600 }}>{f.label}</td>
                <td style={{ padding: '7px 8px', color: 'var(--text2)' }}>{f.transform}</td>
                <td style={{ padding: '7px 8px', color: 'var(--text2)' }}>
                  {f.default_source || <span style={{ color: 'var(--text3)' }}>—</span>}
                  {f.aliases && f.aliases.length > 0 && <span style={{ color: 'var(--text3)' }}> · {f.aliases.join(', ')}</span>}
                </td>
                <td style={{ padding: '7px 8px' }}>{f.required ? '✓' : ''}</td>
                <td style={{ padding: '7px 8px' }}>
                  <span style={{ fontSize: 11, padding: '2px 7px', borderRadius: 10, background: f.source === 'custom' ? '#ecfdf5' : 'var(--surface)', border: '1px solid var(--border)', color: f.source === 'custom' ? '#047857' : 'var(--text3)' }}>{f.source === 'custom' ? 'custom' : 'default'}</span>
                </td>
                <td style={{ padding: '7px 8px' }}>
                  {f.source === 'custom'
                    ? <button onClick={() => removeField(f.target_field)} style={{ ...inp, cursor: 'pointer', fontSize: 11, padding: '3px 8px' }}>✕ remove</button>
                    : <span style={{ color: 'var(--text3)', fontSize: 11 }}>built-in</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

const lbl: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', marginBottom: 3 }
