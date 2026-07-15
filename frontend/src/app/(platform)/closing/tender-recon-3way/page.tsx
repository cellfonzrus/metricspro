'use client'
import { useState, useEffect } from 'react'
import { api, fmt, localToday, apiUpload, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MultiSelect } from '@/lib/multiselect'

// 3-Way Tender Recon — the SAME day's tenders captured three independent ways, per store, per tender:
//  (1) Daily Closing (what the rep entered), (2) POS X-report, (3) Sales Transactions (raw_sales/feed).
// Reads GET /api/v1/closing/tender-recon-3way?date=. Click a Sales figure to drill into the transactions.

type Drill = { store: string; tender: string; label: string; storeName: string }

export default function TenderRecon3WayPage() {
  const [date, setDate] = useState(() => localToday())
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [selStores, setSelStores] = useState<string[]>([])
  const [storeMeta, setStoreMeta] = useState<{ store_code: string; store_address: string; market: string }[]>([])
  const [assetStores, setAssetStores] = useState<{ store: string; market: string }[]>([])
  const [onlyMismatch, setOnlyMismatch] = useState(false)
  const [drill, setDrill] = useState<Drill | null>(null)
  const [xrBusy, setXrBusy] = useState(false)
  const [xrMsg, setXrMsg] = useState('')

  async function uploadXReport(f: File) {
    setXrBusy(true); setXrMsg('')
    const form = new FormData(); form.append('file', f)
    try {
      const d: any = await apiUpload(`/api/v1/commcalc/upload/x_report?close_date=${encodeURIComponent(date)}&org_id=${ORG_ID}`, form)
      const n = d?.tenders ?? d?.rows_saved ?? 0
      setXrMsg(`✅ X‑Report ingested — ${n} tender rows${d?.date ? ' for ' + d.date : ''}. Recon refreshed.`)
      load()
    } catch (e: any) {
      setXrMsg(`❌ ${e?.message || String(e)}`)
    }
    setXrBusy(false)
  }

  function load() {
    setLoading(true)
    api(`/api/v1/closing/tender-recon-3way?date=${date}`)
      .then(setData).catch(e => setData({ error: e?.message || String(e) })).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [date])
  useEffect(() => {
    api('/api/v1/closing/stores').then((d: any) => setStoreMeta(Array.isArray(d) ? d : (d?.stores || d?.data || []))).catch(() => {})
    api('/api/v1/asset/filter-options').then((d: any) => setAssetStores(d?.stores || [])).catch(() => {})
  }, [])

  const tenders: { key: string; label: string }[] = data?.tenders || []
  const allStores: any[] = data?.stores || []
  // Market per store: prefer closing/stores by code, else asset market by address / leading street-number.
  const mktByCode: Record<string, string> = {}
  storeMeta.forEach(s => { if (s.store_code && s.market) mktByCode[s.store_code] = s.market })
  const mktByAddr: Record<string, string> = {}, mktByNum: Record<string, string> = {}
  const leadNum = (a: string) => (a.match(/^\s*([0-9][0-9-]*)/)?.[1] || '').replace(/\D/g, '')
  assetStores.forEach(s => {
    const a = (s.store || '').trim().toLowerCase(); if (!a || !s.market) return
    mktByAddr[a] = s.market
    const nk = leadNum(a); if (nk && !mktByNum[nk]) mktByNum[nk] = s.market
  })
  const marketOf = (s: any): string => {
    if (mktByCode[s.store_code]) return mktByCode[s.store_code]
    const a = (s.store_address || '').trim().toLowerCase()
    if (mktByAddr[a]) return mktByAddr[a]
    const nk = leadNum(a); return (nk && mktByNum[nk]) || ''
  }
  const markets = Array.from(new Set(allStores.map(marketOf).filter(Boolean))).sort()
  const storeOpts = allStores
    .filter(s => !selMarkets.length || selMarkets.includes(marketOf(s)))
    .map(s => ({ value: s.store_code, label: s.store_address }))
  const stores = allStores.filter(s =>
    (!selMarkets.length || selMarkets.includes(marketOf(s))) &&
    (!selStores.length || selStores.includes(s.store_code)) &&
    (!onlyMismatch || s.tenders.some((t: any) => !t.match)))
  function onMarketsChange(vs: string[]) {
    setSelMarkets(vs)
    const allowed = new Set(allStores.filter(s => !vs.length || vs.includes(marketOf(s))).map(s => s.store_code))
    setSelStores(prev => prev.filter(c => allowed.has(c)))
  }
  const sp = data?.sources_present || {}

  function buildPayload(): ExportPayload {
    const rows: any[] = []
    for (const s of stores) for (const t of s.tenders) rows.push({ store: s.store_address, ...t })
    return {
      title: '3-Way Tender Recon', subtitle: date,
      filename: `tender-recon-3way_${date}`,
      sheets: [{ name: 'By store/tender', rows, columns: [
        { header: 'Store', get: (r: any) => r.store },
        { header: 'Tender', get: (r: any) => r.label },
        { header: 'Closing', get: (r: any) => r.closing, money: true },
        { header: 'X-report', get: (r: any) => r.x_report, money: true },
        { header: 'Sales', get: (r: any) => r.sales, money: true },
        { header: 'Match', get: (r: any) => r.match ? 'OK' : 'CHECK' },
      ] }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧮 3-Way Tender Recon</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 720 }}>
            The same day’s money captured three ways — <strong>Daily Closing</strong> (rep entry),
            <strong> POS X-report</strong>, and <strong>Sales Transactions</strong> — per store, across
            cash / credit / external CC / gift card / store account / zelle. The X-report is generated from
            the sales transactions, so those two should agree; the closing is the human cross-check.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input className="select" type="date" value={date} onChange={e => setDate(e.target.value)} />
          <label className="btn" style={{ cursor: xrBusy ? 'wait' : 'pointer', whiteSpace: 'nowrap' }}
            title="Upload the POS X-Report for this day. A single-day report only — a date-range file is rejected.">
            {xrBusy ? '⏳ Uploading…' : '⬆ Upload X‑Report'}
            <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} disabled={xrBusy}
              onChange={e => { const f = e.target.files?.[0]; if (f) uploadXReport(f); e.currentTarget.value = '' }} />
          </label>
          {allStores.length > 0 && <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>}
        </div>
      </div>
      {xrMsg && <div style={{ fontSize: 12, marginBottom: 10, color: xrMsg.startsWith('❌') ? '#b91c1c' : 'var(--text2)' }}>{xrMsg}</div>}

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12, fontSize: 12 }}>
        <Src label="Daily Closing" ok={sp.closing} />
        <Src label="POS X-report" ok={sp.x_report} />
        <Src label="Sales Transactions" ok={sp.sales} />
        <Src label="Bank Deposit" ok={sp.bank_deposit} />
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {data.error}</div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <MultiSelect allLabel="All markets" width={140} value={selMarkets} options={markets} onChange={onMarketsChange} />
            <MultiSelect allLabel="All stores" width={150} value={selStores} searchable options={storeOpts} onChange={setSelStores} />
            <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={onlyMismatch} onChange={e => setOnlyMismatch(e.target.checked)} /> Stores with a mismatch only
            </label>
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>{stores.length} store(s)</span>
            {data?.note && <span style={{ fontSize: 11, color: 'var(--text3)', flex: '1 1 100%' }}>ℹ️ {data.note}</span>}
          </div>

          {stores.length === 0 ? (
            <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No tender data for {date}.</div>
          ) : stores.map((s: any) => (
            <div key={s.store_code} className="card" style={{ padding: 0, overflow: 'auto' }}>
              <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 700, fontSize: 14 }}>{s.store_address}</span>
                <span style={{ fontSize: 12, color: 'var(--text3)' }}>
                  totals — closing {fmt(s.totals.closing)} · X-report {fmt(s.totals.x_report)} · sales {fmt(s.totals.sales)}
                  {s.bank_deposit?.has_deposit && (
                    <> · bank deposit {fmt(s.bank_deposit.deposited)}
                      {s.bank_deposit.declared != null && <> vs {s.bank_deposit.match_target.replace('_', ' ')} {fmt(s.bank_deposit.declared)}</>}
                      {s.bank_deposit.flag
                        ? <span style={{ color: '#b91c1c', fontWeight: 700 }}> ⚠ {s.bank_deposit.var! >= 0 ? '+' : ''}{fmt(s.bank_deposit.var)}</span>
                        : <span style={{ color: '#15803d' }}> ✓</span>}
                      {s.bank_deposit.any_mismatch_flag && <span style={{ color: '#b91c1c' }}> · OCR flagged</span>}
                    </>
                  )}
                  {!s.bank_deposit?.has_deposit && <span> · bank deposit — not recorded</span>}
                </span>
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 620 }}>
                <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                  <th style={{ textAlign: 'left', padding: '7px 12px' }}>Tender</th>
                  <th style={{ textAlign: 'right', padding: '7px 12px' }}>Daily Closing</th>
                  <th style={{ textAlign: 'right', padding: '7px 12px' }}>X-report</th>
                  <th style={{ textAlign: 'right', padding: '7px 12px' }}>Sales Transactions</th>
                  <th style={{ textAlign: 'center', padding: '7px 12px' }}>Match</th>
                </tr></thead>
                <tbody>
                  {s.tenders.map((t: any) => (
                    <tr key={t.tender} style={{ borderTop: '1px solid var(--border)', background: t.match ? undefined : '#fffafa' }}>
                      <td style={{ padding: '7px 12px', fontSize: 13, fontWeight: 600 }}>{t.label}</td>
                      <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(t.closing)}</td>
                      <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(t.x_report)}</td>
                      <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>
                        <button onClick={() => setDrill({ store: s.store_code, tender: t.tender, label: t.label, storeName: s.store_address })}
                          style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: 13, textDecoration: 'underline', padding: 0 }}>
                          {fmt(t.sales)}
                        </button>
                      </td>
                      <td style={{ padding: '7px 12px', textAlign: 'center', fontSize: 12 }}>
                        {t.match ? <span style={{ color: '#15803d' }}>✓</span> : <span style={{ color: '#b91c1c', fontWeight: 700 }}>⚠</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}

      {drill && <DrillModal date={date} drill={drill} onClose={() => setDrill(null)} />}
    </div>
  )
}

