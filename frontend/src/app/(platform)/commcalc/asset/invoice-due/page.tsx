'use client'
// Upcoming Invoice Payment Due (OWNER DIRECTIVE 2026-08-05). Built against commcalc.vip_invoices /
// vip_invoice_devices (the "VIP Wireless Workbook" upload) + asset_ledger + ePay raw_payment_detail —
// NOT the literal "MA Handset Ordering" (raw_ma_fulfillment) report, which was audited and found to
// carry no invoice_number/due_date/grand_total/per-unit-IMEI at all. See backend/app/modules/asset/
// invoice_due.py's module docstring and docs/handoffs/asset.md for the full Phase-1 feasibility matrix.
//
// GATING: same idiom as purchase-orders/vendors (admin-only client-side mirror; the backend is the
// real enforcement via the 'asset_invoice_due' DATA_GRANT, degrade-open on unresolvable caller).
//
// RULE FIVE core set: due-date range (period) + store multi-select + market. DOCUMENTED DEVIATION:
// no rep/employee filter — a VIP invoice has no rep/salesperson attribution (same deviation, same
// reasoning, as the Marketplace Handset COGS report). Module-specific: status multi-select,
// invoice-number search.
//
// RULE FOUR: ExportButtons + SendReportButton via the in-browser exportPayload path (never a
// reportKey+filters server re-query — the export-filter-fix package's whole point).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { useAuth } from '@/lib/auth-context'
import { ExportButtons, ExportPayload, ExportColumn } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MultiSelect } from '@/lib/multiselect'

const NO_MARKET_VALUE = '__no_market__'

type InvoiceRow = {
  vip_id: number; invoice_number: string | null; order_number: string | null
  location: string | null; market: string | null; status: string | null
  due_date: string | null; grand_total: number; period: string | null
  device_count: number; matched_count: number; unmatched_count: number
  sold_count: number; not_sold_count: number; reimbursed_count: number
  commission_earned_m1: number; net_due_estimate: number; net_due_estimate_note: string
}
type ListResp = {
  available: boolean; note?: string; rows: InvoiceRow[]; total: number
  totals: {
    grand_total: number; commission_earned_m1: number; net_due_estimate: number
    invoice_count: number; device_count: number; sold_count: number
    not_sold_count: number; reimbursed_count: number
  }
  basis_note?: string
}
type DeviceRow = {
  serial: string | null; imei: string | null; product_name: string | null; matched: boolean
  store: string | null; market: string | null; device_model: string | null
  sold: boolean; date_sold: string | null; reimbursed: boolean
  reimbursement: number | null; reimbursement_date: string | null; owed_to_vip: number | null
  commission_m1: number; commission_trailing: number; commission_unsplit: number
  commission_lines: { type: string; amount: number; date: string | null; period: string | null; leg: string }[]
}
type DetailResp = {
  invoice: any; devices: DeviceRow[]
  device_count: number; sold_count: number; not_sold_count: number; reimbursed_count: number
  commission_earned_m1: number; net_due_estimate: number; net_due_estimate_note: string
  period_commission_footer: {
    period: string | null; invoice_commission_m1: number
    period_total_commission_m1: number | null; difference: number | null; note: string
  }
}

