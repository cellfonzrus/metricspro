'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import EntityPicker from '@/components/EntityPicker'
import { useActiveCarrier } from '@/lib/auth-context'
import { financingVendorLabel, vendorServesCarrier } from '@/lib/carrier-scope'

// FINANCING VENDOR REGISTRY (admin) — owner directive 2026-08-04: "edge in case of total and acima in
// case of boost, acima could also be added to total at a later date and more vendors can be added to
// both carriers".
//
// Everything on this page is CONFIG. A vendor is a row; a carrier assignment is a row; how a financed
// sale is recognised is a row (or an inherited pointer at a rule the tenant already wrote). Nothing
// about Edge or ACIMA is compiled into the product.
//
// PICK-DON'T-TYPE: the tender value comes from the strings this period's data actually contains, the
// carrier from the tenant's carrier list, and the inherited matcher from the tenant's own pay rules.

type Matcher = { id?: string; match_field: string; match_op: string; match_value: string; field_warning?: string | null; from_rule_label?: string; source?: string }
type Vendor = {
  vendor_key: string; label: string; enabled: boolean; detection_source: string
  detection_ref: { rule_ids?: string[] } | null; amount_basis: string; sort_order: number
  notes: string | null; source: string
  detection_status: string; detection_note: string
  matchers: Matcher[]
  carriers: { id?: string; carrier_id?: string | null; carrier_name?: string | null; source?: string }[]
}
type PlanRule = {
  rule_id: string; plan_name: string; label: string; match_field: string; match_op: string
  match_value: string; payout_kind: string; amount: number; usable: boolean; unusable_reason: string | null
  financing_vendor_key: string | null
}
type Data = {
  ready: boolean; vendors: Vendor[]; carriers: { id: string; name: string; code?: string }[]
  plan_rules: PlanRule[]; acima_tenders: string[]; acima_configured: boolean
  vocabulary: {
    match_fields: { value: string; label: string }[]
    match_ops: { value: string; label: string }[]
    detection_sources: string[]; amount_bases: string[]
  }
  note: string | null
}

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const card: React.CSSProperties = { padding: 16, marginBottom: 14 }

const SOURCE_LABEL: Record<string, string> = {
  rules: 'Its own detection rules (below)',
  plan_rule: 'Inherit from a commission-plan rule — the report can then never disagree with what pays',
  acima_config: 'Inherit this tenant’s existing ACIMA tender mapping',
}
const BASIS_LABEL: Record<string, string> = {
  unit_line: 'The financed device line’s Ext Price',
  transaction: 'Every detected line of the transaction (split across its devices)',
}

