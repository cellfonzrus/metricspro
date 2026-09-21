'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiUpload } from '@/lib/client'
// ═══════════════════════════════════════════════════════════════════════════════════════════════
// TENANT ONBOARDING — what every stage of the intake shares (design 2026-09-20, §0 "one shape for
// every data stage"): the payload types that mirror onboarding_intake.py, the styles, the lamps and
// provenance badges, and the ONE store / rep resolution panel that step 2.4 (sales & inventory) and
// step 3.7 (commission) both render — the same resolver on the backend, the same screen here.
// No carrier, POS vendor or tenant is named in this file (RULE TWO).
// ═══════════════════════════════════════════════════════════════════════════════════════════════

export const BASE = '/api/v1/commcalc/onboarding/intake'

// ── payload types (mirror onboarding_intake.py) ────────────────────────────────────────────────
export type Carrier = { id: string; name: string; code?: string | null; is_default?: boolean }
export type Stage = { stage: string; label: string; status: string; built: boolean; note?: string }
export type Instance = {
  instance_key: string; stage: string; kind: string; label: string; step: string; status: string; payload: Record<string, unknown>
  verified_numbers?: Record<string, unknown> | null; verified_by?: string | null; verified_at?: string | null
  blocking_reason?: string | null; current?: boolean
}
export type VerifyRow = {
  instance_key: string; stage: string; kind: string; label: string; period: string | null
  our_total: number | null; file_total: number | null; difference: number | null; match: boolean | null
  rows_landed: number | null; status: string; red: boolean; verified_by: string | null; verified_at: string | null
  blocking_reason: string | null; basis: string | null; fix_step: string
  // Stage C: the cross-check the commit ran (bill-pay lines extracted from a sales export; units activated but still on hand)
  note?: string | null
  // 2.5a: the activation split of a landed sales export, or "not yet checked" (owner 2026-09-21)
  activation_note?: string | null
}
// links / reports are ScreenLink SCREEN KEYS (the sidebar's own hrefs, RBAC-gated) — derived by the backend from the
// one consumers map (landing_identity.CONSUMERS); `shows_in` per monthly line = that landing table's readers
export type Runbook = {
  links: { label: string; screen: string }[]
  reports?: { label: string; screen: string; why: string }[]
  monthly: { instance_key: string; kind: string; label: string; filename_example: string | null; lands_in: string; mapping_saved: boolean; status: string
             shows_in?: { screen: string; label: string; needs: string[]; gate: boolean; why?: string | null }[] }[]
  note: string
}
export type Rail = {
  stages: Stage[]; steps: { key: string; label: string }[]; steps_by_stage: Record<string, { key: string; label: string }[]>
  instances: Instance[]; resume: { instance_key: string; step: string; stage: string } | null
  verify_table: VerifyRow[]; runbook: Runbook
  sign_off: { signed: boolean; by: string | null; at: string | null; on_behalf: boolean; all_verified: boolean }
}
export type Layout = { report_key: string; label: string; default: boolean; target_table?: string | null }
// `matrix`: the file is read by its destination's own parser (an X-report tender matrix, a settlement export) — no column step.
export type SourceKind = { value: string; label: string; built: boolean; target_table: string | null; layouts: Layout[]; matrix?: boolean; hint?: string | null }
// Stage C — what the 2.0 "other reports" picker needs: the portal registry + this org's processor sources and the two
// settlement roles (config rows, never named here), and which processor bill-pay feed this org's coverage recon reads.
export type MerchantPortals = {
  catalog: { key: string; label: string; settlement_role: string }[]
  sources: { key: string; label: string; settlement_role: string; enabled: boolean }[]
  roles: string[]; role_titles: Record<string, string>
}
export type BillpayFeed = { processor: string | null; default_layout: string; source: string; note: string | null }
// THE BUCKET REGISTRY (commcalc.commission_bucket, mig 1009) — one row per bucket per org; `kind` decides
// the sign a line books with. The page renders whatever the backend sends: no bucket key is named here.
export type BucketRow = {
  key: string; label: string; kind: 'earned' | 'deduction' | string; sort_order: number; is_active: boolean
  hint_words: string[]; pl_line_key: string | null; is_builtin: boolean; column_backed: boolean; source?: string
}
export type BucketMeta = { ready: boolean; migration: string; source: string; tenant_rows?: number; house_rows?: number }
// ── Stage D — REPORT LINKS (index §30.11): every loaded report linked to every other by the columns they
// share, the match counts EACH WAY. Computed by the backend from the LANDED rows (the kinds' own re-reads)
// through ONE pairing implementation (inventory_sold_recon.line_pairings); the page renders, never pairs.
export type LinkCounts = { matched: number; lines: number; no_key: number }
export type LinkDirection = LinkCounts & {
  with_key: number; unmatched: number; ambiguous: number; distinct_keys: number; unmatched_distinct: number; ambiguous_distinct: number
  unmatched_sample: { key: string; lines: number; sample_line?: string }[]
  ambiguous_sample: { key: string; lines: number; other_side_lines: number }[]
  via_lines?: number; via_label?: string; via_reasons?: Record<string, number>
}
export type LinkField = {
  field: string; label: string; basis: 'direct' | 'via_third_report' | string
  via: { through: string[]; through_label: string; how: string } | null
  a_column: string | string[] | null; b_column: string | string[] | null
  a_to_b: LinkDirection; b_to_a: LinkDirection; sentence_a_to_b: string; sentence_b_to_a: string; any_match: boolean
}
export type LinkPair = { a: string; b: string; a_label: string; b_label: string; shared: string[]; strongest: string | null; linked: boolean; note: string | null; fields: LinkField[] }
export type LinkCell = {
  a: string; b: string; shared: string[]; strongest: string | null; linked: boolean; note: string | null
  field: string | null; label: string | null; basis: string | null; a_to_b: LinkCounts | null; b_to_a: LinkCounts | null
}
export type LinkSource = { instance_key: string; label: string; kind: string; lines: number; linked_to: number; shared_with: number; fields: string[] }
export type LinkMatrix = {
  sources: LinkSource[]; skipped: { instance_key: string; label: string; note: string | null }[]
  cells: Record<string, LinkCell>; note: string | null; fields: { field: string; label: string }[]; bridges: string[]
}
export type ReportLinks = {
  available: boolean; computed: boolean; cached: boolean; stale: boolean | null; computed_at: string | null
  matrix: LinkMatrix | null; detail: LinkPair | null; detail_note?: string
  notes?: Record<string, string>; rereads?: Record<string, string>; how: string; note: string | null
}
export type StateResp = {
  state_ready: boolean; migration: string; note?: string; rail: Rail; carriers: Carrier[]
  pos_sources: { pos_key: string; label: string }[]; stores: { store_code: string; address: string | null; market: string | null }[]
  employees: string[]; company: { companies: number; stores: number; carriers: number }
  source_kinds: SourceKind[]; inventory_none_key: string; statement_type_default: string
  other_kinds?: string[]; merchant_portals?: MerchantPortals; billpay_feed?: BillpayFeed
  sign_question: string; buckets: string[]; bucket_labels: Record<string, string>; save?: { saved: boolean; reason?: string }
  bucket_rows?: BucketRow[]; bucket_meta?: BucketMeta
  report_links?: ReportLinks
}
export type SaveResult = { saved: boolean; reason?: string }
export type Column = {
  target_field: string; label: string; required: boolean; transform: string; column: string
  provenance: string | null; provenance_label: string | null; confidence: string; samples: string[]
}
export type MoneyCol = { header: string; sum: number; cells: number; is_amount: boolean }
export type Detect = {
  sheet: string | null; header_row: number | null
  sheets: { name: string; rows: number; header_row: number | null; data_rows: number; used: boolean; role: string }[]
  headers: string[]; data_rows: number; usable_rows: number
  footer: { detected: boolean; rows: { row: number; raw_amount: number }[]; file_total_raw: number | null; lines_sum_raw: number; equals_lines_sum: boolean | null; basis: string }
  footer_rows_dropped: number; identity_fields: string[]
}
export type StoreRow = {
  value: string; count: number; sum_raw: number; resolved_code: string | null; how: string | null
  action: string | null; decision_code: string | null; reason: string | null; status: string; lands_as: string | null; needs?: string
  label?: string | null      // a settlement export's DBA / store label beside its merchant id
}
export type RepRow = { value: string; count: number; sum_raw: number; resolved_name: string | null; how: string | null; action: string | null; decision_name: string | null; status: string }
export type Tie = { our_total: number; file_total: number | null; file_total_source: string | null; difference: number | null; match: boolean | null }
export type FileRef = { stored: boolean; bucket?: string; path?: string; filename?: string; size?: number; reason?: string }
export type StoreDecision = { action: 'assign' | 'create' | 'not_ours' | 'company_level'; store_code?: string; reason?: string; address?: string }
export type RepDecision = { action: 'assign' | 'leave'; canonical?: string }
export type IdentityDecisions = { store?: Record<string, StoreDecision>; rep?: Record<string, RepDecision> }

