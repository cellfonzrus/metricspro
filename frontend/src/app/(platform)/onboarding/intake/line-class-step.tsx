'use client'
// ═══════════════════════════════════════════════════════════════════════════════════════════════
// TENANT ONBOARDING — Stage 2, step 2.5a: "What counts as an activation" (owner 2026-09-21: "sales
// report shows 88 txns but not a break up in to activations and upgrade etc, also nothing on exec mtd").
//
// A sales export does not always carry a contract-type column: the activation type may sit in the
// category path or the product name. This step shows, per class (new activation / upgrade / bring-your-
// own-device / port-in / hardware only), the words the platform found in THIS file, the column they
// live in and how many lines each would classify — computed by the very predicate every report and
// every commission calculation uses — and the same for the Executive MTD's line buckets (phones, bill
// payments …). The person accepts or edits, and the save writes the two existing config homes through
// the backend's /onboarding/intake/line-class; the landed rows are re-counted and the export is verified
// only when a rule classifies ≥1 activation-type line — or the person attests the file has none.
//
// This component classifies nothing and stores nothing of its own. No carrier, POS vendor or tenant
// is named here (RULE TWO).
// ═══════════════════════════════════════════════════════════════════════════════════════════════
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { BASE, card, note, inp, btn, primary, ghost, mono, num } from './intake-shared'

type ClassKey = 'activation' | 'upgrade' | 'byod' | 'port' | 'hardware_only'
type Counts = { scanned: number; skipped: number; unclassified_lines: number; activation_type_lines: number; activation_type_transactions: number
  fields: string[]; source: string; classes: Record<string, { lines: number; transactions: number; label: string }> }
type Candidate = { field: string; token: string; lines: number; ratio: number; samples: string[] }
type MetricBucket = { bucket: string; matched: number; source: string; applicable: boolean; rules: Record<string, string[]>
  candidates: Candidate[]; proposal: Record<string, string[]> | null; preview: number | null }
export type LineClassBlock = {
  step: string; classes: { key: ClassKey; label: string }[]; candidate_fields: string[]
  rules: { fields: string[]; tokens: Record<ClassKey, string[]>; exact: Record<string, string>; source: string; declared: Record<string, boolean> }
  current: Counts; gate_open: boolean; gate_note: string | null
  suggest: { scanned: number; distinct: Record<string, number>; per_class: Record<ClassKey, Candidate[]>
    too_broad: (Candidate & { class: string })[]; proposal: { fields: string[]; tokens: Record<ClassKey, string[]> }; preview: Counts }
  metrics: { scanned: number; buckets: Record<string, MetricBucket>; error?: string }
  error?: string
}
type Get = { instance_key: string; landed: boolean; rows: number; block: LineClassBlock | null; recorded?: Counts | null; status?: string; blocking_reason?: string | null; note?: string }
type Put = { ok: boolean; written: { activation_rules: boolean; metric_buckets: string[] }; landed?: boolean; rows?: number; block?: LineClassBlock | null
  verified?: boolean; activation_gate?: { blocked: boolean; open: boolean; attested: { reason: string; by: string | null } | null; reason: string }; state?: { saved: boolean; reason?: string } }

const CLASS_HELP: Record<ClassKey, string> = {
  activation: 'a brand-new line of service (new number, or a number ported in)',
  upgrade: 'an existing line getting a new device',
  byod: 'a new line on a device the customer brought (no handset sold)',
  port: 'a new line whose number was moved in — a sub-type of new activation',
  hardware_only: 'a device or prepaid unit sold with NO line of service — never counted as an activation',
}
const FIELD_LABEL: Record<string, string> = { contract_type: 'contract type', category: 'category (path)', department: 'department', product_desc: 'product name', trans_type: 'transaction type' }
const csv = (a: string[] | undefined) => (a || []).join(', ')
const parse = (s: string) => s.split(',').map(x => x.trim().toLowerCase()).filter(Boolean)

