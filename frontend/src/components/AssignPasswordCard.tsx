'use client'
// ── "ASSIGN THIS PERSON A PASSWORD I TYPE" — ONE home, dereferenced (owner 2026-10-14, §CORE-09) ──
//
// OWNER: *"provide a link to reset the password for the employee login on payroll Dashboard … in
// both config and this new link to password reset, the admin should be able to assign a manual
// password also"*.
//
// THE CLASS, not the instance. The CAPABILITY already existed and had exactly one server path —
// `POST /api/v1/core/users/set-password` (`core/router.py::admin_set_password`), which validates
// against the tenant password policy, flips `must_reset_password`, and audits
// `password_admin_set`. What did not exist was a DOOR to it anywhere a person actually manages
// employees: its only UI was a card at the bottom of Admin → Security Settings, a page tagged
// `module: 'admin'` that an HR/market manager never sees. So the owner asked for the same thing in
// two more places, and the patchwork answer would be to retype the fetch + the policy echo + the
// picker on each one — three copies of one question, which is the duplicate defect CLAUDE.md
// forbids.
//
// ONE FACT, ONE HOME. This component IS "assign a password to an employee login", for every
// surface that offers it. Three consumers dereference it and none holds a copy:
//
//   · /admin/security                  — Security Settings (where it used to be written inline)
//   · /admin/roles  (👥 Assign people) — "in config": where logins are provisioned/resent/revealed
//   · /hr/employee-password            — the Payroll hub's "Reset an employee's password" door
//
// LOCKED: backend/harness_assign_password_one_home.py fails the build if a second surface calls
// `users/set-password`, retypes the policy echo, or renders its own picker for this — and if any of
// the three consumers stops rendering this component.
//
// THE SERVER IS THE AUTHORITY, not this card. `admin_set_password` gates on
// `_can_edit_setting(caller, 'security')` (per-role grantable at `permissions.settings.security`;
// a full-scope admin holds it by default) and `validate_password` re-checks the policy server-side.
// `can_edit` below only decides whether to SHOW the form, exactly as Security Settings already did:
// putting this card on an `hr`-module page therefore widens nobody's access — an HR manager without
// the Security grant sees the read-only notice, and a forged POST is refused 403.
import { useCallback, useEffect, useState } from 'react'
import { api, ORG_ID } from '@/lib/client'
import EntityPicker, { type EntityOption } from '@/components/EntityPicker'

export type PasswordPolicy = {
  min_length?: number; max_length?: number; require_upper?: boolean
  require_lower?: boolean; require_digit?: boolean; require_special?: boolean
} | null

/** Client ECHO of the server password policy (`core/auth_security.validate_password` is
 *  authoritative — this only spares the admin a round trip). [] = nothing to fix. ONE copy. */