function Src({ label, ok }: { label: string; ok?: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 10px', borderRadius: 20, border: '1px solid var(--border)', background: ok ? '#e7f6ec' : 'var(--surface2)' }}>
      <span style={{ color: ok ? '#15803d' : 'var(--text3)' }}>{ok ? '●' : '○'}</span>
      {label}{ok ? '' : ' — not loaded'}
    </span>
  )
}

function DrillModal({ date, drill, onClose }: { date: string; drill: Drill; onClose: () => void }) {
  const [rows, setRows] = useState<any[] | null>(null)
  const [total, setTotal] = useState(0)
  useEffect(() => {
    api(`/api/v1/closing/tender-drilldown?date=${date}&store=${encodeURIComponent(drill.store)}&tender=${drill.tender}`)
      .then(r => { setRows(r?.rows || []); setTotal(r?.total || 0) }).catch(() => setRows([]))
  }, [date, drill])
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 16 }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{ padding: 0, maxWidth: 820, width: '100%', maxHeight: '85vh', overflow: 'auto' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15 }}>{drill.label} — {drill.storeName}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{date} · sales transactions under this tender · total {fmt(total)}</div>
          </div>
          <button className="btn btn-secondary" onClick={onClose}>✕ Close</button>
        </div>
        {rows === null ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
              <th style={{ textAlign: 'left', padding: '7px 12px' }}>Trans ID</th>
              <th style={{ textAlign: 'left', padding: '7px 12px' }}>Salesperson</th>
              <th style={{ textAlign: 'left', padding: '7px 12px' }}>Product</th>
              <th style={{ textAlign: 'left', padding: '7px 12px' }}>Raw tender</th>
              <th style={{ textAlign: 'right', padding: '7px 12px' }}>Amount</th>
            </tr></thead>
            <tbody>
              {rows.map((r: any, i: number) => (
                <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '6px 12px', fontSize: 12 }}>{r.trans_id || '—'}</td>
                  <td style={{ padding: '6px 12px', fontSize: 12 }}>{r.salesperson || '—'}</td>
                  <td style={{ padding: '6px 12px', fontSize: 12 }}>{r.product_desc || '—'}</td>
                  <td style={{ padding: '6px 12px', fontSize: 12, color: 'var(--text3)' }}>{r.tender_type || '—'}</td>
                  <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(r.amount)}</td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={5} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No transactions under this tender for {date}.</td></tr>}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
