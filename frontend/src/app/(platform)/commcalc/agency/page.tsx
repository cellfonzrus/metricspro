'use client'
// Agency (Master → Sub) — Phase 1 master-side console: Links · Rules · Charges · Transfers · Invoices.
// Pick-don't-type everywhere (§3b via EntityPicker); ReportExportBar on the invoice list/detail (RULE FOUR);
// StandardFilterBar on the period-dimensioned lists (RULE FIVE). Sub-portal visibility = Phase 3 (nothing
// sub-facing here). All money math is server-side (commcalc/agency*.py) — this page is CRUD + display.
import { useEffect, useMemo, useState } from 'react'
import { api, apiUpload, fmt } from '@/lib/client'
import EntityPicker, { type EntityOption } from '@/components/EntityPicker'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'

const TABS = ['Links', 'Rules', 'Charges', 'Transfers', 'Invoices'] as const
type Tab = typeof TABS[number]

const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, padding: 16, background: 'var(--surface)', marginBottom: 16 }
const th: React.CSSProperties = { textAlign: 'left', padding: '6px 10px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)' }
const td: React.CSSProperties = { padding: '6px 10px', fontSize: 13, borderBottom: '1px solid var(--border)' }
const btn: React.CSSProperties = { padding: '6px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', cursor: 'pointer', fontSize: 13 }
const btnP: React.CSSProperties = { ...btn, background: 'var(--accent, #2563eb)', color: '#fff', borderColor: 'transparent' }
const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

const money = (n: any) => fmt(Number(n) || 0)

export default function AgencyPage() {
  const [tab, setTab] = useState<Tab>('Links')
  const [links, setLinks] = useState<any[]>([])
  const [selId, setSelId] = useState<string>('')
  const [err, setErr] = useState<string>('')
  const [notice, setNotice] = useState<string>('')

  const loadLinks = () => api('/api/v1/commcalc/agency/links').then((d: any) => setLinks(d.links || [])).catch((e: any) => setErr(String(e.message || e)))
  useEffect(() => { loadLinks() }, [])
  const sel = useMemo(() => links.find(l => l.id === selId) || null, [links, selId])

  return (
    <div style={{ padding: 24, maxWidth: 1180 }}>
      <h1 style={{ fontSize: 22, marginBottom: 4 }}>Agency — Master → Sub</h1>
      <div style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 16 }}>
        Configure sub-agent relationships, holdback / equipment-margin / charge rules, equipment transfers, and generate agency invoices.
      </div>
      {err && <div style={{ ...card, borderColor: '#dc2626', color: '#dc2626' }}>{err} <button style={btn} onClick={() => setErr('')}>dismiss</button></div>}
      {notice && <div style={{ ...card, borderColor: '#d97706', color: '#b45309' }}>{notice} <button style={btn} onClick={() => setNotice('')}>ok</button></div>}

      <div style={{ display: 'flex', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button key={t} style={t === tab ? btnP : btn} onClick={() => setTab(t)}>{t}</button>
        ))}
        {sel && <span style={{ fontSize: 13, color: 'var(--text2)', alignSelf: 'center', marginLeft: 8 }}>Active link: <b>{sel.sub_name}</b></span>}
      </div>

      {tab === 'Links' && <LinksTab links={links} selId={selId} setSelId={setSelId} reload={loadLinks} onErr={setErr} />}
      {tab !== 'Links' && !sel && <div style={card}>Pick a link on the <b>Links</b> tab first.</div>}
      {tab === 'Rules' && sel && <RulesTab link={sel} onErr={setErr} />}
      {tab === 'Charges' && sel && <ChargesTab link={sel} onErr={setErr} />}
      {tab === 'Transfers' && sel && <TransfersTab link={sel} onErr={setErr} onNotice={setNotice} />}
      {tab === 'Invoices' && sel && <InvoicesTab link={sel} onErr={setErr} />}
    </div>
  )
}