const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }
const selStyle: React.CSSProperties = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const tile: React.CSSProperties = { flex: 1, minWidth: 155, border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }
const tileCap: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', fontWeight: 700, textTransform: 'uppercase' }
const th: React.CSSProperties = { textAlign: 'left', padding: '7px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '6px 9px', borderTop: '1px solid var(--border)', fontSize: 13 }
const d10 = (s: string | null) => (s ? String(s).slice(0, 10) : '—')

function isGateError(m: string) { return /asset_invoice_due/i.test(m) || /restricted/i.test(m) }

function LockNote() {
  return (
    <div className="card" style={{ padding: 16, background: '#fef3c7', border: '1px solid #fcd34d' }}>
      🔒 <b>Restricted.</b> The Upcoming Invoice Payment Due report shows invoice totals and
      commission figures — ask an admin to grant <b>"asset_invoice_due"</b> on your role.
    </div>
  )
}

function daysUntil(due: string | null) {
  if (!due) return null
  const d = new Date(due + 'T00:00:00')
  if (isNaN(d.getTime())) return null
  return Math.round((d.getTime() - new Date(new Date().toDateString()).getTime()) / 86400000)
}

function DueBadge({ due, status }: { due: string | null; status: string | null }) {
  const s = (status || '').toLowerCase()
  if (s === 'paid in full' || s === 'voided') return <span style={{ color: 'var(--text3)' }}>{status}</span>
  const n = daysUntil(due)
  if (n === null) return <span>{status || '—'}</span>
  if (n < 0) return <span style={{ color: '#b91c1c', fontWeight: 700 }}>Overdue {Math.abs(n)}d</span>
  if (n <= 7) return <span style={{ color: '#b45309', fontWeight: 700 }}>Due in {n}d</span>
  return <span style={{ color: 'var(--text2)' }}>Due in {n}d</span>
}

export default function InvoiceDuePage() {
  const { user, permissions } = useAuth()
  const isAdmin = !!(user?.super_admin || (permissions as any)?.scope === 'all' || (user?.role || '').toLowerCase() === 'admin')

  const [statuses, setStatuses] = useState<string[]>([])
  const [statusOpts, setStatusOpts] = useState<string[]>([])
  const [stores, setStores] = useState<string[]>([])
  const [storeOpts, setStoreOpts] = useState<string[]>([])
  const [market, setMarket] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState(() => {
    const d = new Date(); d.setDate(d.getDate() + 60)
    return d.toISOString().slice(0, 10)
  })
  const [invoiceQ, setInvoiceQ] = useState('')
  const [data, setData] = useState<ListResp | null>(null)
  const [loading, setLoading] = useState(true)
  const [errMsg, setErrMsg] = useState('')
  const [gated, setGated] = useState(false)
  const [openId, setOpenId] = useState<number | null>(null)
  const [detail, setDetail] = useState<DetailResp | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [syncMsg, setSyncMsg] = useState('')

  useEffect(() => {
    apiCached(`/api/v1/asset/invoice-due/filter-options?1=1${orgParam()}`, LOOKUP)
      .then((d: any) => {
        if (d.available === false) { setErrMsg(d.note || 'Not available yet.'); return }
        setStatusOpts(d.statuses || []); setStoreOpts(d.stores || [])
      })
      .catch((e: any) => { if (isGateError(e?.message || '')) setGated(true) })
  }, [])

  const load = useCallback(async () => {
    setLoading(true); setErrMsg('')
    try {
      const qs = new URLSearchParams()
      if (statuses.length) qs.set('status', statuses.join(','))
      if (stores.length) qs.set('store', stores.join(','))
      if (market) qs.set('market', market)
      if (dateFrom) qs.set('date_from', dateFrom)
      if (dateTo) qs.set('date_to', dateTo)
      if (invoiceQ.trim()) qs.set('invoice_number', invoiceQ.trim())
      const d: ListResp = await api(`/api/v1/asset/invoice-due?${qs.toString()}${orgParam()}`)
      if (d.available === false) { setErrMsg(d.note || 'Not available yet.'); setData(d) }
      else setData(d)
    } catch (e: any) {
      if (isGateError(e?.message || '')) setGated(true)
      else setErrMsg('Could not load: ' + (e?.message || e))
    }
    setLoading(false)
  }, [statuses, stores, market, dateFrom, dateTo, invoiceQ])
  useEffect(() => { load() }, [load])

  async function openDetail(vipId: number) {
    if (openId === vipId) { setOpenId(null); setDetail(null); return }
    setOpenId(vipId); setDetail(null); setDetailLoading(true)
    try {
      setDetail(await api(`/api/v1/asset/invoice-due/${vipId}?1=1${orgParam()}`))
    } catch (e: any) { setErrMsg('Could not load invoice detail: ' + (e?.message || e)) }
    setDetailLoading(false)
  }

  async function syncFlags() {
    setSyncMsg('Syncing…')
    try {
      const r = await api(`/api/v1/asset/sync-invoice-due-flags?1=1${orgParam()}`, { method: 'POST' })
      setSyncMsg(`✅ ${r.flags_written} attention flag(s) written (overdue = critical, due within 7 days = warning).`)
    } catch (e: any) { setSyncMsg('Failed: ' + (e?.message || e)) }
  }

  function buildPayload(): ExportPayload {
    const rows = data?.rows || []
    const cols: ExportColumn[] = [
      { header: 'Invoice #', get: r => r.invoice_number || r.vip_id },
      { header: 'Order #', get: r => r.order_number },
      { header: 'Store', get: r => r.location, role: 'store' },
      { header: 'Market', get: r => r.market },
      { header: 'Status', get: r => r.status },
      { header: 'Due Date', get: r => d10(r.due_date), role: 'date' },
      { header: 'Total Due', get: r => r.grand_total, money: true },
      { header: 'Devices', get: r => r.device_count, align: 'right' },
      { header: 'Sold', get: r => r.sold_count, align: 'right' },
      { header: 'Not Sold', get: r => r.not_sold_count, align: 'right' },
      { header: 'Reimbursed', get: r => r.reimbursed_count, align: 'right' },
      { header: 'Commission Earned (M1)', get: r => r.commission_earned_m1, money: true },
      { header: 'Net Due Estimate (info only)', get: r => r.net_due_estimate, money: true },
    ]
    const filterParts = [
      statuses.length ? `Status: ${statuses.join(', ')}` : null,
      stores.length ? `Store: ${stores.join(', ')}` : null,
      market === NO_MARKET_VALUE ? '(no market)' : market || null,
      (dateFrom || dateTo) ? `Due ${dateFrom || '…'} to ${dateTo || '…'}` : null,
      invoiceQ ? `Invoice # contains "${invoiceQ}"` : null,
    ].filter(Boolean)
    return {
      title: 'Upcoming Invoice Payment Due',
      subtitle: (filterParts.join(' · ') || 'All invoices') +
        ' — Commission Earned (M1) = 1st-Month ePay commission only (spiff/BYOD/activation bounty); ' +
        'residual and M2-M12 excluded. Net Due Estimate is INFO ONLY and unverified against any real ' +
        'VidaPay deduction record.',
      filename: 'upcoming-invoice-payment-due',
      sheets: [{ name: 'Invoices', rows, columns: cols }],
    }
  }

  const t = data?.totals

  if (!isAdmin) return <LockNote />
  if (gated) return <LockNote />

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <a href="/commcalc/asset" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Asset Ledger</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>🧾 Upcoming Invoice Payment Due</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 820 }}>
            Real VIP invoices (due date + total due) with a per-IMEI sold/reimbursed breakdown and an
            INFO-ONLY estimate of what VidaPay's own net-deduction model implies (total due minus the
            1st-Month commission those same devices earned). Sourced from the VIP Wireless Workbook +
            the Asset Ledger + ePay Payment Detail — see the page footer for what this does NOT cover.
          </p>
        </div>
        {data?.available && <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>}
      </div>

      <div className="card" style={{ padding: 14, marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)' }}>Filters:</span>
        <MultiSelect allLabel="All statuses" value={statuses} onChange={setStatuses} options={statusOpts} width={160} />
        <MultiSelect allLabel="All stores" value={stores} onChange={setStores} options={storeOpts} width={190} searchable />
        <select style={selStyle} value={market} onChange={e => setMarket(e.target.value)}>
          <option value="">All markets</option>
          <option value={NO_MARKET_VALUE}>(no market)</option>
        </select>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Due from <input type="date" style={selStyle} value={dateFrom} onChange={e => setDateFrom(e.target.value)} /></label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>to <input type="date" style={selStyle} value={dateTo} onChange={e => setDateTo(e.target.value)} /></label>
        <input style={{ ...selStyle, width: 150 }} placeholder="Invoice # contains…" value={invoiceQ} onChange={e => setInvoiceQ(e.target.value)} />
        <button className="btn" style={{ marginLeft: 'auto' }} onClick={syncFlags}>🔔 Sync attention flags</button>
      </div>
      {syncMsg && <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12 }}>{syncMsg}</div>}
      {errMsg && <div className="card" style={{ padding: 14, marginBottom: 16, color: '#b45309', background: '#fffbeb', border: '1px solid #fcd34d' }}>{errMsg}</div>}

      {t && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
          <div style={tile}><div style={tileCap}>Invoices</div><div style={{ fontSize: 22, fontWeight: 700 }}>{t.invoice_count}</div></div>
          <div style={tile}><div style={tileCap}>Total Due</div><div style={{ fontSize: 22, fontWeight: 700 }}>{fmt(t.grand_total)}</div></div>
          <div style={tile}><div style={tileCap}>Devices (sold / not sold)</div><div style={{ fontSize: 22, fontWeight: 700 }}>{t.sold_count} / {t.not_sold_count}</div></div>
          <div style={tile}><div style={tileCap}>Reimbursed</div><div style={{ fontSize: 22, fontWeight: 700 }}>{t.reimbursed_count}</div></div>
          <div style={tile}><div style={tileCap} title="1st-Month ePay commission only">Commission Earned (M1)</div><div style={{ fontSize: 22, fontWeight: 700 }}>{fmt(t.commission_earned_m1)}</div></div>
          <div style={{ ...tile, borderStyle: 'dashed' }}>
            <div style={tileCap}>Net Due Estimate <span style={{ fontWeight: 400, textTransform: 'none' }}>(info only)</span></div>
            <div style={{ fontSize: 22, fontWeight: 700 }}>{fmt(t.net_due_estimate)}</div>
          </div>
        </div>
      )}

      {loading ? <div>Loading…</div> : (
        <div className="card" style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={th}>Invoice #</th><th style={th}>Store</th><th style={th}>Market</th>
              <th style={th}>Status</th><th style={th}>Due</th><th style={th}>Total Due</th>
              <th style={th}>Devices</th><th style={th}>Sold / Not Sold</th><th style={th}>Reimbursed</th>
              <th style={th}>Commission (M1)</th><th style={th}>Net Due Est.</th>
            </tr></thead>
            <tbody>
              {(data?.rows || []).map(r => (
                <>
                  <tr key={r.vip_id} style={{ cursor: 'pointer' }} onClick={() => openDetail(r.vip_id)}>
                    <td style={td}>{openId === r.vip_id ? '▾ ' : '▸ '}{r.invoice_number || r.vip_id}</td>
                    <td style={td}>{r.location || '—'}</td>
                    <td style={td}>{r.market || <span style={{ color: 'var(--text3)' }}>(no market)</span>}</td>
                    <td style={td}><DueBadge due={r.due_date} status={r.status} /></td>
                    <td style={td}>{d10(r.due_date)}</td>
                    <td style={td}>{fmt(r.grand_total)}</td>
                    <td style={td}>{r.device_count}{r.unmatched_count > 0 && <span style={{ color: 'var(--text3)' }}> ({r.unmatched_count} unmatched)</span>}</td>
                    <td style={td}>{r.sold_count} / {r.not_sold_count}</td>
                    <td style={td}>{r.reimbursed_count}</td>
                    <td style={td}>{fmt(r.commission_earned_m1)}</td>
                    <td style={td}>{fmt(r.net_due_estimate)}</td>
                  </tr>
                  {openId === r.vip_id && (
                    <tr key={r.vip_id + '-detail'}>
                      <td colSpan={11} style={{ padding: 0, borderTop: 'none' }}>
                        <div style={{ padding: 14, background: 'var(--surface2)' }}>
                          {detailLoading ? 'Loading detail…' : detail && (
                            <div>
                              <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 12 }}>
                                <thead><tr>
                                  <th style={th}>Serial</th><th style={th}>IMEI</th><th style={th}>Product</th>
                                  <th style={th}>Store</th><th style={th}>Sold</th><th style={th}>Reimbursed</th>
                                  <th style={th}>Commission (M1)</th><th style={th}>Trailing (M2-M12)</th><th style={th}>Unsplit</th>
                                </tr></thead>
                                <tbody>
                                  {detail.devices.map((d, i) => (
                                    <tr key={i}>
                                      <td style={td}>{d.serial || '—'}</td>
                                      <td style={td}>{d.imei || '—'}</td>
                                      <td style={td}>{d.product_name || '—'}</td>
                                      <td style={td}>{d.matched ? (d.store || '—') : <span style={{ color: '#b91c1c' }}>no asset match</span>}</td>
                                      <td style={td}>{d.sold ? `✅ ${d10(d.date_sold)}` : '—'}</td>
                                      <td style={td}>{d.reimbursed ? `✅ ${fmt(d.reimbursement || 0)} on ${d10(d.reimbursement_date)}` : '—'}</td>
                                      <td style={td}>{fmt(d.commission_m1)}</td>
                                      <td style={td}>{fmt(d.commission_trailing)}</td>
                                      <td style={td}>{fmt(d.commission_unsplit)}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                              <div style={{ fontSize: 12, color: 'var(--text2)', background: 'var(--surface)', border: '1px dashed var(--border)', borderRadius: 8, padding: 10 }}>
                                <b>INFO ONLY — reconciliation footer.</b> This invoice's devices earned{' '}
                                <b>{fmt(detail.period_commission_footer.invoice_commission_m1)}</b> of 1st-Month
                                commission. The WHOLE org earned{' '}
                                <b>{detail.period_commission_footer.period_total_commission_m1 == null ? '—' : fmt(detail.period_commission_footer.period_total_commission_m1)}</b>{' '}
                                in 1st-Month commission during {detail.period_commission_footer.period || 'this period'} —
                                a difference of{' '}
                                <b>{detail.period_commission_footer.difference == null ? '—' : fmt(detail.period_commission_footer.difference)}</b>{' '}
                                earned on OTHER invoices/devices in the same period. {detail.period_commission_footer.note}
                              </div>
                              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>{r.net_due_estimate_note}</div>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
              {!loading && (data?.rows || []).length === 0 && (
                <tr><td colSpan={11} style={{ ...td, textAlign: 'center', color: 'var(--text3)', padding: 24 }}>
                  No invoices match this filter.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ marginTop: 20, fontSize: 12, color: 'var(--text3)', maxWidth: 900 }}>
        <b>What this does NOT cover:</b> the VidaPay/T-CETRA "MA Handset Ordering" marketplace feed
        (Total Wireless handset purchases) — that source carries no invoice #/due date/total and no
        per-device serial, so it cannot be tracked this way. This report covers VIP Wireless
        (Boost) consignment invoices only.
      </div>
    </div>
  )
}
