'use client'
// ═══════════════════════════════════════════════════════════════════════════════════════════════
// TENANT ONBOARDING — Stage 2: sales / POS / inventory / "other" reports (design 2026-09-20, §2,
// steps 2.0–2.6) on the SAME spine as the commission statement (stage 3, page.tsx):
//
//   2.0 "What do you have?" — a checklist; every ticked item becomes an instance in the rail. "No
//       inventory export" is an explicit RECORDED choice (a reason and a name), never a blank.
//   2.1 drop → 2.2 sheet / header / footer → 2.3 columns with provenance + samples → 2.4 store
//   strings → store codes and rep strings → employees through the SHARED resolver (assign / create /
//   not ours with a reason; zero unresolved is the exit gate) → 2.5 our numbers beside the file's
//   → 2.6 commit with the same save guarantee: map saved + read back, decisions saved + read back,
//   rows landed through the EXISTING importer, RE-READ; "Saved and verified" only when the counts
//   and totals match.
//
// This component owns NO parsing, NO resolution and NO persistence of its own: the backend's
// /onboarding/intake/analyze reads the file and proposes; /onboarding/intake/commit saves and
// re-reads. The file is KEPT by the backend between visits (a private storage object referenced
// from the stage row), so re-checking and committing later need no re-drop.
// No carrier, POS vendor or tenant is named in this file (RULE TWO).
// ═══════════════════════════════════════════════════════════════════════════════════════════════
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, apiUpload } from '@/lib/client'
import {
  BASE, card, note, inp, btn, primary, ghost, mono, money, num, Lamp, DetectPanel, ColumnsTable, StoreResolver, Dropzone, KindDetectZone,
  useAutoSave, SaveButton,
  type StateResp, type Instance, type Column, type MoneyCol, type Detect, type StoreRow, type RepRow, type Tie, type FileRef, type IdentityDecisions,
  type SaveResult, type SourceKind, type DetectCandidate,
} from './intake-shared'
import { useReportKinds } from '@/lib/report-kinds'
import type { ReportKindRow } from '@/lib/carrier-scope'
import ShowsIn from '@/components/ShowsIn'
import { LineClassStep, type LineClassBlock } from './line-class-step'   // 2.5a — what counts as an activation (owner 2026-09-21)

type SalesNumbers = {
  rows: number; distinct_txns: number; sum_amount: number; sum_gp: number | null
  date_span: { from: string | null; to: string | null; undated_rows: number }; voided_rows: number
  per_store: { value: string; count: number; sum: number }[]; per_rep: { value: string; count: number; sum: number }[]; storeless_rows: number
}
type InvNumbers = {
  rows: number; units: number; sum_cost: number; sum_unit_cost: number; rows_with_device_key: number; rows_without_device_key: number
  per_store: { value: string; count: number; units: number; sum: number }[]; storeless_rows: number; as_of_date?: string | null
}
type OtherNumbers = { rows: number; headers: string[]; money_columns: { header: string; sum: number; cells: number }[]; destination: null; basis: string }
// Stage C — the numbers each typed "other" kind shows beside the file's own (mirror onboarding_intake.py)
type XrNumbers = {
  rows: number; stores: number; dates: string[]; close_date: string | null; date_source: string | null
  per_store_day: { store: string; date: string; cash: number; card: number; other: number; total: number; tenders: number; file_total: number | null; difference: number | null }[]
  sum_cash: number; sum_card: number; sum_other: number; sum_total: number; file_total: number | null; tenders_seen: string[]
  parser?: { sheets_read: number; headers_found: number; tender_rows_matched: number; tender_rows_skipped: number; unmatched_labels: string[]; canon_matched_labels: string[]; config_label_count: number }
}
type MerNumbers = {
  rows: number; merchants: string[]; dates: string[]; sum_gross: number; sum_net: number; sum_fees: number; sum_refunds: number; txn_count: number
  per_merchant_day: { merchant_id: string; date: string; store_label: string | null; store_code: string | null; gross: number; net: number; fees: number; refunds: number; count: number; lines: number }[]
  by_brand: Record<string, number>; unmapped_merchants: string[]; file_totals: { gross: number; net: number; fees: number; count: number } | null
  difference?: { gross: number; net: number; fees: number; count: number }; portal_key?: string; settlement_role?: string; role_source?: string; grain?: string
  overlap_with_other_sources?: number | null; warnings?: string[]
}
type BpNumbers = {
  rows: number; billpay_rows: number; non_billpay_rows: number; sum_all_rows: number; sum_amount: number; count: number; dates: string[]
  per_store_day: { store: string; account_id: string; date: string; amount: number; count: number }[]
  unmapped_accounts: string[]; processor?: string; feed?: { processor: string | null; default_layout: string; note: string | null }
  feed_read?: { processor: string; sum_amount: number; reader: string }; date_span?: { from: string | null; to: string | null; undated_rows: number }
}
type Analysis2 = {
  source_kind: string; target_table: string | null; report_key: string | null; instance_key: string; filename?: string | null
  source_ref: string; layout: string; name: string; layout_label: string; as_of_date: string | null
  matrix?: boolean; settlement_role?: string | null; merchant_processor?: string | null; identity_kind?: 'store' | 'merchant_id'
  detect: Detect & { xlsx_repair?: { filename: string; action: string } | null }; columns: Column[]; money_columns: MoneyCol[]
  stores: StoreRow[]; reps: RepRow[]; unresolved_stores: string[]; identity_decisions: IdentityDecisions
  period: { months: { period: string; rows: number }[]; span_from: string | null; span_to: string | null; proposed: string | null; spans_two_months: boolean; dated_rows: number }
  verify: { basis: string; numbers: SalesNumbers | InvNumbers | OtherNumbers | XrNumbers | MerNumbers | BpNumbers; tie: Tie | null; rows_in_file: number; rows_usable: number; footer_rows: number
            rows_excluded_not_ours: number; excluded: Record<string, string>; rows_to_land: number; ignored_money_columns: MoneyCol[]; refusals: string[] }
  file?: FileRef; state?: StateResp
  line_class?: LineClassBlock | null      // 2.5a — the activation types + metric buckets over the frame that would land
}
type Commit2 = {
  ok: boolean; recorded?: boolean; problems: string[]; saved: number; source_kind: string; instance_key: string
  // 2.5a: `ok` = the landing re-read matched; `verified` additionally needs the activation gate closed
  verified?: boolean; activation_gate?: { blocked: boolean; open: boolean; checked: boolean; reason: string; step?: string; attested?: { reason: string; by: string | null } | null }
  mapping_saved?: string[]; identity_written?: { aliases: [string, string][]; stores_created: string[]; rep_aliases: [string, string][] }
  verified_numbers: Record<string, unknown> & { rows_landed?: number; rows_built?: number; rows_inserted?: number; rows_without_device_key?: number
    rows_excluded_not_ours?: number; tie?: Tie; numbers?: SalesNumbers | InvNumbers | XrNumbers | MerNumbers | BpNumbers; basis?: string; attestation?: { reason: string } | null; confirmed_by?: string | null
    billpay_extract?: { basis: string | null; lines?: number; sum?: number; feed_present?: boolean; difference?: number | null; report?: string; error?: string }
    sold_check?: { basis: string | null; activated_not_rung_out?: number; sold_not_cleared?: number; activations_unpairable?: number; activations_present?: boolean; report?: string; error?: string }
    shows_in?: import('@/lib/report-kinds').ShowsIn | null }
  state: { saved: boolean; reason?: string }
}

export const STAGE2_STEPS: [string, string][] = [
  ['2.0', 'What do you have?'], ['2.1', 'Upload the export'], ['2.2', 'Sheet, header row, footer'], ['2.3', 'Confirm the columns'],
  ['2.4', 'Stores and reps'], ['2.5', 'Our numbers beside the file\'s'], ['2.5a', 'What counts as an activation'], ['2.6', 'Confirm this export'],
]
const KEYS = STAGE2_STEPS.map(s => s[0])
// THE 2.0 CARDS ARE THE REGISTRY (design §7, 2026-09-20): what this tenant can upload is read from
// GET /commcalc/report-kinds through lib/report-kinds.ts (the one visibility function) — the layman
// label, "what's in it", where it comes from, and the provenance per card. No list of kinds lives here;
// a landing's display name falls back to the backend's own vocabulary (state.source_kinds).
// A matrix kind has no layout: its instance's third slot is a fixed word (mirrors onboarding_intake.MATRIX_INSTANCE_SLOT)
const MATRIX_SLOT: Record<string, string> = { x_report: 'x_report', merchant_payments: 'settlement' }
const MATRIX_KINDS = Object.keys(MATRIX_SLOT)

