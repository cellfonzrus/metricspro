'use client'
// POS — Structured receipt import (migration 866). Upload a PDF receipt from a chosen POS format
// (RQ / B2B / …), review + EDIT the parsed fields (description / qty / tax / price), save, and REPRINT
// it later in the SAME layout. Nothing about a format is hardcoded — the columns/totals come from the
// backend Document. Backend: /pos/receipt-import/* + /pos/receipt-imports/*.
import { useEffect, useState, type CSSProperties } from 'react'
import { api, apiPrintHtml } from '@/lib/client'

type Fmt = { source: string; label: string }
type Col = { key: string; label: string; kind: string; align?: string }
type Item = { cells: Record<string, string>; editable: string[] }
type Total = { key: string; label: string; amount: number | null; editable?: boolean }
interface ReceiptDoc {
  pos_source?: string; format_label?: string; title?: string
  meta?: { key: string; label: string; value: string; editable?: boolean }[]
  store?: { lines?: string[]; phone?: string | null; fax?: string | null }
  bill_to?: { lines?: string[]; name?: string }
  ship_to?: { lines?: string[] } | null
  columns?: Col[]; items?: Item[]; totals?: Total[]
  payments?: { label: string; amount: number | null }[]
  comments?: string | null; footer_text?: string | null
  derived?: Record<string, unknown>
}
interface ImportRow {
  id: string; pos_source?: string; invoice_no?: string | null; customer_name?: string | null
  device_name?: string | null; imei?: string | null; total?: number | null; sale_date?: string | null
  store_code?: string | null; created_at?: string
}

const money = (v: unknown) => {
  const n = typeof v === 'number' ? v : parseFloat(String(v ?? '').replace(/[^0-9.-]/g, ''))
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : '—'
}
const fileToB64 = (f: File) =>
  new Promise<string>((res, rej) => {
    const r = new FileReader()
    r.onload = () => res(String(r.result).split(',')[1] || '')
    r.onerror = rej
    r.readAsDataURL(f)
  })

const input: CSSProperties = { padding: '4px 6px', border: '1px solid var(--border)', borderRadius: 4, background: 'var(--bg)', color: 'var(--text)', fontSize: 13, width: '100%' }
const label: CSSProperties = { fontSize: 11, color: 'var(--text2)', display: 'block', marginBottom: 2 }

