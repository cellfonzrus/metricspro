'use client'
// ═══════════════════════════════════════════════════════════════════════════════════════════════
// TENANT ONBOARDING — Stage 3: the commission-statement INTAKE (design 2026-09-20, steps 3.1–3.9).
//
// Owner: "I will not do anything manually — the system should ask me while onboarding under 3.4 what
// is considered commission positive or negative. The user does not know how this system works, they
// only can upload their existing data … assign the existing fields to the uploaded data and SAVE
// that, use the intelligence to assign the categories to the fields and ask the user to confirm.
// Whatever is being uploaded should be able to save is most important."
//
// ONE SHAPE (design §0.1): upload → the platform pre-fills everything it can, with provenance →
// the person confirms only what a file cannot say (which sign is money earned; which bucket each
// label belongs to) → our numbers beside the file's → confirm. The left rail is a PROJECTION of the
// persisted state (GET /commcalc/onboarding/intake/state); every choice is written back through
// PUT /state as it is made, so leaving and returning lands on the same step (design §0.2, §5.10).
//
// This page owns NO parsing, NO classification and NO persistence of its own: the backend's
// /onboarding/intake/analyze reads the file and proposes; /onboarding/intake/commit saves the map
// through the one mapping writer, the rules through the Category Map's writer, lands the rows
// through the ledger importer and RE-READS them — and only then reports the numbers shown here.
// No carrier, tenant or product is named in this file (RULE TWO).
// ═══════════════════════════════════════════════════════════════════════════════════════════════
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, apiUpload } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

// ── payload types (mirror onboarding_intake.py) ────────────────────────────────────────────────
type Carrier = { id: string; name: string; code?: string | null; is_default?: boolean }
type Stage = { stage: string; label: string; status: string; built: boolean }
type Instance = {
  instance_key: string; step: string; status: string; payload: Record<string, unknown>
  verified_numbers?: Record<string, unknown> | null; verified_by?: string | null; verified_at?: string | null
  blocking_reason?: string | null; current?: boolean
}
type Rail = { stages: Stage[]; steps: { key: string; label: string }[]; instances: Instance[]; resume: { instance_key: string; step: string } | null }
type StateResp = {
  state_ready: boolean; migration: string; note?: string; rail: Rail; carriers: Carrier[]
  source_kinds: { value: string; label: string; built: boolean }[]; statement_type_default: string
  sign_question: string; buckets: string[]; bucket_labels: Record<string, string>; save?: { saved: boolean; reason?: string }
}
type Column = {
  target_field: string; label: string; required: boolean; transform: string; column: string
  provenance: string | null; provenance_label: string | null; confidence: string; samples: string[]
}
type MoneyCol = { header: string; sum: number; cells: number; is_amount: boolean }
type SignRow = { label: string; sub_label: string; store: string; amount: number }
type Label = {
  label: string; match_field: string | null; count: number; sum_raw: number; sum_canonical: number | null
  positives: number; negatives: number; zeros: number; sign_mix: string
  sub_labels: { sub_label: string; count: number; sum_raw: number }[]
  reversal_flag: boolean; bucket: string; provenance: string | null; provenance_label: string | null
}
type Bucket = { gross: number; chargebacks: number; net: number; count: number }
type Totals = { buckets: Record<string, Bucket>; bucket_labels: Record<string, string>; net_total: number; other: Bucket; charges: { total: number; count: number }; rows: number }
type Tie = { our_total: number; file_total: number | null; file_total_source: string | null; difference: number | null; match: boolean | null; other_unassigned: number; other_count: number }
type Analysis = {
  source_kind: string; statement_type: string; carrier: { id: string; name: string; code: string }
  source_report: string; instance_key: string; filename?: string | null
  detect: {
    sheet: string | null; header_row: number | null
    sheets: { name: string; rows: number; header_row: number | null; data_rows: number; used: boolean; role: string }[]
    headers: string[]; data_rows: number; usable_rows: number
    footer: { detected: boolean; rows: { row: number; raw_amount: number }[]; file_total_raw: number | null; lines_sum_raw: number; equals_lines_sum: boolean | null; basis: string }
    footer_rows_dropped: number; identity_fields: string[]
  }
  columns: Column[]; money_columns: MoneyCol[]
  sign: { question: string; positive: SignRow[]; negative: SignRow[]; positive_count: number; negative_count: number
          options: { value: string; label: string }[]; answer: string | null; stored_answer: string | null; convention: string | null; answered: boolean }
  labels: Label[]; buckets: string[]; bucket_labels: Record<string, string>; unassigned: string[]
  identity: Record<string, { value: string; count: number; sum_raw: number }[]>
  period: { months: { period: string; rows: number }[]; span_from: string | null; span_to: string | null; proposed: string | null; spans_two_months: boolean; dated_rows: number }
  verify: { basis: string; totals: Totals | null; tie: Tie | null; banners: string[]; rows_in_file: number; rows_usable: number; footer_rows: number; rows_to_land: number; ignored_money_columns: MoneyCol[] }
  state?: StateResp
}
type CommitResp = {
  ok: boolean; problems: string[]; saved: number; source_report: string; period: string
  carrier: { id: string; name: string; code: string }; mapping_saved: string[]; sign_convention: string
  rules_saved: number; verified_numbers: { rows_in_file: number; rows_usable: number; footer_rows_dropped: number; rows_built: number; rows_landed: number; rows_inserted: number; totals: Totals; tie: Tie; attestation: { reason: string } | null; confirmed_by: string | null; confirmed_at: string }
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
const UNASSIGNED = 'unassigned'
// The two 3.4 answers, verbatim from the design. The backend sends the same pair; this is the fallback.
const SIGN_OPTIONS = [{ value: 'positive', label: 'Earned is positive' }, { value: 'negative', label: 'Earned is negative' }]
const BASE = '/api/v1/commcalc/onboarding/intake'

const money = (n: number | null | undefined) => (n === null || n === undefined ? '—' : (n || 0).toLocaleString('en-US', { style: 'currency', currency: 'USD' }))
const num = (n: number | null | undefined) => (n === null || n === undefined ? '—' : (n || 0).toLocaleString('en-US'))
const inp: React.CSSProperties = { padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)' }
const btn: React.CSSProperties = { ...inp, cursor: 'pointer', fontWeight: 600 }
const primary: React.CSSProperties = { ...btn, background: 'var(--accent,#2563eb)', color: '#fff', border: 'none', padding: '9px 18px' }
const ghost: React.CSSProperties = { ...btn, background: 'transparent' }
const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 12, padding: 16, background: 'var(--surface)' }
const note: React.CSSProperties = { color: 'var(--text2)', fontSize: 13 }
const mono: React.CSSProperties = { fontVariantNumeric: 'tabular-nums', textAlign: 'right' }
const LAMP: Record<string, { bg: string; label: string }> = {
  not_started: { bg: 'var(--text3,#9ca3af)', label: 'not started' },
  in_progress: { bg: '#f59e0b', label: 'in progress' },
  needs_input: { bg: '#ef4444', label: 'needs input' },
  verified: { bg: '#16a34a', label: 'verified' },
}
const PROV_STYLE: Record<string, React.CSSProperties> = {
  'from your file': { background: 'rgba(37,99,235,.12)', color: '#1d4ed8' },
  'your earlier choice': { background: 'rgba(22,163,74,.12)', color: '#15803d' },
  'house default': { background: 'rgba(147,51,234,.12)', color: '#7e22ce' },
  guess: { background: 'rgba(245,158,11,.15)', color: '#b45309' },
}

