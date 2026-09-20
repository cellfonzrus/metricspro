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
  BASE, card, note, inp, btn, primary, ghost, mono, money, num, Lamp, DetectPanel, ColumnsTable, StoreResolver, Dropzone,
  type StateResp, type Instance, type Column, type MoneyCol, type Detect, type StoreRow, type RepRow, type Tie, type FileRef, type IdentityDecisions,
} from './intake-shared'

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
type Analysis2 = {
  source_kind: string; target_table: string | null; report_key: string | null; instance_key: string; filename?: string | null
  source_ref: string; layout: string; name: string; layout_label: string; as_of_date: string | null
  detect: Detect; columns: Column[]; money_columns: MoneyCol[]
  stores: StoreRow[]; reps: RepRow[]; unresolved_stores: string[]; identity_decisions: IdentityDecisions
  period: { months: { period: string; rows: number }[]; span_from: string | null; span_to: string | null; proposed: string | null; spans_two_months: boolean; dated_rows: number }
  verify: { basis: string; numbers: SalesNumbers | InvNumbers | OtherNumbers; tie: Tie | null; rows_in_file: number; rows_usable: number; footer_rows: number
            rows_excluded_not_ours: number; excluded: Record<string, string>; rows_to_land: number; ignored_money_columns: MoneyCol[]; refusals: string[] }
  file?: FileRef; state?: StateResp
}
type Commit2 = {
  ok: boolean; recorded?: boolean; problems: string[]; saved: number; source_kind: string; instance_key: string
  mapping_saved?: string[]; identity_written?: { aliases: [string, string][]; stores_created: string[]; rep_aliases: [string, string][] }
  verified_numbers: Record<string, unknown> & { rows_landed?: number; rows_built?: number; rows_inserted?: number; rows_without_device_key?: number
    rows_excluded_not_ours?: number; tie?: Tie; numbers?: SalesNumbers | InvNumbers; basis?: string; attestation?: { reason: string } | null; confirmed_by?: string | null }
  state: { saved: boolean; reason?: string }
}

export const STAGE2_STEPS: [string, string][] = [
  ['2.0', 'What do you have?'], ['2.1', 'Upload the export'], ['2.2', 'Sheet, header row, footer'], ['2.3', 'Confirm the columns'],
  ['2.4', 'Stores and reps'], ['2.5', 'Our numbers beside the file\'s'], ['2.6', 'Confirm this export'],
]
const KEYS = STAGE2_STEPS.map(s => s[0])
const KIND_LABEL: Record<string, string> = { sales: 'Sales export', pos: 'POS report', inventory: 'Inventory export', other: 'Other report' }

function ikey(kind: string, ref: string, layoutOrName: string) {
  const slug = (t: string) => t.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  return `${kind}:${slug(ref) || 'file'}:${slug(layoutOrName) || 'report'}`
}