// ── styles + atoms ─────────────────────────────────────────────────────────────────────────────
export const money = (n: number | null | undefined) => (n === null || n === undefined ? '—' : (n || 0).toLocaleString('en-US', { style: 'currency', currency: 'USD' }))
export const num = (n: number | null | undefined) => (n === null || n === undefined ? '—' : (n || 0).toLocaleString('en-US'))
export const inp: React.CSSProperties = { padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)' }
export const btn: React.CSSProperties = { ...inp, cursor: 'pointer', fontWeight: 600 }
export const primary: React.CSSProperties = { ...btn, background: 'var(--accent,#2563eb)', color: '#fff', border: 'none', padding: '9px 18px' }
export const ghost: React.CSSProperties = { ...btn, background: 'transparent' }
export const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 12, padding: 16, background: 'var(--surface)' }
export const note: React.CSSProperties = { color: 'var(--text2)', fontSize: 13 }
export const mono: React.CSSProperties = { fontVariantNumeric: 'tabular-nums', textAlign: 'right' }
export const LAMP: Record<string, { bg: string; label: string }> = {
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

// ── AUTO-SAVE (owner 2026-09-20: "as you keep assigning the buckets it should auto save and give an
// option to save manually also — if you refresh and come out of the module it is not being saved").
// Design §0.2 / §4: every screen writes its payload on each change. `useAutoSave` debounces (600 ms,
// trailing) a PUT /state of the step's draft, merges pending patches, flushes on demand (the Save
// button), on unmount and on pagehide / beforeunload with a keepalive request, and reports its state
// so the person can SEE that their work is safe: saving… / saved hh:mm / not saved — why.
export type SaveStatus = { state: 'idle' | 'saving' | 'saved' | 'error'; at: string | null; reason: string | null; pending: boolean }
export const AUTOSAVE_MS = 600

export function useAutoSave(persistNow: (patch: Record<string, unknown>, keepalive?: boolean) => Promise<SaveResult>) {
  const [status, setStatus] = useState<SaveStatus>({ state: 'idle', at: null, reason: null, pending: false })
  const pending = useRef<Record<string, unknown> | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const persistRef = useRef(persistNow)
  useEffect(() => { persistRef.current = persistNow }, [persistNow])

  const flush = useCallback(async (keepalive = false) => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null }
    const patch = pending.current
    if (!patch) return
    pending.current = null
    setStatus(s => ({ ...s, state: 'saving', pending: false }))
    try {
      const r = await persistRef.current(patch, keepalive)
      if (r.saved) setStatus({ state: 'saved', at: new Date().toISOString(), reason: null, pending: false })
      else setStatus({ state: 'error', at: null, reason: r.reason || 'the server did not confirm the save', pending: false })
    } catch (e: unknown) {
      pending.current = { ...patch, ...(pending.current || {}) }          // keep it for the next try
      setStatus({ state: 'error', at: null, reason: (e as Error)?.message || 'PUT /state failed', pending: true })
    }
  }, [])

  const schedule = useCallback((patch: Record<string, unknown>) => {
    pending.current = { ...(pending.current || {}), ...patch }
    setStatus(s => ({ ...s, pending: true }))
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => { flush(false) }, AUTOSAVE_MS)
  }, [flush])

  const saveNow = useCallback((patch?: Record<string, unknown>) => {
    if (patch) pending.current = { ...(pending.current || {}), ...patch }
    return flush(false)
  }, [flush])

  useEffect(() => {
    const onHide = () => { if (pending.current) flush(true) }
    window.addEventListener('pagehide', onHide)
    window.addEventListener('beforeunload', onHide)
    return () => {
      window.removeEventListener('pagehide', onHide)
      window.removeEventListener('beforeunload', onHide)
      if (pending.current) flush(true)                                     // route change / unmount
    }
  }, [flush])

  return { schedule, saveNow, status }
}

