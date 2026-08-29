'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { SendReportButton } from '@/lib/send-report'
import { ExportButtons, type ExportPayload } from '@/lib/export'

const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: '#dc2626', HIGH: '#d97706', MEDIUM: '#2563eb', LOW: '#64748b',
}

// The flag row shape the table + exports read (the endpoint returns more; these are the used fields).
interface Flag {
  id?: string
  flag_type?: string; severity?: string; days_active?: number | null
  epay_salesperson?: string; store_address?: string; store_code?: string; mdn?: string; imei?: string
  phone_model?: string; customer_plan?: string
  activation_date?: string; transaction_date?: string
  amount?: number; description?: string; coaching_note?: string
  // mig 287 — the district manager's decision, and the flag's lifecycle
  status?: string; resolved_at?: string | null; resolved_reason?: string | null
  reviewed_by?: string | null; reviewed_at?: string | null; action_taken?: string | null
}

const STATUS_LABEL: Record<string, string> = {
  open: 'Open', resolved: 'Cleared', superseded: 'Replaced',
}

const WINDOWS = [
  { id: '', label: 'Any window' },
  { id: '30', label: '≤ 30 days' },
  { id: '60', label: '≤ 60 days' },
  { id: '90', label: '≤ 90 days' },
  { id: '90+', label: '90+ days' },
]

