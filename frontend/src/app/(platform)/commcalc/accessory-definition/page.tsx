'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ReportShell } from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'

// WHAT COUNTS AS AN ACCESSORY — the per-tenant definition.
//
// OWNER, 2026-08-01: "accessory option will be as per mapped manually and anything which says
// accessories or category accessory since every company defines in a different way, generally all
// screen protectors, cases, headsets, earphones, chargers, cables, adapters fall under the category of
// accessories."
//
// NOTHING ON THIS PAGE CHANGES WHAT ANYONE IS PAID. The five existing accessory classifiers still
// decide every existing number — commission, GP, targets, the P&L and the Sales Analyzer are all
// untouched. This page defines what the OWNER means by "accessory" and then shows, item by item, where
// each existing surface agrees with that and where it does not. Adopting the definition as the pay
// basis is a separate decision, with its own before/after numbers.
//
// TWO MECHANISMS: ① map your own items / departments / categories (picked from the values your sales
// data actually contains — never typed); ② a field rule that catches anything whose DEPARTMENT or
// CATEGORY field says "accessor…". Deliberately NOT product-name keywords: a 'case' keyword hits
// 'Casement' and a 'charger' keyword hits 'Charger Port Repair'.

type ClassRow = { class_key: string; label: string; description: string; status: string; source: string }
type Observed = {
  match_field: string; match_value: string; spellings?: string[]
  lines: number; ext_price: number; gp: number
  mapped: boolean; id?: string | null
  is_accessory: boolean | null; accessory_class: string | null; status: string | null
  note?: string | null; token_hit?: string | null; not_in_data?: boolean
}
type FieldRule = { enabled: boolean; token_fields: string[]; tokens: string[] }
type DefData = {
  observed: Record<string, Observed[]>; orphan_mappings: Observed[]
  classes: ClassRow[]; field_rule: FieldRule; field_rule_refused: string[]
  match_fields: { key: string; label: string }[]; token_fields: string[]; statuses: string[]
  counts: { mappings: number; confirmed: number; proposed: number; classes_confirmed: number }
  sku_coverage?: Sku
  meta: any; ready: boolean; migration: string | null
}
type Mech = { key: string; label: string; lines: number; ext_price: number; gp: number }
type Drift = {
  product_desc: string; uncaught_lines: number; uncaught_ext: number
  spellings: { department: string; category: string; lines: number; ext_price: number; caught: boolean; first_date: string | null; last_date: string | null }[]
}
type Sku = { lines: number; with_sku: number; pct: number; accessory_lines: number; accessory_with_sku: number; accessory_pct: number; usable: boolean; note: string | null }
type Agree = {
  rows_read: number; reference: string; reference_label: string
  surfaces: { key: string; label: string }[]
  totals: Record<string, { label: string; lines: number; ext_price: number; gp: number; transactions: number }>
  agreement: Record<string, { same: number; only_here: number; only_reference: number; only_here_ext: number; only_reference_ext: number }>
  disagreeing_items: any[]; disagreeing_item_count: number
  by_mechanism: Mech[]
  uncaught_gap: { lines: number; ext_price: number; products: any[]; product_count: number; note: string }
  negative_price_lines: { lines: number; ext_price: number }
  spelling_drift: Drift[]; sku_coverage: Sku
  lines_excluded_void_return: number
  field_rule: FieldRule; setup_fee_keywords: string[]; setup_fee_note: string
  counts: any; meta: any; ready: boolean; migration: string | null; money_note: string
}