export function SaveIndicator({ status, stateReady, migration }: { status: SaveStatus; stateReady: boolean; migration?: string }) {
  const hhmm = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }) : '')
  if (!stateReady) return <span style={{ ...note, fontSize: 12, color: '#ef4444' }}>Not saved: run migration {migration || '1007_onboarding_intake_state.sql'}</span>
  if (status.state === 'saving') return <span style={{ ...note, fontSize: 12 }}>Saving…</span>
  if (status.state === 'error') return <span style={{ ...note, fontSize: 12, color: '#ef4444' }}>Not saved — {status.reason}</span>
  if (status.state === 'saved') return <span style={{ ...note, fontSize: 12, color: '#15803d' }}>Saved ✓ {hhmm(status.at)}{status.pending ? ' · changes pending…' : ''}</span>
  return <span style={{ ...note, fontSize: 12 }}>{status.pending ? 'Unsaved changes…' : 'Auto-save on'}</span>
}

export function SaveButton({ onSave, status, stateReady, migration, busy }: { onSave: () => void; status: SaveStatus; stateReady: boolean; migration?: string; busy?: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <button style={btn} disabled={busy || status.state === 'saving'} onClick={onSave} title="Save what you have chosen so far (it also auto-saves as you go)">Save</button>
      <SaveIndicator status={status} stateReady={stateReady} migration={migration} />
    </span>
  )
}