export function Stage2Flow({ state, who, reloadState, instanceKey, setInstanceKey, step, setStep, flash }: {
  state: StateResp | null; who: string; reloadState: (ik?: string) => Promise<StateResp | null>
  instanceKey: string; setInstanceKey: (k: string) => void; step: string; setStep: (s: string) => void; flash: (m: string) => void
}) {
  const instances = useMemo(() => (state?.rail.instances || []).filter(i => i.stage === '2'), [state])
  const inst: Instance | undefined = instances.find(i => i.instance_key === instanceKey)
  const kinds = state?.source_kinds || []
  const layoutsOf = (k: string) => kinds.find(x => x.value === k)?.layouts || []

  // ── 2.0 checklist inputs ──────────────────────────────────────────────────────────────────────
  const [posRef, setPosRef] = useState('')
  const [salesKind, setSalesKind] = useState<'pos' | 'sales'>('pos')
  const [salesLayout, setSalesLayout] = useState('')
  const [invLayout, setInvLayout] = useState('')
  const [noInvReason, setNoInvReason] = useState('')
  const [otherName, setOtherName] = useState('')
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
  const [attest, setAttest] = useState('')
  const [commitRes, setCommitRes] = useState<Commit2 | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const restoredFor = useRef('')

  // restore the instance's saved choices when it becomes current (design §0.2: the rail is the state)
  useEffect(() => {
    if (!inst || restoredFor.current === inst.instance_key) return
    restoredFor.current = inst.instance_key
    const p = inst.payload || {}
    setFile(null); setA(null); setCommitRes(null); setAttest(''); setTypedTotal(''); setSheet(''); setHeaderRow(''); setFooterMode('auto')
    setFilename(typeof p.filename === 'string' ? p.filename : '')
    setKept(p.file && typeof p.file === 'object' ? (p.file as FileRef) : null)
    setColumnMap(p.column_map && typeof p.column_map === 'object' ? (p.column_map as Record<string, string>) : {})
    setDecisions(p.identity && typeof p.identity === 'object' ? (p.identity as IdentityDecisions) : {})
    setAsOf(typeof p.as_of_date === 'string' ? p.as_of_date : '')
  }, [inst])

  const persist = useCallback(async (stepKey: string, patch: Record<string, unknown>) => {
    if (!instanceKey) return
    try { await api(`${BASE}/state`, { method: 'PUT', body: JSON.stringify({ instance_key: instanceKey, step: stepKey, payload: patch, by: who }) }); await reloadState(instanceKey) } catch { /* the rail is a convenience */ }
  }, [instanceKey, who, reloadState])
  const go = useCallback((k: string, patch: Record<string, unknown> = {}) => { setStep(k); persist(k, patch) }, [persist, setStep])

  const kind = inst?.kind || ''
  const p = (inst?.payload || {}) as Record<string, unknown>
  const sourceRef = typeof p.source_ref === 'string' ? p.source_ref : ''
  const layout = typeof p.layout === 'string' ? p.layout : ''
  const otherNameOf = typeof p.name === 'string' ? p.name : ''
  const canUseKept = !file && !!kept?.stored

  // ── 2.0: add instances ────────────────────────────────────────────────────────────────────────
  async function addInstance(k: string, ref: string, lay: string, name: string) {
    const key = ikey(k, ref, k === 'other' ? name : lay)
    setBusy(true)
    try {
      await api(`${BASE}/state`, { method: 'PUT', body: JSON.stringify({ instance_key: key, step: '2.1', payload: { kind: k, source_ref: ref, layout: lay, name }, by: who }) })
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
    if (kind === 'other') fd.append('name', otherNameOf); else fd.append('layout', layout)
    if (Object.keys(columnMap).length) fd.append('column_map', JSON.stringify(columnMap))
    if (decisions.store || decisions.rep) fd.append('identity', JSON.stringify(decisions))
    if (typedTotal) fd.append('typed_total', typedTotal)
    if (asOf) fd.append('as_of_date', asOf)
    if (sheet) fd.append('sheet', sheet)
    if (headerRow !== '') fd.append('header_row', headerRow)
    fd.append('footer', footerMode)
    for (const [k, v] of Object.entries(extra)) fd.set(k, v)
    return fd
  }, [file, kept, instanceKey, kind, sourceRef, otherNameOf, layout, columnMap, decisions, typedTotal, asOf, sheet, headerRow, footerMode])

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
      await persist(opts.keepStep ? step : '2.2', { filename: file?.name || filename, column_map: next, identity: opts.dec ?? decisions, as_of_date: asOf || null })
      return r
    } catch (e: unknown) { flash((e as Error)?.message || 'Could not read the file'); return null }
    finally { setBusy(false) }
  }, [inst, file, kept, buildForm, columnMap, kind, asOf, persist, step, filename, decisions, flash])

  const commit = useCallback(async () => {
    if (!inst) return
    if (!file && !kept?.stored) { flash('Drop the file again to commit — it was not kept.'); return }
    setBusy(true)
    try {
      const fd = buildForm({ verified_by: who })
      if (attest.trim()) fd.set('attestation', JSON.stringify({ reason: attest.trim() }))
      const r: Commit2 = await apiUpload(`${BASE}/commit`, fd)
      setCommitRes(r); setStep('2.6')
      await reloadState(instanceKey)
      if (!r.ok && !r.recorded) flash('Committed with problems — see the red panel. Nothing here is reported as verified until the re-read matches.')
    } catch (e: unknown) { flash((e as Error)?.message || 'Commit refused') }
    finally { setBusy(false) }
  }, [inst, file, kept, buildForm, who, attest, reloadState, instanceKey, flash, setStep])

  function onFiles(fl: FileList | null) {
    const f = fl?.[0]
    if (!f) return
    if (!/\.(xlsx|xls|csv)$/i.test(f.name)) { flash('Drop an .xlsx, .xls or .csv file.'); return }
    setFile(f); setFilename(f.name); setCommitRes(null)
  }

  const tie = a?.verify.tie || null
  const needsAttest = !!a && kind !== 'other' && (tie?.match === false || tie?.match === null)
  const unresolved = a?.unresolved_stores || []
  const required: Record<string, string[]> = { sales: ['store', 'trans_date', 'ext_price'], pos: ['store', 'trans_date', 'ext_price'], inventory: ['store', 'sku'], other: [] }
  const canLeave23 = (required[kind] || []).every(f => !!columnMap[f])
  const isSales = kind === 'sales' || kind === 'pos'
  const n = a?.verify.numbers as (SalesNumbers & InvNumbers & OtherNumbers) | undefined

  // ── 2.0 ────────────────────────────────────────────────────────────────────────────────────────
  if (step === '2.0' || !inst) {
    const have = (k: string) => instances.filter(i => i.kind === k)
    const noInv = instances.find(i => i.instance_key === state?.inventory_none_key)
    return (
      <div style={card}>
        <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.0 — What do you have?</h2>
        <p style={{ ...note, marginBottom: 14 }}>You only need the exports you already pull from your systems. Tick what you have; each one becomes a line in the rail you can come back to. Nothing is imported until its own confirm step.</p>
        {/* Sales / POS export, per POS */}
        <div style={{ ...card, background: 'var(--bg,transparent)', marginBottom: 10 }}>
          <div style={{ fontWeight: 700, fontSize: 13 }}>Sales export (one per POS)</div>
          <div style={note}>The line-level or daily sales export your POS produces. Name the POS it comes from — a code you pick or type; a second POS is a second line.</div>
          <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <input list="pos-sources" value={posRef} onChange={e => setPosRef(e.target.value)} placeholder="POS source (e.g. its name or code)" style={{ ...inp, width: 220 }} />
            <datalist id="pos-sources">{(state?.pos_sources || []).map(s => <option key={s.pos_key} value={s.pos_key}>{s.label}</option>)}</datalist>
            <select value={salesKind} onChange={e => setSalesKind(e.target.value as 'pos' | 'sales')} style={inp}>
              <option value="pos">POS report (line-level export)</option>
              <option value="sales">Sales report (daily sales export)</option>
            </select>
            <select value={salesLayout} onChange={e => setSalesLayout(e.target.value)} style={inp}>
              <option value="">layout: default</option>
              {layoutsOf(salesKind).map(l => <option key={l.report_key} value={l.report_key}>{l.label}</option>)}
            </select>
            <button style={btn} disabled={busy || !posRef.trim()} onClick={() => addInstance(salesKind, posRef.trim(), salesLayout || (layoutsOf(salesKind).find(l => l.default)?.report_key || ''), '')}>Add</button>
          </div>
          {[...have('pos'), ...have('sales')].map(i => <div key={i.instance_key} style={{ fontSize: 12, marginTop: 6 }}><Lamp status={i.status} />{i.label} · step {i.step} <button style={{ ...ghost, padding: '2px 8px', fontSize: 12 }} onClick={() => { setInstanceKey(i.instance_key); setStep(i.status === 'verified' ? '2.6' : i.step === '2.0' ? '2.1' : i.step) }}>open</button></div>)}
        </div>
        {/* Inventory */}
        <div style={{ ...card, background: 'var(--bg,transparent)', marginBottom: 10 }}>
          <div style={{ fontWeight: 700, fontSize: 13 }}>Inventory export (on-hand listing)</div>
          <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <select value={invLayout} onChange={e => setInvLayout(e.target.value)} style={inp}>
              <option value="">layout: default</option>
              {layoutsOf('inventory').map(l => <option key={l.report_key} value={l.report_key}>{l.label}</option>)}
            </select>
            <button style={btn} disabled={busy || !posRef.trim()} title={posRef.trim() ? '' : 'name the POS source above first'} onClick={() => addInstance('inventory', posRef.trim(), invLayout || (layoutsOf('inventory').find(l => l.default)?.report_key || ''), '')}>Add inventory export</button>
            <span style={note}>or</span>
            <input value={noInvReason} onChange={e => setNoInvReason(e.target.value)} placeholder="No inventory export — because…" style={{ ...inp, width: 260 }} />
            <button style={btn} disabled={busy || !noInvReason.trim()} onClick={recordNoInventory}>Record: no inventory export</button>
          </div>
          {noInv && <div style={{ fontSize: 12, marginTop: 6 }}><Lamp status={noInv.status} />No inventory export — recorded by {noInv.verified_by || '?'}: &quot;{String((noInv.verified_numbers as Record<string, unknown> | null)?.reason || '')}&quot;</div>}
          {have('inventory').map(i => <div key={i.instance_key} style={{ fontSize: 12, marginTop: 6 }}><Lamp status={i.status} />{i.label} · step {i.step} <button style={{ ...ghost, padding: '2px 8px', fontSize: 12 }} onClick={() => { setInstanceKey(i.instance_key); setStep(i.status === 'verified' ? '2.6' : i.step === '2.0' ? '2.1' : i.step) }}>open</button></div>)}
        </div>
        {/* Commission — stage 3 */}
        <div style={{ ...card, background: 'var(--bg,transparent)', marginBottom: 10 }}>
          <div style={{ fontWeight: 700, fontSize: 13 }}>Commission statement (one per carrier)</div>
          <div style={note}>Taken in under Stage 3 in the rail — one statement per carrier and statement type.</div>
        </div>
        {/* Other */}
        <div style={{ ...card, background: 'var(--bg,transparent)' }}>
          <div style={{ fontWeight: 700, fontSize: 13 }}>Other reports: bill payments, credit card / merchant payments, X-reports, anything else</div>
          <div style={note}>Have you uploaded any other report? Name it. If the platform has no table for it yet, it is recorded as received — with its columns and row count, and the file is kept — never dropped and never faked into a table.</div>
          <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center' }}>
            <input value={otherName} onChange={e => setOtherName(e.target.value)} placeholder="e.g. bill payments, card payments, X report" style={{ ...inp, width: 300 }} />
            <button style={btn} disabled={busy || !otherName.trim()} onClick={() => addInstance('other', posRef.trim(), '', otherName.trim())}>Add</button>
          </div>
          {have('other').map(i => <div key={i.instance_key} style={{ fontSize: 12, marginTop: 6 }}><Lamp status={i.status} />{i.label} · step {i.step} <button style={{ ...ghost, padding: '2px 8px', fontSize: 12 }} onClick={() => { setInstanceKey(i.instance_key); setStep(i.step === '2.0' ? '2.1' : i.step) }}>open</button></div>)}
        </div>
      </div>
    )
  }

  const title = `${KIND_LABEL[kind] || kind} — ${kind === 'other' ? otherNameOf : sourceRef}${layout ? ` · ${layout.replace(/_/g, ' ')}` : ''}`
  return (
    <div>
      <div style={{ ...note, marginBottom: 12 }}><b>{title}</b>{filename ? ` · ${filename}` : ''}{kept?.stored && !file ? <span style={{ color: '#15803d' }}> · file kept — no need to drop it again</span> : !file && !kept?.stored && a ? <span style={{ color: '#b45309' }}> · file not kept — drop it again in 2.1 to re-check or commit</span> : null}
        <button style={{ ...ghost, padding: '2px 8px', fontSize: 12, marginLeft: 8 }} onClick={() => setStep('2.0')}>← checklist</button></div>

      {step === '2.1' && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.1 — Upload the export</h2>
          <p style={{ ...note, marginBottom: 14 }}>Drop it exactly as your system produces it. We read every sheet, find the header row under any title block, and find the total row by its shape.</p>
          <Dropzone file={file} filename={filename} onFiles={onFiles} dragOver={dragOver} setDragOver={setDragOver} hint="Drop the export here" />
          <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
            <button style={primary} disabled={busy || (!file && !canUseKept)} onClick={() => analyze().then(r => r && setStep('2.2'))}>{busy ? 'Reading…' : 'Read the file →'}</button>
          </div>
        </div>
      )}

      {step === '2.2' && a && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.2 — Sheet, header row, footer</h2>
          <p style={{ ...note, marginBottom: 12 }}>What we found in the file. Override anything that is wrong and re-read.</p>
          <DetectPanel d={a.detect} sheet={sheet} setSheet={setSheet} headerRow={headerRow} setHeaderRow={setHeaderRow} footerMode={footerMode} setFooterMode={setFooterMode} />
          <div style={{ display: 'flex', gap: 8 }}>
            <button style={ghost} onClick={() => setStep('2.1')}>← Back</button>
            <button style={btn} disabled={busy || (!file && !canUseKept)} onClick={() => analyze({ keepStep: true })}>Re-read with these settings</button>
            <button style={primary} onClick={() => go(kind === 'other' ? '2.5' : '2.3')}>{kind === 'other' ? 'What we can record →' : 'Columns →'}</button>
          </div>
        </div>
      )}

      {step === '2.3' && a && kind !== 'other' && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.3 — Confirm the columns</h2>
          <p style={{ ...note, marginBottom: 12 }}>Each platform field, the column we propose, where that proposal came from, and three values from that column. {isSales ? 'Store, date and amount are needed: they decide which slice of the table this file owns and which month each row books to.' : 'Store and SKU are needed; a unit without an IMEI yet is reported, not landed.'}</p>
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
            <button style={primary} disabled={!canLeave23} title={canLeave23 ? '' : `Map ${(required[kind] || []).join(', ')} first`}
              onClick={() => { persist('2.4', { column_map: columnMap }); analyze({ keepStep: true }).finally(() => setStep('2.4')) }}>Stores and reps →</button>
          </div>
        </div>
      )}

      {step === '2.4' && a && kind !== 'other' && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.4 — Stores and reps</h2>
          <p style={{ ...note, marginBottom: 12 }}>Every store string in the file, resolved against your stores by the same rules every report uses (an alias you confirmed, the address, the store code). A string we do not know must be assigned, created, or marked not yours with a reason — nothing lands as &quot;Default&quot;.</p>
          <StoreResolver stores={a.stores} reps={a.reps} storeList={state?.stores || []} employees={state?.employees || []} decisions={decisions} setDecisions={setDecisions} />
          <div style={{ display: 'flex', gap: 8, marginTop: 14, alignItems: 'center' }}>
            <button style={ghost} onClick={() => setStep('2.3')}>← Back</button>
            <button style={btn} disabled={busy || (!file && !canUseKept)} onClick={() => analyze({ keepStep: true, dec: decisions })}>Re-check with these decisions</button>
            <button style={primary} disabled={busy || unresolved.length > 0} title={unresolved.length ? `${unresolved.length} store string(s) unresolved` : ''}
              onClick={() => { persist('2.5', { identity: decisions }); analyze({ keepStep: true, dec: decisions }).finally(() => setStep('2.5')) }}>Our numbers →</button>
            {unresolved.length > 0 && <span style={{ ...note, color: '#ef4444' }}>{unresolved.length} unresolved: {unresolved.slice(0, 4).join(', ')}{unresolved.length > 4 ? ' …' : ''} — re-check after deciding</span>}
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
            {kind !== 'other' && <button style={ghost} onClick={() => setStep('2.3')}>Fix the columns</button>}
            <button style={primary} disabled={(kind !== 'other' && (a.verify.refusals.length > 0 && !attest.trim())) || (needsAttest && !attest.trim())} onClick={() => go('2.6', { typed_total: typedTotal, as_of_date: asOf || null })}>Confirm →</button>
          </div>
        </div>
      )}

      {step === '2.6' && (a || inst?.status === 'verified' || inst?.status === 'needs_input') && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.6 — Confirm this export</h2>
          {!commitRes && <>
            <p style={{ ...note, marginBottom: 12 }}>{kind === 'other'
              ? 'Confirming records this report as received (columns, row count, money columns, the kept file). It gets a destination when the owner names one.'
              : `Confirming saves the column map for this layout, saves your store and rep decisions (as aliases your reports resolve through), imports the rows through the platform's importer — replacing only the slice this file owns — and then re-reads what landed. The numbers below come back from the table, not from this screen.`}</p>
            {a && kind !== 'other' && tie && <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>Confirm {title} — {isSales ? `${n?.date_span?.from} → ${n?.date_span?.to}` : `as of ${asOf}`} — {money(tie.our_total)}</div>}
            {inst?.verified_numbers && !a && <div style={{ ...note, marginBottom: 12 }}>Last confirmed by {inst.verified_by || '?'} · {inst.verified_at || ''} · status {inst.status}{inst.blocking_reason ? ` · ${inst.blocking_reason}` : ''}. Re-read the file (2.1) to confirm again.</div>}
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={ghost} onClick={() => setStep('2.5')}>← Back</button>
              <button style={primary} disabled={busy || !a || (!file && !canUseKept)} onClick={commit}>{busy ? 'Saving…' : kind === 'other' ? 'Record as received' : 'Confirm and import'}</button>
            </div>
          </>}
          {commitRes && (
            <div>
              <div style={{ ...card, borderColor: commitRes.ok ? '#16a34a' : commitRes.recorded ? '#f59e0b' : '#ef4444', background: commitRes.ok ? 'rgba(22,163,74,.06)' : commitRes.recorded ? 'rgba(245,158,11,.08)' : 'rgba(239,68,68,.06)', marginBottom: 12 }}>
                <div style={{ fontWeight: 700, fontSize: 15 }}>{commitRes.ok ? 'Saved and verified' : commitRes.recorded ? 'Recorded as received — no destination yet' : 'Saved, but NOT verified'} — {title}</div>
                {!commitRes.ok && <ul style={{ margin: '6px 0 0 18px', color: commitRes.recorded ? '#b45309' : '#ef4444', fontSize: 13 }}>{commitRes.problems.map((p2, i) => <li key={i}>{p2}</li>)}</ul>}
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
              <div style={{ display: 'flex', gap: 8 }}>
                <button style={primary} onClick={() => { setInstanceKey(''); setStep('2.0') }}>Another export?</button>
                {isSales && <a href="/commcalc/sales-report" style={{ ...ghost, textDecoration: 'none', display: 'inline-block' }}>Open the Sales report</a>}
                {!commitRes.ok && !commitRes.recorded && <button style={ghost} onClick={() => { setCommitRes(null); setStep('2.5') }}>Back to the numbers</button>}
              </div>
            </div>
          )}
        </div>
      )}

      {KEYS.includes(step) && step !== '2.1' && step !== '2.0' && !a && !(step === '2.6' && inst && (inst.status === 'verified' || inst.status === 'needs_input')) && (
        <div style={card}>
          <div style={note}>Nothing to show for this step yet — read the file at 2.1 first{kept?.stored ? ' (it was kept; no need to drop it again)' : ''}.</div>
          <button style={{ ...primary, marginTop: 10 }} onClick={() => setStep('2.1')}>Go to 2.1</button>
        </div>
      )}
    </div>
  )
}
