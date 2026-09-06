'use client'
// COMPANIES — the tenant directory, and the audited way in to any of them.
//
// OWNER DIRECTIVE 2026-09-05: "the option for the super admin to log in to any tenant from it is
// list of tenants dashboard an option to log in from there".
//
// WHAT ALREADY EXISTED, AND WHAT IS ACTUALLY NEW (CLAUDE.md duplicate gate).
// A platform super-admin could ALREADY act as any tenant before this page existed: the header
// switcher in the tenant app wrote the chosen org to localStorage and reloaded, the client sent it
// as `x-active-org`, and `tenant_middleware`'s super-admin branch honoured it WITHOUT rewriting.
// That is a real, working "log in to any tenant" — with no reason captured, no time limit, no
// banner, and no record that it ever happened.
//
// So this page does NOT build a second way in. It reuses that exact mechanism, and adds the four
// things it was missing, all of which the "view as employee" feature has had since mig 730:
//   · AUDITED       — POST /core/operator/enter writes a hash-chained core.operator_action row and a
//                     core.operator_entry_session row, under the OPERATOR's own auth id and email;
//   · ATTRIBUTABLE  — never anonymised behind the tenant; the tenant's own admins can read the same
//                     record at GET /core/tenant-operator-access;
//   · TIME-BOXED    — a hard expiry the server chose (the client's requested duration is clamped);
//   · VISIBLE       — a persistent banner renders in the tenant app for as long as the session lives.
//
// AND IT IS NOT AN ESCALATION. Entering a company shows it to you AS YOURSELF. It does not confer
// the DEFAULT-DENY `impersonate` permission ("view as employee"), which has no super-admin bypass
// and is granted only per-role at /admin/roles. `harness_operator_console.py` §B fails if that ever
// stops being true.
import { useCallback, useEffect, useState } from 'react'
import { api, setActiveOrg } from '@/lib/client'
import { OPS, can, fmtWhen } from '@/lib/operator'
import { useOperator } from '@/lib/operator-context'
import { H1, Panel, Table, td, Btn, Field, inputStyle, Err, Empty, Note, Lamp } from '@/lib/operator-ui'

type Tenant = {
  org_id: string; name: string; slug: string | null; is_active: boolean
  created_at: string; users: number; logins: number; plan_status?: string | null
}

export default function OperatorTenants() {
  const me = useOperator()
  const [tenants, setTenants] = useState<Tenant[]>([])
  const [err, setErr] = useState('')
  const [pick, setPick] = useState<Tenant | null>(null)   // the tenant being entered
  const [reason, setReason] = useState('')
  const [minutes, setMinutes] = useState(30)
  const [busy, setBusy] = useState(false)
  const [log, setLog] = useState<any[]>([])
  const [drill, setDrill] = useState<any>(null)

  // The directory is `GET /core/tenants` — the SAME endpoint /admin/tenants has always used. No
  // second tenant list, no second count, nothing to drift.
  const load = useCallback(() => {
    api('/api/v1/core/tenants').then((d: any) => setTenants(d.tenants || []))
      .catch(e => setErr(e?.message || 'Could not load companies'))
  }, [])

  useEffect(() => {
    if (!me) return
    load()
    if (can(me, 'audit.read')) {
      api('/api/v1/core/operator/entry-log?limit=25').then((d: any) => setLog(d.rows || [])).catch(() => {})
    }
    if (can(me, 'control_box.read')) {
      api('/api/v1/core/operator/restore-drill').then(setDrill).catch(() => {})
    }
  }, [me, load])

  const policy = me?.policy || {}
  const reasonRequired = policy.entry_reason_required !== false
  const maxMinutes = Number(policy.entry_max_minutes || 60)

  async function enter() {
    if (!pick) return
    setBusy(true); setErr('')
    try {
      const r: any = await api('/api/v1/core/operator/enter', {
        method: 'POST',
        body: JSON.stringify({ org_id: pick.org_id, reason, minutes }),
      })
      // THE ENTRY ITSELF: set the acting tenant exactly as the switcher always has, then hand the
      // operator into the tenant application. The server has already written the session and the
      // sealed audit row; this line only tells the client which org to send from now on.
      setActiveOrg(r.active_org)
      window.location.href = '/commcalc'
    } catch (e: any) {
      setErr(e?.message || 'Could not open the session'); setBusy(false)
    }
  }

  if (!me) return null

  return (
    <div style={{ maxWidth: 1120 }}>
      <H1 sub={<>Every company on the platform. Entering one shows it to you <b>as yourself</b>, with a
        reason on the record and a hard time limit — it is not “view as employee”.</>}>Companies</H1>

      <Err>{err}</Err>

      <Panel title={`${tenants.length} companies`} right={<Btn tone="ghost" onClick={load}>Refresh</Btn>}>
        <Table head={['Company', 'Users', 'Logins', 'Plan', 'Created', '']}>
          {tenants.map(t => (
            <tr key={t.org_id}>
              <td style={td}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Lamp lamp={t.is_active === false ? 'unmonitored' : 'green'} />
                  <div>
                    <div style={{ fontWeight: 600 }}>{t.name}</div>
                    <div style={{ color: OPS.text3, fontSize: 11.2 }}>{t.slug || t.org_id}</div>
                  </div>
                </div>
              </td>
              <td style={td}>{t.users ?? '—'}</td>
              <td style={td}>{t.logins ?? '—'}</td>
              <td style={{ ...td, color: OPS.text2 }}>{t.plan_status || '—'}</td>
              <td style={{ ...td, color: OPS.text3, whiteSpace: 'nowrap' }}>{fmtWhen(t.created_at)}</td>
              <td style={{ ...td, textAlign: 'right', whiteSpace: 'nowrap' }}>
                {can(me, 'tenant.enter')
                  ? <Btn onClick={() => { setPick(t); setReason(''); setMinutes(Math.min(30, maxMinutes)) }}>
                      Enter →
                    </Btn>
                  : <span style={{ color: OPS.text3, fontSize: 11.5 }}>no entry rights</span>}
              </td>
            </tr>
          ))}
        </Table>
        {tenants.length === 0 && <Empty>No companies yet.</Empty>}
      </Panel>

      {/* ── THE ENTRY DIALOG. Reason + duration, both server-enforced. The copy states plainly what
             the operator is about to do and what will be recorded, because an operator who is
             surprised by the audit trail is an operator who will resent it. */}
      {pick && (
        <Panel title={`Enter ${pick.name}`}>
          <Note>
            You will see this company as <b>{me.email}</b>. Everything you do is logged under your own
            account, {pick.name}’s own administrators can see that you were here, and a banner stays on
            screen until you leave. This does <b>not</b> sign you in as one of their employees.
          </Note>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '12px 0' }}>
            <Field label={`Reason${reasonRequired ? '' : ' (optional)'}`}
              hint="Written to the tenant-entry log, and shown to you while you are in.">
              <input value={reason} onChange={e => setReason(e.target.value)} style={inputStyle}
                placeholder="e.g. investigating a commission discrepancy they reported" />
            </Field>
            <Field label="Minutes" hint={`Server maximum ${maxMinutes}. It expires by itself.`}>
              <input type="number" min={policy.entry_min_minutes || 5} max={maxMinutes} value={minutes}
                onChange={e => setMinutes(Number(e.target.value) || 30)} style={inputStyle} />
            </Field>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Btn onClick={enter} disabled={busy || (reasonRequired && reason.trim().length < 6)}>
              {busy ? 'Opening…' : `Enter ${pick.name}`}
            </Btn>
            <Btn tone="ghost" onClick={() => setPick(null)}>Cancel</Btn>
          </div>
        </Panel>
      )}

      {/* ── BACKUP RESTORE DRILL. §20 of the index declares this UNMONITORED — a real, known gap in
             the control box. It is not observable from the backend, but it IS attestable, so this is
             where a drill gets recorded. */}
      {can(me, 'control_box.read') && (
        <RestoreDrill drill={drill} onSaved={() =>
          api('/api/v1/core/operator/restore-drill').then(setDrill).catch(() => {})} />
      )}

      {can(me, 'audit.read') && (
        <Panel title="Recent tenant entries">
          <Table head={['When', 'Operator', 'Company', 'Reason', 'State']}>
            {log.map((r, i) => (
              <tr key={r.id || i}>
                <td style={{ ...td, color: OPS.text3, whiteSpace: 'nowrap' }}>{fmtWhen(r.started_at)}</td>
                <td style={td}>{r.actor_email}</td>
                <td style={{ ...td, color: OPS.text2 }}>
                  {tenants.find(t => t.org_id === r.org_id)?.name || r.org_id}</td>
                <td style={{ ...td, color: OPS.text2 }}>{r.reason || '—'}</td>
                <td style={td}>{r.state}</td>
              </tr>
            ))}
          </Table>
          {log.length === 0 && <Empty>No operator has entered a company yet.</Empty>}
        </Panel>
      )}
    </div>
  )
}

