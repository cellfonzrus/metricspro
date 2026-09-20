'use client'
// ═══════════════════════════════════════════════════════════════════════════════════════════════
// TENANT ONBOARDING — the INTAKE (design 2026-09-20). One page, one rail, five stages:
//   1. Company setup — NOT built here: a lamp that reads the existing company / store / carrier rows.
//   2. Sales & inventory — stage2.tsx (2.0 "What do you have?" … 2.6), on the same spine as stage 3.
//   3. Commission statements — steps 3.1–3.9 below; 3.7 now RESOLVES stores through the shared resolver.
//   4. Verify everything — one table, one row per source: our total, the file's, the difference, who,
//      when; a red row links to the step that fixes it; then the sign-off.
//   5. Done — the two links (Sales report, Commissions) and what to upload each month.
//
// Owner: "I will not do anything manually — the system should ask me while onboarding under 3.4 what
// is considered commission positive or negative. The user does not know how this system works, they
// only can upload their existing data … assign the existing fields to the uploaded data and SAVE
// that, use the intelligence to assign the categories to the fields and ask the user to confirm.
// Whatever is being uploaded should be able to save is most important."
//
// ONE SHAPE (design §0.1): upload → the platform pre-fills everything it can, with provenance →
// the person confirms only what a file cannot say → our numbers beside the file's → confirm. The left
// rail is a PROJECTION of the persisted state (GET /commcalc/onboarding/intake/state); every choice
// is written back through PUT /state as it is made, so leaving and returning lands on the same step.
// The dropped file is KEPT by the backend, so re-checking or committing later needs no re-drop.
//
// This page owns NO parsing, NO classification, NO resolution and NO persistence of its own.
// No carrier, POS vendor or tenant is named in this file (RULE TWO).
// ═══════════════════════════════════════════════════════════════════════════════════════════════
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, apiUpload } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import {
  BASE, card, note, inp, btn, primary, ghost, mono, money, num, LAMP, Lamp, Badge, DetectPanel, ColumnsTable, StoreResolver, Dropzone,
  useAutoSave, SaveButton,
  type StateResp, type Column, type MoneyCol, type Detect, type StoreRow, type RepRow, type Tie, type FileRef, type IdentityDecisions,
  type BucketRow, type BucketMeta, type SaveResult,
} from './intake-shared'
import { Stage2Flow, STAGE2_STEPS } from './stage2'
import { useReportKinds } from '@/lib/report-kinds'
import { STATEMENT_TYPE_DEFAULT, statementTypeToken } from '@/lib/statement-type'

// ── stage-3 payload types (mirror onboarding_intake.py) ─────────────────────────────────────────
type SignRow = { label: string; sub_label: string; store: string; amount: number }
type Label = {
  label: string; match_field: string | null; count: number; sum_raw: number; sum_canonical: number | null
  positives: number; negatives: number; zeros: number; sign_mix: string
  sub_labels: { sub_label: string; count: number; sum_raw: number }[]
  reversal_flag: boolean; bucket: string; provenance: string | null; provenance_label: string | null
}
type Bucket = { gross: number; chargebacks: number; net: number; count: number; kind?: string; label?: string }
type Totals = {
  buckets: Record<string, Bucket>; bucket_labels: Record<string, string>; bucket_order?: string[]
  earned_total?: number; deductions_total?: number; net_total: number
  other: Bucket; unlisted?: Bucket & { keys: Record<string, number> }; charges: { total: number; count: number }; rows: number
}
type LedgerTie = Tie & { other_unassigned: number; other_count: number }
type Analysis = {
  source_kind: string; statement_type: string; carrier: { id: string; name: string; code: string }
  source_report: string; instance_key: string; filename?: string | null
  detect: Detect; columns: Column[]; money_columns: MoneyCol[]
  sign: { question: string; positive: SignRow[]; negative: SignRow[]; positive_count: number; negative_count: number
          options: { value: string; label: string }[]; answer: string | null; stored_answer: string | null; convention: string | null; answered: boolean }
  labels: Label[]; buckets: string[]; bucket_labels: Record<string, string>; unassigned: string[]
  bucket_rows?: BucketRow[]; bucket_meta?: BucketMeta
  blank_label?: { count: number; sum_raw: number; message: string } | null
  identity: Record<string, { value: string; count: number; sum_raw: number }[]>
  stores: StoreRow[]; reps: RepRow[]; unresolved_stores: string[]
  period: { months: { period: string; rows: number }[]; span_from: string | null; span_to: string | null; proposed: string | null; spans_two_months: boolean; dated_rows: number }
  verify: { basis: string; totals: Totals | null; tie: LedgerTie | null; banners: string[]; rows_in_file: number; rows_usable: number; footer_rows: number; rows_to_land: number; ignored_money_columns: MoneyCol[] }
  file?: FileRef; state?: StateResp
}
type CommitResp = {
  ok: boolean; problems: string[]; saved: number; source_report: string; period: string
  carrier: { id: string; name: string; code: string }; mapping_saved: string[]; sign_convention: string
  rules_saved: number; identity_written?: { aliases: [string, string][]; stores_created: string[] }
  verified_numbers: { rows_in_file: number; rows_usable: number; footer_rows_dropped: number; rows_built: number; rows_landed: number; rows_inserted: number; totals: Totals; tie: LedgerTie; attestation: { reason: string } | null; confirmed_by: string | null; confirmed_at: string }
  state: { saved: boolean; reason?: string }
}
type SignAnswer = 'positive' | 'negative' | null
type Assignment = { label: string; match_field?: string | null; bucket: string; is_reversal: boolean }

const STEPS: [string, string][] = [
  ['3.1', 'Upload the statement'], ['3.2', 'Sheet, header row, footer'], ['3.3', 'Confirm the columns'],
  ['3.4', 'Which sign is money earned'], ['3.5', 'Labels found in the file'], ['3.6', 'Put every label in a bucket'],
  ['3.7', 'Store / account attribution'], ['3.8', 'Our totals beside the file\'s'], ['3.9', 'Confirm this statement'],
]
const STEP_KEYS = STEPS.map(s => s[0])
const STAGE2_KEYS = STAGE2_STEPS.map(s => s[0])
const UNASSIGNED = 'unassigned'
// The two 3.4 answers, verbatim from the design. The backend sends the same pair; this is the fallback.
const SIGN_OPTIONS = [{ value: 'positive', label: 'Earned is positive' }, { value: 'negative', label: 'Earned is negative' }]
const STAGE_TITLES: Record<string, string> = { '1': 'Company setup', '2': 'Sales & inventory intake', '3': 'Commission statement intake', '4': 'Verify everything', '5': 'Done — the monthly runbook' }

