'use client'
// Shared report export: true .xlsx (SheetJS), styled PDF (jsPDF + autotable),
// and browser Print. All three render from the same structured payload so every
// report exports identically. Libs are dynamically imported on click to keep them
// out of the initial bundle and the SSR path.
import { useState } from 'react'

export type ExportColumn = {
  header: string
  get: (row: any) => string | number | null | undefined
  money?: boolean
  align?: 'left' | 'right'
  // Optional hints used by <ReportShell> for filtering/grouping (ignored by export itself):
  field?: string                                   // stable identity (defaults to header)
  type?: 'text' | 'money' | 'number' | 'date'      // filter/sort semantics (money implies type=money)
  role?: 'rep' | 'store' | 'date' | 'month'        // force a quick-filter role (else auto-detected)
}
export type ExportSheet = { name: string; columns: ExportColumn[]; rows: any[] }
export type ExportPayload = {
  title: string
  subtitle?: string
  filename: string // base name, no extension
  sheets: ExportSheet[]
  chartImage?: string     // optional PNG data URL (a captured trend chart) rendered atop PDF + Print
  chartImages?: string[]  // multiple captured charts (e.g. the Trends hub) — rendered stacked
}

function imgSize(dataUrl: string): Promise<{ w: number; h: number }> {
  return new Promise(res => {
    if (typeof window === 'undefined' || !dataUrl) { res({ w: 0, h: 0 }); return }
    const im = new Image(); im.onload = () => res({ w: im.naturalWidth, h: im.naturalHeight }); im.onerror = () => res({ w: 0, h: 0 }); im.src = dataUrl
  })
}

const money = (n: any) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(n) || 0)

function displayCell(col: ExportColumn, row: any): string {
  const v = col.get(row)
  if (col.money) return money(v)
  return v == null ? '' : String(v)
}
function rawCell(col: ExportColumn, row: any): string | number {
  const v = col.get(row)
  if (col.money) return Number(v) || 0
  return v == null ? '' : (v as any)
}
function hasRows(p: ExportPayload) {
  return p.sheets.some(s => s.rows && s.rows.length)
}

// ---- Excel (.xlsx) ----
async function buildWorkbook(p: ExportPayload) {
  const XLSX = await import('xlsx')
  const wb = XLSX.utils.book_new()
  for (const sheet of p.sheets) {
    const aoa: (string | number)[][] = [sheet.columns.map(c => c.header)]
    for (const row of sheet.rows) aoa.push(sheet.columns.map(c => rawCell(c, row)))
    const ws = XLSX.utils.aoa_to_sheet(aoa)
    // Column widths + money number format
    ws['!cols'] = sheet.columns.map(c => ({ wch: Math.max(c.header.length + 2, c.money ? 12 : 16) }))
    for (let ci = 0; ci < sheet.columns.length; ci++) {
      if (!sheet.columns[ci].money) continue
      for (let ri = 1; ri <= sheet.rows.length; ri++) {
        const ref = XLSX.utils.encode_cell({ r: ri, c: ci })
        const cell = ws[ref]
        if (cell && typeof cell.v === 'number') cell.z = '$#,##0.00'
      }
    }
    const safe = sheet.name.replace(/[\\/?*[\]:]/g, ' ').slice(0, 31) || 'Sheet'
    XLSX.utils.book_append_sheet(wb, ws, safe)
  }
  return { XLSX, wb }
}
export async function exportToExcel(p: ExportPayload) {
  const { XLSX, wb } = await buildWorkbook(p)
  XLSX.writeFile(wb, `${p.filename}.xlsx`)
}
// Same workbook, returned as base64 for server-side delivery (Send to rep).
export async function renderExcelBase64(p: ExportPayload) {
  const { XLSX, wb } = await buildWorkbook(p)
  return {
    filename: `${p.filename}.xlsx`,
    mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    content_b64: XLSX.write(wb, { type: 'base64', bookType: 'xlsx' }) as string,
  }
}

