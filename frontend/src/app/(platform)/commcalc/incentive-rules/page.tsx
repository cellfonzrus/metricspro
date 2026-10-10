'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, fmt } from '@/lib/client'

// DAILY INCENTIVE RULES (mig 1071). Owner 2026-10-10: "when you add a category it should be
// available in teh cincentive payout plan to be assesed to be paid out … i dont wnat them to be hard
// coded it should be a design effect".
//
// So this screen declares NO category and NO rule. The categories come from
// GET /commcalc/incentive-rules -> metrics (the closing metric catalog, the one home), and the rule
// shapes, split bases, scopes and comparisons come from the same payload. A list hard-coded here
// would be the second copy of the vocabulary that harness_metric_catalog_lock.py fails the build on.

const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '8px', borderTop: '1px solid var(--border)', fontSize: 13, verticalAlign: 'top' }

type Metric = {
  key: string; label: string; value_kind: string; declared_by: string
  unit_price: number; in_revenue: boolean; source: string
}
type Qualifier = { scope?: string; basis?: string; metric_keys?: string[]; op?: string; threshold?: number }
type Payout = { basis?: string; metric_keys?: string[]; rate?: number; split?: string; cap?: number | null }
type Rule = {
  id?: string; rule_key?: string; label: string; description?: string | null; rule_kind: string
  qualifier: Qualifier; payout: Payout; is_active?: boolean; sort_order?: number
}
type Resp = {
  rules?: Rule[]; metrics?: Metric[]; rule_kinds?: string[]; split_bases?: string[]
  qualifier_scopes?: string[]; measure_bases?: string[]; ops?: string[]; can_edit?: boolean
}

// Display words for the server's vocabulary. A label is not the vocabulary — an unknown key still
// renders (as itself), so adding a shape on the server never needs a change here.
const KIND_LABEL: Record<string, string> = {
  per_unit: 'per unit',
  percent_of_amount: 'percent of amount',
  threshold_pool: 'threshold pool',
}
const KIND_HELP: Record<string, string> = {
  per_unit: 'Pays a flat amount for each one the employee personally did.',
  percent_of_amount: "Pays a percentage of the employee's own sales in these categories.",
  threshold_pool: 'Makes a pool out of the store’s figure and divides it among the employees who qualify.',
}
const SPLIT_LABEL: Record<string, string> = {
  individual_sales: 'by who ran the sale',
  hours: 'by hours worked',
  equal: 'evenly between everyone',
}
// What each choice actually does, in the words somebody configuring it would use. Owner 2026-10-10:
// "we will change the logic by who ran the sale … make it easy to confgure".
const SPLIT_HELP: Record<string, string> = {
  individual_sales: 'Each person gets the share of the pool that matches how much of it they sold. Somebody who sold none of it gets none of it.',
  hours: 'Divided by hours worked — clocked hours where they exist, otherwise the scheduled ones. A day with no hours recorded leaves the pool unpaid rather than split evenly.',
  equal: 'The same amount to everyone who qualified, however much each of them sold.',
}
// The order they are offered in. "Who ran the sale" leads because it is the house default; anything
// the server adds that is not named here still appears, after these.
const SPLIT_ORDER = ['individual_sales', 'hours', 'equal']
const SCOPE_LABEL: Record<string, string> = {
  none: 'No qualifier — always pays',
  store: 'The store must reach it',
  employee: 'The employee must reach it',
}
const BASIS_LABEL: Record<string, string> = {
  revenue: "the day's gross revenue",
  units: 'units',
  amount: 'dollars',
}
const OP_LABEL: Record<string, string> = {
  gte: 'at least', gt: 'more than', lte: 'at most', lt: 'less than', eq: 'exactly',
}
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

const blankRule = (kind: string): Rule => ({
  label: '', description: '', rule_kind: kind, is_active: true,
  qualifier: { scope: 'none' },
  // A new pool defaults to "who ran the sale" — the owner's rule, so the common case needs no
  // thinking and the other two are a dropdown away.
  payout: { basis: kind === 'per_unit' ? 'units' : 'amount', metric_keys: [], rate: 0, ...(kind === 'threshold_pool' ? { split: 'individual_sales' } : {}) },
})

