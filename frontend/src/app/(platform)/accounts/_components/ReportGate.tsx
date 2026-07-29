'use client'
// Page-level DATA_GRANT gate for the two DEFAULT-CLOSED finance reports (owner directive 2026-07-29):
// Residual per Subscriber + Trends (all metrics). Frontend MIRROR only — the backend
// (`account/report_gates.py`, enforced on GET /account/residual-per-sub) is the source of truth, the
// same arrangement the Device History money table uses (`hasDataGrant` hint + backend 403/lock note).
//
// `hasDataGrant` comes from the SHARED `@/lib/rbac` (READ, never edited — AGENT_CONTRACT §1). It is
// deliberately LENIENT while permissions are still loading (an empty perms object has no `scope`, so it
// defaults to 'all' and passes); that is why the pages ALSO treat a backend 403 as "restricted" — the
// server decides, this only keeps the pre-response UI honest and avoids a pointless request.
//
// The two grant keys are registered in rbac.ts's DATA_GRANTS registry by core (filed under ## NEEDS
// CORE in docs/handoffs/finance.md). Until that lands the gate still works — it reads the role's own
// permissions JSONB; the registry only makes the keys tickable in the Roles UI.
import { hasDataGrant } from '@/lib/rbac'
import { useAuth } from '@/lib/auth-context'

export const RESIDUAL_PER_SUB_GRANT = 'residual_per_sub'
export const ACCOUNT_TRENDS_GRANT = 'account_trends'

// Does a thrown api() error mean "you are not allowed" (vs. a real failure)? api() throws
// new Error(detail), so the backend's 403 detail text is what we see. Same shape as the closing
// pages' forbidden check.
export const isForbidden = (e: any) => /permission|403|restricted|forbidden/i.test(String(e?.message || e || ''))

/** Client-side hint for a report grant (see the leniency note above).
 *  `ready` is false while auth/permissions are still loading — pages must NOT decide (and must not
 *  fetch) before then, or a granted user could race a token-less request into a 403 and get stuck
 *  behind the lock note. */
export function useReportGrant(key: string): { granted: boolean; ready: boolean } {
  const { permissions, loading } = useAuth()
  return { granted: hasDataGrant(permissions, key), ready: !loading }
}

/** What a caller without the grant sees INSTEAD of the report (no data, no filters, no exports). */
export function RestrictedReport({ title, grantKey, subtitle }: { title: string; grantKey: string; subtitle?: string }) {
  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>{title}</h1>
      {subtitle && <p style={{ color: 'var(--text2)', fontSize: 13, margin: '4px 0 0' }}>{subtitle}</p>}
      <div className="card" style={{ padding: 24, marginTop: 16, borderLeft: '3px solid #dc2626', maxWidth: 640 }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>🔒 Restricted</div>
        <p style={{ color: 'var(--text2)', fontSize: 13, margin: 0 }}>
          This report is restricted — ask an admin to grant it on your role.
        </p>
        <p style={{ color: 'var(--text3)', fontSize: 12, margin: '8px 0 0' }}>
          Permission needed: <code>{grantKey}</code> (admin-only by default).
        </p>
      </div>
    </div>
  )
}