export default function FinancingVendorsPage() {
  const { period } = usePeriod()
  // Active-carrier lens: a dual-carrier tenant configures ONE carrier's financing at a time. The vendor
  // list is filtered to the active carrier, the vendor name + key show neutrally (never ACIMA/TW/Edge),
  // and the carrier-naming assignment section is hidden. Single-carrier tenants are unchanged.
  const { activeCarrier, multi } = useActiveCarrier()
  const [d, setD] = useState<Data | null>(null)
  const [tenders, setTenders] = useState<{ value: string; transactions: number }[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [draft, setDraft] = useState<Record<string, Partial<Vendor>>>({})
  const [newKey, setNewKey] = useState('')
  const [newLabel, setNewLabel] = useState('')
  const [ruleForm, setRuleForm] = useState<Record<string, { match_field: string; match_op: string; match_value: string }>>({})
  const [carrierPick, setCarrierPick] = useState<Record<string, string[]>>({})

  const load = useCallback(() => {
    setBusy(true); setErr('')
    api(`/api/v1/commcalc/financing/vendors?org_id=${ORG_ID}`)
      .then((r: Data) => { setD(r); setDraft({}) })
      .catch(e => setErr(String(e?.message || e)))
      .finally(() => setBusy(false))
  }, [])
  useEffect(() => { load() }, [load])

  // The tender strings this period really contains — the pick-don't-type source for a detection value.
  useEffect(() => {
    if (!period) return
    api(`/api/v1/commcalc/financing/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then((r: any) => setTenders(r?.tender_values || []))
      .catch(() => setTenders([]))
  }, [period])

  const carrierOpts = useMemo(
    () => (d?.carriers || []).map(c => ({ id: c.id, label: c.name, sublabel: c.code || undefined })), [d])
  const ruleOpts = useMemo(
    () => (d?.plan_rules || []).filter(r => r.usable).map(r => ({
      id: r.rule_id,
      label: `${r.plan_name || 'plan'} · ${r.label || r.match_value || r.rule_id.slice(0, 8)}`,
      sublabel: `${r.match_field} ${r.match_op} “${r.match_value}” · ${r.payout_kind}`,
    })), [d])

  function patch(key: string, p: Partial<Vendor>) {
    setDraft(s => ({ ...s, [key]: { ...(s[key] || {}), ...p } }))
  }
  function value<K extends keyof Vendor>(v: Vendor, k: K): Vendor[K] {
    const dv = draft[v.vendor_key] as any
    return (dv && k in dv) ? dv[k] : v[k]
  }

  async function save(v: Vendor) {
    setBusy(true); setMsg(''); setErr('')
    try {
      await api(`/api/v1/commcalc/financing/vendors?org_id=${ORG_ID}`, {
        method: 'PUT',
        body: JSON.stringify({
          vendor_key: v.vendor_key,
          label: value(v, 'label'),
          enabled: value(v, 'enabled'),
          detection_source: value(v, 'detection_source'),
          detection_ref: value(v, 'detection_ref'),
          amount_basis: value(v, 'amount_basis'),
          sort_order: value(v, 'sort_order'),
          notes: value(v, 'notes'),
        }),
      })
      setMsg(`Saved ${value(v, 'label')}.`)
      load()
    } catch (e: any) { setErr(e?.message || 'Save failed') }
    setBusy(false)
  }

  async function addVendor() {
    const key = newKey.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_')
    if (!key) { setErr('Give the vendor a short key, e.g. affirm'); return }
    setBusy(true); setErr('')
    try {
      await api(`/api/v1/commcalc/financing/vendors?org_id=${ORG_ID}`, {
        method: 'PUT',
        body: JSON.stringify({ vendor_key: key, label: newLabel.trim() || key, enabled: true,
          detection_source: 'rules', amount_basis: 'unit_line', sort_order: 100 }),
      })
      setNewKey(''); setNewLabel(''); setMsg(`Added ${key}. Now say how a ${key} sale is recognised.`)
      load()
    } catch (e: any) { setErr(e?.message || 'Could not add the vendor') }
    setBusy(false)
  }

  async function addCarrier(v: Vendor) {
    const picked = carrierPick[v.vendor_key] || []
    if (!picked.length) return
    setBusy(true); setErr('')
    try {
      for (const id of picked) {
        const c = (d?.carriers || []).find(x => x.id === id)
        await api(`/api/v1/commcalc/financing/vendors/${encodeURIComponent(v.vendor_key)}/carriers?org_id=${ORG_ID}`,
          { method: 'POST', body: JSON.stringify({ carrier_id: id, carrier_name: c?.name || null }) })
      }
      setCarrierPick(s => ({ ...s, [v.vendor_key]: [] }))
      load()
    } catch (e: any) { setErr(e?.message || 'Could not assign the carrier') }
    setBusy(false)
  }

  async function removeCarrier(v: Vendor, rowId?: string) {
    if (!rowId) return
    setBusy(true)
    try {
      await api(`/api/v1/commcalc/financing/vendors/${encodeURIComponent(v.vendor_key)}/carriers/${rowId}?org_id=${ORG_ID}`,
        { method: 'DELETE' })
      load()
    } catch (e: any) { setErr(e?.message || 'Could not remove the assignment') }
    setBusy(false)
  }

  async function addRule(v: Vendor) {
    const f = ruleForm[v.vendor_key] || { match_field: 'tender_type', match_op: 'word', match_value: '' }
    // (defaults mirror DEFAULT_RULE below — tender type, word-anchored)
    if (!f.match_value.trim()) { setErr('Pick or type the value to match'); return }
    setBusy(true); setErr('')
    try {
      const r = await api(`/api/v1/commcalc/financing/vendors/${encodeURIComponent(v.vendor_key)}/detection?org_id=${ORG_ID}`,
        { method: 'POST', body: JSON.stringify(f) })
      setMsg(r?.warning ? `Rule saved — ⚠️ ${r.warning}` : 'Detection rule saved.')
      setRuleForm(s => ({ ...s, [v.vendor_key]: { ...f, match_value: '' } }))
      load()
    } catch (e: any) { setErr(e?.message || 'Could not save the rule') }
    setBusy(false)
  }

  async function removeRule(id?: string) {
    if (!id) return
    setBusy(true)
    try {
      await api(`/api/v1/commcalc/financing/detection/${id}?org_id=${ORG_ID}`, { method: 'DELETE' })
      load()
    } catch (e: any) { setErr(e?.message || 'Could not remove the rule') }
    setBusy(false)
  }

  const DEFAULT_RULE = { match_field: 'tender_type', match_op: 'word', match_value: '' }
  const rf = (key: string) => ruleForm[key] || DEFAULT_RULE
  const setRf = (key: string, p: Partial<typeof DEFAULT_RULE>) =>
    setRuleForm(s => ({ ...s, [key]: { ...(s[key] || DEFAULT_RULE), ...p } }))

  return (
    <div style={{ maxWidth: 1180 }}>
      <div style={{ marginBottom: 18 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Financing vendors</h1>
          <Link href="/commcalc/financing" className="btn btn-secondary" style={{ fontSize: 12 }}>← Financing report</Link>
        </div>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '6px 0 0' }}>
          Who finances your sales, which carriers each one serves, and how a financed sale is recognised
          in the data. Adding a vendor — or putting an existing vendor on another carrier — is a setting
          here, never a code change.
        </p>
      </div>

      {d && !d.ready && (
        <div className="card" style={{ ...card, background: '#fffbeb', border: '1px solid #fde68a', color: '#92400e' }}>
          ⚠️ {d.note}
        </div>
      )}
      {err && <div className="card" style={{ ...card, color: 'var(--red)' }}>{err}</div>}
      {msg && <div className="card" style={{ ...card, color: 'var(--green)' }}>{msg}</div>}

      {(d?.vendors || []).filter(v => !multi || vendorServesCarrier(v.carriers, activeCarrier)).map(v => (
        <div key={v.vendor_key} className="card" style={card}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
            {/* Under the lens the vendor name shows neutrally and is not renamed here (renaming stays a
                single-carrier action) so no brand string is ever printed; the real label is untouched. */}
            {multi
              ? <span style={{ width: 240, fontWeight: 600, fontSize: 14 }}>{financingVendorLabel(v.vendor_key, String(value(v, 'label') ?? ''))}</span>
              : <input className="input" style={{ width: 240, fontWeight: 600 }} value={String(value(v, 'label') ?? '')}
                  onChange={e => patch(v.vendor_key, { label: e.target.value })} />}
            {!multi && (
              <span style={{ fontSize: 11.5, color: 'var(--text3)' }}>
                key <code>{v.vendor_key}</code>{v.source === 'seed' ? ' · built-in default (not yet saved)' : ''}
              </span>
            )}
            <label style={{ fontSize: 12.5, display: 'inline-flex', gap: 6, alignItems: 'center' }}>
              <input type="checkbox" checked={!!value(v, 'enabled')}
                onChange={e => patch(v.vendor_key, { enabled: e.target.checked })} />
              Enabled
            </label>
            <span style={{
              fontSize: 11.5, padding: '2px 8px', borderRadius: 20,
              background: v.detection_status === 'configured' ? '#dcfce7' : '#fef3c7',
              color: v.detection_status === 'configured' ? '#166534' : '#92400e',
            }}>
              {v.detection_status === 'configured' ? '✓ detection configured'
                : v.detection_status === 'inherited_default' ? '◐ inherited default'
                  : v.detection_status === 'unusable' ? '⚠️ detection unusable' : '⚠️ detection not configured'}
            </span>
            <div style={{ flex: 1 }} />
            <button className="btn btn-primary" style={{ fontSize: 12 }} disabled={busy} onClick={() => save(v)}>💾 Save</button>
          </div>

          {/* The seed detection note can name the vendor brand / the other carrier — hide under the lens. */}
          {!multi && <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 10 }}>{v.detection_note}</div>}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: 14 }}>
            {/* carriers — the chips name carriers, so the whole assignment section is hidden under the
                lens (the vendor is already scoped to the active carrier). */}
            {!multi && (
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Carriers this vendor serves</div>
              <div style={{ marginBottom: 6 }}>
                {(v.carriers || []).length === 0 && <span style={{ fontSize: 12.5, color: 'var(--text3)' }}>any carrier</span>}
                {(v.carriers || []).map((c, i) => (
                  <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12.5,
                    border: '1px solid var(--border)', borderRadius: 20, padding: '2px 9px', marginRight: 6 }}>
                    {c.carrier_name || c.carrier_id}
                    {c.source === 'seed'
                      ? <span style={{ color: 'var(--text3)', fontSize: 11 }}>(default)</span>
                      : <button onClick={() => removeCarrier(v, c.id)} style={{ border: 0, background: 'none', cursor: 'pointer' }}>×</button>}
                  </span>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <EntityPicker multi options={carrierOpts} value={carrierPick[v.vendor_key] || []}
                  onChange={val => setCarrierPick(s => ({ ...s, [v.vendor_key]: val }))}
                  placeholder="Add carrier…" width={190} ariaLabel="Assign a carrier" />
                <button className="btn" style={{ fontSize: 12 }} disabled={busy} onClick={() => addCarrier(v)}>Add</button>
              </div>
            </div>
            )}

            {/* detection source */}
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>How a financed sale is recognised</div>
              <select style={{ ...sel, width: '100%' }} value={String(value(v, 'detection_source'))}
                onChange={e => patch(v.vendor_key, { detection_source: e.target.value })}>
                {(d?.vocabulary.detection_sources || []).map(s =>
                  <option key={s} value={s}>{(multi && s === 'acima_config') ? 'Inherit this tenant’s existing lease-to-own tender mapping' : (SOURCE_LABEL[s] || s)}</option>)}
              </select>
              {String(value(v, 'detection_source')) === 'plan_rule' && (
                <div style={{ marginTop: 8 }}>
                  <EntityPicker multi options={ruleOpts}
                    value={(value(v, 'detection_ref') as any)?.rule_ids || []}
                    onChange={ids => patch(v.vendor_key, { detection_ref: { rule_ids: ids } })}
                    placeholder="Pick the pay rule(s)…" width={280} ariaLabel="Inherit detection from a pay rule" />
                  <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 4 }}>
                    Reusing the rule that already pays this vendor means the report and the payout can
                    never disagree about what a financed sale is. Remember to Save.
                  </div>
                </div>
              )}
              {String(value(v, 'detection_source')) === 'acima_config' && (
                <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 6 }}>
                  {multi
                    ? (d?.acima_configured ? 'Inheriting this tenant’s existing tender mapping.' : 'Built-in fallback — nothing mapped yet.')
                    : <>Currently mapped: {(d?.acima_tenders || []).map(t => `“${t}”`).join(', ')}{d?.acima_configured ? '' : ' (built-in fallback — nothing mapped yet)'}</>}
                </div>
              )}
              <div style={{ fontSize: 12, fontWeight: 600, margin: '12px 0 6px' }}>Financed amount shown as</div>
              <select style={{ ...sel, width: '100%' }} value={String(value(v, 'amount_basis'))}
                onChange={e => patch(v.vendor_key, { amount_basis: e.target.value })}>
                {(d?.vocabulary.amount_bases || []).map(b => <option key={b} value={b}>{BASIS_LABEL[b] || b}</option>)}
              </select>
            </div>
          </div>

          {/* own detection rules */}
          {String(value(v, 'detection_source')) === 'rules' && (
            <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Detection rules</div>
              {(v.matchers || []).filter(m => m.source !== 'plan_rule' && m.source !== 'acima_config').map((m, i) => (
                <div key={m.id || i} style={{ fontSize: 12.5, marginBottom: 4 }}>
                  <code>{m.match_field}</code> {m.match_op} <b>“{m.match_value}”</b>
                  {m.id && <button className="btn" style={{ fontSize: 11, marginLeft: 8, padding: '1px 8px' }}
                    onClick={() => removeRule(m.id)}>remove</button>}
                  {m.field_warning && <div style={{ color: '#b45309', fontSize: 11.5 }}>⚠️ {m.field_warning}</div>}
                </div>
              ))}
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 8 }}>
                <select style={sel} value={rf(v.vendor_key).match_field}
                  onChange={e => setRf(v.vendor_key, { match_field: e.target.value })}>
                  {(d?.vocabulary.match_fields || []).map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
                </select>
                <select style={sel} value={rf(v.vendor_key).match_op}
                  onChange={e => setRf(v.vendor_key, { match_op: e.target.value })}>
                  {(d?.vocabulary.match_ops || []).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                {/* pick-don't-type: the tender strings this period's data actually contains */}
                {rf(v.vendor_key).match_field === 'tender_type' && tenders.length > 0 ? (
                  <select style={{ ...sel, minWidth: 220 }} value={rf(v.vendor_key).match_value}
                    onChange={e => setRf(v.vendor_key, { match_value: e.target.value })}>
                    <option value="">Pick a tender value seen in {period}…</option>
                    {tenders.map(t => <option key={t.value} value={t.value}>{t.value} ({t.transactions} txn)</option>)}
                  </select>
                ) : (
                  <input className="input" style={{ width: 220 }} placeholder="value to match"
                    value={rf(v.vendor_key).match_value}
                    onChange={e => setRf(v.vendor_key, { match_value: e.target.value })} />
                )}
                <button className="btn" style={{ fontSize: 12 }} disabled={busy} onClick={() => addRule(v)}>+ Add rule</button>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 6 }}>
                Financing is a <b>payment method</b> — Tender type is the reliable signal. A rule on the
                product description will also count every device whose <b>model name</b> contains the word
                (“edge” is a real word inside “MOTOROLA EDGE 50”), which is exactly how a financing bucket
                gets over-counted.
              </div>
            </div>
          )}
        </div>
      ))}

      <div className="card" style={card}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Add another financing vendor</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input className="input" style={{ width: 160 }} placeholder="key (e.g. affirm)" value={newKey}
            onChange={e => setNewKey(e.target.value)} />
          <input className="input" style={{ width: 240 }} placeholder="Name shown on the report" value={newLabel}
            onChange={e => setNewLabel(e.target.value)} />
          <button className="btn btn-primary" style={{ fontSize: 12 }} disabled={busy} onClick={addVendor}>+ Add vendor</button>
        </div>
      </div>
    </div>
  )
}
