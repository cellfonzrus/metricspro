'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, apiDownload, apiFetchBase64, ORG_ID, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import EntityPicker from '@/components/EntityPicker'
import {
  PlanOptions, MatchRule, MatchValuePicker, MatchWarnings, OptionsSourceNote,
  usePlanMatchStats, FALLBACK_VOCAB, countMatches,
} from '../_lib/planMatch'
import {
  UnassignedRow, UnmatchedExplorer, OrphanAssignments, StoreBridgePanel, ExcludedSellers,
} from '../_lib/coverageDiagnosis'
import RunCommissionButton from '../_lib/RunCommissionButton'
import CoverageWizard from '../_lib/CoverageWizard'
import { useActiveCarrier } from '@/lib/auth-context'

// Configurable commission PLAN engine (migration 059). A PLAN is a set of RULES the user creates — each
// rule matches sale lines on any sales-transaction field (contract_type/tender_type/department/category/
// product_desc/sku/trans_type/any) and defines how matching lines PAY (flat/unit, %MRC, %GP, %price-over-
// cost, flat bonus), optionally TIERED by a qualifying-unit count → multiplier. Plans are ASSIGNED to
// employee/store/market/default (precedence employee>store>market>default). PREVIEW is READ-ONLY: it shows
// what the plan WOULD pay for a period from raw_sales — it does NOT change live commissions.

type Rule = { id?: string; label?: string; match_field: string; match_op: string; match_value: string
  qualifies: boolean; payout_kind: string; amount: number; pct: number; tiered: boolean
  // PAY GATE (mig 260) — how often this rule pays inside ONE transaction. '' = auto.
  unit_basis?: string
  // RULE SCOPE (mig 262) — WHERE this rule applies. '' = everywhere (today's behaviour).
  applies_scope_kind?: string; applies_scope_value?: string }
type Tier = { id?: string; metric?: string; min_count: number; multiplier: number }
type Assign = { id?: string; scope: string; scope_value?: string | null; priority?: number }
type Plan = { id?: string; name: string; carrier_id?: string | null; base_tier_metric?: string | null
  is_active: boolean; notes?: string | null; rules?: Rule[]; tiers?: Tier[]; assignments?: Assign[]
  // mig 232 — how the tier metric is COUNTED. Null/'rule_units' = the legacy count (every qualifying
  // rule-matched LINE, summed across rules). 'transactions' counts DISTINCT matched trans_ids.
  tier_count_basis?: string | null; tier_match_field?: string | null; tier_match_op?: string | null
  tier_match_value?: string | null; tier_below_min_multiplier?: number | string | null
  // mig 297 — where THIS plan's reps get their activations classified from. 'inherit' (default) defers to
  // the org-level setting → today's POS raw_sales. 'raw_sales' pins POS even if the org flips. 'activation_details'
  // pays activations from the uploaded Activation Details report and suppresses POS activations for this plan.
  activation_source?: string | null }
// bulk-assignment roster (people-centric surface)
type CurPlan = { plan_id: string; plan_name: string }
type Person = { id?: string; name: string; value: string; role: string; market: string; email: string
  home_store: string; epay_salesperson: string; is_active: boolean; current_plans: CurPlan[] }
type Roster = { people: Person[]; roles: string[]; markets: string[]; ready: boolean }

const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const lbl: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, fontWeight: 600, color: 'var(--text2)' }
const th: React.CSSProperties = { textAlign: 'left', padding: '5px 8px', fontSize: 11, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '4px 8px', fontSize: 12, borderTop: '1px solid var(--border)' }

// VOCABULARY (match fields / ops / payout kinds / tier bases / tier metrics) is SERVED BY THE ENGINE via
// GET /commcalc/plan-field-options — commission_engine.MATCH_FIELDS / PAYOUT_KINDS / _rule_matches are the
// source of truth, so this editor can never offer a field the engine ignores (or hide one it supports).
// FALLBACK_VOCAB (identical to what this page hard-coded before) is used only if that call fails.

// fallback help text — the endpoint serves the engine-authored version per field
const FIELD_HELP: Record<string, string> = {
  tender_type: 'e.g. acima (case-insensitive)',
  department: 'e.g. accessories, insurance, internet, Ondigo',
  contract_type: 'e.g. new activation, upgrade, swap, BYOD, Port-In',
  category: 'e.g. accessory category as it appears on the report',
  product_desc: 'e.g. Device Setup Charge (use "contains")',
  sku: 'exact SKU (use "in" for a comma list)',
  trans_type: 'e.g. Sale, Return',
  accessory: 'catalog/dept/category-classified accessory — value "yes" (needs a catalog + classification on)',
  activation_bucket: 'premium / upgrade / byod — resolved from this tenant’s classification settings, so BLANK Contract Type still counts',
  any: 'matches every line (a blanket rule)',
}

// Assignment scope hierarchy (mirrors commission_engine._resolve_plan_for SCOPE_RANK: higher = more
// specific = wins). ROLE (mig — none needed; scope is free TEXT) sits between employee and store: an
// employee assignment OVERRIDES a rep's role assignment.
const SCOPE_META: Record<string, { rank: number; color: string; bg: string; help: string }> = {
  employee: { rank: 4, color: '#166534', bg: '#dcfce7', help: 'one specific rep (overrides their role)' },
  role:     { rank: 3, color: '#1e40af', bg: '#dbeafe', help: 'every rep with this job role' },
  store:    { rank: 2, color: '#92400e', bg: '#fef3c7', help: 'every rep at this store' },
  market:   { rank: 1, color: '#6d28d9', bg: '#ede9fe', help: 'every rep in this market' },
  default:  { rank: 0, color: '#475569', bg: '#f1f5f9', help: 'all reps (fallback)' },
}
const ScopeBadge = ({ scope }: { scope: string }) => {
  const m = SCOPE_META[scope] || SCOPE_META.default
  return <span title={m.help} style={{ fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 8, background: m.bg, color: m.color, whiteSpace: 'nowrap' }}>{scope}</span>
}

const blankRule = (): Rule => ({ label: '', match_field: 'contract_type', match_op: 'equals', match_value: '', qualifies: true, payout_kind: 'flat_per_unit', amount: 0, pct: 0, tiered: false, unit_basis: '', applies_scope_kind: '', applies_scope_value: '' })

// PAY GATE (mig 260) — "how often does this rule pay on ONE sale?"
const UNIT_BASES: { value: string; label: string; help: string }[] = [
  { value: '', label: 'auto', help: 'A $/unit rule keyed on a transaction-level field (the tender) pays once per DEVICE; everything else pays per line. This is the default and it implements the owner ruling of 2026-08-01.' },
  { value: 'per_line', label: 'per line', help: 'Pay once for EVERY matching line — correct for a rule that genuinely pays per line item (e.g. $2 per accessory).' },
  { value: 'per_device', label: 'per device', help: 'Pay once per distinct device serial on the sale. The payment lands on the line carrying the serial, so accessory / rate-plan / fee lines never carry it.' },
  { value: 'per_transaction', label: 'per sale', help: 'Pay exactly once per transaction, however many devices or lines it has.' },
]
const blankPlan = (): Plan => ({ name: '', carrier_id: '', base_tier_metric: 'none', is_active: true, notes: '', activation_source: 'inherit', rules: [], tiers: [], assignments: [] })

