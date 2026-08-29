'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'

// Configurable closing-sheet activation-count fields (mig 501): choose the standard 3 fields
// (Upgrades / New Lines / Postpaid) OR define your own, mirroring /closing/tender-config (mig 111).
// Empty config → the app falls back to the built-in 3, so nothing changes until a tenant opts in.
// recon_class only buckets the count-mismatch FLAG shown on the DM verify view + the recon sheet —
// it never touches the cash/credit close gate (block/flag), which is a separate money-recon path.
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }
const RECON_CLASSES = [
  ['activation', 'Activation (New/Postpaid)'],
  ['upgrade', 'Upgrade'],
  ['other', 'Other (not compared to B2B)'],
]

type Def = { field_key: string; label: string; recon_class: string; is_standard?: boolean }

function slug(s: string) { return (s || '').toLowerCase().trim().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') }

export default function CountConfigPage() {
  const [defs, setDefs] = useState<Def[]>([])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api('/api/v1/closing/count-config').then((d: any) => {
      const dfs: Def[] = (d?.defs?.length ? d.defs : d?.standard || []).map((x: any) => ({
        field_key: x.field_key, label: x.label || x.field_key, recon_class: x.recon_class || 'other',
        is_standard: !!x.is_standard,
      }))
      setDefs(dfs)
    }).catch((e: any) => setMsg('❌ ' + (e?.message || e)))
  }, [])
  useEffect(() => { load() }, [load])

  const setDef = (i: number, patch: Partial<Def>) => setDefs(ds => ds.map((d, j) => j === i ? { ...d, ...patch } : d))
  const addDef = () => setDefs(ds => [...ds, { field_key: '', label: '', recon_class: 'other' }])
  const delDef = (i: number) => setDefs(ds => ds.filter((_, j) => j !== i))
  const move = (i: number, dir: -1 | 1) => setDefs(ds => {
    const j = i + dir; if (j < 0 || j >= ds.length) return ds
    const n = [...ds];[n[i], n[j]] = [n[j], n[i]]; return n
  })

  async function seedStandard() {
    setBusy(true)
    try { await api('/api/v1/closing/count-config/seed-standard', { method: 'POST' }); load(); setMsg('✅ Seeded the 3 standard count fields.') }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  async function saveAll() {
    const cleanDefs = defs.map((d, i) => ({ ...d, field_key: d.field_key || slug(d.label), sort_order: i }))
      .filter(d => d.field_key)
    setBusy(true)
    try {
      const r: any = await api('/api/v1/closing/count-config', { method: 'PUT', body: JSON.stringify({ defs: cleanDefs }) })
      setMsg(`✅ Saved ${r?.defs ?? cleanDefs.length} count field(s).`); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔢 Closing Count-Field Configuration</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Define the activation-count fields on your daily closing sheet — the built-in 3 (Upgrades / New Lines / Postpaid), or your own.
            Recon class drives which B2B count (activations vs upgrades) a field is compared against; it&apos;s a flag only, never part of the cash/credit close gate.
            Leave it unconfigured to use the built-in 3.
          </p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      <div style={{ position: 'sticky', top: 0, zIndex: 5, background: 'var(--bg)', padding: '10px 0', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid var(--border)' }}>
        <button className="btn btn-primary" disabled={busy} style={{ fontSize: 13 }} onClick={saveAll}>💾 Save configuration</button>
        <button className="btn btn-secondary" disabled={busy} style={{ fontSize: 13 }} onClick={seedStandard}>Reset to standard 3</button>
        {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
      </div>

      <div className="card table-wrapper" style={{ marginTop: 16, padding: 0 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['', 'Label (on the sheet)', 'Key', 'Recon class', ''].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {defs.map((d, i) => (
              <tr key={i}>
                <td style={{ ...cell, whiteSpace: 'nowrap' }}>
                  <button className="btn btn-secondary" style={{ fontSize: 11, padding: '1px 6px' }} onClick={() => move(i, -1)}>↑</button>
                  <button className="btn btn-secondary" style={{ fontSize: 11, padding: '1px 6px', marginLeft: 2 }} onClick={() => move(i, 1)}>↓</button>
                </td>
                <td style={cell}><input style={{ ...sel, width: '100%' }} value={d.label} placeholder="e.g. Port-In" onChange={e => setDef(i, { label: e.target.value })} /></td>
                <td style={cell}><input style={{ ...sel, width: 150 }} value={d.field_key} placeholder={slug(d.label) || 'auto'} disabled={!!d.is_standard} onChange={e => setDef(i, { field_key: slug(e.target.value) })} />
                  {d.is_standard && <div style={{ fontSize: 10, color: 'var(--text3)' }}>standard</div>}</td>
                <td style={cell}>
                  <select style={sel} value={d.recon_class} onChange={e => setDef(i, { recon_class: e.target.value })}>
                    {RECON_CLASSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </td>
                <td style={cell}><button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => delDef(i)}>✕</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button className="btn btn-secondary" style={{ fontSize: 13, marginTop: 8 }} onClick={addDef}>＋ Add count field</button>
    </div>
  )
}