export default function FlagsPage() {
  const { period } = usePeriod()
  const [flags, setFlags] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [fType, setFType] = useState('')
  const [fRep, setFRep] = useState('')
  const [fStore, setFStore] = useState('')
  const [fSearch, setFSearch] = useState('')
  const [fModel, setFModel] = useState('')
  const [fWindow, setFWindow] = useState('')
  const [fActMonth, setFActMonth] = useState('')
  const [sortKey, setSortKey] = useState('days_active')
  const [sortDir, setSortDir] = useState<'asc'|'desc'>('asc')
  const [showMatrix, setShowMatrix] = useState(false)
  // mig 287 — a flag whose condition has cleared is RETIRED, not deleted, so it is hidden by default
  // and can still be read back for the audit trail.
  const [showRetired, setShowRetired] = useState(false)
  const [fReview, setFReview] = useState('')          // '' | 'todo' | 'done'
  const [reviewing, setReviewing] = useState<Flag | null>(null)
  const [reviewNote, setReviewNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    setLoading(true)
    api(`/api/v1/commcalc/flags/${encodeURIComponent(period)}?org_id=${ORG_ID}`
        + (showRetired ? '&include_resolved=true' : ''))
      .then(d => setFlags(d || []))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [period, showRetired, reload])

  // The DM's decision. It is written on the FLAG, and — since migration 287 — it survives the nightly
  // recalculation instead of being wiped with the rest of the period's flags.
  async function saveReview(f: Flag, clear = false) {
    if (!f.id) return
    setSaving(true)
    try {
      await api(`/api/v1/commcalc/flags/${f.id}/review?org_id=${ORG_ID}`, {
        method: 'POST',
        body: JSON.stringify(clear ? { clear: true } : { action_taken: reviewNote.trim() }),
      })
      setReviewing(null); setReviewNote(''); setReload(r => r + 1)
    } catch (e: any) {
      alert(e?.message || 'Could not save the review')
    } finally {
      setSaving(false)
    }
  }

  // ONE store label for the whole page. `store_address` is the free-text spelling the producing
  // report wrote and is BLANK on most MI-derived rows; `store_code` (mig 285) is the RESOLVED store
  // those rows are routed to. Falling back to it is what makes a newly-visible flag readable — and it
  // keeps the RULE FIVE store filter, the store matrix and every export agreeing on one value.
  const storeOf = (f: Flag) => (f.store_address || '').trim() || (f.store_code || '').trim()

  const types  = useMemo(() => [...new Set(flags.map(f => f.flag_type).filter(Boolean))].sort(), [flags])
  const reps   = useMemo(() => [...new Set(flags.map(f => f.epay_salesperson).filter(Boolean))].sort(), [flags])
  const stores = useMemo(() => [...new Set(flags.map(storeOf).filter(Boolean))].sort(), [flags])
  const actMonths = useMemo(() => [...new Set(flags.map(f => String(f.activation_date || f.transaction_date || '').slice(0, 7)).filter(Boolean))].sort().reverse(), [flags])

  const filtered = useMemo(() => {
    let rows = flags.filter(f => {
      if (fType && f.flag_type !== fType) return false
      if (fRep && f.epay_salesperson !== fRep) return false
      if (fStore && storeOf(f) !== fStore) return false
      if (fReview === 'todo' && f.reviewed_by) return false
      if (fReview === 'done' && !f.reviewed_by) return false
      if (fModel && !(f.phone_model || '').toLowerCase().includes(fModel.toLowerCase())) return false
      if (fActMonth && String(f.activation_date || f.transaction_date || '').slice(0, 7) !== fActMonth) return false
      if (fSearch) {
        const q = fSearch.toLowerCase()
        const hay = `${f.mdn||''} ${f.imei||''} ${f.description||''}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      if (fWindow) {
        const d = f.days_active == null ? null : Number(f.days_active)
        if (d == null || isNaN(d)) return false
        if (fWindow === '30' && !(d <= 30)) return false
        if (fWindow === '60' && !(d <= 60)) return false
        if (fWindow === '90' && !(d <= 90)) return false
        if (fWindow === '90+' && !(d > 90)) return false
       }
      return true
    })
    rows.sort((a, b) => {
      // Sorting the Store column follows what the column DISPLAYS (storeOf), otherwise every
      // resolved-but-address-less row sorts into one blank block at the end.
      const pick = (f: any) => sortKey === 'store_address' ? storeOf(f) : f[sortKey]
      let av = pick(a), bv = pick(b)
      if (av == null) av = sortDir === 'asc' ? Infinity : -Infinity
      if (bv == null) bv = sortDir === 'asc' ? Infinity : -Infinity
      if (typeof av === 'string') { av = av.toLowerCase(); bv = (bv||'').toLowerCase() }
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ? 1 : -1
      return 0
    })
    return rows
  }, [flags, fType, fRep, fStore, fModel, fSearch, fWindow, fActMonth, fReview, sortKey, sortDir])

  const pendingReview = useMemo(
    () => filtered.filter(f => (f.status || 'open') === 'open' && !f.reviewed_by).length, [filtered])

  const totalAtRisk = filtered.reduce((s, f) => s + Math.abs(f.amount || 0), 0)

  const summary = useMemo(() => {
    const m: Record<string, { count: number; amt: number }> = {}
    flags.forEach(f => {
      if (!m[f.flag_type]) m[f.flag_type] = { count: 0, amt: 0 }
      m[f.flag_type].count++
      m[f.flag_type].amt += Math.abs(f.amount || 0)
    })
    return Object.entries(m).sort((a, b) => b[1].count - a[1].count)
  }, [flags])

  function sortBy(key: string) {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('asc') }
  }

  function exportCSV() {
    const head = 'Flag Type,Severity,Days Active,Rep,Store,MDN,IMEI,Phone Model,Plan,Activated,Amount,Description,Status,Reviewed By,Reviewed At,Action Taken'
    const rows = filtered.map(f => [
      f.flag_type, f.severity, f.days_active ?? '', f.epay_salesperson || '',
      `"${storeOf(f).replace(/"/g,'')}"`, f.mdn || '', f.imei || '',
      `"${(f.phone_model||'').replace(/"/g,'')}"`, `"${(f.customer_plan||'').replace(/"/g,'')}"`,
      String(f.activation_date||f.transaction_date||'').slice(0,10),
      f.amount || '', `"${(f.description||'').replace(/"/g,'').replace(/\n/g,' ')}"`,
      STATUS_LABEL[f.status || 'open'] || (f.status || 'Open'),
      `"${(f.reviewed_by||'').replace(/"/g,'')}"`, String(f.reviewed_at||'').slice(0,10),
      `"${(f.action_taken||'').replace(/"/g,'').replace(/\n/g,' ')}"`,
    ].join(','))
    const a = document.createElement('a')
    a.href = 'data:text/csv,' + encodeURIComponent([head, ...rows].join('\n'))
    a.download = `flags-${period.replace(' ','-')}.csv`; a.click()
  }

  // WYSIWYG (§3c/§3d) — Send used to take the server report-key path (reportKey "flags", period only), so
  // notify/report_registry._flags re-queried every flag in the org: type / rep / store / model /
  // window / activation-month / search were all dropped, and a per-rep compliance send carried the
  // whole team. Excel · PDF · Print · Send now all render from THESE filtered+sorted rows.
  const filterDesc = () => [
    fType && `type: ${fType}`, fRep && `rep: ${fRep}`, fStore && `store: ${fStore}`,
    fModel && `model: ${fModel}`, fWindow && `window: ${WINDOWS.find(w => w.id === fWindow)?.label || fWindow}`,
    fActMonth && `activated: ${fActMonth}`, fSearch && `search: “${fSearch}”`,
    fReview === 'todo' && 'awaiting DM review', fReview === 'done' && 'reviewed',
    showRetired && 'including cleared/replaced flags',
  ].filter(Boolean).join(' · ')

  function buildPayload(): ExportPayload {
    const fd = filterDesc()
    return {
      title: 'Flags & Compliance',
      subtitle: `${period} · ${filtered.length}${filtered.length !== flags.length ? ` of ${flags.length}` : ''} flags · at risk ${fmt(totalAtRisk)}${fd ? ` · ${fd}` : ''}`,
      filename: `flags-${period.replace(/ /g, '-')}${filtered.length !== flags.length ? '-filtered' : ''}`.toLowerCase(),
      sheets: [{ name: 'Flags', rows: filtered, columns: [
        { header: 'Flag Type', get: (f: Flag) => (f.flag_type || '').replace(/_/g, ' ') },
        { header: 'Severity', get: (f: Flag) => f.severity },
        { header: 'Days Active', get: (f: Flag) => (f.days_active ?? ''), align: 'right' },
        { header: 'Rep', get: (f: Flag) => f.epay_salesperson || '' },
        { header: 'Store', get: (f: Flag) => storeOf(f) },
        { header: 'MDN', get: (f: Flag) => f.mdn || '' },
        { header: 'IMEI', get: (f: Flag) => f.imei || '' },
        { header: 'Phone Model', get: (f: Flag) => f.phone_model || '' },
        { header: 'Plan', get: (f: Flag) => f.customer_plan || '' },
        { header: 'Activated', get: (f: Flag) => String(f.activation_date || f.transaction_date || '').slice(0, 10) },
        { header: 'Amount', get: (f: Flag) => Math.abs(f.amount || 0), money: true },
        { header: 'Description', get: (f: Flag) => f.description || '' },
        // RULE FOUR (what you see is what exports): the DM's decision and the flag's lifecycle are
        // now part of the record, so they travel with every Excel / PDF / email / WhatsApp export.
        { header: 'Status', get: (f: Flag) => STATUS_LABEL[f.status || 'open'] || (f.status || 'Open') },
        { header: 'Reviewed By', get: (f: Flag) => f.reviewed_by || '' },
        { header: 'Reviewed At', get: (f: Flag) => String(f.reviewed_at || '').slice(0, 10) },
        { header: 'Action Taken', get: (f: Flag) => f.action_taken || '' },
      ] }],
    }
  }

  const TH = ({ k, label, align = 'left' }: { k: string; label: string; align?: string }) => (
    <th onClick={() => sortBy(k)} style={{
      padding: '10px 10px', fontSize: 11, fontWeight: 700, color: 'white', textAlign: align as any,
      cursor: 'pointer', whiteSpace: 'nowrap', position: 'sticky', top: 0, background: '#1e3a5f', zIndex: 2,
    }}>
      {label}{sortKey === k ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
    </th>
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Flags & Compliance</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · {filtered.length} of {flags.length} flags · At risk: <strong style={{ color: 'var(--red)' }}>{fmt(totalAtRisk)}</strong>
            {pendingReview > 0 && <> · <strong style={{ color: '#d97706' }}>{pendingReview} awaiting review</strong></>}
          </p>
        </div>
        <div style={{ display: 'inline-flex', gap: 6 }}>
          <button className="btn btn-secondary" onClick={exportCSV}>📥 CSV</button>
          <ExportButtons payload={buildPayload} compact />
          <SendReportButton exportPayload={buildPayload} title="Flags & Compliance" compact />
        </div>
      </div>

      {/* Filters — moved to top */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
        <select className="select" value={fType} onChange={e => setFType(e.target.value)}>
          <option value="">All types</option>
          {types.map(t => <option key={t} value={t}>{t.replace(/_/g,' ')}</option>)}
        </select>
        <select className="select" value={fRep} onChange={e => setFRep(e.target.value)}>
          <option value="">All reps</option>
          {reps.map(r => <option key={r} value={r}>{r}</option>)}
        </select>
        <select className="select" value={fStore} onChange={e => setFStore(e.target.value)}>
          <option value="">All stores</option>
          {stores.map(s => <option key={s} value={s}>{s.substring(0,35)}</option>)}
        </select>
        <select className="select" value={fWindow} onChange={e => setFWindow(e.target.value)}>
          {WINDOWS.map(w => <option key={w.id} value={w.id}>{w.label}</option>)}
        </select>
        <select className="select" value={fActMonth} onChange={e => setFActMonth(e.target.value)} title="Filter by the month the line was activated">
          <option value="">Any activation month</option>
          {actMonths.map(m => <option key={m} value={m}>Activated {m}</option>)}
        </select>
        <input className="input" placeholder="Search MDN / IMEI…" value={fSearch}
          onChange={e => setFSearch(e.target.value)} style={{ width: 160 }} />
        <input className="input" placeholder="Phone model…" value={fModel}
          onChange={e => setFModel(e.target.value)} style={{ width: 140 }} />
        <select className="select" value={fReview} onChange={e => setFReview(e.target.value)}
          title="A flag is meant to reach the district manager, be decided, and stay decided">
          <option value="">Reviewed or not</option>
          <option value="todo">Awaiting DM review</option>
          <option value="done">Reviewed</option>
        </select>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text2)' }}
          title="A flag whose condition has cleared is kept for the audit trail instead of being deleted, so the review on it is never lost.">
          <input type="checkbox" checked={showRetired} onChange={e => setShowRetired(e.target.checked)} />
          Show cleared / replaced
        </label>
        {(fType||fRep||fStore||fWindow||fSearch||fModel||fActMonth||fReview) && (
          <button className="btn btn-secondary" onClick={() => { setFType('');setFRep('');setFStore('');setFWindow('');setFSearch('');setFModel('');setFActMonth('');setFReview('') }}>✕ Clear</button>
        )}
      </div>

      {/* Store summary matrix — collapsible (▸/▾) */}
      {(() => {
        const storeRows: Record<string, Record<string, number>> = {}
        flags.forEach(f => {
          const st = storeOf(f) || 'Unrouted (no store resolved)'
          if (!storeRows[st]) storeRows[st] = {}
          storeRows[st][f.flag_type] = (storeRows[st][f.flag_type] || 0) + 1
        })
        const allTypes = [...new Set(flags.map(f => f.flag_type).filter(Boolean))].sort()
        const rows = Object.entries(storeRows)
          .map(([st, counts]) => ({ st, counts, total: Object.values(counts).reduce((a, b) => a + b, 0) }))
          .sort((a, b) => b.total - a.total)
        if (!rows.length) return null
        return (
          <div style={{ marginBottom: 20, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 12, background: 'white' }}>
            <div onClick={() => setShowMatrix(!showMatrix)} style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: showMatrix ? '1px solid var(--border)' : 'none', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>{showMatrix ? '▾' : '▸'} Store Summary — {rows.length} stores{fStore ? ` · filtered: ${fStore.substring(0,24)}` : ' · click a store to filter details'}</span>
              <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>{showMatrix ? 'hide' : 'show'}</span>
            </div>
            {showMatrix && (
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
              <thead>
                <tr>
                  <th style={{ padding: '8px 10px', fontSize: 10, fontWeight: 700, textAlign: 'left', position: 'sticky', left: 0, background: '#1e3a5f', color: 'white' }}>Store</th>
                  {allTypes.map(t => (
                    <th key={t} style={{ padding: '8px 6px', fontSize: 9, fontWeight: 700, textAlign: 'center', background: '#1e3a5f', color: 'white', whiteSpace: 'nowrap' }}>
                      {t.replace(/_/g, ' ')}
                    </th>
                  ))}
                  <th style={{ padding: '8px 10px', fontSize: 10, fontWeight: 700, textAlign: 'center', background: '#0f2540', color: 'white' }}>Total</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ st, counts, total }, i) => (
                  <tr key={st} onClick={() => setFStore(fStore === st ? '' : st)}
                    style={{ cursor: 'pointer', background: fStore === st ? '#eff6ff' : i % 2 ? '#fafbfc' : 'white', borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '7px 10px', fontSize: 11, fontWeight: 600, position: 'sticky', left: 0, background: 'inherit' }}>{st.substring(0, 30)}</td>
                    {allTypes.map(t => (
                      <td key={t} style={{ padding: '7px 6px', fontSize: 12, textAlign: 'center', color: counts[t] ? 'var(--text)' : 'var(--text3)', fontWeight: counts[t] ? 700 : 400 }}>
                        {counts[t] || '·'}
                      </td>
                    ))}
                    <td style={{ padding: '7px 10px', fontSize: 12, textAlign: 'center', fontWeight: 700, background: '#f1f5f9' }}>{total}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            )}
          </div>
        )
      })()}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
        {summary.map(([type, s]) => (
          <button key={type} onClick={() => setFType(fType === type ? '' : type)} className="card" style={{
            padding: '8px 14px', cursor: 'pointer', border: fType === type ? '2px solid var(--accent)' : '1px solid var(--border)',
            display: 'flex', flexDirection: 'column', gap: 2, minWidth: 120,
          }}>
            <span style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600 }}>{type.replace(/_/g, ' ')}</span>
            <span style={{ fontSize: 16, fontWeight: 700 }}>{s.count}</span>
            {s.amt > 0 && <span style={{ fontSize: 11, color: 'var(--red)' }}>{fmt(s.amt)}</span>}
          </button>
        ))}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 340px)', background: 'white', border: '1px solid var(--border)', borderRadius: 12 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1100 }}>
            <thead>
              <tr>
                <TH k="flag_type" label="Flag Type" />
                <TH k="severity" label="Severity" />
                <TH k="days_active" label="Days Active" align="right" />
                <TH k="epay_salesperson" label="Rep" />
                <TH k="store_address" label="Store" />
                <TH k="mdn" label="MDN" />
                <TH k="imei" label="IMEI" />
                <TH k="phone_model" label="Phone Model" />
                <TH k="customer_plan" label="Plan" />
                <TH k="activation_date" label="Activated" />
                <TH k="amount" label="Amount" align="right" />
                <TH k="reviewed_by" label="DM Review" />
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr><td colSpan={12} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                  {flags.length === 0 ? 'No flags — run calculation to generate' : 'No flags match filters'}
                </td></tr>
              ) : filtered.map((f, i) => (
                <tr key={f.id || i} style={{
                  background: (f.status && f.status !== 'open') ? '#f8fafc' : i % 2 ? '#fafbfc' : 'white',
                  borderBottom: '1px solid var(--border)',
                  opacity: (f.status && f.status !== 'open') ? 0.62 : 1,
                }}>
                  <td style={{ padding: '8px 10px', fontSize: 12, fontWeight: 600 }}>{f.flag_type?.replace(/_/g,' ')}</td>
                  <td style={{ padding: '8px 10px' }}>
                    <span style={{ fontSize: 10, fontWeight: 700, color: SEVERITY_COLORS[f.severity] || '#64748b',
                      background: (SEVERITY_COLORS[f.severity] || '#64748b') + '18', padding: '2px 7px', borderRadius: 999 }}>
                      {f.severity}
                    </span>
                  </td>
                  <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 600,
                    color: f.days_active != null && f.days_active <= 30 ? '#dc2626' : f.days_active != null && f.days_active <= 60 ? '#d97706' : 'var(--text)' }}>
                    {f.days_active != null ? f.days_active : '—'}
                  </td>
                  <td style={{ padding: '8px 10px', fontSize: 12 }}>{f.epay_salesperson || '—'}</td>
                  <td style={{ padding: '8px 10px', fontSize: 11, color: 'var(--text3)' }}>{(storeOf(f)||'—').substring(0,28)}</td>
                  <td style={{ padding: '8px 10px', fontSize: 11 }}>{f.mdn || '—'}</td>
                  <td style={{ padding: '8px 10px', fontSize: 11, fontFamily: 'monospace' }}>{f.imei || '—'}</td>
                  <td style={{ padding: '8px 10px', fontSize: 11 }}>{(f.phone_model||'—').substring(0,30)}</td>
                  <td style={{ padding: '8px 10px', fontSize: 11, color: 'var(--text3)' }}>{(f.customer_plan||'—').substring(0,25)}</td>
                  <td style={{ padding: '8px 10px', fontSize: 11, color: 'var(--text3)' }}>{String(f.activation_date||f.transaction_date||'—').substring(0,10)}</td>
                  <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 600 }} title={f.flag_type==='CHARGEBACK'?'Rebate lost':''}>{f.amount ? fmt(Math.abs(f.amount)) : '—'}</td>
                  {/* The DM's decision. Since mig 287 it survives the nightly recalculation. */}
                  <td style={{ padding: '8px 10px', fontSize: 11, whiteSpace: 'nowrap' }}>
                    {f.status && f.status !== 'open' && (
                      <div style={{ fontSize: 10, fontWeight: 700, color: '#475569' }}
                        title={f.resolved_reason || ''}>
                        {STATUS_LABEL[f.status] || f.status}
                        {f.resolved_at ? ` ${String(f.resolved_at).slice(0, 10)}` : ''}
                      </div>
                    )}
                    {f.reviewed_by ? (
                      <button onClick={() => { setReviewing(f); setReviewNote(f.action_taken || '') }}
                        title={`${f.action_taken || 'Reviewed'} — ${String(f.reviewed_at || '').slice(0, 10)}`}
                        style={{ border: 'none', background: 'transparent', padding: 0, cursor: 'pointer',
                                 textAlign: 'left', color: '#15803d', fontWeight: 700, fontSize: 11 }}>
                        ✓ {String(f.reviewed_by).split('@')[0]}
                      </button>
                    ) : (
                      <button className="btn btn-secondary" style={{ padding: '2px 8px', fontSize: 11 }}
                        onClick={() => { setReviewing(f); setReviewNote('') }}>Review</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── DM review ────────────────────────────────────────────────────────────────────────── */}
      {reviewing && (
        <div onClick={() => !saving && setReviewing(null)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,.45)', display: 'flex',
                   alignItems: 'center', justifyContent: 'center', zIndex: 60, padding: 16 }}>
          <div onClick={e => e.stopPropagation()} className="card"
            style={{ width: 520, maxWidth: '100%', padding: 20, background: 'white' }}>
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>Review flag</h3>
            <p style={{ margin: '6px 0 0', fontSize: 12, color: 'var(--text2)' }}>
              {(reviewing.flag_type || '').replace(/_/g, ' ')} · {reviewing.epay_salesperson || 'no rep'} ·{' '}
              {storeOf(reviewing) || 'no store'}
            </p>
            <p style={{ margin: '8px 0 0', fontSize: 13 }}>{reviewing.description}</p>
            {reviewing.coaching_note && (
              <p style={{ margin: '6px 0 0', fontSize: 12, color: 'var(--text3)' }}>{reviewing.coaching_note}</p>
            )}
            <label style={{ display: 'block', marginTop: 14, fontSize: 12, fontWeight: 600 }}>
              What did you decide?
            </label>
            <textarea className="input" rows={3} value={reviewNote} autoFocus
              onChange={e => setReviewNote(e.target.value)}
              placeholder="e.g. Coached the rep — not chargeable"
              style={{ width: '100%', marginTop: 4, resize: 'vertical' }} />
            <p style={{ margin: '8px 0 0', fontSize: 11, color: 'var(--text3)' }}>
              Your decision stays on this flag. It is no longer wiped when commissions are recalculated,
              and it is kept even if the flag later clears.
            </p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 14 }}>
              {reviewing.reviewed_by && (
                <button className="btn btn-secondary" disabled={saving}
                  onClick={() => saveReview(reviewing, true)}>Clear review</button>
              )}
              <button className="btn btn-secondary" disabled={saving}
                onClick={() => setReviewing(null)}>Cancel</button>
              <button className="btn" disabled={saving} onClick={() => saveReview(reviewing)}>
                {saving ? 'Saving…' : 'Save review'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
    )
}