// ── Links ─────────────────────────────────────────────────────────────────────────────────────────────
function LinksTab({ links, selId, setSelId, reload, onErr }: any) {
  const [tenants, setTenants] = useState<any[]>([])
  const [carriers, setCarriers] = useState<EntityOption[]>([])
  const [kind, setKind] = useState<'tenant' | 'external'>('tenant')
  const [subOrg, setSubOrg] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [taxable, setTaxable] = useState(false)
  const [rate, setRate] = useState('0')

  useEffect(() => {
    api('/api/v1/commcalc/agency/sub-candidates').then((d: any) => setTenants(d.tenants || [])).catch(() => {})
    api('/api/v1/commcalc/agency/scope-options').then((d: any) => setCarriers((d.carriers || []).map((c: any) => ({ id: c.id, label: c.name })))).catch(() => {})
  }, [])
  const tenantOpts: EntityOption[] = tenants.map(t => ({ id: t.org_id, label: t.name, sublabel: t.slug || undefined }))

  const create = async () => {
    try {
      const body: any = { sub_kind: kind, sub_name: kind === 'tenant' ? (tenants.find(t => t.org_id === subOrg)?.name || name) : name, taxable, tax_rate: Number(rate) || 0 }
      if (kind === 'tenant') body.sub_org_id = subOrg
      const r = await api('/api/v1/commcalc/agency/links', { method: 'POST', body: JSON.stringify(body) })
      setName(''); setSubOrg(null)
      reload()
      if (r?.link?.id) setSelId(r.link.id)
    } catch (e: any) { onErr(String(e.message || e)) }
  }

  return (
    <>
      <div style={card}>
        <div style={{ fontWeight: 600, marginBottom: 10 }}>New link</div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <select style={inp} value={kind} onChange={e => setKind(e.target.value as any)}>
            <option value="tenant">Tenant sub</option>
            <option value="external">External party</option>
          </select>
          {kind === 'tenant'
            ? <EntityPicker options={tenantOpts} value={subOrg} onChange={setSubOrg} placeholder="Pick a tenant…" width={240} ariaLabel="Sub tenant" />
            : <input style={{ ...inp, width: 240 }} placeholder="External party name" value={name} onChange={e => setName(e.target.value)} />}
          <label style={{ fontSize: 13, display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            <input type="checkbox" checked={taxable} onChange={e => setTaxable(e.target.checked)} /> Taxable
          </label>
          {taxable && <input style={{ ...inp, width: 90 }} placeholder="rate e.g. 0.06" value={rate} onChange={e => setRate(e.target.value)} />}
          <button style={btnP} onClick={create} disabled={kind === 'tenant' ? !subOrg : !name.trim()}>Create link</button>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 8 }}>Wholesale (unchecked) = no tax. A cycle (the sub is already your master upstream) is refused.</div>
      </div>

      <div style={card}>
        <div style={{ fontWeight: 600, marginBottom: 10 }}>Links</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Sub</th><th style={th}>Kind</th><th style={th}>Status</th><th style={th}>Consent</th><th style={th}>Stores</th><th style={th}>Taxable</th><th style={th}></th></tr></thead>
          <tbody>
            {links.map((l: any) => (
              <tr key={l.id} style={{ background: l.id === selId ? 'var(--surface2, #eef2ff)' : undefined }}>
                <td style={td}>{l.sub_name}</td>
                <td style={td}>{l.sub_kind}</td>
                <td style={td}>{l.status}</td>
                <td style={td}>{l.sub_consent_status}</td>
                <td style={td}>{l.store_count ?? 0}</td>
                <td style={td}>{l.taxable ? `${(Number(l.tax_rate) * 100).toFixed(2)}%` : 'wholesale'}</td>
                <td style={td}><button style={btn} onClick={() => setSelId(l.id)}>{l.id === selId ? 'selected' : 'select'}</button></td>
              </tr>
            ))}
            {!links.length && <tr><td style={td} colSpan={7}>No links yet.</td></tr>}
          </tbody>
        </table>
        {selId && <LinkDetail linkId={selId} link={links.find((l: any) => l.id === selId)} carriers={carriers} reload={reload} onErr={onErr} />}
      </div>
    </>
  )
}