export default function CommissionPlansPage() {
  // Active-carrier lens: the set-up-fee reference copy names only the active carrier for a dual-carrier
  // tenant (single-carrier tenants keep the original Boost/Total reference text).
  const { activeCarrier, multi } = useActiveCarrier()
  const isTotalCarrier = activeCarrier === 'total'
  // Show Boost-branded default wording ONLY to a single-carrier Boost tenant; a non-Boost tenant
  // never sees "Boost" language.
  const showBoost = !multi && activeCarrier === 'boost'
  const [plans, setPlans] = useState<Plan[]>([])
  const [carriers, setCarriers] = useState<any[]>([])
  const [employees, setEmployees] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  // ENGINE VOCABULARY + this tenant's OBSERVED values per match_field (RULE THREE §3b) — powers every
  // dropdown here, so a rule references a REAL sales value instead of a typo that silently never matches
  // ($0 pay) or a hand-typed pattern that double-pays with another rule. Read-only; no calc is triggered.
  const [planOpts, setPlanOpts] = useState<PlanOptions | null>(null)
  const [ready, setReady] = useState(true)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState<Plan | null>(null)
  const [period, setPeriod] = useState('June 2026')
  const [preview, setPreview] = useState<any>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  // plan-coverage diagnostic (mig 232): uncovered sellers · unmatched lines · tier/CT warnings · stale snapshot
  const [cov, setCov] = useState<any>(null)
  const [covBusy, setCovBusy] = useState(false)
  // Coverage Wizard (guided fix for "lines not paying") — opens a modal, writes nothing until its Apply.
  const [wizOpen, setWizOpen] = useState(false)
  // Part D — the tenant's "not a commissionable seller" list (mig 248). Diagnostics only: it moves a $0
  // seller out of the uncovered list into a visible collapsed note; it can never change a payout.
  const [exclBusy, setExclBusy] = useState(false)
  // ── people-centric BULK assignment (owner directive 2026-07-23) ──
  const [tab, setTab] = useState<'plans' | 'bulk'>('plans')
  const [roster, setRoster] = useState<Roster>({ people: [], roles: [], markets: [], ready: true })
  const [fMarkets, setFMarkets] = useState<string[]>([])
  const [fRoles, setFRoles] = useState<string[]>([])
  const [nameQuery, setNameQuery] = useState('')
  const [showInactive, setShowInactive] = useState(false)
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [bulkPlanId, setBulkPlanId] = useState<string | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkResult, setBulkResult] = useState<any>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  // PAY GATE (mig 260) — how often a rule pays on one sale + the before/after it produces.
  const [gate, setGate] = useState<any>(null)
  const [gateBusy, setGateBusy] = useState(false)
  const [impact, setImpact] = useState<any>(null)
  const [impactBusy, setImpactBusy] = useState(false)
  const [audit, setAudit] = useState<any>(null)
  const [auditBusy, setAuditBusy] = useState(false)
  const txnLevelFields: string[] = gate?.config?.unit_basis?.auto_txn_level_fields || ['tender_type']

  async function loadGate() {
    try { setGate(await api('/api/v1/commcalc/commission-plans/pay-gate')) } catch { setGate(null) }
  }
  async function saveGate(next: any) {
    setGateBusy(true)
    try {
      const r = await api('/api/v1/commcalc/commission-plans/pay-gate', { method: 'PUT', body: JSON.stringify({ config: next }) })
      setGate({ ...(gate || {}), config: r.config, is_default: false })
      setMsg('Pay-gate settings saved. Nothing was recalculated — run Calculate for the period(s) concerned.')
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) } finally { setGateBusy(false) }
  }
  async function runImpact() {
    setImpactBusy(true); setImpact(null)
    try { setImpact(await api(`/api/v1/commcalc/commission-plans/unit-dedup-impact/${encodeURIComponent(period)}`)) }
    catch (e: any) { setImpact({ error: String(e?.message || e) }) } finally { setImpactBusy(false) }
  }
  async function runAudit() {
    setAuditBusy(true); setAudit(null)
    try { setAudit(await api(`/api/v1/commcalc/commission-plans/unit-multiplication-audit/${encodeURIComponent(period)}`)) }
    catch (e: any) { setAudit({ error: String(e?.message || e) }) } finally { setAuditBusy(false) }
  }
  // PAYOUT EXCLUSIONS (mig 261) — classes of line that never pay, whatever a rule says.
  const [excl, setExcl] = useState<any>(null)
  const [exclDraft, setExclDraft] = useState<any>({ match_field: 'product_desc', match_op: 'word', match_value: '', label: '', reason: '' })
  const [exclSaving, setExclSaving] = useState(false)
  const [exclImpact, setExclImpact] = useState<any>(null)
  const [exclImpactBusy, setExclImpactBusy] = useState(false)

  async function loadExclusions() {
    try { setExcl(await api('/api/v1/commcalc/commission-plans/payout-exclusions')) } catch { setExcl(null) }
  }
  async function saveExclusion(row: any) {
    setExclSaving(true)
    try {
      await api('/api/v1/commcalc/commission-plans/payout-exclusions', { method: 'POST', body: JSON.stringify(row) })
      setExclDraft({ match_field: 'product_desc', match_op: 'word', match_value: '', label: '', reason: '' })
      await loadExclusions()
      setMsg('Exclusion saved. Nothing was recalculated — run Calculate for the period(s) concerned.')
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) } finally { setExclSaving(false) }
  }
  async function deleteExclusion(id: string) {
    setExclSaving(true)
    try { await api(`/api/v1/commcalc/commission-plans/payout-exclusions/${id}`, { method: 'DELETE' }); await loadExclusions() }
    catch (e: any) { setMsg('Delete failed: ' + (e?.message || e)) } finally { setExclSaving(false) }
  }
  async function runExclImpact() {
    setExclImpactBusy(true); setExclImpact(null)
    try { setExclImpact(await api(`/api/v1/commcalc/commission-plans/exclusion-impact/${encodeURIComponent(period)}`)) }
    catch (e: any) { setExclImpact({ error: String(e?.message || e) }) } finally { setExclImpactBusy(false) }
  }
  // ACCESSORY %-OF-GP BASIS GUARD (mig 260, default OFF fleet-wide).
  const [accImpact, setAccImpact] = useState<any>(null)
  const [accBusy, setAccBusy] = useState(false)
  async function runAccImpact() {
    setAccBusy(true); setAccImpact(null)
    try { setAccImpact(await api(`/api/v1/commcalc/commission-plans/accessory-basis-impact/${encodeURIComponent(period)}`)) }
    catch (e: any) { setAccImpact({ error: String(e?.message || e) }) } finally { setAccBusy(false) }
  }
  // SET-UP / ACTIVATION FEE (mig 263) — mapping + per-carrier economics + the employee pay item.
  const [sf, setSf] = useState<any>(null)
  const [sfBusy, setSfBusy] = useState(false)
  const [sfCand, setSfCand] = useState<any>(null)
  const [sfCandBusy, setSfCandBusy] = useState(false)
  const [sfImpact, setSfImpact] = useState<any>(null)
  const [sfImpactBusy, setSfImpactBusy] = useState(false)
  const [sfPctDraft, setSfPctDraft] = useState('')

  async function loadSf() {
    try { setSf(await api('/api/v1/commcalc/setup-fee/config')) } catch { setSf(null) }
  }
  async function saveSf(next: any) {
    setSfBusy(true)
    try {
      const r = await api('/api/v1/commcalc/setup-fee/config', { method: 'PUT', body: JSON.stringify({ config: next }) })
      setSf({ ...(sf || {}), config: r.config, is_default: false })
      setMsg(r.note || 'Saved.')
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) } finally { setSfBusy(false) }
  }
  async function loadSfCandidates() {
    setSfCandBusy(true); setSfCand(null)
    try { setSfCand(await api(`/api/v1/commcalc/setup-fee/candidates/${encodeURIComponent(period)}`)) }
    catch (e: any) { setSfCand({ error: String(e?.message || e) }) } finally { setSfCandBusy(false) }
  }
  async function runSfImpact() {
    setSfImpactBusy(true); setSfImpact(null)
    const q = sfPctDraft.trim() ? `?employee_pct=${encodeURIComponent(sfPctDraft.trim())}` : ''
    try { setSfImpact(await api(`/api/v1/commcalc/setup-fee/impact/${encodeURIComponent(period)}${q}`)) }
    catch (e: any) { setSfImpact({ error: String(e?.message || e) }) } finally { setSfImpactBusy(false) }
  }
  async function mapKeyword(kw: string) {
    // The mapping lives in the SHARED mig-217 list (accessory_config.setup_fee_keywords) — the same
    // list the Sales Report / Executive MTD read, so one definition drives the report and the pay.
    const cur: string[] = sf?.keywords || []
    if (cur.includes(kw)) return
    setSfBusy(true)
    try {
      await api('/api/v1/commcalc/accessory-config', { method: 'PUT', body: JSON.stringify({ setup_fee_keywords: [...cur, kw] }) })
      await loadSf(); await loadSfCandidates()
      setMsg('Mapped. This also updates the Sales Report and Executive MTD — one definition, one number.')
    } catch (e: any) { setMsg('Map failed: ' + (e?.message || e)) } finally { setSfBusy(false) }
  }

  async function loadRoster() {
    try {
      const r = await api('/api/v1/commcalc/commission-plans/roster')
      setRoster({ people: r.people || [], roles: r.roles || [], markets: r.markets || [], ready: r.ready !== false })
    } catch { setRoster({ people: [], roles: [], markets: [], ready: true }) }
  }

  async function load() {
    try {
      // These four reads are independent — one round trip instead of a 4-deep waterfall. The three
      // reference lists (carriers, roster, stores) are cache-served (LOOKUP); plans is live data.
      // include_inactive: the role-count preview must agree with the engine, which matches INACTIVE reps
      // too (a mid-month-terminated rep's sales still pay under their role). We show active/inactive split.
      const [r, carr, emps, sts] = await Promise.all([
        api('/api/v1/commcalc/commission-plans'),
        apiCached('/api/v1/commcalc/carriers', LOOKUP).catch(() => []),
        apiCached('/api/v1/storeops/employees?all_company=true&include_inactive=true', LOOKUP).catch(() => []),
        apiCached('/api/v1/storeops/stores', LOOKUP).catch(() => []),
      ])
      setPlans(r.plans || []); setReady(r.ready !== false)
      if (r.ready === false) setMsg(r.note || 'Run migration 059 to enable.')
      setCarriers(carr)
      setEmployees(emps)
      setStores(sts)
      // (the value options load in their own effect below — they depend on the previewed period)
    } catch (e: any) { setMsg('Load failed: ' + (e?.message || e)) }
  }
  // Engine vocabulary + observed values + the facet table that powers the exact "matches nothing" /
  // "N lines also match rule X" guards. The window is the last 3 months PLUS the period being previewed.
  // Falls back to the older /sales-fields lists (and the hard-coded vocabulary) if the endpoint is
  // unavailable, so the editor degrades to exactly today's behaviour instead of breaking.
  async function loadOptions(forPeriod: string) {
    try {
      const o: PlanOptions = await api(`/api/v1/commcalc/plan-field-options?months=3&period=${encodeURIComponent(forPeriod || '')}`)
      setPlanOpts({ ...o, vocab: o?.vocab || FALLBACK_VOCAB })
    } catch {
      try {
        const sf: any = await api('/api/v1/commcalc/sales-fields')
        const mk = (vals: string[]): any => ({ values: (vals || []).map(v => ({ value: v })), truncated: true })
        setPlanOpts({
          ready: false, vocab: FALLBACK_VOCAB, facets: null, periods: [],
          fields: {
            contract_type: mk(sf.contract_types), tender_type: mk(sf.tenders),
            department: mk(sf.departments), category: mk(sf.categories),
            product_desc: { ...mk(sf.products), free_text: true }, trans_type: mk(sf.trans_types),
            sku: mk([]), accessory: { values: [{ value: 'yes' }, { value: 'no' }], closed: true },
            activation_bucket: { values: [{ value: 'premium' }, { value: 'upgrade' }, { value: 'byod' }], closed: true },
          },
        })
      } catch { setPlanOpts({ ready: false, vocab: FALLBACK_VOCAB, fields: {}, facets: null, periods: [] }) }
    }
  }
  useEffect(() => { load(); loadRoster(); loadGate(); loadExclusions(); loadSf() }, [])
  // re-read the options when the operator changes the period (server-side TTL cache makes this cheap)
  useEffect(() => {
    const t = setTimeout(() => { loadOptions(period) }, 400)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period])

  const markets = Array.from(new Set(stores.map(s => (s.market || '').trim()).filter(Boolean))).sort()
  const carrierName = (id?: string | null) => carriers.find(c => c.id === id)?.name || ''

  // ── engine-served vocabulary (falls back to the previously hard-coded lists) ──
  const vocab = planOpts?.vocab || FALLBACK_VOCAB
  const usesPct = (k: string) => vocab.payout_kinds.find(p => p.value === k)?.uses === 'pct'
  const fieldHelp = (f: string) => vocab.match_fields.find(x => x.value === f)?.help || FIELD_HELP[f] || ''
  // ZERO-WIPE for the two metric dropdowns: a value already stored on THIS plan is always offered, even
  // when it isn't one of the suggested metrics (the backend also unions in every metric this org stores).
  const tierMetrics = useMemo(() => {
    const out = [...vocab.tier_metrics]
    const stored = [draft?.base_tier_metric || '', ...((draft?.tiers || []).map(t => t.metric || ''))]
    stored.forEach(v => { if (v && !out.includes(v)) out.push(v) })
    return out
  }, [vocab, draft?.base_tier_metric, draft?.tiers])
  // exact matched-line counts + the pairwise overlap matrix for the rules being edited, PLUS the tier
  // matcher as one extra pseudo-rule (so the tier rule gets the same "matches nothing" guard).
  const matchRules: MatchRule[] = useMemo(() => [
    ...(draft?.rules || []).map(r => ({ match_field: r.match_field, match_op: r.match_op, match_value: r.match_value, label: r.label, qualifies: r.qualifies })),
  ], [draft?.rules])
  const matchStats = usePlanMatchStats(planOpts, matchRules)
  const tierRule: MatchRule = useMemo(() => ({
    match_field: draft?.tier_match_field || 'any', match_op: draft?.tier_match_op || 'equals',
    match_value: draft?.tier_match_value || '', label: 'tier rule',
  }), [draft?.tier_match_field, draft?.tier_match_op, draft?.tier_match_value])
  const tierCount = useMemo(() => countMatches(planOpts, tierRule), [planOpts, tierRule])

  // ── bulk-assign derived state ──
  const nameCounts = useMemo(() => {
    const m: Record<string, number> = {}
    roster.people.forEach(p => { const k = p.name.trim().toLowerCase(); m[k] = (m[k] || 0) + 1 })
    return m
  }, [roster.people])
  const filteredPeople = useMemo(() => {
    const mset = new Set(fMarkets), rset = new Set(fRoles), q = nameQuery.trim().toLowerCase()
    return roster.people.filter(p => {
      if (!showInactive && !p.is_active) return false
      if (mset.size && !mset.has(p.market)) return false
      if (rset.size && !rset.has(p.role)) return false
      if (q && !`${p.name} ${p.email} ${p.value}`.toLowerCase().includes(q)) return false
      return true
    })
  }, [roster.people, fMarkets, fRoles, nameQuery, showInactive])
  const filteredValues = useMemo(() => filteredPeople.map(p => p.value), [filteredPeople])
  const allFilteredChecked = filteredValues.length > 0 && filteredValues.every(v => checked.has(v))
  const someFilteredChecked = filteredValues.some(v => checked.has(v))
  const selectedPeople = useMemo(() => roster.people.filter(p => checked.has(p.value)), [roster.people, checked])
  const bulkPreview = useMemo(() => {
    let newCount = 0, alreadyCount = 0, replacePeople = 0, replaceRows = 0
    selectedPeople.forEach(p => {
      if (p.current_plans.some(c => c.plan_id === bulkPlanId)) alreadyCount++
      else if (p.current_plans.length) { replacePeople++; replaceRows += p.current_plans.length }
      else newCount++
    })
    return { newCount, alreadyCount, replacePeople, replaceRows }
  }, [selectedPeople, bulkPlanId])
  const planOptions = useMemo(() => plans.map(p => ({
    id: p.id!, label: p.name,
    sublabel: `${p.rules?.length || 0} rules · ${p.assignments?.length || 0} assignments${p.is_active ? '' : ' · inactive'}`,
  })), [plans])
  const bulkPlanName = plans.find(p => p.id === bulkPlanId)?.name || ''

  function toggleAll() {
    setChecked(prev => {
      const next = new Set(prev)
      if (allFilteredChecked) filteredValues.forEach(v => next.delete(v))
      else filteredValues.forEach(v => next.add(v))
      return next
    })
  }
  function toggleOne(v: string) {
    setChecked(prev => { const n = new Set(prev); n.has(v) ? n.delete(v) : n.add(v); return n })
  }
  async function applyBulk(replace: boolean) {
    if (!bulkPlanId || selectedPeople.length === 0) return
    setBulkBusy(true); setBulkResult(null)
    try {
      const r = await api('/api/v1/commcalc/commission-plans/bulk-assign', {
        method: 'POST',
        body: JSON.stringify({ plan_id: bulkPlanId, replace_existing: replace, people: selectedPeople.map(p => p.value) }),
      })
      setBulkResult(r); setConfirmOpen(false); setChecked(new Set())
      await Promise.all([loadRoster(), load()])   // refresh current-plan badges + plan assignment counts
    } catch (e: any) { setBulkResult({ error: e?.message || String(e) }) } finally { setBulkBusy(false) }
  }

  // EMPLOYEE picker (pick-don't-type, §3b). scope_value = epay_salesperson || name — the rep's EXPLICIT
  // ePay/POS name when set (the escape hatch for POS strings that differ beyond word order — initials,
  // nicknames — which the name bridge alone can't reconcile), else the roster name. Sublabel shows role +
  // email, plus an "epay: <x>" hint when the stored ePay name differs from the display name. Active only.
  const employeeOptions = employees
    .filter(e => e.is_active !== false)
    .map(e => {
      const nm = String(e.name || ''), epay = String(e.epay_salesperson || '')
      const idVal = epay || nm
      const hint = epay && epay !== nm ? `epay: ${epay}` : null
      return { id: idVal, label: nm || idVal,
               sublabel: [e.role, e.email, hint].filter(Boolean).join(' · ') || undefined }
    })
    .filter(o => o.id)
  // ROLE picker: distinct job roles from the org's roster with an employee-count preview. Writing scope=
  // 'role' scope_value=<role> assigns the plan to EVERY rep with that role. Count shows "N active (+M
  // inactive)" because the engine matches inactive reps too — so the number agrees with what pays.
  const roleStats = employees.reduce((m: Record<string, { active: number; inactive: number }>, e) => {
    const r = String(e.role || '').trim(); if (!r) return m
    const s = m[r] || (m[r] = { active: 0, inactive: 0 })
    if (e.is_active === false) s.inactive++; else s.active++
    return m
  }, {})
  const roleOptions = Object.entries(roleStats).sort((a, b) => a[0].localeCompare(b[0]))
    .map(([r, s]) => ({ id: r,
      label: `${r} — ${s.active} active${s.inactive ? ` (+${s.inactive} inactive)` : ''}` }))

  // Store / market pickers (RULE THREE). A scope_value already saved on the plan is ALWAYS offered even
  // when the store/market list no longer contains it, so opening the editor can never silently blank an
  // assignment (which would move that rep onto a different plan on the next save).
  const storeOptions = (current?: string | null) => {
    const out = stores.map(s => ({ id: String(s.address || s.store_code || ''), label: String(s.address || s.store_code || ''), sublabel: s.market || undefined }))
      .filter(o => o.id)
    if (current && !out.some(o => o.id.toLowerCase() === current.toLowerCase())) out.unshift({ id: current, label: current, sublabel: 'not in the current store list' })
    return out
  }
  const marketOptions = (current?: string | null) => {
    const out = markets.map(m => ({ id: m, label: m, sublabel: undefined as string | undefined }))
    if (current && !out.some(o => o.id.toLowerCase() === current.toLowerCase())) out.unshift({ id: current, label: current, sublabel: 'not in the current market list' })
    return out
  }

  // ── plan-level mutators ──
  const upd = (patch: Partial<Plan>) => setDraft(d => d ? { ...d, ...patch } : d)
  const updRule = (i: number, patch: Partial<Rule>) => setDraft(d => d ? { ...d, rules: (d.rules || []).map((r, j) => j === i ? { ...r, ...patch } : r) } : d)
  const addRule = () => setDraft(d => d ? { ...d, rules: [...(d.rules || []), blankRule()] } : d)
  const delRule = (i: number) => setDraft(d => d ? { ...d, rules: (d.rules || []).filter((_, j) => j !== i) } : d)
  const updTier = (i: number, patch: Partial<Tier>) => setDraft(d => d ? { ...d, tiers: (d.tiers || []).map((t, j) => j === i ? { ...t, ...patch } : t) } : d)
  const addTier = () => setDraft(d => d ? { ...d, tiers: [...(d.tiers || []), { min_count: 0, multiplier: 1 }] } : d)
  const delTier = (i: number) => setDraft(d => d ? { ...d, tiers: (d.tiers || []).filter((_, j) => j !== i) } : d)
  const updAssign = (i: number, patch: Partial<Assign>) => setDraft(d => d ? { ...d, assignments: (d.assignments || []).map((a, j) => j === i ? { ...a, ...patch } : a) } : d)
  const addAssign = () => setDraft(d => d ? { ...d, assignments: [...(d.assignments || []), { scope: 'default', scope_value: '', priority: 0 }] } : d)
  const delAssign = (i: number) => setDraft(d => d ? { ...d, assignments: (d.assignments || []).filter((_, j) => j !== i) } : d)

  async function save() {
    if (!draft) return
    if (!draft.name.trim()) { setMsg('Plan name is required.'); return }
    setBusy(true); setMsg('')
    try {
      const body = {
        ...draft, carrier_id: draft.carrier_id || null,
        base_tier_metric: draft.base_tier_metric === 'none' ? null : draft.base_tier_metric,
        // mig 232 tier-attainment config — always sent so clearing a value persists as NULL (the backend
        // ignores these keys entirely when the migration hasn't run).
        tier_count_basis: draft.tier_count_basis || '',
        tier_match_field: draft.tier_count_basis ? (draft.tier_match_field || 'any') : '',
        tier_match_op: draft.tier_count_basis ? (draft.tier_match_op || 'equals') : '',
        tier_match_value: draft.tier_count_basis ? (draft.tier_match_value || '') : '',
        tier_below_min_multiplier: (draft.tier_below_min_multiplier === null || draft.tier_below_min_multiplier === undefined || draft.tier_below_min_multiplier === '') ? '' : Number(draft.tier_below_min_multiplier),
        rules: (draft.rules || []).map((r, i) => ({ ...r, amount: Number(r.amount) || 0, pct: Number(r.pct) || 0, sort: i })),
        tiers: (draft.tiers || []).map((t, i) => ({ ...t, min_count: Number(t.min_count) || 0, multiplier: Number(t.multiplier) || 1, sort: i })),
        assignments: (draft.assignments || []).map(a => ({ ...a, priority: Number(a.priority) || 0 })),
      }
      await api('/api/v1/commcalc/commission-plans', { method: 'POST', body: JSON.stringify(body) })
      setMsg('✅ Saved.'); setDraft(null); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }
  async function del(id?: string) {
    if (!id || !confirm('Delete this plan and all its rules/tiers/assignments?')) return
    try { await api(`/api/v1/commcalc/commission-plans/${id}`, { method: 'DELETE' }); load() } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function runCoverage() {
    setCovBusy(true); setCov(null)
    try {
      setCov(await api(`/api/v1/commcalc/commission-plans/coverage?period=${encodeURIComponent(period)}`))
    } catch (e: any) { setMsg('❌ Coverage: ' + (e?.message || e)) } finally { setCovBusy(false) }
  }
  // Part D writer — admin-gated server-side. Re-runs coverage so the panel always shows what the SERVER
  // stored, never what was clicked.
  async function saveExcluded(sellers: string[]) {
    setExclBusy(true)
    try {
      await api('/api/v1/commcalc/commission-plans/coverage-excluded', {
        method: 'PUT', body: JSON.stringify({ sellers }),
      })
      await runCoverage()
    } catch (e: any) { setMsg('❌ Excluded sellers: ' + (e?.message || e)) } finally { setExclBusy(false) }
  }
  const excludedNow: string[] = cov?.coverage?.excluded_config?.sellers || []

  // Periods this tenant actually has sales for (pick-don't-type); free entry stays allowed so an operator
  // can still look at a period that has no rows yet.
  const periodOptions = useMemo(() => {
    const out = (planOpts?.periods || []).map(p => ({ id: p.value, label: p.value, sublabel: `${(p.lines || 0).toLocaleString()} sale lines` }))
    if (period && !out.some(o => o.id === period)) out.unshift({ id: period, label: period, sublabel: 'no sales rows found' })
    return out
  }, [planOpts?.periods, period])

  function coveragePayload(): ExportPayload {
    const c = cov?.coverage || {}
    return {
      title: 'Incentive Plan Coverage', subtitle: `${cov?.period || period} — read-only diagnostic`,
      filename: `commission-coverage-${String(cov?.period || period).replace(/\s+/g, '-')}`,
      sheets: [
        { name: 'Uncovered sellers', rows: c.unassigned_reps || [], columns: [
          { header: 'Rep', get: (r: any) => r.rep }, { header: 'Store', get: (r: any) => r.store },
          { header: 'Market', get: (r: any) => r.market }, { header: 'Role', get: (r: any) => r.role },
          { header: 'Transactions', get: (r: any) => r.transactions }, { header: 'Lines', get: (r: any) => r.lines },
          { header: 'Sales $', get: (r: any) => r.ext_price, money: true },
          // the structured diagnosis, flattened — an emailed export must be as actionable as the page
          { header: 'What to do', get: (r: any) => r.diagnosis?.conclusion || r.reason },
          { header: 'Name bridge', get: (r: any) => r.diagnosis?.name_bridge?.status },
          { header: 'Roster candidates', get: (r: any) => (r.diagnosis?.name_bridge?.candidates || []).map((x: any) => `${x.name} (${Math.round((x.score || 0) * 100)}%)`).join('; ') },
          { header: 'Assignment near-miss', get: (r: any) => (r.diagnosis?.assignment_near_miss || []).map((x: any) => `${x.plan_name}: '${x.scope_value}'`).join('; ') },
          { header: 'Store resolution', get: (r: any) => r.diagnosis?.store_bridge?.message },
          { header: 'With alias resolution', get: (r: any) => r.diagnosis?.alias_preview?.message },
          { header: 'Looks like a POS artifact', get: (r: any) => (r.diagnosis?.artifact?.reasons || []).join('; ') }] },
        { name: 'Excluded sellers', rows: c.excluded_reps || [], columns: [
          { header: 'Rep', get: (r: any) => r.rep }, { header: 'Store', get: (r: any) => r.store },
          { header: 'Lines', get: (r: any) => r.lines }, { header: 'Sales $', get: (r: any) => r.ext_price, money: true }] },
        { name: 'Assigned to nobody', rows: c.orphan_assignments || [], columns: [
          { header: 'Assigned name', get: (r: any) => r.scope_value }, { header: 'Plan', get: (r: any) => r.plan_name },
          { header: 'Nearest sellers', get: (r: any) => (r.nearest_sellers || []).map((x: any) => `${x.rep} (${Math.round((x.score || 0) * 100)}%)`).join('; ') },
          { header: 'Why', get: (r: any) => r.message }] },
        { name: 'Store to market', rows: c.stores?.rows || [], columns: [
          { header: 'POS store string', get: (r: any) => r.store }, { header: 'Lines', get: (r: any) => r.lines },
          { header: 'Market today', get: (r: any) => r.market }, { header: 'Status', get: (r: any) => r.status },
          { header: 'With alias', get: (r: any) => r.would_resolve_with_alias ? `${r.alias?.store_code} → ${r.alias_market}` : '' },
          { header: 'What to do', get: (r: any) => r.message }] },
        { name: 'Covered reps', rows: cov?.by_rep || [], columns: [
          { header: 'Rep', get: (r: any) => r.rep }, { header: 'Plan', get: (r: any) => r.plan_name },
          { header: 'Tier count', get: (r: any) => r.tier_units }, { header: 'Basis', get: (r: any) => r.tier_basis },
          { header: 'Tier x', get: (r: any) => r.tier_multiplier }, { header: 'Total', get: (r: any) => r.total_payout },
          { header: 'Unmatched lines', get: (r: any) => r.unmatched_lines },
          { header: 'Unmatched $', get: (r: any) => r.unmatched_ext_price }] },
        { name: 'Warnings', rows: c.plan_warnings || [], columns: [
          { header: 'Plan', get: (r: any) => r.plan }, { header: 'Severity', get: (r: any) => r.severity },
          { header: 'Code', get: (r: any) => r.code }, { header: 'Message', get: (r: any) => r.message }] },
      ],
    }
  }

  async function runPreview(planId?: string) {
    setPreviewBusy(true); setPreview(null)
    try {
      const q = `?period=${encodeURIComponent(period)}${planId ? `&plan_id=${planId}` : ''}`
      setPreview(await api(`/api/v1/commcalc/commission-plans/preview${q}`))
    } catch (e: any) { setMsg('❌ Preview: ' + (e?.message || e)) } finally { setPreviewBusy(false) }
  }

  // ── PAYOUT STRUCTURE — the employee-facing "how commission is earned" document ─────────────────────
  // Server-rendered PDF (payout_structure.py) built from THIS tenant's real commission-plan config: what
  // pays, at what rate, how often, and what never pays. Handed to staff BEFORE they start selling. Passing
  // no plan_id renders every plan (the document you give any employee); passing plan_id renders one plan as
  // a per-team handout. READ-ONLY — the endpoint computes and writes nothing. Download uses the authed
  // byte-download choke point; Send fetches the SAME PDF as base64 and posts it through the shared
  // /notify/send-file modal (SendReportButton's serverFiles path — the PDF is rendered on the SERVER, so
  // the in-browser export path can't produce it).
  const payoutStructureUrl = (planId?: string) =>
    `/api/v1/commcalc/commission-plans/payout-structure?fmt=pdf&org_id=${ORG_ID}${planId ? `&plan_id=${planId}` : ''}`
  function downloadPayoutStructure(planId?: string) {
    apiDownload(payoutStructureUrl(planId)).catch(e => setMsg('❌ Payout structure: ' + (e?.message || e)))
  }
  async function payoutStructureFiles(planId?: string, planName?: string) {
    const b64 = await apiFetchBase64(payoutStructureUrl(planId))
    const safe = (planName ? `payout-structure-${planName}` : 'payout-structure')
      .replace(/[^\w]+/g, '-').replace(/^-|-$/g, '').toLowerCase()
    return [{ filename: `${safe}.pdf`, mime: 'application/pdf', content_b64: b64 }]
  }

  function previewPayload(): ExportPayload {
    return {
      title: 'Incentive Plan Preview (read-only)', subtitle: `${period} — does NOT change live incentives`,
      filename: `commission-preview-${period.replace(/\s+/g, '-')}`,
      sheets: [{
        name: 'By Rep', rows: preview?.by_rep || [],
        columns: [
          { header: 'Rep', get: (r: any) => r.rep },
          { header: 'Store', get: (r: any) => r.store },
          { header: 'Plan', get: (r: any) => r.plan_name },
          { header: 'Qual. units', get: (r: any) => r.qualifying_units },
          { header: 'Tier ×', get: (r: any) => r.tier_multiplier },
          { header: 'Total payout', get: (r: any) => r.total_payout, money: true },
        ],
      }],
    }
  }

  return (
    <div style={{ maxWidth: 1140 }}>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧮 Incentive Plans</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Build your own incentive plans. Any line on the sales transaction report can qualify for incentive
          on rules YOU define — then assign each plan to employees / stores / markets. The preview shows what a
          plan <strong>would</strong> pay; it is <strong>read-only</strong> and does not change live incentives.
        </p>
        <p style={{ fontSize: 13, margin: '6px 0 0' }}>
          🕵️ <a href="/commcalc/plan-assignment-audit" style={{ color: 'var(--accent)' }}>Plan Assignment Audit</a>
          {' '}— see which plan every employee resolves to and catch by-name pins that override a rep&rsquo;s store/market.
        </p>
      </div>

      {/* RUN COMMISSION (owner directive 2026-08-05) — editing a plan changes NOTHING until the period
          is recomputed, so the recalculate control lives here, next to the structure being edited.
          Shares the period picker below (Preview) so one page never targets two different months. */}
      <div className="card" style={{ padding: 14, marginBottom: 14, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ fontWeight: 700, fontSize: 13 }}>⚡ Apply these plans to live pay</div>
        <RunCommissionButton period={period} onPeriodChange={setPeriod} periodOptions={periodOptions}
          note="Saving a plan, rule, tier or assignment does not change anyone's pay by itself. Recalculate the period to write the new numbers into the Rep Incentive report." />
      </div>

      {!ready && <div className="card" style={{ padding: 14, marginBottom: 14, background: '#fffbeb', border: '1px solid #fde68a', fontSize: 13 }}>⚠️ {msg || 'Run migration 059_commission_plans.sql in Supabase to enable.'}</div>}

      {/* PAYOUT STRUCTURE (owner directive) — the employee-facing "how commission is earned" document.
          Built from this tenant's real plan config; hand it to staff BEFORE they start selling. Download
          the PDF or send it to recipients. READ-ONLY — nothing is computed or written. All-plans is the
          primary (the document you give any employee); a per-plan handout lives on each plan row below. */}
      {ready && (
        <div className="card" style={{ padding: 14, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 700, fontSize: 13, whiteSpace: 'nowrap' }}>📄 Payout Structure</div>
          <span style={{ fontSize: 12, color: 'var(--text2)', flex: '1 1 240px', minWidth: 200 }}>
            The employee-facing “how incentive is earned” document — what pays, at what rate, how often, and
            what never pays. Hand it to staff before they start selling. Read-only.
          </span>
          <button className="btn btn-secondary" onClick={() => downloadPayoutStructure()}
            title="Download the Payout Structure PDF for all plans (the document you give any employee)">
            📄 Payout Structure (PDF)
          </button>
          <SendReportButton
            title={`Payout structure — all plans — ${localToday()}`}
            label="📤 Send structure"
            serverFiles={() => payoutStructureFiles()} />
        </div>
      )}

      {/* tab bar — plan-centric editor  vs  people-centric bulk assign */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 16, borderBottom: '1px solid var(--border)' }}>
        {([['plans', '🧮 Plans'], ['bulk', '👥 Assign to people']] as const).map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)} style={{
            border: 'none', background: 'none', cursor: 'pointer', padding: '8px 14px', fontSize: 13,
            fontWeight: tab === t ? 700 : 500, color: tab === t ? 'var(--text)' : 'var(--text2)',
            borderBottom: tab === t ? '2px solid var(--primary, #2563eb)' : '2px solid transparent', marginBottom: -1,
          }}>{label}</button>
        ))}
      </div>

      {tab === 'plans' && (<>
      {/* list + new */}
      {!draft && (
        <div className="card" style={{ padding: 0, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', padding: '10px 14px', borderBottom: '1px solid var(--border)' }}>
            <div style={{ fontWeight: 700, fontSize: 13 }}>Plans ({plans.length})</div>
            <span style={{ flex: 1 }} />
            <button className="btn btn-primary" onClick={() => setDraft(blankPlan())}>➕ New plan</button>
          </div>
          {plans.map(p => (
            <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', borderTop: '1px solid var(--border)', fontSize: 13, flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 700 }}>{p.name}</span>
              {p.carrier_id && <span style={{ fontSize: 12, color: 'var(--text3)' }}>{carrierName(p.carrier_id)}</span>}
              <span style={{ fontSize: 11, color: 'var(--text3)' }}>{(p.rules?.length || 0)} rules · {(p.tiers?.length || 0)} tiers · {(p.assignments?.length || 0)} assignments</span>
              {Array.from(new Set((p.assignments || []).map(a => a.scope || 'default')))
                .sort((a, b) => (SCOPE_META[b]?.rank ?? 0) - (SCOPE_META[a]?.rank ?? 0))
                .map(s => <ScopeBadge key={s} scope={s} />)}
              {p.base_tier_metric && <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: '#ede9fe', color: '#6d28d9' }}>tier: {p.base_tier_metric}</span>}
              {!p.is_active && <span style={{ fontSize: 11, color: '#b45309' }}>inactive</span>}
              <span style={{ flex: 1 }} />
              {/* Per-team handout: this ONE plan's Payout Structure (per-plan variant of the all-plans doc
                  above). Read-only server-rendered PDF. */}
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }}
                onClick={() => downloadPayoutStructure(p.id)}
                title="Download this plan's Payout Structure PDF (per-team handout)">📄 Payout</button>
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => runPreview(p.id)}>👁️ Preview</button>
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => setDraft({ ...blankPlan(), ...p, base_tier_metric: p.base_tier_metric || 'none', carrier_id: p.carrier_id || '', rules: p.rules || [], tiers: p.tiers || [], assignments: p.assignments || [] })}>Edit</button>
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px', color: '#dc2626' }} onClick={() => del(p.id)}>Delete</button>
            </div>
          ))}
          {plans.length === 0 && ready && <div style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No plans yet — create one above.</div>}
        </div>
      )}

      {/* editor */}
      {draft && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>{draft.id ? '✏️ Edit plan' : '➕ New plan'}</div>
            <span style={{ flex: 1 }} />
            <button className="btn btn-secondary" onClick={() => { setDraft(null); setMsg('') }}>Cancel</button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12, marginBottom: 16 }}>
            <label style={lbl}>Plan name *<input style={sel} value={draft.name} onChange={e => upd({ name: e.target.value })} /></label>
            <label style={lbl}>Carrier
              <select style={sel} value={draft.carrier_id || ''} onChange={e => upd({ carrier_id: e.target.value })}>
                <option value="">Any / N/A</option>
                {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
            <label style={lbl}>Tier metric
              <select style={sel} value={draft.base_tier_metric || 'none'} onChange={e => upd({ base_tier_metric: e.target.value })}>
                {tierMetrics.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label style={lbl}>Activation source
              <select style={sel} value={draft.activation_source || 'inherit'}
                onChange={e => upd({ activation_source: e.target.value })}>
                <option value="inherit">Inherit (default)</option>
                <option value="raw_sales">POS sales</option>
                <option value="activation_details">Activation Details report</option>
              </select>
            </label>
            <label style={{ ...lbl, justifyContent: 'flex-end' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
                <input type="checkbox" checked={draft.is_active} onChange={e => upd({ is_active: e.target.checked })} /> Active
              </span>
            </label>
          </div>
          {/* mig 297 — one-line explainer for the Activation source dropdown above. */}
          <div style={{ fontSize: 11, color: '#64748b', marginTop: -8, marginBottom: 16 }}>
            <b>Activation source</b> controls where this plan's reps get their activations counted from.
            {' '}<b>Inherit</b> uses the org default (POS sales). <b>POS sales</b> always counts POS activations.
            {' '}<b>Activation Details report</b> pays activations from the uploaded report and suppresses POS
            activations for this plan's reps (single source, no double-count). Changes nothing until you recalculate.
          </div>

          {/* RULES */}
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Rules — which line items qualify + how they pay</div>
          <div style={{ overflowX: 'auto', marginBottom: 8 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 920 }}>
              <thead><tr>{['Label', 'Match field', 'Op', 'Value', 'Qualifies', 'Payout', 'Amount / %', 'Pays', 'Applies to', 'Tiered', ''].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
              <tbody>
                {(draft.rules || []).map((r, i) => (
                  <tr key={i}>
                    <td style={td}><input style={{ ...sel, width: 110 }} placeholder="(optional)" value={r.label || ''} onChange={e => updRule(i, { label: e.target.value })} /></td>
                    <td style={td}>
                      <select style={{ ...sel, width: 130 }} value={r.match_field} title={fieldHelp(r.match_field)}
                        onChange={e => updRule(i, { match_field: e.target.value })}>
                        {vocab.match_fields.map(f => <option key={f.value} value={f.value} title={f.help}>{f.label}</option>)}
                        {!vocab.match_fields.some(f => f.value === r.match_field) && <option value={r.match_field}>{r.match_field} (saved)</option>}
                      </select>
                    </td>
                    <td style={td}>
                      <select style={{ ...sel, width: 90 }} value={r.match_op} onChange={e => updRule(i, { match_op: e.target.value })} disabled={r.match_field === 'any'}>
                        {vocab.match_ops.map(o => <option key={o.value} value={o.value} title={o.help}>{o.label}</option>)}
                      </select>
                    </td>
                    <td style={td}>
                      {/* RULE THREE §3b: the value is PICKED from what this tenant's own sales actually
                          contain (a typo silently never matches → $0 pay). Only a 'contains' PATTERN — or a
                          list the backend had to truncate — can still be typed, and then it is checked:
                          "matches nothing" and "N lines also match rule X" (the double-pay guard) are shown
                          inline, computed exactly from the facet table. */}
                      <MatchValuePicker opts={planOpts} field={r.match_field} op={r.match_op}
                        value={r.match_value || ''} width={176}
                        onChange={v => updRule(i, { match_value: v })} />
                      {r.match_field !== 'any' && (
                        <MatchWarnings opts={planOpts} rules={matchRules} stats={matchStats} index={i} />
                      )}
                    </td>
                    <td style={{ ...td, textAlign: 'center' }}><input type="checkbox" checked={r.qualifies} onChange={e => updRule(i, { qualifies: e.target.checked })} /></td>
                    <td style={td}>
                      <select style={{ ...sel, width: 150 }} value={r.payout_kind} onChange={e => updRule(i, { payout_kind: e.target.value })}>
                        {vocab.payout_kinds.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                        {!vocab.payout_kinds.some(p => p.value === r.payout_kind) && <option value={r.payout_kind}>{r.payout_kind} (saved)</option>}
                      </select>
                    </td>
                    <td style={td}>
                      {usesPct(r.payout_kind)
                        ? <input style={{ ...sel, width: 80 }} type="number" step="0.01" placeholder="0.10" value={r.pct} onChange={e => updRule(i, { pct: Number(e.target.value) })} title="fraction, e.g. 0.10 = 10%" />
                        : <input style={{ ...sel, width: 80 }} type="number" step="0.01" placeholder="$" value={r.amount} onChange={e => updRule(i, { amount: Number(e.target.value) })} />}
                    </td>
                    <td style={td}>
                      {/* PAY GATE (mig 260): how often this rule pays on ONE sale. Only $/unit rules
                          can be deduped — collapsing a %-of-basis rule would delete real dollars. */}
                      <select style={{ ...sel, width: 118 }} value={r.unit_basis || ''}
                        disabled={r.payout_kind !== 'flat_per_unit'}
                        title={r.payout_kind !== 'flat_per_unit'
                          ? 'Only a $/unit rule can be paid once per device — a %-of-basis rule reads each line’s own price/GP/MRC.'
                          : (UNIT_BASES.find(u => u.value === (r.unit_basis || ''))?.help || '')}
                        onChange={e => updRule(i, { unit_basis: e.target.value })}>
                        {UNIT_BASES.map(u => <option key={u.value} value={u.value} title={u.help}>{u.label}</option>)}
                      </select>
                      {r.payout_kind === 'flat_per_unit' && !(r.unit_basis || '') && txnLevelFields.includes(r.match_field) && (
                        <div style={{ fontSize: 10, color: '#b45309', marginTop: 2, maxWidth: 130 }}>
                          auto → per device (this field describes the whole sale)
                        </div>
                      )}
                    </td>
                    <td style={td}>
                      {/* RULE SCOPE (mig 262): blank = everywhere, exactly as before. OWNER 2026-08-01:
                          "All activations are being paid $10 flat , this is only for NY employees, but
                          this empluee is in Chicago." Values are PICKED from the tenant's own markets /
                          stores (RULE THREE §3b) — never typed. */}
                      <select style={{ ...sel, width: 96 }} value={r.applies_scope_kind || ''}
                        onChange={e => updRule(i, { applies_scope_kind: e.target.value, applies_scope_value: '' })}>
                        <option value="">everywhere</option>
                        <option value="market">market…</option>
                        <option value="store">store…</option>
                        <option value="employee">employee…</option>
                      </select>
                      {r.applies_scope_kind === 'market' && (
                        <EntityPicker options={marketOptions(r.applies_scope_value)} value={r.applies_scope_value || null} width={150}
                          placeholder="pick market…" onChange={v => updRule(i, { applies_scope_value: v || '' })} />
                      )}
                      {r.applies_scope_kind === 'store' && (
                        <EntityPicker options={storeOptions(r.applies_scope_value)} value={r.applies_scope_value || null} width={150}
                          placeholder="pick store…" onChange={v => updRule(i, { applies_scope_value: v || '' })} />
                      )}
                      {r.applies_scope_kind === 'employee' && (
                        <EntityPicker options={employeeOptions} value={r.applies_scope_value || null} width={150}
                          placeholder="pick employee…" onChange={v => updRule(i, { applies_scope_value: v || '' })} />
                      )}
                      {!!r.applies_scope_kind && !r.applies_scope_value && (
                        <div style={{ fontSize: 10, color: '#b45309', marginTop: 2, maxWidth: 150 }}>
                          pick a value — a scope with nothing chosen is saved as “everywhere”, never as “nobody”
                        </div>
                      )}
                    </td>
                    <td style={{ ...td, textAlign: 'center' }}><input type="checkbox" checked={r.tiered} onChange={e => updRule(i, { tiered: e.target.checked })} /></td>
                    <td style={td}><button style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#dc2626' }} onClick={() => delRule(i)}>✕</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="btn btn-secondary" style={{ fontSize: 12, marginBottom: 16 }} onClick={addRule}>➕ Add rule</button>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6 }}>
            % payout uses a fraction (0.10 = 10%). pct_mrc joins raw_mi by mdn (then subscriber/serial); pct_price_over_cost uses raw_catalog cost by product_id.
            “Tiered” rules are scaled by the plan’s tier multiplier; “qualifies” lines count toward the tier metric.
          </div>
          <div style={{ marginBottom: 16 }}>
            <OptionsSourceNote opts={planOpts} />
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>
              The line counts under each value are what the rule WOULD match — they never change anyone’s pay.
              Two rules that match the same line both pay on it, so an overlap warning is worth reading before saving.
            </div>
          </div>

          {/* TIER ATTAINMENT (mig 232) — HOW the tier count is measured */}
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Tier attainment — what counts toward the tier</div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 6 }}>
            <label style={lbl}>Count basis
              <select style={{ ...sel, minWidth: 300 }} value={draft.tier_count_basis || ''}
                onChange={e => upd({ tier_count_basis: e.target.value || null })}>
                {vocab.tier_bases.map(b => <option key={b.value} value={b.value} title={b.help}>{b.label}</option>)}
              </select>
            </label>
            {!!draft.tier_count_basis && <>
              <label style={lbl}>Tier rule — field
                <select style={{ ...sel, width: 150 }} value={draft.tier_match_field || 'any'}
                  title={fieldHelp(draft.tier_match_field || 'any')}
                  onChange={e => upd({ tier_match_field: e.target.value })}>
                  {vocab.match_fields.map(f => <option key={f.value} value={f.value} title={f.help}>{f.label}</option>)}
                  {!vocab.match_fields.some(f => f.value === (draft.tier_match_field || 'any')) &&
                    <option value={draft.tier_match_field || ''}>{draft.tier_match_field} (saved)</option>}
                </select>
              </label>
              <label style={lbl}>Op
                <select style={{ ...sel, width: 90 }} value={draft.tier_match_op || 'equals'}
                  disabled={(draft.tier_match_field || 'any') === 'any'}
                  onChange={e => upd({ tier_match_op: e.target.value })}>
                  {vocab.match_ops.map(o => <option key={o.value} value={o.value} title={o.help}>{o.label}</option>)}
                </select>
              </label>
              <label style={lbl}>Value
                <MatchValuePicker opts={planOpts} field={draft.tier_match_field || 'any'}
                  op={draft.tier_match_op || 'equals'} value={draft.tier_match_value || ''} width={200}
                  ariaLabel="Tier match value" onChange={v => upd({ tier_match_value: v })} />
                {(draft.tier_match_field || 'any') !== 'any' && tierCount && (
                  <span style={{ fontSize: 10.5, color: tierCount.lines === 0 ? '#b45309' : 'var(--text3)' }}>
                    {tierCount.lines === 0
                      ? `⚠ matches nothing in the last ${planOpts?.window?.months || 3} months — the tier would count 0`
                      : `${tierCount.lines.toLocaleString()} line${tierCount.lines === 1 ? '' : 's'} match this tier rule`}
                  </span>
                )}
              </label>
            </>}
            <label style={lbl}>Below lowest tier ×
              <input style={{ ...sel, width: 110 }} type="number" step="0.01" placeholder="1.0 (default)"
                value={draft.tier_below_min_multiplier ?? ''}
                onChange={e => upd({ tier_below_min_multiplier: e.target.value === '' ? null : Number(e.target.value) })} />
            </label>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 16, maxWidth: 900 }}>
            <b>Count basis</b> decides what the tier counts. The legacy default counts every qualifying
            rule-matched <b>line</b>, summed across rules — one activation that rings a device + a plan + a
            SIM counts as 3, and a line matched by two rules counts twice. Pick
            <b> distinct transactions</b> with a tier rule (e.g. <code>activation_bucket in premium,byod</code>)
            to make “30 activations” mean 30 activations. <b>Below lowest tier ×</b> is what a rep who reaches
            NO tier gets — blank keeps the historic behaviour of full (1.0×) pay.
            Changes take effect on the next Run Calculation.
          </div>

          {/* TIERS */}
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Tiers — qualifying-unit count → multiplier {draft.base_tier_metric === 'none' && <span style={{ fontWeight: 400, color: 'var(--text3)' }}>(set a tier metric above to use)</span>}</div>
          <div style={{ overflowX: 'auto', marginBottom: 8 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', maxWidth: 520 }}>
              <thead><tr>{['Metric', 'Min count', 'Multiplier', ''].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
              <tbody>
                {(draft.tiers || []).map((t, i) => (
                  <tr key={i}>
                    <td style={td}>
                      <select style={{ ...sel, width: 130 }} value={t.metric || draft.base_tier_metric || 'none'} onChange={e => updTier(i, { metric: e.target.value })}>
                        {tierMetrics.filter(m => m !== 'none' || (t.metric || draft.base_tier_metric || 'none') === 'none')
                          .map(m => <option key={m} value={m}>{m}</option>)}
                      </select>
                    </td>
                    <td style={td}><input style={{ ...sel, width: 90 }} type="number" value={t.min_count} onChange={e => updTier(i, { min_count: Number(e.target.value) })} /></td>
                    <td style={td}><input style={{ ...sel, width: 90 }} type="number" step="0.01" value={t.multiplier} onChange={e => updTier(i, { multiplier: Number(e.target.value) })} /></td>
                    <td style={td}><button style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#dc2626' }} onClick={() => delTier(i)}>✕</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="btn btn-secondary" style={{ fontSize: 12, marginBottom: 16 }} onClick={addTier}>➕ Add tier</button>

          {/* ASSIGNMENTS */}
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Assignments — who this plan applies to <span style={{ fontWeight: 400, color: 'var(--text3)' }}>(precedence employee &gt; role &gt; store &gt; market &gt; default — a more specific scope overrides a broader one)</span></div>
          <div style={{ overflowX: 'auto', marginBottom: 8 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', maxWidth: 620 }}>
              <thead><tr>{['Scope', 'Value', 'Priority', ''].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
              <tbody>
                {(draft.assignments || []).map((a, i) => (
                  <tr key={i}>
                    <td style={td}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <ScopeBadge scope={a.scope} />
                        <select style={{ ...sel, width: 104 }} value={a.scope} onChange={e => updAssign(i, { scope: e.target.value, scope_value: '' })}>
                          {['employee', 'role', 'store', 'market', 'default'].map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                      </div>
                    </td>
                    <td style={td}>
                      {a.scope === 'employee' ? (
                        <EntityPicker options={employeeOptions} value={a.scope_value || null} width={240}
                          placeholder="pick employee…" onChange={v => updAssign(i, { scope_value: v || '' })} />
                      ) : a.scope === 'role' ? (
                        <EntityPicker options={roleOptions} value={a.scope_value || null} width={240}
                          placeholder="pick role — assigns all reps with it…" onChange={v => updAssign(i, { scope_value: v || '' })} />
                      ) : a.scope === 'store' ? (
                        <EntityPicker options={storeOptions(a.scope_value)} value={a.scope_value || null} width={240}
                          placeholder="pick store…" onChange={v => updAssign(i, { scope_value: v || '' })} />
                      ) : a.scope === 'market' ? (
                        <EntityPicker options={marketOptions(a.scope_value)} value={a.scope_value || null} width={240}
                          placeholder="pick market…" onChange={v => updAssign(i, { scope_value: v || '' })} />
                      ) : <span style={{ fontSize: 12, color: 'var(--text3)' }}>all reps (fallback)</span>}
                    </td>
                    <td style={td}><input style={{ ...sel, width: 70 }} type="number" value={a.priority || 0} onChange={e => updAssign(i, { priority: Number(e.target.value) })} /></td>
                    <td style={td}><button style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#dc2626' }} onClick={() => delAssign(i)}>✕</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="btn btn-secondary" style={{ fontSize: 12, marginBottom: 16 }} onClick={addAssign}>➕ Add assignment</button>

          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <input style={{ ...sel, flex: 1 }} placeholder="Notes" value={draft.notes || ''} onChange={e => upd({ notes: e.target.value })} />
            <button className="btn btn-primary" disabled={busy} onClick={save}>💾 Save plan</button>
          </div>
          {msg && <div style={{ fontSize: 13, marginTop: 8 }}>{msg}</div>}
        </div>
      )}

      {/* PREVIEW */}
      <div className="card" style={{ padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>👁️ Preview <span style={{ fontWeight: 400, fontSize: 12, color: '#b45309' }}>(read-only — does NOT change live incentives)</span></div>
          <span style={{ flex: 1 }} />
          <EntityPicker options={periodOptions} value={period || null} width={170} allowCreate clearable={false}
            placeholder="pick a period…" onChange={v => setPeriod(v || '')} onCreate={v => setPeriod(v)}
            createLabel={v => `Use “${v}”`} ariaLabel="Period" />
          <button className="btn btn-secondary" disabled={previewBusy} onClick={() => runPreview(draft?.id)}>{previewBusy ? '…' : draft?.id ? 'Preview this plan' : 'Preview (per assignment)'}</button>
          {preview?.by_rep?.length > 0 && <><ExportButtons payload={previewPayload} /><SendReportButton exportPayload={previewPayload} compact /></>}
        </div>
        {preview && (
          preview.ready === false ? <div style={{ fontSize: 13, color: '#b45309' }}>{preview.note || 'Migration 059 not applied.'}</div>
          : preview.by_rep?.length === 0 ? <div style={{ fontSize: 13, color: 'var(--text3)' }}>{preview.note || 'No payout for this period (no matching sales / no plan resolved).'}</div>
          : (
            <>
              <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 8 }}>
                {preview.totals?.reps} reps · {fmt(preview.totals?.payout || 0)} total · {preview.totals?.sale_lines} sale lines · period {preview.period}
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr>{['Rep', 'Store', 'Plan', 'Qual. units', 'Tier ×', 'Base', 'Tiered', 'Total'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                  <tbody>
                    {preview.by_rep.map((row: any, i: number) => (
                      <PreviewRow key={i} row={row} />
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )
        )}
        {!preview && <div style={{ fontSize: 13, color: 'var(--text3)' }}>Enter a period and Preview to see what a plan would pay.</div>}
      </div>

      {/* ── PAY GATE (mig 260) — how often does a rule pay on ONE sale? ────────────────────────────
          OWNER 2026-08-01: one financed sale paid 8 x $25 because the rule keys on the TENDER (which
          the POS stamps on every line of the receipt) and "$/unit" meant "per matching LINE". The
          settings below are the tenant's; the impact panel quotes the real engine, twice, before and
          after, and writes nothing. */}
      <div className="card" style={{ padding: 16, marginTop: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>🧾 Pay gate — how often a rule pays on one sale
            <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text3)' }}> (money-touching · takes effect on the next Calculate)</span>
          </div>
          <span style={{ flex: 1 }} />
          <button className="btn btn-secondary" disabled={impactBusy} onClick={runImpact}>{impactBusy ? '…' : `Check impact for ${period}`}</button>
          <button className="btn btn-secondary" disabled={auditBusy} onClick={runAudit}>{auditBusy ? '…' : 'Which rules multiply?'}</button>
        </div>
        {gate?.config?.unit_basis && (
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 10 }}>
            <label style={{ fontSize: 12 }}>
              <div style={{ color: 'var(--text3)', marginBottom: 2 }}>One payment per…</div>
              <select style={sel} value={gate.config.unit_basis.default_basis} disabled={gateBusy}
                onChange={e => saveGate({ ...gate.config, unit_basis: { ...gate.config.unit_basis, default_basis: e.target.value } })}>
                <option value="per_device">device (owner rule: one IMEI, one payment)</option>
                <option value="per_transaction">sale</option>
                <option value="per_line">line (no dedup)</option>
              </select>
            </label>
            <label style={{ fontSize: 12 }}>
              <div style={{ color: 'var(--text3)', marginBottom: 2 }}>…for $/unit rules keyed on</div>
              <select style={sel} value={(gate.config.unit_basis.auto_txn_level_fields || []).join(',')} disabled={gateBusy}
                onChange={e => saveGate({ ...gate.config, unit_basis: { ...gate.config.unit_basis, auto_txn_level_fields: e.target.value ? e.target.value.split(',') : [] } })}>
                <option value="tender_type">tender type (the sale's payment method)</option>
                <option value="tender_type,trans_type">tender type + transaction type</option>
                <option value="">nothing — never auto-dedup</option>
              </select>
            </label>
            <label style={{ fontSize: 12 }}>
              <div style={{ color: 'var(--text3)', marginBottom: 2 }}>If no line has a device serial</div>
              <select style={sel} value={gate.config.unit_basis.no_unit_fallback} disabled={gateBusy}
                onChange={e => saveGate({ ...gate.config, unit_basis: { ...gate.config.unit_basis, no_unit_fallback: e.target.value } })}>
                <option value="once_per_transaction">pay once for the sale (and warn)</option>
                <option value="skip">pay nothing (and warn)</option>
              </select>
            </label>
            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={!!gate.config.unit_basis.enabled} disabled={gateBusy}
                onChange={e => saveGate({ ...gate.config, unit_basis: { ...gate.config.unit_basis, enabled: e.target.checked } })} />
              <span>Dedup enabled</span>
            </label>
            {gate.is_default && <span style={{ fontSize: 11, color: 'var(--text3)' }}>(showing the code defaults — nothing saved for this tenant yet)</span>}
          </div>
        )}
        {!gate && <div style={{ fontSize: 13, color: 'var(--text3)' }}>Pay-gate settings unavailable (the endpoint did not answer). The code defaults are in force.</div>}
        {impact && !impact.error && (
          <div style={{ marginTop: 6 }}>
            <div style={{ fontSize: 12.5, marginBottom: 6 }}>
              <b>{period}</b>: total <b>{fmt(impact.totals?.before)}</b> before → <b>{fmt(impact.totals?.after)}</b> after
              (<b style={{ color: (impact.totals?.delta || 0) < 0 ? '#b91c1c' : '#15803d' }}>{fmt(impact.totals?.delta)}</b>),
              {' '}{impact.by_rep?.length || 0} rep(s) move, {impact.reps_unchanged} unchanged.
            </div>
            {!!(impact.by_rep || []).length && (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead><tr>{['Rep', 'Before', 'After', 'Delta'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                  <tbody>
                    {(impact.by_rep || []).map((r: any, i: number) => (
                      <tr key={i}>
                        <td style={td}>{r.rep}</td><td style={td}>{fmt(r.before)}</td><td style={td}>{fmt(r.after)}</td>
                        <td style={{ ...td, color: r.delta < 0 ? '#b91c1c' : '#15803d', fontWeight: 700 }}>{fmt(r.delta)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {!!impact.pay_gate?.unit?.notes?.length && (
              <ul style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 6, paddingLeft: 18 }}>
                {impact.pay_gate.unit.notes.slice(0, 12).map((n: any, i: number) => <li key={i}>{n.rep} — {n.detail}</li>)}
              </ul>
            )}
          </div>
        )}
        {impact?.error && <div style={{ fontSize: 12.5, color: '#b91c1c' }}>{impact.error}</div>}
        {audit && !audit.error && (
          <div style={{ marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
            <div style={{ fontSize: 12.5, marginBottom: 4 }}>
              <b>$/unit rules that pay more than once on one transaction</b> — {audit.totals?.rules} rule(s),
              {' '}{audit.totals?.transactions} transaction(s), {fmt(audit.totals?.extra_amount)} of extra payments.
            </div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6 }}>{audit.note}</div>
            {!!(audit.rules || []).length && (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead><tr>{['Rule', 'Keyed on', 'Txns', 'Extra lines', 'Extra $', 'Deduped now?'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                <tbody>
                  {(audit.rules || []).map((r: any, i: number) => (
                    <tr key={i}>
                      <td style={td}>{r.label || r.rule_id}</td>
                      <td style={td}>{r.match_field} {r.match_op} “{r.match_value ?? ''}”</td>
                      <td style={td}>{r.transactions}</td><td style={td}>{r.extra_lines}</td>
                      <td style={{ ...td, fontWeight: 700 }}>{fmt(r.extra_amount)}</td>
                      <td style={td}>{r.auto_deduped ? '✅ yes' : (r.unit_basis && r.unit_basis !== 'per_line' ? '✅ by rule' : '— no')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
        {audit?.error && <div style={{ fontSize: 12.5, color: '#b91c1c' }}>{audit.error}</div>}

        {/* ── PAYOUT EXCLUSIONS (mig 261) ────────────────────────────────────────────────────────
            OWNER 2026-08-01: "there shgould be no paymentfor any rtr trasactions … but with mapping
            … let the user define going forward". A class of line that never pays, whatever a rule
            says — because a rule with "qualifies" off only stops ITS OWN payment (the plan engine has
            no exclusivity), so this could not be expressed in the existing config at all. */}
        <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
            <div style={{ fontWeight: 700, fontSize: 13 }}>🚫 Never pay these lines</div>
            <span style={{ flex: 1 }} />
            <button className="btn btn-secondary" disabled={exclImpactBusy} onClick={runExclImpact}>{exclImpactBusy ? '…' : `What would this exclude in ${period}?`}</button>
          </div>
          {excl?.ready === false && (
            <div style={{ fontSize: 11.5, color: '#92400e', background: '#fffbeb', border: '1px solid #fcd34d', borderRadius: 6, padding: '6px 8px', marginBottom: 6 }}>
              Run <b>{excl.migration}</b> to make this list editable. The built-in mapping below is already in force.
            </div>
          )}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, marginBottom: 6 }}>
            <thead><tr>{['What', 'Field', 'Match', 'Value', 'On', 'Source', ''].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
            <tbody>
              {(excl?.rules || []).map((r: any, i: number) => (
                <tr key={i}>
                  <td style={td}>{r.label || r.code || '—'}</td>
                  <td style={td}>{r.match_field}</td>
                  <td style={td}>{r.match_op}{r.match_op === 'word' && <span title="matches the whole token only — a substring match on a short token would hit unrelated products (‘contains RTR’ also matches CARTRIDGE)" style={{ marginLeft: 4, color: '#2563eb' }}>ⓘ</span>}</td>
                  <td style={td}><code>{r.match_value}</code></td>
                  <td style={td}>{r.enabled ? '✅' : '—'}{r.status === 'proposed' ? ' (proposed)' : ''}</td>
                  <td style={td}>{r.source === 'seed' ? 'built-in' : 'yours'}</td>
                  <td style={td}>
                    {r.source === 'seed'
                      ? <button className="btn btn-secondary" style={{ fontSize: 11, padding: '1px 8px' }} disabled={exclSaving}
                          onClick={() => saveExclusion({ ...r, enabled: false, source: 'tenant' })}>switch off</button>
                      : <button style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#dc2626' }} disabled={exclSaving}
                          onClick={() => deleteExclusion(r.id)}>✕</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <select style={{ ...sel, width: 140 }} value={exclDraft.match_field}
              onChange={e => setExclDraft({ ...exclDraft, match_field: e.target.value, match_value: '' })}>
              {(excl?.match_fields || ['product_desc']).map((f: string) => <option key={f} value={f}>{f}</option>)}
            </select>
            <select style={{ ...sel, width: 110 }} value={exclDraft.match_op}
              onChange={e => setExclDraft({ ...exclDraft, match_op: e.target.value })}>
              {(excl?.match_ops || ['word']).map((o: string) => <option key={o} value={o}>{o}</option>)}
            </select>
            {/* RULE THREE §3b — the value is PICKED from what this tenant's sales actually contain. */}
            <MatchValuePicker opts={planOpts} field={exclDraft.match_field} op={exclDraft.match_op === 'word' ? 'contains' : exclDraft.match_op}
              value={exclDraft.match_value || ''} width={220}
              onChange={(v: string) => setExclDraft({ ...exclDraft, match_value: v })} />
            <input style={{ ...sel, width: 150 }} placeholder="what to call it" value={exclDraft.label}
              onChange={e => setExclDraft({ ...exclDraft, label: e.target.value })} />
            <input style={{ ...sel, flex: 1, minWidth: 180 }} placeholder="reason shown on the drill-down" value={exclDraft.reason}
              onChange={e => setExclDraft({ ...exclDraft, reason: e.target.value })} />
            <button className="btn btn-secondary" disabled={exclSaving || !exclDraft.match_value}
              onClick={() => saveExclusion(exclDraft)}>➕ Add</button>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>
            “word” matches the token and never a substring — use it for short codes. A built-in mapping switched off stays visible and can be switched back on.
          </div>
          {exclImpact && !exclImpact.error && (
            <div style={{ marginTop: 8, fontSize: 12.5 }}>
              <b>{period}</b>: {exclImpact.excluded_lines} line(s) would stop paying,
              {' '}{fmt(exclImpact.excluded_amount)} total ({fmt(exclImpact.totals?.delta)} across all reps).
              {!!(exclImpact.samples || []).length && (
                <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 11.5, color: 'var(--text3)' }}>
                  {exclImpact.samples.slice(0, 10).map((s: any, i: number) => (
                    <li key={i}>{s.date} · trans {s.trans_id} · {s.rep} · {s.product} — {fmt(s.would_have_paid)}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {exclImpact?.error && <div style={{ fontSize: 12.5, color: '#b91c1c' }}>{exclImpact.error}</div>}
        </div>

        {/* ── ACCESSORY %-OF-GP BASIS GUARD (mig 260) ────────────────────────────────────────────
            OWNER 2026-08-01: "accessories not being paid , they should be paid as all of these have
            been mapped". Their GP is $0 because the POS catalog carries cost == retail on the "* BYOD"
            class, so a %-of-GP payout is $0 by arithmetic — and three lines pay NEGATIVE. OFF by
            default fleet-wide: switching it on is an explicit tenant decision, previewed first. */}
        <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
            <div style={{ fontWeight: 700, fontSize: 13 }}>🎧 Accessories whose GP is unusable</div>
            <span style={{ flex: 1 }} />
            <button className="btn btn-secondary" disabled={accBusy} onClick={runAccImpact}>{accBusy ? '…' : `Preview for ${period}`}</button>
          </div>
          {gate?.config?.accessory_basis_guard && (
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 6 }}>
              <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                <input type="checkbox" checked={!!gate.config.accessory_basis_guard.enabled} disabled={gateBusy}
                  onChange={e => saveGate({ ...gate.config, accessory_basis_guard: { ...gate.config.accessory_basis_guard, enabled: e.target.checked } })} />
                <span>Pay the rate on the PRICE when the GP is not believable</span>
              </label>
              <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                <input type="checkbox" checked={gate.config.accessory_basis_guard.clamp_negative !== false} disabled={gateBusy}
                  onChange={e => saveGate({ ...gate.config, accessory_basis_guard: { ...gate.config.accessory_basis_guard, clamp_negative: e.target.checked } })} />
                <span>Never pay a negative accessory line</span>
              </label>
              <label style={{ fontSize: 12 }}>
                <div style={{ color: 'var(--text3)', marginBottom: 2 }}>Assumed margin (blank = full price)</div>
                <input style={{ ...sel, width: 100 }} placeholder="e.g. 0.35" disabled={gateBusy}
                  defaultValue={gate.config.accessory_basis_guard.assumed_margin_pct ?? ''}
                  onBlur={e => saveGate({ ...gate.config, accessory_basis_guard: { ...gate.config.accessory_basis_guard, assumed_margin_pct: e.target.value === '' ? null : Number(e.target.value) } })} />
              </label>
            </div>
          )}
          {accImpact && !accImpact.error && (
            <div style={{ fontSize: 12.5 }}>
              <b>{period}</b>: {accImpact.lines_changed} accessory line(s) would move from
              {' '}<b>{fmt(accImpact.amount_before)}</b> to <b>{fmt(accImpact.amount_after)}</b>
              {' '}(total {fmt(accImpact.totals?.delta)} across all reps).
              {accImpact.hypothesis_note && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{accImpact.hypothesis_note}</div>}
              {accImpact.accessory_definition_loaded === false && (
                <div style={{ fontSize: 11.5, color: '#92400e', marginTop: 4 }}>
                  No accessory definition is mapped for this tenant yet, so the guard has nothing to act on.
                  Map the products under <a href="/commcalc/accessory-definition" style={{ color: 'var(--accent)' }}>Accessory Definition</a> first.
                </div>
              )}
              {!!(accImpact.samples || []).length && (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, marginTop: 6 }}>
                  <thead><tr>{['Rep', 'Product', 'Price', 'GP', 'Was', 'Would be', 'Why'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                  <tbody>
                    {accImpact.samples.slice(0, 20).map((s: any, i: number) => (
                      <tr key={i}>
                        <td style={td}>{s.rep}</td><td style={td}>{s.product}</td>
                        <td style={td}>{fmt(s.ext_price)}</td><td style={td}>{fmt(s.gp)}</td>
                        <td style={td}>{fmt(s.was)}</td>
                        <td style={{ ...td, fontWeight: 700, color: '#15803d' }}>{fmt(s.now)}</td>
                        <td style={{ ...td, fontSize: 11, color: 'var(--text3)' }}>{s.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{accImpact.note}</div>
            </div>
          )}
          {accImpact?.error && <div style={{ fontSize: 12.5, color: '#b91c1c' }}>{accImpact.error}</div>}
        </div>
      </div>

      {/* ── SET-UP / ACTIVATION FEE (mig 263) ──────────────────────────────────────────────────
          OWNER 2026-08-01: "the device set up fee is the same as activation fee on luxelink , an
          option should be there in commission payout if this has to be a part of commission and what
          % is used to pay out comp … if criclet delaer uses metrics pro they should be able to design
          based on their payouts". One pay concept, per-carrier names and per-carrier numbers. */}
      <div className="card" style={{ padding: 16, marginTop: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>🧾 Set-up / activation fee
            <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text3)' }}> (money-touching · takes effect on the next Calculate)</span>
          </div>
          <span style={{ flex: 1 }} />
          <button className="btn btn-secondary" disabled={sfCandBusy} onClick={loadSfCandidates}>{sfCandBusy ? '…' : `Which line is it? (${period})`}</button>
        </div>

        {sf && (<>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>
            Currently recognised as the fee:{' '}
            {(sf.keywords || []).map((k: string) => (
              <code key={k} style={{ background: 'var(--bg2)', padding: '1px 5px', borderRadius: 4, marginRight: 4 }}>{k}</code>
            ))}
            {sf.keywords_are_default && <span style={{ marginLeft: 6, color: '#b45309' }}>← the built-in default ({showBoost ? 'Boost' : 'default'} wording). Map your own below if your POS calls it something else.</span>}
          </div>

          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 8 }}>
            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={!!sf.config?.default?.include_in_commission} disabled={sfBusy}
                onChange={e => saveSf({ ...sf.config, default: { ...sf.config.default, include_in_commission: e.target.checked } })} />
              <span>Part of employee incentive</span>
            </label>
            <label style={{ fontSize: 12 }}>
              <div style={{ color: 'var(--text3)', marginBottom: 2 }}>Employee % of fee collected</div>
              <input style={{ ...sel, width: 110 }} placeholder="e.g. 0.10" disabled={sfBusy}
                defaultValue={sf.config?.default?.employee_pct_of_collected ?? ''}
                onBlur={e => saveSf({ ...sf.config, default: { ...sf.config.default, employee_pct_of_collected: e.target.value === '' ? null : Number(e.target.value) } })} />
            </label>
            <label style={{ fontSize: 12 }}>
              <div style={{ color: 'var(--text3)', marginBottom: 2 }}>Dealer share (carrier pays dealer)</div>
              <input style={{ ...sel, width: 110 }} placeholder="e.g. 0.50" disabled={sfBusy}
                defaultValue={sf.config?.default?.dealer_share_pct ?? ''}
                onBlur={e => saveSf({ ...sf.config, default: { ...sf.config.default, dealer_share_pct: e.target.value === '' ? null : Number(e.target.value) } })} />
            </label>
            <div style={{ fontSize: 11, color: 'var(--text3)', maxWidth: 320 }}>
              Fractions (0.10 = 10%). Leaving the employee % <b>blank</b> means “not decided yet”: the fee pays $0
              and the next calculation says so by name. It is never read as 0%.
            </div>
          </div>

          {sf.owner_reference && (
            <div style={{ fontSize: 11.5, color: 'var(--text3)', marginBottom: 8 }}>
              <>For reference (owner, 2026-08-01, <b>not applied</b>): {isTotalCarrier
                ? 'the carrier pays the dealer 50% of the activation fee and the employee 0% today.'
                : 'the carrier pays the dealer 100% of the set-up fee and the employee 10%.'}</>
            </div>
          )}

          {!!(sf.carriers || []).length && (
            <div style={{ fontSize: 11.5, color: 'var(--text3)', marginBottom: 8 }}>
              {multi
                ? <>A plan’s carrier picks its own numbers, so each carrier can carry its own set-up-fee split.</>
                : <>Per-carrier overrides available for: {(sf.carriers || []).map((c: any) => c.name).join(', ')} — a plan’s carrier picks its own numbers, so each carrier can carry its own set-up-fee split.</>}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 6 }}>
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>Preview at</span>
            <input style={{ ...sel, width: 90 }} placeholder="0.10" value={sfPctDraft} onChange={e => setSfPctDraft(e.target.value)} />
            <button className="btn btn-secondary" disabled={sfImpactBusy} onClick={runSfImpact}>{sfImpactBusy ? '…' : `Preview ${period}`}</button>
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>Runs the real engine twice. Writes nothing, recalculates nothing.</span>
          </div>
        </>)}
        {!sf && <div style={{ fontSize: 13, color: 'var(--text3)' }}>Set-up-fee settings unavailable (the endpoint did not answer). The code defaults pay nobody.</div>}

        {sfCand && !sfCand.error && (
          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 12.5, marginBottom: 4 }}>
              <b>Your own product descriptions in {period}</b>, ranked by the money they carry. Pick the one your POS uses — nothing is chosen for you.
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead><tr>{['Product', 'Lines', 'Txns', 'Collected $', 'First', 'Last', ''].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                <tbody>
                  {(sfCand.candidates || []).map((c: any, i: number) => (
                    <tr key={i} style={{ opacity: c.collects_money ? 1 : 0.55 }}>
                      <td style={td}>{c.product_desc}</td>
                      <td style={td}>{c.lines}</td>
                      <td style={td}>{c.transactions}</td>
                      <td style={{ ...td, fontWeight: 700 }}>{fmt(c.ext_price)}</td>
                      <td style={td}>{c.first}</td><td style={td}>{c.last}</td>
                      <td style={td}>
                        {c.mapped_now
                          ? <span style={{ color: '#15803d', fontWeight: 700 }}>✓ mapped</span>
                          : c.collects_money
                            ? <button className="btn btn-secondary" style={{ fontSize: 11, padding: '1px 8px' }} disabled={sfBusy}
                                onClick={() => mapKeyword(c.product_desc)}>map as the fee</button>
                            : <span title="every line of this product sold for $0 — it collects nothing, so mapping it would pay nobody" style={{ fontSize: 11, color: 'var(--text3)' }}>collects $0</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{sfCand.note}</div>
          </div>
        )}
        {sfCand?.error && <div style={{ fontSize: 12.5, color: '#b91c1c' }}>{sfCand.error}</div>}

        {sfImpact && !sfImpact.error && (
          <div style={{ marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
            <div style={{ fontSize: 12.5, marginBottom: 4 }}>
              <b>{period}</b>: {fmt(sfImpact.collected_total)} collected over {sfImpact.collected_lines} line(s);
              employees paid <b>{fmt(sfImpact.paid_total)}</b>
              {sfImpact.dealer_share !== null && sfImpact.dealer_share !== undefined && <> · dealer share {fmt(sfImpact.dealer_share)}</>}.
            </div>
            {sfImpact.hypothesis_note && <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4 }}>{sfImpact.hypothesis_note}</div>}
            {!!(sfImpact.warnings || []).length && (
              <ul style={{ fontSize: 11.5, color: '#92400e', paddingLeft: 18, margin: '4px 0' }}>
                {sfImpact.warnings.slice(0, 8).map((w: any, i: number) => <li key={i}>{w.message}</li>)}
              </ul>
            )}
            {!!(sfImpact.by_rep || []).length && (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead><tr>{['Rep', 'Fee collected', 'Lines', 'Pay now', 'At this %', 'Delta'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                <tbody>
                  {sfImpact.by_rep.map((r: any, i: number) => (
                    <tr key={i}>
                      <td style={td}>{r.rep}</td><td style={td}>{fmt(r.collected)}</td><td style={td}>{r.lines}</td>
                      <td style={td}>{fmt(r.now)}</td><td style={td}>{fmt(r.with_pct)}</td>
                      <td style={{ ...td, fontWeight: 700, color: r.delta > 0 ? '#15803d' : 'var(--text3)' }}>{fmt(r.delta)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{sfImpact.note}</div>
          </div>
        )}
        {sfImpact?.error && <div style={{ fontSize: 12.5, color: '#b91c1c' }}>{sfImpact.error}</div>}
      </div>

      {/* PLAN COVERAGE — why isn't the plan paying what I configured? (read-only diagnostic, mig 232) */}
      <div className="card" style={{ padding: 16, marginTop: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>🩺 Plan coverage <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text3)' }}>(read-only — who is uncovered, which lines no rule matched, why a tier didn’t move pay)</span></div>
          <span style={{ flex: 1 }} />
          <button className="btn btn-primary" onClick={() => setWizOpen(true)} title="Guided step-by-step fix: attach plans + add owner-authored rules so unpaid lines pay. Writes nothing until you Apply.">▶ Fix coverage (wizard)</button>
          <button className="btn btn-secondary" disabled={covBusy} onClick={runCoverage}>{covBusy ? '…' : `Check ${period}`}</button>
          {cov?.coverage && <><ExportButtons payload={coveragePayload} /><SendReportButton exportPayload={coveragePayload} compact /></>}
        </div>
        {!cov && <div style={{ fontSize: 13, color: 'var(--text3)' }}>Uses the period above. Nothing is written and no calculation is triggered.</div>}
        {cov && (<>
          {cov.snapshot?.stale && (
            <div style={{ background: '#fef3c7', border: '1px solid #fcd34d', borderRadius: 8, padding: '8px 10px', fontSize: 12.5, marginBottom: 10 }}>
              ⚠️ <b>Stored snapshot is stale.</b> The plans compute {fmt(cov.snapshot.engine_total)} for {cov.period} but
              the saved incentive rows total {fmt(cov.snapshot.stored_total)} across {cov.snapshot.stored_rows} rows.
              The incentive pages show the SAVED numbers — run <b>Calculate</b> for this period to apply the current configuration.
            </div>
          )}
          {(cov.coverage?.plan_warnings || []).length > 0 && (
            <div style={{ marginBottom: 10 }}>
              {cov.coverage.plan_warnings.map((w: any, i: number) => (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 12.5, padding: '5px 0', borderTop: i ? '1px solid var(--border)' : undefined }}>
                  <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 8, whiteSpace: 'nowrap', background: w.severity === 'high' ? '#fee2e2' : '#fef3c7', color: w.severity === 'high' ? '#991b1b' : '#92400e' }}>{w.severity}</span>
                  <span>{w.message}</span>
                </div>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 12.5, marginBottom: 10 }}>
            <span>carrier mode: <b>{cov.carrier_mode}</b></span>
            <span>reps paid: <b>{cov.totals?.reps ?? 0}</b></span>
            <span>sellers with NO plan: <b style={{ color: (cov.coverage?.unassigned_count || 0) ? '#b91c1c' : undefined }}>{cov.coverage?.unassigned_count ?? 0}</b> ({fmt(cov.coverage?.unassigned_ext_price || 0)} of sales)</span>
            <span>lines no rule matched: <b>{cov.coverage?.unmatched?.total_lines ?? 0}</b></span>
            <span>blank Contract Type: <b>{cov.coverage?.contract_type?.blank ?? 0}</b> / {cov.coverage?.contract_type?.sale_lines ?? 0} ({cov.coverage?.contract_type?.blank_pct ?? 0}%)</span>
          </div>
          {/* the three OTHER halves of the same story: assignments attached to nobody, the store→market
              bridge, and the sellers this tenant has confirmed are not people */}
          <OrphanAssignments rows={cov.coverage?.orphan_assignments || []} />
          <StoreBridgePanel stores={cov.coverage?.stores} />
          <ExcludedSellers cov={cov.coverage} busy={exclBusy}
            onChange={saveExcluded} />
          {(cov.coverage?.unassigned_reps || []).length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>Sellers with sales but NO plan attached — these reps pay $0</div>
              <div style={{ fontSize: 11.5, color: 'var(--text2)', marginBottom: 4 }}>
                Expand a row (▸) to see exactly which bridge failed — the roster NAME match, an assignment
                saved under a different spelling, or the store→market lookup — and fix it from there.
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr>{['Rep', 'Store', 'Market', 'Role', 'Txns', 'Lines', 'Sales $', 'What to do'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                  <tbody>{cov.coverage.unassigned_reps.map((u: any, i: number) => (
                    <UnassignedRow key={i} u={u} people={roster.people} busy={exclBusy || covBusy}
                      onLinked={loadRoster}
                      onExclude={(rep: string) => saveExcluded([...excludedNow, rep])} />
                  ))}</tbody>
                </table>
              </div>
            </div>
          )}
          {(cov.by_rep || []).length > 0 && (
            <div>
              <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 4 }}>Covered reps — tier attainment + unmatched lines</div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr>{['Rep', 'Plan', 'Tier count', 'Basis', 'Tier ×', 'Total', 'Lines no rule matched', '$ unmatched'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                  <tbody>{cov.by_rep.map((r: any, i: number) => (
                    <tr key={i}><td style={td}>{r.rep}</td><td style={td}>{r.plan_name}</td>
                      <td style={td}>{r.tier_units}</td><td style={td}>{r.tier_basis}</td>
                      <td style={td}>{r.tier_multiplier}×</td><td style={td}>{fmt(r.total_payout)}</td>
                      <td style={{ ...td, color: (r.unmatched_lines || 0) ? '#b45309' : undefined }}>{r.unmatched_lines}</td>
                      <td style={td}>{fmt(r.unmatched_ext_price || 0)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          )}
          {/* Part C — every line NOT considered for commission, from the pay engine itself. The guided
              "Fix coverage (wizard)" button above is now the primary way to act on this; the full detailed
              table is kept here, collapsed, as the advanced view. */}
          <details style={{ marginTop: 12 }}>
            <summary style={{ cursor: 'pointer', fontWeight: 700, fontSize: 13 }}>Advanced: full “lines not paying” table</summary>
            <div style={{ marginTop: 8 }}>
              <UnmatchedExplorer period={cov.period || period} />
            </div>
          </details>
        </>)}
      </div>
      <CoverageWizard open={wizOpen} onClose={() => setWizOpen(false)} period={cov?.period || period}
        plans={plans} onApplied={() => { runCoverage(); load() }} />
      </>)}

      {tab === 'bulk' && (
        <div>
          <div className="card" style={{ padding: 14, marginBottom: 14 }}>
            <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>👥 Assign a plan to many people at once</div>
            <div style={{ fontSize: 12.5, color: 'var(--text2)' }}>
              Filter by market / role / name, tick the people (or <strong>select-all</strong> the filtered set),
              pick a plan, and apply it to everyone checked in one action. Each person shows their
              <strong> role</strong>, <strong>market</strong> and <strong>current plan</strong> so you can see who
              already has what before overwriting. This only sets WHO is on WHICH plan — no pay is recomputed until
              you run <em>Calculate</em>.
            </div>
          </div>

          {/* standardized filter bar (RULE FIVE) — market · role · name, pick-don't-type (§3b) */}
          <div className="card" style={{ padding: 12, marginBottom: 12, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <label style={lbl}>Market
              <EntityPicker multi width={230} placeholder="All markets…" clearable
                options={roster.markets.map(m => ({ id: m, label: m }))}
                value={fMarkets} onChange={setFMarkets} ariaLabel="Filter by market" />
            </label>
            <label style={lbl}>Role
              <EntityPicker multi width={230} placeholder="All roles…" clearable
                options={roster.roles.map(r => ({ id: r, label: r }))}
                value={fRoles} onChange={setFRoles} ariaLabel="Filter by role" />
            </label>
            <label style={lbl}>Name / email
              <input style={{ ...sel, width: 200 }} placeholder="type to filter…" value={nameQuery}
                onChange={e => setNameQuery(e.target.value)} aria-label="Filter by name or email" />
            </label>
            <label style={{ ...lbl, flexDirection: 'row', alignItems: 'center', gap: 6, fontWeight: 600 }}>
              <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} /> Show inactive
            </label>
            {(fMarkets.length > 0 || fRoles.length > 0 || nameQuery) && (
              <button className="btn btn-secondary" style={{ fontSize: 12 }}
                onClick={() => { setFMarkets([]); setFRoles([]); setNameQuery('') }}>Clear filters</button>
            )}
          </div>

          {/* apply bar — pick plan + apply to checked */}
          <div className="card" style={{ padding: 12, marginBottom: 12, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', background: checked.size ? '#eff6ff' : 'var(--surface)' }}>
            <div style={{ fontSize: 13, fontWeight: 700 }}>{checked.size} selected</div>
            <span style={{ color: 'var(--text3)' }}>→</span>
            <EntityPicker width={280} placeholder="Pick a plan to assign…" options={planOptions}
              value={bulkPlanId} onChange={setBulkPlanId} ariaLabel="Plan to assign" />
            <button className="btn btn-primary" disabled={!bulkPlanId || checked.size === 0 || bulkBusy}
              onClick={() => { setBulkResult(null); setConfirmOpen(true) }}>
              {bulkBusy ? '…' : `Assign to ${checked.size} ${checked.size === 1 ? 'person' : 'people'}`}
            </button>
            {checked.size > 0 && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setChecked(new Set())}>Clear selection</button>}
          </div>

          {bulkResult && !bulkResult.error && (
            <div className="card" style={{ padding: 12, marginBottom: 12, background: '#f0fdf4', border: '1px solid #bbf7d0', fontSize: 13 }}>
              ✅ <strong>{bulkResult.plan_name}</strong> — {bulkResult.summary?.assigned || 0} newly assigned ·
              {' '}{bulkResult.summary?.replaced || 0} replaced ({bulkResult.summary?.rows_deleted || 0} old removed) ·
              {' '}{bulkResult.summary?.already || 0} already had it{bulkResult.summary?.skipped ? ` · ${bulkResult.summary.skipped} skipped (kept their other plan)` : ''}.
            </div>
          )}
          {bulkResult?.error && <div className="card" style={{ padding: 12, marginBottom: 12, background: '#fef2f2', border: '1px solid #fecaca', fontSize: 13 }}>❌ {bulkResult.error}</div>}

          {/* people table */}
          {!roster.ready && <div className="card" style={{ padding: 14, marginBottom: 12, background: '#fffbeb', border: '1px solid #fde68a', fontSize: 13 }}>⚠️ Run migration 059 to enable plan assignments.</div>}
          <div className="card" style={{ padding: 0 }}>
            <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)' }}>
              {filteredPeople.length} of {roster.people.length} people{fMarkets.length || fRoles.length || nameQuery ? ' (filtered)' : ''}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={{ ...th, width: 34 }}>
                      <input type="checkbox" aria-label="Select all filtered"
                        checked={allFilteredChecked}
                        ref={el => { if (el) el.indeterminate = !allFilteredChecked && someFilteredChecked }}
                        onChange={toggleAll} disabled={filteredValues.length === 0} />
                    </th>
                    {['Name', 'Role', 'Market', 'Current plan(s)'].map(h => <th key={h} style={th}>{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {filteredPeople.map(p => {
                    const dup = (nameCounts[p.name.trim().toLowerCase()] || 0) > 1
                    const isChecked = checked.has(p.value)
                    return (
                      <tr key={p.value} style={{ borderTop: '1px solid var(--border)', background: isChecked ? '#eff6ff' : undefined, cursor: 'pointer' }}
                        onClick={() => toggleOne(p.value)}>
                        <td style={{ ...td, textAlign: 'center' }} onClick={e => e.stopPropagation()}>
                          <input type="checkbox" checked={isChecked} onChange={() => toggleOne(p.value)} aria-label={`Select ${p.name}`} />
                        </td>
                        <td style={td}>
                          <span style={{ fontWeight: 600 }}>{p.name}</span>
                          {dup && p.email && <span style={{ color: 'var(--text3)', fontSize: 11 }}> — {p.email}</span>}
                          {!p.is_active && <span style={{ fontSize: 10, marginLeft: 6, color: '#b45309' }}>inactive</span>}
                        </td>
                        <td style={td}>{p.role ? <span style={{ fontSize: 11, fontWeight: 700, padding: '1px 6px', borderRadius: 8, background: '#dbeafe', color: '#1e40af' }}>{p.role}</span> : <span style={{ color: 'var(--text3)' }}>—</span>}</td>
                        <td style={td}>{p.market || <span style={{ color: 'var(--text3)' }}>—</span>}</td>
                        <td style={td}>
                          {p.current_plans.length === 0 ? <span style={{ color: 'var(--text3)' }}>none</span> :
                            p.current_plans.map(c => (
                              <span key={c.plan_id} style={{ fontSize: 11, fontWeight: 600, padding: '1px 7px', borderRadius: 8, marginRight: 4, background: c.plan_id === bulkPlanId ? '#dcfce7' : '#f1f5f9', color: c.plan_id === bulkPlanId ? '#166534' : '#475569' }}>{c.plan_name}</span>
                            ))}
                        </td>
                      </tr>
                    )
                  })}
                  {filteredPeople.length === 0 && <tr><td colSpan={5} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No people match the current filters.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>

          {/* confirm modal */}
          {confirmOpen && (
            <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100 }}
              onClick={() => !bulkBusy && setConfirmOpen(false)}>
              <div className="card" style={{ padding: 20, maxWidth: 480, width: '90%' }} onClick={e => e.stopPropagation()}>
                <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 10 }}>Assign “{bulkPlanName}” to {selectedPeople.length} {selectedPeople.length === 1 ? 'person' : 'people'}?</div>
                <ul style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 14px', paddingLeft: 18, lineHeight: 1.7 }}>
                  <li><strong>{bulkPreview.newCount}</strong> will be newly assigned</li>
                  <li><strong>{bulkPreview.alreadyCount}</strong> already have this plan (skipped)</li>
                  {bulkPreview.replacePeople > 0 && (
                    <li style={{ color: '#b45309' }}>
                      <strong>{bulkPreview.replacePeople}</strong> currently have a DIFFERENT plan — replacing removes
                      {' '}<strong>{bulkPreview.replaceRows}</strong> existing assignment{bulkPreview.replaceRows === 1 ? '' : 's'}
                    </li>
                  )}
                </ul>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                  <button className="btn btn-secondary" disabled={bulkBusy} onClick={() => setConfirmOpen(false)}>Cancel</button>
                  {bulkPreview.replacePeople > 0 && (
                    <button className="btn btn-secondary" disabled={bulkBusy} onClick={() => applyBulk(false)}
                      title="Assign only people who have no plan yet; leave the others on their current plan">
                      Assign new only ({bulkPreview.newCount})
                    </button>
                  )}
                  <button className="btn btn-primary" disabled={bulkBusy || (bulkPreview.newCount === 0 && bulkPreview.replacePeople === 0)}
                    onClick={() => applyBulk(bulkPreview.replacePeople > 0)}>
                    {bulkBusy ? '…' : bulkPreview.replacePeople > 0
                      ? `Replace & assign (${bulkPreview.replaceRows} overwritten)`
                      : `Assign ${bulkPreview.newCount}`}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function PreviewRow({ row }: { row: any }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <tr style={{ borderTop: '1px solid var(--border)', cursor: 'pointer' }} onClick={() => setOpen(o => !o)}>
        <td style={td}>{open ? '▾ ' : '▸ '}{row.rep}</td>
        <td style={td}>{row.store || '—'}</td>
        <td style={td}>{row.plan_name}</td>
        <td style={td}>{row.qualifying_units}</td>
        <td style={td}>{row.tier_multiplier}×</td>
        <td style={td}>{fmt(row.base_payout)}</td>
        <td style={td}>{fmt(row.tiered_payout)}</td>
        <td style={{ ...td, fontWeight: 700 }}>{fmt(row.total_payout)}</td>
      </tr>
      {open && (row.rules || []).map((rb: any, j: number) => (
        <tr key={j} style={{ background: '#f8fafc' }}>
          <td style={{ ...td, paddingLeft: 24, color: 'var(--text3)' }} colSpan={3}>{rb.label || rb.payout_kind} · {rb.payout_kind}{rb.tiered ? ' · tiered' : ''}{rb.qualifies === false ? ' · non-qualifying' : ''}</td>
          <td style={{ ...td, color: 'var(--text3)' }}>{rb.qualifying_units} / {rb.matched_lines}</td>
          <td style={td} colSpan={3} />
          <td style={td}>{fmt(rb.payout)}</td>
        </tr>
      ))}
    </>
  )
}