// The server's split list, house order first. Nothing is filtered out — a basis this screen has no
// word for still appears, named by its key, rather than silently vanishing from the editor.
function orderedSplits(bases: string[]): string[] {
  return [...SPLIT_ORDER.filter(s => bases.includes(s)), ...bases.filter(s => !SPLIT_ORDER.includes(s))]
}

// The rule, as a sentence. Built from the rule itself and the catalog's own labels — so a category
// renamed on Count Fields renames itself here too, and a shape the server adds degrades to its key
// rather than to a wrong sentence.
function plainWords(r: Rule, metrics: Metric[]): string {
  const name = (k: string) => metrics.find(m => m.key === k)?.label || k
  const names = (ks?: string[]) => (ks || []).map(name).join(' and ')
  const pct = (n?: number) => `${((n || 0) * 100).toFixed(2).replace(/\.?0+$/, '')}%`
  const q = r.qualifier || {}, p = r.payout || {}

  let when = 'Every day, '
  if (q.scope === 'store' || q.scope === 'employee') {
    const who = q.scope === 'store' ? 'the store' : 'an employee'
    const what = (q.metric_keys || []).length ? names(q.metric_keys)
      : (q.basis === 'revenue' ? 'the day’s sales' : 'the figure')
    const amount = q.basis === 'units' ? `${q.threshold ?? 0}` : fmt(q.threshold || 0)
    when = `When ${who} reaches ${OP_LABEL[q.op || 'gte'] || q.op} ${amount} of ${what}, `
  }

  const on = (p.metric_keys || []).length ? names(p.metric_keys) : 'the day’s sales'
  if (r.rule_kind === 'per_unit') {
    return `${when}each person is paid ${fmt(p.rate || 0)} for every ${on} they did themselves.`
  }
  if (r.rule_kind === 'percent_of_amount') {
    return `${when}each person is paid ${pct(p.rate)} of their own ${on}.`
  }
  if (r.rule_kind === 'threshold_pool') {
    const split = SPLIT_LABEL[p.split || 'individual_sales'] || p.split
    return `${when}${pct(p.rate)} of ${on} becomes a pool, shared out ${split}.`
  }
  return `${when}this rule pays on ${on}.`
}

function MetricPicker({ metrics, value, onChange, hint }:
  { metrics: Metric[]; value: string[]; onChange: (v: string[]) => void; hint: string }) {
  if (!metrics.length) {
    return (
      <div style={{ fontSize: 12, color: 'var(--text3)' }}>
        No categories declared yet —{' '}
        <Link href="/closing/count-config" style={{ textDecoration: 'underline' }}>add them on Count Fields</Link>.
      </div>
    )
  }
  const toggle = (k: string) => onChange(value.includes(k) ? value.filter(x => x !== k) : [...value, k])
  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {metrics.map(m => (
          <label key={m.key} style={{
            fontSize: 12, padding: '2px 7px', borderRadius: 11, cursor: 'pointer',
            border: '1px solid var(--border)',
            background: value.includes(m.key) ? 'var(--surface2)' : 'transparent',
            opacity: value.includes(m.key) ? 1 : 0.7,
          }}>
            <input type="checkbox" checked={value.includes(m.key)} onChange={() => toggle(m.key)}
              style={{ marginRight: 5 }} />
            {m.label}
          </label>
        ))}
      </div>
      <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 3 }}>{hint}</div>
    </div>
  )
}