function LinkDetail({ linkId, link, carriers, reload, onErr }: any) {
  const [selCarr, setSelCarr] = useState<string[]>([])
  const [roster, setRoster] = useState<any[]>([])
  const [cand, setCand] = useState<any>(null)
  const loadDetail = () => {
    api(`/api/v1/commcalc/agency/links/${linkId}`).then((d: any) => setSelCarr((d.carriers || []).map((c: any) => c.carrier_id))).catch(() => {})
    api(`/api/v1/commcalc/agency/links/${linkId}/stores`).then((d: any) => setRoster(d.stores || [])).catch(() => {})
    api(`/api/v1/commcalc/agency/links/${linkId}/store-candidates`).then((d: any) => setCand(d)).catch(() => {})
  }
  useEffect(() => { loadDetail() }, [linkId])

  const saveCarriers = async (ids: string[]) => {
    setSelCarr(ids)
    try { await api(`/api/v1/commcalc/agency/links/${linkId}/carriers`, { method: 'POST', body: JSON.stringify({ carrier_ids: ids }) }) } catch (e: any) { onErr(String(e.message || e)) }
  }
  const setConsent = async (status: string) => {
    try { await api(`/api/v1/commcalc/agency/links/${linkId}/consent`, { method: 'POST', body: JSON.stringify({ status }) }); reload(); loadDetail() } catch (e: any) { onErr(String(e.message || e)) }
  }
  const addStore = async (body: any) => {
    try { await api(`/api/v1/commcalc/agency/links/${linkId}/stores`, { method: 'POST', body: JSON.stringify(body) }); loadDetail(); reload() } catch (e: any) { onErr(String(e.message || e)) }
  }

  return (
    <div style={{ marginTop: 16, borderTop: '1px dashed var(--border)', paddingTop: 12 }}>
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: 12, color: 'var(--text2)' }}>Carrier scope (empty = all carriers)</div>
          <EntityPicker multi options={carriers} value={selCarr} onChange={saveCarriers} placeholder="Carriers…" width={240} ariaLabel="Carrier scope" />
        </div>
        <div>
          <div style={{ fontSize: 12, color: 'var(--text2)' }}>Consent: <b>{link?.sub_consent_status}</b></div>
          <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
            <button style={btn} onClick={() => setConsent('pending')}>Request</button>
            <button style={btn} onClick={() => setConsent('accepted')} title="records offline consent — Phase 3 = sub-side accept">Record accepted</button>
            <button style={btn} onClick={() => setConsent('revoked')}>Revoke</button>
          </div>
        </div>
      </div>
      <div style={{ marginTop: 14, fontWeight: 600 }}>Store roster</div>
      {cand && !cand.consented && <div style={{ fontSize: 12, color: '#b45309', margin: '4px 0' }}>{cand.reason || 'manual entry only'} — add stores manually below.</div>}
      {cand && cand.consented && <ConsentedStorePicker stores={cand.stores} onAdd={(s: any) => addStore({ store_kind: 'storeops', store_id: s.store_id, store_code: s.store_code, store_address: s.store_address })} />}
      <ManualStoreAdd onAdd={addStore} />
      <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8 }}>
        <thead><tr><th style={th}>Store</th><th style={th}>Code</th><th style={th}>Effective</th></tr></thead>
        <tbody>
          {roster.map((s: any) => <tr key={s.id}><td style={td}>{s.store_label || s.store_address || s.store_code}</td><td style={td}>{s.store_code}</td><td style={td}>{s.effective_start || '—'}</td></tr>)}
          {!roster.length && <tr><td style={td} colSpan={3}>No roster stores.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function ConsentedStorePicker({ stores, onAdd }: any) {
  const [pick, setPick] = useState<string | null>(null)
  const opts: EntityOption[] = (stores || []).map((s: any) => ({ id: String(s.store_id), label: s.store_address || s.store_code, sublabel: s.market || undefined }))
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '6px 0' }}>
      <EntityPicker options={opts} value={pick} onChange={setPick} placeholder="Pick a sub store…" width={260} ariaLabel="Sub store" />
      <button style={btn} disabled={!pick} onClick={() => { const s = stores.find((x: any) => String(x.store_id) === pick); if (s) { onAdd(s); setPick(null) } }}>Add to roster</button>
    </div>
  )
}

function ManualStoreAdd({ onAdd }: any) {
  const [label, setLabel] = useState(''); const [code, setCode] = useState(''); const [eff, setEff] = useState('')
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '6px 0', flexWrap: 'wrap' }}>
      <input style={{ ...inp, width: 180 }} placeholder="Store label" value={label} onChange={e => setLabel(e.target.value)} />
      <input style={{ ...inp, width: 120 }} placeholder="Store code" value={code} onChange={e => setCode(e.target.value)} />
      <input style={inp} type="date" value={eff} onChange={e => setEff(e.target.value)} title="effective start (proration)" />
      <button style={btn} disabled={!label && !code} onClick={() => { onAdd({ store_kind: 'external', store_label: label || undefined, store_code: code || undefined, effective_start: eff || undefined }); setLabel(''); setCode(''); setEff('') }}>Add manual store</button>
    </div>
  )
}

