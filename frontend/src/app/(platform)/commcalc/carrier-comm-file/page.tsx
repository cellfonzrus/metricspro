'use client'
import { useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'

// Carrier Commission File → Table. A carrier's commission/comp statement (the "comp_report") arrives in
// different shapes per carrier. Upload an Excel/CSV here to see it as a clean TABLE (readable by the user)
// and download a normalized CSV; then map + load it into the system via the Implementation Wizard (the
// comp_report path). PDF table extraction needs server-side support — flagged below as a follow-up.

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function CarrierCommFilePage() {
  const [sheets, setSheets] = useState<{ name: string; rows: any[][] }[]>([])
  const [active, setActive] = useState(0)
  const [fileName, setFileName] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  async function onFile(file: File) {
    setMsg(''); setSheets([]); setFileName(file.name); setBusy(true)
    try {
      if (/\.pdf$/i.test(file.name)) {
        // PDF tables are extracted server-side (pdfplumber).
        const fd = new FormData(); fd.append('file', file)
        const r: any = await api('/api/v1/commcalc/carrier-comm-file/extract', { method: 'POST', body: fd })
        const out = (r?.sheets || []).filter((s: any) => s.rows?.length)
        setSheets(out); setActive(0)
        setMsg(out.length ? `✅ Extracted ${out.length} table(s) from the PDF.` : (r?.note || 'No tables found in the PDF.'))
        return
      }
      const XLSX = await import('xlsx')
      const wb = XLSX.read(await file.arrayBuffer(), { cellDates: true })
      const out = wb.SheetNames.map(name => ({
        name,
        rows: XLSX.utils.sheet_to_json<any[]>(wb.Sheets[name], { header: 1, blankrows: false, defval: '' }) as any[][],
      })).filter(s => s.rows.length)
      setSheets(out); setActive(0)
      setMsg(`✅ Parsed ${out.length} sheet(s) — ${out[0]?.rows.length || 0} rows in “${out[0]?.name}”.`)
    } catch (e: any) { setMsg('❌ Could not read the file: ' + (e?.message || e)) } finally { setBusy(false) }
  }

  async function downloadCsv() {
    const s = sheets[active]; if (!s) return
    const XLSX = await import('xlsx')
    const ws = XLSX.utils.aoa_to_sheet(s.rows)
    const csv = XLSX.utils.sheet_to_csv(ws)
    const blob = new Blob([csv], { type: 'text/csv' })
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob)
    a.download = `${(fileName || 'carrier-comm').replace(/\.[^.]+$/, '')}_${s.name}.csv`; a.click()
  }

  const sheet = sheets[active]
  const header = sheet?.rows[0] || []
  const body = sheet?.rows.slice(1) || []

  return (
    <div style={{ maxWidth: 1000 }}>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📑 Carrier Commission File → Table</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Upload a carrier&apos;s commission / comp statement to view it as a clean table and download a normalized CSV.
          Then map its columns and load it into the system on the <Link href="/commcalc/implementation">Implementation Wizard</Link> (the
          comp report path). Works for any carrier — the file shape doesn&apos;t have to match ours.
        </p>
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <label className="btn btn-primary" style={{ cursor: 'pointer', margin: 0 }}>
          {busy ? '⏳ Reading…' : '⬆️ Upload commission file'}
          <input type="file" accept=".xlsx,.xls,.csv,.pdf" style={{ display: 'none' }} disabled={busy}
            onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f); e.currentTarget.value = '' }} />
        </label>
        {sheets.length > 1 && (
          <select style={sel} value={active} onChange={e => setActive(parseInt(e.target.value))}>
            {sheets.map((s, i) => <option key={s.name} value={i}>{s.name} ({s.rows.length} rows)</option>)}
          </select>
        )}
        {sheet && <button className="btn btn-secondary" onClick={downloadCsv}>⬇️ Download as CSV</button>}
        {fileName && <span style={{ fontSize: 12, color: 'var(--text3)' }}>{fileName}</span>}
        {msg && <span style={{ fontSize: 13, marginLeft: 'auto' }}>{msg}</span>}
      </div>

      {sheet && (
        <div className="card" style={{ padding: 0, overflow: 'auto' }}>
          <div style={{ padding: '8px 12px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
            {sheet.name} — {body.length} rows × {header.length} cols {body.length > 300 ? '(showing first 300)' : ''}
          </div>
          <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: '100%' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              <th style={{ padding: '5px 8px', borderRight: '1px solid var(--border)', color: 'var(--text3)' }}>#</th>
              {header.map((h, i) => <th key={i} style={{ padding: '5px 10px', textAlign: 'left', borderRight: '1px solid var(--border)', whiteSpace: 'nowrap', fontWeight: 700 }}>{String(h)}</th>)}
            </tr></thead>
            <tbody>
              {body.slice(0, 300).map((r, ri) => (
                <tr key={ri} style={{ borderTop: '1px solid var(--border)', background: ri % 2 ? 'var(--surface2)' : undefined }}>
                  <td style={{ padding: '4px 8px', color: 'var(--text3)', borderRight: '1px solid var(--border)' }}>{ri + 1}</td>
                  {header.map((_, ci) => <td key={ci} style={{ padding: '4px 10px', borderRight: '1px solid var(--border)', whiteSpace: 'nowrap' }}>{String(r[ci] ?? '')}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
