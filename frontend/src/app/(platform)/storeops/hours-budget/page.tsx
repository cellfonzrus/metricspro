'use client'
import { useEffect, useState, useCallback, useMemo } from 'react'
import { api } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, type StandardFilterValue } from '@/lib/standard-filters'

// Per-store weekly hours BUDGET + DM-approved overrides (mig 087). A manager sets each store's
// weekly hours budget; the scheduler blocks going over it and shows an alert here; to exceed, a
// manager requests DM approval and the DM ticks approve in-app (recorded) — which unlocks that
// store+week. "Week" = the tenant work-week (from Pay Period & Work-Week settings).
interface Row { store_code: string; address?: string; market?: string | null; weekly_hours: number | null; used_hours: number; over: boolean; override: boolean }
interface Ov { id: string; store_code: string; week_start: string; reason?: string; status: string; requested_by?: string; decided_by?: string }

export default function HoursBudgetPage() {
  const [week, setWeek] = useState('')
  const [weekStart, setWeekStart] = useState('')
  const [rows, setRows] = useState<Row[]>([])
  const [ovs, setOvs] = useState<Ov[]>([])
  const [edit, setEdit] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')
  // RULE FIVE (§3d): this surface is one row PER STORE (no employee/rep dimension — omitted below,
  // deviation documented in the handoff) with `market` now returned by the backend. Period stays the
  // existing native "week" date control above (not substituted).
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())

  const load = useCallback(() => {
    api(`/api/v1/storeops/hours-budgets${week ? `?week=${week}` : ''}`).then((r: any) => { setRows(r.budgets || []); setWeekStart(r.week_start || '') }).catch((e: any) => setMsg('❌ ' + (e?.message || e)))
    api('/api/v1/storeops/budget-overrides').then((r: any) => setOvs(r.overrides || [])).catch(() => {})
  }, [week])
  useEffect(() => { load() }, [load])

  const storeMarket = useMemo(() => {
    const m: Record<string, string> = {}
    for (const r of rows) if (r.store_code) m[r.store_code] = r.market || ''
    return m
  }, [rows])
  const storeOptions = useMemo(() => rows
    .filter(r => r.store_code)
    .map(r => ({ id: r.store_code, label: r.store_code, sublabel: r.address || r.market || undefined }))
    .sort((a, b) => a.label.localeCompare(b.label)), [rows])
  const marketOptions = useMemo(() =>
    Array.from(new Set(rows.map(r => r.market).filter(Boolean) as string[])).sort(), [rows])

  // Filters narrow BOTH tables (budget rows are already 1-per-store, so this is a plain row filter —
  // no shift-level aggregation to narrow first here) and drive both exports.
  const visibleRows = useMemo(() => filterRows(rows, filt, {
    store: r => r.store_code, market: r => r.market || '',
  }), [rows, filt])
  const visibleOvs = useMemo(() => filterRows(ovs, filt, {
    store: r => r.store_code, market: r => storeMarket[r.store_code] || '',
  }), [ovs, filt, storeMarket])

  async function saveBudget(store: string) {
    const v = edit[store]
    try {
      await api('/api/v1/storeops/hours-budgets', { method: 'PUT', body: JSON.stringify({ store_code: store, weekly_hours: v === '' ? null : Number(v) }) })
      setMsg(`✅ Budget saved for ${store}.`); setEdit(e => { const n = { ...e }; delete n[store]; return n }); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function requestOverride(store: string) {
    const reason = prompt(`Reason to exceed ${store}'s budget for the week of ${weekStart}:`) ?? ''
    try {
      const r: any = await api('/api/v1/storeops/budget-overrides', { method: 'POST', body: JSON.stringify({ store_code: store, week_start: weekStart, reason }) })
      setMsg(r.dm?.emailed ? `✅ Requested — DM (${r.dm.name || r.dm.email}) notified.` : `✅ Requested.${r.note ? ' ' + r.note : ''}`); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function decide(o: Ov, decision: 'approve' | 'deny') {
    const note = decision === 'deny' ? (prompt('Reason (optional):') ?? '') : ''
    try { await api(`/api/v1/storeops/budget-overrides/${o.id}/decision`, { method: 'POST', body: JSON.stringify({ decision, note }) }); setMsg(decision === 'approve' ? '✅ Approved.' : 'Denied.'); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  const pending = visibleOvs.filter(o => o.status === 'pending')
  const inp: React.CSSProperties = { width: 90, padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

  // RULE FOUR (§3c): export the visible rows for each table — no PII in either.
  const budgetCols: ExportColumn[] = [
    { header: 'Store', field: 'store_code', role: 'store', get: r => r.store_code },
    { header: 'Address', field: 'address', get: r => r.address || '' },
    { header: 'Weekly Budget (h)', field: 'weekly_hours', type: 'number', get: r => r.weekly_hours ?? '' },
    { header: 'Scheduled (h)', field: 'used_hours', type: 'number', get: r => r.used_hours },
    { header: 'Status', field: 'status', get: r => r.override ? 'Override approved' : (r.over ? 'Over budget' : 'OK') },
  ]
  const ovCols: ExportColumn[] = [
    { header: 'Store', field: 'store_code', role: 'store', get: o => o.store_code },
    { header: 'Week', field: 'week_start', role: 'date', type: 'date', get: o => o.week_start },
    { header: 'Reason', field: 'reason', get: o => o.reason || '' },
    { header: 'Status', field: 'status', get: o => o.status },
    { header: 'Requested By', field: 'requested_by', role: 'rep', get: o => o.requested_by || '' },
    { header: 'Decided By', field: 'decided_by', get: o => o.decided_by || '' },
  ]

  return (
    <div style={{ maxWidth: 900 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>📊 Hours budget</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 14px' }}>
        Each store&apos;s weekly labor-hours budget. Scheduling past it is blocked with an alert; a manager can
        request District Manager approval to exceed it for a given week. Week starting <b>{weekStart || '—'}</b>.
        <input type="date" value={week} onChange={e => setWeek(e.target.value)} style={{ ...inp, width: 150, marginLeft: 10 }} />
      </p>
      {msg && <div style={{ fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      <StandardFilterBar
        value={filt} onChange={setFilt}
        show={{ period: false, reps: false }}
        periodMode="none"
        storeOptions={storeOptions} marketOptions={marketOptions}
        storeLabel="Stores…"
      />

      <div className="card" style={{ padding: 16, marginBottom: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
          <ReportExportBar title="Hours Budget" subtitle={`Week of ${weekStart || '—'}`} filename="hours-budget" columns={budgetCols} rows={visibleRows} />
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>{['Store', 'Weekly budget (h)', 'Scheduled this week', '', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '7px 9px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
          <tbody>
            {visibleRows.map(r => {
              const editing = r.store_code in edit
              const val = editing ? edit[r.store_code] : (r.weekly_hours ?? '')
              return (
                <tr key={r.store_code} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '7px 9px', fontWeight: 600 }}>{r.store_code}<div style={{ fontSize: 11, color: 'var(--text3)' }}>{r.address}</div></td>
                  <td style={{ padding: '7px 9px' }}>
                    <input style={inp} type="number" placeholder="—" value={val as any} onChange={e => setEdit(x => ({ ...x, [r.store_code]: e.target.value }))} />
                    {editing && <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 8px', marginLeft: 6 }} onClick={() => saveBudget(r.store_code)}>Save</button>}
                  </td>
                  <td style={{ padding: '7px 9px', fontWeight: 700, color: r.over ? '#dc2626' : 'var(--text1)' }}>
                    {r.used_hours}h{r.weekly_hours != null && <span style={{ fontWeight: 400, color: 'var(--text3)' }}> / {r.weekly_hours}h</span>}
                  </td>
                  <td style={{ padding: '7px 9px' }}>
                    {r.over && !r.override && <span style={{ padding: '1px 7px', borderRadius: 999, fontSize: 11, fontWeight: 700, color: '#991b1b', background: '#fee2e2' }}>⚠ Over budget</span>}
                    {r.override && <span style={{ padding: '1px 7px', borderRadius: 999, fontSize: 11, fontWeight: 700, color: '#166534', background: '#dcfce7' }}>✓ Override approved</span>}
                  </td>
                  <td style={{ padding: '7px 9px', textAlign: 'right' }}>
                    {r.over && !r.override && <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => requestOverride(r.store_code)}>Request DM approval</button>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {visibleRows.length === 0 && <div style={{ fontSize: 13, color: 'var(--text3)', padding: 8 }}>No stores found.</div>}
      </div>

      <div className="card" style={{ padding: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>Override approvals {pending.length > 0 && <span style={{ color: '#92400e' }}>· {pending.length} pending</span>}</div>
          {visibleOvs.length > 0 && <ReportExportBar title="Hours Budget — Override Approvals" columns={ovCols} rows={visibleOvs} />}
        </div>
        {visibleOvs.length === 0 ? <div style={{ fontSize: 13, color: 'var(--text3)' }}>No override requests.</div> : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Store', 'Week', 'Reason', 'Status', 'Requested by', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '7px 9px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
            <tbody>
              {visibleOvs.slice(0, 40).map(o => (
                <tr key={o.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '7px 9px', fontWeight: 600 }}>{o.store_code}</td>
                  <td style={{ padding: '7px 9px' }}>{o.week_start}</td>
                  <td style={{ padding: '7px 9px', color: 'var(--text3)' }}>{o.reason || '—'}</td>
                  <td style={{ padding: '7px 9px' }}>{o.status === 'pending' ? <b style={{ color: '#92400e' }}>Pending</b> : o.status === 'approved' ? <span style={{ color: '#166534' }}>Approved</span> : <span style={{ color: '#991b1b' }}>Denied</span>}</td>
                  <td style={{ padding: '7px 9px', color: 'var(--text3)' }}>{o.requested_by || '—'}</td>
                  <td style={{ padding: '7px 9px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {o.status === 'pending' && <>
                      <button className="btn btn-primary" style={{ fontSize: 12, padding: '3px 10px' }} onClick={() => decide(o, 'approve')}>✓ Approve</button>{' '}
                      <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 10px', color: '#dc2626' }} onClick={() => decide(o, 'deny')}>✕ Deny</button>
                    </>}
                    {o.status !== 'pending' && <span style={{ fontSize: 11, color: 'var(--text3)' }}>{o.decided_by}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