// ── Rules (holdback + equipment margin) ───────────────────────────────────────────────────────────────
function RulesTab({ link, onErr }: any) {
  const [opts, setOpts] = useState<any>({ ledger_bucket: [], commission_component: [], statement_line_type: [], product_class: [], carriers: [] })
  const [rules, setRules] = useState<any[]>([])
  const [margins, setMargins] = useState<any[]>([])
  const load = () => {
    api('/api/v1/commcalc/agency/scope-options').then(setOpts).catch(() => {})
    api(`/api/v1/commcalc/agency/links/${link.id}/holdback-rules`).then((d: any) => setRules(d.rules || [])).catch(() => {})
    api(`/api/v1/commcalc/agency/links/${link.id}/equipment-margins`).then((d: any) => setMargins(d.margins || [])).catch(() => {})
  }
  useEffect(() => { load() }, [link.id])

  return (
    <>
      <div style={card}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Holdback rules <span style={{ fontSize: 12, color: 'var(--text2)' }}>(Phase 2 nets these in settlement)</span></div>
        <HoldbackForm link={link} opts={opts} onSaved={load} onErr={onErr} />
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8 }}>
          <thead><tr><th style={th}>Scope</th><th style={th}>Value</th><th style={th}>Method</th><th style={th}>Amount</th><th style={th}>Per</th><th style={th}>Prio</th><th style={th}></th></tr></thead>
          <tbody>
            {rules.map((r: any) => (
              <tr key={r.id}>
                <td style={td}>{r.scope_kind}</td><td style={td}>{r.scope_value || '—'}</td><td style={td}>{r.method}</td>
                <td style={td}>{r.method === 'percent' ? `${(Number(r.value) * 100).toFixed(2)}%` : money(r.value)}</td>
                <td style={td}>{r.flat_per}</td><td style={td}>{r.priority}</td>
                <td style={td}><button style={btn} onClick={async () => { try { await api(`/api/v1/commcalc/agency/links/${link.id}/holdback-rules/${r.id}`, { method: 'DELETE' }); load() } catch (e: any) { onErr(String(e.message || e)) } }}>del</button></td>
              </tr>
            ))}
            {!rules.length && <tr><td style={td} colSpan={7}>No holdback rules.</td></tr>}
          </tbody>
        </table>
      </div>

      <div style={card}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Equipment margin (markup billed on transfers)</div>
        <MarginForm link={link} opts={opts} onSaved={load} onErr={onErr} />
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8 }}>
          <thead><tr><th style={th}>Class</th><th style={th}>Method</th><th style={th}>Value</th><th style={th}>Basis</th><th style={th}>Prio</th><th style={th}></th></tr></thead>
          <tbody>
            {margins.map((m: any) => (
              <tr key={m.id}>
                <td style={td}>{m.equip_class_value}</td><td style={td}>{m.method}</td>
                <td style={td}>{m.method === 'percent' ? `${(Number(m.value) * 100).toFixed(2)}%` : money(m.value)}</td>
                <td style={td}>{m.markup_basis}</td><td style={td}>{m.priority}</td>
                <td style={td}><button style={btn} onClick={async () => { try { await api(`/api/v1/commcalc/agency/links/${link.id}/equipment-margins/${m.id}`, { method: 'DELETE' }); load() } catch (e: any) { onErr(String(e.message || e)) } }}>del</button></td>
              </tr>
            ))}
            {!margins.length && <tr><td style={td} colSpan={6}>No equipment margins.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}

