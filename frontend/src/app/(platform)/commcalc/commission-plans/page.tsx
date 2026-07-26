'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import EntityPicker from '@/components/EntityPicker'
import {
  PlanOptions, MatchRule, MatchValuePicker, MatchWarnings, OptionsSourceNote,
  usePlanMatchStats, FALLBACK_VOCAB, countMatches,
} from '../_lib/planMatch'

// Configurable commission PLAN engine (migration 059). A PLAN is a set of RULES the user creates — each
// rule matches sale lines on any sales-transaction field (contract_type/tender_type/department/category/
// product_desc/sku/trans_type/any) and defines how matching lines PAY (flat/unit, %MRC, %GP, %price-over-
// cost, flat bonus), optionally TIERED by a qualifying-unit count → multiplier. Plans are ASSIGNED to
// employee/store/market/default (precedence employee>store>market>default). PREVIEW is READ-ONLY: it shows
// what the plan WOULD pay for a period from raw_sales — it does NOT change live commissions.

type Rule = { id?: string; label?: string; match_field: string; match_op: string; match_value: string
  qualifies: boolean; payout_kind: string; amount: number; pct: number; tiered: boolean }
type Tier = { id?: string; metric?: string; min_count: number; multiplier: number }
type Assign = { id?: string; scope: string; scope_value?: string | null; priority?: number }
type Plan = { id?: string; name: string; carrier_id?: string | null; base_tier_metric?: string | null
  is_active: boolean; notes?: string | null; rules?: Rule[]; tiers?: Tier[]; assignments?: Assign[]
  // mig 232 — how the tier metric is COUNTED. Null/'rule_units' = the legacy count (every qualifying
  // rule-matched LINE, summed across rules). 'transactions' counts DISTINCT matched trans_ids.
  tier_count_basis?: string | null; tier_match_field?: string | null; tier_match_op?: string | null
  tier_match_value?: string | null; tier_below_min_multiplier?: number | string | null }
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

const blankRule = (): Rule => ({ label: '', match_field: 'contract_type', match_op: 'equals', match_value: '', qualifies: true, payout_kind: 'flat_per_unit', amount: 0, pct: 0, tiered: false })
const blankPlan = (): Plan => ({ name: '', carrier_id: '', base_tier_metric: 'none', is_active: true, notes: '', rules: [], tiers: [], assignments: [] })