export function Lamp({ status }: { status: string }) {
  const l = LAMP[status] || LAMP.not_started
  return <span title={l.label} style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 5, background: l.bg, marginRight: 8, verticalAlign: 'middle' }} />
}
export function Badge({ prov, label }: { prov: string | null; label?: string | null }) {
  if (!prov) return <span style={{ ...note, fontSize: 11 }}>—</span>
  const st = PROV_STYLE[prov] || PROV_STYLE.guess
  return <span style={{ ...st, fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999, whiteSpace: 'nowrap' }}>{label || prov}</span>
}

// ── the sheet / header / footer panel (2.2 and 3.2 are the same screen) ─────────────────────────
export function DetectPanel({ d, sheet, setSheet, headerRow, setHeaderRow, footerMode, setFooterMode }: {
  d: Detect; sheet: string; setSheet: (v: string) => void; headerRow: string; setHeaderRow: (v: string) => void
  footerMode: 'auto' | 'none'; setFooterMode: (v: 'auto' | 'none') => void
}) {
  return (
    <>
      <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 12 }}>
        <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Sheet</th><th align="right">Rows</th><th align="right">Header row</th><th align="right">Data rows</th><th align="left">Role</th></tr></thead>
        <tbody>{d.sheets.map(s => (
          <tr key={s.name} style={{ borderTop: '1px solid var(--border)' }}>
            <td>{s.name}</td><td style={mono}>{num(s.rows)}</td><td style={mono}>{s.header_row ?? '—'}</td><td style={mono}>{num(s.data_rows)}</td>
            <td>{s.role === 'primary' ? 'the data' : s.role === 'continuation' ? 'continuation — appended' : s.role === 'other' ? 'different layout — not used' : 'no header found'}</td>
          </tr>))}</tbody>
      </table>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
        <label style={{ fontSize: 13 }}>Use sheet
          <select value={sheet} onChange={e => setSheet(e.target.value)} style={{ ...inp, width: '100%', marginTop: 4 }}>
            <option value="">auto: {d.sheet}</option>
            {d.sheets.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 13 }}>Header row (0-based)
          <input value={headerRow} onChange={e => setHeaderRow(e.target.value.replace(/[^0-9]/g, ''))} placeholder={`auto: ${d.header_row}`} style={{ ...inp, width: '100%', marginTop: 4 }} />
        </label>
        <label style={{ fontSize: 13 }}>Total row
          <select value={footerMode} onChange={e => setFooterMode(e.target.value as 'auto' | 'none')} style={{ ...inp, width: '100%', marginTop: 4 }}>
            <option value="auto">detect by shape</option>
            <option value="none">this file has no total row</option>
          </select>
        </label>
      </div>
      <div style={{ ...card, background: 'var(--bg,transparent)', marginBottom: 12, fontSize: 13 }}>
        <div><b>Footer / total row:</b> {d.footer.detected
          ? <>found — {d.footer.rows.length} row(s), value <b>{money(d.footer.file_total_raw)}</b>{d.footer.equals_lines_sum ? ' — equals the other lines\' sum' : d.footer.equals_lines_sum === false ? ` — the other lines sum to ${money(d.footer.lines_sum_raw)}` : ''}</>
          : <>not found — {d.footer.basis}</>}</div>
        <div style={note}>{d.footer.basis}. Data rows {num(d.data_rows)} · usable {num(d.usable_rows)} · footer rows dropped {d.footer_rows_dropped}.</div>
      </div>
    </>
  )
}

