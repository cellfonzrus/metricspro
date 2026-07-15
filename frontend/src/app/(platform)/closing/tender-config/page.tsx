'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import { api, apiUpload } from '@/lib/client'

// Configurable closing tenders (mig 111): choose the standard 7 tenders OR create your own, then map
// each POS report's raw Tender Type values to them (smart-suggested) for the 3-way / regular recon.
// Empty config → the app falls back to the built-in 7, so nothing changes until a tenant opts in.
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }
const CONF_COLOR: Record<string, string> = { exact: '#16794a', mapped: '#16794a', fuzzy: '#b45309', '': 'var(--text3)' }
const RECON_CLASSES = [['cash', 'Cash (gate)'], ['card', 'Card / credit (gate)'], ['other', 'Other']]
const LEGS: { key: 'sales' | 'x_report'; label: string }[] = [
  { key: 'sales', label: 'Sales Transaction report' },
  { key: 'x_report', label: 'X-report' },
]

type Def = { tender_key: string; label: string; recon_class: string; include_in_total: boolean; is_standard?: boolean }
type Sug = { raw_label: string; suggested_tender?: string; confidence?: string }

function slug(s: string) { return (s || '').toLowerCase().trim().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') }

export default function TenderConfigPage() {
  const [defs, setDefs] = useState<Def[]>([])
  const [reconMode, setReconMode] = useState<'3way' | '2way'>('3way')
  const [custom, setCustom] = useState(false)
  // per-leg: the raw labels seen + the tender each is assigned to + the suggestion confidence
  const [rawLabels, setRawLabels] = useState<Record<string, Sug[]>>({ sales: [], x_report: [] })
  const [assign, setAssign] = useState<Record<string, Record<string, string>>>({ sales: {}, x_report: {} })
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [uploadLeg, setUploadLeg] = useState<'sales' | 'x_report' | 'auto'>('auto')
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(() => {
    api('/api/v1/closing/tender-config').then((d: any) => {
      const dfs: Def[] = (d?.defs?.length ? d.defs : d?.standard || []).map((x: any) => ({
        tender_key: x.tender_key, label: x.label || x.tender_key, recon_class: x.recon_class || 'other',
        include_in_total: x.include_in_total !== false, is_standard: !!x.is_standard,
      }))
      setDefs(dfs)
      setReconMode(d?.recon_mode === '2way' ? '2way' : '3way')
      setCustom(!!d?.custom)
      // hydrate existing maps into per-leg raw-label → tender assignments
      const a: Record<string, Record<string, string>> = { sales: {}, x_report: {} }
      const rl: Record<string, Sug[]> = { sales: [], x_report: [] }
      for (const m of d?.maps || []) {
        const legs = m.report === 'both' ? ['sales', 'x_report'] : [m.report]
        for (const leg of legs) {
          for (const lab of m.source_labels || []) {
            a[leg][lab] = m.tender_key
            if (!rl[leg].some(s => s.raw_label === lab)) rl[leg].push({ raw_label: lab, confidence: 'mapped' })
          }
        }
      }
      setAssign(a); setRawLabels(rl)
    }).catch((e: any) => setMsg('❌ ' + (e?.message || e)))
  }, [])
  useEffect(() => { load() }, [load])

  // ── field (tender def) editing ──
  const setDef = (i: number, patch: Partial<Def>) => setDefs(ds => ds.map((d, j) => j === i ? { ...d, ...patch } : d))
  const addDef = () => setDefs(ds => [...ds, { tender_key: '', label: '', recon_class: 'other', include_in_total: true }])
  const delDef = (i: number) => setDefs(ds => ds.filter((_, j) => j !== i))
  const move = (i: number, dir: -1 | 1) => setDefs(ds => {
    const j = i + dir; if (j < 0 || j >= ds.length) return ds
    const n = [...ds];[n[i], n[j]] = [n[j], n[i]]; return n
  })
  async function seedStandard() {
    setBusy(true)
    try { await api('/api/v1/closing/tender-config/seed-standard', { method: 'POST' }); setCustom(false); load(); setMsg('✅ Seeded the 7 standard tenders.') }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  // ── detect (smart map): from an uploaded sample, or from already-ingested data ──
  // `leg` ('sales'|'x_report'|'auto', default 'auto') lets the caller force which leg an uploaded
  // sample belongs to; the backend auto-detects the file's own shape (real B2B multi-sheet X-Report vs
  // a Sales Transaction Details export) when left on auto, and reports back which leg it landed the
  // values in (`detected_leg`/`detect_detail`) so a sample that has no ingested data yet — the case an
  // upload-only leg needs — still gets mapped.
  async function detect(file?: File, leg: 'sales' | 'x_report' | 'auto' = 'auto') {
    setBusy(true); setMsg('🔍 Detecting tender types…')
    try {
      const fd = new FormData()
      if (file) { fd.append('file', file); fd.append('leg', leg) }
      const d: any = await apiUpload('/api/v1/closing/tender-config/detect', fd)
      setRawLabels(prev => {
        const next = { ...prev }
        for (const l of ['sales', 'x_report'] as const) {
          const found: Sug[] = d?.[l] || []
          const merged = [...next[l]]
          for (const s of found) if (!merged.some(x => x.raw_label === s.raw_label)) merged.push(s)
          next[l] = merged
        }
        return next
      })
      // pre-fill assignments from suggestions (don't clobber an existing manual choice)
      setAssign(prev => {
        const next = { sales: { ...prev.sales }, x_report: { ...prev.x_report } }
        for (const l of ['sales', 'x_report'] as const) for (const s of (d?.[l] || []))
          if (s.suggested_tender && !next[l][s.raw_label]) next[l][s.raw_label] = s.suggested_tender
        return next
      })
      const n = (d?.sales?.length || 0) + (d?.x_report?.length || 0)
      if (file && d?.detected_leg) {
        const legLabel = d.detected_leg === 'x_report' ? 'X-Report' : 'Sales transactions'
        const found = (d?.[d.detected_leg]?.length || 0)
        setMsg(`🔍 Detected: ${legLabel}${d?.detect_detail ? ` (${d.detect_detail})` : ''} — ${found} raw value(s). Pre-filled the best match — review & Save.`)
      } else {
        setMsg(n ? `🔍 Found ${n} tender value(s); pre-filled the best match — review & Save.` : 'No tender values found — upload a sample report, or ingest a day of sales first.')
      }
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }
  const setAssignOne = (leg: string, raw: string, key: string) => setAssign(a => ({ ...a, [leg]: { ...a[leg], [raw]: key } }))

  async function saveAll() {
    // validate defs
    const cleanDefs = defs.map((d, i) => ({ ...d, tender_key: d.tender_key || slug(d.label), sort_order: i }))
      .filter(d => d.tender_key)
    if (!cleanDefs.length) { setMsg('Add at least one tender.'); return }
    const keys = new Set(cleanDefs.map(d => d.tender_key))
    // group per-leg assignments back into map rows {tender_key, report, source_labels[]}
    const maps: any[] = []
    for (const leg of ['sales', 'x_report'] as const) {
      const byKey: Record<string, string[]> = {}
      for (const [lab, key] of Object.entries(assign[leg])) {
        if (!key || !keys.has(key)) continue
        ;(byKey[key] = byKey[key] || []).push(lab)
      }
      for (const [key, labels] of Object.entries(byKey)) maps.push({ tender_key: key, report: leg, source_labels: labels })
    }
    setBusy(true)
    try {
      const r: any = await api('/api/v1/closing/tender-config', { method: 'PUT', body: JSON.stringify({ defs: cleanDefs, maps, recon_mode: reconMode, custom }) })
      setMsg(`✅ Saved ${r?.defs ?? cleanDefs.length} tenders · ${r?.maps ?? maps.length} mapping row(s).`); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  const StepHead = ({ n, title, sub }: { n: number; title: string; sub?: string }) => (
    <div style={{ margin: '22px 0 10px' }}>
      <div style={{ fontSize: 15, fontWeight: 700 }}><span style={{ color: 'var(--accent)' }}>Step {n}</span> · {title}</div>
      {sub && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 6 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Closing Tender Configuration</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Define the tenders on your daily closing sheet and map each POS report&apos;s raw Tender Type values to them —
          so the 3-way / regular recon works with <b>any</b> POS. Leave it unconfigured to use the built-in 7 tenders.
        </p>
      </div>

      <div style={{ position: 'sticky', top: 0, zIndex: 5, background: 'var(--bg)', padding: '10px 0', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid var(--border)' }}>
        <button className="btn btn-primary" disabled={busy} style={{ fontSize: 13 }} onClick={saveAll}>💾 Save configuration</button>
        <button className="btn btn-secondary" disabled={busy} style={{ fontSize: 13 }} onClick={seedStandard}>Reset to standard 7</button>
        {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
      </div>

      {/* Step 1 — fields */}
      <StepHead n={1} title="Tender fields (standard or custom)" sub="The tender columns on the closing sheet. recon_class drives the cash/credit close gate + the regular (2-way) recon." />
      <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginBottom: 8 }}>
        <input type="checkbox" checked={custom} onChange={e => setCustom(e.target.checked)} />
        This tenant uses <b>custom</b> tenders (beyond the standard 7). When off, the standard 7 are used as-is.
      </label>
      <div className="card table-wrapper" style={{ padding: 0 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['', 'Label (on the sheet)', 'Key', 'Recon class', 'In total', ''].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {defs.map((d, i) => (
              <tr key={i}>
                <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                  <button className="btn btn-secondary" style={{ fontSize: 11, padding: '1px 6px' }} onClick={() => move(i, -1)}>↑</button>
                  <button className="btn btn-secondary" style={{ fontSize: 11, padding: '1px 6px', marginLeft: 2 }} onClick={() => move(i, 1)}>↓</button>
                </td>
                <td style={cell}><input style={{ ...sel, width: '100%' }} value={d.label} placeholder="e.g. Financing" onChange={e => setDef(i, { label: e.target.value })} /></td>
                <td style={cell}><input style={{ ...sel, width: 130 }} value={d.tender_key} placeholder={slug(d.label) || 'auto'} onChange={e => setDef(i, { tender_key: slug(e.target.value) })} />
                  {d.is_standard && <div style={{ fontSize: 10, color: 'var(--text3)' }}>standard</div>}</td>
                <td style={cell}>
                  <select style={sel} value={d.recon_class} onChange={e => setDef(i, { recon_class: e.target.value })}>
                    {RECON_CLASSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </td>
                <td style={cell}><input type="checkbox" checked={d.include_in_total} onChange={e => setDef(i, { include_in_total: e.target.checked })} /></td>
                <td style={cell}><button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => delDef(i)}>✕</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button className="btn btn-secondary" style={{ fontSize: 13, marginTop: 8 }} onClick={addDef}>＋ Add tender</button>

      {/* Step 2 — detect */}
      <StepHead n={2} title="Find your POS tender values" sub="Upload a sample Sales Transaction report or a sample X-Report (auto-detected by shape, or pick which leg it is), or pull the distinct Tender Types from data you've already ingested. On the Total side both reports come from b2bsoft." />
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }}
          onChange={e => { const f = e.target.files?.[0]; if (f) detect(f, uploadLeg); e.target.value = '' }} />
        <select style={sel} value={uploadLeg} onChange={e => setUploadLeg(e.target.value as any)} title="Which report the next uploaded sample is">
          <option value="auto">Auto-detect shape</option>
          <option value="sales">Sales transactions sample</option>
          <option value="x_report">X-Report sample</option>
        </select>
        <button className="btn btn-secondary" disabled={busy} style={{ fontSize: 13 }} onClick={() => fileRef.current?.click()}>📄 Upload a sample report</button>
        <button className="btn btn-secondary" disabled={busy} style={{ fontSize: 13 }} onClick={() => detect()}>⚡ Detect from ingested data</button>
      </div>

      {/* Step 3 — map */}
      <StepHead n={3} title="Map each raw value to a tender" sub="Confidence-coloured; green = confident. Adjust any that are wrong." />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 14 }}>
        {LEGS.map(leg => (
          <div key={leg.key} className="card" style={{ padding: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>{leg.label}</div>
            {(rawLabels[leg.key] || []).length === 0
              ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>No values yet — run Detect above.</div>
              : (rawLabels[leg.key] || []).map(s => (
                <div key={s.raw_label} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '4px 0' }}>
                  <span style={{ flex: 1, fontSize: 13, borderLeft: `3px solid ${CONF_COLOR[s.confidence || '']}`, paddingLeft: 6 }}>{s.raw_label}</span>
                  <span style={{ color: 'var(--text3)' }}>→</span>
                  <select style={{ ...sel, minWidth: 150 }} value={assign[leg.key]?.[s.raw_label] || ''} onChange={e => setAssignOne(leg.key, s.raw_label, e.target.value)}>
                    <option value="">— ignore —</option>
                    {defs.map(d => <option key={d.tender_key || d.label} value={d.tender_key || slug(d.label)}>{d.label || d.tender_key}</option>)}
                  </select>
                </div>
              ))}
          </div>
        ))}
      </div>

      {/* Step 4 — recon type */}
      <StepHead n={4} title="Recon type" sub="3-way = closing vs X-report vs Sales transactions. Regular (2-way) = closing vs one source." />
      <div style={{ display: 'flex', gap: 16, marginBottom: 30 }}>
        {(['3way', '2way'] as const).map(m => (
          <label key={m} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 14 }}>
            <input type="radio" name="reconmode" checked={reconMode === m} onChange={() => setReconMode(m)} />
            {m === '3way' ? '3-way recon' : 'Regular (2-way) recon'}
          </label>
        ))}
      </div>
    </div>
  )
}
