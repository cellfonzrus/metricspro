'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'

// Commission Category Map (SAP-style) — the per-template rules that classify a carrier's commission labels
// into the five canonical buckets. Rules match on product_name OR order_type (contains/equals), in ascending
// priority (first match wins), and only book a payout when the amount is negative (sign_rule). The
// "Observed labels" table shows the real (order_type, product_name) values in the imported ledger with the
// bucket they CURRENTLY land in, so unmapped ("other") payouts are easy to spot and fix. Backed by
// commcalc.commission_category_map (migration 071); falls back to built-in defaults until 071 is run.

type Rule = { id?: string; source_report: string; match_field: string; match_op: string; pattern: string; category: string; sign_rule: string; priority: number; is_seeded?: boolean }
type Tmpl = { key: string; label: string; builtin: boolean; rule_count: number }
type Obs = { order_type: string; product_name: string; count: number; payout_total: number; category: string; is_payout: boolean }
const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const money = (n: number) => (n || 0).toLocaleString('en-US', { style: 'currency', currency: 'USD' })

export default function CommissionCategoryMapPage() {
  const [tmpls, setTmpls] = useState<Tmpl[]>([])
  const [src, setSrc] = useState('ma_daily_tx')
  const [rules, setRules] = useState<Rule[]>([])
  const [usingDefaults, setUsingDefaults] = useState(false)
  const [ready, setReady] = useState(true)
  const [cats, setCats] = useState<string[]>([])
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [meta, setMeta] = useState<{ match_fields: string[]; match_ops: string[]; sign_rules: string[] }>({ match_fields: ['product_name', 'order_type'], match_ops: ['contains', 'equals'], sign_rules: ['negative_only', 'any'] })
  const [obs, setObs] = useState<Obs[]>([])
  const [msg, setMsg] = useState('')
  const [nr, setNr] = useState<Rule>({ source_report: 'ma_daily_tx', match_field: 'product_name', match_op: 'contains', pattern: '', category: 'commission', sign_rule: 'negative_only', priority: 100 })

  async function loadTemplates() {
    try { const d = await api('/commcalc/commission-ledger/templates'); setTmpls(d?.templates || []) } catch { /* noop */ }
  }
  async function loadMap(s: string) {
    try {
      const d = await api('/commcalc/commission-category-map?source_report=' + encodeURIComponent(s))
      setRules(d?.rules?.length ? d.rules : (d?.default_rules || []))
      setUsingDefaults(!!d?.using_defaults); setReady(d?.ready !== false)
      setCats(d?.categories || []); setLabels(d?.category_labels || {})
      setMeta({ match_fields: d?.match_fields || meta.match_fields, match_ops: d?.match_ops || meta.match_ops, sign_rules: d?.sign_rules || meta.sign_rules })
      setNr(p => ({ ...p, source_report: s, category: (d?.categories || ['commission'])[0] }))
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
  }
  async function loadObserved(s: string) {
    try { const d = await api('/commcalc/commission-ledger/observed-types?source_report=' + encodeURIComponent(s)); setObs(d?.types || []) } catch { setObs([]) }
  }
  useEffect(() => { loadTemplates() }, [])         // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadMap(src); loadObserved(src) }, [src])  // eslint-disable-line react-hooks/exhaustive-deps

  function flash(m: string) { setMsg(m); setTimeout(() => setMsg(''), 4000) }

  async function save(rule: Rule) {
    try {
      await api('/commcalc/commission-category-map', { method: 'POST', body: JSON.stringify({ ...rule, source_report: src }) })
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
    try { await api('/commcalc/commission-category-map/' + rule.id, { method: 'DELETE' }); flash('Removed'); loadMap(src); loadObserved(src) }
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
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🗺️ Commission Category Map</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 8 }}>
        Rules that classify a carrier's labels into the five canonical buckets. First match by ascending
        priority wins; payouts are <b>negative</b> amounts. Pick a template (Total / Boost / your own) — a
        new tenant can adopt a preconfigured one or fork it. See results on{' '}
        <a href="/commcalc/commission-ledger" style={{ color: 'var(--accent,#2563eb)' }}>Commission Ledger →</a>
      </p>
      {!ready && (
        <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>
          Run migration <code>071_commission_ledger.sql</code> to edit + persist rules. Until then the classifier uses the built-in defaults (shown below, read-only).
        </div>
      )}
      {usingDefaults && ready && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>No saved rules for this template yet — showing built-in defaults. Save one to start a custom set.</div>}
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}

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
          <th style={{ padding: '6px 8px' }}>Pattern</th><th style={{ padding: '6px 8px' }}>→ Category</th><th style={{ padding: '6px 8px' }}>Sign</th><th></th>
        </tr></thead>
        <tbody>
          {[...rules].sort((a, b) => (a.priority || 100) - (b.priority || 100)).map((r, i) => (
            <tr key={r.id || i} style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: '5px 8px', color: 'var(--text2)' }}>{r.priority}</td>
              <td style={{ padding: '5px 8px' }}>{r.match_field}</td>
              <td style={{ padding: '5px 8px' }}>{r.match_op}</td>
              <td style={{ padding: '5px 8px', fontFamily: 'monospace' }}>{r.pattern}</td>
              <td style={{ padding: '5px 8px', fontWeight: 600 }}>{labels[r.category] || r.category}</td>
              <td style={{ padding: '5px 8px', color: 'var(--text2)' }}>{r.sign_rule === 'any' ? 'any' : 'neg'}</td>
              <td style={{ padding: '5px 8px' }}>{r.id ? <button onClick={() => del(r)} style={{ ...inp, cursor: 'pointer', fontSize: 11, padding: '3px 8px' }}>✕</button> : <span style={{ fontSize: 11, color: 'var(--text3)' }}>default</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* add-rule form */}
      <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 12, marginBottom: 22, background: 'var(--surface)', display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)' }}>Add rule:</span>
        <select style={inp} value={nr.match_field} onChange={e => setNr({ ...nr, match_field: e.target.value })}>{meta.match_fields.map(f => <option key={f} value={f}>{f}</option>)}</select>
        <select style={inp} value={nr.match_op} onChange={e => setNr({ ...nr, match_op: e.target.value })}>{meta.match_ops.map(o => <option key={o} value={o}>{o}</option>)}</select>
        <input style={{ ...inp, width: 170 }} placeholder="pattern (e.g. Spiff)" value={nr.pattern} onChange={e => setNr({ ...nr, pattern: e.target.value })} />
        <span style={{ fontSize: 13 }}>→</span>
        <select style={inp} value={nr.category} onChange={e => setNr({ ...nr, category: e.target.value })}>{cats.map(c => <option key={c} value={c}>{labels[c] || c}</option>)}</select>
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
            <th style={{ padding: '5px 8px', textAlign: 'right' }}>Lines</th><th style={{ padding: '5px 8px', textAlign: 'right' }}>Payout</th><th style={{ padding: '5px 8px' }}>Lands in</th>
          </tr></thead>
          <tbody>
            {obs.map((o, i) => (
              <tr key={i} style={{ borderTop: '1px solid var(--border)', background: o.category === 'other' ? '#fff7ed' : undefined }}>
                <td style={{ padding: '5px 8px', color: 'var(--text2)' }}>{o.order_type}</td>
                <td style={{ padding: '5px 8px' }}>{o.product_name}</td>
                <td style={{ padding: '5px 8px', textAlign: 'right' }}>{o.count}</td>
                <td style={{ padding: '5px 8px', textAlign: 'right' }}>{money(o.payout_total)}</td>
                <td style={{ padding: '5px 8px', fontWeight: 600, color: o.category === 'other' ? '#9a3412' : 'inherit' }}>{labels[o.category] || o.category}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
