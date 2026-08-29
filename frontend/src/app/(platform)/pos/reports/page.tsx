'use client'
// POS module — Phase 2: Reports (ported from the standalone pos-system app;
// data access rewired from direct Supabase to the FastAPI /pos router).
// 8 implemented reports; everything else in the catalog shows a "not built yet" notice.
import { useEffect, useState } from 'react'
import { api, fmt } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { serializeCsv, type CsvCell } from '@/lib/pos-csv'

interface Kpis {
  today: { count: number; total: number }
  week: { count: number; total: number }
  month: { count: number; total: number }
  customers: number
  products: number
  in_stock_units: number
}

interface SaleRow {
  transaction_id: number
  created_at: string
  total: number
  discount_total: number | null
  receipt_type: string
  status: string
  customer_name: string | null
  employee_name: string | null
  store_code: string | null
}

interface ActivationRow {
  activation_number: number
  carrier: string | null
  activation_date: string | null
  monthly_fee: number | null
  cell_number: string | null
  mobile_phone: string | null
  status: string | null
  customer_name: string | null
  employee_name: string | null
}

interface TradeInRow {
  id: string
  received_at: string | null
  device_description: string | null
  serial_number: string | null
  imei: string | null
  credit_amount: number | null
  status: string
  sent_back_at: string | null
  customer_name: string | null
  activation_number: number | null
}

interface StoreOption { store_code: string; label: string }
interface EmployeeOption { id: string; label: string }

const SALES_REPORTS = ['Daily Sales Summary', 'Void Report', 'Discount Report']
const ACTIVATION_REPORTS = ['Activations Summary', 'Activations by Carrier', 'Activations by Employee']
const TRADE_IN_REPORTS = ['Trade-In Return Tracking', 'Trade-In Financial Impact']

const REPORT_CATEGORIES = [
  {
    name: 'Sales', icon: '💰', color: '#27ae60', reports: [
      'Daily Sales Summary', 'Sales by Employee', 'Sales by Product', 'Sales by Category',
      'Sales Tax by Transaction', 'Sales Tax Summary', 'Void Report', 'Discount Report',
    ]
  },
  {
    name: 'Activations', icon: '📡', color: '#3498db', reports: [
      'Activations Summary', 'Activations by Carrier', 'Activations by Employee',
      'Activations by Store', 'Carrier Commissions',
    ]
  },
  {
    name: 'Trade-Ins', icon: '♻️', color: '#16a085', reports: [
      'Trade-In Return Tracking', 'Trade-In Financial Impact',
    ]
  },
  {
    name: 'Inventory', icon: '📦', color: '#e67e22', reports: [
      'Inventory Quantity and Cost', 'Inventory Reorder by Store', 'Real-time Inventory Snapshot',
      'Product Price List by Store', 'Purchase Orders', 'RMA Requests', 'Stock Maintenance',
      'Inventory Aging', 'Inventory Adjustment Details',
    ]
  },
  {
    name: 'Customers', icon: '👤', color: '#9b59b6', reports: [
      'Customer Care', 'Customer Contact', 'Customer Activity',
    ]
  },
  {
    name: 'Finance', icon: '🏦', color: '#e74c3c', reports: [
      'Cash Register Status', 'Cash Register Transactions', 'Cash Register Reconciliation',
      'Expenses', 'Sales Tax Summary', 'Sweep Report',
    ]
  },
  {
    name: 'Scorecard', icon: '🏆', color: '#f39c12', reports: [
      'Employee KPIs', 'Employee Performance', 'Employee Performance by Store',
      'Employee Scorecard', 'Location KPIs', 'Location Scorecard', 'Performance Report',
    ]
  },
  {
    name: 'KPI', icon: '📊', color: '#1abc9c', reports: [
      'Store KPIs', 'Daily KPI Dashboard', 'Weekly KPI Summary', 'Monthly KPI Report',
    ]
  },
  {
    name: 'Time & Attendance', icon: '🕐', color: '#95a5a6', reports: [
      'Employee Time Sheet', 'Employee Attendance', 'Employee Schedule',
      'Average Transactions per Day', 'Time and Attendance',
    ]
  },
  {
    name: 'MoM/YoY', icon: '📈', color: '#2980b9', reports: [
      'Month over Month Sales', 'Year over Year Sales', 'Growth Trends',
    ]
  },
  {
    name: 'Summaries', icon: '📋', color: '#7f8c8d', reports: [
      'Daily Summary', 'Weekly Summary', 'Monthly Summary', 'Annual Summary',
    ]
  },
]

const IMPLEMENTED_REPORTS = [...SALES_REPORTS, ...ACTIVATION_REPORTS, ...TRADE_IN_REPORTS]

const inputStyle: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', outline: 'none' }
const labelStyle: React.CSSProperties = { fontSize: 11, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const panel: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10 }
const th: React.CSSProperties = { padding: '8px 14px', textAlign: 'left', color: 'var(--text2)', fontSize: 11, fontWeight: 600, textTransform: 'uppercase', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '8px 14px', borderBottom: '1px solid var(--border)' }
const chip = (color: string): React.CSSProperties => ({ fontSize: 12, background: `${color}22`, border: `1px solid ${color}`, color, borderRadius: 20, padding: '3px 12px', fontWeight: 700 })