function HoldbackForm({ link, opts, onSaved, onErr }: any) {
  const [scope, setScope] = useState('all')
  const [val, setVal] = useState<string | null>(null)
  const [method, setMethod] = useState('percent')
  const [amount, setAmount] = useState('0.1')
  const [flatPer, setFlatPer] = useState('activation')
  const valueOpts: EntityOption[] = (opts[scope] || []).map((v: any) => ({ id: String(v), label: String(v) }))
  const needsValue = ['ledger_bucket', 'commission_component', 'statement_line_type', 'product_class'].includes(scope)
  const save = async () => {
    try {
      await api(`/api/v1/commcalc/agency/links/${link.id}/holdback-rules`, { method: 'POST', body: JSON.stringify({ scope_kind: scope, scope_value: needsValue ? val : null, method, value: Number(amount) || 0, flat_per: flatPer }) })
      setVal(null); onSaved()
    } catch (e: any) { onErr(String(e.message || e)) }
  }
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
      <select style={inp} value={scope} onChange={e => { setScope(e.target.value); setVal(null) }}>
        {['all', 'ledger_bucket', 'commission_component', 'statement_line_type', 'product_class', 'carrier'].map(s => <option key={s} value={s}>{s}</option>)}
      </select>
      {needsValue && <EntityPicker options={valueOpts} value={val} onChange={setVal} placeholder="scope value…" width={200} ariaLabel="scope value" />}
      <select style={inp} value={method} onChange={e => setMethod(e.target.value)}><option value="percent">percent</option><option value="flat">flat $</option></select>
      <input style={{ ...inp, width: 100 }} value={amount} onChange={e => setAmount(e.target.value)} placeholder={method === 'percent' ? '0.10' : '$'} />
      {method === 'flat' && <select style={inp} value={flatPer} onChange={e => setFlatPer(e.target.value)}>{['activation', 'line_item', 'invoice'].map(p => <option key={p} value={p}>per {p}</option>)}</select>}
      <button style={btnP} disabled={needsValue && !val} onClick={save}>Add rule</button>
    </div>
  )
}

function MarginForm({ link, opts, onSaved, onErr }: any) {
  const [cls, setCls] = useState<string | null>(null)
  const [method, setMethod] = useState('percent')
  const [amount, setAmount] = useState('0.15')
  const [basis, setBasis] = useState('cost')
  const clsOpts: EntityOption[] = (opts.product_class || []).map((v: any) => ({ id: String(v), label: String(v) }))
  const save = async () => {
    try { await api(`/api/v1/commcalc/agency/links/${link.id}/equipment-margins`, { method: 'POST', body: JSON.stringify({ equip_class_value: cls, method, value: Number(amount) || 0, markup_basis: basis }) }); setCls(null); onSaved() } catch (e: any) { onErr(String(e.message || e)) }
  }
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
      <EntityPicker options={clsOpts} value={cls} onChange={setCls} allowCreate onCreate={(v: string) => setCls(v)} placeholder="Equipment class…" width={200} ariaLabel="Equipment class" />
      <select style={inp} value={method} onChange={e => setMethod(e.target.value)}><option value="percent">percent</option><option value="flat">flat $/unit</option></select>
      <input style={{ ...inp, width: 100 }} value={amount} onChange={e => setAmount(e.target.value)} />
      {method === 'percent' && <select style={inp} value={basis} onChange={e => setBasis(e.target.value)}>{['cost', 'ext_price', 'gp'].map(b => <option key={b} value={b}>{b}</option>)}</select>}
      <button style={btnP} disabled={!cls} onClick={save}>Add margin</button>
    </div>
  )
}

