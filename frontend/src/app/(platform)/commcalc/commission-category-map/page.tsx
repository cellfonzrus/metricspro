'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { useActiveCarrier } from '@/lib/auth-context'
import { carrierDisplayName } from '@/lib/carrier-scope'

// Commission Category Map (SAP-style) — the per-template rules that classify a carrier's commission labels
// into the five canonical buckets. Rules match on product_name OR order_type (contains/equals), in ascending
// priority (first match wins), and only book a payout when the amount is negative (sign_rule). The
// "Observed labels" table shows the real (order_type, product_name) values in the imported ledger with the
// bucket they CURRENTLY land in, so unmapped ("other") payouts are easy to spot and fix. Backed by
// commcalc.commission_category_map (migration 071); falls back to built-in defaults until 071 is run.

type Rule = { id?: string; source_report: string; match_field: string; match_op: string; pattern: string; category: string; sign_rule: string; priority: number; is_seeded?: boolean; leg_bucket?: string | null }
type Tmpl = { key: string; label: string; builtin: boolean; rule_count: number }
type Obs = { order_type: string; product_name: string; count: number; payout_total: number; category: string; is_payout: boolean; leg_bucket?: string; leg_month?: number | null; leg_why?: string }
// COMMISSION LEG (owner 2026-08-04) — a SECOND, orthogonal dimension over the five buckets: was this
// dollar received in the month the number activated (1st Month), or later for an already-activated
// number (M2–M12)? A rule's leg is OPTIONAL: blank = Derive, which reads the line's own payment month
// ("… MONTH 4", "… M1"). Every pre-existing rule is blank, so nothing is re-bucketed by this.
const LEG_LABEL: Record<string, string> = { m1: '1st Month', trailing: 'M2–M12', unsplit: 'Unsplit' }
const LEG_TINT: Record<string, string> = { m1: '#065f46', trailing: '#1d4ed8', unsplit: '#9a3412' }
const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const money = (n: number) => (n || 0).toLocaleString('en-US', { style: 'currency', currency: 'USD' })