export default function ReceiptImportPage() {
  const [formats, setFormats] = useState<Fmt[]>([])
  const [source, setSource] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [doc, setDoc] = useState<ReceiptDoc | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null) // set when editing a stored receipt
  const [notes, setNotes] = useState('')
  const [storeCode, setStoreCode] = useState('')
  const [savedId, setSavedId] = useState<string | null>(null)

  const [q, setQ] = useState('')
  const [rows, setRows] = useState<ImportRow[]>([])

  useEffect(() => {
    api('/api/v1/pos/receipt-import/formats').then((r: { formats: Fmt[]; default_source?: string }) => {
      setFormats(r.formats || [])
      setSource(r.default_source || r.formats?.[0]?.source || '')
    }).catch(() => {})
    search()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const search = async () => {
    try {
      const r = await api(`/api/v1/pos/receipt-imports?q=${encodeURIComponent(q)}`)
      setRows(r.receipt_imports || [])
    } catch (e) { setError(e instanceof Error ? e.message : 'Search failed') }
  }

  const onFile = async (f: File | null) => {
    if (!f) return
    if (!source) { setError('Pick which POS this receipt is from first.'); return }
    setError(''); setBusy('parse'); setSavedId(null); setEditingId(null)
    try {
      const b64 = await fileToB64(f)
      const r = await api('/api/v1/pos/receipt-import/structured', {
        method: 'POST', body: JSON.stringify({ pos_source: source, file: b64, dry_run: true }),
      })
      setDoc(r.document); setNotes('')
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not read that PDF') }
    finally { setBusy(null) }
  }

  const setCell = (i: number, key: string, val: string) =>
    setDoc(d => {
      if (!d) return d
      const items = d.items!.map((it, idx) => idx === i ? { ...it, cells: { ...it.cells, [key]: val } } : it)
      return { ...d, items }
    })
  const setTotal = (i: number, val: string) =>
    setDoc(d => {
      if (!d) return d
      const n = parseFloat(val.replace(/[^0-9.-]/g, ''))
      const totals = d.totals!.map((t, idx) => idx === i ? { ...t, amount: Number.isFinite(n) ? n : null } : t)
      return { ...d, totals }
    })
  const setMeta = (i: number, val: string) =>
    setDoc(d => d ? { ...d, meta: d.meta!.map((m, idx) => idx === i ? { ...m, value: val } : m) } : d)

  const saveNew = async () => {
    if (!doc) return
    setBusy('save'); setError('')
    try {
      const r = await api('/api/v1/pos/receipt-import/structured', {
        method: 'POST',
        body: JSON.stringify({ pos_source: doc.pos_source || source, document: doc, store_code: storeCode || undefined, notes: notes || undefined }),
      })
      setSavedId(r.import_id); setEditingId(r.import_id)
    } catch (e) { setError(e instanceof Error ? e.message : 'Save failed') }
    finally { setBusy(null); search() }
  }

  const saveEdits = async () => {
    if (!doc || !editingId) return
    setBusy('save'); setError('')
    try {
      await api(`/api/v1/pos/receipt-imports/${editingId}/document`, {
        method: 'PATCH', body: JSON.stringify({ document: doc }),
      })
      setSavedId(editingId)
    } catch (e) { setError(e instanceof Error ? e.message : 'Save failed') }
    finally { setBusy(null); search() }
  }

  const openStored = async (id: string) => {
    setError(''); setBusy('open')
    try {
      const r = await api(`/api/v1/pos/receipt-imports/${id}`)
      const ri = r.receipt_import
      if (!ri?.document) { setError('This receipt has no editable document (older import).'); return }
      setDoc(ri.document); setEditingId(id); setSavedId(null); setNotes(ri.notes || ''); setStoreCode(ri.store_code || '')
    } catch (e) { setError(e instanceof Error ? e.message : 'Open failed') }
    finally { setBusy(null) }
  }

  const print = async (id: string) => {
    try { await apiPrintHtml(`/api/v1/pos/receipt-imports/${id}/print`) }
    catch (e) { setError(e instanceof Error ? e.message : 'Print failed') }
  }

  const reset = () => { setDoc(null); setEditingId(null); setSavedId(null); setError('') }

  return (
    <div style={{ padding: 20, maxWidth: 1100, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>🧾 Receipt Import</h1>
      <p style={{ color: 'var(--text2)', margin: '0 0 16px', fontSize: 13 }}>
        Upload a receipt PDF from another POS, review &amp; edit the fields, and reprint it later in the same format.
      </p>

      {error && <div style={{ background: '#fde8e8', color: '#9b1c1c', padding: '8px 12px', borderRadius: 6, marginBottom: 12, fontSize: 13 }}>{error}</div>}

      {!doc && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, marginBottom: 20 }}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <label style={label}>Which POS is this receipt from?</label>
              <select value={source} onChange={e => setSource(e.target.value)} style={{ ...input, width: 240 }}>
                {formats.map(f => <option key={f.source} value={f.source}>{f.label}</option>)}
              </select>
            </div>
            <div>
              <label style={label}>Receipt PDF</label>
              <input type="file" accept="application/pdf" onChange={e => onFile(e.target.files?.[0] || null)} style={{ fontSize: 13 }} />
            </div>
            {busy === 'parse' && <span style={{ color: 'var(--text2)', fontSize: 13 }}>Reading…</span>}
          </div>
        </div>
      )}

      {doc && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, marginBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <div style={{ fontWeight: 700 }}>{doc.title} · <span style={{ color: 'var(--text2)', fontWeight: 400 }}>{doc.format_label}</span></div>
            <div style={{ display: 'flex', gap: 8 }}>
              {savedId && <button className="btn btn-secondary" onClick={() => print(savedId)}>🖨 Print</button>}
              {editingId ? <button className="btn btn-primary" disabled={busy === 'save'} onClick={saveEdits}>{busy === 'save' ? 'Saving…' : 'Save edits'}</button>
                         : <button className="btn btn-primary" disabled={busy === 'save'} onClick={saveNew}>{busy === 'save' ? 'Saving…' : 'Save & import'}</button>}
              <button className="btn btn-secondary" onClick={reset}>Close</button>
            </div>
          </div>

          {/* header: store + meta */}
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 12 }}>
            <div style={{ fontSize: 12 }}>
              <div style={{ color: 'var(--text2)', fontWeight: 700 }}>Store</div>
              {(doc.store?.lines || []).map((l, i) => <div key={i}>{l}</div>)}
            </div>
            <div style={{ fontSize: 12 }}>
              <div style={{ color: 'var(--text2)', fontWeight: 700 }}>Bill To</div>
              {(doc.bill_to?.lines || []).map((l, i) => <div key={i}>{l}</div>)}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'auto 160px', gap: '2px 8px', alignItems: 'center', fontSize: 12 }}>
              {(doc.meta || []).map((m, i) => (
                <span key={m.key} style={{ display: 'contents' }}>
                  <label style={{ color: 'var(--text2)', textAlign: 'right' }}>{m.label}</label>
                  {m.editable ? <input value={m.value} onChange={e => setMeta(i, e.target.value)} style={input} /> : <span style={{ fontWeight: 600 }}>{m.value}</span>}
                </span>
              ))}
            </div>
          </div>

          {/* items */}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 640 }}>
              <thead>
                <tr>{(doc.columns || []).map(c => (
                  <th key={c.key} style={{ textAlign: (c.align as 'left' | 'right') || 'left', borderBottom: '1.5px solid var(--border)', padding: '4px 6px', whiteSpace: 'nowrap' }}>{c.label}</th>
                ))}</tr>
              </thead>
              <tbody>
                {(doc.items || []).map((it, i) => (
                  <tr key={i}>
                    {(doc.columns || []).map(c => {
                      const editable = it.editable?.includes(c.key)
                      return (
                        <td key={c.key} style={{ padding: '2px 6px', borderBottom: '1px solid var(--border)', textAlign: (c.align as 'left' | 'right') || 'left' }}>
                          {editable
                            ? <input value={it.cells[c.key] ?? ''} onChange={e => setCell(i, c.key, e.target.value)} style={{ ...input, textAlign: (c.align as 'left' | 'right') || 'left', minWidth: c.kind === 'desc' ? 180 : 70 }} />
                            : <span>{it.cells[c.key]}</span>}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* totals (editable tax/price rows) */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
            <table style={{ fontSize: 13 }}>
              <tbody>
                {(doc.totals || []).map((t, i) => (
                  <tr key={t.key}>
                    <td style={{ color: 'var(--text2)', padding: '2px 10px 2px 0', textAlign: 'right' }}>{t.label}</td>
                    <td style={{ textAlign: 'right', fontWeight: 700, minWidth: 120 }}>
                      {t.editable ? <input value={t.amount ?? ''} onChange={e => setTotal(i, e.target.value)} style={{ ...input, textAlign: 'right' }} /> : money(t.amount)}
                    </td>
                  </tr>
                ))}
                {(doc.payments || []).map((p, i) => (
                  <tr key={`p${i}`}><td style={{ color: 'var(--text2)', padding: '2px 10px 2px 0', textAlign: 'right' }}>{p.label}</td><td style={{ textAlign: 'right', fontWeight: 700 }}>{money(p.amount)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>

          {!editingId && (
            <div style={{ display: 'flex', gap: 12, marginTop: 12, flexWrap: 'wrap' }}>
              <div><label style={label}>Store code (optional)</label><input value={storeCode} onChange={e => setStoreCode(e.target.value)} style={{ ...input, width: 140 }} /></div>
              <div style={{ flex: 1, minWidth: 240 }}><label style={label}>Note (optional) — saved on the import</label><input value={notes} onChange={e => setNotes(e.target.value)} style={input} /></div>
            </div>
          )}
          {savedId && <div style={{ marginTop: 10, color: '#0a7d33', fontSize: 13 }}>✓ Saved. Use 🖨 Print to reprint in the original format.</div>}
        </div>
      )}

      {/* imported receipts list */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '4px 0 10px' }}>
        <input placeholder="Search IMEI, phone, or customer…" value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && search()} style={{ ...input, maxWidth: 320 }} />
        <button className="btn btn-primary" onClick={search}>Search</button>
      </div>
      <div className="table-wrapper" style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, minWidth: 800 }}>
          <thead><tr style={{ textAlign: 'left', color: 'var(--text2)' }}>
            <th style={{ padding: 6 }}>POS</th><th style={{ padding: 6 }}>Invoice #</th><th style={{ padding: 6 }}>Customer</th>
            <th style={{ padding: 6 }}>Device</th><th style={{ padding: 6 }}>IMEI</th><th style={{ padding: 6 }}>Total</th>
            <th style={{ padding: 6 }}>Date</th><th style={{ padding: 6 }}></th>
          </tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: 6, textTransform: 'uppercase' }}>{r.pos_source || '—'}</td>
                <td style={{ padding: 6 }}>{r.invoice_no || '—'}</td>
                <td style={{ padding: 6 }}>{r.customer_name || '—'}</td>
                <td style={{ padding: 6 }}>{r.device_name || '—'}</td>
                <td style={{ padding: 6 }}>{r.imei || '—'}</td>
                <td style={{ padding: 6 }}>{money(r.total)}</td>
                <td style={{ padding: 6 }}>{r.sale_date || '—'}</td>
                <td style={{ padding: 6, whiteSpace: 'nowrap' }}>
                  <button className="btn btn-secondary" onClick={() => openStored(r.id)}>Edit</button>{' '}
                  <button className="btn btn-secondary" onClick={() => print(r.id)}>🖨</button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={8} style={{ padding: 16, color: 'var(--text2)', textAlign: 'center' }}>No imported receipts yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