// ── Charges ───────────────────────────────────────────────────────────────────────────────────────────
function ChargesTab({ link, onErr }: any) {
  const [charges, setCharges] = useState<any[]>([])
  const [roster, setRoster] = useState<any[]>([])
  const [label, setLabel] = useState(''); const [method, setMethod] = useState('flat'); const [amount, setAmount] = useState('0')
  const [cadence, setCadence] = useState('monthly'); const [proration, setProration] = useState('default')
  const [store, setStore] = useState<string | null>(null); const [pbasis, setPbasis] = useState('invoice_subtotal')
  const load = () => {
    api(`/api/v1/commcalc/agency/links/${link.id}/charges`).then((d: any) => setCharges(d.charges || [])).catch(() => {})
    api(`/api/v1/commcalc/agency/links/${link.id}/stores`).then((d: any) => setRoster(d.stores || [])).catch(() => {})
  }
  useEffect(() => { load() }, [link.id])
  const storeOpts: EntityOption[] = roster.map(s => ({ id: s.id, label: s.store_label || s.store_address || s.store_code }))
  const save = async () => {
    try {
      await api(`/api/v1/commcalc/agency/links/${link.id}/charges`, { method: 'POST', body: JSON.stringify({ label, method, value: Number(amount) || 0, cadence, proration_mode: proration, link_store_id: store || null, percent_basis: method === 'percent' ? pbasis : null }) })
      setLabel(''); setAmount('0'); setStore(null); load()
    } catch (e: any) { onErr(String(e.message || e)) }
  }
  return (
    <div style={card}>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>Charges <span style={{ fontSize: 12, color: 'var(--text2)' }}>(monthly store fee, co-op, one-time…)</span></div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <input style={{ ...inp, width: 170 }} placeholder="Label" value={label} onChange={e => setLabel(e.target.value)} />
        <select style={inp} value={method} onChange={e => setMethod(e.target.value)}><option value="flat">flat $</option><option value="percent">percent</option></select>
        <input style={{ ...inp, width: 90 }} value={amount} onChange={e => setAmount(e.target.value)} />
        {method === 'percent' && <select style={inp} value={pbasis} onChange={e => setPbasis(e.target.value)}>{['invoice_subtotal', 'equipment_margin_total', 'holdback_total'].map(b => <option key={b} value={b}>{b}</option>)}</select>}
        <select style={inp} value={cadence} onChange={e => setCadence(e.target.value)}>{['monthly', 'per_invoice', 'one_time'].map(c => <option key={c} value={c}>{c}</option>)}</select>
        <EntityPicker options={storeOpts} value={store} onChange={setStore} placeholder="store (optional)…" width={180} ariaLabel="charge store" />
        <select style={inp} value={proration} onChange={e => setProration(e.target.value)}>{['default', 'full', 'prorated'].map(p => <option key={p} value={p}>{p}</option>)}</select>
        <button style={btnP} disabled={!label.trim()} onClick={save}>Add charge</button>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10 }}>
        <thead><tr><th style={th}>Label</th><th style={th}>Method</th><th style={th}>Value</th><th style={th}>Cadence</th><th style={th}>Store</th><th style={th}>Proration</th><th style={th}></th></tr></thead>
        <tbody>
          {charges.map((c: any) => (
            <tr key={c.id}>
              <td style={td}>{c.label}</td><td style={td}>{c.method}</td>
              <td style={td}>{c.method === 'percent' ? `${(Number(c.value) * 100).toFixed(2)}%` : money(c.value)}</td>
              <td style={td}>{c.cadence}</td><td style={td}>{roster.find(s => s.id === c.link_store_id)?.store_label || (c.link_store_id ? '(store)' : '—')}</td>
              <td style={td}>{c.proration_mode}</td>
              <td style={td}><button style={btn} onClick={async () => { try { await api(`/api/v1/commcalc/agency/links/${link.id}/charges/${c.id}`, { method: 'DELETE' }); load() } catch (e: any) { onErr(String(e.message || e)) } }}>del</button></td>
            </tr>
          ))}
          {!charges.length && <tr><td style={td} colSpan={7}>No charges.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

// ── Transfers ─────────────────────────────────────────────────────────────────────────────────────────
function TransfersTab({ link, onErr, onNotice }: any) {
  const [filter, setFilter] = useState<StandardFilterValue>(emptyStandardFilter())
  const [rows, setRows] = useState<any[]>([])
  const [ecls, setEcls] = useState(''); const [pdesc, setPdesc] = useState(''); const [qty, setQty] = useState('0'); const [cost, setCost] = useState('0'); const [per, setPer] = useState('')
  const load = () => api(`/api/v1/commcalc/agency/links/${link.id}/transfers${filter.period ? `?period=${filter.period}` : ''}`).then((d: any) => setRows(d.transfers || [])).catch(() => {})
  useEffect(() => { load() }, [link.id, filter.period])
  const addManual = async () => {
    try { await api(`/api/v1/commcalc/agency/links/${link.id}/transfers`, { method: 'POST', body: JSON.stringify({ equip_class_value: ecls, product_desc: pdesc, qty: Number(qty) || 0, unit_cost: Number(cost) || 0, period: per || filter.period || null }) }); setEcls(''); setPdesc(''); setQty('0'); setCost('0'); load() } catch (e: any) { onErr(String(e.message || e)) }
  }
  const upload = async (kind: 'csv' | 'ocr', file: File) => {
    try {
      const form = new FormData(); form.append('file', file)
      if (kind === 'ocr' && (per || filter.period)) form.append('period', per || filter.period || '')
      const r = await apiUpload(`/api/v1/commcalc/agency/links/${link.id}/transfers/upload-${kind}`, form)
      if (r?.notice) onNotice(r.notice)
      load()
    } catch (e: any) { onErr(String(e.message || e)) }
  }
  const decide = async (tid: string, action: 'confirm' | 'reject') => {
    try { await api(`/api/v1/commcalc/agency/transfers/${tid}/${action}`, { method: 'POST' }); load() } catch (e: any) { onErr(String(e.message || e)) }
  }
  return (
    <div style={card}>
      <StandardFilterBar value={filter} onChange={setFilter} show={{ period: true, stores: false, markets: false, reps: false }} periodMode="month" />
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
        <input style={{ ...inp, width: 120 }} placeholder="equip class" value={ecls} onChange={e => setEcls(e.target.value)} />
        <input style={{ ...inp, width: 170 }} placeholder="product desc" value={pdesc} onChange={e => setPdesc(e.target.value)} />
        <input style={{ ...inp, width: 70 }} placeholder="qty" value={qty} onChange={e => setQty(e.target.value)} />
        <input style={{ ...inp, width: 80 }} placeholder="unit cost" value={cost} onChange={e => setCost(e.target.value)} />
        <input style={{ ...inp, width: 110 }} placeholder="period YYYY-MM" value={per} onChange={e => setPer(e.target.value)} />
        <button style={btnP} disabled={!ecls.trim()} onClick={addManual}>Add manual</button>
        <label style={btn}>CSV feed<input type="file" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) upload('csv', f) }} /></label>
        <label style={btn}>OCR invoice<input type="file" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) upload('ocr', f) }} /></label>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead><tr><th style={th}>Period</th><th style={th}>Class</th><th style={th}>Desc</th><th style={th}>Qty</th><th style={th}>Cost</th><th style={th}>Source</th><th style={th}>Status</th><th style={th}></th></tr></thead>
        <tbody>
          {rows.map((t: any) => (
            <tr key={t.id}>
              <td style={td}>{t.period}</td><td style={td}>{t.equip_class_value}</td><td style={td}>{t.product_desc}</td>
              <td style={td}>{t.qty}</td><td style={td}>{money(t.unit_cost)}</td><td style={td}>{t.source}</td>
              <td style={td}>{t.confirm_status}{t.billed_invoice_id ? ' (billed)' : ''}</td>
              <td style={td}>{t.confirm_status === 'unconfirmed' && <span style={{ display: 'inline-flex', gap: 4 }}><button style={btn} onClick={() => decide(t.id, 'confirm')}>confirm</button><button style={btn} onClick={() => decide(t.id, 'reject')}>reject</button></span>}</td>
            </tr>
          ))}
          {!rows.length && <tr><td style={td} colSpan={8}>No transfers.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

// ── Invoices ──────────────────────────────────────────────────────────────────────────────────────────
const INV_COLS: ExportColumn[] = [
  { header: 'Period', get: r => r.period },
  { header: 'Status', get: r => r.status },
  { header: 'Equipment margin', get: r => Number(r.equipment_margin_total) || 0, money: true },
  { header: 'Store fees', get: r => Number(r.store_fee_total) || 0, money: true },
  { header: 'Other', get: r => Number(r.other_charge_total) || 0, money: true },
  { header: 'Subtotal', get: r => Number(r.subtotal) || 0, money: true },
  { header: 'Tax', get: r => Number(r.tax_total) || 0, money: true },
  { header: 'Total', get: r => Number(r.total) || 0, money: true },
]
const LINE_COLS: ExportColumn[] = [
  { header: 'Type', get: r => r.source_type },
  { header: 'Description', get: r => r.description },
  { header: 'Qty', get: r => Number(r.qty) || 0 },
  { header: 'Unit', get: r => Number(r.unit_amount) || 0, money: true },
  { header: 'Proration', get: r => Number(r.proration_factor) || 1 },
  { header: 'Amount', get: r => Number(r.amount) || 0, money: true },
]

function InvoicesTab({ link, onErr }: any) {
  const [filter, setFilter] = useState<StandardFilterValue>(emptyStandardFilter())
  const [invoices, setInvoices] = useState<any[]>([])
  const [detail, setDetail] = useState<any>(null)
  const [genPer, setGenPer] = useState('')
  const load = () => api(`/api/v1/commcalc/agency/invoices?link_id=${link.id}${filter.period ? `&period=${filter.period}` : ''}`).then((d: any) => setInvoices(d.invoices || [])).catch(() => {})
  useEffect(() => { load(); setDetail(null) }, [link.id, filter.period])
  const gen = async () => {
    if (!genPer) return
    try { await api(`/api/v1/commcalc/agency/links/${link.id}/invoices/generate`, { method: 'POST', body: JSON.stringify({ period: genPer }) }); load() } catch (e: any) { onErr(String(e.message || e)) }
  }
  const openDetail = (id: string) => api(`/api/v1/commcalc/agency/invoices/${id}`).then(setDetail).catch((e: any) => onErr(String(e.message || e)))
  const act = async (id: string, action: 'issue' | 'void') => {
    try { await api(`/api/v1/commcalc/agency/invoices/${id}/${action}`, { method: 'POST' }); load(); if (detail?.invoice?.id === id) openDetail(id) } catch (e: any) { onErr(String(e.message || e)) }
  }
  return (
    <>
      <div style={card}>
        <StandardFilterBar value={filter} onChange={setFilter} show={{ period: true, stores: false, markets: false, reps: false }} periodMode="month" right={
          <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            <input style={{ ...inp, width: 120 }} type="month" value={genPer} onChange={e => setGenPer(e.target.value)} />
            <button style={btnP} disabled={!genPer} onClick={gen}>Generate draft</button>
          </span>
        } />
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 6 }}>
          <ReportExportBar title={`Agency invoices — ${link.sub_name}`} filename="agency_invoices" columns={INV_COLS} rows={invoices} />
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Period</th><th style={th}>Status</th><th style={th}>Subtotal</th><th style={th}>Tax</th><th style={th}>Total</th><th style={th}></th></tr></thead>
          <tbody>
            {invoices.map((i: any) => (
              <tr key={i.id}>
                <td style={td}>{i.period}</td><td style={td}>{i.status}</td><td style={td}>{money(i.subtotal)}</td><td style={td}>{money(i.tax_total)}</td><td style={td}><b>{money(i.total)}</b></td>
                <td style={td}>
                  <span style={{ display: 'inline-flex', gap: 4 }}>
                    <button style={btn} onClick={() => openDetail(i.id)}>view</button>
                    {i.status === 'draft' && <button style={btn} onClick={() => act(i.id, 'issue')}>issue</button>}
                    {i.status !== 'void' && <button style={btn} onClick={() => act(i.id, 'void')}>void</button>}
                  </span>
                </td>
              </tr>
            ))}
            {!invoices.length && <tr><td style={td} colSpan={6}>No invoices.</td></tr>}
          </tbody>
        </table>
      </div>

      {detail && (
        <div style={card}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <div style={{ fontWeight: 600 }}>Invoice {detail.invoice.period} — {detail.invoice.status}{detail.invoice.taxable_snapshot ? ` · taxed ${(Number(detail.invoice.tax_rate_snapshot) * 100).toFixed(2)}%` : ' · wholesale'}</div>
            <ReportExportBar title={`Agency invoice — ${link.sub_name} — ${detail.invoice.period}`} filename={`agency_invoice_${detail.invoice.period}`} columns={LINE_COLS} rows={detail.lines} />
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={th}>Type</th><th style={th}>Description</th><th style={th}>Qty</th><th style={th}>Unit</th><th style={th}>Proration</th><th style={th} title="amount">Amount</th></tr></thead>
            <tbody>
              {(detail.lines || []).map((l: any) => (
                <tr key={l.id}><td style={td}>{l.source_type}</td><td style={td}>{l.description}</td><td style={td}>{l.qty}</td><td style={td}>{money(l.unit_amount)}</td><td style={td}>{Number(l.proration_factor) < 1 ? Number(l.proration_factor).toFixed(3) : '—'}</td><td style={td}><b>{money(l.amount)}</b></td></tr>
              ))}
              {!(detail.lines || []).length && <tr><td style={td} colSpan={6}>No lines.</td></tr>}
            </tbody>
          </table>
          <div style={{ textAlign: 'right', marginTop: 8, fontSize: 14 }}>
            Subtotal {money(detail.invoice.subtotal)} · Tax {money(detail.invoice.tax_total)} · <b>Total {money(detail.invoice.total)}</b>
          </div>
        </div>
      )}
    </>
  )
}