export default function OnboardingIntakePage() {
  const { user } = useAuth()
  const who = user?.full_name || user?.email || ''
  const [state, setState] = useState<StateResp | null>(null)
  const [stateErr, setStateErr] = useState('')
  const [stage, setStage] = useState('2')
  const [step, setStep] = useState('2.0')
  const [s2Instance, setS2Instance] = useState('')
  const [carrierId, setCarrierId] = useState('')
  const [newCarrier, setNewCarrier] = useState('')
  const [statementType, setStatementType] = useState('commission statement')
  // THE STATEMENT TYPES OFFERED AT 3.1 ARE THE REGISTRY'S commission-family kinds (design §7): a
  // carrier that sends a separate residual file gets the residual card — a row, not a branch. The
  // input stays free text (a type nobody has defined is still allowed); the matching card's key rides
  // along as `report_kind` so the confirmed layout is learned under it.
  const registry = useReportKinds()
  const statementKinds = useMemo(() => registry.visible.filter(k => k.landing === 'commission'), [registry.visible])
  // The typed text names a registry TOKEN the same way the backend's `statement_type_token` reads
  // it (longest known token contained in the text; blank = the default). The mapping the commit
  // saves is keyed by that token (index §30.10), so a residual statement keeps its own map and sign.
  const statementToken = useMemo(() => statementTypeToken(statementType, statementKinds.map(k => k.statement_type || '')), [statementType, statementKinds])
  const statementReportKind = useMemo(() => statementKinds.find(k => (k.statement_type || STATEMENT_TYPE_DEFAULT) === statementToken)?.key || '', [statementKinds, statementToken])
  const [file, setFile] = useState<File | null>(null)
  const [filename, setFilename] = useState('')
  const [kept, setKept] = useState<FileRef | null>(null)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [columnMap, setColumnMap] = useState<Record<string, string>>({})
  const [sheet, setSheet] = useState('')
  const [headerRow, setHeaderRow] = useState('')
  const [footerMode, setFooterMode] = useState<'auto' | 'none'>('auto')
  // 3.4 — NO default. The buttons start unselected and the flow cannot proceed until one is pressed.
  const [signAnswer, setSignAnswer] = useState<SignAnswer>(null)
  const [assign, setAssign] = useState<Record<string, string>>({})
  const [reversal, setReversal] = useState<Record<string, boolean>>({})
  const [decisions, setDecisions] = useState<IdentityDecisions>({})
  const [period, setPeriod] = useState('')
  const [typedTotal, setTypedTotal] = useState('')
  const [attest, setAttest] = useState('')
  const [commitRes, setCommitRes] = useState<CommitResp | null>(null)
  const [signName, setSignName] = useState('')
  const [signRole, setSignRole] = useState('')
  const [signBehalf, setSignBehalf] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const restored = useRef(false)

  const flash = useCallback((m: string) => { setMsg(m); setTimeout(() => setMsg(''), 7000) }, [])
  const carriers = state?.carriers || []
  const instanceKey = analysis?.instance_key || (carrierId ? `commission:${carrierId}:${statementType.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'commission_statement'}` : '')

  // ── the rail, and resuming where the person left off ─────────────────────────────────────────
  const loadState = useCallback(async (ik?: string) => {
    try {
      const d: StateResp = await api(`${BASE}/state${ik ? `?instance_key=${encodeURIComponent(ik)}` : ''}`)
      setState(d)
      return d
    } catch (e: unknown) { setStateErr((e as Error)?.message || 'Could not load the onboarding state'); return null }
  }, [])

  useEffect(() => {
    // Sync FROM the external system (the persisted state) — setState only inside the callback.
    let alive = true
    api(`${BASE}/state`).then((d: StateResp) => {
      if (!alive) return
      setState(d)
      if (!d || restored.current) return
      restored.current = true
      const r = d.rail?.resume
      const inst = r ? d.rail.instances.find(i => i.instance_key === r.instance_key) : null
      if (!inst) { setStage(d.rail?.instances?.length ? '4' : '2'); return }
      if (inst.stage === '2') {
        setStage('2'); setS2Instance(inst.instance_key)
        setStep(inst.status === 'verified' ? '2.6' : STAGE2_KEYS.includes(r!.step) ? r!.step : '2.1')
        flash(`Resumed Stage 2 at step ${inst.status === 'verified' ? '2.6' : r!.step} — ${inst.label}.`)
        return
      }
      const p = (inst.payload || {}) as Record<string, unknown>
      setStage('3')
      if (typeof p.carrier_id === 'string') setCarrierId(p.carrier_id)
      if (typeof p.statement_type === 'string') setStatementType(p.statement_type)
      if (p.column_map && typeof p.column_map === 'object') setColumnMap(p.column_map as Record<string, string>)
      if (p.sign_answer === 'positive' || p.sign_answer === 'negative') setSignAnswer(p.sign_answer)
      if (Array.isArray(p.assignments)) {
        const a: Record<string, string> = {}; const rv: Record<string, boolean> = {}
        for (const x of p.assignments as Assignment[]) { a[x.label] = x.bucket; rv[x.label] = !!x.is_reversal }
        if (p.reversal_flags && typeof p.reversal_flags === 'object') for (const [k, v] of Object.entries(p.reversal_flags as Record<string, boolean>)) rv[k] = !!v
        setAssign(a); setReversal(rv)
      }
      if (p.identity && typeof p.identity === 'object') setDecisions(p.identity as IdentityDecisions)
      if (typeof p.period === 'string') setPeriod(p.period)
      if (typeof p.typed_total === 'string') setTypedTotal(p.typed_total)
      // every field the auto-save writes comes back (owner 2026-09-20: "if you refresh … it is not being saved")
      if (typeof p.attestation === 'string') setAttest(p.attestation)
      if (typeof p.sheet === 'string') setSheet(p.sheet)
      if (typeof p.header_row === 'string') setHeaderRow(p.header_row)
      if (p.footer_mode === 'auto' || p.footer_mode === 'none') setFooterMode(p.footer_mode)
      if (typeof p.filename === 'string') setFilename(p.filename)
      if (p.file && typeof p.file === 'object') setKept(p.file as FileRef)
      if (p.analysis && typeof p.analysis === 'object') setAnalysis(p.analysis as Analysis)
      if (inst.status === 'verified') setStep('3.9')
      else if (STEP_KEYS.includes(r!.step)) setStep(r!.step)
      const keptFile = p.file && typeof p.file === 'object' && (p.file as FileRef).stored
      flash(`Resumed at step ${inst.status === 'verified' ? '3.9' : r!.step}${typeof p.filename === 'string' ? ` — ${p.filename}` : ''}. ${keptFile ? 'The file was kept — no need to drop it again.' : 'Re-attach the file if you need to re-check or commit.'}`)
    }).catch((e: unknown) => { if (alive) setStateErr((e as Error)?.message || 'Could not load the onboarding state') })
    return () => { alive = false }
  }, [flash])

  const persist = useCallback(async (stepKey: string, patch: Record<string, unknown>, status?: string) => {
    if (!instanceKey) return
    try {
      const d: StateResp = await api(`${BASE}/state`, { method: 'PUT', body: JSON.stringify({ instance_key: instanceKey, step: stepKey, status, payload: patch, by: who }) })
      setState(prev => prev ? { ...prev, rail: d.rail, state_ready: d.state_ready, save: d.save } : d)
    } catch { /* the rail is a convenience; the flow keeps working without it */ }
  }, [instanceKey, who])

  const go = useCallback((k: string, patch: Record<string, unknown> = {}) => {
    setStep(k)
    persist(k, patch)
  }, [persist])

  // ── the assignments as the backend wants them ────────────────────────────────────────────────
  const assignments: Assignment[] = useMemo(() => {
    const labels = analysis?.labels || []
    return labels.map(l => ({ label: l.label, match_field: l.match_field, bucket: assign[l.label] || UNASSIGNED, is_reversal: !!reversal[l.label] }))
  }, [analysis, assign, reversal])
  const unassigned = useMemo(() => assignments.filter(a => a.bucket === UNASSIGNED).map(a => a.label), [assignments])

  // ── AUTO-SAVE (owner 2026-09-20): every choice on every step is written, debounced, with the step
  // kept where it is; the Save button flushes the same write at once; the indicator says what happened.
  // PUT /state merges per key on the backend, so a debounced `assignments` write never clobbers a
  // `sign_answer` written a moment earlier (pinned in harness_onboarding_intake.py §K).
  const persistNow = useCallback(async (patch: Record<string, unknown>, keepalive = false): Promise<SaveResult> => {
    if (!instanceKey) return { saved: false, reason: 'pick the carrier first' }
    const d: StateResp = await api(`${BASE}/state`, { method: 'PUT', body: JSON.stringify({ instance_key: instanceKey, step, payload: patch, by: who }), keepalive })
    setState(prev => prev ? { ...prev, rail: d.rail, state_ready: d.state_ready, save: d.save } : d)
    if (d.state_ready === false) return { saved: false, reason: `run migration ${d.migration}` }
    return d.save || { saved: true }
  }, [instanceKey, step, who])
  const { schedule, saveNow, status: saveStatus } = useAutoSave(persistNow)
  const draft = useMemo(() => ({
    kind: 'commission', carrier_id: carrierId, statement_type: statementType,
    assignments: assignments.filter(x => x.bucket !== UNASSIGNED),
    reversal_flags: reversal, sign_answer: signAnswer, identity: decisions, typed_total: typedTotal, period,
    attestation: attest, column_map: columnMap, sheet, header_row: headerRow, footer_mode: footerMode,
  }), [carrierId, statementType, assignments, reversal, signAnswer, decisions, typedTotal, period, attest, columnMap, sheet, headerRow, footerMode])
  const draftSeen = useRef('')
  useEffect(() => {
    if (stage !== '3' || !instanceKey || !analysis) return
    const key = JSON.stringify(draft)
    if (!draftSeen.current) { draftSeen.current = key; return }        // the restore / first read is not a change
    if (key === draftSeen.current) return
    draftSeen.current = key
    schedule(draft)
  }, [draft, stage, instanceKey, analysis, schedule])
  const saveUi = <SaveButton onSave={() => saveNow(draft)} status={saveStatus} stateReady={state?.state_ready !== false} migration={state?.migration} busy={busy} />

  // ── THE BUCKETS ARE THE REGISTRY (mig 1009): whatever the backend sends, in its order — no key named here
  const bucketRows: BucketRow[] = useMemo(() => (analysis?.bucket_rows || state?.bucket_rows || []).filter(b => b.is_active !== false), [analysis, state])
  const bucketKind = useCallback((k: string) => bucketRows.find(b => b.key === k)?.kind || 'earned', [bucketRows])
  const earnedKeys = useMemo(() => bucketRows.filter(b => b.kind !== 'deduction').map(b => b.key), [bucketRows])
  const dedKeys = useMemo(() => bucketRows.filter(b => b.kind === 'deduction').map(b => b.key), [bucketRows])
  const trimmedAnalysis = (a: Analysis) => ({ ...a, state: undefined, labels: a.labels.slice(0, 400).map(l => ({ ...l, sub_labels: (l.sub_labels || []).slice(0, 25) })) })
  const canUseKept = !file && !!kept?.stored

  // ── analyze (read-only) ───────────────────────────────────────────────────────────────────────
  const analyze = useCallback(async (opts: { cm?: Record<string, string>; sign?: SignAnswer; asg?: Assignment[]; dec?: IdentityDecisions; typed?: string; keepStep?: boolean } = {}) => {
    if (!file && !kept?.stored) { flash('Attach the statement file first.'); return null }
    if (!carrierId) { flash('Pick the carrier this statement is from.'); return null }
    setBusy(true); setMsg('')
    try {
      const fd = new FormData()
      if (file) fd.append('file', file); else { fd.append('use_stored', '1'); fd.append('instance_key', instanceKey) }
      fd.append('source_kind', 'commission'); fd.append('carrier_id', carrierId)
      fd.append('statement_type', statementType)
      if (statementReportKind) fd.append('report_kind', statementReportKind)
      const cm = opts.cm ?? columnMap
      if (Object.keys(cm).length) fd.append('column_map', JSON.stringify(cm))
      const sa = opts.sign === undefined ? signAnswer : opts.sign
      if (sa) fd.append('sign_answer', sa)
      const asg = opts.asg ?? assignments
      if (asg.length) fd.append('assignments', JSON.stringify(asg.filter(a => a.bucket !== UNASSIGNED)))
      const dec = opts.dec ?? decisions
      if (dec.store || dec.rep) fd.append('identity', JSON.stringify(dec))
      const tt = opts.typed ?? typedTotal
      if (tt) fd.append('typed_total', tt)
      if (sheet) fd.append('sheet', sheet)
      if (headerRow !== '') fd.append('header_row', headerRow)
      fd.append('footer', footerMode)
      const a: Analysis = await apiUpload(`${BASE}/analyze`, fd)
      setAnalysis(a)
      if (file) { setFilename(file.name); if (a.file) setKept(a.file) }
      // the proposal fills what the person has not chosen yet; their own picks are kept
      const next: Record<string, string> = { ...cm }
      for (const c of a.columns) if (!(c.target_field in next) && c.column) next[c.target_field] = c.column
      setColumnMap(next)
      // labels: pre-place from the suggestion where the person has not placed them
      setAssign(prev => {
        const out: Record<string, string> = { ...prev }
        for (const l of a.labels) if (!(l.label in out) && l.bucket !== UNASSIGNED) out[l.label] = l.bucket
        return out
      })
      setReversal(prev => {
        const out: Record<string, boolean> = { ...prev }
        for (const l of a.labels) if (!(l.label in out)) out[l.label] = !!l.reversal_flag
        return out
      })
      if (!period && a.period?.proposed) setPeriod(a.period.proposed)
      if (a.sign?.stored_answer && !sa) setSignAnswer(a.sign.stored_answer as SignAnswer)
      if (a.state) setState(prev => prev ? { ...prev, rail: a.state!.rail, state_ready: a.state!.state_ready } : prev)
      await persist(opts.keepStep ? step : '3.2', { carrier_id: carrierId, carrier_name: a.carrier?.name, statement_type: statementType, filename: file?.name || filename, column_map: next, identity: dec, analysis: trimmedAnalysis(a), kind: 'commission' })
      return a
    } catch (e: unknown) { flash((e as Error)?.message || 'Could not read the file'); return null }
    finally { setBusy(false) }
  }, [file, kept, carrierId, instanceKey, statementType, statementReportKind, columnMap, signAnswer, assignments, decisions, typedTotal, sheet, headerRow, footerMode, period, persist, step, filename, flash])

  // ── 3.9 commit (the SAVE) ────────────────────────────────────────────────────────────────────
  const commit = useCallback(async () => {
    if (!file && !kept?.stored) { flash('Re-attach the statement file to commit — it was not kept.'); return }
    if (!signAnswer) { flash('3.4 is unanswered.'); setStep('3.4'); return }
    if (unassigned.length) { flash(`${unassigned.length} label(s) are still Unassigned.`); setStep('3.6'); return }
    if (!period.trim()) { flash('Enter the period this statement is for (e.g. August 2026).'); return }
    setBusy(true); setMsg('')
    try {
      const fd = new FormData()
      if (file) fd.append('file', file); else { fd.append('use_stored', '1'); fd.append('instance_key', instanceKey) }
      fd.append('source_kind', 'commission'); fd.append('carrier_id', carrierId)
      fd.append('statement_type', statementType); fd.append('period', period.trim())
      if (statementReportKind) fd.append('report_kind', statementReportKind)
      fd.append('column_map', JSON.stringify(columnMap)); fd.append('sign_answer', signAnswer)
      fd.append('assignments', JSON.stringify(assignments.filter(a => a.bucket !== UNASSIGNED)))
      if (decisions.store || decisions.rep) fd.append('identity', JSON.stringify(decisions))
      if (attest.trim()) fd.append('attestation', JSON.stringify({ reason: attest.trim() }))
      if (typedTotal) fd.append('typed_total', typedTotal)
      if (sheet) fd.append('sheet', sheet)
      if (headerRow !== '') fd.append('header_row', headerRow)
      fd.append('footer', footerMode); fd.append('verified_by', who)
      const r: CommitResp = await apiUpload(`${BASE}/commit`, fd)
      setCommitRes(r)
      setStep('3.9')
      await loadState(instanceKey)
      if (!r.ok) flash('Committed with problems — see the red panel. Nothing here is reported as verified until the re-read matches.')
    } catch (e: unknown) { flash((e as Error)?.message || 'Commit refused') }
    finally { setBusy(false) }
  }, [file, kept, signAnswer, unassigned, period, carrierId, instanceKey, statementType, statementReportKind, columnMap, assignments, decisions, attest, typedTotal, sheet, headerRow, footerMode, who, loadState, flash])

  // ── carrier creation (a carrier is a ROW — design §0.4) ──────────────────────────────────────
  async function addCarrier() {
    const name = newCarrier.trim()
    if (!name) return
    setBusy(true)
    try {
      await api('/api/v1/commcalc/carriers', { method: 'POST', body: JSON.stringify({ name, code: name.toLowerCase().replace(/\s+/g, '-') }) })
      const d = await loadState()
      const made = d?.carriers.find(c => c.name.toLowerCase() === name.toLowerCase())
      if (made) setCarrierId(made.id)
      setNewCarrier('')
      flash(`Added ${name} as a carrier. It starts with no presets — this flow will learn its layout from the file.`)
    } catch (e: unknown) { flash((e as Error)?.message || 'Could not add the carrier') }
    setBusy(false)
  }

  // ── 4.2 sign-off ──────────────────────────────────────────────────────────────────────────────
  async function signOff() {
    setBusy(true)
    try {
      const d: StateResp & { signed_off?: unknown } = await api(`${BASE}/sign-off`, { method: 'POST', body: JSON.stringify({ name: signName.trim() || who, role: signRole.trim(), on_behalf: signBehalf }) })
      setState(d)
      setStage('5'); setStep('5.1')
      flash('Signed off. The runbook below is what to upload each month.')
    } catch (e: unknown) { flash((e as Error)?.message || 'Sign-off refused') }
    setBusy(false)
  }

  function onFiles(fl: FileList | null) {
    const f = fl?.[0]
    if (!f) return
    if (!/\.(xlsx|xls|csv)$/i.test(f.name)) { flash('Drop an .xlsx, .xls or .csv file.'); return }
    setFile(f); setFilename(f.name); setCommitRes(null)
  }

  function resetForAnother() {
    setStep('3.1'); setFile(null); setFilename(''); setKept(null); setAnalysis(null); setColumnMap({}); setSheet(''); setHeaderRow(''); setFooterMode('auto')
    setSignAnswer(null); setAssign({}); setReversal({}); setDecisions({}); setPeriod(''); setTypedTotal(''); setAttest(''); setCommitRes(null); setCarrierId('')
  }

  function openStage(s: string) {
    setStage(s)
    setStep(s === '2' ? (s2Instance ? step.startsWith('2.') ? step : '2.1' : '2.0') : s === '3' ? (STEP_KEYS.includes(step) ? step : '3.1') : s === '4' ? '4.1' : s === '5' ? '5.1' : step)
  }
  function openRow(ik: string, stg: string, fixStep: string) {
    if (stg === '2') { setS2Instance(ik); setStage('2'); setStep(fixStep) }
    else {
      const inst = state?.rail.instances.find(i => i.instance_key === ik)
      const p = (inst?.payload || {}) as Record<string, unknown>
      if (typeof p.carrier_id === 'string') setCarrierId(p.carrier_id)
      if (typeof p.statement_type === 'string') setStatementType(p.statement_type)
      if (p.file && typeof p.file === 'object') setKept(p.file as FileRef)
      if (typeof p.filename === 'string') setFilename(p.filename)
      if (p.analysis && typeof p.analysis === 'object') setAnalysis(p.analysis as Analysis)
      setStage('3'); setStep(fixStep)
    }
  }

  const a = analysis
  const headers = a?.detect.headers || []
  const buckets = bucketRows.length ? bucketRows.map(b => b.key) : (a?.buckets || state?.buckets || [])
  const bucketLabels = { ...(state?.bucket_labels || {}), ...(a?.bucket_labels || {}) }
  const bucketMeta = a?.bucket_meta || state?.bucket_meta
  const canLeave33 = !!columnMap.raw_amount && !!columnMap.product_name
  const tie = a?.verify.tie || null
  const needsAttest = !!a && (tie?.match === false || tie?.match === null)
  const unresolvedStores = a?.stores ? a.stores.filter(s => s.status !== 'resolved').map(s => s.value) : []
  const rail = state?.rail
  const stageRows = (stg: string) => (rail?.instances || []).filter(i => i.stage === stg)
  const stepsOf = stage === '2' ? STAGE2_STEPS : stage === '3' ? STEPS : stage === '4' ? [['4.1', 'Every source, verified'], ['4.2', 'Sign-off']] as [string, string][] : [['5.1', 'Monthly runbook']] as [string, string][]
  const stepKeysOf = stepsOf.map(s => s[0])

  return (
    <div style={{ display: 'flex', gap: 20, padding: 24, alignItems: 'flex-start' }}>
      {/* ── LEFT RAIL — a projection of persisted state ───────────────────────────────────── */}
      <aside style={{ ...card, width: 280, flex: '0 0 280px', position: 'sticky', top: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>Onboarding</div>
        {(rail?.stages || []).map(s => (
          <div key={s.stage} style={{ marginBottom: 8 }}>
            <div onClick={() => s.built && openStage(s.stage)} title={s.note || ''}
              style={{ fontSize: 13, fontWeight: s.stage === stage ? 700 : 500, color: s.built ? 'var(--text)' : 'var(--text2)', cursor: s.built ? 'pointer' : 'default' }}>
              <Lamp status={s.status} />{s.stage}. {s.label}{!s.built && <span style={{ ...note, fontSize: 11 }}> · {s.stage === '1' ? `${state?.company.stores ?? 0} stores · ${state?.company.carriers ?? 0} carriers` : 'next build'}</span>}
            </div>
            {s.stage === stage && s.built && (
              <div style={{ marginLeft: 18, marginTop: 4 }}>
                {stepsOf.map(([k, l]) => {
                  const done = stepKeysOf.indexOf(k) < stepKeysOf.indexOf(step)
                  return (
                    <div key={k} onClick={() => (done || k === step) && setStep(k)}
                      style={{ fontSize: 12, padding: '3px 6px', borderRadius: 6, cursor: done ? 'pointer' : 'default',
                        background: k === step ? 'var(--accent,#2563eb)' : 'transparent', color: k === step ? '#fff' : done ? 'var(--text2)' : 'var(--text3)' }}>
                      {k} {l}{done ? ' ✓' : ''}
                    </div>
                  )
                })}
              </div>
            )}
            {(s.stage === '2' || s.stage === '3') && stageRows(s.stage).length > 0 && (
              <div style={{ marginLeft: 18, marginTop: 4 }}>
                {stageRows(s.stage).map(i => (
                  <div key={i.instance_key} onClick={() => openRow(i.instance_key, i.stage, i.status === 'verified' ? (i.stage === '2' ? '2.6' : '3.9') : i.step)}
                    style={{ fontSize: 11, marginBottom: 2, cursor: 'pointer', color: (i.stage === '2' ? s2Instance === i.instance_key : instanceKey === i.instance_key) ? 'var(--text)' : 'var(--text2)' }}>
                    <Lamp status={i.status} />{i.label} · step {i.step}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        {state && !state.state_ready && (
          <div style={{ ...note, fontSize: 11, marginTop: 10, color: '#b45309' }}>
            Your place is not being saved: migration {state.migration} is not applied yet. The flow still works.
          </div>
        )}
        {stateErr && <div style={{ ...note, fontSize: 11, marginTop: 10, color: '#ef4444' }}>{stateErr}</div>}
        {rail?.sign_off.signed && <div style={{ ...note, fontSize: 11, marginTop: 10 }}>Signed off by <b>{rail.sign_off.by}</b>{rail.sign_off.on_behalf ? ' (on behalf)' : ''}.</div>}
      </aside>

      {/* ── MAIN ──────────────────────────────────────────────────────────────────────────── */}
      <main style={{ flex: 1, minWidth: 0, maxWidth: 1100 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 2 }}>{STAGE_TITLES[stage] || 'Onboarding intake'}</h1>
        <p style={{ ...note, marginBottom: 16 }}>
          Drop each report exactly as your system produces it. We read it, propose what each column is and show where every proposal came from;
          you confirm only what a file cannot say. Nothing is imported until a confirm step, and the confirm step reports what was actually saved.
        </p>
        {msg && <div style={{ ...card, padding: '8px 12px', fontSize: 13, marginBottom: 14, borderColor: '#f59e0b' }}>{msg}</div>}

        {/* ═══ STAGE 2 ═══ */}
        {stage === '2' && (
          <Stage2Flow state={state} who={who} reloadState={loadState} instanceKey={s2Instance} setInstanceKey={setS2Instance} step={step} setStep={setStep} flash={flash} />
        )}

        {/* ═══ STAGE 4 — verify everything ═══ */}
        {stage === '4' && rail && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>4.1 — Every source, verified</h2>
            <p style={{ ...note, marginBottom: 12 }}>One row per source. The numbers are what was RE-READ from the tables after each confirm — never a screen. A red row links to the step that fixes it.</p>
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 12 }}>
              <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Source</th><th align="left">Period</th><th align="right">Our total</th><th align="right">File total</th><th align="right">Diff</th><th align="left">Status</th><th align="left">Verified by</th><th align="left">When</th></tr></thead>
              <tbody>
                {rail.verify_table.length === 0 && <tr><td colSpan={8} style={{ ...note, padding: 8 }}>No source has been taken in yet — start at Stage 2 or Stage 3.</td></tr>}
                {rail.verify_table.map(r => (
                  <tr key={r.instance_key} onClick={() => r.red && openRow(r.instance_key, r.stage, r.fix_step)}
                    style={{ borderTop: '1px solid var(--border)', background: r.red ? 'rgba(239,68,68,.06)' : 'transparent', cursor: r.red ? 'pointer' : 'default' }}>
                    <td style={{ padding: '6px 4px', fontWeight: 600 }}>{r.label}{r.red && <div style={{ ...note, fontSize: 11, color: '#ef4444' }}>{r.blocking_reason || r.status} → step {r.fix_step}</div>}{!r.red && r.basis && <div style={{ ...note, fontSize: 11 }}>{r.basis}</div>}
                      {/* Stage C — the cross-check this commit ran (bill payments extracted / units activated but still on hand) */}
                      {r.note && <div style={{ ...note, fontSize: 11, color: '#b45309', fontWeight: 600 }}>{r.note}</div>}</td>
                    <td style={{ padding: '6px 4px' }}>{r.period || '—'}</td>
                    <td style={mono}>{money(r.our_total)}</td><td style={mono}>{money(r.file_total)}</td>
                    <td style={{ ...mono, color: r.match === false ? '#ef4444' : r.match ? '#16a34a' : 'inherit' }}>{r.difference === null ? '—' : money(r.difference)}</td>
                    <td style={{ padding: '6px 4px' }}><Lamp status={r.status} />{(LAMP[r.status] || LAMP.not_started).label}</td>
                    <td style={{ padding: '6px 4px' }}>{r.verified_by || '—'}</td><td style={{ padding: '6px 4px', ...note }}>{r.verified_at ? r.verified_at.slice(0, 16).replace('T', ' ') : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>4.2 — Sign-off</h2>
            {rail.sign_off.signed
              ? <div style={note}>Signed off by <b>{rail.sign_off.by}</b>{rail.sign_off.on_behalf ? ' on behalf of the tenant' : ''} at {rail.sign_off.at?.slice(0, 16).replace('T', ' ')}.{!rail.sign_off.all_verified && <span style={{ color: '#ef4444' }}> A source was added or changed since — its row above is red until re-verified.</span>}</div>
              : <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <input value={signName} onChange={e => setSignName(e.target.value)} placeholder={who || 'your name'} style={{ ...inp, width: 200 }} />
                  <input value={signRole} onChange={e => setSignRole(e.target.value)} placeholder="role (e.g. owner, implementer)" style={{ ...inp, width: 220 }} />
                  <label style={{ fontSize: 13 }}><input type="checkbox" checked={signBehalf} onChange={e => setSignBehalf(e.target.checked)} /> on behalf of the tenant</label>
                  <button style={primary} disabled={busy || !rail.sign_off.all_verified} title={rail.sign_off.all_verified ? '' : 'every row above must be green first'} onClick={signOff}>Sign off</button>
                  {!rail.sign_off.all_verified && <span style={{ ...note, color: '#b45309' }}>every row above must be green first</span>}
                </div>}
          </div>
        )}

        {/* ═══ STAGE 5 — the runbook ═══ */}
        {stage === '5' && rail && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>5.1 — Monthly runbook</h2>
            <p style={{ ...note, marginBottom: 12 }}>{rail.runbook.note}</p>
            <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap', alignItems: 'center' }}>
              {rail.runbook.links.map(l => <a key={l.href} href={l.href} style={{ ...primary, textDecoration: 'none', display: 'inline-block' }}>{l.label}</a>)}
              {(rail.runbook.reports || []).map(l => <a key={l.href} href={l.href} title={l.why} style={{ ...ghost, textDecoration: 'none', display: 'inline-block' }}>{l.label}</a>)}
            </div>
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead><tr style={{ color: 'var(--text2)' }}><th align="left">What to upload each month</th><th align="left">Example file</th><th align="left">Lands in</th><th align="left">Mapping saved</th><th align="left">Status</th></tr></thead>
              <tbody>{rail.runbook.monthly.map(m => (
                <tr key={m.instance_key} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '6px 4px', fontWeight: 600 }}>{m.label}</td><td style={{ padding: '6px 4px', ...note }}>{m.filename_example || '—'}</td>
                  <td style={{ padding: '6px 4px' }}><code>{m.lands_in}</code></td><td style={{ padding: '6px 4px' }}>{m.mapping_saved ? 'yes' : 'not yet'}</td>
                  <td style={{ padding: '6px 4px' }}><Lamp status={m.status} />{(LAMP[m.status] || LAMP.not_started).label}</td>
                </tr>))}</tbody>
            </table>
            {!rail.sign_off.signed && <div style={{ ...note, marginTop: 10, color: '#b45309' }}>Not signed off yet — Stage 4.</div>}
          </div>
        )}

        {/* ═══ STAGE 3 ═══ */}
        {stage === '3' && a && step !== '3.1' && (
          <div style={{ ...note, marginBottom: 12 }}>
            <b>{a.carrier.name}</b> · {a.statement_type} · {filename}{canUseKept ? <span style={{ color: '#15803d' }}> · file kept — no need to drop it again</span> : !file && <span style={{ color: '#b45309' }}> · file not attached (re-drop it in 3.1 to re-check or commit)</span>}
          </div>
        )}

        {/* 3.1 UPLOAD */}
        {stage === '3' && step === '3.1' && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>3.1 — Upload the statement</h2>
            <p style={{ ...note, marginBottom: 14 }}>Which carrier sent this, and what kind of statement is it? A carrier that sends a separate residual or spiff file gets a second statement type — not a different setup.</p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
              <label style={{ fontSize: 13 }}>Carrier
                <select value={carrierId} onChange={e => setCarrierId(e.target.value)} style={{ ...inp, width: '100%', marginTop: 4 }}>
                  <option value="">— pick a carrier —</option>
                  {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </label>
              <label style={{ fontSize: 13 }}>Statement type
                <input list="statement-kinds" value={statementType} onChange={e => setStatementType(e.target.value)} style={{ ...inp, width: '100%', marginTop: 4 }} placeholder="commission statement" />
                <datalist id="statement-kinds">
                  {statementKinds.map(k => <option key={k.key} value={`${k.statement_type || STATEMENT_TYPE_DEFAULT} statement`}>{k.label}</option>)}
                </datalist>
                {statementKinds.length > 0 && <div style={{ ...note, fontSize: 11, marginTop: 2 }}>{statementKinds.map(k => `${k.label} (${k.provenance_text})`).join(' · ')}{registry.withheld ? ` · ${registry.withheld}` : ''}</div>}
              </label>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14 }}>
              <span style={note}>Carrier not in the list?</span>
              <input value={newCarrier} onChange={e => setNewCarrier(e.target.value)} placeholder="Carrier name" style={inp} />
              <button style={btn} disabled={busy || !newCarrier.trim()} onClick={addCarrier}>Add carrier</button>
            </div>
            <Dropzone file={file} filename={filename} onFiles={onFiles} dragOver={dragOver} setDragOver={setDragOver} hint="Drop the statement here" />
            <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
              <button style={primary} disabled={busy || (!file && !canUseKept) || !carrierId} onClick={() => analyze().then(r => r && setStep('3.2'))}>{busy ? 'Reading…' : 'Read the file →'}</button>
              {a && !file && !canUseKept && <button style={ghost} onClick={() => setStep('3.2')}>Continue with what was saved →</button>}
              {saveUi}
            </div>
          </div>
        )}

        {/* 3.2 DETECT */}
        {stage === '3' && step === '3.2' && a && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>3.2 — Sheet, header row, footer</h2>
            <p style={{ ...note, marginBottom: 12 }}>What we found in the file. Override anything that is wrong and re-read.</p>
            <DetectPanel d={a.detect} sheet={sheet} setSheet={setSheet} headerRow={headerRow} setHeaderRow={setHeaderRow} footerMode={footerMode} setFooterMode={setFooterMode} />
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={ghost} onClick={() => setStep('3.1')}>← Back</button>
              <button style={btn} disabled={busy || (!file && !canUseKept)} onClick={() => analyze({ keepStep: true })}>Re-read with these settings</button>
              <button style={primary} onClick={() => go('3.3')}>Columns →</button>
              {saveUi}
            </div>
          </div>
        )}

        {/* 3.3 COLUMNS */}
        {stage === '3' && step === '3.3' && a && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>3.3 — Confirm the columns</h2>
            <p style={{ ...note, marginBottom: 12 }}>Each platform field, the column we propose, where that proposal came from, and three values from that column. The <b>Amount</b> and <b>Product / description</b> (the label) are required.</p>
            <ColumnsTable columns={a.columns} headers={headers} columnMap={columnMap} setColumnMap={setColumnMap} />
            {a.money_columns.length > 1 && (
              <div style={{ ...card, background: 'var(--bg,transparent)', marginTop: 12, fontSize: 13 }}>
                <b>Money columns in this file</b> — the one mapped as Amount is used; the others are recorded with their totals, never silently ignored.
                <ul style={{ margin: '6px 0 0 18px' }}>{a.money_columns.map(m => <li key={m.header}>{m.header}: <span style={mono}>{money(m.sum)}</span> over {num(m.cells)} cells {m.is_amount ? '— the amount' : '— recorded as ignored'}</li>)}</ul>
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
              <button style={ghost} onClick={() => setStep('3.2')}>← Back</button>
              <button style={btn} disabled={busy || (!file && !canUseKept)} onClick={() => analyze({ keepStep: true })}>Re-check with these columns</button>
              <button style={primary} disabled={!canLeave33} title={canLeave33 ? '' : 'Map the Amount and the Product / description first'}
                onClick={() => { persist('3.4', { column_map: columnMap }); analyze({ keepStep: true }).finally(() => setStep('3.4')) }}>Which sign is money earned →</button>
              {saveUi}
            </div>
          </div>
        )}

        {/* 3.4 SIGN — verbatim, no default */}
        {stage === '3' && step === '3.4' && a && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>3.4 — {a.sign.question || 'In this file, is money you EARNED positive or negative?'}</h2>
            <p style={{ ...note, marginBottom: 12 }}>Every statement writes the same money with its own sign. Look at the real rows below and tell us which way this file is written. This is stored on the amount column&apos;s mapping for this carrier and statement type, and shown again every month.</p>
            <div style={{ display: 'flex', gap: 10, marginBottom: 14 }}>
              {(a.sign.options?.length ? a.sign.options : SIGN_OPTIONS).map(o => (
                <button key={o.value} style={{ ...btn, padding: '10px 18px', borderWidth: 2,
                  borderColor: signAnswer === o.value ? 'var(--accent,#2563eb)' : 'var(--border)',
                  background: signAnswer === o.value ? 'var(--accent,#2563eb)' : 'var(--surface)', color: signAnswer === o.value ? '#fff' : 'var(--text)' }}
                  onClick={() => { const v = o.value as SignAnswer; setSignAnswer(v); persist('3.4', { sign_answer: v }); analyze({ sign: v, keepStep: true }) }}>{o.label}</button>
              ))}
              {a.sign.stored_answer && <span style={{ ...note, alignSelf: 'center' }}>Stored earlier for this carrier: <b>earned is {a.sign.stored_answer}</b></span>}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              {(['positive', 'negative'] as const).map(side => (
                <div key={side} style={{ ...card, background: 'var(--bg,transparent)' }}>
                  <div style={{ fontWeight: 700, marginBottom: 6 }}>The three largest {side} rows <span style={note}>({num(side === 'positive' ? a.sign.positive_count : a.sign.negative_count)} {side} in the file)</span></div>
                  {(a.sign[side] || []).length === 0 && <div style={note}>none</div>}
                  {(a.sign[side] || []).map((r, i) => (
                    <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '4px 0', borderTop: i ? '1px solid var(--border)' : 'none' }}>
                      <span>{r.label}{r.sub_label ? ` › ${r.sub_label}` : ''}{r.store ? ` · ${r.store}` : ''}</span>
                      <span style={{ ...mono, fontWeight: 600, color: r.amount < 0 ? '#ef4444' : 'inherit' }}>{money(r.amount)}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
              <button style={ghost} onClick={() => setStep('3.3')}>← Back</button>
              <button style={primary} disabled={!signAnswer || busy} title={signAnswer ? '' : 'Answer the question first'} onClick={() => go('3.5', { sign_answer: signAnswer })}>Labels →</button>
              {saveUi}
            </div>
          </div>
        )}

        {/* 3.5 / 3.6 LABELS + BUCKETS */}
        {stage === '3' && (step === '3.5' || step === '3.6') && a && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>{step === '3.5' ? '3.5 — Labels found in the file' : '3.6 — Put every label in a bucket'}</h2>
            <p style={{ ...note, marginBottom: 12 }}>
              {step === '3.5'
                ? `${a.labels.length} distinct labels, largest first. Σ canonical is the amount after your sign answer (earned is ${signAnswer}). A label pre-placed in a bucket shows where that came from.`
                : `Every label must be in one of your buckets — there is no "other". A reversal (chargeback / deactivation) can go into the bucket it reverses (it books negative there, so the bucket reads net) or into a deduction bucket of its own — your choice, nothing is decided for you. Deduction buckets book signed: money taken off the statement reads negative. Add, rename or reorder buckets on the Category → Bucket Map page.`}
            </p>
            {step === '3.6' && bucketMeta && bucketMeta.ready === false && (
              <div style={{ ...card, padding: '8px 12px', fontSize: 13, marginBottom: 10, borderColor: '#f59e0b', background: 'rgba(245,158,11,.08)' }}>
                The bucket registry (migration {bucketMeta.migration}) is not applied yet: the buckets shown are the built-in defaults. A label can be placed in a deduction bucket here, but the confirm step will refuse until the migration runs.
              </div>
            )}
            {step === '3.6' && a.blank_label && (
              <div style={{ ...card, padding: '8px 12px', fontSize: 13, marginBottom: 10, borderColor: '#ef4444', background: 'rgba(239,68,68,.06)', color: '#b91c1c' }}>
                {a.blank_label.message} <button style={{ ...ghost, padding: '2px 8px', fontSize: 12 }} onClick={() => setStep('3.3')}>Fix the columns</button>
              </div>
            )}
            {step === '3.5' && (
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Label</th><th align="right">Rows</th><th align="right">Σ raw</th><th align="right">Σ canonical</th><th align="left">Sign mix</th><th align="left">Pre-placed</th></tr></thead>
                <tbody>{a.labels.map(l => (
                  <tr key={l.label} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px 4px' }}>{l.label}{l.reversal_flag && <span style={{ ...note, fontSize: 11 }}> · reversal?</span>}
                      {!!l.sub_labels.length && <div style={{ ...note, fontSize: 11 }}>{l.sub_labels.slice(0, 6).map(s => `${s.sub_label} (${money(s.sum_raw)})`).join(' · ')}{l.sub_labels.length > 6 ? ' …' : ''}</div>}</td>
                    <td style={mono}>{num(l.count)}</td><td style={mono}>{money(l.sum_raw)}</td><td style={mono}>{money(l.sum_canonical)}</td>
                    <td>{l.sign_mix.replace('_', ' ')} ({l.positives}+ / {l.negatives}− / {l.zeros} zero)</td>
                    <td>{assign[l.label] && assign[l.label] !== UNASSIGNED ? <>{bucketLabels[assign[l.label]] || assign[l.label]} <Badge prov={l.provenance} label={l.provenance_label} /></> : <span style={note}>unassigned</span>}</td>
                  </tr>))}</tbody>
              </table>
            )}
            {step === '3.6' && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10 }}>
                {[UNASSIGNED, ...(bucketRows.length ? [...earnedKeys, ...dedKeys] : buckets)].map(b => {
                  const cards = a.labels.filter(l => (assign[l.label] || UNASSIGNED) === b)
                  const sum = cards.reduce((s, l) => s + (l.sum_canonical ?? l.sum_raw), 0)
                  const ded = b !== UNASSIGNED && bucketKind(b) === 'deduction'
                  return (
                    <div key={b} style={{ ...card, padding: 10, borderColor: b === UNASSIGNED && cards.length ? '#ef4444' : ded ? 'rgba(239,68,68,.35)' : 'var(--border)', minHeight: 120 }}>
                      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>{b === UNASSIGNED ? 'Unassigned' : bucketLabels[b] || b}{ded && <span style={{ ...note, fontSize: 10, marginLeft: 6, color: '#b91c1c' }}>deduction</span>}</div>
                      <div style={{ ...note, fontSize: 11, marginBottom: 8 }}>{cards.length} label(s) · {money(sum)}</div>
                      {cards.map(l => (
                        <div key={l.label} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 8, marginBottom: 6, background: 'var(--bg,transparent)' }}>
                          <div style={{ fontWeight: 600, fontSize: 12 }}>{l.label}</div>
                          <div style={{ ...note, fontSize: 11 }}>{num(l.count)} rows · {money(l.sum_canonical ?? l.sum_raw)} · {l.sign_mix.replace('_', ' ')}</div>
                          {assign[l.label] && assign[l.label] !== UNASSIGNED && l.provenance && l.bucket === assign[l.label] && <div style={{ marginTop: 4 }}><Badge prov={l.provenance} label={l.provenance_label} /></div>}
                          <select value={assign[l.label] || UNASSIGNED} onChange={e => setAssign(m => ({ ...m, [l.label]: e.target.value }))} style={{ ...inp, width: '100%', marginTop: 6, fontSize: 12, padding: '4px 6px' }}>
                            <option value={UNASSIGNED}>— unassigned —</option>
                            {bucketRows.length ? <>
                              <optgroup label="Earned">{earnedKeys.map(x => <option key={x} value={x}>{bucketLabels[x] || x}</option>)}</optgroup>
                              {dedKeys.length > 0 && <optgroup label="Deductions (book signed)">{dedKeys.map(x => <option key={x} value={x}>{bucketLabels[x] || x}</option>)}</optgroup>}
                            </> : buckets.map(x => <option key={x} value={x}>{bucketLabels[x] || x}</option>)}
                          </select>
                          <label style={{ ...note, fontSize: 11, display: 'block', marginTop: 4 }}>
                            <input type="checkbox" checked={!!reversal[l.label]} onChange={e => setReversal(m => ({ ...m, [l.label]: e.target.checked }))} /> reversal (chargeback / deactivation)
                          </label>
                        </div>
                      ))}
                    </div>
                  )
                })}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, marginTop: 14, alignItems: 'center' }}>
              <button style={ghost} onClick={() => setStep(step === '3.5' ? '3.4' : '3.5')}>← Back</button>
              {step === '3.5' && <button style={primary} onClick={() => go('3.6')}>Bucket the labels →</button>}
              {step === '3.6' && <>
                <button style={primary} disabled={!!unassigned.length || busy} title={unassigned.length ? `${unassigned.length} label(s) still unassigned` : ''}
                  onClick={() => { persist('3.7', { assignments, reversal_flags: reversal }); analyze({ keepStep: true }).finally(() => setStep('3.7')) }}>Store / account attribution →</button>
                {!!unassigned.length && <span style={{ ...note, color: '#ef4444' }}>{unassigned.length} label(s) unassigned: {unassigned.slice(0, 5).join(', ')}{unassigned.length > 5 ? ' …' : ''}</span>}
              </>}
              {saveUi}
            </div>
          </div>
        )}

        {/* 3.7 IDENTITY — the SHARED resolver (the same panel as 2.4) */}
        {stage === '3' && step === '3.7' && a && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>3.7 — Store / account attribution</h2>
            <p style={{ ...note, marginBottom: 12 }}>The store strings this statement carries, resolved against your stores by the same rules the sales stage and every report use. A string we do not know must be assigned, created, marked company-level, or marked not yours with a reason before the statement can be confirmed. Account ids and reps are listed below for the record.</p>
            <StoreResolver stores={a.stores || []} reps={[]} storeList={state?.stores || []} employees={state?.employees || []} decisions={decisions} setDecisions={setDecisions} allowCompanyLevel />
            {Object.entries(a.identity).filter(([f]) => f !== 'store').map(([field, rows]) => (
              <div key={field} style={{ marginTop: 10 }}>
                <div style={{ fontWeight: 700, fontSize: 13 }}>{field.replace('_', ' ')} · {rows.length} distinct</div>
                <div style={{ ...note, fontSize: 12 }}>{rows.slice(0, 12).map(r => `${r.value} (${num(r.count)} · ${money(r.sum_raw)})`).join(' · ')}{rows.length > 12 ? ' …' : ''}</div>
              </div>
            ))}
            <div style={{ display: 'flex', gap: 8, marginTop: 14, alignItems: 'center' }}>
              <button style={ghost} onClick={() => setStep('3.6')}>← Back</button>
              <button style={btn} disabled={busy || (!file && !canUseKept)} onClick={() => analyze({ keepStep: true, dec: decisions })}>Re-check with these decisions</button>
              <button style={primary} disabled={unresolvedStores.length > 0} title={unresolvedStores.length ? `${unresolvedStores.length} store string(s) unresolved` : ''} onClick={() => { persist('3.8', { identity: decisions }); analyze({ keepStep: true, dec: decisions }).finally(() => setStep('3.8')) }}>Totals →</button>
              {unresolvedStores.length > 0 && <span style={{ ...note, color: '#ef4444' }}>{unresolvedStores.length} unresolved: {unresolvedStores.slice(0, 4).join(', ')}{unresolvedStores.length > 4 ? ' …' : ''} — re-check after deciding</span>}
              {saveUi}
            </div>
          </div>
        )}

        {/* 3.8 TOTALS */}
        {stage === '3' && step === '3.8' && a && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>3.8 — Our totals beside the file&apos;s</h2>
            <p style={{ ...note, marginBottom: 12 }}>{a.verify.basis}. Match means zero difference to the cent.</p>
            {a.verify.banners.map((b, i) => <div key={i} style={{ ...card, padding: '8px 12px', fontSize: 13, marginBottom: 8, borderColor: '#f59e0b', background: 'rgba(245,158,11,.08)' }}>{b}</div>)}
            {!a.verify.totals && <div style={{ ...note, marginBottom: 12, color: '#b45309' }}>Answer 3.4 (and re-attach the file) to compute the totals.</div>}
            {a.verify.totals && tie && (
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 12 }}>
                <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Bucket</th><th align="right">Earned (+)</th><th align="right">Reversed (−)</th><th align="right">Net</th><th align="right">Rows</th></tr></thead>
                <tbody>
                  {(a.verify.totals.bucket_order || buckets).filter(b => bucketKind(b) !== 'deduction').map(b => { const x = a.verify.totals!.buckets[b]; return (
                    <tr key={b} style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>{x?.label || bucketLabels[b] || b}</td><td style={mono}>{money(x?.gross)}</td><td style={{ ...mono, color: (x?.chargebacks || 0) < 0 ? '#ef4444' : 'inherit' }}>{money(x?.chargebacks)}</td><td style={{ ...mono, fontWeight: 600 }}>{money(x?.net)}</td><td style={mono}>{num(x?.count)}</td></tr>) })}
                  <tr style={{ borderTop: '1px solid var(--border)', fontWeight: 600, color: 'var(--text2)' }}><td style={{ padding: '5px 4px' }}>Earned buckets</td><td /><td /><td style={mono}>{money(a.verify.totals.earned_total ?? a.verify.totals.net_total)}</td><td /></tr>
                  {(a.verify.totals.bucket_order || buckets).filter(b => bucketKind(b) === 'deduction').map(b => { const x = a.verify.totals!.buckets[b]; return (
                    <tr key={b} style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>{x?.label || bucketLabels[b] || b} <span style={{ ...note, fontSize: 10, color: '#b91c1c' }}>deduction</span></td><td style={mono}>{money(x?.gross)}</td><td style={{ ...mono, color: (x?.chargebacks || 0) < 0 ? '#ef4444' : 'inherit' }}>{money(x?.chargebacks)}</td><td style={{ ...mono, fontWeight: 600 }}>{money(x?.net)}</td><td style={mono}>{num(x?.count)}</td></tr>) })}
                  {dedKeys.length > 0 && <tr style={{ borderTop: '1px solid var(--border)', fontWeight: 600, color: 'var(--text2)' }}><td style={{ padding: '5px 4px' }}>Deduction buckets</td><td /><td /><td style={{ ...mono, color: (a.verify.totals.deductions_total || 0) < 0 ? '#ef4444' : 'inherit' }}>{money(a.verify.totals.deductions_total ?? 0)}</td><td /></tr>}
                  {a.verify.totals.other.count > 0 && <tr style={{ borderTop: '1px solid var(--border)', color: '#ef4444' }}><td style={{ padding: '5px 4px' }}>Unassigned (booked to no bucket)</td><td style={mono}>{money(a.verify.totals.other.gross)}</td><td style={mono}>{money(a.verify.totals.other.chargebacks)}</td><td style={mono}>{money(a.verify.totals.other.net)}</td><td style={mono}>{num(a.verify.totals.other.count)}</td></tr>}
                  {!!a.verify.totals.unlisted?.count && <tr style={{ borderTop: '1px solid var(--border)', color: '#b45309' }}><td style={{ padding: '5px 4px' }}>Filed under a bucket no longer listed ({Object.keys(a.verify.totals.unlisted.keys || {}).join(', ')})</td><td style={mono}>{money(a.verify.totals.unlisted.gross)}</td><td style={mono}>{money(a.verify.totals.unlisted.chargebacks)}</td><td style={mono}>{money(a.verify.totals.unlisted.net)}</td><td style={mono}>{num(a.verify.totals.unlisted.count)}</td></tr>}
                  <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700 }}><td style={{ padding: '6px 4px' }}>Σ all buckets (net: earned + deductions)</td><td /><td /><td style={mono}>{money(tie.our_total)}</td><td /></tr>
                  <tr><td style={{ padding: '4px 4px' }}>File&apos;s own total ({tie.file_total_source === 'footer' ? `footer row${a.detect.footer.rows.length ? ` ${a.detect.footer.rows.map(r => r.row).join(', ')}` : ''}` : tie.file_total_source === 'typed' ? 'typed by you' : 'none'})</td><td /><td /><td style={mono}>{money(tie.file_total)}</td><td /></tr>
                  <tr style={{ fontWeight: 700, color: tie.match ? '#16a34a' : '#ef4444' }}><td style={{ padding: '4px 4px' }}>Difference</td><td /><td /><td style={mono}>{tie.difference === null ? 'nothing to compare' : money(tie.difference)}</td><td /></tr>
                </tbody>
              </table>
            )}
            <div style={{ ...note, marginBottom: 6 }}>
              Rows in file: {num(a.verify.rows_in_file)} data rows read · {num(a.verify.rows_usable)} usable · {a.verify.footer_rows} footer · rows to land: <b>{num(a.verify.rows_to_land)}</b>
              {a.verify.totals && a.verify.totals.charges.count > 0 && <> · {a.verify.totals.charges.count} zero / charge line(s) book nothing ({money(a.verify.totals.charges.total)})</>}
            </div>
            {!!a.verify.ignored_money_columns.length && <div style={{ ...note, marginBottom: 6 }}>Ignored money columns (recorded): {a.verify.ignored_money_columns.map(m => `"${m.header}" Σ ${money(m.sum)}`).join(' · ')}</div>}
            {!a.detect.footer.detected && (
              <label style={{ fontSize: 13, display: 'block', margin: '10px 0' }}>The file states no total — type the statement&apos;s total (recorded as &quot;typed&quot;):
                <input value={typedTotal} onChange={e => setTypedTotal(e.target.value)} placeholder="e.g. 86970.34" style={{ ...inp, marginLeft: 8, width: 160 }} />
                <button style={{ ...btn, marginLeft: 8 }} disabled={busy || (!file && !canUseKept)} onClick={() => analyze({ keepStep: true, typed: typedTotal })}>Recompute</button>
              </label>
            )}
            {needsAttest && (
              <label style={{ fontSize: 13, display: 'block', margin: '10px 0' }}>{tie?.match === false ? 'The totals differ.' : 'There is nothing to compare against.'} To proceed anyway, say why (recorded with your name):
                <textarea value={attest} onChange={e => setAttest(e.target.value)} rows={2} style={{ ...inp, width: '100%', marginTop: 4 }} placeholder="Reason for accepting this difference" />
              </label>
            )}
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <button style={ghost} onClick={() => setStep('3.7')}>← Back</button>
              <button style={btn} disabled={busy || (!file && !canUseKept)} onClick={() => analyze({ keepStep: true })}>Recompute</button>
              <button style={ghost} onClick={() => setStep('3.3')}>Fix the columns</button>
              <button style={ghost} onClick={() => setStep('3.6')}>Fix the buckets</button>
              <button style={primary} disabled={!a.verify.totals || (needsAttest && !attest.trim())} onClick={() => go('3.9', { period, typed_total: typedTotal, attestation: attest })}>Confirm →</button>
              {saveUi}
            </div>
          </div>
        )}

        {/* 3.9 CONFIRM + RESULT */}
        {stage === '3' && step === '3.9' && a && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>3.9 — Confirm this statement</h2>
            {!commitRes && <>
              <p style={{ ...note, marginBottom: 12 }}>Confirming saves the column map (with your sign answer) for this carrier and statement type, saves the bucket rules and your store decisions, imports the rows, and then re-reads what landed. The numbers below come back from the table, not from this screen.</p>
              <label style={{ fontSize: 13, display: 'block', marginBottom: 12 }}>Period this statement is for
                <input value={period} onChange={e => setPeriod(e.target.value)} placeholder="e.g. August 2026" style={{ ...inp, marginLeft: 8, width: 200 }} />
                {a.period.spans_two_months && <span style={{ ...note, marginLeft: 8, color: '#b45309' }}>the dates span {a.period.months.map(m => `${m.period} (${m.rows})`).join(', ')} — pick the month this statement is for</span>}
                {!a.period.spans_two_months && a.period.proposed && <span style={{ ...note, marginLeft: 8 }}>from the statement&apos;s own dates</span>}
              </label>
              <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>Confirm {a.carrier.name} — {period || '(period)'} — net {money(tie?.our_total)}</div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button style={ghost} onClick={() => setStep('3.8')}>← Back</button>
                <button style={primary} disabled={busy || (!file && !canUseKept) || !period.trim()} onClick={commit}>{busy ? 'Saving…' : 'Confirm and import'}</button>
                {!file && !canUseKept && <span style={{ ...note, color: '#b45309', alignSelf: 'center' }}>re-attach the file in 3.1 to commit</span>}
              </div>
            </>}
            {commitRes && (
              <div>
                <div style={{ ...card, borderColor: commitRes.ok ? '#16a34a' : '#ef4444', background: commitRes.ok ? 'rgba(22,163,74,.06)' : 'rgba(239,68,68,.06)', marginBottom: 12 }}>
                  <div style={{ fontWeight: 700, fontSize: 15 }}>{commitRes.ok ? 'Saved and verified' : 'Saved, but NOT verified'} — {commitRes.carrier.name} · {commitRes.period}</div>
                  {!commitRes.ok && <ul style={{ margin: '6px 0 0 18px', color: '#ef4444', fontSize: 13 }}>{commitRes.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>}
                  <div style={{ ...note, marginTop: 6 }}>
                    Column map saved for {commitRes.mapping_saved.length} fields (sign: {commitRes.sign_convention}) · {commitRes.rules_saved} bucket rules · store aliases {commitRes.identity_written?.aliases.length ?? 0} · rows inserted {num(commitRes.verified_numbers.rows_inserted)} · <b>rows re-read from the table: {num(commitRes.verified_numbers.rows_landed)}</b> of {num(commitRes.verified_numbers.rows_built)} built · footer rows dropped {commitRes.verified_numbers.footer_rows_dropped}
                    {!commitRes.state.saved && <> · your place was not saved ({commitRes.state.reason})</>}
                  </div>
                </div>
                <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 12 }}>
                  <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Bucket (re-read)</th><th align="right">Earned (+)</th><th align="right">Reversed (−)</th><th align="right">Net</th></tr></thead>
                  <tbody>
                    {(commitRes.verified_numbers.totals.bucket_order || buckets).map(b => { const x = commitRes.verified_numbers.totals.buckets[b]; return <tr key={b} style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>{x?.label || bucketLabels[b] || b}{x?.kind === 'deduction' && <span style={{ ...note, fontSize: 10, color: '#b91c1c' }}> deduction</span>}</td><td style={mono}>{money(x?.gross)}</td><td style={mono}>{money(x?.chargebacks)}</td><td style={{ ...mono, fontWeight: 600 }}>{money(x?.net)}</td></tr> })}
                    {commitRes.verified_numbers.totals.deductions_total !== undefined && <tr style={{ borderTop: '1px solid var(--border)', color: 'var(--text2)', fontWeight: 600 }}><td style={{ padding: '5px 4px' }}>Earned {money(commitRes.verified_numbers.totals.earned_total)} · deductions {money(commitRes.verified_numbers.totals.deductions_total)}</td><td /><td /><td /></tr>}
                    <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700 }}><td style={{ padding: '5px 4px' }}>Σ all buckets (net)</td><td /><td /><td style={mono}>{money(commitRes.verified_numbers.tie.our_total)}</td></tr>
                    <tr><td style={{ padding: '5px 4px' }}>File&apos;s own total</td><td /><td /><td style={mono}>{money(commitRes.verified_numbers.tie.file_total)}</td></tr>
                    <tr style={{ fontWeight: 700, color: commitRes.verified_numbers.tie.match ? '#16a34a' : '#ef4444' }}><td style={{ padding: '5px 4px' }}>Difference</td><td /><td /><td style={mono}>{money(commitRes.verified_numbers.tie.difference)}</td></tr>
                  </tbody>
                </table>
                {commitRes.verified_numbers.attestation && <div style={{ ...note, marginBottom: 8 }}>Attested: &quot;{commitRes.verified_numbers.attestation.reason}&quot; — {commitRes.verified_numbers.confirmed_by}</div>}
                <div style={{ display: 'flex', gap: 8 }}>
                  <button style={primary} onClick={resetForAnother}>Another carrier?</button>
                  <a href="/commcalc/commission-ledger" style={{ ...ghost, textDecoration: 'none', display: 'inline-block' }}>Open the Commission Ledger</a>
                  <button style={ghost} onClick={() => openStage('4')}>Verify everything →</button>
                  {!commitRes.ok && <button style={ghost} onClick={() => { setCommitRes(null); setStep('3.8') }}>Back to the totals</button>}
                </div>
              </div>
            )}
          </div>
        )}

        {stage === '3' && step !== '3.1' && !a && (
          <div style={card}>
            <div style={note}>Nothing to show for this step yet — start at 3.1 by dropping the statement.</div>
            <button style={{ ...primary, marginTop: 10 }} onClick={() => setStep('3.1')}>Go to 3.1</button>
          </div>
        )}
      </main>
    </div>
  )
}