// ── Restore-drill attestation ────────────────────────────────────────────────────────────────────
// An untested backup is not a backup. The lamp is RED until a drill is recorded, and never green for
// a failed or stale one — the same honesty rule §20 applies to every other lamp on the board.
function RestoreDrill({ drill, onSaved }: { drill: any; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ outcome: 'passed', scope: '', performed_at: '', notes: '' })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true); setErr('')
    try {
      await api('/api/v1/core/operator/restore-drill', {
        method: 'POST',
        body: JSON.stringify({ ...f, performed_at: f.performed_at || new Date().toISOString() }),
      })
      setOpen(false); setF({ outcome: 'passed', scope: '', performed_at: '', notes: '' }); onSaved()
    } catch (e: any) { setErr(e?.message || 'Could not record the drill') } finally { setBusy(false) }
  }

  return (
    <Panel title="Backup restore drill"
      right={<Btn tone="ghost" onClick={() => setOpen(o => !o)}>{open ? 'Close' : 'Record a drill'}</Btn>}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 13 }}>
        <Lamp lamp={drill?.lamp} size={10} />
        <span style={{ color: OPS.text2 }}>{drill?.reason || 'not yet recorded'}</span>
      </div>
      <Note>
        The System Control Box declares this <b>unmonitored</b> today: backup health is not observable
        from the backend. Recording a drill here gives it something honest to read — and migration 981
        carries the one-line, commented-out change that turns that grey lamp into a real one.
      </Note>
      {open && (
        <>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '12px 0' }}>
            <Field label="Outcome">
              <select value={f.outcome} onChange={e => setF({ ...f, outcome: e.target.value })} style={inputStyle}>
                <option value="passed">passed</option>
                <option value="partial">partial</option>
                <option value="failed">failed</option>
              </select>
            </Field>
            <Field label="What was restored" hint="e.g. full cluster to a staging project">
              <input value={f.scope} onChange={e => setF({ ...f, scope: e.target.value })} style={inputStyle} />
            </Field>
            <Field label="When" hint="Blank = now">
              <input type="datetime-local" value={f.performed_at}
                onChange={e => setF({ ...f, performed_at: e.target.value })} style={inputStyle} />
            </Field>
          </div>
          <Err>{err}</Err>
          <Btn onClick={save} disabled={busy || f.scope.trim().length < 3}>
            {busy ? 'Saving…' : 'Record drill'}</Btn>
        </>
      )}
    </Panel>
  )
}
