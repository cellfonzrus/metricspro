'use client'
// Page/tab-level DATA_GRANT gate for the FOUR DEFAULT-CLOSED What-If reports (owner directive
// 2026-08-03: "what if also has 4 reports all of which need to be gated for permissions").
//
// FRONTEND MIRROR ONLY — the backend (`commcalc/whatif_gates.py`, enforced at the top of all four
// /whatif/* endpoints) is the source of truth. Same arrangement the Device Cost Recon page and the
// two finance report gates use: `hasDataGrant` hint + backend 403 → lock note.
//
// `hasDataGrant` comes from the SHARED `@/lib/rbac` (READ, never edited — AGENT_CONTRACT §1). It is
// deliberately LENIENT while permissions are still loading (an empty perms object has no `scope`, so it
// defaults to 'all' and passes). That is why this page ALSO asks the backend directly via
// GET /whatif/access — four booleans from ONE server-side caller resolution — and treats a 403 as
// "restricted". Server decides; this only keeps the pre-response UI honest.
//
// The four grant keys are registered in rbac.ts's DATA_GRANTS registry by core (filed under
// ## NEEDS CORE in docs/handoffs/commission.md). Until that lands the gate still works — it reads the
// role's own permissions JSONB; the registry only makes the keys tickable in the Roles UI.
import { useEffect, useState } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { hasDataGrant } from '@/lib/rbac'
import { useAuth } from '@/lib/auth-context'

export const WHATIF_GRANTS = {
  mix: 'whatif_employee_payout',
  byod: 'whatif_byod_residual',
  corr: 'whatif_accessory_corr',
  carrier: 'whatif_carrier_income',
} as const

export type WhatIfTabKey = keyof typeof WHATIF_GRANTS

// Does a thrown api() error mean "you are not allowed" (vs. a real failure)? api() throws
// new Error(detail), so the backend's 403 detail text is what we see — and whatif_gates.py puts the
// literal grant key in that detail on purpose. Same shape as the finance/closing forbidden checks.
export const isForbidden = (e: any) =>
  /permission|403|restricted|forbidden|whatif_/i.test(String(e?.message || e || ''))

/** Server-authoritative "which of the four may I open?", with the optimistic client mirror as the
 *  pre-response fallback so a granted user never flashes the lock note.
 *  `ready` is false until the server answers (or definitively fails) — callers must not decide, and
 *  must not fetch a report, before then. */
export function useWhatIfAccess(): { allowed: Record<string, boolean>; ready: boolean } {
  const { permissions, loading } = useAuth()
  const [server, setServer] = useState<Record<string, boolean> | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    let alive = true
    api(`/api/v1/commcalc/whatif/access?org_id=${ORG_ID}`)
      .then((d: any) => { if (alive) setServer(d?.allowed || {}) })
      .catch(() => { if (alive) setServer(null) })
      .finally(() => { if (alive) setDone(true) })
    return () => { alive = false }
  }, [])

  // Fallback (access endpoint unreachable / older backend): the optimistic client mirror. It can only
  // ever SHOW a tab whose own endpoint will still 403 — never hide one the server would have allowed.
  const mirror: Record<string, boolean> = {}
  for (const k of Object.values(WHATIF_GRANTS)) mirror[k] = hasDataGrant(permissions, k)
  return { allowed: server || mirror, ready: done && !loading }
}

/** What a caller without the grant sees INSTEAD of the report (no data, no filters, no exports). */
export function RestrictedWhatIf({ title, grantKey }: { title: string; grantKey: string }) {
  return (
    <div className="card" style={{ padding: 24, marginTop: 16, borderLeft: '3px solid #dc2626', maxWidth: 660 }}>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>🔒 Restricted</div>
      <p style={{ color: 'var(--text2)', fontSize: 13, margin: 0 }}>
        <b>{title}</b> is restricted — ask an admin to grant it on your role.
      </p>
      <p style={{ color: 'var(--text3)', fontSize: 12, margin: '8px 0 0' }}>
        Permission needed: <code>{grantKey}</code> (admin-only by default).
      </p>
    </div>
  )
}
