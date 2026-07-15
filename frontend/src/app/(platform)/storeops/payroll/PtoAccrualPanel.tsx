'use client'
import { useState, useEffect } from 'react'
import { api, fmt } from '@/lib/client'

// "Paid Leave Accumulated" (PTO accrual) — config + run panel for the storeops payroll page.
// Backend: GET/PUT/DELETE /storeops/pto-accrual-config, GET /storeops/pto-accrual/{period},
// POST /storeops/pto-accrual/run/{period}. See backend/app/modules/storeops/pto_accrual.py +
// docs/handoffs/people.md for the full contract (money-adjacent, PARKED pending Gate 1/2 review).

interface PtoStore { store: string; accrued_hours: number; taken_hours: number; cost: number }
interface PtoEmployee {
  employee_id: string; name: string; store: string; hours_worked: number
  accrued_hours: number; taken_hours: number; rate: number; mode: string
  payable_balance: number; cost: number; capped: boolean
}

export default function PtoAccrualPanel({ month }: { month: string }) {
  const [cfg, setCfg] = useState<any>(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [view, setView] = useState<{ stores: PtoStore[]; employees: PtoEmployee[]; mode: string; rate: number; last_run_at: string | null } | null>(null)
  const [running, setRunning] = useState(false)
  const [runResult, setRunResult] = useState<any>(null)
  const [open, setOpen] = useState(false)

  function loadConfig() {
    api('/api/v1/storeops/pto-accrual-config').then(r => {
      const eff = r.effective_org_defaults || {}
      setCfg({ enabled: eff.enabled, accrual_rate: eff.accrual_rate, mode: eff.mode,
                max_accrual_hours: eff.max_accrual_hours, hours_per_pto_day: eff.hours_per_pto_day })
    }).catch(() => setCfg({ enabled: true, accrual_rate: 0.0385, mode: 'accrue', max_accrual_hours: null, hours_per_pto_day: 8 }))
  }

  function loadView() {
    api(`/api/v1/storeops/pto-accrual/${month}`).then(setView).catch(() => setView(null))
  }

  useEffect(() => { if (open) { loadConfig(); loadView(); setRunResult(null) } }, [open, month])

  async function saveConfig() {
    if (!cfg) return
    setSaving(true); setMsg('')
    try {
      await api('/api/v1/storeops/pto-accrual-config', {
        method: 'PUT',
        body: JSON.stringify({ scope: 'org', enabled: cfg.enabled, accrual_rate: Number(cfg.accrual_rate),
          mode: cfg.mode, max_accrual_hours: cfg.max_accrual_hours === '' || cfg.max_accrual_hours === null ? null : Number(cfg.max_accrual_hours),
          hours_per_pto_day: Number(cfg.hours_per_pto_day) }),
      })
      setMsg('✅ Saved. This applies org-wide unless a role/employee override exists.')
      loadView()
    } catch (e: any) { setMsg('❌ ' + (e?.message || 'Save failed')) }
    finally { setSaving(false) }
  }

  async function runNow() {
    setRunning(true); setMsg('')
    try {
      const r = await api(`/api/v1/storeops/pto-accrual/run/${month}`, { method: 'POST' })
      setRunResult(r)
      setView(r)
      const push = r.push || {}
      setMsg(push.pushed ? '✅ Ran — pushed to Store Expenses.'
        : `✅ Ran — ledger saved (${r.ledger_rows_written} rows). Expense push not applied yet: ${push.note || 'unknown reason'}`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || 'Run failed')) }
    finally { setRunning(false) }
  }

  const totalCost = (view?.stores || []).reduce((s, r) => s + (r.cost || 0), 0)
  const totalAccrued = (view?.stores || []).reduce((s, r) => s + (r.accrued_hours || 0), 0)

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }}
           onClick={() => setOpen(o => !o)}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>🏖️ Paid Leave Accumulated (PTO accrual)</div>
        <div style={{ fontSize: 12, color: 'var(--text3)' }}>{open ? '▲ collapse' : '▼ configure / run'}</div>
      </div>

      {open && (
        <div style={{ marginTop: 14 }}>
          <p style={{ fontSize: 12, color: 'var(--text2)', marginTop: 0 }}>
            Computes a PTO accrual cost per store for this pay period and hands it to Store Expenses as an
            ADDITIVE "Paid Leave Accumulated" line — it never changes any wage already computed. Config here
            is the org default; role/employee overrides can be layered via the API (per-role/per-employee
            editor coming later — see docs/handoffs/people.md).
          </p>

          {cfg && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10, alignItems: 'end', marginBottom: 10 }}>
              <label style={{ fontSize: 12 }}>
                <div style={{ marginBottom: 4, color: 'var(--text2)' }}>Enabled</div>
                <input type="checkbox" checked={!!cfg.enabled} onChange={e => setCfg({ ...cfg, enabled: e.target.checked })} />
              </label>
              <label style={{ fontSize: 12 }}>
                <div style={{ marginBottom: 4, color: 'var(--text2)' }}>Accrual rate (hrs/hr worked)</div>
                <input className="input" type="number" step="0.0001" value={cfg.accrual_rate ?? ''}
                       onChange={e => setCfg({ ...cfg, accrual_rate: e.target.value })} />
              </label>
              <label style={{ fontSize: 12 }}>
                <div style={{ marginBottom: 4, color: 'var(--text2)' }}>Mode</div>
                <select className="select" value={cfg.mode} onChange={e => setCfg({ ...cfg, mode: e.target.value })}>
                  <option value="accrue">Accrue (book as earned)</option>
                  <option value="on_use">On use (book when taken)</option>
                </select>
              </label>
              <label style={{ fontSize: 12 }}>
                <div style={{ marginBottom: 4, color: 'var(--text2)' }}>Cap (hrs, blank = none)</div>
                <input className="input" type="number" value={cfg.max_accrual_hours ?? ''}
                       onChange={e => setCfg({ ...cfg, max_accrual_hours: e.target.value })} />
              </label>
              <label style={{ fontSize: 12 }}>
                <div style={{ marginBottom: 4, color: 'var(--text2)' }}>Hrs / PTO day</div>
                <input className="input" type="number" value={cfg.hours_per_pto_day ?? ''}
                       onChange={e => setCfg({ ...cfg, hours_per_pto_day: e.target.value })} />
              </label>
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            <button className="btn btn-secondary" disabled={saving} onClick={saveConfig}>
              {saving ? 'Saving…' : 'Save Config'}
            </button>
            <button className="btn btn-primary" disabled={running} onClick={runNow}
                    title="Computes this month's accrual, saves the ledger, and pushes the per-store cost to Store Expenses">
              {running ? 'Running…' : `▶ Run PTO Accrual for ${month}`}
            </button>
          </div>

          {msg && <div style={{ fontSize: 12, marginBottom: 10 }}>{msg}</div>}

          {view && (
            <>
              <div style={{ display: 'flex', gap: 20, fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
                <div>Org mode: <b>{view.mode}</b></div>
                <div>Org rate: <b>{view.rate}</b> hrs/hr</div>
                <div>Total accrued this period: <b>{totalAccrued.toFixed(1)} hrs</b></div>
                <div>Total cost this period: <b>{fmt(totalCost)}</b></div>
                {view.last_run_at && <div>Last run: <b>{new Date(view.last_run_at).toLocaleString()}</b></div>}
              </div>
              <table className="table" style={{ fontSize: 12 }}>
                <thead><tr><th>Store</th><th>Accrued Hrs</th><th>Taken Hrs</th><th>Cost</th></tr></thead>
                <tbody>
                  {(view.stores || []).map(s => (
                    <tr key={s.store}>
                      <td>{s.store}</td>
                      <td>{s.accrued_hours.toFixed(2)}</td>
                      <td>{s.taken_hours.toFixed(2)}</td>
                      <td>{fmt(s.cost)}</td>
                    </tr>
                  ))}
                  {(!view.stores || view.stores.length === 0) && (
                    <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--text3)' }}>No worked hours or PTO taken for {month} yet.</td></tr>
                  )}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </div>
  )
}