function ikey(kind: string, ref: string, layoutOrName: string) {
  const slug = (t: string) => t.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  return `${kind}:${slug(ref) || 'file'}:${slug(MATRIX_SLOT[kind] || layoutOrName) || 'report'}`
}

export function Stage2Flow({ state, who, reloadState, instanceKey, setInstanceKey, step, setStep, flash }: {
  state: StateResp | null; who: string; reloadState: (ik?: string) => Promise<StateResp | null>
  instanceKey: string; setInstanceKey: (k: string) => void; step: string; setStep: (s: string) => void; flash: (m: string) => void
}) {
  const instances = useMemo(() => (state?.rail.instances || []).filter(i => i.stage === '2'), [state])
  const inst: Instance | undefined = instances.find(i => i.instance_key === instanceKey)
  const kinds = state?.source_kinds || []
  const layoutsOf = (k: string) => kinds.find(x => x.value === k)?.layouts || []
  const registry = useReportKinds()
  const intakeCards = useMemo(() => registry.forSurface('intake'), [registry])
  const cardLabel = (key: string, landing: string) => registry.byKey(key)?.label || kinds.find(x => x.value === landing)?.label || landing

  // ── 2.0 checklist inputs ──────────────────────────────────────────────────────────────────────
  const [posRef, setPosRef] = useState('')
  const [salesLayout, setSalesLayout] = useState('')
  const [invLayout, setInvLayout] = useState('')
  const [noInvReason, setNoInvReason] = useState('')
  const [otherName, setOtherName] = useState('')
  const [portalKey, setPortalKey] = useState('')
  const [portalRole, setPortalRole] = useState('')
  const [bpSource, setBpSource] = useState('')
  const [bpLayout, setBpLayout] = useState('')
  const [busy, setBusy] = useState(false)

  // ── the current instance's working state ──────────────────────────────────────────────────────
  const [file, setFile] = useState<File | null>(null)
  const [filename, setFilename] = useState('')
  const [kept, setKept] = useState<FileRef | null>(null)
  const [a, setA] = useState<Analysis2 | null>(null)
  const [columnMap, setColumnMap] = useState<Record<string, string>>({})
  const [sheet, setSheet] = useState('')
  const [headerRow, setHeaderRow] = useState('')
  const [footerMode, setFooterMode] = useState<'auto' | 'none'>('auto')
  const [decisions, setDecisions] = useState<IdentityDecisions>({})
  const [typedTotal, setTypedTotal] = useState('')
  const [asOf, setAsOf] = useState('')
  const [role, setRole] = useState('')          // a merchant settlement's role (external terminal / the POS's card tender)
  const [attest, setAttest] = useState('')
  // 2.6: confirm deleting rows of a DIFFERENT report kind that sit in this file's store × date slice (landing
  // identity, 2026-09-20). Off by default — such a landing is refused naming the loss until this is ticked.
  const [replaceOther, setReplaceOther] = useState(false)
  const [commitRes, setCommitRes] = useState<Commit2 | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const restoredFor = useRef('')

  // restore the instance's saved choices when it becomes current (design §0.2: the rail is the state)
  useEffect(() => {
    if (!inst || restoredFor.current === inst.instance_key) return
    restoredFor.current = inst.instance_key
    const p = inst.payload || {}
    // every field the auto-save writes comes back (owner 2026-09-20: "if you refresh … it is not being saved")
    setFile(null); setA(null); setCommitRes(null)
    setAttest(typeof p.attestation === 'string' ? p.attestation : '')
    setTypedTotal(typeof p.typed_total === 'string' ? p.typed_total : '')
    setSheet(typeof p.sheet === 'string' ? p.sheet : '')
    setHeaderRow(typeof p.header_row === 'string' ? p.header_row : '')
    setFooterMode(p.footer_mode === 'none' ? 'none' : 'auto')
    setFilename(typeof p.filename === 'string' ? p.filename : '')
    setKept(p.file && typeof p.file === 'object' ? (p.file as FileRef) : null)
    setColumnMap(p.column_map && typeof p.column_map === 'object' ? (p.column_map as Record<string, string>) : {})
    setDecisions(p.identity && typeof p.identity === 'object' ? (p.identity as IdentityDecisions) : {})
    setAsOf(typeof p.as_of_date === 'string' ? p.as_of_date : '')
    setRole(typeof p.settlement_role === 'string' ? p.settlement_role : typeof p.role === 'string' ? p.role : '')
  }, [inst])

  const persist = useCallback(async (stepKey: string, patch: Record<string, unknown>) => {
    if (!instanceKey) return
    try { await api(`${BASE}/state`, { method: 'PUT', body: JSON.stringify({ instance_key: instanceKey, step: stepKey, payload: patch, by: who }) }); await reloadState(instanceKey) } catch { /* the rail is a convenience */ }
  }, [instanceKey, who, reloadState])
  const go = useCallback((k: string, patch: Record<string, unknown> = {}) => { setStep(k); persist(k, patch) }, [persist, setStep])

  // ── AUTO-SAVE (owner 2026-09-20) — the same debounced PUT /state as stage 3, step kept where it is
  const persistNow = useCallback(async (patch: Record<string, unknown>, keepalive = false): Promise<SaveResult> => {
    if (!instanceKey) return { saved: false, reason: 'add the export on the checklist first' }
    const d: StateResp = await api(`${BASE}/state`, { method: 'PUT', body: JSON.stringify({ instance_key: instanceKey, step, payload: patch, by: who }), keepalive })
    if (d.state_ready === false) return { saved: false, reason: `run migration ${d.migration}` }
    return d.save || { saved: true }
  }, [instanceKey, step, who])
  const { schedule, saveNow, status: saveStatus } = useAutoSave(persistNow)
  const draft = useMemo(() => ({
    column_map: columnMap, identity: decisions, typed_total: typedTotal, as_of_date: asOf || null, attestation: attest,
    sheet, header_row: headerRow, footer_mode: footerMode, role: role || null,
  }), [columnMap, decisions, typedTotal, asOf, attest, sheet, headerRow, footerMode, role])
  const draftSeen = useRef({ instance: '', key: '' })
  useEffect(() => {
    if (!instanceKey || !a) return
    const key = JSON.stringify(draft)
    if (draftSeen.current.instance !== instanceKey) { draftSeen.current = { instance: instanceKey, key }; return }   // a (re)opened instance: the restore is not a change
    if (key === draftSeen.current.key) return
    draftSeen.current = { instance: instanceKey, key }
    schedule(draft)
  }, [draft, instanceKey, a, schedule])
  const saveUi = <SaveButton onSave={() => saveNow(draft)} status={saveStatus} stateReady={state?.state_ready !== false} migration={state?.migration} busy={busy} />

  const kind = inst?.kind || ''
  const p = (inst?.payload || {}) as Record<string, unknown>
  const sourceRef = typeof p.source_ref === 'string' ? p.source_ref : ''
  const layout = typeof p.layout === 'string' ? p.layout : ''
  const otherNameOf = typeof p.name === 'string' ? p.name : ''
  const reportKindOf = typeof p.report_kind === 'string' ? p.report_kind : ''
  const canUseKept = !file && !!kept?.stored
  const isMatrix = MATRIX_KINDS.includes(kind)
  const knownKinds: SourceKind[] = (state?.other_kinds || ['other']).map(k => kinds.find(x => x.value === k) || { value: k, label: k, built: true, target_table: null, layouts: [] })
  const portals = state?.merchant_portals
  const feed = state?.billpay_feed

  // ── 2.0: add instances — `reportKind` = the registry card picked, learned under on confirm ─────
  async function addInstance(k: string, ref: string, lay: string, name: string, reportKind = '') {
    const key = ikey(k, ref, k === 'other' ? name : lay)
    setBusy(true)
    try {
      await api(`${BASE}/state`, { method: 'PUT', body: JSON.stringify({ instance_key: key, step: '2.1', payload: { kind: k, source_ref: ref, layout: lay, name, report_kind: reportKind || null }, by: who }) })
      await reloadState(key)
      setInstanceKey(key); setStep('2.1')
    } catch (e: unknown) { flash((e as Error)?.message || 'Could not add') }
    setBusy(false)
  }
  async function recordNoInventory() {
    if (!state) return
    setBusy(true)
    try {
      await api(`${BASE}/state`, { method: 'PUT', body: JSON.stringify({ instance_key: state.inventory_none_key, step: '2.0', status: 'verified', payload: { reason: noInvReason.trim(), kind: 'inventory' }, by: who }) })
      await reloadState()
      flash('Recorded: no inventory export, with your reason and name.')
    } catch (e: unknown) { flash((e as Error)?.message || 'Could not record') }
    setBusy(false)
  }

  // ── analyze (read-only) ───────────────────────────────────────────────────────────────────────
  const buildForm = useCallback((extra: Record<string, string> = {}) => {
    const fd = new FormData()
    if (file) fd.append('file', file); else if (kept?.stored) { fd.append('use_stored', '1') }
    fd.append('instance_key', instanceKey); fd.append('source_kind', kind); fd.append('pos_source', sourceRef)
    if (kind === 'other') fd.append('name', otherNameOf); else if (!isMatrix) fd.append('layout', layout)
    if (reportKindOf) fd.append('report_kind', reportKindOf)
    if (kind === 'merchant_payments' && role) fd.append('role', role)
    if (Object.keys(columnMap).length) fd.append('column_map', JSON.stringify(columnMap))
    if (decisions.store || decisions.rep) fd.append('identity', JSON.stringify(decisions))
    if (typedTotal) fd.append('typed_total', typedTotal)
    if (asOf) fd.append('as_of_date', asOf)
    if (sheet) fd.append('sheet', sheet)
    if (headerRow !== '') fd.append('header_row', headerRow)
    fd.append('footer', footerMode)
    for (const [k, v] of Object.entries(extra)) fd.set(k, v)
    return fd
  }, [file, kept, instanceKey, kind, sourceRef, otherNameOf, reportKindOf, layout, isMatrix, role, columnMap, decisions, typedTotal, asOf, sheet, headerRow, footerMode])

  const analyze = useCallback(async (opts: { keepStep?: boolean; cm?: Record<string, string>; dec?: IdentityDecisions } = {}) => {
    if (!inst) return null
    if (!file && !kept?.stored) { flash('Drop the file first.'); return null }
    setBusy(true)
    try {
      const fd = buildForm()
      if (opts.cm) fd.set('column_map', JSON.stringify(opts.cm))
      if (opts.dec) fd.set('identity', JSON.stringify(opts.dec))
      const r: Analysis2 = await apiUpload(`${BASE}/analyze`, fd)
      setA(r)
      if (file) { setFilename(file.name); if (r.file) setKept(r.file) }
      const next: Record<string, string> = { ...(opts.cm ?? columnMap) }
      for (const c of r.columns) if (!(c.target_field in next) && c.column) next[c.target_field] = c.column
      setColumnMap(next)
      if (kind === 'inventory' && !asOf) setAsOf(new Date().toISOString().slice(0, 10))
      await persist(opts.keepStep ? step : '2.2', { filename: file?.name || filename, column_map: next, identity: opts.dec ?? decisions, as_of_date: asOf || null, role: role || null })
      return r
    } catch (e: unknown) { flash((e as Error)?.message || 'Could not read the file'); return null }
    finally { setBusy(false) }
  }, [inst, file, kept, buildForm, columnMap, kind, asOf, role, persist, step, filename, decisions, flash])

  const commit = useCallback(async () => {
    if (!inst) return
    if (!file && !kept?.stored) { flash('Drop the file again to commit — it was not kept.'); return }
    setBusy(true)
    try {
      const fd = buildForm({ verified_by: who })
      if (attest.trim()) fd.set('attestation', JSON.stringify({ reason: attest.trim() }))
      if (replaceOther) fd.set('confirm_replace_other_kinds', '1')
      const r: Commit2 = await apiUpload(`${BASE}/commit`, fd)
      setCommitRes(r); setStep('2.6')
      await reloadState(instanceKey)
      if (!r.ok && !r.recorded) flash('Committed with problems — see the red panel. Nothing here is reported as verified until the re-read matches.')
    } catch (e: unknown) { flash((e as Error)?.message || 'Commit refused') }
    finally { setBusy(false) }
  }, [inst, file, kept, buildForm, who, attest, replaceOther, reloadState, instanceKey, flash, setStep])

  function onFiles(fl: FileList | null) {
    const f = fl?.[0]
    if (!f) return
    if (!/\.(xlsx|xls|csv)$/i.test(f.name)) { flash('Drop an .xlsx, .xls or .csv file.'); return }
    setFile(f); setFilename(f.name); setCommitRes(null)
  }

  const tie = a?.verify.tie || null
  const needsAttest = !!a && kind !== 'other' && (tie?.match === false || tie?.match === null)
  const unresolved = a?.unresolved_stores || []
  const required: Record<string, string[]> = { sales: ['store', 'trans_date', 'ext_price'], pos: ['store', 'trans_date', 'ext_price'], inventory: ['store', 'sku'], other: [], x_report: [], merchant_payments: [] }
  // a bill-pay report's required columns follow its LAYOUT — the backend names them per column
  const requiredHere = required[kind] || (a?.columns || []).filter(c => c.required).map(c => c.target_field)
  const canLeave23 = requiredHere.every(f => !!columnMap[f])
  const isSales = kind === 'sales' || kind === 'pos'
  const n = a?.verify.numbers as (SalesNumbers & InvNumbers & OtherNumbers & XrNumbers & MerNumbers & BpNumbers) | undefined

  // ── 2.0 ────────────────────────────────────────────────────────────────────────────────────────
  if (step === '2.0' || !inst) {
    const have = (k: string) => instances.filter(i => i.kind === k)
    const noInv = instances.find(i => i.instance_key === state?.inventory_none_key)
    const openInst = (i: Instance) => { setInstanceKey(i.instance_key); setStep(i.status === 'verified' ? '2.6' : i.step === '2.0' ? '2.1' : i.step) }
    const instLines = (landing: string) => have(landing).map(i => <div key={i.instance_key} style={{ fontSize: 12, marginTop: 6 }}><Lamp status={i.status} />{i.label} · step {i.step} <button style={{ ...ghost, padding: '2px 8px', fontSize: 12 }} onClick={() => openInst(i)}>open</button></div>)
    const defaultLayout = (c: ReportKindRow) => c.layout || layoutsOf(c.landing).find(l => l.default)?.report_key || ''
    // what a detected candidate becomes: the same add as its card
    const pickDetected = (c: DetectCandidate | null, fname: string) => {
      if (!c) { setOtherName(fname.replace(/\.[a-z0-9]+$/i, '')); flash('Name it below and add it as something else.'); return }
      const card = registry.byKey(c.key)
      if (!card) return
      if (card.landing === 'commission') { flash(`A ${card.label} is taken in under Stage 3 — pick Stage 3 in the rail.`); return }
      if (card.landing === 'other') { setOtherName(fname.replace(/\.[a-z0-9]+$/i, '')); return }
      if (['sales', 'pos', 'inventory', 'x_report'].includes(card.landing)) {
        if (!posRef.trim()) { flash(`This looks like your ${card.label} — name the POS it comes from, then add it.`); return }
        addInstance(card.landing, posRef.trim(), defaultLayout(card), '', card.key); return
      }
      flash(`This looks like your ${card.label} — fill in where it comes from below and add it.`)
    }
    const actionFor = (c: ReportKindRow) => {
      const l = c.landing
      if (l === 'sales' || l === 'pos') return (
        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <select value={salesLayout} onChange={e => setSalesLayout(e.target.value)} style={inp}>
            <option value="">layout: {c.layout ? c.layout.replace(/_/g, ' ') : 'default'}</option>
            {layoutsOf(l).map(x => <option key={x.report_key} value={x.report_key}>{x.label}</option>)}
          </select>
          <button style={btn} disabled={busy || !posRef.trim()} title={posRef.trim() ? '' : 'name the POS source above first'} onClick={() => addInstance(l, posRef.trim(), salesLayout || defaultLayout(c), '', c.key)}>Add</button>
        </div>)
      if (l === 'inventory') return (
        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <select value={invLayout} onChange={e => setInvLayout(e.target.value)} style={inp}>
            <option value="">layout: {c.layout ? c.layout.replace(/_/g, ' ') : 'default'}</option>
            {layoutsOf('inventory').map(x => <option key={x.report_key} value={x.report_key}>{x.label}</option>)}
          </select>
          <button style={btn} disabled={busy || !posRef.trim()} title={posRef.trim() ? '' : 'name the POS source above first'} onClick={() => addInstance('inventory', posRef.trim(), invLayout || defaultLayout(c), '', c.key)}>Add</button>
        </div>)
      if (l === 'commission') return <div style={{ ...note, marginTop: 6 }}>Taken in under Stage 3 in the rail — one statement per carrier and statement type.</div>
      if (l === 'x_report') return (
        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={note}>from the POS named above ({posRef.trim() || 'name it first'})</span>
          <button style={btn} disabled={busy || !posRef.trim()} title={posRef.trim() ? '' : 'name the POS source above first'} onClick={() => addInstance('x_report', posRef.trim(), '', '', c.key)}>Add</button>
        </div>)
      if (l === 'merchant_payments') return (
        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input list="portal-keys" value={portalKey} onChange={e => setPortalKey(e.target.value)} placeholder="card processor / portal" style={{ ...inp, width: 220 }} />
          <datalist id="portal-keys">
            {(portals?.sources || []).map(s2 => <option key={`src-${s2.key}`} value={s2.key}>{s2.label} (your data source · {s2.settlement_role})</option>)}
            {(portals?.catalog || []).map(c2 => <option key={`cat-${c2.key}`} value={c2.key}>{c2.label}</option>)}
          </datalist>
          <select value={portalRole} onChange={e => setPortalRole(e.target.value)} style={inp} title="settlement role — which side of the daily card tally this export answers">
            <option value="">role: the portal&apos;s default</option>
            {(portals?.roles || []).map(r => <option key={r} value={r}>{portals?.role_titles?.[r] || r}</option>)}
          </select>
          <button style={btn} disabled={busy || !portalKey.trim()} onClick={() => { setRole(portalRole); addInstance('merchant_payments', portalKey.trim(), '', '', c.key) }}>Add</button>
        </div>)
      if (l === 'bill_payments') return (
        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input value={bpSource} onChange={e => setBpSource(e.target.value)} placeholder="the processor / portal it comes from" style={{ ...inp, width: 220 }} />
          <select value={bpLayout} onChange={e => setBpLayout(e.target.value)} style={inp}>
            <option value="">layout: {feed?.default_layout ? `default (${feed.default_layout})` : 'default'}</option>
            {layoutsOf('bill_payments').map(x => <option key={x.report_key} value={x.report_key}>{x.label}</option>)}
          </select>
          <button style={btn} disabled={busy || !bpSource.trim()} onClick={() => addInstance('bill_payments', bpSource.trim(), bpLayout || feed?.default_layout || defaultLayout(c), '', c.key)}>Add</button>
          {feed && <span style={{ ...note, width: '100%' }}>{feed.processor ? `Your bill-pay coverage recon reads the "${feed.processor}" feed (${feed.source}) — the default layout is the one it re-reads.` : feed.note}</span>}
        </div>)
      return (
        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input value={otherName} onChange={e => setOtherName(e.target.value)} placeholder="name the report" style={{ ...inp, width: 300 }} />
          <button style={btn} disabled={busy || !otherName.trim()} onClick={() => addInstance('other', posRef.trim(), '', otherName.trim(), c.key)}>Add</button>
        </div>)
    }
    const hint = (landing: string) => knownKinds.find(k => k.value === landing)?.hint || kinds.find(k => k.value === landing)?.hint
    return (
      <div style={card}>
        <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.0 — What do you have?</h2>
        <p style={{ ...note, marginBottom: 10 }}>You only need the exports you already pull from your systems. Each card is a report kind; add the ones you have and each becomes a line in the rail you can come back to. Nothing is imported until its own confirm step.</p>
        {/* WHAT DECIDES THE CARDS — said out loud (design §7). */}
        <div style={{ ...note, marginBottom: 10, fontSize: 12 }}>
          {!registry.loaded ? 'Reading which report kinds this company may upload…'
            : registry.error ? <span style={{ color: '#ef4444' }}>⚠️ The report-kind registry could not be read ({registry.error}) — the cards are withheld rather than guessed.</span>
            : <>Offered for {registry.declaration?.pos?.length ? <>POS <b>{registry.declaration.pos.join(' / ')}</b></> : 'no declared POS'} · {registry.declaration?.carriers?.length ? <>carrier <b>{registry.declaration.carriers.join(' / ')}</b></> : 'no declared carrier'}{!registry.ready && <> · registry table not applied yet — house defaults ({registry.payload?.migration})</>}{registry.withheld && <> · {registry.withheld}</>}</>}
        </div>
        <KindDetectZone visible={intakeCards} onPick={pickDetected} fallbackKey={intakeCards.find(c => c.landing === 'other')?.key} />
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>Your POS source</span>
          <input list="pos-sources" value={posRef} onChange={e => setPosRef(e.target.value)} placeholder="POS source (e.g. its name or code)" style={{ ...inp, width: 240 }} />
          <datalist id="pos-sources">{(state?.pos_sources || []).map(s2 => <option key={s2.pos_key} value={s2.pos_key}>{s2.label}</option>)}</datalist>
          <span style={note}>the POS exports below are filed under it — a second POS is a second line</span>
        </div>
        {intakeCards.map(c => (
          <div key={c.key} style={{ ...card, background: 'var(--bg,transparent)', marginBottom: 10 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{c.label}</div>
              {c.source_hint && <span style={{ ...note, fontSize: 12 }}>— {c.source_hint}</span>}
              <span style={{ ...note, fontSize: 11, marginLeft: 'auto' }}>{c.provenance_text}</span>
            </div>
            {c.what_in_it && <div style={note}>{c.what_in_it}</div>}
            {(c.recognisable_columns || []).length > 0 && <div style={{ ...note, fontSize: 11 }}>You&apos;ll recognise it by columns like {c.recognisable_columns!.slice(0, 5).join(', ')}.</div>}
            {/* WHERE IT SHOWS UP — the card's landing table's consumers, linked (owner 2026-09-20) */}
            <ShowsIn info={c.shows_in} loaded={registry.loaded} compact />
            {hint(c.landing) && c.landing !== 'sales' && c.landing !== 'pos' && c.landing !== 'inventory' && <div style={{ ...note, fontSize: 11, marginTop: 2 }}>{hint(c.landing)}{knownKinds.find(k => k.value === c.landing)?.target_table ? <> · lands in <code>{knownKinds.find(k => k.value === c.landing)?.target_table}</code></> : null}</div>}
            {actionFor(c)}
            {c.landing === 'inventory' && c.key === intakeCards.find(x => x.landing === 'inventory')?.key && (
              <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <span style={note}>or</span>
                <input value={noInvReason} onChange={e => setNoInvReason(e.target.value)} placeholder="No inventory export — because…" style={{ ...inp, width: 260 }} />
                <button style={btn} disabled={busy || !noInvReason.trim()} onClick={recordNoInventory}>Record: no inventory export</button>
                {noInv && <div style={{ fontSize: 12, width: '100%' }}><Lamp status={noInv.status} />No inventory export — recorded by {noInv.verified_by || '?'}: &quot;{String((noInv.verified_numbers as Record<string, unknown> | null)?.reason || '')}&quot;</div>}
              </div>
            )}
            {instLines(c.landing).filter((_, idx, arr) => c.key === intakeCards.find(x => x.landing === c.landing)?.key || arr.length === 0)}
          </div>
        ))}
        {registry.loaded && !registry.error && intakeCards.length === 0 && <div style={{ ...note, color: '#b45309' }}>No report kinds are offered for this company yet — declare its POS and carrier in Stage 1 (or ask the platform team to widen the set).</div>}
      </div>
    )
  }

  const title = `${cardLabel(reportKindOf, kind)} — ${kind === 'other' ? otherNameOf : sourceRef}${layout ? ` · ${layout.replace(/_/g, ' ')}` : ''}${kind === 'merchant_payments' && (a?.settlement_role || role) ? ` · ${a?.settlement_role || role}` : ''}`
  return (
    <div>
      <div style={{ ...note, marginBottom: 12 }}><b>{title}</b>{filename ? ` · ${filename}` : ''}{kept?.stored && !file ? <span style={{ color: '#15803d' }}> · file kept — no need to drop it again</span> : !file && !kept?.stored && a ? <span style={{ color: '#b45309' }}> · file not kept — drop it again in 2.1 to re-check or commit</span> : null}
        <button style={{ ...ghost, padding: '2px 8px', fontSize: 12, marginLeft: 8 }} onClick={() => setStep('2.0')}>← checklist</button></div>

      {step === '2.1' && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.1 — Upload the export</h2>
          <p style={{ ...note, marginBottom: 6 }}>Drop it exactly as your system produces it. We read every sheet, find the header row under any title block, and find the total row by its shape.</p>
          <ShowsIn info={registry.showsIn(reportKindOf) || (registry.forSurface('intake').find(k => k.landing === kind && (!layout || !k.layout || k.layout === layout))?.shows_in ?? null)} loaded={registry.loaded} style={{ marginBottom: 12 }} />
          <Dropzone file={file} filename={filename} onFiles={onFiles} dragOver={dragOver} setDragOver={setDragOver} hint={kind === 'x_report' ? 'Drop the X-report workbook (one sheet per store, one day — X-Report_MMDDYYYY-MMDDYYYY)' : kind === 'merchant_payments' ? 'Drop the settlement export (per merchant, per business day, per card brand)' : 'Drop the export here'} />
          {kind === 'merchant_payments' && portals && (
            <label style={{ fontSize: 13, display: 'block', marginTop: 10 }}>Settlement role — which side of the daily card tally this export answers:
              <select value={role} onChange={e => setRole(e.target.value)} style={{ ...inp, marginLeft: 8 }}>
                <option value="">the portal&apos;s default</option>
                {portals.roles.map(r => <option key={r} value={r}>{portals.role_titles?.[r] || r}</option>)}
              </select>
            </label>
          )}
          <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
            <button style={primary} disabled={busy || (!file && !canUseKept)} onClick={() => analyze().then(r => r && setStep('2.2'))}>{busy ? 'Reading…' : 'Read the file →'}</button>
            {saveUi}
          </div>
        </div>
      )}

      {step === '2.2' && a && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.2 — Sheet, header row, footer</h2>
          <p style={{ ...note, marginBottom: 12 }}>{a.matrix ? 'What the destination\'s own parser read from the file — there is nothing to map by hand.' : 'What we found in the file. Override anything that is wrong and re-read.'}</p>
          {a.detect.xlsx_repair && <div style={{ ...card, borderColor: '#f59e0b', background: 'rgba(245,158,11,.08)', fontSize: 13, marginBottom: 10 }}><b>This workbook needed a repair to be read</b> ({a.detect.xlsx_repair.action}): its string table was mis-named or missing. Every row was read; the repair is on the record.</div>}
          {a.matrix ? (
            <div style={{ ...card, background: 'var(--bg,transparent)', marginBottom: 12, fontSize: 13 }}>
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 8 }}>
                <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Sheet</th><th align="right">Rows read</th><th align="left">Outcome</th></tr></thead>
                <tbody>{a.detect.sheets.map(s2 => <tr key={s2.name} style={{ borderTop: '1px solid var(--border)' }}><td>{s2.name}</td><td style={mono}>{num(s2.data_rows)}</td><td>{s2.role}</td></tr>)}</tbody>
              </table>
              <div><b>Total row:</b> {a.detect.footer.detected ? <>found — value <b>{money(a.detect.footer.file_total_raw)}</b></> : 'not found'} · {a.detect.footer.basis}</div>
              {kind === 'x_report' && n?.parser && <div style={{ ...note, marginTop: 6 }}>Tender rows matched {num(n.parser.tender_rows_matched)} · skipped {num(n.parser.tender_rows_skipped)}{n.parser.unmatched_labels?.length ? <span style={{ color: '#ef4444' }}> · not recognised: {n.parser.unmatched_labels.join(', ')} — map them under Closing → Tender Config</span> : null}{n.parser.canon_matched_labels?.length ? <> · placed by the recon&apos;s own vocabulary: {n.parser.canon_matched_labels.join(', ')}</> : null}</div>}
              {kind === 'merchant_payments' && n?.warnings?.length ? <div style={{ ...note, marginTop: 6, color: '#b45309' }}>{n.warnings.join(' · ')}</div> : null}
            </div>
          ) : (
            <DetectPanel d={a.detect} sheet={sheet} setSheet={setSheet} headerRow={headerRow} setHeaderRow={setHeaderRow} footerMode={footerMode} setFooterMode={setFooterMode} />
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <button style={ghost} onClick={() => setStep('2.1')}>← Back</button>
            <button style={btn} disabled={busy || (!file && !canUseKept)} onClick={() => analyze({ keepStep: true })}>Re-read with these settings</button>
            <button style={primary} onClick={() => go(kind === 'other' ? '2.5' : a.matrix ? '2.4' : '2.3')}>{kind === 'other' ? 'What we can record →' : a.matrix ? 'Stores →' : 'Columns →'}</button>
            {saveUi}
          </div>
        </div>
      )}

      {step === '2.3' && a && kind !== 'other' && !a.matrix && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.3 — Confirm the columns</h2>
          <p style={{ ...note, marginBottom: 12 }}>Each platform field, the column we propose, where that proposal came from, and three values from that column. {isSales ? 'Store, date and amount are needed: they decide which slice of the table this file owns and which month each row books to.' : kind === 'bill_payments' ? 'The account / terminal id, the date, the amount and the product are needed: they decide the slice this file owns, and which rows your bill-payment rule counts.' : 'Store and SKU are needed; a unit without an IMEI yet is reported, not landed.'}</p>
          <ColumnsTable columns={a.columns} headers={a.detect.headers} columnMap={columnMap} setColumnMap={setColumnMap} />
          {a.money_columns.length > 1 && (
            <div style={{ ...card, background: 'var(--bg,transparent)', marginTop: 12, fontSize: 13 }}>
              <b>Money columns in this file</b> — the one mapped as the amount is summed; the others are recorded with their totals, never silently ignored.
              <ul style={{ margin: '6px 0 0 18px' }}>{a.money_columns.map(m => <li key={m.header}>{m.header}: <span style={mono}>{money(m.sum)}</span> over {num(m.cells)} cells {m.is_amount ? '— the amount' : '— recorded'}</li>)}</ul>
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            <button style={ghost} onClick={() => setStep('2.2')}>← Back</button>
            <button style={btn} disabled={busy || (!file && !canUseKept)} onClick={() => analyze({ keepStep: true })}>Re-check with these columns</button>
            <button style={primary} disabled={!canLeave23} title={canLeave23 ? '' : `Map ${requiredHere.join(', ')} first`}
              onClick={() => { persist('2.4', { column_map: columnMap }); analyze({ keepStep: true }).finally(() => setStep('2.4')) }}>Stores and reps →</button>
            {saveUi}
          </div>
        </div>
      )}

      {step === '2.4' && a && kind !== 'other' && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.4 — Stores and reps</h2>
          <p style={{ ...note, marginBottom: 12 }}>{a.identity_kind === 'merchant_id'
            ? 'Every merchant / account id in the file, resolved through the per-store merchant-id map every feed uses. An id we do not know must be assigned to one of your stores (saved to that map), created, or marked not yours with a reason — an unmapped id is never counted as a store\'s $0.'
            : kind === 'x_report' ? 'Every sheet name in the workbook is a store string, resolved by the same rules every closing recon uses (an alias you confirmed, the address, the store code). A name we do not know must be assigned, created, or marked not yours with a reason — the closing recons resolve the landed rows through the same alias.'
            : 'Every store string in the file, resolved against your stores by the same rules every report uses (an alias you confirmed, the address, the store code). A string we do not know must be assigned, created, or marked not yours with a reason — nothing lands as "Default".'}</p>
          <StoreResolver stores={a.stores} reps={a.reps} storeList={state?.stores || []} employees={state?.employees || []} decisions={decisions} setDecisions={setDecisions} identityKind={a.identity_kind} />
          <div style={{ display: 'flex', gap: 8, marginTop: 14, alignItems: 'center' }}>
            <button style={ghost} onClick={() => setStep(a.matrix ? '2.2' : '2.3')}>← Back</button>
            <button style={btn} disabled={busy || (!file && !canUseKept)} onClick={() => analyze({ keepStep: true, dec: decisions })}>Re-check with these decisions</button>
            <button style={primary} disabled={busy || unresolved.length > 0} title={unresolved.length ? `${unresolved.length} store string(s) unresolved` : ''}
              onClick={() => { persist('2.5', { identity: decisions }); analyze({ keepStep: true, dec: decisions }).finally(() => setStep('2.5')) }}>Our numbers →</button>
            {unresolved.length > 0 && <span style={{ ...note, color: '#ef4444' }}>{unresolved.length} unresolved: {unresolved.slice(0, 4).join(', ')}{unresolved.length > 4 ? ' …' : ''} — re-check after deciding</span>}
            {saveUi}
          </div>
        </div>
      )}

      {step === '2.5' && a && n && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.5 — {kind === 'other' ? 'What we can record' : 'Our numbers beside the file\'s'}</h2>
          <p style={{ ...note, marginBottom: 12 }}>{a.verify.basis}.</p>
          {kind === 'other' && (
            <div>
              <div style={{ ...card, borderColor: '#f59e0b', background: 'rgba(245,158,11,.08)', marginBottom: 10, fontSize: 13 }}>
                <b>No destination yet.</b> The platform has no table for &quot;{otherNameOf}&quot;. Confirming records it as <b>received</b> — {num(n.rows)} rows, {n.headers?.length} columns, the money columns below, and the kept file — and shows it that way in Stage 4. It is never written into another table and never dropped.
              </div>
              <div style={{ ...note, marginBottom: 6 }}>Columns: {n.headers?.join(' · ')}</div>
              {!!n.money_columns?.length && <ul style={{ margin: '6px 0 12px 18px', fontSize: 13 }}>{n.money_columns.map(m => <li key={m.header}>{m.header}: <span style={mono}>{money(m.sum)}</span> over {num(m.cells)} cells</li>)}</ul>}
            </div>
          )}
          {isSales && (
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 12 }}>
              <tbody>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Rows in file / usable / footer / excluded (not ours) / to land</td><td style={mono}>{num(a.verify.rows_in_file)} / {num(a.verify.rows_usable)} / {a.verify.footer_rows} / {a.verify.rows_excluded_not_ours} / <b>{num(a.verify.rows_to_land)}</b></td></tr>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Distinct transactions</td><td style={mono}>{num(n.distinct_txns)}</td></tr>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Σ amount</td><td style={{ ...mono, fontWeight: 700 }}>{money(n.sum_amount)}</td></tr>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Σ gross profit</td><td style={mono}>{money(n.sum_gp)}</td></tr>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Date span</td><td style={mono}>{n.date_span?.from || '—'} → {n.date_span?.to || '—'}{n.date_span?.undated_rows ? <span style={{ color: '#ef4444' }}> · {n.date_span.undated_rows} rows without a date</span> : null}</td></tr>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Voided / refund rows</td><td style={mono}>{num(n.voided_rows)}</td></tr>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px', verticalAlign: 'top' }}>Per store</td><td style={{ ...note, textAlign: 'right' }}>{(n.per_store || []).slice(0, 12).map(s => `${s.value}: ${num(s.count)} · ${money(s.sum)}`).join(' | ')}</td></tr>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px', verticalAlign: 'top' }}>Per rep</td><td style={{ ...note, textAlign: 'right' }}>{(n.per_rep || []).slice(0, 12).map(s => `${s.value}: ${num(s.count)} · ${money(s.sum)}`).join(' | ')}</td></tr>
              </tbody>
            </table>
          )}
          {kind === 'x_report' && n && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ ...note, marginBottom: 6 }}>Close date: <b>{n.close_date || <span style={{ color: '#ef4444' }}>unknown — enter it</span>}</b>{n.date_source ? ` (${n.date_source})` : ''} · stores {num(n.stores)} · tender rows {num(n.rows)} · tenders seen: {(n.tenders_seen || []).join(', ')}</div>
              {!n.close_date && <label style={{ fontSize: 13, display: 'block', margin: '6px 0 10px' }}>The file name carries no date — the day this X-report is for: <input type="date" value={asOf} onChange={e => setAsOf(e.target.value)} style={{ ...inp, marginLeft: 8, padding: '4px 6px' }} /> <button style={{ ...btn, marginLeft: 8 }} disabled={busy || (!file && !canUseKept)} onClick={() => analyze({ keepStep: true })}>Re-read</button></label>}
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Store (sheet)</th><th align="left">Date</th><th align="right">Cash</th><th align="right">Card</th><th align="right">Other</th><th align="right">Total</th><th align="right">Sheet&apos;s total</th><th align="right">Diff</th></tr></thead>
                <tbody>{((n.per_store_day || []) as XrNumbers['per_store_day']).map(r => <tr key={`${r.store}|${r.date}`} style={{ borderTop: '1px solid var(--border)' }}><td>{r.store}</td><td>{r.date}</td><td style={mono}>{money(r.cash)}</td><td style={mono}>{money(r.card)}</td><td style={mono}>{money(r.other)}</td><td style={{ ...mono, fontWeight: 700 }}>{money(r.total)}</td><td style={mono}>{money(r.file_total)}</td><td style={{ ...mono, color: r.difference === 0 ? '#16a34a' : r.difference === null ? 'inherit' : '#ef4444' }}>{r.difference === null ? '—' : money(r.difference)}</td></tr>)}
                  <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700 }}><td colSpan={2}>Σ</td><td style={mono}>{money(n.sum_cash)}</td><td style={mono}>{money(n.sum_card)}</td><td style={mono}>{money(n.sum_other)}</td><td style={mono}>{money(n.sum_total)}</td><td style={mono}>{money(n.file_total)}</td><td /></tr></tbody>
              </table>
            </div>
          )}
          {kind === 'merchant_payments' && n && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ ...note, marginBottom: 6 }}>Role <b>{n.settlement_role}</b> ({n.role_source}) · {n.grain} · merchants {(n.merchants || []).length} · days {(n.dates || []).length}{n.unmapped_merchants?.length ? <span style={{ color: '#ef4444' }}> · unmapped: {n.unmapped_merchants.join(', ')}</span> : null}{typeof n.overlap_with_other_sources === 'number' && n.overlap_with_other_sources > 0 ? <span style={{ color: '#ef4444' }}> · {n.overlap_with_other_sources} merchant-day(s) already landed by the scheduled pull</span> : null}</div>
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Merchant id</th><th align="left">Store</th><th align="left">Business day</th><th align="right">Gross</th><th align="right">Refunds</th><th align="right">Net</th><th align="right">Fees</th><th align="right">Txns</th></tr></thead>
                <tbody>{(n.per_merchant_day || []).map(r => <tr key={`${r.merchant_id}|${r.date}`} style={{ borderTop: '1px solid var(--border)' }}><td>{r.merchant_id}{r.store_label ? <span style={note}> · {r.store_label}</span> : null}</td><td>{r.store_code || <span style={{ color: '#ef4444' }}>unmapped</span>}</td><td>{r.date}</td><td style={mono}>{money(r.gross)}</td><td style={mono}>{money(r.refunds)}</td><td style={{ ...mono, fontWeight: 700 }}>{money(r.net)}</td><td style={mono}>{money(r.fees)}</td><td style={mono}>{num(r.count)}</td></tr>)}
                  <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700 }}><td colSpan={3}>Σ ours</td><td style={mono}>{money(n.sum_gross)}</td><td style={mono}>{money(n.sum_refunds)}</td><td style={mono}>{money(n.sum_net)}</td><td style={mono}>{money(n.sum_fees)}</td><td style={mono}>{num(n.txn_count)}</td></tr>
                  {n.file_totals && <tr style={{ color: 'var(--text2)' }}><td colSpan={3}>File&apos;s TOTAL row</td><td style={mono}>{money(n.file_totals.gross)}</td><td /><td style={mono}>{money(n.file_totals.net)}</td><td style={mono}>{money(n.file_totals.fees)}</td><td style={mono}>{num(n.file_totals.count)}</td></tr>}
                  {n.difference && <tr style={{ fontWeight: 700, color: (n.difference.gross === 0 && n.difference.net === 0 && n.difference.fees === 0 && n.difference.count === 0) ? '#16a34a' : '#ef4444' }}><td colSpan={3}>Difference</td><td style={mono}>{money(n.difference.gross)}</td><td /><td style={mono}>{money(n.difference.net)}</td><td style={mono}>{money(n.difference.fees)}</td><td style={mono}>{num(n.difference.count)}</td></tr>}</tbody>
              </table>
              <div style={{ ...note, marginTop: 6 }}>By card brand (net): {Object.entries(n.by_brand || {}).map(([b, v]) => `${b} ${money(v)}`).join(' · ')}</div>
            </div>
          )}
          {kind === 'bill_payments' && n && (
            <div style={{ marginBottom: 12 }}>
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 8 }}>
                <tbody>
                  <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Rows in file / usable / footer / to land</td><td style={mono}>{num(a.verify.rows_in_file)} / {num(a.verify.rows_usable)} / {a.verify.footer_rows} / <b>{num(a.verify.rows_to_land)}</b></td></tr>
                  <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Σ every row (the file&apos;s own total)</td><td style={{ ...mono, fontWeight: 700 }}>{money(n.sum_all_rows)}</td></tr>
                  <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Rows your bill-payment rule counts / does not (order type + product tokens — metric source of truth, product list)</td><td style={mono}>{num(n.billpay_rows)} / {num(n.non_billpay_rows)}</td></tr>
                  <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Σ bill payments (what the coverage recon will read) · count</td><td style={{ ...mono, fontWeight: 700 }}>{money(n.sum_amount)} · {num(n.count)}</td></tr>
                  <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Date span</td><td style={mono}>{n.date_span?.from || '—'} → {n.date_span?.to || '—'}</td></tr>
                  <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Feed this lands in</td><td style={{ textAlign: 'right' }}><code>{a.target_table}</code>{n.feed?.processor ? <span style={note}> · your recon reads &quot;{n.feed.processor}&quot;</span> : n.feed?.note ? <span style={{ ...note, color: '#b45309' }}> · {n.feed.note}</span> : null}</td></tr>
                </tbody>
              </table>
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Store</th><th align="left">Account / terminal</th><th align="left">Day</th><th align="right">Bill payments</th><th align="right">Count</th></tr></thead>
                <tbody>{((n.per_store_day || []) as BpNumbers['per_store_day']).slice(0, 60).map(r => <tr key={`${r.store}|${r.account_id}|${r.date}`} style={{ borderTop: '1px solid var(--border)' }}><td>{r.store}</td><td>{r.account_id}</td><td>{r.date}</td><td style={mono}>{money(r.amount)}</td><td style={mono}>{num(r.count)}</td></tr>)}</tbody>
              </table>
            </div>
          )}
          {kind === 'inventory' && (
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 12 }}>
              <tbody>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Rows in file / usable / footer / to land</td><td style={mono}>{num(a.verify.rows_in_file)} / {num(a.verify.rows_usable)} / {a.verify.footer_rows} / <b>{num(a.verify.rows_to_land)}</b></td></tr>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Units</td><td style={mono}>{num(n.units)}</td></tr>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Σ cost</td><td style={{ ...mono, fontWeight: 700 }}>{money(n.sum_cost)}</td></tr>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>Units with a device key (IMEI / serial) / without (ordered, not yet received — reported, not landed)</td><td style={mono}>{num(n.rows_with_device_key)} / <span style={{ color: n.rows_without_device_key ? '#b45309' : 'inherit' }}>{num(n.rows_without_device_key)}</span></td></tr>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px', verticalAlign: 'top' }}>Per store (units · Σ cost)</td><td style={{ ...note, textAlign: 'right' }}>{((n.per_store || []) as InvNumbers['per_store']).slice(0, 12).map(s => `${s.value}: ${num(s.units)} · ${money(s.sum)}`).join(' | ')}</td></tr>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>As of (the date this listing was taken)</td><td style={{ textAlign: 'right' }}><input type="date" value={asOf} onChange={e => setAsOf(e.target.value)} style={{ ...inp, padding: '4px 6px' }} /></td></tr>
              </tbody>
            </table>
          )}
          {kind !== 'other' && tie && (
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 12 }}>
              <tbody>
                <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700 }}><td style={{ padding: '6px 4px' }}>Our total</td><td style={mono}>{money(tie.our_total)}</td></tr>
                <tr><td style={{ padding: '4px 4px' }}>File&apos;s own total ({tie.file_total_source === 'footer' ? `footer row${a.detect.footer.rows.length ? ` ${a.detect.footer.rows.map(r => r.row).join(', ')}` : ''}` : tie.file_total_source === 'typed' ? 'typed by you' : 'none'})</td><td style={mono}>{money(tie.file_total)}</td></tr>
                <tr style={{ fontWeight: 700, color: tie.match ? '#16a34a' : '#ef4444' }}><td style={{ padding: '4px 4px' }}>Difference</td><td style={mono}>{tie.difference === null ? 'nothing to compare' : money(tie.difference)}</td></tr>
              </tbody>
            </table>
          )}
          {!!a.verify.ignored_money_columns.length && kind !== 'other' && <div style={{ ...note, marginBottom: 6 }}>Ignored money columns (recorded): {a.verify.ignored_money_columns.map(m => `"${m.header}" Σ ${money(m.sum)}`).join(' · ')}</div>}
          {!!Object.keys(a.verify.excluded || {}).length && <div style={{ ...note, marginBottom: 6 }}>Excluded as not yours: {Object.entries(a.verify.excluded).map(([k, v]) => `${k} (${v})`).join(' · ')}</div>}
          {!!a.verify.refusals.length && <div style={{ ...card, borderColor: '#ef4444', background: 'rgba(239,68,68,.06)', fontSize: 13, marginBottom: 10 }}><b>Before this can be confirmed:</b><ul style={{ margin: '4px 0 0 18px' }}>{a.verify.refusals.map((r, i) => <li key={i}>{r}</li>)}</ul></div>}
          {kind !== 'other' && !a.detect.footer.detected && (
            <label style={{ fontSize: 13, display: 'block', margin: '10px 0' }}>The file states no total — type the export&apos;s total (recorded as &quot;typed&quot;):
              <input value={typedTotal} onChange={e => setTypedTotal(e.target.value)} placeholder="e.g. 4196.10" style={{ ...inp, marginLeft: 8, width: 160 }} />
              <button style={{ ...btn, marginLeft: 8 }} disabled={busy || (!file && !canUseKept)} onClick={() => analyze({ keepStep: true })}>Recompute</button>
            </label>
          )}
          {needsAttest && (
            <label style={{ fontSize: 13, display: 'block', margin: '10px 0' }}>{tie?.match === false ? 'The totals differ.' : 'There is nothing to compare against.'} To proceed anyway, say why (recorded with your name):
              <textarea value={attest} onChange={e => setAttest(e.target.value)} rows={2} style={{ ...inp, width: '100%', marginTop: 4 }} placeholder="Reason for accepting this difference" />
            </label>
          )}
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button style={ghost} onClick={() => setStep(kind === 'other' ? '2.2' : '2.4')}>← Back</button>
            {kind !== 'other' && <button style={btn} disabled={busy || (!file && !canUseKept)} onClick={() => analyze({ keepStep: true })}>Recompute</button>}
            {kind !== 'other' && !a.matrix && <button style={ghost} onClick={() => setStep('2.3')}>Fix the columns</button>}
            <button style={primary} disabled={(kind !== 'other' && (a.verify.refusals.length > 0 && !attest.trim())) || (needsAttest && !attest.trim())} onClick={() => go(a.line_class ? '2.5a' : '2.6', { typed_total: typedTotal, as_of_date: asOf || null })}>{a.line_class ? 'Next: what counts as an activation →' : 'Confirm →'}</button>
            {saveUi}
          </div>
        </div>
      )}

      {step === '2.5a' && inst && (
        <LineClassStep instanceKey={instanceKey} who={who} analysis={a?.line_class || null}
          canReanalyze={!!a && (!!file || canUseKept)} reanalyze={() => analyze({ keepStep: true })}
          onBack={() => setStep('2.5')} onNext={() => go('2.6', {})} saveUi={saveUi} flash={flash} />
      )}

      {step === '2.6' && (a || inst?.status === 'verified' || inst?.status === 'needs_input') && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.6 — Confirm this export</h2>
          {!commitRes && <>
            <p style={{ ...note, marginBottom: 12 }}>{kind === 'other'
              ? 'Confirming records this report as received (columns, row count, money columns, the kept file). It gets a destination when the owner names one.'
              : kind === 'x_report' ? 'Confirming saves your store decisions (as aliases every closing recon resolves through), lands the tender rows through the X-report import (one row per store, day and tender — a re-upload restates the day), and re-reads them from the table.'
              : kind === 'merchant_payments' ? 'Confirming saves your merchant-id decisions to the per-store merchant-id map, lands the settlement rows exactly as the scheduled portal pull would (per merchant, business day and card brand, marked as an upload) and re-reads them. The daily card tally reads them from there.'
              : kind === 'bill_payments' ? 'Confirming saves the column map, saves your account decisions to the per-store merchant-id map, lands the rows in the processor bill-pay feed — replacing only the slice this file owns — and re-reads the bill payments through the very reader the coverage recon uses.'
              : `Confirming saves the column map for this layout, saves your store and rep decisions (as aliases your reports resolve through), imports the rows through the platform's importer — replacing only the slice this file owns — and then re-reads what landed. The numbers below come back from the table, not from this screen.`}</p>
            {a && kind !== 'other' && tie && <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>Confirm {title} — {isSales || kind === 'bill_payments' ? `${n?.date_span?.from} → ${n?.date_span?.to}` : kind === 'x_report' ? (n?.close_date || asOf) : kind === 'merchant_payments' ? `${n?.dates?.[0]} → ${n?.dates?.[n.dates.length - 1]}` : `as of ${asOf}`} — {money(tie.our_total)}</div>}
            {inst?.verified_numbers && !a && <div style={{ ...note, marginBottom: 12 }}>Last confirmed by {inst.verified_by || '?'} · {inst.verified_at || ''} · status {inst.status}{inst.blocking_reason ? ` · ${inst.blocking_reason}` : ''}. Re-read the file (2.1) to confirm again.</div>}
            {isSales && (
              <label style={{ ...note, display: 'block', marginBottom: 10 }}>
                <input type="checkbox" checked={replaceOther} onChange={e => setReplaceOther(e.target.checked)} style={{ marginRight: 6 }} />
                Replace rows of a <b>different report kind</b> already stored for these stores and dates. Leave this off: a landing that would delete another kind&apos;s rows is refused and names the loss, so nothing is overwritten by accident.
              </label>
            )}
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={ghost} onClick={() => setStep('2.5')}>← Back</button>
              <button style={primary} disabled={busy || !a || (!file && !canUseKept)} onClick={commit}>{busy ? 'Saving…' : kind === 'other' ? 'Record as received' : 'Confirm and import'}</button>
            </div>
          </>}
          {commitRes && (
            <div>
              <div style={{ ...card, borderColor: commitRes.ok && commitRes.verified !== false ? '#16a34a' : commitRes.recorded ? '#f59e0b' : '#ef4444', background: commitRes.ok && commitRes.verified !== false ? 'rgba(22,163,74,.06)' : commitRes.recorded ? 'rgba(245,158,11,.08)' : 'rgba(239,68,68,.06)', marginBottom: 12 }}>
                <div style={{ fontWeight: 700, fontSize: 15 }}>{commitRes.ok && commitRes.verified !== false ? 'Saved and verified' : commitRes.recorded ? 'Recorded as received — no destination yet' : commitRes.ok ? 'Rows landed, but NOT verified' : 'Saved, but NOT verified'} — {title}</div>
                {(!commitRes.ok || commitRes.verified === false) && <ul style={{ margin: '6px 0 0 18px', color: commitRes.recorded ? '#b45309' : '#ef4444', fontSize: 13 }}>{commitRes.problems.map((p2, i) => <li key={i}>{p2}</li>)}</ul>}
                {commitRes.ok && commitRes.verified === false && commitRes.activation_gate?.blocked && (
                  <div style={{ marginTop: 6 }}><button style={{ ...primary, fontSize: 12, padding: '4px 10px' }} onClick={() => setStep('2.5a')}>Map what counts as an activation (2.5a)</button></div>
                )}
                {!commitRes.recorded && <div style={{ ...note, marginTop: 6 }}>
                  Column map saved for {commitRes.mapping_saved?.length ?? 0} fields · aliases written {commitRes.identity_written?.aliases.length ?? 0} · stores created {commitRes.identity_written?.stores_created.length ?? 0} · rows inserted {num(commitRes.verified_numbers.rows_inserted)} · <b>rows re-read from the table: {num(commitRes.verified_numbers.rows_landed)}</b> of {num(commitRes.verified_numbers.rows_built)} built
                  {!!commitRes.verified_numbers.rows_without_device_key && <> · {commitRes.verified_numbers.rows_without_device_key} unit(s) without a device key not landed</>}
                  {!!commitRes.verified_numbers.rows_excluded_not_ours && <> · {commitRes.verified_numbers.rows_excluded_not_ours} row(s) excluded as not yours</>}
                  {!commitRes.state.saved && <> · your place was not saved ({commitRes.state.reason})</>}
                </div>}
              </div>
              {commitRes.verified_numbers.tie && (
                <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 12 }}>
                  <tbody>
                    <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700 }}><td style={{ padding: '5px 4px' }}>Our total (re-read)</td><td style={mono}>{money(commitRes.verified_numbers.tie.our_total)}</td></tr>
                    <tr><td style={{ padding: '5px 4px' }}>File&apos;s own total</td><td style={mono}>{money(commitRes.verified_numbers.tie.file_total)}</td></tr>
                    <tr style={{ fontWeight: 700, color: commitRes.verified_numbers.tie.match ? '#16a34a' : '#ef4444' }}><td style={{ padding: '5px 4px' }}>Difference</td><td style={mono}>{money(commitRes.verified_numbers.tie.difference)}</td></tr>
                  </tbody>
                </table>
              )}
              {commitRes.verified_numbers.attestation && <div style={{ ...note, marginBottom: 8 }}>Attested: &quot;{commitRes.verified_numbers.attestation.reason}&quot; — {commitRes.verified_numbers.confirmed_by}</div>}
              {/* 2.5a — the activation split of the landed rows, by the one predicate; or the gate that keeps this export unverified */}
              {commitRes.activation_gate?.checked && (
                <div style={{ ...card, background: 'var(--bg,transparent)', fontSize: 13, marginBottom: 10 }}>
                  <b>Activation types in the landed rows:</b>{' '}
                  {commitRes.activation_gate.open
                    ? (commitRes.activation_gate.attested ? <>none — attested by {commitRes.activation_gate.attested.by || '?'}: &quot;{commitRes.activation_gate.attested.reason}&quot;</> : <span style={{ color: '#ef4444' }}>none could be told apart — {commitRes.activation_gate.reason}</span>)
                    : <>{String((commitRes.verified_numbers as Record<string, unknown>).activation_classes ? ((commitRes.verified_numbers as { activation_classes: { activation_type_transactions: number } }).activation_classes.activation_type_transactions) : '')} activation-type invoices counted — the Sales Report and Executive MTD split them by type.</>}
                </div>
              )}
              {commitRes.verified_numbers.shows_in && <ShowsIn info={commitRes.verified_numbers.shows_in} lead="These rows now show in" />}
              {/* Stage C — the cross-checks the commit ran, stated with their basis */}
              {commitRes.verified_numbers.billpay_extract && (
                <div style={{ ...card, background: 'var(--bg,transparent)', fontSize: 13, marginBottom: 10 }}>
                  <b>Bill payments extracted from this export:</b> {commitRes.verified_numbers.billpay_extract.basis
                    ? <>{num(commitRes.verified_numbers.billpay_extract.lines)} line(s), {money(commitRes.verified_numbers.billpay_extract.sum)} — {commitRes.verified_numbers.billpay_extract.feed_present ? `the carrier's own bill-pay report is present for these days; difference ${money(commitRes.verified_numbers.billpay_extract.difference)}` : 'no carrier bill-pay report for these days, so this IS the bill-payments figure'} · <a href={commitRes.verified_numbers.billpay_extract.report || '/commcalc/bill-payments'}>open the Bill Payments report</a></>
                    : <span style={{ color: '#b45309' }}>{commitRes.verified_numbers.billpay_extract.error}</span>}
                </div>
              )}
              {commitRes.verified_numbers.sold_check && (
                <div style={{ ...card, background: 'var(--bg,transparent)', fontSize: 13, marginBottom: 10 }}>
                  <b>Auto-check against sales and activations:</b> {commitRes.verified_numbers.sold_check.basis
                    ? <><span style={{ color: commitRes.verified_numbers.sold_check.activated_not_rung_out ? '#b45309' : 'inherit' }}>{num(commitRes.verified_numbers.sold_check.activated_not_rung_out)} unit(s) activated but still on hand</span> · {num(commitRes.verified_numbers.sold_check.sold_not_cleared)} sold but still on hand{commitRes.verified_numbers.sold_check.activations_unpairable ? <> · {num(commitRes.verified_numbers.sold_check.activations_unpairable)} activation(s) could not be paired to a unit (listed, not guessed)</> : null}{!commitRes.verified_numbers.sold_check.activations_present && <> · no activation report loaded — checked against sales only</>} · <a href={commitRes.verified_numbers.sold_check.report || '/commcalc/inventory-sold-recon'}>open Inventory vs Sold</a></>
                    : <span style={{ color: '#b45309' }}>{commitRes.verified_numbers.sold_check.error}</span>}
                </div>
              )}
              <div style={{ display: 'flex', gap: 8 }}>
                <button style={primary} onClick={() => { setInstanceKey(''); setStep('2.0') }}>Another export?</button>
                {isSales && <a href="/commcalc/sales-report" style={{ ...ghost, textDecoration: 'none', display: 'inline-block' }}>Open the Sales report</a>}
                {kind === 'x_report' && <a href="/closing/cash-recon-management" style={{ ...ghost, textDecoration: 'none', display: 'inline-block' }}>Open the cash recon</a>}
                {kind === 'merchant_payments' && <a href="/closing/cash-recon-management" style={{ ...ghost, textDecoration: 'none', display: 'inline-block' }}>Open the card settlement recon</a>}
                {kind === 'bill_payments' && <a href="/commcalc/bill-payments" style={{ ...ghost, textDecoration: 'none', display: 'inline-block' }}>Open the Bill Payments report</a>}
                {!commitRes.ok && !commitRes.recorded && <button style={ghost} onClick={() => { setCommitRes(null); setStep('2.5') }}>Back to the numbers</button>}
                {isSales && <button style={ghost} onClick={() => setStep('2.5a')}>What counts as an activation (2.5a)</button>}
              </div>
            </div>
          )}
        </div>
      )}

      {KEYS.includes(step) && step !== '2.1' && step !== '2.0' && step !== '2.5a' && !a && !(step === '2.6' && inst && (inst.status === 'verified' || inst.status === 'needs_input')) && (
        <div style={card}>
          <div style={note}>Nothing to show for this step yet — read the file at 2.1 first{kept?.stored ? ' (it was kept; no need to drop it again)' : ''}.</div>
          <button style={{ ...primary, marginTop: 10 }} onClick={() => setStep('2.1')}>Go to 2.1</button>
        </div>
      )}
    </div>
  )
}
