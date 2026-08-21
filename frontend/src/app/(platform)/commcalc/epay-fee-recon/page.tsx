'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportButtons, type ExportColumn, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MarketStorePicker, type StoreOpt } from '@/components/MarketStorePicker'

// ePay (Boost) FEE reconciliation report (P3, owner directive 2026-08-20).
// Per store-day: the "ePay service charge" fee OUR system rang (raw_sales) vs the fee the owner's Boost
// portal shows (Daily Transaction Detail). Biggest discrepancies first — a gap means fees rang on the
// register aren't reaching the portal (or vice-versa). Data: GET /commcalc/epay/fee-recon (scope-gated
// server-side by the caller's store keyset). Filters + export + send mirror the sibling recon reports.

interface ReconRow {
  store_code: string
  close_date: string
  system_fee: number
  portal_fee: number
  var: number
  in_system: boolean
  in_portal: boolean
  shortage: boolean   // portal has MORE fee than our system captured
  overage: boolean    // our system rang MORE fee than the portal shows
  flag: boolean
}
interface ReconTotals {
  system_fee: number; portal_fee: number; var: number; flagged: number; store_days: number
}
interface StoreMeta { store_code: string; store_address: string; market: string }

function statusText(r: ReconRow): string {
  if (!r.flag) return 'OK'
  if (r.shortage) return 'PORTAL > SYSTEM'
  if (r.overage) return 'SYSTEM > PORTAL'
  return 'FLAGGED'
}