// ---- PDF ----
async function buildPdfDoc(p: ExportPayload) {
  const { jsPDF } = await import('jspdf')
  const autoTable = (await import('jspdf-autotable')).default
  const doc = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a4' })
  const pageW = doc.internal.pageSize.getWidth()
  doc.setFontSize(14); doc.setTextColor(15, 23, 42)
  doc.text(p.title, 40, 42)
  if (p.subtitle) { doc.setFontSize(9); doc.setTextColor(100, 116, 139); doc.text(p.subtitle, 40, 58) }
  doc.setFontSize(8); doc.setTextColor(150, 163, 175)
  doc.text(new Date().toLocaleString(), pageW - 40, 42, { align: 'right' })
  let startY = p.subtitle ? 74 : 62
  // Trend chart image(s) atop the report (scaled to fit; paginated when several are passed).
  const pdfImgs = [p.chartImage, ...(p.chartImages || [])].filter(Boolean) as string[]
  const pageH = doc.internal.pageSize.getHeight()
  for (const im of pdfImgs) {
    try {
      const { w: iw, h: ih } = await imgSize(im)
      if (iw && ih) {
        const maxW = pageW - 80, maxH = 250
        let w = maxW, h = w * ih / iw
        if (h > maxH) { h = maxH; w = h * iw / ih }
        if (startY + h > pageH - 40) { doc.addPage(); startY = 50 }
        doc.addImage(im, 'PNG', 40, startY, w, h)
        startY += h + 18
      }
    } catch { /* chart is best-effort; table still exports */ }
  }
  for (const sheet of p.sheets) {
    if (p.sheets.length > 1) {
      doc.setFontSize(11); doc.setTextColor(30, 58, 95)
      doc.text(sheet.name, 40, startY)
      startY += 10
    }
    const columnStyles: any = {}
    sheet.columns.forEach((c, i) => {
      if (c.money || c.align === 'right') columnStyles[i] = { halign: 'right' }
    })
    autoTable(doc, {
      head: [sheet.columns.map(c => c.header)],
      body: sheet.rows.map(r => sheet.columns.map(c => displayCell(c, r))),
      startY,
      styles: { fontSize: 7.5, cellPadding: 3, overflow: 'linebreak' },
      headStyles: { fillColor: [30, 58, 95], textColor: 255, fontSize: 7.5 },
      alternateRowStyles: { fillColor: [241, 245, 249] },
      columnStyles,
      margin: { left: 40, right: 40 },
    })
    startY = (doc as any).lastAutoTable.finalY + 26
  }
  return doc
}
export async function renderPdfBase64(p: ExportPayload) {
  const doc = await buildPdfDoc(p)
  // jsPDF 'datauristring' → "data:application/pdf;filename=...;base64,XXXX"; keep only the base64.
  const uri = doc.output('datauristring')
  return { filename: `${p.filename}.pdf`, mime: 'application/pdf', content_b64: uri.slice(uri.indexOf(',') + 1) }
}
export async function exportToPDF(p: ExportPayload) {
  const doc = await buildPdfDoc(p)
  doc.save(`${p.filename}.pdf`)
}

// ---- Print ----
function esc(s: any) {
  return String(s ?? '').replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch] as string))
}
export function printReport(p: ExportPayload) {
  const w = window.open('', '_blank', 'width=1100,height=800')
  if (!w) { alert('Pop-up blocked — allow pop-ups to print.'); return }
  const tables = p.sheets.map(sheet => `
    ${p.sheets.length > 1 ? `<h2>${esc(sheet.name)}</h2>` : ''}
    <table>
      <thead><tr>${sheet.columns.map(c => `<th class="${c.money || c.align === 'right' ? 'r' : ''}">${esc(c.header)}</th>`).join('')}</tr></thead>
      <tbody>${sheet.rows.map(row => `<tr>${sheet.columns.map(c =>
        `<td class="${c.money || c.align === 'right' ? 'r' : ''}">${esc(displayCell(c, row))}</td>`).join('')}</tr>`).join('')}</tbody>
    </table>`).join('')
  w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(p.filename)}</title>
    <style>
      *{box-sizing:border-box} body{font:12px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;color:#0f172a;margin:24px}
      h1{font-size:18px;margin:0 0 2px} .sub{color:#64748b;font-size:12px;margin:0 0 16px}
      h2{font-size:13px;margin:18px 0 6px;color:#1e3a5f}
      table{width:100%;border-collapse:collapse;margin-bottom:18px;font-size:11px}
      th{background:#1e3a5f;color:#fff;text-align:left;padding:5px 8px;font-size:10px;text-transform:uppercase}
      td{padding:4px 8px;border-bottom:1px solid #e2e8f0}
      tr:nth-child(even) td{background:#f1f5f9}
      .r{text-align:right} img.chart{max-width:100%;height:auto;margin:0 0 18px;border:1px solid #e2e8f0;border-radius:8px}
      @media print{body{margin:10mm}}
    </style></head><body>
    <h1>${esc(p.title)}</h1>${p.subtitle ? `<p class="sub">${esc(p.subtitle)}</p>` : ''}
    ${[p.chartImage, ...(p.chartImages || [])].filter(Boolean).map(src => `<img class="chart" src="${src}"/>`).join('')}
    ${tables}
    <script>window.onload=function(){window.print()}</script>
    </body></html>`)
  w.document.close()
}

// ---- Buttons ----
export function ExportButtons({ payload, compact }: { payload: () => ExportPayload; compact?: boolean }) {
  const [busy, setBusy] = useState<'' | 'excel' | 'pdf'>('')
  async function go(kind: 'excel' | 'pdf') {
    const p = payload()
    if (!hasRows(p)) { alert('Nothing to export yet.'); return }
    try {
      setBusy(kind)
      if (kind === 'excel') await exportToExcel(p)
      else await exportToPDF(p)
    } catch (e: any) {
      console.error(e); alert('Export failed: ' + (e?.message || e))
    } finally { setBusy('') }
  }
  function doPrint() {
    const p = payload()
    if (!hasRows(p)) { alert('Nothing to export yet.'); return }
    printReport(p)
  }
  const style: React.CSSProperties = {
    fontSize: compact ? 12 : 13, padding: compact ? '5px 10px' : '6px 12px',
  }
  return (
    <div style={{ display: 'inline-flex', gap: 6 }}>
      <button className="btn btn-secondary" style={style} disabled={!!busy} onClick={() => go('excel')}>
        {busy === 'excel' ? '⏳' : '⬇️'} Excel
      </button>
      <button className="btn btn-secondary" style={style} disabled={!!busy} onClick={() => go('pdf')}>
        {busy === 'pdf' ? '⏳' : '📄'} PDF
      </button>
      <button className="btn btn-secondary" style={style} disabled={!!busy} onClick={doPrint}>
        🖨️ Print
      </button>
    </div>
  )
}
