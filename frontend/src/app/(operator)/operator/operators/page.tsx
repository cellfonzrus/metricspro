'use client'
// OPERATORS — who holds platform authority, with what scope, until when. And the CUTOVER.
//
// THIS PAGE IS THE SEPARATION, made operable. It shows the two sources of platform authority side by
// side so the owner can see exactly what would happen before anything happens:
//   · REGISTRY  — `core.platform_operator` rows: an identity keyed by auth id, belonging to NO
//                 tenant. This is the separated model.
//   · LEGACY    — every `storeops.app_users.super_admin` login: authority riding on a column of
//                 somebody's employment record. This is what we are moving away from.
//
// ★ THE CUTOVER IS THE ONLY DANGEROUS CONTROL IN THIS WHOLE CHANGE, AND IT IS DELIBERATELY MANUAL. ★
// Switching the legacy flag off is what finally separates the personas, and it is the one action
// that could lock the owner out of their own platform. So:
//   · deploying this code NEVER performs it (migration 980 ships the statement commented out);
//   · migration 980 SEEDS a platform-operator record for every existing super-admin first, so by the
//     time the button is reachable there is normally nobody left to lock out;
//   · the server REFUSES the flip while zero active operator records exist
//     (`operator.policy_change_decision`, proven in harness_operator_console.py §A7);
//   · with exactly one operator it is allowed but says so loudly — one record is a single point of
//     failure for the entire platform;
//   · it is reversible with the same control, and the page says so before you press anything.
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { OPS, can, fmtWhen } from '@/lib/operator'
import { useOperator } from '@/lib/operator-context'
import { H1, Panel, Table, td, Btn, Field, inputStyle, Err, Empty, Note, Lamp, Row } from '@/lib/operator-ui'