export function pwErrors(pw: string, p: PasswordPolicy): string[] {
  const e: string[] = []
  if (pw.length > 128) return ['Password must be at most 128 characters.']
  if (!p) return e
  if (p.min_length && pw.length < p.min_length) e.push(`Use at least ${p.min_length} characters.`)
  if (p.max_length && pw.length > p.max_length) e.push(`Use at most ${p.max_length} characters.`)
  if (p.require_upper && !/[A-Z]/.test(pw)) e.push('Add an uppercase letter.')
  if (p.require_lower && !/[a-z]/.test(pw)) e.push('Add a lowercase letter.')
  if (p.require_digit && !/[0-9]/.test(pw)) e.push('Add a number.')
  if (p.require_special && !/[!@#$%^&*()\-_=+[\]{};:,.?/]/.test(pw)) e.push('Add a special character.')
  return e
}

export type AssignPasswordCardProps = {
  /** The tenant policy, when the host page already loaded it (avoids a second fetch). */
  policy?: PasswordPolicy
  /** The login roster to pick from, when the host page already has it. */
  users?: EntityOption[]
  /** Whether this caller may edit the 'security' setting, when the host already asked. */
  canEdit?: boolean
  /** Heading + blurb, for a page that already titles itself. */
  title?: string
  /** Suppress the card chrome (the host is already a card). */
  bare?: boolean
}

export default function AssignPasswordCard({ policy, users, canEdit, title, bare }: AssignPasswordCardProps) {
  // Self-load anything the host did not hand over, so a new surface needs ONE line to adopt this.
  // The host's props are DERIVED FROM, never copied into state: a `useEffect` that mirrors a prop
  // into state is a second source of truth for the same fact (and `react-hooks/set-state-in-effect`
  // refuses it) — exactly the duplication this component exists to remove.
  const [fetched, setFetched] = useState<{ pol: PasswordPolicy; may: boolean } | null>(null)
  const [fetchedUsers, setFetchedUsers] = useState<EntityOption[] | null>(null)
  const [email, setEmail] = useState<string | null>(null)
  const [pw, setPw] = useState('')
  const [requireChange, setRequireChange] = useState(true)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const needSettings = policy === undefined || canEdit === undefined
  useEffect(() => {
    if (!needSettings) return
    let live = true
    api('/api/v1/core/security-settings')
      .then(s => { if (live) setFetched({ pol: s?.password_policy || null, may: !!s?.can_edit }) })
      .catch(() => { if (live) setFetched({ pol: null, may: false }) })
    return () => { live = false }
  }, [needSettings])
  useEffect(() => {
    if (users) return
    let live = true
    api(`/api/v1/core/users?org_id=${ORG_ID}`)
      .then(u => {
        if (!live) return
        setFetchedUsers(((u?.users || []) as Record<string, string>[])
          .filter(x => x.email)
          .map(x => ({ id: x.email, label: x.full_name || x.email, sublabel: x.email })))
      })
      .catch(() => { if (live) setFetchedUsers([]) })
    return () => { live = false }
  }, [users])

  const pol: PasswordPolicy = policy !== undefined ? policy : (fetched ? fetched.pol : null)
  // null = "not answered yet". The form is never rendered on a guess.
  const may: boolean | null = canEdit !== undefined ? canEdit : (fetched ? fetched.may : null)
  const roster: EntityOption[] = users || fetchedUsers || []

  const assign = useCallback(async () => {
    if (!email) { setMsg('Pick an employee.'); return }
    const errs = pwErrors(pw, pol)
    if (errs.length) { setMsg(errs.join(' ')); return }
    setBusy(true); setMsg('')
    try {
      const r = await api('/api/v1/core/users/set-password', { method: 'POST',
        body: JSON.stringify({ email, password: pw, require_change: requireChange }) })
      setMsg(r?.note || `Password set for ${email}. Hand it over securely.`)
      setPw('')
    } catch (e) { setMsg('Failed: ' + ((e as Error)?.message || String(e))) }
    finally { setBusy(false) }
  }, [email, pw, pol, requireChange])

  if (may === null) return null                 // still asking the server; show nothing rather than a form
  const errs = pw ? pwErrors(pw, pol) : []
  const head = title || 'Set an employee’s password'
  const body = !may ? (
    <div style={{ fontSize: 12.5, color: 'var(--c-fg-b45309)' }}>
      You don&apos;t have permission to set passwords. Ask an administrator to grant you the
      <b> Security</b> setting, or to set it for you.
    </div>
  ) : (
    <>
      <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>
        Type a password for anyone in this company and hand it to them. It must pass this company&apos;s
        password rules; they&apos;ll be asked to change it the first time they sign in, unless you turn that off.
      </div>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div style={{ minWidth: 240 }}>
          <EntityPicker options={roster} value={email} onChange={setEmail} placeholder="Pick an employee…" />
        </div>
        <div>
          <input type="text" value={pw} onChange={e => setPw(e.target.value)} placeholder="New password"
            style={{ padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 14, width: 200 }} />
          {errs.length > 0 && (
            <ul style={{ margin: '6px 0 0', paddingLeft: 16, fontSize: 11, color: 'var(--c-fg-b45309)' }}>
              {errs.map((x, i) => <li key={i}>{x}</li>)}
            </ul>
          )}
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, marginTop: 8 }}>
          <input type="checkbox" checked={requireChange} onChange={e => setRequireChange(e.target.checked)} />
          Require change on next login
        </label>
        <button className="btn btn-primary" style={{ marginTop: 4 }} disabled={busy} onClick={assign}>
          {busy ? 'Setting…' : 'Set password'}
        </button>
      </div>
      {msg && <div style={{ marginTop: 10, fontSize: 13, color: msg.startsWith('Failed') ? 'var(--c-fg-dc2626)' : 'var(--c-fg-059669)' }}>{msg}</div>}
    </>
  )

  return (
    <div className={bare ? undefined : 'card'} style={bare ? undefined : { padding: 16 }}>
      <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>{head}</div>
      {body}
    </div>
  )
}
