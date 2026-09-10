import type { MePayload } from '@/api/core'

// ── Reporting scope ──────────────────────────────────────────────────────────────────────────────
// MIRRORS frontend/src/lib/rbac.ts `Scope` — the REPORTING span resolved by /core/me into
// permissions.scope. It answers "whose numbers can this person see": org-wide, a market, a store, or
// only their own.
//
// WHY THE APP GATES ON THIS: the summary endpoints these dashboards read (account/overview,
// commcalc/exec-mtd, sales-report, gp, commissions) are authorised by tenant MEMBERSHIP alone — they
// do not additionally gate on role — so a store rep's token can technically fetch org-wide P&L. The
// web hides those reports behind its permission check; the app must do the same, or a financial
// dashboard would leak company numbers to anyone signed in. So org-wide financial dashboards require
// 'all' or 'market'; a store-scoped, server-filtered endpoint (POS KPIs) and the personal dashboard
// stay open.
export type Scope = 'all' | 'market' | 'store' | 'self'

const RANK: Record<Scope, number> = { all: 4, market: 3, store: 2, self: 1 }

/** The signed-in user's reporting scope. Unknown/absent → 'self' (most restrictive), so a missing
 *  grant never accidentally exposes company financials. */
export function scopeOf(me: MePayload | null): Scope {
  const raw = (me?.permissions as { scope?: unknown } | undefined)?.scope
  if (raw === 'all' || raw === 'market' || raw === 'store' || raw === 'self') return raw
  return 'self'
}

/** True when `have` reaches at least `min` (e.g. atLeast('market','market') → true, 'store' → false). */
export function atLeast(have: Scope, min: Scope): boolean {
  return RANK[have] >= RANK[min]
}