export default function OperatorsPage() {
  const me = useOperator()
  const [data, setData] = useState<any>(null)
  const [policy, setPolicy] = useState<any>(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [f, setF] = useState({ email: '', operator_role: 'support', expires_at: '', notes: '' })
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api('/api/v1/core/operator/roster').then(setData).catch(e => setErr(e?.message || 'Could not load'))
    if (can(me, 'policy.write')) api('/api/v1/core/operator/policy').then(setPolicy).catch(() => {})
  }, [me])
  useEffect(() => { if (me) load() }, [me, load])

  async function grant() {
    setBusy(true); setErr(''); setMsg('')
    try {
      await api('/api/v1/core/operator/roster', { method: 'POST', body: JSON.stringify(f) })
      setF({ email: '', operator_role: 'support', expires_at: '', notes: '' })
      setMsg('Operator record saved.'); load()
    } catch (e: any) { setErr(e?.message || 'Could not grant') } finally { setBusy(false) }
  }

  async function revoke(email: string) {
    if (!confirm(`Remove platform authority from ${email}?`)) return
    setErr(''); setMsg('')
    try {
      await api(`/api/v1/core/operator/roster?email=${encodeURIComponent(email)}`, { method: 'DELETE' })
      setMsg(`${email} is no longer a platform operator.`); load()
    } catch (e: any) { setErr(e?.message || 'Could not revoke') }
  }

  async function setLegacy(honored: boolean) {
    const warn = honored
      ? 'Re-enable the legacy tenant super-admin flag as a source of platform authority?'
      : 'Stop honouring the tenant super-admin flag?\n\nAfter this, ONLY a platform-operator record '
        + 'gets anyone into the console or any super-admin endpoint.\n\nThis is reversible from this '
        + 'same page.'
    if (!confirm(warn)) return
    setErr(''); setMsg('')
    try {
      const r: any = await api('/api/v1/core/operator/policy', {
        method: 'POST', body: JSON.stringify({ legacy_membership_flag_honored: honored }),
      })
      setMsg(r.message || 'Policy updated.'); load()
    } catch (e: any) { setErr(e?.message || 'Could not change the policy') }
  }

  if (!me) return null
  const orphans: any[] = data?.would_lose_access_at_cutover || []
  const registry: any[] = data?.registry || []
  const legacy: any[] = data?.legacy_super_admins || []
  const activeCount = registry.filter(r => r.active).length

  return (
    <div style={{ maxWidth: 1120 }}>
      <H1 sub={<>Platform authority — separate from any company. A person can operate the platform
        without being anybody’s employee, and can be an employee without operating the platform.</>}>
        Operators
      </H1>

      <Err>{err}</Err>
      {msg && <Note>{msg}</Note>}

      {/* ── THE CUTOVER PANEL ─────────────────────────────────────────────────────────────────── */}
      {can(me, 'policy.write') && policy && (
        <Panel title="Where platform authority comes from">
          <Row label="Tenant super-admin flag honored"
            value={policy.policy?.legacy_membership_flag_honored
              ? 'yes — authority still rides on tenant memberships'
              : 'no — separated: only platform-operator records grant authority'}
            warn={!!policy.policy?.legacy_membership_flag_honored} />
          <Row label="Active platform-operator records" value={String(activeCount)} />
          {orphans.length > 0 && (
            <Note tone="warn">
              <b>{orphans.length} login{orphans.length > 1 ? 's' : ''} would lose access</b> if you
              separated now: {orphans.map(o => o.email).join(', ')}. Give each one a platform-operator
              record below first.
            </Note>
          )}
          {policy.policy?.legacy_membership_flag_honored ? (
            <div style={{ marginTop: 12 }}>
              <Btn onClick={() => setLegacy(false)} disabled={!policy.cutover_allowed}>
                Separate now — stop honouring the tenant flag
              </Btn>
              <div style={{ color: OPS.text3, fontSize: 11.8, marginTop: 7, lineHeight: 1.6 }}>
                {policy.cutover_allowed
                  ? (policy.cutover_note || 'Reversible from this page at any time.')
                  : policy.cutover_note}
              </div>
            </div>
          ) : (
            <div style={{ marginTop: 12 }}>
              <Btn tone="ghost" onClick={() => setLegacy(true)}>
                Undo — honour the tenant super-admin flag again
              </Btn>
            </div>
          )}
        </Panel>
      )}

      {/* ── THE REGISTRY ──────────────────────────────────────────────────────────────────────── */}
      <Panel title="Platform operator records">
        <Table head={['Operator', 'Role', 'Expires', 'Granted by', 'Capabilities', '']}>
          {registry.map(r => (
            <tr key={r.auth_id}>
              <td style={td}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Lamp lamp={r.active ? 'green' : 'unmonitored'} />
                  <span>{r.email || r.auth_id}</span>
                </div>
              </td>
              <td style={{ ...td, color: OPS.accent }}>{r.operator_role}</td>
              <td style={{ ...td, color: OPS.text3 }}>{r.expires_at ? fmtWhen(r.expires_at) : 'standing'}</td>
              <td style={{ ...td, color: OPS.text3 }}>{r.granted_by_email || '—'}</td>
              <td style={{ ...td, color: OPS.text2, fontSize: 11.4 }}>
                {(r.capabilities || []).join(', ')}</td>
              <td style={{ ...td, textAlign: 'right' }}>
                {can(me, 'operator.write') && r.active &&
                  <Btn tone="danger" onClick={() => revoke(r.email)}>Revoke</Btn>}
              </td>
            </tr>
          ))}
        </Table>
        {registry.length === 0 && (
          <Empty>No platform-operator records yet. Migration 980 seeds one for every existing
            super-admin; if this is empty, that migration has not run.</Empty>
        )}
      </Panel>

      {can(me, 'operator.write') && (
        <Panel title="Grant platform authority">
          <Note>
            This never creates a login and never changes anyone’s tenant membership — the person must
            already have an account. Scope them as narrowly as the job allows, and use an expiry for
            anything temporary.
          </Note>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '12px 0' }}>
            <Field label="Email of an existing login">
              <input value={f.email} onChange={e => setF({ ...f, email: e.target.value })} style={inputStyle} />
            </Field>
            <Field label="Operator role" hint={roleHint(f.operator_role, data)}>
              <select value={f.operator_role} onChange={e => setF({ ...f, operator_role: e.target.value })}
                style={inputStyle}>
                {Object.keys(data?.roles || { owner: 1, support: 1, billing: 1, engineering: 1, readonly: 1 })
                  .map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </Field>
            <Field label="Expires" hint="Blank = standing. Set it for just-in-time access.">
              <input type="datetime-local" value={f.expires_at}
                onChange={e => setF({ ...f, expires_at: e.target.value })} style={inputStyle} />
            </Field>
            <Field label="Note">
              <input value={f.notes} onChange={e => setF({ ...f, notes: e.target.value })} style={inputStyle}
                placeholder="why they need it" />
            </Field>
          </div>
          <Btn onClick={grant} disabled={busy || !f.email.trim()}>{busy ? 'Saving…' : 'Grant'}</Btn>
        </Panel>
      )}

      {/* ── THE LEGACY SIDE, shown honestly ───────────────────────────────────────────────────── */}
      <Panel title="Logins whose platform access still comes from a tenant membership flag">
        <Table head={['Login', 'Home company', 'Role', 'Last login', 'Separated?']}>
          {legacy.map(l => (
            <tr key={l.email + l.org_id}>
              <td style={td}>{l.email}</td>
              <td style={{ ...td, color: OPS.text3, fontSize: 11.4 }}>{l.org_id}</td>
              <td style={{ ...td, color: OPS.text2 }}>{l.role}</td>
              <td style={{ ...td, color: OPS.text3 }}>{fmtWhen(l.last_login)}</td>
              <td style={td}>
                {l.has_registry_record
                  ? <span style={{ color: OPS.good }}>yes — has an operator record</span>
                  : <span style={{ color: OPS.warn }}>no — would lose access at cutover</span>}
              </td>
            </tr>
          ))}
        </Table>
        {legacy.length === 0 && <Empty>None.</Empty>}
      </Panel>
    </div>
  )
}

function roleHint(role: string, data: any): string {
  const caps: string[] = data?.roles?.[role] || []
  return caps.length ? caps.join(', ') : 'what this role may do'
}
