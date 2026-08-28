'use client'
// POS — Vendor rebate / activation report import (migration 867). Upload the carrier's Vendor
// Rebate/Commission History .xlsx; the parser classifies each line (device rebate vs commission),
// computes device gross profit (rebate − cost) and per-store/period totals. A dry-run PREVIEW shows
// the numbers first; Import then creates customers + activations and feeds the P&L
// (commission → carrier_comm, device rebate → device_rebate contra-COGS). Backend: /pos/activation-report/*.
import { useState, type CSSProperties } from 'react'
import { api } from '@/lib/client'

type Family = { product_name: string | null; kind: string; count: number; amount: number }
type StorePeriod = {
  store_name: string | null; period: string | null; rows: number; activations: number
  commission_income: number; device_rebate: number; device_cost: number; device_gp: number
}
interface Totals {
  rows_in: number; activations: number; commission_income: number; device_rebate: number
  device_cost: number; device_gp: number; gross_profit_total: number
  distinct_customers: number; distinct_imeis: number
  dropped: { totals_row: number; trade_in: number; no_amount: number }
}
interface Preview { totals: Totals; families: Family[]; summary_by_store_period: StorePeriod[]; sample: Record<string, unknown>[] }
interface ImportResult { imported: true; customers_created: number; activations_created: number; ledger_periods: number; totals: Totals }