export default function CommissionPlansPage() {
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

  async function loadRoster() {
    try {
      const r = await api('/api/v1/commcalc/commission-plans/roster')
      setRoster({ people: r.people || [], roles: r.roles || [], markets: r.markets || [], ready: r.ready !== false })
    } catch { setRoster({ people: [], roles: [], markets: [], ready: true }) }
  }

  async function load() {
    try {
      const r = await api('/api/v1/commcalc/commission-plans')
      setPlans(r.plans || []); setReady(r.ready !== false)
      if (r.ready === false) setMsg(r.note || 'Run migration 059 to enable.')
      setCarriers(await api('/api/v1/commcalc/carriers').catch(() => []))
      // include_inactive: the role-count preview must agree with the engine, which matches INACTIVE reps
      // too (a mid-month-terminated rep's sales still pay under their role). We show active/inactive split.
      setEmployees(await api('/api/v1/storeops/employees?all_company=true&include_inactive=true').catch(() => []))
      setStores(await api('/api/v1/storeops/stores').catch(() => []))
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
  useEffect(() => { load(); loadRoster() }, [])
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
      title: 'Commission Plan Coverage', subtitle: `${cov?.period || period} — read-only diagnostic`,
      filename: `commission-coverage-${String(cov?.period || period).replace(/\s+/g, '-')}`,
      sheets: [
        { name: 'Uncovered sellers', rows: c.unassigned_reps || [], columns: [
          { header: 'Rep', get: (r: any) => r.rep }, { header: 'Store', get: (r: any) => r.store },
          { header: 'Market', get: (r: any) => r.market }, { header: 'Role', get: (r: any) => r.role },
          { header: 'Transactions', get: (r: any) => r.transactions }, { header: 'Lines', get: (r: any) => r.lines },
          { header: 'Sales $', get: (r: any) => r.ext_price }] },
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

  function previewPayload(): ExportPayload {
    return {
      title: 'Commission Plan Preview (read-only)', subtitle: `${period} — does NOT change live commissions`,
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
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧮 Commission Plans</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Build your own commission plans. Any line on the sales transaction report can qualify for commission
          on rules YOU define — then assign each plan to employees / stores / markets. The preview shows what a
          plan <strong>would</strong> pay; it is <strong>read-only</strong> and does not change live commissions.
        </p>
      </div>
      {!ready && <div className="card" style={{ padding: 14, marginBottom: 14, background: '#fffbeb', border: '1px solid #fde68a', fontSize: 13 }}>⚠️ {msg || 'Run migration 059_commission_plans.sql in Supabase to enable.'}</div>}

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
            <label style={{ ...lbl, justifyContent: 'flex-end' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
                <input type="checkbox" checked={draft.is_active} onChange={e => upd({ is_active: e.target.checked })} /> Active
              </span>
            </label>
          </div>

          {/* RULES */}
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Rules — which line items qualify + how they pay</div>
          <div style={{ overflowX: 'auto', marginBottom: 8 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 920 }}>
              <thead><tr>{['Label', 'Match field', 'Op', 'Value', 'Qualifies', 'Payout', 'Amount / %', 'Tiered', ''].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
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
          <div style={{ fontWeight: 700, fontSize: 14 }}>👁️ Preview <span style={{ fontWeight: 400, fontSize: 12, color: '#b45309' }}>(read-only — does NOT change live commissions)</span></div>
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

      {/* PLAN COVERAGE — why isn't the plan paying what I configured? (read-only diagnostic, mig 232) */}
      <div className="card" style={{ padding: 16, marginTop: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>🩺 Plan coverage <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text3)' }}>(read-only — who is uncovered, which lines no rule matched, why a tier didn’t move pay)</span></div>
          <span style={{ flex: 1 }} />
          <button className="btn btn-secondary" disabled={covBusy} onClick={runCoverage}>{covBusy ? '…' : `Check ${period}`}</button>
          {cov?.coverage && <><ExportButtons payload={coveragePayload} /><SendReportButton exportPayload={coveragePayload} compact /></>}
        </div>
        {!cov && <div style={{ fontSize: 13, color: 'var(--text3)' }}>Uses the period above. Nothing is written and no calculation is triggered.</div>}
        {cov && (<>
          {cov.snapshot?.stale && (
            <div style={{ background: '#fef3c7', border: '1px solid #fcd34d', borderRadius: 8, padding: '8px 10px', fontSize: 12.5, marginBottom: 10 }}>
              ⚠️ <b>Stored snapshot is stale.</b> The plans compute {fmt(cov.snapshot.engine_total)} for {cov.period} but
              the saved commission rows total {fmt(cov.snapshot.stored_total)} across {cov.snapshot.stored_rows} rows.
              The commission pages show the SAVED numbers — run <b>Calculate</b> for this period to apply the current configuration.
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
          {(cov.coverage?.unassigned_reps || []).length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 4 }}>Sellers with sales but NO plan attached — these reps pay $0</div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr>{['Rep', 'Store', 'Market', 'Role', 'Txns', 'Lines', 'Sales $'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                  <tbody>{cov.coverage.unassigned_reps.map((u: any, i: number) => (
                    <tr key={i}><td style={td}>{u.rep}</td><td style={td}>{u.store}</td><td style={td}>{u.market}</td>
                      <td style={td}>{u.role || '—'}</td><td style={td}>{u.transactions}</td><td style={td}>{u.lines}</td>
                      <td style={td}>{fmt(u.ext_price)}</td></tr>
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
        </>)}
      </div>
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