const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const th: React.CSSProperties = { textAlign: 'left', padding: '7px 8px', fontSize: 11, textTransform: 'uppercase', letterSpacing: .4, color: 'var(--text3)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '6px 8px', fontSize: 13, borderBottom: '1px solid var(--border)', verticalAlign: 'top' }

function Badge({ kind }: { kind: 'unmapped' | 'proposed' | 'confirmed' | 'rule' | 'excluded' }) {
  const s: Record<string, React.CSSProperties> = {
    unmapped: { background: '#f8fafc', color: '#475569', border: '1px solid #e2e8f0' },
    proposed: { background: '#fff7ed', color: '#9a3412', border: '1px solid #fdba74' },
    confirmed: { background: '#f0fdf4', color: '#15803d', border: '1px solid #bbf7d0' },
    rule: { background: '#eff6ff', color: '#1d4ed8', border: '1px solid #bfdbfe' },
    excluded: { background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca' },
  }
  const label = kind === 'unmapped' ? 'NOT MAPPED' : kind === 'proposed' ? 'PROPOSED — not confirmed'
    : kind === 'confirmed' ? 'CONFIRMED' : kind === 'rule' ? 'BY FIELD RULE' : 'EXCLUDED'
  return <span style={{ ...s[kind], borderRadius: 999, padding: '1px 8px', fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap' }}>{label}</span>
}

const ITEM_COLS: ExportColumn[] = [
  { header: 'Item', get: r => r.product_desc },
  { header: 'SKU', get: r => r.sku },
  { header: 'Department', get: r => r.department },
  { header: 'Category', get: r => r.category },
  { header: 'Lines', get: r => r.lines, type: 'number' },
  { header: 'Sold $', get: r => r.ext_price, money: true },
  { header: 'GP $', get: r => r.gp, money: true },
  { header: 'PAY BASIS today', get: r => (r.verdicts?.combined ? 'accessory' : '—') },
  { header: 'This definition (confirmed)', get: r => (r.verdicts?.definition_confirmed ? 'accessory' : '—') },
  { header: 'This definition (+proposed)', get: r => (r.verdicts?.definition_proposed ? 'accessory' : '—') },
  { header: 'Accessory settings', get: r => (r.verdicts?.legacy ? 'accessory' : '—') },
  { header: 'Product catalog', get: r => (r.verdicts?.catalog ? 'accessory' : '—') },
  { header: 'Installment classifier', get: r => (r.verdicts?.installment ? 'accessory' : '—') },
  { header: 'Sales Analyzer', get: r => (r.verdicts?.analyzer ? 'accessory' : '—') },
  { header: 'GP category map', get: r => (r.verdicts?.gp_map ? 'accessory' : '—') },
]

const DRIFT_COLS: ExportColumn[] = [
  { header: 'Product', get: r => r.product_desc },
  { header: 'Department', get: r => r.department },
  { header: 'Category', get: r => r.category },
  { header: 'First sold', get: r => r.first_date, type: 'date' },
  { header: 'Last sold', get: r => r.last_date, type: 'date' },
  { header: 'Lines', get: r => r.lines, type: 'number' },
  { header: 'Sold $', get: r => r.ext_price, money: true },
  { header: 'Caught by the field rule?', get: r => (r.caught ? 'yes' : 'NO — needs a mapping') },
]

const MECH_COLS: ExportColumn[] = [
  { header: 'How the line was decided', get: r => r.label },
  { header: 'Lines', get: r => r.lines, type: 'number' },
  { header: 'Sold $', get: r => r.ext_price, money: true },
  { header: 'GP $', get: r => r.gp, money: true },
]

const SURFACE_COLS: ExportColumn[] = [
  { header: 'Surface', get: r => r.label },
  { header: 'Lines counted', get: r => r.lines, type: 'number' },
  { header: 'Sold $', get: r => r.ext_price, money: true },
  { header: 'GP $', get: r => r.gp, money: true },
  { header: 'Transactions', get: r => r.transactions, type: 'number' },
  { header: 'Agrees with pay basis', get: r => r.same, type: 'number' },
  { header: 'Extra vs pay basis', get: r => r.only_here, type: 'number' },
  { header: 'Extra $', get: r => r.only_here_ext, money: true },
  { header: 'Missing vs pay basis', get: r => r.only_reference, type: 'number' },
  { header: 'Missing $', get: r => r.only_reference_ext, money: true },
]

export default function AccessoryDefinitionPage() {
  const { period } = usePeriod()
  const [tab, setTab] = useState<'define' | 'compare'>('define')
  const [data, setData] = useState<DefData | null>(null)
  const [agree, setAgree] = useState<Agree | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [field, setField] = useState('department')
  const [q, setQ] = useState('')
  const [ruleDraft, setRuleDraft] = useState<FieldRule | null>(null)
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [facets, setFacets] = useState<{ periods: string[]; stores: string[]; reps: string[] } | null>(null)
  const [proposalPreview, setProposalPreview] = useState<any>(null)

  const store = (filt.stores || [])[0] || ''
  const rep = (filt.reps || [])[0] || ''

  const loadDef = useCallback(() => {
    setErr('')
    const qs = new URLSearchParams({ org_id: ORG_ID })
    if (period) qs.set('period', period)
    if (store) qs.set('store', store)
    if (rep) qs.set('rep', rep)
    api(`/api/v1/commcalc/accessory-definition?${qs.toString()}`)
      .then((d: DefData) => { setData(d); if (!ruleDraft) setRuleDraft(d.field_rule) })
      .catch(e => setErr(String(e?.message || e)))
  }, [period, store, rep, ruleDraft])

  const loadAgree = useCallback(() => {
    if (!period) return
    setBusy(true); setErr('')
    const qs = new URLSearchParams({ org_id: ORG_ID })
    if (store) qs.set('store', store)
    if (rep) qs.set('rep', rep)
    api(`/api/v1/commcalc/accessory-definition/agreement/${encodeURIComponent(period)}?${qs.toString()}`)
      .then(setAgree)
      .catch(e => setErr(String(e?.message || e)))
      .finally(() => setBusy(false))
  }, [period, store, rep])

  useEffect(() => { loadDef() }, [loadDef])
  useEffect(() => { if (tab === 'compare') loadAgree() }, [tab, loadAgree])
  useEffect(() => {
    api(`/api/v1/commcalc/accessory-definition/facets?org_id=${ORG_ID}`).then(setFacets).catch(() => {})
  }, [])

  async function saveMapping(row: Observed, patch: Partial<Observed>) {
    setMsg('')
    try {
      const body = {
        match_field: row.match_field, match_value: row.match_value,
        is_accessory: patch.is_accessory !== undefined ? patch.is_accessory : (row.is_accessory ?? true),
        accessory_class: patch.accessory_class !== undefined ? patch.accessory_class : row.accessory_class,
        status: patch.status || row.status || 'proposed',
        period: period || '',
      }
      await api(`/api/v1/commcalc/accessory-definition?org_id=${ORG_ID}`, { method: 'POST', body: JSON.stringify(body) })
      loadDef()
    } catch (e: any) { setMsg(e.message) }
  }

  async function unmap(row: Observed) {
    if (!row.id) return
    setMsg('')
    try {
      await api(`/api/v1/commcalc/accessory-definition/${row.id}?org_id=${ORG_ID}`, { method: 'DELETE' })
      loadDef()
    } catch (e: any) { setMsg(e.message) }
  }

  async function confirmAll(what: 'maps' | 'classes') {
    setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/accessory-definition/confirm?org_id=${ORG_ID}`,
        { method: 'POST', body: JSON.stringify(what === 'maps' ? { all: true } : { all_classes: true }) })
      setMsg(`Confirmed ${r?.confirmed_count ?? 0}.`)
      loadDef()
    } catch (e: any) { setMsg(e.message) }
  }

  async function seedClasses() {
    setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/accessory-definition/seed-classes?org_id=${ORG_ID}`, { method: 'POST', body: '{}' })
      setMsg(`${r?.inserted ?? 0} class proposal(s) created for this tenant.`)
      loadDef()
    } catch (e: any) { setMsg(e.message) }
  }

  // Infer PROPOSED product-description mappings from this tenant's OWN rows. This is what closes the
  // week-shaped hole the live July export exposed: the POS renamed the department/category mid-month,
  // so the field rule catches the same product from the 9th but not from the 2nd. The description does
  // not change, so mapping it covers both. Evidence-bound — see the endpoint docstring.
  async function proposeFromData(dryRun: boolean) {
    setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/accessory-definition/propose-from-data?org_id=${ORG_ID}`,
        { method: 'POST', body: JSON.stringify({ period: period || '', store, rep, dry_run: dryRun }) })
      setProposalPreview(r)
      if (!dryRun) { setMsg(r?.note || `${r?.inserted ?? 0} proposal(s) created.`); loadDef() }
    } catch (e: any) { setMsg(e.message) }
  }

  async function saveRule(reset = false) {
    setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/accessory-definition/field-rule?org_id=${ORG_ID}`,
        { method: 'PUT', body: JSON.stringify(reset ? { reset: true } : ruleDraft) })
      setMsg(r?.note || 'Field rule saved.')
      setRuleDraft(r?.field_rule || null)
      loadDef()
    } catch (e: any) { setMsg(e.message) }
  }

  const rows = useMemo(() => {
    const base = (data?.observed?.[field] || []).concat(
      (data?.orphan_mappings || []).filter(o => o.match_field === field))
    const s = q.trim().toLowerCase()
    return s ? base.filter(r => String(r.match_value || '').toLowerCase().includes(s)) : base
  }, [data, field, q])

  const surfaceRows = useMemo(() => {
    if (!agree) return []
    return (agree.surfaces || []).map(s => {
      const t: any = agree.totals?.[s.key] || { lines: 0, ext_price: 0, gp: 0, transactions: 0 }
      const a: any = agree.agreement?.[s.key] || { same: 0, only_here: 0, only_reference: 0, only_here_ext: 0, only_reference_ext: 0 }
      // `label` comes from the SURFACE list, not from the totals bucket — the payload carries it in
      // both places and the surface list is the ordered, canonical one.
      return { ...t, ...a, key: s.key, label: s.label }
    })
  }, [agree])

  const classLabel = (k: string | null) =>
    (data?.classes || []).find(c => c.class_key === k)?.label || k || ''

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>What counts as an accessory</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 940 }}>
          {period} · <b>read-only with respect to money</b> — nothing here changes what anyone is paid,
          what the GP report shows or what the P&amp;L books. This is your definition of “accessory”, plus
          an item-by-item comparison against every classifier the system already uses.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
        <button className={`btn ${tab === 'define' ? 'btn-primary' : ''}`} onClick={() => setTab('define')}>🏷️ Define</button>
        <button className={`btn ${tab === 'compare' ? 'btn-primary' : ''}`} onClick={() => setTab('compare')}>⚖️ Compare surfaces</button>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <StandardFilterBar
          value={filt} onChange={setFilt} periodMode="none"
          show={{ period: false, stores: true, markets: false, reps: true }}
          storeOptions={facets?.stores || []} repOptions={(facets?.reps || []).map(r => ({ id: r, label: r }))}
          right={<button className="btn btn-secondary" onClick={() => (tab === 'compare' ? loadAgree() : loadDef())} disabled={busy}>{busy ? 'Loading…' : 'Refresh'}</button>}
        />
        <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>
          Market is not offered here — <code>raw_sales</code> carries no market column, so a market filter
          would be a guess rather than a filter.
        </div>
      </div>

      {err && <div className="card" style={{ borderLeft: '4px solid var(--red)', marginBottom: 14, fontSize: 13 }}>{err}</div>}
      {msg && <div className="card" style={{ borderLeft: '4px solid var(--blue)', marginBottom: 14, fontSize: 13 }}>{msg}</div>}
      {data && !data.ready && (
        <div className="card" style={{ borderLeft: '4px solid var(--amber)', marginBottom: 14, fontSize: 13 }}>
          Migration <code>{data.migration}</code> hasn’t been run yet, so nothing can be saved. The classes
          below are the built-in proposals and the field rule is at its default — everything is read-only
          until the SQL runs.
        </div>
      )}

      {tab === 'define' && (
        <>
          {/* ── ② the field rule ─────────────────────────────────────────────────────────── */}
          <div className="card" style={{ marginBottom: 14 }}>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>Rule — “anything whose department or category says accessory”</div>
            <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '0 0 10px', maxWidth: 880 }}>
              This catches every line whose <b>Department</b> or <b>Category</b> field contains one of the
              words below, whatever it is called in your POS (“Accessories”, “ACCESSORY”, “Ondigo
              Accessories”…). It reads those two FIELDS only — never the product name. A product-name
              keyword would misfire: “case” also matches <i>Casement</i>, “charger” also matches
              <i> Charger Port Repair</i>.
            </p>
            {ruleDraft && (
              <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
                <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}>
                  <input type="checkbox" checked={!!ruleDraft.enabled}
                    onChange={e => setRuleDraft({ ...ruleDraft, enabled: e.target.checked })} />
                  Rule on
                </label>
                {(data?.token_fields || []).map(f => (
                  <label key={f} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}>
                    <input type="checkbox" checked={(ruleDraft.token_fields || []).includes(f)}
                      onChange={e => setRuleDraft({
                        ...ruleDraft,
                        token_fields: e.target.checked
                          ? [...(ruleDraft.token_fields || []), f]
                          : (ruleDraft.token_fields || []).filter(x => x !== f),
                      })} />
                    read the {f} field
                  </label>
                ))}
                <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
                  words
                  <input style={{ ...sel, width: 260 }} value={(ruleDraft.tokens || []).join(', ')}
                    onChange={e => setRuleDraft({ ...ruleDraft, tokens: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} />
                </label>
                <button className="btn btn-primary" onClick={() => saveRule(false)}>Save rule</button>
                <button className="btn" onClick={() => saveRule(true)}>Reset</button>
              </div>
            )}
            {(data?.field_rule_refused || []).length > 0 && (
              <div style={{ fontSize: 12, color: 'var(--red)' }}>
                Refused (this rule may only read a department or category field, never a product name):{' '}
                {(data?.field_rule_refused || []).join(', ')}
              </div>
            )}
          </div>

          {/* ── the class vocabulary ─────────────────────────────────────────────────────── */}
          <div className="card" style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <div style={{ fontWeight: 700 }}>Accessory classes — your list, waiting for your confirmation</div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn" onClick={seedClasses}>Create these for this tenant</button>
                <button className="btn btn-primary" onClick={() => confirmAll('classes')}>Confirm all classes</button>
              </div>
            </div>
            <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '0 0 10px', maxWidth: 880 }}>
              These are the classes you named — screen protectors, cases, headsets, earphones, chargers,
              cables, adapters. They are <b>labels</b>, not matchers: nothing is classified by them. They
              start as <b>proposals</b> so the vocabulary is yours before anything is mapped to it.
            </p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
              {(data?.classes || []).map(c => (
                <div key={c.class_key} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '6px 10px', fontSize: 12.5, minWidth: 200 }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <b>{c.label}</b><Badge kind={c.status === 'confirmed' ? 'confirmed' : 'proposed'} />
                  </div>
                  <div style={{ color: 'var(--text3)', fontSize: 11.5 }}>{c.description}</div>
                </div>
              ))}
            </div>
          </div>

          {/* ── ① the manual mapping grid ────────────────────────────────────────────────── */}
          <div className="card">
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
              <div style={{ fontWeight: 700 }}>Map your own values</div>
              <select style={sel} value={field} onChange={e => setField(e.target.value)}>
                {(data?.match_fields || []).map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
              </select>
              <input style={{ ...sel, width: 220 }} placeholder="filter the list…" value={q} onChange={e => setQ(e.target.value)} />
              <button className="btn btn-primary" onClick={() => confirmAll('maps')}>Confirm all proposed mappings</button>
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>
                {data?.counts?.confirmed ?? 0} confirmed · {data?.counts?.proposed ?? 0} proposed
              </span>
            </div>
            {data?.sku_coverage && !data.sku_coverage.usable && data.sku_coverage.note && (
              <div style={{ marginBottom: 10, padding: '8px 10px', borderLeft: '3px solid var(--amber)', background: 'var(--surface2)', fontSize: 12, maxWidth: 880 }}>
                <b>SKU won’t work for you.</b> {data.sku_coverage.note} ({data.sku_coverage.accessory_with_sku}
                {' '}of {data.sku_coverage.accessory_lines} accessory line(s) carry one.)
              </div>
            )}
            <div style={{ marginBottom: 10, padding: '8px 10px', borderLeft: '3px solid var(--blue)', background: 'var(--surface2)', fontSize: 12, maxWidth: 880 }}>
              <b>Let the data propose them.</b> Your POS can rename a department or category mid-month
              for the same product — when that happens the field rule catches it on some days and not
              others. This looks at your own lines and proposes a <i>product description</i> mapping for
              every product that is already an accessory on at least one of its own lines, which covers
              the days where the spelling differs. It proposes nothing without that evidence, and nothing
              is confirmed or paid until you say so.
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <button className="btn" onClick={() => proposeFromData(true)}>Preview proposals</button>
                <button className="btn btn-primary" onClick={() => proposeFromData(false)}>Create them as proposals</button>
              </div>
              {proposalPreview && (
                <div style={{ marginTop: 8 }}>
                  <b>{proposalPreview.count}</b> product(s) would be proposed
                  {proposalPreview.dry_run ? ' (nothing was written)' : ''}:
                  {(proposalPreview.proposals || []).slice(0, 12).map((p: any, i: number) => (
                    <div key={i} style={{ color: 'var(--text2)' }}>
                      • <b>{p.match_value}</b> — accessory on {p.covered_lines} of {p.lines} line(s)
                      {p.uncovered_lines ? `, would newly cover ${p.uncovered_lines}` : ''}
                      {p.evidence ? ` (evidence: ${p.evidence.category || p.evidence.department} on ${p.evidence.date})` : ''}
                    </div>
                  ))}
                </div>
              )}
            </div>
            <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '0 0 10px', maxWidth: 880 }}>
              Every row below is a value your own sales data actually contains — there is no free-text
              box, so a typo cannot create a mapping nothing will ever match. Mark a value as an accessory
              (optionally with a class), or mark it <b>not</b> an accessory to carve one product out of an
              otherwise-accessory department. Set-up fees are never accessories and are excluded before
              any of this is considered.
            </p>
            <div className="table-wrapper" style={{ border: 'none' }}>
              <table style={{ width: '100%' }}>
                <thead><tr>
                  <th style={th}>Value</th><th style={th}>Lines</th><th style={th}>Sold $</th><th style={th}>GP $</th>
                  <th style={th}>Accessory?</th><th style={th}>Class</th><th style={th}>Status</th><th style={th}></th>
                </tr></thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={`${r.match_field}:${r.match_value}:${i}`}>
                      <td style={td}>
                        {r.match_value || <i style={{ color: 'var(--text3)' }}>(blank)</i>}
                        {r.not_in_data && <div style={{ fontSize: 11, color: 'var(--text3)' }}>mapped, but not present in this view</div>}
                        {(r.spellings || []).length > 1 && <div style={{ fontSize: 11, color: 'var(--text3)' }}>spellings: {(r.spellings || []).join(' · ')}</div>}
                      </td>
                      <td style={td}>{r.lines.toLocaleString()}</td>
                      <td style={td}>{fmt(r.ext_price)}</td>
                      <td style={td}>{fmt(r.gp)}</td>
                      <td style={td}>
                        <select style={sel} value={r.mapped ? (r.is_accessory ? 'yes' : 'no') : ''}
                          onChange={e => e.target.value === '' ? unmap(r) : saveMapping(r, { is_accessory: e.target.value === 'yes' })}>
                          <option value="">— not mapped —</option>
                          <option value="yes">Accessory</option>
                          <option value="no">NOT an accessory</option>
                        </select>
                      </td>
                      <td style={td}>
                        <select style={sel} value={r.accessory_class || ''} disabled={!r.mapped || r.is_accessory === false}
                          onChange={e => saveMapping(r, { accessory_class: e.target.value || null })}>
                          <option value="">— no class —</option>
                          {(data?.classes || []).map(c => <option key={c.class_key} value={c.class_key}>{c.label}</option>)}
                        </select>
                      </td>
                      <td style={td}>
                        {!r.mapped
                          ? (r.token_hit ? <Badge kind="rule" /> : <Badge kind="unmapped" />)
                          : r.is_accessory === false ? <Badge kind="excluded" />
                            : r.status === 'confirmed' ? <Badge kind="confirmed" /> : <Badge kind="proposed" />}
                        {r.token_hit && <div style={{ fontSize: 11, color: 'var(--text3)' }}>matched “{r.token_hit}”</div>}
                      </td>
                      <td style={td}>
                        {r.mapped && r.status !== 'confirmed' && (
                          <button className="btn" style={{ fontSize: 11, padding: '2px 8px' }}
                            onClick={() => saveMapping(r, { status: 'confirmed' })}>Confirm</button>
                        )}
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && <tr><td style={td} colSpan={8}><i style={{ color: 'var(--text3)' }}>No values for this field in the current view.</i></td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {tab === 'compare' && (
        <>
          {agree && (
            <div className="card" style={{ marginBottom: 14, borderLeft: '4px solid var(--blue)', fontSize: 12.5 }}>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>How to read this</div>
              <div style={{ color: 'var(--text2)' }}>
                {agree.money_note} The reference column is <b>{agree.reference_label}</b>. “Missing vs pay
                basis” = lines that are counted as accessories today but this definition would <b>not</b>{' '}
                count; “Extra vs pay basis” = lines the definition would <b>add</b>. Those two numbers are
                the entire cost of adopting it.
              </div>
              <div style={{ color: 'var(--text3)', marginTop: 6 }}>
                {agree.rows_read.toLocaleString()} live line(s) read
                {agree.lines_excluded_void_return ? ` · ${agree.lines_excluded_void_return} voided/returned line(s) excluded` : ''} ·{' '}
                {agree.setup_fee_note} ({(agree.setup_fee_keywords || []).join(', ') || 'no keywords configured'})
              </div>
            </div>
          )}
          {agree && (agree.spelling_drift || []).length > 0 && (
            <div className="card" style={{ marginBottom: 14, borderLeft: '4px solid var(--amber)', fontSize: 12.5 }}>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>
                ⚠ Your POS renamed the department/category mid-period for {(agree.spelling_drift || []).length} product(s)
              </div>
              <div style={{ color: 'var(--text2)' }}>
                The same physical product was sold under two different spellings, and the field rule only
                catches one of them — so those lines are counted on some days and not others.
                <b> {(agree.spelling_drift || []).reduce((a, d) => a + d.uncaught_lines, 0)}</b> line(s)
                ({fmt((agree.spelling_drift || []).reduce((a, d) => a + d.uncaught_ext, 0))}) fall in the gap.
                Use <b>Define → Let the data propose them</b> to close it.
              </div>
            </div>
          )}
          {agree && (agree.spelling_drift || []).length > 0 && (
            <div style={{ marginBottom: 14 }}>
              <ReportShell
                title={`Mid-period spelling changes — ${period}`}
                subtitle="One row per spelling. A product with both a 'yes' and a 'NO' row is a hole the field rule cannot close on its own."
                filename={`accessory-spelling-drift-${period}`}
                columns={DRIFT_COLS} compact stickyHeader
                rows={(agree.spelling_drift || []).flatMap(d => d.spellings.map(sp => ({ product_desc: d.product_desc, ...sp })))}
              />
            </div>
          )}
          {agree && (
            <div style={{ marginBottom: 14 }}>
              <ReportShell
                title={`How each line was decided — ${period}`}
                subtitle="Attribution per mechanism. If 'Nothing matched it' is large, the field rule is not carrying as much as it looks."
                filename={`accessory-mechanisms-${period}`}
                columns={MECH_COLS} rows={agree.by_mechanism || []} compact
              />
              {agree.uncaught_gap?.lines > 0 && (
                <div className="card" style={{ marginTop: 10, fontSize: 12.5, borderLeft: '4px solid var(--red)' }}>
                  <b>{agree.uncaught_gap.lines} line(s) ({fmt(agree.uncaught_gap.ext_price)}) nothing in your definition caught</b>
                  {' '}but an existing classifier does count as accessories. {agree.uncaught_gap.note}
                  {(agree.uncaught_gap.products || []).slice(0, 10).map((p: any, i: number) => (
                    <div key={i} style={{ color: 'var(--text2)' }}>
                      • {p.product_desc || '(no description)'} — {p.lines} line(s), {fmt(p.ext_price)} ·
                      {' '}dept {(p.departments || []).join('/')} · cat {(p.categories || []).join('/')}
                    </div>
                  ))}
                </div>
              )}
              {agree.negative_price_lines?.lines > 0 && (
                <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
                  {agree.negative_price_lines.lines} line(s) carry a NEGATIVE price ({fmt(agree.negative_price_lines.ext_price)}) —
                  return-shaped rows that the export did not flag as voids or returns. They are counted here as they appear.
                </div>
              )}
            </div>
          )}
          <div style={{ marginBottom: 14 }}>
            <ReportShell
              title={`Accessory surfaces — ${period}`}
              subtitle="What each classifier counts, and how far it is from the pay basis. Read-only."
              filename={`accessory-surfaces-${period}`}
              columns={SURFACE_COLS} rows={surfaceRows} compact stickyHeader
            />
          </div>
          <ReportShell
            title={`Items the surfaces disagree about — ${period}`}
            subtitle={agree ? `${agree.disagreeing_item_count.toLocaleString()} item(s) classified differently by at least two surfaces. These are the ones worth mapping.` : ''}
            filename={`accessory-disagreements-${period}`}
            columns={ITEM_COLS} rows={agree?.disagreeing_items || []} compact stickyHeader
          />
        </>
      )}
    </div>
  )
}