const money = (n: unknown) => {
  const v = Number(n)
  return Number.isFinite(v) ? `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'
}
const fileToB64 = (f: File) =>
  new Promise<string>((res, rej) => {
    const r = new FileReader()
    r.onload = () => res(String(r.result).split(',')[1] || '')
    r.onerror = rej
    r.readAsDataURL(f)
  })

const input: CSSProperties = { padding: '4px 6px', border: '1px solid var(--border)', borderRadius: 4, background: 'var(--bg)', color: 'var(--text)', fontSize: 13 }
const label: CSSProperties = { fontSize: 11, color: 'var(--text2)', display: 'block', marginBottom: 2 }
const th: CSSProperties = { textAlign: 'left', padding: '4px 8px', borderBottom: '1.5px solid var(--border)', whiteSpace: 'nowrap' }
const td: CSSProperties = { padding: '3px 8px', borderBottom: '1px solid var(--border)' }
const tdr: CSSProperties = { ...td, textAlign: 'right' }

export default function ActivationReportPage() {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [file, setFile] = useState('')            // base64
  const [fileName, setFileName] = useState('')
  const [storeCode, setStoreCode] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [result, setResult] = useState<ImportResult | null>(null)

  const onFile = async (f: File | null) => {
    if (!f) return
    setError(''); setBusy('parse'); setResult(null); setPreview(null); setFileName(f.name)
    try {
      const b64 = await fileToB64(f)
      setFile(b64)
      const r: Preview = await api('/api/v1/pos/activation-report/preview', {
        method: 'POST', body: JSON.stringify({ file: b64 }),
      })
      setPreview(r)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not read the report') }
    finally { setBusy(null) }
  }

  const doImport = async () => {
    if (!file) return
    setBusy('import'); setError('')
    try {
      const r: ImportResult = await api('/api/v1/pos/activation-report/import', {
        method: 'POST', body: JSON.stringify({ file, store_code: storeCode || undefined }),
      })
      setResult(r)
    } catch (e) { setError(e instanceof Error ? e.message : 'Import failed') }
    finally { setBusy(null) }
  }

  const t = preview?.totals
  return (
    <div style={{ padding: 20, maxWidth: 1100, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>📶 Activation / Rebate Report</h1>
      <p style={{ color: 'var(--text2)', margin: '0 0 16px', fontSize: 13 }}>
        Upload the carrier&apos;s Vendor Rebate/Commission History (.xlsx). We create customers + activations and feed the
        commission into the P&amp;L (device rebate nets against device cost as a contra-COGS).
      </p>

      {error && <div style={{ background: '#fde8e8', color: '#9b1c1c', padding: '8px 12px', borderRadius: 6, marginBottom: 12, fontSize: 13 }}>{error}</div>}

      <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, marginBottom: 18, display: 'flex', gap: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div>
          <label style={label}>Report file (.xlsx)</label>
          <input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={e => onFile(e.target.files?.[0] || null)} style={{ fontSize: 13 }} />
        </div>
        <div>
          <label style={label}>Store code (optional)</label>
          <input value={storeCode} onChange={e => setStoreCode(e.target.value)} style={{ ...input, width: 140 }} />
        </div>
        {busy === 'parse' && <span style={{ color: 'var(--text2)', fontSize: 13 }}>Reading {fileName}…</span>}
      </div>

      {t && (
        <>
          {/* headline totals */}
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
            <Stat label="Activations" value={t.activations.toLocaleString()} />
            <Stat label="Commission income" value={money(t.commission_income)} good />
            <Stat label="Device rebate" value={money(t.device_rebate)} />
            <Stat label="Device cost" value={money(t.device_cost)} />
            <Stat label="Device GP (rebate − cost)" value={money(t.device_gp)} warn={t.device_gp < 0} />
            <Stat label="Customers" value={t.distinct_customers.toLocaleString()} />
          </div>
          {t.device_gp < 0 && (
            <div style={{ background: '#fff7e6', color: '#8a5a00', padding: '8px 12px', borderRadius: 6, marginBottom: 14, fontSize: 12 }}>
              Note: device GP (rebate − cost) is negative because the customer-paid device revenue isn&apos;t in this report.
              The P&amp;L feed books the rebate as a <b>contra-COGS</b> (it reduces the device cost already booked from sales),
              so it does <b>not</b> post this figure as a standalone loss.
            </div>
          )}

          {/* families */}
          <h2 style={{ fontSize: 15, fontWeight: 700, margin: '10px 0 6px' }}>Line families</h2>
          <div style={{ overflowX: 'auto', marginBottom: 16 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead><tr><th style={th}>Product Name</th><th style={th}>Treated as</th><th style={{ ...th, textAlign: 'right' }}>Count</th><th style={{ ...th, textAlign: 'right' }}>Unit Rebate $</th></tr></thead>
              <tbody>
                {preview!.families.map((f, i) => (
                  <tr key={i}>
                    <td style={td}>{f.product_name || '(blank)'}</td>
                    <td style={td}><Badge kind={f.kind} /></td>
                    <td style={tdr}>{f.count.toLocaleString()}</td>
                    <td style={tdr}>{money(f.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* per store / period */}
          <h2 style={{ fontSize: 15, fontWeight: 700, margin: '10px 0 6px' }}>By store &amp; month (P&amp;L)</h2>
          <div style={{ overflowX: 'auto', marginBottom: 16 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead><tr><th style={th}>Store</th><th style={th}>Period</th><th style={{ ...th, textAlign: 'right' }}>Activations</th><th style={{ ...th, textAlign: 'right' }}>Commission → carrier_comm</th><th style={{ ...th, textAlign: 'right' }}>Rebate → device_rebate</th></tr></thead>
              <tbody>
                {preview!.summary_by_store_period.map((s, i) => (
                  <tr key={i}>
                    <td style={td}>{s.store_name || '—'}</td>
                    <td style={td}>{s.period || '—'}</td>
                    <td style={tdr}>{s.activations.toLocaleString()}</td>
                    <td style={tdr}>{money(s.commission_income)}</td>
                    <td style={tdr}>{money(s.device_rebate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            {!result && <button className="btn btn-primary" disabled={busy === 'import'} onClick={doImport}>{busy === 'import' ? 'Importing…' : 'Import (create customers + activations, feed P&L)'}</button>}
            {result && (
              <div style={{ color: '#0a7d33', fontSize: 13 }}>
                ✓ Imported — {result.customers_created} new customers, {result.activations_created} activations,
                {' '}{result.ledger_periods} store/period P&amp;L rows. Re-running the same file is a no-op.
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function Stat({ label, value, good, warn }: { label: string; value: string; good?: boolean; warn?: boolean }) {
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px', minWidth: 140 }}>
      <div style={{ fontSize: 11, color: 'var(--text2)' }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 800, color: warn ? '#b45309' : good ? '#0a7d33' : 'var(--text)' }}>{value}</div>
    </div>
  )
}

function Badge({ kind }: { kind: string }) {
  const map: Record<string, [string, string]> = {
    commission: ['Commission', '#0a7d33'],
    device_rebate: ['Device rebate', '#2563eb'],
    trade_in: ['Trade-in (skip)', '#8a5a00'],
  }
  const [txt, color] = map[kind] || [kind, 'var(--text2)']
  return <span style={{ fontSize: 11, fontWeight: 700, color }}>{txt}</span>
}