export default function EpayFeeReconPage() {
  const [dateFrom, setDateFrom] = useState(() => localToday())
  const [dateTo, setDateTo] = useState(() => localToday())
  const [tolerance, setTolerance] = useState('1')
  const [data, setData] = useState<{ rows: ReconRow[]; totals: ReconTotals } | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [flaggedOnly, setFlaggedOnly] = useState(false)
  const [stores, setStores] = useState<StoreMeta[]>([])
  const [fMarkets, setFMarkets] = useState<string[]>([])
  const [fStores, setFStores] = useState<string[]>([])

  function load() {
    setLoading(true); setErr('')
    const qs = new URLSearchParams({ date_from: dateFrom, date_to: dateTo || dateFrom, tolerance: tolerance || '1' })
    api(`/api/v1/commcalc/epay/fee-recon?${qs.toString()}`)
      .then((d: any) => setData({ rows: d?.rows || [], totals: d?.totals || {} }))
      .catch((e: any) => { setErr(e?.message || String(e)); setData({ rows: [], totals: {} as ReconTotals }) })
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [dateFrom, dateTo, tolerance])
  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP)
      .then((s: any) => setStores(Array.isArray(s) ? s : (s?.stores || [])))
      .catch(() => {})
  }, [])

  // store_code → { address, market }, for display + the market/store filter (rows carry only store_code).
  const metaByCode = useMemo(() => {
    const m: Record<string, StoreMeta> = {}
    for (const s of stores) if (s.store_code) m[s.store_code] = s
    return m
  }, [stores])
  const addr = (code: string) => metaByCode[code]?.store_address || code
  const market = (code: string) => metaByCode[code]?.market || ''

  const storesForCascade: StoreOpt[] = useMemo(
    () => stores.filter(s => s.store_code).map(s => ({ id: s.store_code, label: s.store_address || s.store_code, market: s.market || null })),
    [stores])
  const fMarketsFold = useMemo(() => new Set(fMarkets.map(m => m.trim().toLowerCase())), [fMarkets])

  const allRows: ReconRow[] = data?.rows || []
  // Server already sorts biggest |variance| first; client filters preserve that order (WYSIWYG).
  const rows = allRows.filter(r =>
    (!flaggedOnly || r.flag) &&
    (!fStores.length || fStores.includes(r.store_code)) &&
    (!fMarketsFold.size || fMarketsFold.has(market(r.store_code).trim().toLowerCase())))

  // Totals recomputed from the VISIBLE rows so the total row ties out to what is on screen / exported.
  const totals = useMemo(() => rows.reduce((a, r) => ({
    system_fee: a.system_fee + (r.system_fee || 0),
    portal_fee: a.portal_fee + (r.portal_fee || 0),
    var: a.var + (r.var || 0),
    flagged: a.flagged + (r.flag ? 1 : 0),
  }), { system_fee: 0, portal_fee: 0, var: 0, flagged: 0 }), [rows])

  const rangeLabel = dateTo && dateTo !== dateFrom ? `${dateFrom} → ${dateTo}` : dateFrom

  function buildPayload(): ExportPayload {
    return {
      title: 'ePay Fee Reconciliation',
      subtitle: `${rangeLabel} · ${rows.length} store-day(s) · ${totals.flagged} flagged`,
      filename: `epay-fee-recon_${dateFrom}${dateTo && dateTo !== dateFrom ? `_${dateTo}` : ''}`,
      sheets: [{ name: 'Fee recon', rows, columns: [
        { header: 'Store', get: (r: ReconRow) => addr(r.store_code) },
        { header: 'Market', get: (r: ReconRow) => market(r.store_code) },
        { header: 'Date', get: (r: ReconRow) => r.close_date },
        { header: 'System fee (raw sales)', get: (r: ReconRow) => r.system_fee, money: true },
        { header: 'Portal fee (DTD)', get: (r: ReconRow) => r.portal_fee, money: true },
        { header: 'Variance', get: (r: ReconRow) => r.var, money: true },
        { header: 'Status', get: (r: ReconRow) => statusText(r) },
      ] as ExportColumn[] }],
    }
  }

  const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 ePay Fee Reconciliation</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            The Boost <strong>ePay service charge</strong> our system rang (from Sales / raw&nbsp;sales) vs the fee the
            owner&apos;s <strong>Boost portal</strong> shows (Daily Transaction Detail), per store‑day. <strong>Biggest
            discrepancies first.</strong> A gap means fees rang on the register aren&apos;t reaching the portal — or the reverse.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: 'var(--text3)', display: 'flex', alignItems: 'center', gap: 4 }}>From
            <input style={inp} type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} /></label>
          <label style={{ fontSize: 12, color: 'var(--text3)', display: 'flex', alignItems: 'center', gap: 4 }}>To
            <input style={inp} type="date" value={dateTo} min={dateFrom} onChange={e => setDateTo(e.target.value)} /></label>
          {rows.length > 0 && <ExportButtons payload={buildPayload} compact />}
          {rows.length > 0 && <SendReportButton exportPayload={buildPayload} title="ePay Fee Reconciliation" compact />}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 14, marginBottom: 16 }}>
        <Stat label="System fee (raw sales)" value={fmt(totals.system_fee)} />
        <Stat label="Portal fee (DTD)" value={fmt(totals.portal_fee)} color="#16a34a" />
        <Stat label="Net variance" value={`${totals.var >= 0 ? '+' : ''}${fmt(totals.var)}`} color={Math.abs(totals.var) > 0.005 ? '#dc2626' : 'var(--text1)'} />
        <Stat label="Store‑days flagged" value={`${totals.flagged} / ${rows.length}`} color={totals.flagged ? '#dc2626' : '#059669'} />
      </div>

      <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <MarketStorePicker
          stores={storesForCascade}
          selectedMarkets={fMarkets} onMarketsChange={setFMarkets}
          selectedStores={fStores} onStoresChange={setFStores}
        />
        <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={flaggedOnly} onChange={e => setFlaggedOnly(e.target.checked)} /> Discrepancies only
        </label>
        <label style={{ fontSize: 12, color: 'var(--text3)', display: 'flex', alignItems: 'center', gap: 4 }}>Tolerance ±$
          <input style={{ ...inp, width: 64 }} inputMode="decimal" value={tolerance} onChange={e => setTolerance(e.target.value)} /></label>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{rows.length} store‑day(s)</span>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : err ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {err}</div>
      ) : rows.length === 0 ? (
        <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>
          No ePay fee data for {rangeLabel}.
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 820 }}>
            <thead><tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
              {['Store', 'Market', 'Date', 'System fee', 'Portal fee', 'Variance', 'Status'].map(h =>
                <th key={h} style={{ textAlign: h === 'Store' || h === 'Market' || h === 'Date' ? 'left' : 'right', padding: '8px 12px', whiteSpace: 'nowrap' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.store_code}_${r.close_date}_${i}`} style={{ borderTop: '1px solid var(--border)', background: r.flag ? '#fffafa' : undefined }}>
                  <td style={{ padding: '9px 12px', fontSize: 13, fontWeight: 600 }}>{addr(r.store_code)}</td>
                  <td style={{ padding: '9px 12px', fontSize: 12, color: 'var(--text3)' }}>{market(r.store_code) || '—'}</td>
                  <td style={{ padding: '9px 12px', fontSize: 13, color: 'var(--text2)' }}>{r.close_date}</td>
                  <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(r.system_fee)}</td>
                  <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, color: '#16a34a' }}>{fmt(r.portal_fee)}</td>
                  <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, fontWeight: 700, color: r.flag ? '#dc2626' : 'var(--text1)' }}>{r.var >= 0 ? '+' : ''}{fmt(r.var)}</td>
                  <td style={{ padding: '9px 12px', textAlign: 'center', fontSize: 12 }}>
                    {r.flag
                      ? <span style={{ background: '#fee2e2', color: '#b91c1c', borderRadius: 5, padding: '1px 8px', fontWeight: 600, whiteSpace: 'nowrap' }}>{statusText(r)}</span>
                      : <span style={{ color: '#059669' }}>✓ OK</span>}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr style={{ borderTop: '2px solid var(--border)', background: 'var(--surface2)', fontWeight: 700 }}>
                <td style={{ padding: '10px 12px', fontSize: 13 }}>TOTAL ({rows.length} store‑days)</td>
                <td /><td />
                <td style={{ padding: '10px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(totals.system_fee)}</td>
                <td style={{ padding: '10px 12px', textAlign: 'right', fontSize: 13, color: '#16a34a' }}>{fmt(totals.portal_fee)}</td>
                <td style={{ padding: '10px 12px', textAlign: 'right', fontSize: 13, color: Math.abs(totals.var) > 0.005 ? '#dc2626' : 'var(--text1)' }}>{totals.var >= 0 ? '+' : ''}{fmt(totals.var)}</td>
                <td style={{ padding: '10px 12px', textAlign: 'center', fontSize: 12, color: 'var(--text3)' }}>{totals.flagged} flagged</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 12, maxWidth: 820 }}>
        &ldquo;System fee&rdquo; = the ePay service charge captured in our sales feed (raw&nbsp;sales, the SAME figure the
        P&amp;L books as ePay fee income). &ldquo;Portal fee&rdquo; = the &ldquo;…FEE&rdquo; lines on the Boost Daily
        Transaction Detail. <strong>PORTAL&nbsp;&gt;&nbsp;SYSTEM</strong> = the portal counted more fee than the register
        rang; <strong>SYSTEM&nbsp;&gt;&nbsp;PORTAL</strong> = the reverse. Flagged when |variance| exceeds the tolerance.
      </p>
    </div>
  )
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return <div className="card" style={{ padding: '14px 16px' }}>
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: color || 'var(--text1)' }}>{value}</div>
  </div>
}