// ── the column table (2.3 and 3.3 are the same screen) ──────────────────────────────────────────
export function ColumnsTable({ columns, headers, columnMap, setColumnMap }: {
  columns: Column[]; headers: string[]; columnMap: Record<string, string>; setColumnMap: (f: (m: Record<string, string>) => Record<string, string>) => void
}) {
  return (
    <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
      <thead><tr style={{ color: 'var(--text2)' }}><th align="left">Platform field</th><th align="left">Your column</th><th align="left">Provenance</th><th align="left">Sample values</th></tr></thead>
      <tbody>{columns.map(c => {
        const cur = columnMap[c.target_field] || ''
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
            <td style={{ padding: '6px 4px' }}>{cur && cur === c.column ? <Badge prov={c.provenance} label={c.provenance_label} /> : cur ? <Badge prov="from your file" label="your pick" /> : <Badge prov={null} />}</td>
            <td style={{ padding: '6px 4px', ...note }}>{samples.length ? samples.join(' · ') : cur ? 're-check to see values' : ''}</td>
          </tr>
        )
      })}</tbody>
    </table>
  )
}

// ── THE SHARED STORE / REP RESOLUTION PANEL (2.4 and 3.7) ───────────────────────────────────────
// Every store string the file carries, with count and Σ, what the platform resolved it to and by which
// rule (alias / address / code), and — for a string it does not know — the three answers: assign it to
// one of your stores, create the store, or mark it not yours with a reason. Zero unresolved is the exit
// gate (design §5.7): an unresolved string never lands as "Default".
export function StoreResolver({ stores, reps, storeList, employees, decisions, setDecisions, allowCompanyLevel, identityKind }: {
  stores: StoreRow[]; reps: RepRow[]; storeList: { store_code: string; address: string | null }[]; employees: string[]
  decisions: IdentityDecisions; setDecisions: (f: (d: IdentityDecisions) => IdentityDecisions) => void; allowCompanyLevel?: boolean
  // 'store' (a store string the address resolver knows) or 'merchant_id' (a processor's merchant / terminal / account id,
  // resolved and WRITTEN through the mig-902 per-store merchant-id map — Stage C)
  identityKind?: 'store' | 'merchant_id'
}) {
  const setStore = (raw: string, patch: Partial<StoreDecision> | null) => setDecisions(d => {
    const s = { ...(d.store || {}) }
    if (!patch) delete s[raw]; else s[raw] = { ...(s[raw] || { action: 'assign' }), ...patch } as StoreDecision
    return { ...d, store: s }
  })
  const setRep = (raw: string, patch: Partial<RepDecision> | null) => setDecisions(d => {
    const r = { ...(d.rep || {}) }
    if (!patch) delete r[raw]; else r[raw] = { ...(r[raw] || { action: 'assign' }), ...patch } as RepDecision
    return { ...d, rep: r }
  })
  const unresolved = stores.filter(s => s.status !== 'resolved').length
  return (
    <div>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>{identityKind === 'merchant_id' ? 'Merchant / account ids in this file' : 'Store strings in this file'} · {stores.length} distinct {unresolved ? <span style={{ color: '#ef4444' }}>· {unresolved} unresolved</span> : <span style={{ color: '#16a34a' }}>· all resolved</span>}</div>
      {identityKind === 'merchant_id' && <div style={{ ...note, marginBottom: 6 }}>Each id is the processor&apos;s own name for one of your stores. An assignment is saved to the per-store merchant-id map every feed resolves through — the scheduled pull will use it too.</div>}
      {stores.length === 0 && <div style={note}>No store column is mapped, or every store cell is blank.</div>}
      <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 14 }}>
        <thead><tr style={{ color: 'var(--text2)' }}><th align="left">As written in the file</th><th align="right">Rows</th><th align="right">Σ</th><th align="left">Resolves to</th><th align="left">Your decision</th></tr></thead>
        <tbody>{stores.map(s => {
          const d = decisions.store?.[s.value]
          const act = d?.action || (s.resolved_code ? 'auto' : '')
          return (
            <tr key={s.value} style={{ borderTop: '1px solid var(--border)', background: s.status !== 'resolved' ? 'rgba(239,68,68,.06)' : 'transparent' }}>
              <td style={{ padding: '6px 4px', fontWeight: 600 }}>{s.value}{s.label ? <span style={{ ...note, fontWeight: 400 }}> · {s.label}</span> : null}</td>
              <td style={mono}>{num(s.count)}</td><td style={mono}>{money(s.sum_raw)}</td>
              <td style={{ padding: '6px 4px' }}>{s.resolved_code ? <>{s.resolved_code} <span style={{ ...note, fontSize: 11 }}>by {s.how}</span></> : s.lands_as ? <>{s.lands_as} <span style={{ ...note, fontSize: 11 }}>(your decision)</span></> : s.action === 'not_ours' && s.reason ? <span style={note}>not yours — rows excluded</span> : s.action === 'company_level' ? <span style={note}>company-level</span> : <span style={{ color: '#ef4444' }}>unknown</span>}</td>
              <td style={{ padding: '6px 4px' }}>
                <select value={act} onChange={e => { const v = e.target.value; if (v === 'auto' || v === '') setStore(s.value, null); else setStore(s.value, { action: v as StoreDecision['action'] }) }} style={{ ...inp, padding: '4px 6px', fontSize: 12 }}>
                  {s.resolved_code ? <option value="auto">keep: {s.resolved_code}</option> : <option value="">— decide —</option>}
                  <option value="assign">assign to one of my stores</option>
                  <option value="create">create this store</option>
                  <option value="not_ours">not ours (give a reason)</option>
                  {allowCompanyLevel && <option value="company_level">company-level (no store)</option>}
                </select>
                {(act === 'assign') && (
                  <select value={d?.store_code || ''} onChange={e => setStore(s.value, { store_code: e.target.value })} style={{ ...inp, padding: '4px 6px', fontSize: 12, marginLeft: 6 }}>
                    <option value="">— pick a store —</option>
                    {storeList.map(st => <option key={st.store_code} value={st.store_code}>{st.store_code}{st.address ? ` · ${st.address}` : ''}</option>)}
                  </select>
                )}
                {(act === 'create') && <>
                  <input value={d?.store_code || ''} onChange={e => setStore(s.value, { store_code: e.target.value })} placeholder="new store code" style={{ ...inp, padding: '4px 6px', fontSize: 12, marginLeft: 6, width: 120 }} />
                  <input value={d?.address || ''} onChange={e => setStore(s.value, { address: e.target.value })} placeholder="address" style={{ ...inp, padding: '4px 6px', fontSize: 12, marginLeft: 6, width: 160 }} />
                </>}
                {(act === 'not_ours') && <input value={d?.reason || ''} onChange={e => setStore(s.value, { reason: e.target.value })} placeholder="why is this not yours? (required)" style={{ ...inp, padding: '4px 6px', fontSize: 12, marginLeft: 6, width: 220 }} />}
              </td>
            </tr>
          )
        })}</tbody>
      </table>
      {reps.length > 0 && <>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Rep strings · {reps.length} distinct <span style={note}>· a rep the roster does not know is shown, not a gate — assign it so pay attaches</span></div>
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <thead><tr style={{ color: 'var(--text2)' }}><th align="left">As written</th><th align="right">Rows</th><th align="right">Σ</th><th align="left">Employee</th><th align="left">Your decision</th></tr></thead>
          <tbody>{reps.map(r => {
            const d = decisions.rep?.[r.value]
            return (
              <tr key={r.value} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '6px 4px', fontWeight: 600 }}>{r.value}</td><td style={mono}>{num(r.count)}</td><td style={mono}>{money(r.sum_raw)}</td>
                <td style={{ padding: '6px 4px' }}>{r.resolved_name ? <>{r.resolved_name} <span style={{ ...note, fontSize: 11 }}>by {r.how}</span></> : d?.canonical ? <>{d.canonical} <span style={{ ...note, fontSize: 11 }}>(your decision)</span></> : <span style={{ color: '#b45309' }}>not on the roster</span>}</td>
                <td style={{ padding: '6px 4px' }}>
                  <select value={d?.action || (r.resolved_name ? 'auto' : '')} onChange={e => { const v = e.target.value; if (v === 'auto' || v === '') setRep(r.value, null); else setRep(r.value, { action: v as RepDecision['action'] }) }} style={{ ...inp, padding: '4px 6px', fontSize: 12 }}>
                    {r.resolved_name ? <option value="auto">keep: {r.resolved_name}</option> : <option value="">— decide —</option>}
                    <option value="assign">this is an employee…</option>
                    <option value="leave">leave as written</option>
                  </select>
                  {d?.action === 'assign' && (
                    <select value={d.canonical || ''} onChange={e => setRep(r.value, { canonical: e.target.value })} style={{ ...inp, padding: '4px 6px', fontSize: 12, marginLeft: 6 }}>
                      <option value="">— pick an employee —</option>
                      {employees.map(n => <option key={n} value={n}>{n}</option>)}
                    </select>
                  )}
                </td>
              </tr>
            )
          })}</tbody>
        </table>
      </>}
    </div>
  )
}

