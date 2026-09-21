// THE ONE WAY A PAGE LEARNS WHICH CONNECTORS APPLY TO THIS TENANT (mig 1014; owner 2026-09-21).
//
// Owner, on a Verizon tenant whose declared POS is RQ, reading the Inventory Values chip
// "🔌 RQ portal connection / last: error / The b2bsoft (wsreports.b2bsoft.com) portal client is not
// reverse-engineered yet …": "it says rq connection but refers to b2b reports".
//
// THE CLASS: connectors (portal sweeps, data-source logins, mailbox / FTP pulls) carried no POS /
// carrier scope anywhere, so a vendor-specific connector was offered to — and its health and errors
// shown to — every tenant, labelled with whatever POS the tenant declared. Report kinds solved the
// same class (lib/report-kinds.ts + carrier-scope.reportKindsVisible); this hook REUSES that predicate
// for the connector registry rather than copying it:
//
//   · `GET /commcalc/connector-registry` (backend connector_registry.payload) carries the registry
//     rows merged per key (house + this org's overrides), the tenant's DECLARATION (the SAME one
//     reader /report-kinds uses), the `connector:` cap overrides, the tenant's POS term and the ONE
//     neutral line.
//   · `reportKindsVisible(rows, declaration, caps, CONNECTOR_CAP_PREFIX)` — THE visibility function,
//     run here and nowhere else in a page, one axis over from report kinds.
//   · a surface that renders ONE connector's form (the Inventory Values portal connection, the per-
//     vendor sweep pages) reads the `connector_scope` the backend attached to that connector's own
//     payload (computed by the same backend function) through `scopeState` — no page spells a slug.
//
// HONEST BEFORE IT LOADS: `loaded` is false until the payload arrives and a surface offers no
// connector until then; a failed fetch leaves `error` set and the surface shows what it always did —
// a lookup that fails must never withhold a connector somebody needs ('unknown' never hides a page).
// Every connector surface imports THIS module and nothing else decides — backend/
// harness_connector_scope_lock.py fails the build when one stops, or when a page keeps its own list.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, getActiveOrg } from '@/lib/client'
import { reportKindsVisible, posSquash, type ReportDeclaration } from '@/lib/carrier-scope'
import { type FeedApplies } from '@/lib/report-kinds'

export const CONNECTOR_CAP_PREFIX = 'connector:'

export type ConnectorRow = {
  key: string; aliases?: string[]; label: string; host?: string | null
  kind?: 'portal' | 'mailbox' | 'ftp' | 'google_sa' | string
  applies_to_pos?: string[]; applies_to_carrier?: string[]; defined_by?: 'house' | 'tenant'; defined_by_org?: string | null
  sort_order?: number; is_active?: boolean; provenance?: string; provenance_text?: string
}
/** What the backend attaches to ONE connector's own payload (a sweep config, a data-source row, a
 *  connector instance): the registry's answer for that slug (connector_registry.scope). */
export type ConnectorScope = {
  applies: boolean; registered: boolean; key: string | null; label: string | null; host: string | null
  kind: string | null; why: string; miss?: 'pos' | 'carrier' | 'both' | 'override' | null; provenance?: string | null
}
export type ConnectorRegistryPayload = {
  registry_ready: boolean; migration: string
  declaration: ReportDeclaration
  connectors: ConnectorRow[]
  hidden: { key: string; label: string; why: string }[]
  caps: Record<string, boolean | null>
  all_keys: { key: string; aliases: string[]; label: string; kind: string; applies_to_pos: string[]; applies_to_carrier: string[] }[]
  pos: { term: string | null; declared: boolean }
  neutral_line: string | null
}

// ── PURE selectors (proven by frontend/prove_connector_scope.mjs against the real transpiled TS) ──

/** The visible registry row for a connector slug — by key or by any alias (case-insensitive). */
export function connectorFor(visible: ConnectorRow[], slugOrAlias: string | null | undefined): ConnectorRow | null {
  const s = posSquash(slugOrAlias)
  if (!s) return null
  return visible.find(r => posSquash(r.key) === s || (r.aliases || []).some(a => posSquash(a) === s)) || null
}

/**
 * Step 1 for a surface that holds ONE connector's payload: the backend-computed `connector_scope`
 * as the same four states the report-kind two-step uses. `undefined` scope (an older backend, a failed
 * read) → 'unknown', which never hides the surface.
 */
export function scopeState(scope: ConnectorScope | null | undefined, loaded: boolean): FeedApplies {
  if (!loaded) return 'checking'
  if (!scope || typeof scope.applies !== 'boolean') return 'unknown'
  return scope.applies ? 'defined' : 'not_defined'
}

