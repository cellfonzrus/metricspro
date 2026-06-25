'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '@/lib/client'

// Generic column mapping (A2): map a carrier's spreadsheet column headers to our canonical DB
// fields — config-driven, so a NEW carrier's report is ingested with zero code. Seed the Boost
// default layout, upload a sample to auto-detect headers, then save per-field mappings. The legacy
// hard-coded upload path is untouched; this drives the new /upload-mapped any-carrier importer.
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }
const CONF_COLOR: Record<string, string> = { mapped: '#16794a', exact: '#16794a', alias: '#b45309', fuzzy: '#b42318', '': 'var(--text3)' }

type Draft = { id?: string; source_header: string; transform: string; is_active: boolean; required?: boolean; label?: string; confidence?: string }

export default function ColumnMappingPage() {
  const [reportKeys, setReportKeys] = useState<string[]>([])
  const [transforms, setTransforms] = useState<string[]>([])
  const [fields, setFields] = useState<any[]>([])
  const [carriers, setCarriers] = useState<any[]>([])
  const [rk, setRk] = useState('')
  const [cid, setCid] = useState('')           // '' = all carriers (global/default)
  const [draft, setDraft] = useState<Record<string, Draft>>({})
  const [headers, setHeaders] = useState<string[]>([])
  const [msg, setMsg] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api('/api/v1/commcalc/column-mapping/targets').then((d: any) => {
      setReportKeys(d?.report_keys || []); setTransforms(d?.transforms || [])
      if (d?.report_keys?.length) setRk(prev => prev || d.report_keys[0])
    }).catch(() => {})
    api('/api/v1/commcalc/carriers').then((c: any) => setCarriers(c || [])).catch(() => {})
  }, [])

  const load = useCallback(() => {
    if (!rk) return
    setHeaders([])
    Promise.all([
      api(`/api/v1/commcalc/column-mapping/targets?report_key=${encodeURIComponent(rk)}`),
      api(`/api/v1/commcalc/column-mapping?report_key=${encodeURIComponent(rk)}${cid ? `&carrier_id=${cid}` : ''}`),
    ]).then(([t, rules]: any) => {
      const flds = t?.fields || []
      setFields(flds)
      const ruleByTf: Record<string, any> = {}
      for (const r of rules || []) {
        // only show rules matching the selected scope (carrier or global)
        if (cid ? r.carrier_id === cid : !r.carrier_id) ruleByTf[r.target_field] = r
      }
      const d: Record<string, Draft> = {}
      for (const f of flds) {
        const r = ruleByTf[f.target_field]
        d[f.target_field] = { id: r?.id, source_header: r?.source_header || '', transform: r?.transform || f.transform || 'text', is_active: r ? r.is_active !== false : true, required: f.required, label: f.label }
        delete ruleByTf[f.target_field]
      }
      // custom rules (target fields not in the registry — e.g. a brand-new report_key)
      for (const tf of Object.keys(ruleByTf)) {
        const r = ruleByTf[tf]
        d[tf] = { id: r.id, source_header: r.source_header || '', transform: r.transform || 'text', is_active: r.is_active !== false, required: false, label: tf }
      }
      setDraft(d)
    }).catch((e: any) => setMsg('❌ ' + (e?.message || e)))
  }, [rk, cid])
  useEffect(() => { load() }, [load])

  const setRow = (tf: string, patch: Partial<Draft>) => setDraft(d => ({ ...d, [tf]: { ...d[tf], ...patch } }))

  async function saveRow(tf: string) {
    const r = draft[tf]
    if (!r?.source_header?.trim()) { setMsg('Enter a source header first.'); return }
    try {
      const saved: any = await api('/api/v1/commcalc/column-mapping', { method: 'POST', body: JSON.stringify({ id: r.id, report_key: rk, carrier_id: cid || undefined, target_field: tf, source_header: r.source_header.trim(), transform: r.transform, is_active: r.is_active }) })
      if (saved?.id) setRow(tf, { id: saved.id })
      setMsg('✅ Saved ' + tf)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function saveAll() {
    const toSave = Object.entries(draft).filter(([, r]) => r.source_header?.trim())
    let n = 0
    for (const [tf, r] of toSave) {
      try { const saved: any = await api('/api/v1/commcalc/column-mapping', { method: 'POST', body: JSON.stringify({ id: r.id, report_key: rk, carrier_id: cid || undefined, target_field: tf, source_header: r.source_header.trim(), transform: r.transform, is_active: r.is_active }) }); if (saved?.id) draft[tf].id = saved.id; n++ } catch { /* keep going */ }
    }
    setMsg(`✅ Saved ${n} mapping${n === 1 ? '' : 's'}.`); load()
  }
  async function delRow(tf: string) {
    const r = draft[tf]
    if (!r?.id) { setRow(tf, { source_header: '' }); return }
    try { await api(`/api/v1/commcalc/column-mapping/${r.id}`, { method: 'DELETE' }); setRow(tf, { id: undefined, source_header: '' }); setMsg('Removed ' + tf) } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function seedDefaults() {
    try { const d: any = await api(`/api/v1/commcalc/column-mapping/seed?report_key=${encodeURIComponent(rk)}${cid ? `&carrier_id=${cid}` : ''}`, { method: 'POST' }); setMsg(`✅ Seeded ${d?.seeded ?? 0} default mappings.`); load() } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function detectFromSample(file: File) {
    const fd = new FormData(); fd.append('report_key', rk); if (cid) fd.append('carrier_id', cid); fd.append('file', file)
    try {
      const d: any = await api('/api/v1/commcalc/column-mapping/detect', { method: 'POST', body: fd })
      setHeaders(d?.headers || [])
      setDraft(prev => {
        const next = { ...prev }
        for (const s of d?.suggestions || []) {
          if (s.suggested_source) next[s.target_field] = { ...next[s.target_field], source_header: s.suggested_source, confidence: s.confidence }
        }
        return next
      })
      setMsg(`🔍 Detected ${d?.headers?.length || 0} columns; pre-filled matches — review & Save all.`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  const orderedTfs = [...fields.map(f => f.target_field), ...Object.keys(draft).filter(tf => !fields.some(f => f.target_field === tf))]

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧩 Column Mapping</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Map each carrier&apos;s spreadsheet columns to our canonical fields — config-driven, so a new carrier&apos;s report ingests with no code. Seed the default, or upload a sample to auto-detect.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <select style={sel} value={rk} onChange={e => setRk(e.target.value)}>
          {reportKeys.map(k => <option key={k} value={k}>{k}</option>)}
        </select>
        <select style={sel} value={cid} onChange={e => setCid(e.target.value)}>
          <option value="">All carriers (default)</option>
          {carriers.map(c => <option key={c.id} value={c.id}>{c.name}{c.is_default ? ' (default)' : ''}</option>)}
        </select>
        <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={seedDefaults}>Seed default layout</button>
        <input ref={fileRef} type="file" accept=".xlsx,.xls" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) detectFromSample(f); e.target.value = '' }} />
        <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={() => fileRef.current?.click()}>📄 Upload sample to auto-detect</button>
        <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={saveAll}>Save all</button>
        {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
      </div>

      {headers.length > 0 && (
        <div className="card" style={{ padding: '8px 12px', marginBottom: 12, fontSize: 12, color: 'var(--text2)' }}>
          <b>Sample columns ({headers.length}):</b> {headers.join('  ·  ')}
        </div>
      )}

      <div className="card table-wrapper" style={{ padding: 0 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Our field (target)', 'Source column header', 'Transform', 'On', ''].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {orderedTfs.map(tf => {
              const r = draft[tf]; if (!r) return null
              return (
                <tr key={tf}>
                  <td style={cell}>
                    <span style={{ fontWeight: 600 }}>{r.label || tf}</span>
                    {r.required && <span style={{ color: '#b42318', marginLeft: 4 }}>*</span>}
                    <div style={{ fontSize: 10, color: 'var(--text3)' }}>{tf}</div>
                  </td>
                  <td style={cell}>
                    <input list={`hdrs-${tf}`} style={{ ...sel, width: '100%', borderColor: r.confidence ? CONF_COLOR[r.confidence] : 'var(--border)' }}
                      placeholder="(unmapped)" value={r.source_header} onChange={e => setRow(tf, { source_header: e.target.value, confidence: '' })} />
                    {headers.length > 0 && <datalist id={`hdrs-${tf}`}>{headers.map(h => <option key={h} value={h} />)}</datalist>}
                    {r.confidence && <span style={{ fontSize: 10, color: CONF_COLOR[r.confidence] }}>{r.confidence} match</span>}
                  </td>
                  <td style={cell}>
                    <select style={sel} value={r.transform} onChange={e => setRow(tf, { transform: e.target.value })}>
                      {transforms.map(t => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </td>
                  <td style={cell}><input type="checkbox" checked={r.is_active} onChange={e => setRow(tf, { is_active: e.target.checked })} /></td>
                  <td style={cell}>
                    <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={() => saveRow(tf)}>Save</button>
                    <button className="btn btn-secondary" style={{ fontSize: 12, marginLeft: 4 }} onClick={() => delRow(tf)}>✕</button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
        * required field. Scope a mapping to one carrier, or leave &quot;All carriers&quot; for the org default (a carrier-specific rule overrides the default for that field). Transforms: text · number · int · date10 (first 10 chars) · mdn (strip trailing .0) · upper · lower · bool.
      </p>
    </div>
  )
}