export default function IncentiveRulesPage() {
  const [d, setD] = useState<Resp | null>(null)
  const [edit, setEdit] = useState<Rule | null>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api('/api/v1/commcalc/incentive-rules?include_inactive=1')
      .then((r: Resp | null) => setD(r))
      .catch((e: unknown) => setMsg('❌ ' + errText(e)))
  }, [])
  useEffect(() => { load() }, [load])

  const metrics = (d?.metrics || []).filter(m => m.source === 'count_field')
  const countMetrics = metrics.filter(m => m.value_kind !== 'amount')
  const moneyMetrics = metrics.filter(m => m.value_kind !== 'count' || m.unit_price > 0)
  const kinds = d?.rule_kinds || []
  const canEdit = d?.can_edit !== false

  async function save(r: Rule) {
    setBusy(true)
    try {
      await api('/api/v1/commcalc/incentive-rules', { method: 'PUT', body: JSON.stringify(r) })
      setMsg('✅ Saved.'); setEdit(null); load()
    } catch (e: unknown) { setMsg('❌ ' + errText(e)) }
    setBusy(false)
  }
  async function switchOff(r: Rule) {
    if (!r.id) return
    setBusy(true)
    try { await api(`/api/v1/commcalc/incentive-rules/${r.id}`, { method: 'DELETE' }); load(); setMsg('✅ Switched off.') }
    catch (e: unknown) { setMsg('❌ ' + errText(e)) }
    setBusy(false)
  }

  function describeQualifier(q: Qualifier) {
    const scope = q.scope || 'none'
    if (scope === 'none') return '—'
    const names = (q.metric_keys || []).map(k => metrics.find(m => m.key === k)?.label || k)
    const what = names.length ? names.join(' + ') : BASIS_LABEL[q.basis || 'revenue']
    const who = scope === 'store' ? 'Store' : 'Employee'
    const amount = (q.basis === 'units') ? `${q.threshold ?? 0}` : fmt(q.threshold || 0)
    return `${who} ${what} ${OP_LABEL[q.op || 'gte'] || q.op} ${amount}`
  }
  function describePayout(r: Rule) {
    const p = r.payout || {}
    const names = (p.metric_keys || []).map(k => metrics.find(m => m.key === k)?.label || k)
    if (r.rule_kind === 'per_unit') return `${fmt(p.rate || 0)} each`
    if (r.rule_kind === 'percent_of_amount') return `${((p.rate || 0) * 100).toFixed(2).replace(/\.?0+$/, '')}% of ${names.join(' + ') || 'sales'}`
    return `${((p.rate || 0) * 100).toFixed(2).replace(/\.?0+$/, '')}% pool · ${SPLIT_LABEL[p.split || 'equal'] || p.split}`
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🎯 Daily Incentive Rules</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 820 }}>
            What a day&apos;s declared categories pay each employee. Every rule here is a setting, not code —
            the categories it can point at are the ones you declared on{' '}
            <Link href="/closing/count-config" style={{ textDecoration: 'underline' }}>Daily Closing → Count Fields</Link>,
            so adding a category is all it takes to be able to pay on it.
          </p>
        </div>
        <Link href="/commcalc" className="btn btn-secondary" style={{ fontSize: 13 }}>← Commissions</Link>
      </div>

      {!metrics.length && (
        <div className="card" style={{ marginTop: 12, fontSize: 13 }}>
          This business has not declared any daily categories yet, so there is nothing to pay on.
          Start on <Link href="/closing/count-config" style={{ textDecoration: 'underline' }}>Count Fields</Link>:
          add what the store counts or sells each day, say whether each employee declares their own,
          and come back here.
        </div>
      )}

      {msg && <div style={{ fontSize: 13, margin: '10px 0' }}>{msg}</div>}

      <div className="card table-wrapper" style={{ marginTop: 16, padding: 0 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Rule', 'Type', 'Qualifier', 'Payout', 'Active', ''].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {(d?.rules || []).map(r => (
              <tr key={r.id || r.rule_key} style={{ opacity: r.is_active === false ? 0.5 : 1 }}>
                <td style={cell}>
                  <div style={{ fontWeight: 600 }}>{r.label}</div>
                  {r.description && <div style={{ fontSize: 11, color: 'var(--text3)', maxWidth: 300 }}>{r.description}</div>}
                </td>
                <td style={cell}>
                  <span style={{ fontSize: 11, padding: '2px 7px', borderRadius: 11, background: 'var(--surface2)' }}>
                    {KIND_LABEL[r.rule_kind] || r.rule_kind}
                  </span>
                </td>
                <td style={cell}>{describeQualifier(r.qualifier || {})}</td>
                <td style={{ ...cell, fontWeight: 600 }}>{describePayout(r)}</td>
                <td style={cell}>{r.is_active === false ? 'Off' : 'Active'}</td>
                <td style={cell}>
                  {canEdit && <>
                    <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setEdit(r)}>Edit</button>
                    {r.is_active !== false &&
                      <button className="btn btn-secondary" style={{ fontSize: 12, marginLeft: 4 }}
                        disabled={busy} onClick={() => switchOff(r)}>Switch off</button>}
                  </>}
                </td>
              </tr>
            ))}
            {!(d?.rules || []).length && (
              <tr><td style={{ ...cell, color: 'var(--text3)' }} colSpan={6}>No rules yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {canEdit && !!metrics.length && (
        <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {kinds.map(k => (
            <button key={k} className="btn btn-secondary" style={{ fontSize: 13 }}
              title={KIND_HELP[k]} onClick={() => setEdit(blankRule(k))}>
              ＋ New {KIND_LABEL[k] || k} rule
            </button>
          ))}
        </div>
      )}

      {edit && (
        <div className="card" style={{ marginTop: 16 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 2px' }}>
            {edit.id ? 'Edit rule' : `New ${KIND_LABEL[edit.rule_kind] || edit.rule_kind} rule`}
          </h2>
          <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>{KIND_HELP[edit.rule_kind]}</p>
          {/* The rule read back as a sentence while it is being built, so nobody has to hold the
              four dropdowns in their head to know what they just configured. */}
          <div style={{
            fontSize: 13, background: 'var(--surface2)', borderRadius: 8, padding: '8px 11px',
            margin: '0 0 14px', lineHeight: 1.5,
          }}>
            <strong style={{ fontSize: 11, color: 'var(--text2)' }}>IN PLAIN WORDS</strong>
            <div style={{ marginTop: 3 }}>{plainWords(edit, metrics)}</div>
          </div>

          <div style={{ display: 'grid', gap: 12, maxWidth: 720 }}>
            <label style={{ fontSize: 12 }}>Name
              <input style={{ ...sel, width: '100%', marginTop: 3 }} value={edit.label}
                placeholder="e.g. Overnight Shipments"
                onChange={e => setEdit({ ...edit, label: e.target.value })} />
            </label>
            <label style={{ fontSize: 12 }}>What it is, in plain words (shown on this screen)
              <input style={{ ...sel, width: '100%', marginTop: 3 }} value={edit.description || ''}
                onChange={e => setEdit({ ...edit, description: e.target.value })} />
            </label>

            <fieldset style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12 }}>
              <legend style={{ fontSize: 12, padding: '0 6px' }}>Qualifier — what has to happen before it pays</legend>
              <select style={sel} value={edit.qualifier?.scope || 'none'}
                onChange={e => setEdit({ ...edit, qualifier: { ...edit.qualifier, scope: e.target.value } })}>
                {(d?.qualifier_scopes || ['none']).map(v => <option key={v} value={v}>{SCOPE_LABEL[v] || v}</option>)}
              </select>
              {edit.qualifier?.scope && edit.qualifier.scope !== 'none' && (
                <div style={{ marginTop: 10, display: 'grid', gap: 8 }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <select style={sel} value={edit.qualifier?.basis || 'revenue'}
                      onChange={e => setEdit({ ...edit, qualifier: { ...edit.qualifier, basis: e.target.value } })}>
                      {(d?.measure_bases || []).map(v => <option key={v} value={v}>{BASIS_LABEL[v] || v}</option>)}
                    </select>
                    <select style={sel} value={edit.qualifier?.op || 'gte'}
                      onChange={e => setEdit({ ...edit, qualifier: { ...edit.qualifier, op: e.target.value } })}>
                      {(d?.ops || []).map(v => <option key={v} value={v}>{OP_LABEL[v] || v}</option>)}
                    </select>
                    <input style={{ ...sel, width: 110 }} inputMode="decimal"
                      value={edit.qualifier?.threshold ?? ''}
                      onChange={e => setEdit({ ...edit, qualifier: { ...edit.qualifier, threshold: Number(e.target.value) } })} />
                  </div>
                  {edit.qualifier?.basis !== 'revenue' && (
                    <MetricPicker metrics={edit.qualifier?.basis === 'units' ? countMetrics : moneyMetrics}
                      value={edit.qualifier?.metric_keys || []}
                      onChange={v => setEdit({ ...edit, qualifier: { ...edit.qualifier, metric_keys: v } })}
                      hint="Tick several to require them COMBINED — that is how “overnight and express together must reach 5” is said." />
                  )}
                  {edit.qualifier?.basis === 'revenue' && !(edit.qualifier?.metric_keys || []).length && (
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                      Measured against the whole day&apos;s gross revenue — every category you marked as a sale.
                    </div>
                  )}
                </div>
              )}
            </fieldset>

            <fieldset style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12 }}>
              <legend style={{ fontSize: 12, padding: '0 6px' }}>Payout — what it pays</legend>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12 }}>
                  {edit.rule_kind === 'per_unit' ? 'Dollars per unit' : 'Percent'}
                </span>
                <input style={{ ...sel, width: 110 }} inputMode="decimal"
                  value={edit.rule_kind === 'per_unit'
                    ? (edit.payout?.rate ?? '')
                    : ((edit.payout?.rate ?? 0) * 100 || '')}
                  onChange={e => {
                    const n = Number(e.target.value)
                    setEdit({ ...edit, payout: { ...edit.payout, rate: edit.rule_kind === 'per_unit' ? n : n / 100 } })
                  }} />
                {edit.rule_kind !== 'per_unit' && <span style={{ fontSize: 12 }}>%</span>}
                {edit.rule_kind === 'threshold_pool' && (
                  <select style={sel} value={edit.payout?.split || 'individual_sales'}
                    onChange={e => setEdit({ ...edit, payout: { ...edit.payout, split: e.target.value } })}>
                    {orderedSplits(d?.split_bases || []).map(v =>
                      <option key={v} value={v}>Split {SPLIT_LABEL[v] || v}</option>)}
                  </select>
                )}
              </div>
              {edit.rule_kind === 'threshold_pool' && (
                <div style={{ fontSize: 11.5, color: 'var(--text2)', marginTop: 6 }}>
                  {SPLIT_HELP[edit.payout?.split || 'individual_sales'] || ''}
                </div>
              )}
              <div style={{ marginTop: 10 }}>
                {edit.rule_kind === 'threshold_pool' && !(edit.payout?.metric_keys || []).length ? (
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                    The pool is a percentage of the whole day&apos;s gross revenue. Tick categories below to
                    base it on just those instead.
                  </div>
                ) : null}
                <MetricPicker metrics={edit.rule_kind === 'per_unit' ? countMetrics : moneyMetrics}
                  value={edit.payout?.metric_keys || []}
                  onChange={v => setEdit({ ...edit, payout: { ...edit.payout, metric_keys: v } })}
                  hint={edit.rule_kind === 'threshold_pool'
                    ? "Leave all unticked to pool a percentage of the whole day."
                    : "Each employee is paid on their own figure in these categories."} />
              </div>
              {edit.rule_kind === 'threshold_pool' && edit.payout?.split === 'individual_sales'
                && !!(edit.payout?.metric_keys || []).length && (
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>
                  Shares are worked out from the categories ticked above — the ones that made the
                  pool — not from everything a person sold that day.
                </div>
              )}
            </fieldset>

            <label style={{ fontSize: 12 }}>
              <input type="checkbox" checked={edit.is_active !== false} style={{ marginRight: 6 }}
                onChange={e => setEdit({ ...edit, is_active: e.target.checked })} />
              Active
            </label>
          </div>

          <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
            <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={busy || !edit.label}
              onClick={() => save(edit)}>💾 Save rule</button>
            <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={() => setEdit(null)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  )
}