/**
 * THE NEUTRAL LINE — the one sentence a surface prints when no connector of this kind is defined for the
 * tenant's declared POS. The backend twin is connector_registry.neutral_line; the lock pins the words equal.
 * `pos` is the tenant's POS term (usePosTerm) — never a vendor spelled here.
 */
export function neutralConnectorLine(a: { pos: string; posDeclared: boolean; noun?: string }): string {
  const noun = a.noun || 'reports-portal'
  if (a.posDeclared) {
    return `No ${noun} connection is defined for ${a.pos} yet — when ${a.pos} has a reports portal, it can be added under Connectors.`
  }
  return `No POS is declared for this tenant yet, so no ${noun} connection is offered. Declare your POS in the Implementation wizard and the connection defined for it will appear here.`
}

/**
 * The sentence a surface that drives ONE connector prints when the registry withholds it — the neutral
 * line when the DECLARED POS is what it misses (the owner's case), else the carrier / override reason
 * from the registry. Backend twin: connector_registry.not_applicable_copy.
 */
export function connectorNotApplicableCopy(a: { scope: ConnectorScope | null | undefined; pos: string; posDeclared: boolean }): string {
  const miss = a.scope?.miss
  if (miss === 'pos' || miss === 'both') return neutralConnectorLine({ pos: a.pos, posDeclared: a.posDeclared })
  const why = (a.scope?.why || '').trim() || 'it is not offered to this tenant'
  return `This connector is not offered to this tenant — ${why}. Widen its scope in the connector registry if this tenant does use it.`
}

/** A one-line explanation of what is withheld, for a surface to print instead of nothing. */
export function withheldConnectorsSummary(p: ConnectorRegistryPayload | null): string {
  if (!p) return ''
  const n = (p.hidden || []).length
  if (!n) return ''
  const decl = [p.declaration?.pos?.length ? `POS ${p.declaration.pos.join('/')}` : 'no POS declared',
                p.declaration?.carriers?.length ? `carrier ${p.declaration.carriers.join('/')}` : 'no carrier declared'].join(' · ')
  return `${n} connector${n === 1 ? '' : 's'} not offered — you declared ${decl}.`
}

const orgQS = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

/** Fetch-once hook. See the header: nothing is offered until it loads, and a failure never hides. */
export function useConnectors() {
  const [payload, setPayload] = useState<ConnectorRegistryPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const reload = useCallback(() => {
    api(`/api/v1/commcalc/connector-registry${orgQS()}`)
      .then((p: ConnectorRegistryPayload) => { setPayload(p); setError(null) })
      .catch((e: unknown) => { setPayload(null); setError((e as Error)?.message || 'could not read the connector registry') })
      .finally(() => setLoaded(true))
  }, [])
  useEffect(() => { reload() }, [reload])
  // THE ONE VISIBILITY FUNCTION (report kinds' — one axis over), run here and nowhere else in a page.
  const visible = useMemo(
    () => payload ? reportKindsVisible<ConnectorRow>(payload.connectors, payload.declaration, payload.caps, CONNECTOR_CAP_PREFIX) : [],
    [payload])
  const byKey = useCallback((slugOrAlias: string | null | undefined) => connectorFor(visible, slugOrAlias), [visible])
  // applies: 'checking' | 'unknown' | 'not_defined' | 'defined' — the same states as the report-kind two-step.
  // A slug the registry does not KNOW (all_keys) is a tenant-defined connector and is never withheld.
  const applies = useCallback((slugOrAlias: string | null | undefined): FeedApplies => {
    const row = connectorFor(visible, slugOrAlias)
    if (!loaded) return 'checking'
    if (error) return 'unknown'
    if (row) return 'defined'
    const s = posSquash(slugOrAlias)
    const known = (payload?.all_keys || []).some(k => posSquash(k.key) === s || (k.aliases || []).some(a => posSquash(a) === s))
    return known ? 'not_defined' : 'defined'
  }, [visible, loaded, error, payload])
  // allows: true unless the registry KNOWS the connector and withholds it (a failed read hides nothing).
  const allows = useCallback((slugOrAlias: string | null | undefined) => applies(slugOrAlias) !== 'not_defined', [applies])
  const labelFor = useCallback((slugOrAlias: string | null | undefined, fallback: string) => byKey(slugOrAlias)?.label || fallback, [byKey])
  return {
    payload, loaded, error, reload, visible, byKey, applies, allows, labelFor,
    declaration: payload?.declaration || null,
    hidden: payload?.hidden || [],
    ready: !!payload?.registry_ready,
    neutralLine: payload?.neutral_line || null,
    withheld: withheldConnectorsSummary(payload),
  }
}