const ymd = (d: Date) => {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

// Local calendar day (YYYY-MM-DD) → real instants, so the sales report filters compare
// correctly against UTC created_at timestamps. Activation/trade-in filters stay bare dates
// (activation_date is a DATE column; trade_ins received_at gets the backend's day-bound handling).
const dayStartIso = (s: string) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d).toISOString() }
const dayEndIso = (s: string) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d, 23, 59, 59).toISOString() }

export default function PosReportsPage() {
  const [activeCategory, setActiveCategory] = useState('Sales')
  const [activeReport, setActiveReport] = useState('Daily Sales Summary')
  const [stats, setStats] = useState<Kpis | null>(null)
  const [sales, setSales] = useState<SaleRow[]>([])
  const [activations, setActivations] = useState<ActivationRow[]>([])
  const [tradeIns, setTradeIns] = useState<TradeInRow[]>([])
  const [loading, setLoading] = useState(false)
  // Initialized empty and filled on mount so server render and client
  // hydration produce identical markup (no new Date() during initial render).
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [carrierStats, setCarrierStats] = useState<{ carrier: string; count: number; revenue: number }[]>([])
  const [reportError, setReportError] = useState('')
  const [reportInfo, setReportInfo] = useState('')
  const [statsError, setStatsError] = useState('')

  // Standard filter bar
  const [storesList, setStoresList] = useState<StoreOption[]>([])
  const [employeesList, setEmployeesList] = useState<EmployeeOption[]>([])
  const [storeFilter, setStoreFilter] = useState('')
  const [employeeFilter, setEmployeeFilter] = useState('')
  const [filterLoadError, setFilterLoadError] = useState('')

  // Trade-in specific controls
  const [tradeStatusFilter, setTradeStatusFilter] = useState<'all' | 'received' | 'sent_back' | 'written_off'>('all')
  const [minAgeDays, setMinAgeDays] = useState('14')

  // Share popover
  const [showShare, setShowShare] = useState(false)

  useEffect(() => {
    // Default date range: month-to-date.
    const now = new Date()
    setDateFrom(ymd(new Date(now.getFullYear(), now.getMonth(), 1)))
    setDateTo(ymd(now))
    loadStats()
    loadFilterOptions()
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  async function loadFilterOptions() {
    const problems: string[] = []
    const [storesRes, empRes] = await Promise.all([
      apiCached('/api/v1/storeops/stores', LOOKUP).catch((e: any) => { problems.push(`stores: ${e?.message || e}`); return null }),
      apiCached('/api/v1/storeops/employees', LOOKUP).catch((e: any) => { problems.push(`employees: ${e?.message || e}`); return null }),
    ])
    if (Array.isArray(storesRes)) {
      setStoresList(storesRes.filter((s: any) => s.store_code).map((s: any) => ({
        store_code: String(s.store_code),
        label: s.address ? `${s.store_code} — ${s.address}` : String(s.store_code),
      })))
    }
    if (Array.isArray(empRes)) {
      setEmployeesList(empRes.filter((e: any) => e.is_active !== false).map((e: any) => ({
        id: String(e.employee_id || ''), label: e.name || e.employee_id || '',
      })).filter((e: EmployeeOption) => e.id))
    }
    setFilterLoadError(problems.length > 0 ? `Filter lists failed to load — ${problems.join('; ')}` : '')
  }

  async function loadStats() {
    try {
      setStats(await api('/api/v1/pos/reports/kpis'))
      setStatsError('')
    } catch (err: any) {
      setStatsError(`Some summary figures failed to load: ${err?.message || err}`)
    }
  }

  async function runReport() {
    if (!dateFrom || !dateTo) { alert('Pick a date range first.'); return }
    setLoading(true)
    setSales([]); setActivations([]); setCarrierStats([]); setTradeIns([])
    setReportError(''); setReportInfo('')
    try {
      if (SALES_REPORTS.includes(activeReport)) {
        const params = new URLSearchParams({ date_from: dayStartIso(dateFrom), date_to: dayEndIso(dateTo) })
        if (storeFilter) params.set('store_code', storeFilter)
        if (employeeFilter) params.set('employee_id', employeeFilter)
        params.set('kind', activeReport === 'Void Report' ? 'voids' : activeReport === 'Discount Report' ? 'discounts' : 'daily')
        const r = await api(`/api/v1/pos/reports/sales?${params}`)
        setSales(r.rows || [])
      } else if (ACTIVATION_REPORTS.includes(activeReport)) {
        const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo })
        if (storeFilter) params.set('store_code', storeFilter)
        if (employeeFilter) params.set('employee_id', employeeFilter)
        const r = await api(`/api/v1/pos/reports/activations?${params}`)
        const rows: ActivationRow[] = r.rows || []
        setActivations(rows)
        if (activeReport === 'Activations by Carrier') {
          const grouped: { [k: string]: { count: number; revenue: number } } = {}
          rows.forEach(a => {
            const c = a.carrier || 'Unknown'
            if (!grouped[c]) grouped[c] = { count: 0, revenue: 0 }
            grouped[c].count++
            grouped[c].revenue += a.monthly_fee || 0
          })
          setCarrierStats(Object.entries(grouped).map(([carrier, v]) => ({ carrier, ...v })).sort((a, b) => b.count - a.count))
        }
      } else if (TRADE_IN_REPORTS.includes(activeReport)) {
        // trade_ins are not store-scoped; the employee filter is applied server-side
        // through the linked activation's salesperson. Store filter is inapplicable.
        const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo })
        if (employeeFilter) params.set('employee_id', employeeFilter)
        const r = await api(`/api/v1/pos/reports/trade-ins?${params}`)
        let rows: TradeInRow[] = r.rows || []
        if (activeReport === 'Trade-In Financial Impact') {
          // Keep written-off units plus 'received' units that have been sitting
          // longer than the age threshold — the money at risk of never coming back.
          const threshold = Math.max(0, parseInt(minAgeDays, 10) || 0)
          rows = rows.filter(t => t.status === 'written_off' || (t.status === 'received' && ageDays(t) >= threshold))
          if (rows.length === 0) setReportInfo(`No written-off units and no outstanding units older than ${threshold} days in this range.`)
        }
        setTradeIns(rows)
      } else {
        setReportInfo(`"${activeReport}" is not built yet. Currently available: ${IMPLEMENTED_REPORTS.join(', ')}.`)
      }
    } catch (err: any) {
      setReportError(`Report failed: ${err?.message || err}`)
    }
    setLoading(false)
  }

  function ageDays(t: TradeInRow): number {
    if (!t.received_at) return 0
    return Math.max(0, Math.floor((Date.now() - new Date(t.received_at).getTime()) / 86400000))
  }

  const statusLabel = (s: string) => s === 'received' ? 'Outstanding' : s === 'sent_back' ? 'Sent Back' : s === 'written_off' ? 'Written Off' : s
  const statusColor = (s: string) => s === 'received' ? '#f39c12' : s === 'sent_back' ? '#27ae60' : '#e74c3c'

  // Rows currently shown for Return Tracking, after the status filter.
  const displayedTradeIns = activeReport === 'Trade-In Return Tracking' && tradeStatusFilter !== 'all'
    ? tradeIns.filter(t => t.status === tradeStatusFilter)
    : tradeIns

  const tradeReceived = tradeIns.length
  const tradeSentBack = tradeIns.filter(t => t.status === 'sent_back').length
  const tradeOutstanding = tradeIns.filter(t => t.status === 'received').length
  const tradeOutstandingCredit = tradeIns.filter(t => t.status === 'received').reduce((s, t) => s + (t.credit_amount || 0), 0)
  const tradeWrittenOffCredit = tradeIns.filter(t => t.status === 'written_off').reduce((s, t) => s + (t.credit_amount || 0), 0)

  async function updateTradeIn(t: TradeInRow, action: 'sent_back' | 'written_off') {
    const question = action === 'sent_back'
      ? `Mark this trade-in as sent back to the carrier/vendor?\n\n${t.device_description || 'Device'} — ${t.serial_number || t.imei || 'no serial'}`
      : `Write off this trade-in? Its credit (${fmt(t.credit_amount || 0)}) becomes a loss.\n\n${t.device_description || 'Device'} — ${t.serial_number || t.imei || 'no serial'}`
    if (!confirm(question)) return
    try {
      const r = await api(`/api/v1/pos/trade-ins/${t.id}`, { method: 'PATCH', body: JSON.stringify({ status: action }) })
      const updated = r.trade_in || {}
      setTradeIns(prev => prev.map(x => x.id === t.id
        ? { ...x, status: updated.status || action, sent_back_at: updated.sent_back_at ?? x.sent_back_at }
        : x))
    } catch (err: any) {
      const m = String(err?.message || err)
      // The server enforces permissions (pos_settings / pos_inventory_adjust) and returns 403.
      if (/not allow|permission|forbidden/i.test(m)) alert('Update was blocked — you may not have permission.')
      else alert(`Update failed: ${m}`)
    }
  }

  const fmtDay = (d: string | null) => d ? d.split('T')[0] : ''

  const totalSales = sales.reduce((s, r) => s + (r.total || 0), 0)
  const totalActivationFees = activations.reduce((s, a) => s + (a.monthly_fee || 0), 0)

  /**
   * The current report as headers + rows + top-line summary lines.
   * Single source for CSV export and the share-summary builder (and, later,
   * a server-side sender can mirror this shape).
   */
  function getReportTable(): { headers: string[]; rows: CsvCell[][]; summaryLines: string[] } | null {
    if (activeReport === 'Activations by Carrier' && carrierStats.length > 0) {
      return {
        headers: ['Carrier', 'Total Activations', 'Total Monthly Fees', 'Avg Monthly Fee'],
        rows: carrierStats.map(c => [c.carrier, c.count, c.revenue.toFixed(2), (c.revenue / c.count).toFixed(2)]),
        summaryLines: [
          `Activations: ${activations.length}`,
          `Total monthly fees: ${fmt(carrierStats.reduce((s, c) => s + c.revenue, 0))}`,
        ],
      }
    }
    if (activeReport === 'Trade-In Return Tracking' && tradeIns.length > 0) {
      return {
        headers: ['Received', 'Device', 'Serial/IMEI', 'Customer', 'Activation #', 'Credit', 'Status', 'Sent Back'],
        rows: displayedTradeIns.map(t => [fmtDay(t.received_at), t.device_description || '', t.serial_number || t.imei || '',
          t.customer_name || '', t.activation_number ?? '', (t.credit_amount || 0).toFixed(2),
          statusLabel(t.status), fmtDay(t.sent_back_at)]),
        summaryLines: [
          `Received: ${tradeReceived}`,
          `Sent back: ${tradeSentBack}`,
          `Outstanding (not yet sent back): ${tradeOutstanding}`,
        ],
      }
    }
    if (activeReport === 'Trade-In Financial Impact' && tradeIns.length > 0) {
      return {
        headers: ['Received', 'Device', 'Serial/IMEI', 'Customer', 'Activation #', 'Age (days)', 'Status', 'Credit at Risk'],
        rows: tradeIns.map(t => [fmtDay(t.received_at), t.device_description || '', t.serial_number || t.imei || '',
          t.customer_name || '', t.activation_number ?? '', ageDays(t),
          statusLabel(t.status), (t.credit_amount || 0).toFixed(2)]),
        summaryLines: [
          `Outstanding credit (still out): ${fmt(tradeOutstandingCredit)}`,
          `Written off: ${fmt(tradeWrittenOffCredit)}`,
          `Total at risk: ${fmt(tradeOutstandingCredit + tradeWrittenOffCredit)}`,
        ],
      }
    }
    if (sales.length > 0) {
      return {
        headers: ['Transaction ID', 'Date', 'Status', 'Type', 'Customer', 'Employee', 'Total'],
        rows: sales.map(s => [s.transaction_id, s.created_at, s.status, s.receipt_type,
          s.customer_name || '', s.employee_name || '', (s.total || 0).toFixed(2)]),
        summaryLines: [
          `Records: ${sales.length}`,
          `Total revenue: ${fmt(totalSales)}`,
        ],
      }
    }
    if (activations.length > 0) {
      return {
        headers: ['Activation #', 'Customer', 'Employee', 'Carrier', 'Date', 'Cell Number', 'Monthly Fee'],
        rows: activations.map(a => [a.activation_number, a.customer_name || '', a.employee_name || '',
          a.carrier || '', a.activation_date || '', a.cell_number || a.mobile_phone || '',
          (a.monthly_fee || 0).toFixed(2)]),
        summaryLines: [
          `Activations: ${activations.length}`,
          `Total monthly fees: ${fmt(totalActivationFees)}`,
        ],
      }
    }
    return null
  }

  function exportCsv() {
    const table = getReportTable()
    if (!table) {
      alert('Run a report first — there is no data to export.')
      return
    }
    const blob = new Blob([serializeCsv([table.headers, ...table.rows])], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${activeReport.toLowerCase().replace(/[^a-z0-9]+/g, '-')}-${dateFrom}-to-${dateTo}.csv`
    link.click()
    URL.revokeObjectURL(url)
  }

  /**
   * Plain-text summary of the current report: title + filters + top-line
   * totals + first 15 rows in aligned columns. Reused by both share targets;
   * a future server-side sender (email attachment, WhatsApp API) should call
   * the same logic.
   */
  function buildShareSummary(): string | null {
    const table = getReportTable()
    if (!table) return null
    const lines: string[] = []
    lines.push(`${activeReport} — ${dateFrom} to ${dateTo}`)
    if (storeFilter) lines.push(`Store: ${storesList.find(s => s.store_code === storeFilter)?.label || storeFilter}`)
    if (employeeFilter) lines.push(`Employee/Sales Rep: ${employeesList.find(e => e.id === employeeFilter)?.label || employeeFilter}`)
    lines.push(...table.summaryLines)
    lines.push('')
    const preview = [table.headers, ...table.rows.slice(0, 15)]
      .map(r => r.map(c => (c === null || c === undefined) ? '' : String(c)))
    const widths = preview[0].map((_, col) => Math.max(...preview.map(r => (r[col] || '').length)))
    for (const r of preview) lines.push(r.map((c, i) => c.padEnd(widths[i])).join('  ').trimEnd())
    if (table.rows.length > 15) lines.push(`... and ${table.rows.length - 15} more rows`)
    lines.push('')
    lines.push('Full report attached from POS System.')
    return lines.join('\n')
  }

  const shareSummary = showShare ? buildShareSummary() : null
  const shareSubject = `${activeReport} — ${dateFrom} to ${dateTo}`
  const shareEmailHref = shareSummary
    ? `mailto:?subject=${encodeURIComponent(shareSubject)}&body=${encodeURIComponent(shareSummary.slice(0, 4000))}`
    : undefined
  const shareWhatsAppHref = shareSummary
    ? `https://wa.me/?text=${encodeURIComponent(shareSummary.slice(0, 1500))}`
    : undefined

  const isTradeReport = TRADE_IN_REPORTS.includes(activeReport)
  const hasResults = sales.length > 0 || activations.length > 0 || carrierStats.length > 0 || tradeIns.length > 0

  const shareLink: React.CSSProperties = { display: 'block', background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 6, padding: '9px 12px', fontSize: 12, color: 'var(--text)', textDecoration: 'none', fontWeight: 600 }

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📊 Reports</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {activeCategory} › {activeReport}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="btn btn-primary" onClick={exportCsv}>📊 Export CSV</button>

          {/* SHARE */}
          <div style={{ position: 'relative' }}>
            <button className="btn btn-secondary" onClick={() => setShowShare(s => !s)}>📤 Share</button>
            {showShare && (
              <div style={{ position: 'absolute', top: 'calc(100% + 8px)', right: 0, width: 270, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 14, zIndex: 300, boxShadow: '0 10px 30px rgba(0,0,0,0.35)' }}>
                <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 10 }}>Share this report</div>
                {shareSummary ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <a href={shareEmailHref} target="_blank" rel="noreferrer" onClick={() => setShowShare(false)} style={shareLink}>
                      ✉️ Share via Email
                    </a>
                    <a href={shareWhatsAppHref} target="_blank" rel="noreferrer" onClick={() => setShowShare(false)} style={shareLink}>
                      💬 Share via WhatsApp
                    </a>
                  </div>
                ) : (
                  <div style={{ fontSize: 12, color: 'var(--text2)' }}>Run a report first — there is nothing to share yet.</div>
                )}
                <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 10, lineHeight: 1.5 }}>
                  Sends a text summary (totals + first 15 rows). Direct sending &amp; attachments will be enabled once email service is configured.
                </div>
              </div>
            )}
          </div>

          <button className="btn btn-secondary" onClick={() => window.print()}>🖨️ Print Preview</button>
        </div>
      </div>

      {statsError && (
        <div style={{ marginBottom: 14, border: '1px solid #e74c3c', color: '#e74c3c', borderRadius: 8, padding: '10px 14px', fontSize: 12 }}>{statsError}</div>
      )}

      {/* KPI SUMMARY CARDS */}
      {stats && (
        <div style={{ marginBottom: 16, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
          {[
            { label: "Today's Revenue", value: fmt(stats.today.total), sub: `${stats.today.count} transactions`, icon: '💵', color: '#27ae60' },
            { label: 'This Week', value: fmt(stats.week.total), sub: `${stats.week.count} transactions`, icon: '📅', color: '#3498db' },
            { label: 'This Month', value: fmt(stats.month.total), sub: `${stats.month.count} transactions`, icon: '📆', color: '#9b59b6' },
            { label: 'Total Customers', value: (stats.customers || 0).toLocaleString(), sub: 'Active accounts', icon: '👤', color: '#e67e22' },
            { label: 'Total Products', value: (stats.products || 0).toLocaleString(), sub: 'In catalog', icon: '📦', color: '#1abc9c' },
            { label: 'Phones In Stock', value: (stats.in_stock_units || 0).toLocaleString(), sub: 'Serial tracked', icon: '📱', color: '#e74c3c' },
          ].map((s, i) => (
            <div key={i} style={{ ...panel, padding: 14, position: 'relative', overflow: 'hidden' }}>
              <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, background: s.color }} />
              <div style={{ fontSize: 20, marginBottom: 6 }}>{s.icon}</div>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{s.value}</div>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', marginTop: 2 }}>{s.label}</div>
              <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 1 }}>{s.sub}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 16 }}>

        {/* LEFT SIDEBAR — Report categories */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6, padding: '0 8px' }}>System Reports</div>
          {REPORT_CATEGORIES.map(cat => (
            <div key={cat.name}>
              <button onClick={() => { setActiveCategory(cat.name); setActiveReport(cat.reports[0]) }}
                style={{ width: '100%', background: activeCategory === cat.name ? 'var(--surface2)' : 'transparent', border: 'none', borderLeft: `3px solid ${activeCategory === cat.name ? cat.color : 'transparent'}`, color: activeCategory === cat.name ? 'var(--text)' : 'var(--text2)', padding: '9px 12px', fontSize: 12, fontWeight: activeCategory === cat.name ? 700 : 400, cursor: 'pointer', textAlign: 'left', display: 'flex', alignItems: 'center', gap: 8, borderRadius: '0 6px 6px 0' }}>
                <span style={{ fontSize: 14 }}>{cat.icon}</span>
                {cat.name}
                <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text3)' }}>{cat.reports.length}</span>
              </button>
              {activeCategory === cat.name && (
                <div style={{ paddingLeft: 16, marginTop: 2, display: 'flex', flexDirection: 'column', gap: 1 }}>
                  {cat.reports.map(r => (
                    <button key={r} onClick={() => setActiveReport(r)}
                      style={{ width: '100%', background: activeReport === r ? 'var(--surface2)' : 'transparent', border: 'none', color: activeReport === r ? 'var(--text)' : 'var(--text3)', padding: '6px 10px', fontSize: 11, fontWeight: activeReport === r ? 600 : 400, cursor: 'pointer', textAlign: 'left', borderRadius: 4, borderLeft: `2px solid ${activeReport === r ? cat.color : 'transparent'}`, opacity: IMPLEMENTED_REPORTS.includes(r) || activeReport === r ? 1 : 0.7 }}>
                      {r}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        {/* RIGHT — Report content */}
        <div style={{ minWidth: 0 }}>
          {/* Report header + filters */}
          <div style={{ ...panel, padding: 16, marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14, gap: 12, flexWrap: 'wrap' }}>
              <div>
                <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 4px' }}>{activeReport}</h2>
                <div style={{ fontSize: 12, color: 'var(--text2)' }}>{activeCategory} › {activeReport}</div>
              </div>
              <button className="btn btn-primary" onClick={runReport} disabled={loading}>▶ Run Report</button>
            </div>

            {/* Standard filter bar */}
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <div>
                <label style={labelStyle}>Store Location</label>
                <select value={storeFilter} onChange={e => setStoreFilter(e.target.value)} style={{ ...inputStyle, width: 200 }}>
                  <option value="">All Stores</option>
                  {storesList.map(s => <option key={s.store_code} value={s.store_code}>{s.label}</option>)}
                </select>
              </div>
              <div>
                <label style={labelStyle}>Employee / Sales Rep</label>
                <select value={employeeFilter} onChange={e => setEmployeeFilter(e.target.value)} style={{ ...inputStyle, width: 170 }}>
                  <option value="">All Employees</option>
                  {employeesList.map(emp => <option key={emp.id} value={emp.id}>{emp.label}</option>)}
                </select>
              </div>
              <div>
                <label style={labelStyle}>Date From</label>
                <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={{ ...inputStyle, width: 140 }} />
              </div>
              <div>
                <label style={labelStyle}>Date To</label>
                <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={{ ...inputStyle, width: 140 }} />
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                {['Today', 'This Week', 'MTD', 'This Year'].map(p => (
                  <button key={p} className="btn btn-secondary" style={{ fontSize: 11, padding: '6px 10px', whiteSpace: 'nowrap' }} onClick={() => {
                    const now = new Date()
                    const to = ymd(now)
                    let from = to
                    if (p === 'This Week') from = ymd(new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000))
                    if (p === 'MTD') from = ymd(new Date(now.getFullYear(), now.getMonth(), 1))
                    if (p === 'This Year') from = ymd(new Date(now.getFullYear(), 0, 1))
                    setDateFrom(from); setDateTo(to)
                  }}>{p}</button>
                ))}
              </div>

              {/* Trade-in specific controls */}
              {activeReport === 'Trade-In Return Tracking' && (
                <div>
                  <label style={labelStyle}>Status</label>
                  <select value={tradeStatusFilter} onChange={e => setTradeStatusFilter(e.target.value as any)} style={{ ...inputStyle, width: 140 }}>
                    <option value="all">All</option>
                    <option value="received">Outstanding</option>
                    <option value="sent_back">Sent Back</option>
                    <option value="written_off">Written Off</option>
                  </select>
                </div>
              )}
              {activeReport === 'Trade-In Financial Impact' && (
                <div>
                  <label style={labelStyle}>Outstanding older than (days)</label>
                  <input type="number" min={0} value={minAgeDays} onChange={e => setMinAgeDays(e.target.value)} style={{ ...inputStyle, width: 110 }} />
                </div>
              )}
            </div>

            {filterLoadError && (
              <div style={{ marginTop: 8, fontSize: 11, color: '#e67e22' }}>{filterLoadError}</div>
            )}
            {isTradeReport && storeFilter && (
              <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text2)' }}>
                Note: the store filter does not apply to trade-ins (they are not store-scoped) — all stores are shown. The employee filter matches the salesperson on the linked activation.
              </div>
            )}
          </div>

          {/* Report output */}
          <div style={{ ...panel, overflow: 'hidden' }}>

            {/* Summary totals bar */}
            {hasResults && (
              <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)', display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)' }}>SUMMARY</span>
                {sales.length > 0 && (
                  <>
                    <span style={{ fontSize: 13 }}>Records: <strong>{sales.length}</strong></span>
                    <span style={{ fontSize: 13, color: '#27ae60', fontWeight: 700 }}>Total Revenue: {fmt(totalSales)}</span>
                    <span style={{ fontSize: 13, color: 'var(--text2)' }}>Avg: {fmt(totalSales / sales.length)}</span>
                  </>
                )}
                {activations.length > 0 && (
                  <>
                    <span style={{ fontSize: 13 }}>Activations: <strong>{activations.length}</strong></span>
                    <span style={{ fontSize: 13, color: '#27ae60', fontWeight: 700 }}>Monthly Fees: {fmt(totalActivationFees)}</span>
                  </>
                )}
                {tradeIns.length > 0 && activeReport === 'Trade-In Return Tracking' && (
                  <>
                    <span style={chip('#16a085')}>Received: {tradeReceived}</span>
                    <span style={chip('#27ae60')}>Sent Back: {tradeSentBack}</span>
                    <span style={chip('#f39c12')}>Outstanding: {tradeOutstanding}</span>
                  </>
                )}
                {tradeIns.length > 0 && activeReport === 'Trade-In Financial Impact' && (
                  <>
                    <span style={chip('#f39c12')}>Outstanding Credit: {fmt(tradeOutstandingCredit)}</span>
                    <span style={chip('#e74c3c')}>Written Off: {fmt(tradeWrittenOffCredit)}</span>
                    <span style={{ ...chip('#e74c3c'), color: 'var(--text)' }}>Total at Risk: {fmt(tradeOutstandingCredit + tradeWrittenOffCredit)}</span>
                  </>
                )}
              </div>
            )}

            {/* Loading */}
            {loading && (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, padding: 40, color: 'var(--text2)' }}>
                <div className="spinner" />
                Running report...
              </div>
            )}

            {/* Errors / not-implemented notices */}
            {!loading && reportError && (
              <div style={{ margin: '14px 16px', border: '1px solid #e74c3c', color: '#e74c3c', borderRadius: 8, padding: '10px 14px', fontSize: 12 }}>{reportError}</div>
            )}
            {!loading && reportInfo && (
              <div style={{ margin: '14px 16px', border: '1px solid #e67e22', color: '#e67e22', borderRadius: 8, padding: '10px 14px', fontSize: 12 }}>{reportInfo}</div>
            )}

            {/* Empty state */}
            {!loading && !reportError && !reportInfo && !hasResults && (
              <div style={{ padding: 50, textAlign: 'center', color: 'var(--text2)' }}>
                <div style={{ fontSize: 40, marginBottom: 12 }}>
                  {REPORT_CATEGORIES.find(c => c.name === activeCategory)?.icon || '📊'}
                </div>
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>{activeReport}</div>
                <div style={{ fontSize: 13, marginBottom: 16 }}>Select your date range and click <strong style={{ color: '#3498db' }}>Run Report</strong> to generate data</div>
                {dateFrom && (
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                    {dateFrom === dateTo ? `Date: ${dateFrom}` : `${dateFrom} to ${dateTo}`}
                  </div>
                )}
              </div>
            )}

            {/* SALES TABLE */}
            {!loading && sales.length > 0 && SALES_REPORTS.includes(activeReport) && (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ background: 'var(--surface2)' }}>
                      {['Transaction ID', 'Date', 'Status', 'Type', 'Customer', 'Employee', 'Total Sale'].map(h => (
                        <th key={h} style={th}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sales.map(sale => (
                      <tr key={sale.transaction_id}>
                        <td style={{ ...td, color: '#3498db', fontWeight: 600 }}>{sale.transaction_id}</td>
                        <td style={{ ...td, color: 'var(--text2)' }}>{new Date(sale.created_at).toLocaleString()}</td>
                        <td style={td}>
                          <span style={{ color: sale.status === 'completed' ? '#27ae60' : '#e74c3c', fontWeight: 600, textTransform: 'capitalize' }}>{sale.status}</span>
                        </td>
                        <td style={{ ...td, color: 'var(--text2)', textTransform: 'capitalize' }}>{sale.receipt_type}</td>
                        <td style={td}>{sale.customer_name || '—'}</td>
                        <td style={{ ...td, color: 'var(--text2)' }}>{sale.employee_name || '—'}</td>
                        <td style={{ ...td, color: '#27ae60', fontWeight: 700 }}>{fmt(sale.total || 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr style={{ background: 'var(--surface2)', borderTop: '2px solid var(--border)' }}>
                      <td colSpan={6} style={{ padding: '10px 14px', fontWeight: 700, color: 'var(--text2)', fontSize: 12 }}>TOTAL ({sales.length} records)</td>
                      <td style={{ padding: '10px 14px', fontWeight: 700, color: '#27ae60', fontSize: 14 }}>{fmt(totalSales)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}

            {/* ACTIVATIONS TABLE */}
            {!loading && activations.length > 0 && activeReport !== 'Activations by Carrier' && (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ background: 'var(--surface2)' }}>
                      {['Activation #', 'Customer', 'Employee', 'Carrier', 'Date', 'Cell Number', 'Monthly Fee'].map(h => (
                        <th key={h} style={th}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {activations.map(a => (
                      <tr key={a.activation_number}>
                        <td style={{ ...td, color: '#3498db', fontWeight: 600 }}>{a.activation_number}</td>
                        <td style={td}>{a.customer_name || '—'}</td>
                        <td style={{ ...td, color: 'var(--text2)' }}>{a.employee_name || '—'}</td>
                        <td style={td}>{a.carrier || '—'}</td>
                        <td style={{ ...td, color: 'var(--text2)' }}>{a.activation_date ? new Date(a.activation_date).toLocaleDateString() : '—'}</td>
                        <td style={{ ...td, color: 'var(--text2)' }}>{a.cell_number || a.mobile_phone || '—'}</td>
                        <td style={{ ...td, color: '#27ae60', fontWeight: 700 }}>{a.monthly_fee ? fmt(a.monthly_fee) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr style={{ background: 'var(--surface2)', borderTop: '2px solid var(--border)' }}>
                      <td colSpan={6} style={{ padding: '10px 14px', fontWeight: 700, color: 'var(--text2)' }}>TOTAL ({activations.length} activations)</td>
                      <td style={{ padding: '10px 14px', fontWeight: 700, color: '#27ae60', fontSize: 14 }}>{fmt(totalActivationFees)}/mo</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}

            {/* CARRIER BREAKDOWN */}
            {!loading && carrierStats.length > 0 && activeReport === 'Activations by Carrier' && (
              <div style={{ padding: 20 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginBottom: 20 }}>
                  {carrierStats.map((c, i) => (
                    <div key={i} style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 10, padding: 16, borderTop: `3px solid ${['#e74c3c', '#3498db', '#e67e22', '#9b59b6', '#27ae60'][i % 5]}` }}>
                      <div style={{ fontSize: 18, fontWeight: 700 }}>{c.carrier}</div>
                      <div style={{ fontSize: 24, fontWeight: 700, color: '#27ae60', margin: '8px 0' }}>{c.count}</div>
                      <div style={{ fontSize: 12, color: 'var(--text2)' }}>activations</div>
                      <div style={{ fontSize: 13, color: '#e67e22', marginTop: 6, fontWeight: 600 }}>{fmt(c.revenue)}/mo total</div>
                    </div>
                  ))}
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ background: 'var(--surface2)' }}>
                      {['Carrier', 'Total Activations', 'Total Monthly Fees', 'Avg Monthly Fee'].map(h => (
                        <th key={h} style={th}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {carrierStats.map((c, i) => (
                      <tr key={i}>
                        <td style={{ ...td, fontWeight: 600 }}>{c.carrier}</td>
                        <td style={td}>{c.count}</td>
                        <td style={{ ...td, color: '#27ae60' }}>{fmt(c.revenue)}/mo</td>
                        <td style={{ ...td, color: 'var(--text2)' }}>{fmt(c.revenue / c.count)}/mo</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* TRADE-IN RETURN TRACKING TABLE */}
            {!loading && tradeIns.length > 0 && activeReport === 'Trade-In Return Tracking' && (
              <div style={{ overflowX: 'auto' }}>
                {displayedTradeIns.length === 0 ? (
                  <div style={{ padding: 30, textAlign: 'center', color: 'var(--text2)', fontSize: 13 }}>
                    No trade-ins match the current status filter ({tradeIns.length} in range).
                  </div>
                ) : (
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr style={{ background: 'var(--surface2)' }}>
                        {['Received', 'Device', 'Serial / IMEI', 'Customer', 'Activation #', 'Credit', 'Status', 'Sent Back', 'Actions'].map(h => (
                          <th key={h} style={th}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {displayedTradeIns.map(t => (
                        <tr key={t.id}>
                          <td style={{ ...td, color: 'var(--text2)' }}>{t.received_at ? new Date(t.received_at).toLocaleDateString() : '—'}</td>
                          <td style={{ ...td, fontWeight: 600 }}>{t.device_description || '—'}</td>
                          <td style={{ ...td, color: 'var(--text2)', fontFamily: 'monospace' }}>{t.serial_number || t.imei || '—'}</td>
                          <td style={td}>{t.customer_name || '—'}</td>
                          <td style={{ ...td, color: '#3498db', fontWeight: 600 }}>{t.activation_number ?? '—'}</td>
                          <td style={{ ...td, color: '#27ae60', fontWeight: 700 }}>{fmt(t.credit_amount || 0)}</td>
                          <td style={td}>
                            <span style={{ color: statusColor(t.status), fontWeight: 600 }}>{statusLabel(t.status)}</span>
                          </td>
                          <td style={{ ...td, color: 'var(--text2)' }}>{t.sent_back_at ? new Date(t.sent_back_at).toLocaleDateString() : '—'}</td>
                          {/* Actions always shown — the server enforces who may change trade-ins. */}
                          <td style={{ ...td, whiteSpace: 'nowrap' }}>
                            {t.status === 'received' ? (
                              <div style={{ display: 'flex', gap: 6 }}>
                                <button onClick={() => updateTradeIn(t, 'sent_back')} style={{ background: '#27ae60', border: 'none', color: 'white', borderRadius: 5, padding: '4px 10px', fontSize: 11, fontWeight: 600, cursor: 'pointer' }}>Mark Sent Back</button>
                                <button onClick={() => updateTradeIn(t, 'written_off')} style={{ background: 'transparent', border: '1px solid #e74c3c', color: '#e74c3c', borderRadius: 5, padding: '4px 10px', fontSize: 11, fontWeight: 600, cursor: 'pointer' }}>Write Off</button>
                              </div>
                            ) : (
                              <span style={{ color: 'var(--text3)', fontSize: 11 }}>—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr style={{ background: 'var(--surface2)', borderTop: '2px solid var(--border)' }}>
                        <td colSpan={5} style={{ padding: '10px 14px', fontWeight: 700, color: 'var(--text2)' }}>SHOWN ({displayedTradeIns.length} of {tradeIns.length} in range)</td>
                        <td style={{ padding: '10px 14px', fontWeight: 700, color: '#27ae60', fontSize: 14 }}>{fmt(displayedTradeIns.reduce((s, t) => s + (t.credit_amount || 0), 0))}</td>
                        <td colSpan={3} />
                      </tr>
                    </tfoot>
                  </table>
                )}
              </div>
            )}

            {/* TRADE-IN FINANCIAL IMPACT TABLE */}
            {!loading && tradeIns.length > 0 && activeReport === 'Trade-In Financial Impact' && (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ background: 'var(--surface2)' }}>
                      {['Received', 'Device', 'Serial / IMEI', 'Customer', 'Activation #', 'Age (days)', 'Status', 'Credit at Risk'].map(h => (
                        <th key={h} style={th}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {tradeIns.map(t => (
                      <tr key={t.id}>
                        <td style={{ ...td, color: 'var(--text2)' }}>{t.received_at ? new Date(t.received_at).toLocaleDateString() : '—'}</td>
                        <td style={{ ...td, fontWeight: 600 }}>{t.device_description || '—'}</td>
                        <td style={{ ...td, color: 'var(--text2)', fontFamily: 'monospace' }}>{t.serial_number || t.imei || '—'}</td>
                        <td style={td}>{t.customer_name || '—'}</td>
                        <td style={{ ...td, color: '#3498db', fontWeight: 600 }}>{t.activation_number ?? '—'}</td>
                        <td style={{ ...td, color: ageDays(t) > 30 ? '#e74c3c' : '#f39c12', fontWeight: 700 }}>{ageDays(t)}</td>
                        <td style={td}>
                          <span style={{ color: statusColor(t.status), fontWeight: 600 }}>{statusLabel(t.status)}</span>
                        </td>
                        <td style={{ ...td, color: '#e74c3c', fontWeight: 700 }}>{fmt(t.credit_amount || 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr style={{ background: 'var(--surface2)', borderTop: '2px solid var(--border)' }}>
                      <td colSpan={7} style={{ padding: '10px 14px', fontWeight: 700, color: 'var(--text2)' }}>
                        TOTAL AT RISK ({tradeIns.length} units — money lost if devices never go back)
                      </td>
                      <td style={{ padding: '10px 14px', fontWeight: 700, color: '#e74c3c', fontSize: 14 }}>{fmt(tradeOutstandingCredit + tradeWrittenOffCredit)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}

          </div>
        </div>
      </div>
    </div>
  )
}