export default function CommissionCategoryMapPage() {
  // Active-carrier lens: name only the active carrier in template copy for a dual-carrier tenant.
  const { activeCarrier, multi } = useActiveCarrier()
  const carrierNames = multi ? carrierDisplayName(activeCarrier) : 'Total / Boost'
  const carrierNames2 = multi ? carrierDisplayName(activeCarrier) : 'Total, Boost,'
  const [tmpls, setTmpls] = useState<Tmpl[]>([])
  const [src, setSrc] = useState('ma_daily_tx')
  const [rules, setRules] = useState<Rule[]>([])
  const [usingDefaults, setUsingDefaults] = useState(false)
  const [ready, setReady] = useState(true)
  const [cats, setCats] = useState<string[]>([])
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [meta, setMeta] = useState<{ match_fields: string[]; match_ops: string[]; sign_rules: string[] }>({ match_fields: ['product_name', 'order_type'], match_ops: ['contains', 'equals'], sign_rules: ['negative_only', 'any'] })
  const [legBuckets, setLegBuckets] = useState<string[]>(['m1', 'trailing', 'unsplit'])
  const [legHelp, setLegHelp] = useState('')
  const [obs, setObs] = useState<Obs[]>([])
  // the MA product-class vocabulary — the PATTERN for a `product_class` rule is a class key,
  // never free text (RULE THREE: pick, don't type). Loaded from the tenant's own vocabulary.
  const [classKeys, setClassKeys] = useState<string[]>([])
  const [msg, setMsg] = useState('')
  const [nr, setNr] = useState<Rule>({ source_report: 'ma_daily_tx', match_field: 'product_name', match_op: 'contains', pattern: '', category: 'commission', sign_rule: 'negative_only', priority: 100, leg_bucket: '' })

  async function loadTemplates() {
    try { const d = await api('/api/v1/commcalc/commission-ledger/templates'); setTmpls(d?.templates || []) } catch { /* noop */ }
  }
  async function loadMap(s: string) {
    try {
      const d = await api('/api/v1/commcalc/commission-category-map?source_report=' + encodeURIComponent(s))
      setRules(d?.rules?.length ? d.rules : (d?.default_rules || []))
      setUsingDefaults(!!d?.using_defaults); setReady(d?.ready !== false)
      setCats(d?.categories || []); setLabels(d?.category_labels || {})
      setMeta({ match_fields: d?.match_fields || meta.match_fields, match_ops: d?.match_ops || meta.match_ops, sign_rules: d?.sign_rules || meta.sign_rules })
      setLegBuckets(d?.leg_buckets || ['m1', 'trailing', 'unsplit']); setLegHelp(d?.leg_help || '')
      setNr(p => ({ ...p, source_report: s, category: (d?.categories || ['commission'])[0] }))
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
  }
  async function loadObserved(s: string) {
    try { const d = await api('/api/v1/commcalc/commission-ledger/observed-types?source_report=' + encodeURIComponent(s)); setObs(d?.types || []) } catch { setObs([]) }
  }
  async function loadClasses() {
    try { const d = await api('/api/v1/commcalc/ma-product-class/classes'); setClassKeys(d?.assignable || []) } catch { setClassKeys([]) }
  }
  useEffect(() => { loadTemplates(); loadClasses() }, [])         // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadMap(src); loadObserved(src) }, [src])  // eslint-disable-line react-hooks/exhaustive-deps

  function flash(m: string) { setMsg(m); setTimeout(() => setMsg(''), 4000) }

  async function save(rule: Rule) {
    try {
      await api('/api/v1/commcalc/commission-category-map', { method: 'POST', body: JSON.stringify({ ...rule, source_report: src }) })
      flash('Saved'); loadMap(src); loadObserved(src)
    } catch (e: any) { flash(e?.message || 'Save failed — is migration 071 applied?') }
  }
  async function addRule() {
    if (!nr.pattern.trim()) { flash('Pattern is required'); return }
    await save(nr)
    setNr({ ...nr, pattern: '', priority: 100 })
  }
  async function del(rule: Rule) {
    if (!rule.id) { flash('Built-in default — edit the rules after migration 071 to override'); return }
    try { await api('/api/v1/commcalc/commission-category-map/' + rule.id, { method: 'DELETE' }); flash('Removed'); loadMap(src); loadObserved(src) }
    catch (e: any) { flash(e?.message || 'Delete failed') }
  }
  function newTemplate() {
    const k = prompt('New template key (lowercase, e.g. "cricket", "metro"):')?.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
    if (!k) return
    if (!tmpls.find(t => t.key === k)) setTmpls(p => [...p, { key: k, label: k, builtin: false, rule_count: 0 }])
    setSrc(k)
  }

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🗺️ Category → Bucket Map (Commission Ledger)</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 8 }}>
        Rules that classify a carrier's labels into the five canonical buckets. First match by ascending
        priority wins; payouts are <b>negative</b> amounts. Pick a template ({carrierNames} / your own) — a
        new tenant can adopt a preconfigured one or fork it. See results on{' '}
        <a href="/commcalc/commission-ledger" style={{ color: 'var(--accent,#2563eb)' }}>Commission Ledger →</a>
      </p>
      <p style={{ color: 'var(--text2)', fontSize: 12.5, marginBottom: 8, maxWidth: 900, lineHeight: 1.5 }}>
        Looking for what KIND of money each MA Daily Tx line is — commission vs spiff vs residual vs a
        customer bill payment vs a device sale? That is a separate, exact-match classification of the
        <code> product_name</code> column:{' '}
        <a href="/commcalc/ma-product-class" style={{ color: 'var(--accent,#2563eb)' }}>MA Product Name Classification →</a>
      </p>
      <p style={{ color: 'var(--text2)', fontSize: 12.5, marginBottom: 8, maxWidth: 900, lineHeight: 1.5 }}>
        A rule can now match on <b>is product class</b> instead of a keyword — one rule per class, and a
        <b> confirmed</b> class always beats a keyword rule whatever its priority. Class rules are
        <b> inert</b> until the Commission Ledger switch is set to “Product class”, and only CONFIRMED
        classifications ever match. Switch, class → leg map and the before/after delta live on{' '}
        <a href="/commcalc/ma-class-wiring" style={{ color: 'var(--accent,#2563eb)' }}>MA Product Class → Money →</a>
      </p>
      <p style={{ color: 'var(--text2)', fontSize: 12.5, marginBottom: 8, maxWidth: 900, lineHeight: 1.5 }}>
        Each rule now also carries a <b>Leg</b>: is this money <b>1st Month</b> commission (received in the
        same month the number activated) or <b>M2–M12</b> (received later for a number that was already
        active)? Leave it on <b>Derive</b> — the default on every existing rule, and the right answer
        whenever the label already says which month it is (&ldquo;… MONTH 4&rdquo;, &ldquo;… M1&rdquo;).
        Set it only for labels whose source never states a month. Category and Leg are independent: adding
        a leg never moves a line out of the bucket it is already in. Same split, same rules, on{' '}
        <a href="/commcalc/gp" style={{ color: 'var(--accent,#2563eb)' }}>Gross Profit →</a> and{' '}
        <a href="/commcalc/commission-legs" style={{ color: 'var(--accent,#2563eb)' }}>Commission Legs →</a>
        {legHelp ? <><br /><span style={{ fontSize: 12, color: 'var(--text3)' }}>{legHelp}</span></> : null}
      </p>
      {!ready && (
        <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>
          Run migration <code>071_commission_ledger.sql</code> to edit + persist rules. Until then the classifier uses the built-in defaults (shown below, read-only).
        </div>
      )}
      {usingDefaults && ready && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>No saved rules for this template yet — showing built-in defaults. Save one to start a custom set.</div>}
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      {/* How-to (plain-language, step by step) */}
      <details style={{ border: '1px solid var(--border)', borderRadius: 10, background: 'var(--surface)', padding: 12, marginBottom: 14 }}>
        <summary style={{ cursor: 'pointer', fontWeight: 700, fontSize: 14 }}>📘 How to use this page (step by step)</summary>
        <ol style={{ margin: '10px 0 6px 18px', fontSize: 13, lineHeight: 1.7, color: 'var(--text2)' }}>
          <li>Pick the <b>Template</b> for the carrier/file you're mapping ({carrierNames2} or <b>＋ New template</b> for your own).</li>
          <li><b>Import that carrier's commission file</b> on the{' '}
            <a href="/commcalc/commission-ledger" style={{ color: 'var(--accent,#2563eb)' }}>Commission Ledger</a> page,
            so its real labels appear in the table at the bottom of this page.</li>
          <li>Scroll to <b>“Observed labels in the ledger”</b> — <b style={{ color: '#9a3412' }}>orange rows land in
            “other”</b> (not yet classified). Those are exactly what you need to map.</li>
          <li>In <b>“Add rule”</b>: choose the <b>field</b> to look at, how to <b>match</b> it, type the <b>pattern</b>,
            choose the <b>bucket</b> it belongs to, then <b>Add</b>.</li>
          <li>Re-check the observed table — the row should now land in your bucket. Totals show on the Commission Ledger.</li>
        </ol>
        <div style={{ fontSize: 12.5, color: 'var(--text2)', lineHeight: 1.7 }}>
          <b>Field:</b> <b>product_name</b> = the item / description text · <b>order_type</b> = the transaction type.
          &nbsp;<b>Op:</b> <b>contains</b> = the text appears anywhere (most common) · <b>equals</b> = exact match.
          &nbsp;<b>Sign:</b> <b>negative only</b> = count it only when the amount is a payout (carriers post payouts as
          negatives) — leave this unless you know a bucket uses positive amounts.
          &nbsp;<b>Priority:</b> lower number is tested first and the <b>first match wins</b>, so put specific rules
          above generic ones.
          <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--border)' }}>
            ℹ️ <b>Not the same as “Carrier Mapping”.</b> THIS page classifies a carrier's <b>commission-file line
            items</b> into the 5 <b>Commission Ledger</b> buckets. To classify a carrier's <b>comp / residual
            statement</b> into the 4 components for <b>Total Compensation</b>, use{' '}
            <a href="/commcalc/carrier-mapping" style={{ color: 'var(--accent,#2563eb)' }}>Carrier Mapping</a>.
          </div>
        </div>
      </details>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 16 }}>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Template</label>
        <select style={inp} value={src} onChange={e => setSrc(e.target.value)}>
          {tmpls.map(t => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
        <button onClick={newTemplate} style={{ ...inp, cursor: 'pointer' }}>＋ New template</button>
      </div>

      {/* rules table */}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginBottom: 14 }}>
        <thead><tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
          <th style={{ padding: '6px 8px' }}>Pri</th><th style={{ padding: '6px 8px' }}>Field</th><th style={{ padding: '6px 8px' }}>Op</th>
          <th style={{ padding: '6px 8px' }}>Pattern</th><th style={{ padding: '6px 8px' }}>→ Category</th><th style={{ padding: '6px 8px' }} title="1st Month vs M2–M12. Derive = read the line's own payment month.">→ Leg</th><th style={{ padding: '6px 8px' }}>Sign</th><th></th>
        </tr></thead>
        <tbody>
          {[...rules].sort((a, b) => (a.priority || 100) - (b.priority || 100)).map((r, i) => (
            <tr key={r.id || i} style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: '5px 8px', color: 'var(--text2)' }}>{r.priority}</td>
              <td style={{ padding: '5px 8px' }}>{r.match_field}</td>
              <td style={{ padding: '5px 8px' }}>{r.match_op === 'product_class' ? 'is product class' : r.match_op}</td>
              <td style={{ padding: '5px 8px', fontFamily: 'monospace' }}>{r.pattern}{r.match_op === 'product_class' && <span style={{ fontFamily: 'inherit', fontSize: 11, color: 'var(--text3)', marginLeft: 6 }}>(class)</span>}</td>
              <td style={{ padding: '5px 8px', fontWeight: 600 }}>{labels[r.category] || r.category}</td>
              <td style={{ padding: '5px 8px' }}>
                {r.id ? (
                  <select style={{ ...inp, padding: '2px 6px', fontSize: 12 }} value={r.leg_bucket || ''}
                    onChange={e => save({ ...r, leg_bucket: e.target.value })}
                    title="Leave on Derive unless this label's source never states a month-of-life">
                    <option value="">Derive</option>
                    {legBuckets.map(b => <option key={b} value={b}>{LEG_LABEL[b] || b}</option>)}
                  </select>
                ) : (
                  <span style={{ fontSize: 11, color: 'var(--text3)' }}>Derive</span>
                )}
              </td>
              <td style={{ padding: '5px 8px', color: 'var(--text2)' }}>{r.sign_rule === 'any' ? 'any' : 'neg'}</td>
              <td style={{ padding: '5px 8px' }}>{r.id ? <button onClick={() => del(r)} style={{ ...inp, cursor: 'pointer', fontSize: 11, padding: '3px 8px' }}>✕</button> : <span style={{ fontSize: 11, color: 'var(--text3)' }}>default</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* add-rule form */}
      <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 12, marginBottom: 22, background: 'var(--surface)', display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)' }}>Add rule:</span>
        <select style={inp} disabled={nr.match_op === 'product_class'} value={nr.match_field} onChange={e => setNr({ ...nr, match_field: e.target.value })}>{meta.match_fields.map(f => <option key={f} value={f}>{f}</option>)}</select>
        <select style={inp} value={nr.match_op} onChange={e => setNr({ ...nr, match_op: e.target.value, pattern: '' })}>{meta.match_ops.map(o => <option key={o} value={o}>{o === 'product_class' ? 'is product class' : o}</option>)}</select>
        {nr.match_op === 'product_class' ? (
          <select style={{ ...inp, width: 190 }} value={nr.pattern} onChange={e => setNr({ ...nr, pattern: e.target.value })}>
            <option value="">— pick a product class —</option>
            {classKeys.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        ) : (
          <input style={{ ...inp, width: 170 }} placeholder="pattern (e.g. Spiff)" value={nr.pattern} onChange={e => setNr({ ...nr, pattern: e.target.value })} />
        )}
        <span style={{ fontSize: 13 }}>→</span>
        <select style={inp} value={nr.category} onChange={e => setNr({ ...nr, category: e.target.value })}>{cats.map(c => <option key={c} value={c}>{labels[c] || c}</option>)}</select>
        <select style={inp} value={nr.leg_bucket || ''} onChange={e => setNr({ ...nr, leg_bucket: e.target.value })} title="Commission leg — leave on Derive to read the line's own payment month">
          <option value="">leg: Derive</option>
          {legBuckets.map(b => <option key={b} value={b}>leg: {LEG_LABEL[b] || b}</option>)}
        </select>
        <select style={inp} value={nr.sign_rule} onChange={e => setNr({ ...nr, sign_rule: e.target.value })}>{meta.sign_rules.map(s => <option key={s} value={s}>{s === 'any' ? 'any sign' : 'negative only'}</option>)}</select>
        <input style={{ ...inp, width: 64 }} type="number" value={nr.priority} onChange={e => setNr({ ...nr, priority: Number(e.target.value) })} title="priority (lower wins)" />
        <button onClick={addRule} style={{ ...inp, cursor: 'pointer', fontWeight: 700, background: 'var(--accent,#2563eb)', color: '#fff', border: 'none' }}>Add</button>
      </div>

      {/* observed labels in the imported ledger */}
      <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 6 }}>Observed labels in the ledger</div>
      {obs.length === 0 ? (
        <div style={{ color: 'var(--text3)', fontSize: 13 }}>Nothing imported for this template yet — import a file on the Commission Ledger page to see real labels here.</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead><tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
            <th style={{ padding: '5px 8px' }}>Order type</th><th style={{ padding: '5px 8px' }}>Product / description</th>
            <th style={{ padding: '5px 8px', textAlign: 'right' }}>Lines</th><th style={{ padding: '5px 8px', textAlign: 'right' }}>Payout</th><th style={{ padding: '5px 8px' }}>Lands in</th><th style={{ padding: '5px 8px' }} title="1st Month = received in the activation month · M2–M12 = received later for an already-activated number">Leg</th>
          </tr></thead>
          <tbody>
            {obs.map((o, i) => (
              <tr key={i} style={{ borderTop: '1px solid var(--border)', background: o.category === 'other' ? '#fff7ed' : undefined }}>
                <td style={{ padding: '5px 8px', color: 'var(--text2)' }}>{o.order_type}</td>
                <td style={{ padding: '5px 8px' }}>{o.product_name}</td>
                <td style={{ padding: '5px 8px', textAlign: 'right' }}>{o.count}</td>
                <td style={{ padding: '5px 8px', textAlign: 'right' }}>{money(o.payout_total)}</td>
                <td style={{ padding: '5px 8px', fontWeight: 600, color: o.category === 'other' ? '#9a3412' : 'inherit' }}>{labels[o.category] || o.category}</td>
                <td style={{ padding: '5px 8px', fontWeight: 600, color: LEG_TINT[o.leg_bucket || 'unsplit'] }}
                  title={`${o.leg_why === 'rule_override' ? 'set by a rule' : o.leg_why === 'payment_month' ? "from the label's own payment month" : o.leg_why === 'label_override' ? 'set for this exact label' : o.leg_why === 'month_in_label' ? 'month named in the label' : 'the source states no month-of-life'}${o.leg_month ? ` · month ${o.leg_month}` : ''}`}>
                  {LEG_LABEL[o.leg_bucket || 'unsplit'] || o.leg_bucket}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