function Lamp({ status }: { status: string }) {
  const l = LAMP[status] || LAMP.not_started
  return <span title={l.label} style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 5, background: l.bg, marginRight: 8, verticalAlign: 'middle' }} />
}
function Badge({ prov, label }: { prov: string | null; label?: string | null }) {
  if (!prov) return <span style={{ ...note, fontSize: 11 }}>—</span>
  const st = PROV_STYLE[prov] || PROV_STYLE.guess
  return <span style={{ ...st, fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999, whiteSpace: 'nowrap' }}>{label || prov}</span>
}

export default function OnboardingIntakePage() {
  const { user } = useAuth()
  const who = user?.full_name || user?.email || ''
  const [state, setState] = useState<StateResp | null>(null)
  const [stateErr, setStateErr] = useState('')
  const [step, setStep] = useState('3.1')
  const [carrierId, setCarrierId] = useState('')
  const [newCarrier, setNewCarrier] = useState('')
  const [statementType, setStatementType] = useState('commission statement')
  const [file, setFile] = useState<File | null>(null)
  const [filename, setFilename] = useState('')
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [columnMap, setColumnMap] = useState<Record<string, string>>({})
  const [sheet, setSheet] = useState('')
  const [headerRow, setHeaderRow] = useState('')
  const [footerMode, setFooterMode] = useState<'auto' | 'none'>('auto')
  // 3.4 — NO default. The buttons start unselected and the flow cannot proceed until one is pressed.
  const [signAnswer, setSignAnswer] = useState<SignAnswer>(null)
  const [assign, setAssign] = useState<Record<string, string>>({})
  const [reversal, setReversal] = useState<Record<string, boolean>>({})
  const [period, setPeriod] = useState('')
  const [typedTotal, setTypedTotal] = useState('')
  const [attest, setAttest] = useState('')
  const [commitRes, setCommitRes] = useState<CommitResp | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const restored = useRef(false)

  const flash = useCallback((m: string) => { setMsg(m); setTimeout(() => setMsg(''), 6000) }, [])
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
      if (!inst) return
      const p = (inst.payload || {}) as Record<string, unknown>
      if (typeof p.carrier_id === 'string') setCarrierId(p.carrier_id)
      if (typeof p.statement_type === 'string') setStatementType(p.statement_type)
      if (p.column_map && typeof p.column_map === 'object') setColumnMap(p.column_map as Record<string, string>)
      if (p.sign_answer === 'positive' || p.sign_answer === 'negative') setSignAnswer(p.sign_answer)
      if (Array.isArray(p.assignments)) {
        const a: Record<string, string> = {}; const rv: Record<string, boolean> = {}
        for (const x of p.assignments as Assignment[]) { a[x.label] = x.bucket; rv[x.label] = !!x.is_reversal }
        setAssign(a); setReversal(rv)
      }
      if (typeof p.period === 'string') setPeriod(p.period)
      if (typeof p.typed_total === 'string') setTypedTotal(p.typed_total)
      if (typeof p.filename === 'string') setFilename(p.filename)
      if (p.analysis && typeof p.analysis === 'object') setAnalysis(p.analysis as Analysis)
      if (inst.status === 'verified') setStep('3.9')
      else if (STEP_KEYS.includes(r!.step)) setStep(r!.step)
      flash(`Resumed at step ${inst.status === 'verified' ? '3.9' : r!.step}${typeof p.filename === 'string' ? ` — ${p.filename}` : ''}. Re-attach the file if you need to re-check or commit.`)
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
  const trimmedAnalysis = (a: Analysis) => ({ ...a, state: undefined, labels: a.labels.slice(0, 400).map(l => ({ ...l, sub_labels: (l.sub_labels || []).slice(0, 25) })) })

  // ── analyze (read-only) ───────────────────────────────────────────────────────────────────────
  const analyze = useCallback(async (opts: { cm?: Record<string, string>; sign?: SignAnswer; asg?: Assignment[]; typed?: string; keepStep?: boolean } = {}) => {
    if (!file) { flash('Attach the statement file first (it is not stored — drop it again after returning).'); return null }
    if (!carrierId) { flash('Pick the carrier this statement is from.'); return null }
    setBusy(true); setMsg('')
    try {
      const fd = new FormData()
      fd.append('file', file); fd.append('source_kind', 'commission'); fd.append('carrier_id', carrierId)
      fd.append('statement_type', statementType)
      const cm = opts.cm ?? columnMap
      if (Object.keys(cm).length) fd.append('column_map', JSON.stringify(cm))
      const sa = opts.sign === undefined ? signAnswer : opts.sign
      if (sa) fd.append('sign_answer', sa)
      const asg = opts.asg ?? assignments
      if (asg.length) fd.append('assignments', JSON.stringify(asg.filter(a => a.bucket !== UNASSIGNED)))
      const tt = opts.typed ?? typedTotal
      if (tt) fd.append('typed_total', tt)
      if (sheet) fd.append('sheet', sheet)
      if (headerRow !== '') fd.append('header_row', headerRow)
      fd.append('footer', footerMode)
      const a: Analysis = await apiUpload(`${BASE}/analyze`, fd)
      setAnalysis(a)
      setFilename(file.name)
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
      await persist(opts.keepStep ? step : '3.2', { carrier_id: carrierId, statement_type: statementType, filename: file.name, column_map: next, analysis: trimmedAnalysis(a) })
      return a
    } catch (e: unknown) { flash((e as Error)?.message || 'Could not read the file'); return null }
    finally { setBusy(false) }
  }, [file, carrierId, statementType, columnMap, signAnswer, assignments, typedTotal, sheet, headerRow, footerMode, period, persist, step, flash])

  // ── 3.9 commit (the SAVE) ────────────────────────────────────────────────────────────────────
  const commit = useCallback(async () => {
    if (!file) { flash('Re-attach the statement file to commit — the file itself is not stored between visits.'); return }
    if (!signAnswer) { flash('3.4 is unanswered.'); setStep('3.4'); return }
    if (unassigned.length) { flash(`${unassigned.length} label(s) are still Unassigned.`); setStep('3.6'); return }
    if (!period.trim()) { flash('Enter the period this statement is for (e.g. August 2026).'); return }
    setBusy(true); setMsg('')
    try {
      const fd = new FormData()
      fd.append('file', file); fd.append('source_kind', 'commission'); fd.append('carrier_id', carrierId)
      fd.append('statement_type', statementType); fd.append('period', period.trim())
      fd.append('column_map', JSON.stringify(columnMap)); fd.append('sign_answer', signAnswer)
      fd.append('assignments', JSON.stringify(assignments.filter(a => a.bucket !== UNASSIGNED)))
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
  }, [file, signAnswer, unassigned, period, carrierId, statementType, columnMap, assignments, attest, typedTotal, sheet, headerRow, footerMode, who, loadState, instanceKey, flash])

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

  function onFiles(fl: FileList | null) {
    const f = fl?.[0]
    if (!f) return
    if (!/\.(xlsx|xls|csv)$/i.test(f.name)) { flash('Drop an .xlsx, .xls or .csv file.'); return }
    setFile(f); setFilename(f.name); setCommitRes(null)
  }

  function resetForAnother() {
    setStep('3.1'); setFile(null); setFilename(''); setAnalysis(null); setColumnMap({}); setSheet(''); setHeaderRow(''); setFooterMode('auto')
    setSignAnswer(null); setAssign({}); setReversal({}); setPeriod(''); setTypedTotal(''); setAttest(''); setCommitRes(null); setCarrierId('')
  }

  const a = analysis
  const headers = a?.detect.headers || []
  const buckets = state?.buckets || a?.buckets || ['commission', 'spiff', 'equipment_rebate', 'residual_monthly', 'autopay_residual']
  const bucketLabels = state?.bucket_labels || a?.bucket_labels || {}
  const canLeave33 = !!columnMap.raw_amount && !!columnMap.product_name
  const tie = a?.verify.tie || null
  const needsAttest = !!a && (tie?.match === false || tie?.match === null)
  const s3 = state?.rail.stages.find(s => s.stage === '3')

  return (
    <div style={{ display: 'flex', gap: 20, padding: 24, alignItems: 'flex-start' }}>
      {/* ── LEFT RAIL — a projection of persisted state ───────────────────────────────────── */}
      <aside style={{ ...card, width: 260, flex: '0 0 260px', position: 'sticky', top: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>Onboarding</div>
        {(state?.rail.stages || []).map(s => (
          <div key={s.stage} style={{ marginBottom: 6 }}>
            <div style={{ fontSize: 13, fontWeight: s.stage === '3' ? 700 : 500, color: s.built ? 'var(--text)' : 'var(--text3)' }}>
              <Lamp status={s.status} />{s.stage}. {s.label}{!s.built && <span style={{ ...note, fontSize: 11 }}> · next build</span>}
            </div>
            {s.stage === '3' && (
              <div style={{ marginLeft: 18, marginTop: 4 }}>
                {STEPS.map(([k, l]) => {
                  const done = STEP_KEYS.indexOf(k) < STEP_KEYS.indexOf(step)
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
          </div>
        ))}
        {!!state?.rail.instances.length && (
          <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
            <div style={{ ...note, fontSize: 11, marginBottom: 4 }}>Statements in this run</div>
            {state.rail.instances.map(i => {
              const c = carriers.find(x => i.instance_key.split(':')[1] === x.id)
              return (
                <div key={i.instance_key} style={{ fontSize: 12, marginBottom: 3 }}>
                  <Lamp status={i.status} />{c?.name || i.instance_key.split(':')[1]?.slice(0, 8)} · {i.instance_key.split(':')[2]?.replace(/_/g, ' ')} · step {i.step}
                </div>
              )
            })}
          </div>
        )}
        {state && !state.state_ready && (
          <div style={{ ...note, fontSize: 11, marginTop: 10, color: '#b45309' }}>
            Your place is not being saved: migration {state.migration} is not applied yet. The flow still works.
          </div>
        )}
        {stateErr && <div style={{ ...note, fontSize: 11, marginTop: 10, color: '#ef4444' }}>{stateErr}</div>}
        {s3 && <div style={{ ...note, fontSize: 11, marginTop: 10 }}>Stage 3 is <b>{(LAMP[s3.status] || LAMP.not_started).label}</b>.</div>}
      </aside>

      {/* ── MAIN ──────────────────────────────────────────────────────────────────────────── */}
      <main style={{ flex: 1, minWidth: 0, maxWidth: 1100 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 2 }}>Commission statement intake</h1>
        <p style={{ ...note, marginBottom: 16 }}>
          Drop the statement exactly as the carrier sends it. We read it, propose what each column is and show where every proposal came from;
          you confirm only what a file cannot say. Nothing is imported until step 3.9, and 3.9 reports what was actually saved.
        </p>
        {msg && <div style={{ ...card, padding: '8px 12px', fontSize: 13, marginBottom: 14, borderColor: '#f59e0b' }}>{msg}</div>}
        {a && step !== '3.1' && (
          <div style={{ ...note, marginBottom: 12 }}>
            <b>{a.carrier.name}</b> · {a.statement_type} · {filename}{!file && <span style={{ color: '#b45309' }}> · file not attached (re-drop it in 3.1 to re-check or commit)</span>}
          </div>
        )}

        {/* 3.1 UPLOAD */}
        {step === '3.1' && (
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
                <input value={statementType} onChange={e => setStatementType(e.target.value)} style={{ ...inp, width: '100%', marginTop: 4 }} placeholder="commission statement" />
              </label>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14 }}>
              <span style={note}>Carrier not in the list?</span>
              <input value={newCarrier} onChange={e => setNewCarrier(e.target.value)} placeholder="Carrier name" style={inp} />
              <button style={btn} disabled={busy || !newCarrier.trim()} onClick={addCarrier}>Add carrier</button>
            </div>
            <div onDragOver={e => { e.preventDefault(); setDragOver(true) }} onDragLeave={() => setDragOver(false)}
              onDrop={e => { e.preventDefault(); setDragOver(false); onFiles(e.dataTransfer.files) }}
              style={{ border: `2px dashed ${dragOver ? 'var(--accent,#2563eb)' : 'var(--border)'}`, borderRadius: 12, padding: 28, textAlign: 'center', background: dragOver ? 'rgba(37,99,235,.06)' : 'transparent' }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>{file ? file.name : filename ? `Re-attach ${filename}` : 'Drop the statement here'}</div>
              <div style={{ ...note, marginBottom: 10 }}>.xlsx, .xls or .csv — every sheet is read</div>
              <input type="file" accept=".xlsx,.xls,.csv" onChange={e => onFiles(e.target.files)} />
            </div>
            <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
              <button style={primary} disabled={busy || !file || !carrierId} onClick={() => analyze().then(r => r && setStep('3.2'))}>{busy ? 'Reading…' : 'Read the file →'}</button>
              {a && !file && <button style={ghost} onClick={() => setStep('3.2')}>Continue with what was saved →</button>}
            </div>
          </div>
        )}

        {/* 3.2 DETECT */}
        {step === '3.2' && a && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>3.2 — Sheet, header row, footer</h2>
            <p style={{ ...note, marginBottom: 12 }}>What we found in the file. Override anything that is wrong and re-read.</p>
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 12 }}>
              <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Sheet</th><th align="right">Rows</th><th align="right">Header row</th><th align="right">Data rows</th><th align="left">Role</th></tr></thead>
              <tbody>{a.detect.sheets.map(s => (
                <tr key={s.name} style={{ borderTop: '1px solid var(--border)' }}>
                  <td>{s.name}</td><td style={mono}>{num(s.rows)}</td><td style={mono}>{s.header_row ?? '—'}</td><td style={mono}>{num(s.data_rows)}</td>
                  <td>{s.role === 'primary' ? 'the data' : s.role === 'continuation' ? 'continuation — appended' : s.role === 'other' ? 'different layout — not used' : 'no header found'}</td>
                </tr>))}</tbody>
            </table>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
              <label style={{ fontSize: 13 }}>Use sheet
                <select value={sheet} onChange={e => setSheet(e.target.value)} style={{ ...inp, width: '100%', marginTop: 4 }}>
                  <option value="">auto: {a.detect.sheet}</option>
                  {a.detect.sheets.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
                </select>
              </label>
              <label style={{ fontSize: 13 }}>Header row (0-based)
                <input value={headerRow} onChange={e => setHeaderRow(e.target.value.replace(/[^0-9]/g, ''))} placeholder={`auto: ${a.detect.header_row}`} style={{ ...inp, width: '100%', marginTop: 4 }} />
              </label>
              <label style={{ fontSize: 13 }}>Total row
                <select value={footerMode} onChange={e => setFooterMode(e.target.value as 'auto' | 'none')} style={{ ...inp, width: '100%', marginTop: 4 }}>
                  <option value="auto">detect by shape</option>
                  <option value="none">this file has no total row</option>
                </select>
              </label>
            </div>
            <div style={{ ...card, background: 'var(--bg,transparent)', marginBottom: 12, fontSize: 13 }}>
              <div><b>Footer / total row:</b> {a.detect.footer.detected
                ? <>found — {a.detect.footer.rows.length} row(s), value <b>{money(a.detect.footer.file_total_raw)}</b>{a.detect.footer.equals_lines_sum ? ' — equals the other lines\' sum' : a.detect.footer.equals_lines_sum === false ? ` — the other lines sum to ${money(a.detect.footer.lines_sum_raw)}` : ''}</>
                : <>not found — {a.detect.footer.basis}</>}</div>
              <div style={note}>{a.detect.footer.basis}. Data rows {num(a.detect.data_rows)} · usable {num(a.detect.usable_rows)} · footer rows dropped {a.detect.footer_rows_dropped}.</div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={ghost} onClick={() => setStep('3.1')}>← Back</button>
              <button style={btn} disabled={busy || !file} onClick={() => analyze({ keepStep: true })}>Re-read with these settings</button>
              <button style={primary} onClick={() => go('3.3')}>Columns →</button>
            </div>
          </div>
        )}

        {/* 3.3 COLUMNS */}
        {step === '3.3' && a && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>3.3 — Confirm the columns</h2>
            <p style={{ ...note, marginBottom: 12 }}>Each platform field, the column we propose, where that proposal came from, and three values from that column. The <b>Amount</b> and <b>Product / description</b> (the label) are required.</p>
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Platform field</th><th align="left">Your column</th><th align="left">Provenance</th><th align="left">Sample values</th></tr></thead>
              <tbody>{a.columns.map(c => {
                const cur = columnMap[c.target_field] || ''
                const live = a.columns.find(x => x.target_field === c.target_field)
                const samples = cur === c.column ? c.samples : []
                return (
                  <tr key={c.target_field} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px 4px' }}>{c.label}{c.required && <span style={{ color: '#ef4444' }}> *</span>}<div style={{ ...note, fontSize: 11 }}>{c.target_field} · {c.transform}</div></td>
                    <td style={{ padding: '6px 4px' }}>
                      <select value={cur} onChange={e => setColumnMap(m => { const n = { ...m }; if (e.target.value) n[c.target_field] = e.target.value; else delete n[c.target_field]; return n })} style={{ ...inp, minWidth: 180 }}>
                        <option value="">— not in this file —</option>
                        {headers.map(h => <option key={h} value={h}>{h}</option>)}
                      </select>
                    </td>
                    <td style={{ padding: '6px 4px' }}>{cur && cur === live?.column ? <Badge prov={c.provenance} label={c.provenance_label} /> : cur ? <Badge prov="from your file" label="your pick" /> : <Badge prov={null} />}</td>
                    <td style={{ padding: '6px 4px', ...note }}>{samples.length ? samples.join(' · ') : cur ? 're-check to see values' : ''}</td>
                  </tr>
                )
              })}</tbody>
            </table>
            {a.money_columns.length > 1 && (
              <div style={{ ...card, background: 'var(--bg,transparent)', marginTop: 12, fontSize: 13 }}>
                <b>Money columns in this file</b> — the one mapped as Amount is used; the others are recorded with their totals, never silently ignored.
                <ul style={{ margin: '6px 0 0 18px' }}>{a.money_columns.map(m => <li key={m.header}>{m.header}: <span style={mono}>{money(m.sum)}</span> over {num(m.cells)} cells {m.is_amount ? '— the amount' : '— recorded as ignored'}</li>)}</ul>
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
              <button style={ghost} onClick={() => setStep('3.2')}>← Back</button>
              <button style={btn} disabled={busy || !file} onClick={() => analyze({ keepStep: true })}>Re-check with these columns</button>
              <button style={primary} disabled={!canLeave33} title={canLeave33 ? '' : 'Map the Amount and the Product / description first'}
                onClick={() => { persist('3.4', { column_map: columnMap }); analyze({ keepStep: true }).finally(() => setStep('3.4')) }}>Which sign is money earned →</button>
            </div>
          </div>
        )}

        {/* 3.4 SIGN — verbatim, no default */}
        {step === '3.4' && a && (
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
            </div>
          </div>
        )}

        {/* 3.5 / 3.6 LABELS + BUCKETS */}
        {(step === '3.5' || step === '3.6') && a && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>{step === '3.5' ? '3.5 — Labels found in the file' : '3.6 — Put every label in a bucket'}</h2>
            <p style={{ ...note, marginBottom: 12 }}>
              {step === '3.5'
                ? `${a.labels.length} distinct labels, largest first. Σ canonical is the amount after your sign answer (earned is ${signAnswer}). A label pre-placed in a bucket shows where that came from.`
                : 'Every label must be in one of the five buckets — there is no "other". A reversal (chargeback / deactivation) goes into the bucket it reverses; it books negative there, so the bucket reads net.'}
            </p>
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
                {[UNASSIGNED, ...buckets].map(b => {
                  const cards = a.labels.filter(l => (assign[l.label] || UNASSIGNED) === b)
                  const sum = cards.reduce((s, l) => s + (l.sum_canonical ?? l.sum_raw), 0)
                  return (
                    <div key={b} style={{ ...card, padding: 10, borderColor: b === UNASSIGNED && cards.length ? '#ef4444' : 'var(--border)', minHeight: 120 }}>
                      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>{b === UNASSIGNED ? 'Unassigned' : bucketLabels[b] || b}</div>
                      <div style={{ ...note, fontSize: 11, marginBottom: 8 }}>{cards.length} label(s) · {money(sum)}</div>
                      {cards.map(l => (
                        <div key={l.label} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 8, marginBottom: 6, background: 'var(--bg,transparent)' }}>
                          <div style={{ fontWeight: 600, fontSize: 12 }}>{l.label}</div>
                          <div style={{ ...note, fontSize: 11 }}>{num(l.count)} rows · {money(l.sum_canonical ?? l.sum_raw)} · {l.sign_mix.replace('_', ' ')}</div>
                          {assign[l.label] && assign[l.label] !== UNASSIGNED && l.provenance && l.bucket === assign[l.label] && <div style={{ marginTop: 4 }}><Badge prov={l.provenance} label={l.provenance_label} /></div>}
                          <select value={assign[l.label] || UNASSIGNED} onChange={e => setAssign(m => ({ ...m, [l.label]: e.target.value }))} style={{ ...inp, width: '100%', marginTop: 6, fontSize: 12, padding: '4px 6px' }}>
                            <option value={UNASSIGNED}>— unassigned —</option>
                            {buckets.map(x => <option key={x} value={x}>{bucketLabels[x] || x}</option>)}
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
                  onClick={() => { persist('3.7', { assignments }); analyze({ keepStep: true }).finally(() => setStep('3.7')) }}>Store / account attribution →</button>
                {!!unassigned.length && <span style={{ ...note, color: '#ef4444' }}>{unassigned.length} label(s) unassigned: {unassigned.slice(0, 5).join(', ')}{unassigned.length > 5 ? ' …' : ''}</span>}
              </>}
            </div>
          </div>
        )}

        {/* 3.7 IDENTITY (surfaced) */}
        {step === '3.7' && a && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>3.7 — Store / account attribution</h2>
            <p style={{ ...note, marginBottom: 12 }}>The store, account and rep strings this statement carries, with how much money sits behind each. Resolving them to your stores uses the same resolver as the sales stage, which is the next build; nothing here blocks the commission tie-out.</p>
            {Object.keys(a.identity).length === 0 && <div style={note}>This file carries no store, account or rep column that is mapped.</div>}
            {Object.entries(a.identity).map(([field, rows]) => (
              <div key={field} style={{ marginBottom: 10 }}>
                <div style={{ fontWeight: 700, fontSize: 13 }}>{field.replace('_', ' ')} · {rows.length} distinct</div>
                <div style={{ ...note, fontSize: 12 }}>{rows.slice(0, 12).map(r => `${r.value} (${num(r.count)} · ${money(r.sum_raw)})`).join(' · ')}{rows.length > 12 ? ' …' : ''}</div>
              </div>
            ))}
            <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
              <button style={ghost} onClick={() => setStep('3.6')}>← Back</button>
              <button style={primary} onClick={() => go('3.8')}>Totals →</button>
            </div>
          </div>
        )}

        {/* 3.8 TOTALS */}
        {step === '3.8' && a && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>3.8 — Our totals beside the file&apos;s</h2>
            <p style={{ ...note, marginBottom: 12 }}>{a.verify.basis}. Match means zero difference to the cent.</p>
            {a.verify.banners.map((b, i) => <div key={i} style={{ ...card, padding: '8px 12px', fontSize: 13, marginBottom: 8, borderColor: '#f59e0b', background: 'rgba(245,158,11,.08)' }}>{b}</div>)}
            {!a.verify.totals && <div style={{ ...note, marginBottom: 12, color: '#b45309' }}>Answer 3.4 (and re-attach the file) to compute the totals.</div>}
            {a.verify.totals && tie && (
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 12 }}>
                <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Bucket</th><th align="right">Gross</th><th align="right">Chargebacks</th><th align="right">Net</th><th align="right">Rows</th></tr></thead>
                <tbody>
                  {buckets.map(b => { const x = a.verify.totals!.buckets[b]; return (
                    <tr key={b} style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>{bucketLabels[b] || b}</td><td style={mono}>{money(x?.gross)}</td><td style={{ ...mono, color: (x?.chargebacks || 0) < 0 ? '#ef4444' : 'inherit' }}>{money(x?.chargebacks)}</td><td style={{ ...mono, fontWeight: 600 }}>{money(x?.net)}</td><td style={mono}>{num(x?.count)}</td></tr>) })}
                  {a.verify.totals.other.count > 0 && <tr style={{ borderTop: '1px solid var(--border)', color: '#ef4444' }}><td style={{ padding: '5px 4px' }}>Unassigned (booked to no bucket)</td><td style={mono}>{money(a.verify.totals.other.gross)}</td><td style={mono}>{money(a.verify.totals.other.chargebacks)}</td><td style={mono}>{money(a.verify.totals.other.net)}</td><td style={mono}>{num(a.verify.totals.other.count)}</td></tr>}
                  <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700 }}><td style={{ padding: '6px 4px' }}>Σ canonical (all buckets)</td><td /><td /><td style={mono}>{money(tie.our_total)}</td><td /></tr>
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
                <button style={{ ...btn, marginLeft: 8 }} disabled={busy || !file} onClick={() => analyze({ keepStep: true, typed: typedTotal })}>Recompute</button>
              </label>
            )}
            {needsAttest && (
              <label style={{ fontSize: 13, display: 'block', margin: '10px 0' }}>{tie?.match === false ? 'The totals differ.' : 'There is nothing to compare against.'} To proceed anyway, say why (recorded with your name):
                <textarea value={attest} onChange={e => setAttest(e.target.value)} rows={2} style={{ ...inp, width: '100%', marginTop: 4 }} placeholder="Reason for accepting this difference" />
              </label>
            )}
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <button style={ghost} onClick={() => setStep('3.7')}>← Back</button>
              <button style={btn} disabled={busy || !file} onClick={() => analyze({ keepStep: true })}>Recompute</button>
              <button style={ghost} onClick={() => setStep('3.3')}>Fix the columns</button>
              <button style={ghost} onClick={() => setStep('3.6')}>Fix the buckets</button>
              <button style={primary} disabled={!a.verify.totals || (needsAttest && !attest.trim())} onClick={() => go('3.9', { period, typed_total: typedTotal, attestation: attest })}>Confirm →</button>
            </div>
          </div>
        )}

        {/* 3.9 CONFIRM + RESULT */}
        {step === '3.9' && a && (
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>3.9 — Confirm this statement</h2>
            {!commitRes && <>
              <p style={{ ...note, marginBottom: 12 }}>Confirming saves the column map (with your sign answer) for this carrier and statement type, saves the bucket rules, imports the rows, and then re-reads what landed. The numbers below come back from the table, not from this screen.</p>
              <label style={{ fontSize: 13, display: 'block', marginBottom: 12 }}>Period this statement is for
                <input value={period} onChange={e => setPeriod(e.target.value)} placeholder="e.g. August 2026" style={{ ...inp, marginLeft: 8, width: 200 }} />
                {a.period.spans_two_months && <span style={{ ...note, marginLeft: 8, color: '#b45309' }}>the dates span {a.period.months.map(m => `${m.period} (${m.rows})`).join(', ')} — pick the month this statement is for</span>}
                {!a.period.spans_two_months && a.period.proposed && <span style={{ ...note, marginLeft: 8 }}>from the statement&apos;s own dates</span>}
              </label>
              <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>Confirm {a.carrier.name} — {period || '(period)'} — net {money(tie?.our_total)}</div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button style={ghost} onClick={() => setStep('3.8')}>← Back</button>
                <button style={primary} disabled={busy || !file || !period.trim()} onClick={commit}>{busy ? 'Saving…' : 'Confirm and import'}</button>
                {!file && <span style={{ ...note, color: '#b45309', alignSelf: 'center' }}>re-attach the file in 3.1 to commit</span>}
              </div>
            </>}
            {commitRes && (
              <div>
                <div style={{ ...card, borderColor: commitRes.ok ? '#16a34a' : '#ef4444', background: commitRes.ok ? 'rgba(22,163,74,.06)' : 'rgba(239,68,68,.06)', marginBottom: 12 }}>
                  <div style={{ fontWeight: 700, fontSize: 15 }}>{commitRes.ok ? 'Saved and verified' : 'Saved, but NOT verified'} — {commitRes.carrier.name} · {commitRes.period}</div>
                  {!commitRes.ok && <ul style={{ margin: '6px 0 0 18px', color: '#ef4444', fontSize: 13 }}>{commitRes.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>}
                  <div style={{ ...note, marginTop: 6 }}>
                    Column map saved for {commitRes.mapping_saved.length} fields (sign: {commitRes.sign_convention}) · {commitRes.rules_saved} bucket rules · rows inserted {num(commitRes.verified_numbers.rows_inserted)} · <b>rows re-read from the table: {num(commitRes.verified_numbers.rows_landed)}</b> of {num(commitRes.verified_numbers.rows_built)} built · footer rows dropped {commitRes.verified_numbers.footer_rows_dropped}
                    {!commitRes.state.saved && <> · your place was not saved ({commitRes.state.reason})</>}
                  </div>
                </div>
                <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 12 }}>
                  <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Bucket (re-read)</th><th align="right">Gross</th><th align="right">Chargebacks</th><th align="right">Net</th></tr></thead>
                  <tbody>
                    {buckets.map(b => { const x = commitRes.verified_numbers.totals.buckets[b]; return <tr key={b} style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '5px 4px' }}>{bucketLabels[b] || b}</td><td style={mono}>{money(x?.gross)}</td><td style={mono}>{money(x?.chargebacks)}</td><td style={{ ...mono, fontWeight: 600 }}>{money(x?.net)}</td></tr> })}
                    <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700 }}><td style={{ padding: '5px 4px' }}>Σ canonical</td><td /><td /><td style={mono}>{money(commitRes.verified_numbers.tie.our_total)}</td></tr>
                    <tr><td style={{ padding: '5px 4px' }}>File&apos;s own total</td><td /><td /><td style={mono}>{money(commitRes.verified_numbers.tie.file_total)}</td></tr>
                    <tr style={{ fontWeight: 700, color: commitRes.verified_numbers.tie.match ? '#16a34a' : '#ef4444' }}><td style={{ padding: '5px 4px' }}>Difference</td><td /><td /><td style={mono}>{money(commitRes.verified_numbers.tie.difference)}</td></tr>
                  </tbody>
                </table>
                {commitRes.verified_numbers.attestation && <div style={{ ...note, marginBottom: 8 }}>Attested: &quot;{commitRes.verified_numbers.attestation.reason}&quot; — {commitRes.verified_numbers.confirmed_by}</div>}
                <div style={{ display: 'flex', gap: 8 }}>
                  <button style={primary} onClick={resetForAnother}>Another carrier?</button>
                  <a href="/commcalc/commission-ledger" style={{ ...ghost, textDecoration: 'none', display: 'inline-block' }}>Open the Commission Ledger</a>
                  {!commitRes.ok && <button style={ghost} onClick={() => { setCommitRes(null); setStep('3.8') }}>Back to the totals</button>}
                </div>
              </div>
            )}
          </div>
        )}

        {step !== '3.1' && !a && (
          <div style={card}>
            <div style={note}>Nothing to show for this step yet — start at 3.1 by dropping the statement.</div>
            <button style={{ ...primary, marginTop: 10 }} onClick={() => setStep('3.1')}>Go to 3.1</button>
          </div>
        )}
      </main>
    </div>
  )
}