export function LineClassStep({ instanceKey, who, analysis, canReanalyze, reanalyze, onBack, onNext, saveUi, flash }: {
  instanceKey: string; who: string; analysis: LineClassBlock | null; canReanalyze: boolean; reanalyze: () => Promise<unknown>
  onBack: () => void; onNext: () => void; saveUi: React.ReactNode; flash: (m: string) => void
}) {
  const [got, setGot] = useState<Get | null>(null)
  const [loading, setLoading] = useState(!!instanceKey)   // the landed-slice read starts with the step
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<Put | null>(null)
  const [fields, setFields] = useState<string[]>([])
  const [tokens, setTokens] = useState<Record<string, string>>({})
  const [useMetric, setUseMetric] = useState<Record<string, boolean>>({})
  const [noAct, setNoAct] = useState('')
  const [seeded, setSeeded] = useState('')

  // the block: the LANDED slice when the export has landed (what the reports count), else the analysis.
  // The read is subscribed in an effect (state set only in the response callbacks); a save bumps `tick`.
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!instanceKey) return
    let alive = true
    api(`${BASE}/line-class?instance_key=${encodeURIComponent(instanceKey)}`)
      .then((g: Get) => { if (alive) setGot(g) })
      .catch(() => { if (alive) setGot(null) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [instanceKey, tick])
  const load = useCallback(async () => { setTick(t => t + 1) }, [])
  const block: LineClassBlock | null = (got?.landed && got.block) ? got.block : analysis
  const landed = !!got?.landed

  // seed the editable rules once per block (the "adjust state when a prop changes" pattern): the proposal
  // when nothing classifies, else the rules in force
  const seedKey = block && !block.error ? `${instanceKey}|${landed}|${block.gate_open}` : ''
  if (block && !block.error && seeded !== seedKey) {
    setSeeded(seedKey)
    const src = block.gate_open ? block.suggest.proposal : block.rules
    setFields([...(src.fields || [])])
    const t: Record<string, string> = {}
    for (const c of block.classes) t[c.key] = csv(src.tokens?.[c.key])
    setTokens(t)
    const um: Record<string, boolean> = {}
    for (const [b, m] of Object.entries(block.metrics?.buckets || {})) um[b] = !!m.proposal
    setUseMetric(um)
    setRes(null)
  }

  const preview = useMemo(() => {
    if (!block || block.error) return null
    return block.gate_open ? block.suggest.preview : block.current
  }, [block])
  const anyActivation = (c: Counts | null) => !!c && c.activation_type_lines > 0

  const save = useCallback(async () => {
    if (!block) return
    setBusy(true)
    try {
      const metric_rules: Record<string, Record<string, string[]>> = {}
      for (const [b, m] of Object.entries(block.metrics?.buckets || {})) if (useMetric[b] && m.proposal) metric_rules[b] = m.proposal
      const body = {
        instance_key: instanceKey, by: who,
        fields, tokens: Object.fromEntries(Object.entries(tokens).map(([k, v]) => [k, parse(v)])),
        metric_rules: Object.keys(metric_rules).length ? metric_rules : null,
        no_activations: noAct.trim() || null,
      }
      const r: Put = await api(`${BASE}/line-class`, { method: 'PUT', body: JSON.stringify(body) })
      setRes(r)
      if (r.landed) await load()
      else if (canReanalyze) await reanalyze()
      flash(r.landed
        ? (r.verified ? 'Saved — the landed rows were re-counted and this export is verified.' : 'Saved — the landed rows were re-counted; still not verified (see below).')
        : 'Saved — the rules apply to every report from now on; re-read the file to see the counts.')
    } catch (e: unknown) { flash((e as Error)?.message || 'Could not save') }
    finally { setBusy(false) }
  }, [block, instanceKey, who, fields, tokens, useMetric, noAct, canReanalyze, reanalyze, load, flash])

  if (!block) {
    return (
      <div style={card}>
        <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.5a — What counts as an activation</h2>
        <p style={note}>{loading ? 'Reading the landed rows…' : (got?.note || 'Nothing to check yet — read the file at 2.1 first.')}</p>
        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}><button style={ghost} onClick={onBack}>← Back</button></div>
      </div>
    )
  }
  if (block.error) {
    return (
      <div style={card}>
        <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.5a — What counts as an activation</h2>
        <p style={{ ...note, color: '#b45309' }}>{block.error}</p>
        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}><button style={ghost} onClick={onBack}>← Back</button><button style={primary} onClick={onNext}>Continue →</button></div>
      </div>
    )
  }
  const shown = res?.block || block
  const counts = res?.block ? res.block.current : preview
  const gateOpen = res ? !!res.activation_gate?.open : block.gate_open
  const blocked = res ? !!res.activation_gate?.blocked : (block.gate_open && !noAct.trim())
  const basis = landed ? `${num(got?.rows || 0)} rows landed for this export, re-read from the table` : `${num(block.current.scanned)} rows that would land, from the parsed file`

  return (
    <div style={card}>
      <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>2.5a — What counts as an activation</h2>
      <p style={{ ...note, marginBottom: 8 }}>
        Your export does not have to carry a &quot;contract type&quot; column — the activation type may be written in the category or the product name.
        Below is what the platform found in <b>this file</b>: per type, the words that name it, the column they live in and how many lines each would count.
        Accept or edit, then save. Basis: {basis}.
      </p>
      {block.gate_open && (
        <div style={{ ...card, borderColor: '#f59e0b', background: 'rgba(245,158,11,.08)', marginBottom: 10, fontSize: 13 }}>
          <b>With the rules in force, not one line could be told apart as an activation, upgrade, port-in or bring-your-own-device line</b>
          {' '}(columns read: {(block.current.fields || []).map(f => FIELD_LABEL[f] || f).join(', ')}). The Sales Report and Executive MTD would show 0 for every activation column.
          {block.suggest.proposal && anyActivation(block.suggest.preview)
            ? <> The proposal below was built from this file&apos;s own words — it would count <b>{num(block.suggest.preview.activation_type_transactions)}</b> activation-type invoices.</>
            : <> No word in this file matched the activation vocabulary — type the words yourself below, or attest that this file truly has no activations.</>}
        </div>
      )}

      <div style={{ fontSize: 13, fontWeight: 700, margin: '8px 0 4px' }}>Which columns carry the activation type?</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 10 }}>
        {block.candidate_fields.map(f => (
          <label key={f} style={{ fontSize: 13 }}>
            <input type="checkbox" checked={fields.includes(f)} onChange={e => setFields(e.target.checked ? [...fields, f] : fields.filter(x => x !== f))} style={{ marginRight: 5 }} />
            {FIELD_LABEL[f] || f} <span style={note}>({num(block.suggest.distinct?.[f] || 0)} distinct values)</span>
          </label>
        ))}
      </div>

      <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 10 }}>
        <thead><tr style={{ color: 'var(--text2)' }}><th align="left" style={{ width: 210 }}>Type</th><th align="left">Words that mean it (comma-separated; matched anywhere in the chosen columns)</th><th align="right" style={{ width: 130 }}>Would count</th></tr></thead>
        <tbody>
          {block.classes.map(c => {
            const cands = block.suggest.per_class?.[c.key] || []
            const cnt = counts?.classes?.[c.key]
            return (
              <tr key={c.key} style={{ borderTop: '1px solid var(--border)', verticalAlign: 'top' }}>
                <td style={{ padding: '6px 4px' }}><b>{c.label}</b><div style={{ ...note, fontSize: 11 }}>{CLASS_HELP[c.key]}</div></td>
                <td style={{ padding: '6px 4px' }}>
                  <input value={tokens[c.key] ?? ''} onChange={e => setTokens({ ...tokens, [c.key]: e.target.value })} style={{ ...inp, width: '100%' }} placeholder="no words — this type is not counted" />
                  {!!cands.length && <div style={{ ...note, fontSize: 11, marginTop: 3 }}>found in this file: {cands.map(x => `"${x.token}" in ${FIELD_LABEL[x.field] || x.field} (${num(x.lines)} lines, e.g. ${x.samples[0]})`).join(' · ')}</div>}
                </td>
                <td style={{ ...mono, padding: '6px 4px', textAlign: 'right' }}>{cnt ? <>{num(cnt.lines)} lines<br /><span style={note}>{num(cnt.transactions)} invoices</span></> : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {!!block.suggest.too_broad?.length && (
        <div style={{ ...note, fontSize: 12, marginBottom: 8 }}>
          Not proposed (the word appears on nearly every line, so it names a department, not a type): {block.suggest.too_broad.map(b => `"${b.token}" in ${FIELD_LABEL[b.field] || b.field} for ${b.class} (${Math.round(b.ratio * 100)}% of lines)`).join(' · ')}
        </div>
      )}
      <div style={{ ...note, fontSize: 12, marginBottom: 12 }}>
        Counting rule: an invoice counts once per type however many of its lines carry the word; an invoice with both a new-activation line and an upgrade line counts in both.
        The same rules drive the Sales Report, Executive MTD, Daily Targets and every commission calculation — this is the one place they are set.
      </div>

      <div style={{ fontSize: 13, fontWeight: 700, margin: '8px 0 4px' }}>Executive MTD line columns — phones, bill payments, protection, accessories, activation fee</div>
      {block.metrics?.error && <div style={{ ...note, color: '#b45309' }}>{block.metrics.error}</div>}
      <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse', marginBottom: 12 }}>
        <thead><tr style={{ color: 'var(--text2)' }}><th align="left" style={{ width: 150 }}>Column</th><th align="right" style={{ width: 120 }}>Matches now</th><th align="left">Proposal from this file</th></tr></thead>
        <tbody>
          {Object.values(shown.metrics?.buckets || {}).map(m => (
            <tr key={m.bucket} style={{ borderTop: '1px solid var(--border)', verticalAlign: 'top' }}>
              <td style={{ padding: '6px 4px' }}><b>{m.bucket.replace(/_/g, ' ')}</b><div style={{ ...note, fontSize: 11 }}>rule source: {m.source}{!m.applicable ? ' · marked not applicable' : ''}</div></td>
              <td style={{ ...mono, padding: '6px 4px', textAlign: 'right', color: m.matched === 0 && m.applicable ? '#b45309' : 'inherit' }}>{num(m.matched)} lines</td>
              <td style={{ padding: '6px 4px' }}>
                {m.matched > 0 ? <span style={note}>the current rule already matches — nothing to change</span>
                  : !m.applicable ? <span style={note}>not applicable for this business — left as is</span>
                  : m.proposal ? (
                    <label style={{ fontSize: 13 }}>
                      <input type="checkbox" checked={!!useMetric[m.bucket]} onChange={e => setUseMetric({ ...useMetric, [m.bucket]: e.target.checked })} style={{ marginRight: 5 }} />
                      use: {m.candidates.map(x => `"${x.token}" in ${FIELD_LABEL[x.field] || x.field} (${num(x.lines)})`).join(', ')} → would match <b>{num(m.preview || 0)}</b> lines
                    </label>
                  ) : <span style={{ ...note, color: '#b45309' }}>no word in this file matched — this column will read 0 until a rule is set under Executive MTD → Metric definitions</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {gateOpen && (
        <label style={{ fontSize: 13, display: 'block', margin: '10px 0' }}>
          If this file truly has no activations (an accessory-only register, a repair counter), say why — recorded with your name — and the export is verified without an activation rule:
          <textarea value={noAct} onChange={e => setNoAct(e.target.value)} rows={2} style={{ ...inp, width: '100%', marginTop: 4 }} placeholder="Reason this file has no activations" />
        </label>
      )}

      {res && (
        <div style={{ ...card, borderColor: res.verified || !res.landed ? '#16a34a' : '#ef4444', background: res.verified || !res.landed ? 'rgba(22,163,74,.06)' : 'rgba(239,68,68,.06)', marginBottom: 10, fontSize: 13 }}>
          <b>Saved.</b> Activation rules {res.written.activation_rules ? 'written' : 'unchanged'} · metric columns written: {res.written.metric_buckets.length ? res.written.metric_buckets.join(', ') : 'none'}
          {res.landed && res.block && <> · re-counted over {num(res.rows || 0)} landed rows: {res.block.current.activation_type_transactions} activation-type invoices
            ({block.classes.map(c => `${res.block!.current.classes[c.key]?.transactions ?? 0} ${c.label.toLowerCase()}`).join(', ')})</>}
          {res.landed && <div style={{ marginTop: 4, fontWeight: 700, color: res.verified ? '#16a34a' : '#ef4444' }}>{res.verified ? 'This export is verified.' : `Not verified — ${res.activation_gate?.reason}`}</div>}
          {res.state && !res.state.saved && <div style={note}>your place was not saved ({res.state.reason})</div>}
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
        <button style={ghost} onClick={onBack}>← Back</button>
        <button style={primary} disabled={busy} onClick={save}>{busy ? 'Saving…' : landed ? 'Save and re-count the landed rows' : 'Save these rules'}</button>
        <button style={blocked ? btn : primary} onClick={onNext}>{blocked ? 'Continue anyway →' : 'Continue →'}</button>
        {saveUi}
      </div>
      {blocked && <div style={{ ...note, fontSize: 12, marginTop: 6 }}>Continuing without a rule or an attestation lands the rows but leaves this export <b>not verified</b> in Stage 4 until one is recorded here.</div>}
    </div>
  )
}
