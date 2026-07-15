'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

// Executive MTD summary — replicates b2bsoft's "Month To Date Location / Employee Sales Report".
// Reads the org-corrected sales source (feed for the open month, raw_sales for a closed one) so it works
// for luxelink AND the house org with NO monthly upload. DISPLAY-ONLY.

const th: React.CSSProperties = { textAlign: 'right', padding: '7px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
const thL: React.CSSProperties = { ...th, textAlign: 'left' }
const td: React.CSSProperties = { textAlign: 'right', padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 12.5, whiteSpace: 'nowrap' }
const tdL: React.CSSProperties = { ...td, textAlign: 'left' }

const pct = (n: number) => `${((n || 0) * 100).toFixed(1)}%`
const n2 = (n: number) => Number(n || 0).toFixed(2)
const int = (n: number) => String(Math.round(n || 0))

export default function ExecMtdPage() {
  const { period } = usePeriod()
  const [data, setData] = useState<any>(null)
  const [tab, setTab] = useState<'location' | 'employee'>('location')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [showCfg, setShowCfg] = useState(false)

  const load = useCallback(() => {
    if (!period) return
    setLoading(true); setErr(null)
    api(`/api/v1/commcalc/exec-mtd/${encodeURIComponent(period)}`)
      .then(setData).catch((e) => setErr(String(e?.message || e))).finally(() => setLoading(false))
  }, [period])
  useEffect(() => { load() }, [load])

  const active = tab === 'location' ? data?.by_location : data?.by_employee
  const labelKey = tab === 'location' ? 'store' : 'employee'
  const labelHdr = tab === 'location' ? 'Store' : 'Employee'
  const rows: any[] = active?.rows || []
  const total = active?.total || {}
  const tr = data?.trending || {}

  // 16-column layout, in the exact order of the owner's spreadsheet. Conv. exported as the raw ratio (as
  // the file stores it); money columns flagged so Excel/PDF format + subtotal correctly.
  const cols = (lk: string): ExportColumn[] => [
    { header: lk === 'store' ? 'Store' : 'Employee', field: lk, role: lk === 'store' ? 'store' : 'rep', get: (r) => r[lk] },
    { header: 'Total Activation', field: 'total_activation', type: 'number', get: (r) => r.total_activation },
    { header: 'Activation', field: 'activation', type: 'number', get: (r) => r.activation },
    { header: 'Port', field: 'port', type: 'number', get: (r) => r.port },
    { header: 'BYOD', field: 'byod', type: 'number', get: (r) => r.byod },
    { header: 'Upgrade', field: 'upgrade', type: 'number', get: (r) => r.upgrade },
    { header: 'Total Phones', field: 'total_phones', type: 'number', get: (r) => r.total_phones },
    { header: 'Trending Box', field: 'trending_box', type: 'number', get: (r) => r.trending_box },
    { header: 'Bill Payment Qty', field: 'bill_payment_qty', type: 'number', get: (r) => r.bill_payment_qty },
    { header: '$', field: 'amount', money: true, get: (r) => r.amount },
    { header: 'Conv.', field: 'conv', type: 'number', get: (r) => r.conv },
    { header: 'Acc. Sales', field: 'acc_sales', money: true, get: (r) => r.acc_sales },
    { header: 'APB', field: 'apb', type: 'number', get: (r) => r.apb },
    { header: 'Trending Acc. Sales', field: 'trending_acc_sales', money: true, get: (r) => r.trending_acc_sales },
    { header: 'Activation Fee', field: 'activation_fee', money: true, get: (r) => r.activation_fee },
    { header: 'Total Protect', field: 'total_protect', type: 'number', get: (r) => r.total_protect },
  ]
  // Export BOTH tabs (like the file's two sheets), each with its own totals row appended.
  const withTotal = (rs: any[], tot: any, lk: string) => [...rs, { ...tot, [lk]: 'TOTAL' }]
  const exportSheets = [
    { name: 'By location', columns: cols('store'), rows: withTotal(data?.by_location?.rows || [], data?.by_location?.total || {}, 'store') },
    { name: 'By employee', columns: cols('employee'), rows: withTotal(data?.by_employee?.rows || [], data?.by_employee?.total || {}, 'employee') },
  ]

  const HEADERS = ['Total Activation', 'Activation', 'Port', 'BYOD', 'Upgrade', 'Total Phones', 'Trending Box',
    'Bill Payment Qty', '$', 'Conv.', 'Acc. Sales', 'APB', 'Trending Acc. Sales', 'Activation Fee', 'Total Protect']

  const cellVals = (r: any) => [
    int(r.total_activation), int(r.activation), int(r.port), int(r.byod), int(r.upgrade), int(r.total_phones),
    int(r.trending_box), int(r.bill_payment_qty), fmt(r.amount), pct(r.conv), fmt(r.acc_sales), n2(r.apb),
    fmt(r.trending_acc_sales), fmt(r.activation_fee), int(r.total_protect),
  ]

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 21, fontWeight: 700, margin: 0 }}>📈 Executive MTD — {period}</h1>
          <Link href="/commcalc/exec" style={{ fontSize: 12 }}>← Owner Overview</Link>
        </div>
        <p style={{ color: 'var(--text2)', fontSize: 13.5, margin: '4px 0 0' }}>
          Month-to-date location &amp; employee sales — activations by type, phones, bill payments, accessory
          sales, and trending projections. {tr.factor ? `Trending = MTD × ${tr.days_in_month} ÷ ${tr.elapsed_days} complete days.` : ''}
        </p>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
        <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
          {(['location', 'employee'] as const).map((tb) => (
            <button key={tb} onClick={() => setTab(tb)}
              style={{ padding: '6px 14px', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer',
                background: tab === tb ? 'var(--accent)' : 'transparent', color: tab === tb ? '#fff' : 'var(--text2)' }}>
              {tb === 'location' ? 'By location' : 'By employee'}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <button onClick={() => setShowCfg((s) => !s)} style={{ fontSize: 12, background: 'none', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 10px', cursor: 'pointer', color: 'var(--text2)' }}>
            ⚙︎ Metric definitions
          </button>
          <ReportExportBar title={`Executive MTD ${period}`} filename={`exec_mtd_${String(period).replace(/\s+/g, '_')}`} sheets={exportSheets} />
        </div>
      </div>

      {showCfg && <MetricConfigPanel onClose={() => setShowCfg(false)} onSaved={load} />}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : err ? (
        <div className="card" style={{ padding: 16, borderLeft: '3px solid #b42318', color: '#b42318', fontSize: 13 }}>Could not load MTD summary: {err}</div>
      ) : rows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
          No sales for {period}. (If the daily feed is ingesting but this is empty, run the sales promotion —
          POST /commcalc/sales/promote-due — or check the tenant&apos;s sales mailbox.)
        </div>
      ) : (
        <div className="card table-wrapper" style={{ padding: 0 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              <th style={thL}>{labelHdr}</th>
              {HEADERS.map((h) => <th key={h} style={th}>{h}</th>)}
            </tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td style={{ ...tdL, fontWeight: 600 }}>{r[labelKey] || '—'}</td>
                  {cellVals(r).map((v, j) => <td key={j} style={td}>{v}</td>)}
                </tr>
              ))}
              <tr style={{ background: 'var(--surface2)', fontWeight: 700 }}>
                <td style={{ ...tdL, fontWeight: 800 }}>TOTAL</td>
                {cellVals(total).map((v, j) => <td key={j} style={{ ...td, fontWeight: 700 }}>{v}</td>)}
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Admin-editable metric definitions (SAP-configurable; no hard-coded classifier) ──────────────────
function MetricConfigPanel({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [cfg, setCfg] = useState<any>(null)
  const [saving, setSaving] = useState<string | null>(null)
  useEffect(() => { api('/api/v1/commcalc/exec-metric-config').then(setCfg).catch(console.error) }, [])
  const BUCKET_KEYS: Record<string, string[]> = {
    activation: ['byod', 'upgrade', 'port'],
    phones: ['category'],
    bill_payment: ['department', 'category'],
    accessory: ['category'],
    activation_fee: ['product_desc_contains'],
    protect: ['product_desc_contains', 'exclude_product_desc_contains', 'exclude_department', 'exclude_category'],
  }
  const setTok = (bucket: string, key: string, csv: string) => {
    setCfg((c: any) => {
      const nc = { ...c, config: { ...c.config, [bucket]: { ...c.config[bucket], rules: { ...c.config[bucket].rules, [key]: csv.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean) } } } }
      return nc
    })
  }
  const save = (bucket: string) => {
    setSaving(bucket)
    api('/api/v1/commcalc/exec-metric-config', { method: 'PUT', body: JSON.stringify({ bucket, rules: cfg.config[bucket].rules, basis: cfg.config[bucket].basis }) })
      .then(() => onSaved()).catch(console.error).finally(() => setSaving(null))
  }
  if (!cfg) return <div className="card" style={{ padding: 16, marginBottom: 12 }}>Loading definitions…</div>
  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>Metric definitions (config — comma-separated tokens, case-insensitive)</div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text3)' }}>✕</button>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>
        These map raw sales lines to the MTD buckets. Tokens match the stored <code>department</code> /
        <code>category</code> (a b2bsoft export stores either the Category or the System Category column) or
        a substring of <code>product_desc</code>. Editing here does NOT touch commission pay.
      </p>
      {(cfg.buckets as string[]).map((b) => (
        <div key={b} style={{ borderTop: '1px solid var(--border)', padding: '8px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>{b} <span style={{ color: 'var(--text3)', fontWeight: 400 }}>({cfg.config[b].basis})</span></div>
            <button onClick={() => save(b)} disabled={saving === b} style={{ fontSize: 12, padding: '3px 10px', cursor: 'pointer' }}>{saving === b ? 'Saving…' : 'Save'}</button>
          </div>
          {(BUCKET_KEYS[b] || Object.keys(cfg.config[b].rules)).map((k) => (
            <label key={k} style={{ display: 'grid', gridTemplateColumns: '190px 1fr', gap: 8, alignItems: 'center', margin: '4px 0' }}>
              <span style={{ fontSize: 12, color: 'var(--text2)' }}>{k}</span>
              <input value={(cfg.config[b].rules[k] || []).join(', ')} onChange={(e) => setTok(b, k, e.target.value)}
                style={{ fontSize: 12, padding: '4px 8px', border: '1px solid var(--border)', borderRadius: 6 }} />
            </label>
          ))}
        </div>
      ))}
    </div>
  )
}
