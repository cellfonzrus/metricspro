'use client'
import { useEffect, useState, useCallback } from 'react'
import { api } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

// Rep-initiated time-clock permissions → District Manager approval (migration 432). Reps are
// auto-clocked-out at their scheduled end + a 5-minute grace. Two things then need the DM's tick to
// COUNT toward pay:
//   • reclock_in    — a second session after that auto-clock-out (the whole session is held pending).
//   • late_clockout — extra time worked past the scheduled end + grace (only the extra is held pending).
// The tick IS the approval, recorded with who + when; the same request also shows on the rep's kiosk.
interface Perm {
  id: string; employee_id: string; employee_name?: string; store_code?: string; work_date?: string
  kind: 'reclock_in' | 'late_clockout'; status: string
  anchor_at?: string; requested_clock_out?: string; extra_minutes?: number; reason?: string
  requested_by?: string; requested_at?: string; dm_email?: string
  decided_by?: string; decided_at?: string; decision_note?: string
}
const badge: Record<string, { t: string; c: string; b: string }> = {
  pending: { t: 'Pending', c: '#92400e', b: '#fef3c7' },
  approved: { t: 'Approved', c: '#166534', b: '#dcfce7' },
  denied: { t: 'Denied', c: '#991b1b', b: '#fee2e2' },
}
const kindLabel: Record<string, string> = {
  reclock_in: 'Re-clock-in (2nd session)',
  late_clockout: 'Extra time past shift',
}

export default function TimeclockPermissionsPage() {
  const [perms, setPerms] = useState<Perm[]>([])
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    api('/api/v1/storeops/timeclock/permissions').then((r: any) => setPerms(r.permissions || [])).catch((e: any) => setMsg('❌ ' + (e?.message || e)))
  }, [])
  useEffect(() => { load() }, [load])

  async function decide(x: Perm, decision: 'approve' | 'deny') {
    const note = decision === 'deny' ? (prompt('Reason for denying (optional):') ?? '') : ''
    try {
      await api(`/api/v1/storeops/timeclock/permissions/${x.id}/decision`, { method: 'POST', body: JSON.stringify({ decision, note }) })
      setMsg(decision === 'approve' ? '✅ Approved — the time now counts toward their hours.' : 'Denied — the time stays uncounted.'); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  const extra = (x: Perm) => x.kind === 'late_clockout' && x.extra_minutes != null ? `${x.extra_minutes} min` : '—'
  const pending = perms.filter(x => x.status === 'pending')
  const decided = perms.filter(x => x.status !== 'pending').slice(0, 30)

  // RULE FOUR: export exactly what each table shows — no PII beyond name/store/date/kind/status.
  const pendingCols: ExportColumn[] = [
    { header: 'Employee', field: 'employee', role: 'rep', get: x => x.employee_name || x.employee_id },
    { header: 'Store', field: 'store_code', role: 'store', get: x => x.store_code || '' },
    { header: 'Date', field: 'work_date', role: 'date', type: 'date', get: x => x.work_date },
    { header: 'Request', field: 'kind', get: x => kindLabel[x.kind] || x.kind },
    { header: 'Extra', field: 'extra', get: x => extra(x) },
    { header: 'Reason', field: 'reason', get: x => x.reason || '' },
  ]

  return (
    <div style={{ maxWidth: 980 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>⏳ Time-clock permissions</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 16px' }}>
        Reps are auto-clocked-out at their scheduled shift end plus a 5-minute grace. A <b>second session</b> after
        that, or <b>extra time</b> worked past it, is held here until you approve it — approving is what makes that
        time count toward their hours. The rep sees the same pending state on their kiosk.
      </p>
      {msg && <div style={{ fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      <div className="card" style={{ padding: 18, marginBottom: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>Permissions to approve {pending.length > 0 && <span style={{ color: '#92400e' }}>· {pending.length}</span>}</div>
          {pending.length > 0 && <ReportExportBar title="Time-clock Permissions — Pending" columns={pendingCols} rows={pending} />}
        </div>
        {pending.length === 0 ? <div style={{ fontSize: 13, color: 'var(--text3)' }}>Nothing waiting.</div> : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Employee', 'Store', 'Date', 'Request', 'Extra', 'Reason', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '7px 9px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
            <tbody>
              {pending.map(x => (
                <tr key={x.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '7px 9px', fontWeight: 600 }}>{x.employee_name || x.employee_id}</td>
                  <td style={{ padding: '7px 9px' }}>{x.store_code || '—'}</td>
                  <td style={{ padding: '7px 9px' }}>{x.work_date || '—'}</td>
                  <td style={{ padding: '7px 9px', fontWeight: 600, color: 'var(--accent)' }}>{kindLabel[x.kind] || x.kind}</td>
                  <td style={{ padding: '7px 9px' }}>{extra(x)}</td>
                  <td style={{ padding: '7px 9px', color: 'var(--text3)' }}>{x.reason || '—'}</td>
                  <td style={{ padding: '7px 9px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <button className="btn btn-primary" style={{ fontSize: 12, padding: '3px 10px' }} onClick={() => decide(x, 'approve')}>✓ Approve</button>{' '}
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 10px', color: '#dc2626' }} onClick={() => decide(x, 'deny')}>✕ Deny</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {decided.length > 0 && (
        <div className="card" style={{ padding: 18 }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>Recent decisions</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Employee', 'Date', 'Request', 'Status', 'Decided by'].map(h => <th key={h} style={{ textAlign: 'left', padding: '7px 9px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
            <tbody>
              {decided.map(x => { const b = badge[x.status] || badge.pending; return (
                <tr key={x.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '7px 9px', fontWeight: 600 }}>{x.employee_name || x.employee_id}</td>
                  <td style={{ padding: '7px 9px' }}>{x.work_date || '—'}</td>
                  <td style={{ padding: '7px 9px' }}>{kindLabel[x.kind] || x.kind}</td>
                  <td style={{ padding: '7px 9px' }}><span style={{ padding: '1px 7px', borderRadius: 999, fontSize: 11, fontWeight: 700, color: b.c, background: b.b }}>{b.t}</span></td>
                  <td style={{ padding: '7px 9px', color: 'var(--text3)' }}>{x.decided_by || '—'}</td>
                </tr>
              ) })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