export function Dropzone({ file, filename, onFiles, dragOver, setDragOver, hint }: {
  file: File | null; filename: string; onFiles: (fl: FileList | null) => void; dragOver: boolean; setDragOver: (v: boolean) => void; hint: string
}) {
  return (
    <div onDragOver={e => { e.preventDefault(); setDragOver(true) }} onDragLeave={() => setDragOver(false)}
      onDrop={e => { e.preventDefault(); setDragOver(false); onFiles(e.dataTransfer.files) }}
      style={{ border: `2px dashed ${dragOver ? 'var(--accent,#2563eb)' : 'var(--border)'}`, borderRadius: 12, padding: 28, textAlign: 'center', background: dragOver ? 'rgba(37,99,235,.06)' : 'transparent' }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>{file ? file.name : filename ? `${filename} (kept — drop again only to replace it)` : hint}</div>
      <div style={{ ...note, marginBottom: 10 }}>.xlsx, .xls or .csv — every sheet is read</div>
      <input type="file" accept=".xlsx,.xls,.csv" onChange={e => onFiles(e.target.files)} />
    </div>
  )
}

// ── "DROP ANY REPORT HERE" — detection against the report-kind registry (design §7; index §30.8 (b)) ──
// Owner: "the system to check against what report it matches using intelligence gained by using all
// the reports." The backend (POST /commcalc/report-kinds/detect) reads the HEADER NAMES the way the
// intake does and ranks the tenant's VISIBLE registry kinds — a confirmed fingerprint first ("seen
// before as …, confirmed N times"), then header overlap, then the kind's signature fields. Nothing is
// stored. This zone only shows the answer and lets the person CONFIRM it — "This looks like your
// <label> — right?" [Yes] [No, it's …] — never assigns silently, and never offers a candidate that
// is not in the visible set it is handed (`visible` = the one visibility function's output).
export type DetectCandidate = { key: string; label: string; confidence: number; evidence: string[]; landing: string; layout?: string | null; statement_type?: string | null }
export type DetectResult = { mode: 'confirm' | 'ask' | 'none'; candidates: DetectCandidate[]; header_count: number; sheet?: string | null; registry_ready: boolean; fallback_key: string | null }

export function KindDetectZone({ visible, onPick, fallbackKey }: {
  visible: { key: string; label: string }[]; onPick: (c: DetectCandidate | null, filename: string) => void; fallbackKey?: string | null
}) {
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<DetectResult | null>(null)
  const [fname, setFname] = useState('')
  const [err, setErr] = useState('')
  const allowed = new Set(visible.map(v => v.key))
  async function detect(fl: FileList | null) {
    const f = fl?.[0]; if (!f) return
    if (!/\.(xlsx|xls|csv)$/i.test(f.name)) { setErr('Drop an .xlsx, .xls or .csv file.'); return }
    setBusy(true); setErr(''); setRes(null); setFname(f.name)
    try {
      const fd = new FormData(); fd.append('file', f)
      const r: DetectResult = await apiUpload('/api/v1/commcalc/report-kinds/detect', fd)
      setRes({ ...r, candidates: (r.candidates || []).filter(c => allowed.has(c.key)) })
    } catch (e: unknown) { setErr((e as Error)?.message || 'Could not read the file') }
    finally { setBusy(false) }
  }
  const cands = res?.candidates || []
  return (
    <div onDragOver={e => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
      onDrop={e => { e.preventDefault(); setOver(false); detect(e.dataTransfer.files) }}
      style={{ border: `2px dashed ${over ? 'var(--accent,#2563eb)' : 'var(--border)'}`, borderRadius: 12, padding: 16, marginBottom: 12, background: over ? 'rgba(37,99,235,.06)' : 'transparent' }}>
      <div style={{ fontWeight: 600 }}>Not sure which report you have? Drop any report here</div>
      <div style={{ ...note, marginBottom: 8 }}>We read only the column names and say what it looks like — you confirm. Nothing is imported or stored from this drop.</div>
      <input type="file" accept=".xlsx,.xls,.csv" onChange={e => detect(e.target.files)} disabled={busy} />
      {busy && <div style={{ ...note, marginTop: 6 }}>Reading the column names…</div>}
      {err && <div style={{ ...note, color: '#ef4444', marginTop: 6 }}>{err}</div>}
      {res && !busy && (
        <div style={{ marginTop: 10 }}>
          {res.mode === 'confirm' && cands[0] && (
            <div>
              <div style={{ fontSize: 14 }}>This looks like your <b>{cands[0].label}</b> — right? <span style={note}>({Math.round(cands[0].confidence * 100)}% · {cands[0].evidence[0]})</span></div>
              <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                <button style={primary} onClick={() => onPick(cands[0], fname)}>Yes, it is</button>
                <button style={ghost} onClick={() => { setRes({ ...res, mode: 'ask', candidates: [] }) }}>No, it&apos;s something else…</button>
              </div>
            </div>
          )}
          {res.mode === 'ask' && (
            <div>
              <div style={{ fontSize: 14 }}>{cands.length ? 'It could be one of these — which is it?' : 'Pick what it is from the cards below, or record it as something else.'}</div>
              {cands.map(c => (
                <div key={c.key} style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 6, flexWrap: 'wrap' }}>
                  <button style={btn} onClick={() => onPick(c, fname)}>{c.label}</button>
                  <span style={note}>{Math.round(c.confidence * 100)}% · {c.evidence.join(' · ')}</span>
                </div>
              ))}
              {(fallbackKey || res.fallback_key) && <div style={{ marginTop: 8 }}><button style={ghost} onClick={() => onPick(null, fname)}>Record it as something else</button></div>}
            </div>
          )}
          {res.mode === 'none' && (
            <div>
              <div style={{ fontSize: 14 }}>We don&apos;t recognise these {res.header_count} column names as any report kind you can upload.</div>
              <div style={{ marginTop: 8 }}><button style={ghost} onClick={() => onPick(null, fname)}>Record it as something else</button></div>
            </div>
          )}
          {!res.registry_ready && <div style={{ ...note, marginTop: 6 }}>Detection is running on the shipped defaults — confirmed layouts are not remembered until the registry migration is applied.</div>}
        </div>
      )}
    </div>
  )
}
