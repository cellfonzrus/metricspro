'use client'
// Carrier Documents (index §39) — the platform super admin's list of the documents a new tenant uploads at setup,
// per carrier. Owner 2026-09-27: "create a list of documents which need to be uploaded for each carrier … as a part
// of super admin console and attach them by default when the tenant is set up".
//
// Every row here IS a house row of the report-kind registry (commcalc.report_kind, mig 1010 + the mig-1028 setup
// columns): a tenant inherits it at read time, so "attached by default" needs no copy. Which carrier / POS /
// business type it applies to, whether it is required, where it is downloaded from, how often it is due and how much
// automation history unlocks the "add your login" offer — all DATA, edited here. This file names no carrier.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { isPlatformAdmin } from '@/lib/rbac'

type Proof = { proven: boolean; runs: number; needed: number; window_days: number; how: string | null }
type Kind = {
  key: string; label: string; what_in_it: string | null; source_hint: string | null; landing: string
  upload_types: string[]; applies_to_carrier: string[]; applies_to_pos: string[]; applies_to_vertical: string[]
  is_active: boolean; sort_order: number; required: boolean; default_cadence: string
  download_url: string | null; download_steps: string | null; evidence_table: string | null
  automation_min_runs: number; automation_window_days: number; automation: Proof; setup_ready: boolean
}
type Payload = { kinds: Kind[]; cadences: string[]; landings: string[]; setup_ready: boolean; migration: string }
type TenantRow = { org_id: string; name: string }

const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, padding: 14, background: 'var(--surface)', marginBottom: 12 }
const inp: React.CSSProperties = { padding: '6px 9px', fontSize: 13, border: '1px solid var(--border)', borderRadius: 7, width: '100%', boxSizing: 'border-box' }
const btn: React.CSSProperties = { padding: '6px 14px', fontSize: 13, borderRadius: 7, border: '1px solid var(--border)', cursor: 'pointer', background: 'var(--surface)' }
const lbl: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', display: 'block', marginBottom: 3 }
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))
const list = (s: string) => s.split(/[,\s]+/).map(x => x.trim()).filter(Boolean)
const carrierGroup = (k: Kind) => (k.applies_to_carrier.length ? k.applies_to_carrier.join(' / ') : 'Every carrier')

function ProofBadge({ p }: { p: Proof }) {
  const ok = p.proven
  return (
    <span title={`Successful automated runs (any company) in the last ${p.window_days} days: ${p.runs}. Needed before the setup wizard offers a login: ${p.needed}.`}
      style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, whiteSpace: 'nowrap',
        color: ok ? '#15803d' : 'var(--text3)', background: ok ? '#f0fdf4' : 'var(--surface2)' }}>
      {ok ? `✓ automation proven (${p.runs}/${p.needed}${p.how ? ' · ' + p.how : ''})` : `manual only (${p.runs}/${p.needed} runs)`}
    </span>
  )
}

function KindEditor({ k, cadences, onSaved }: { k: Kind; cadences: string[]; onSaved: () => void }) {
  const [d, setD] = useState<Kind>(k)
  const [carriers, setCarriers] = useState(k.applies_to_carrier.join(', '))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const dirty = JSON.stringify(d) !== JSON.stringify(k) || carriers !== k.applies_to_carrier.join(', ')
  async function save() {
    setBusy(true); setMsg('')
    try {
      await api(`/api/v1/commcalc/report-kinds/house/${encodeURIComponent(k.key)}`, {
        method: 'PUT', body: JSON.stringify({
          label: d.label, what_in_it: d.what_in_it, source_hint: d.source_hint, required: d.required,
          default_cadence: d.default_cadence, download_url: d.download_url, download_steps: d.download_steps,
          automation_min_runs: d.automation_min_runs, automation_window_days: d.automation_window_days,
          is_active: d.is_active, applies_to_carrier: list(carriers),
        }),
      })
      setMsg('✓ Saved — every company on this carrier sees the change.'); onSaved()
    } catch (e) { setMsg('❌ ' + errText(e)) } finally { setBusy(false) }
  }
  return (
    <div style={{ ...card, opacity: d.is_active ? 1 : 0.6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>
          {d.label} <span style={{ fontWeight: 400, fontSize: 11, color: 'var(--text3)', fontFamily: 'monospace' }}>{k.key}</span>
        </div>
        <ProofBadge p={k.automation} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10, marginTop: 10 }}>
        <label><span style={lbl}>Required at setup</span>
          <input type="checkbox" checked={d.required} onChange={e => setD({ ...d, required: e.target.checked })} /> {d.required ? 'Required' : 'Optional'}</label>
        <label><span style={lbl}>Default upload frequency</span>
          <select style={inp} value={d.default_cadence} onChange={e => setD({ ...d, default_cadence: e.target.value })}>
            {cadences.map(c => <option key={c} value={c}>{c}</option>)}
          </select></label>
        <label><span style={lbl}>Carriers (codes, blank = every carrier)</span>
          <input style={inp} value={carriers} onChange={e => setCarriers(e.target.value)} placeholder="e.g. a carrier code" /></label>
        <label><span style={lbl}>Active</span>
          <input type="checkbox" checked={d.is_active} onChange={e => setD({ ...d, is_active: e.target.checked })} /> {d.is_active ? 'Shown' : 'Switched off'}</label>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10, marginTop: 10 }}>
        <label><span style={lbl}>Name the tenant sees</span>
          <input style={inp} value={d.label} onChange={e => setD({ ...d, label: e.target.value })} /></label>
        <label><span style={lbl}>Download link (portal)</span>
          <input style={inp} value={d.download_url || ''} placeholder="https://…" onChange={e => setD({ ...d, download_url: e.target.value })} /></label>
      </div>
      <label style={{ display: 'block', marginTop: 10 }}><span style={lbl}>Where / how to download it (shown in the setup wizard)</span>
        <textarea style={{ ...inp, minHeight: 56 }} value={d.download_steps || ''} onChange={e => setD({ ...d, download_steps: e.target.value })} /></label>
      <label style={{ display: 'block', marginTop: 10 }}><span style={lbl}>What is in it</span>
        <textarea style={{ ...inp, minHeight: 40 }} value={d.what_in_it || ''} onChange={e => setD({ ...d, what_in_it: e.target.value })} /></label>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap', marginTop: 10 }}>
        <label style={{ width: 170 }}><span style={lbl}>Automation: successful runs needed</span>
          <input style={inp} type="number" min={1} max={1000} value={d.automation_min_runs}
            onChange={e => setD({ ...d, automation_min_runs: Number(e.target.value) || 1 })} /></label>
        <label style={{ width: 170 }}><span style={lbl}>…within this many days</span>
          <input style={inp} type="number" min={1} max={365} value={d.automation_window_days}
            onChange={e => setD({ ...d, automation_window_days: Number(e.target.value) || 1 })} /></label>
        <div style={{ fontSize: 11, color: 'var(--text3)', flex: 1, minWidth: 200 }}>
          Uploads through: {k.upload_types.length ? k.upload_types.join(', ') : (k.evidence_table ? `its own page (arrival read from ${k.evidence_table})` : 'the guided intake')}
          {k.applies_to_pos.length ? ` · POS: ${k.applies_to_pos.join(', ')}` : ''}{k.applies_to_vertical.length ? ` · business type: ${k.applies_to_vertical.join(', ')}` : ''}
        </div>
        <button style={{ ...btn, fontWeight: 600 }} disabled={busy || !dirty} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
      </div>
      {msg && <div style={{ fontSize: 12, marginTop: 6, color: msg.startsWith('❌') ? '#b91c1c' : '#15803d' }}>{msg}</div>}
    </div>
  )
}

export default function CarrierDocumentsPage() {
  const { user } = useAuth()
  const platform = isPlatformAdmin(user)
  const [data, setData] = useState<Payload | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [onlyRequired, setOnlyRequired] = useState(false)
  const [tenants, setTenants] = useState<TenantRow[]>([])
  const [reopenOrg, setReopenOrg] = useState('')
  const [reopenMsg, setReopenMsg] = useState('')
  const [newKind, setNewKind] = useState({ label: '', carriers: '', upload_types: '' })
  const [addMsg, setAddMsg] = useState('')

  useEffect(() => {
    if (!platform) return
    let alive = true
    api('/api/v1/commcalc/report-kinds/house')
      .then((d: Payload) => { if (alive) { setData(d); setErr('') } })
      .catch((e: unknown) => { if (alive) setErr(errText(e)) })
    return () => { alive = false }
  }, [platform, tick])
  useEffect(() => {
    if (!platform) return
    let alive = true
    api('/api/v1/core/tenants')
      .then((r: { tenants?: { org_id: string; name?: string }[] }) => {
        if (alive) setTenants((r?.tenants || []).map(t => ({ org_id: t.org_id, name: t.name || t.org_id })))
      })
      .catch(() => { if (alive) setTenants([]) })
    return () => { alive = false }
  }, [platform])
  const reload = useCallback(() => setTick(t => t + 1), [])

  const groups = useMemo(() => {
    const m = new Map<string, Kind[]>()
    for (const k of data?.kinds || []) {
      if (onlyRequired && !k.required) continue
      const g = carrierGroup(k)
      m.set(g, [...(m.get(g) || []), k])
    }
    return Array.from(m.entries()).sort((a, b) => (a[0] === 'Every carrier' ? -1 : b[0] === 'Every carrier' ? 1 : a[0].localeCompare(b[0])))
  }, [data, onlyRequired])

  async function reopen() {
    if (!reopenOrg) return
    setReopenMsg('')
    try {
      await api('/api/v1/commcalc/setup-documents/reopen', { method: 'POST', body: JSON.stringify({ org_id: reopenOrg }) })
      setReopenMsg('✓ That company’s admins will be walked through the setup wizard on their next page load.')
    } catch (e) { setReopenMsg('❌ ' + errText(e)) }
  }
  async function addKind() {
    setAddMsg('')
    try {
      await api('/api/v1/commcalc/report-kinds/house', {
        method: 'POST', body: JSON.stringify({
          label: newKind.label, applies_to_carrier: list(newKind.carriers), upload_types: list(newKind.upload_types),
          required: true,
        }),
      })
      setNewKind({ label: '', carriers: '', upload_types: '' }); setAddMsg('✓ Added.'); reload()
    } catch (e) { setAddMsg('❌ ' + errText(e)) }
  }

  if (!platform) {
    return <div className="card" style={{ padding: 24, maxWidth: 560 }}>This page is for the platform super admin.</div>
  }
  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📑 Carrier Documents</h1>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 14px', maxWidth: 820 }}>
        The documents every new company uploads at setup, per carrier. Each company is given the ones that match its
        carrier, POS and business type automatically. Its admins are walked to the Upload Wizard until the required ones
        are in, and then reminded on the schedule they pick. A login for automatic updates is only offered once that
        document has been pulled automatically, successfully, often enough (per row below).
      </p>
      {err && <div style={{ color: '#b91c1c', marginBottom: 10 }}>❌ {err}</div>}
      {data && !data.setup_ready && (
        <div style={{ ...card, background: '#fffbeb', borderColor: '#fcd34d', fontSize: 13 }}>
          The setup columns are not in the database yet — apply migration <b>{data.migration}</b>. Until then nothing is
          required and no company is redirected.
        </div>
      )}
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <label style={{ fontSize: 13 }}><input type="checkbox" checked={onlyRequired} onChange={e => setOnlyRequired(e.target.checked)} /> Show required documents only</label>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{data ? `${data.kinds.filter(k => k.required).length} required · ${data.kinds.length} in total` : 'Loading…'}</span>
      </div>
      {groups.map(([g, ks]) => (
        <section key={g} style={{ marginBottom: 18 }}>
          <div style={{ fontSize: 15, fontWeight: 700, margin: '4px 0 8px' }}>{g}</div>
          {ks.map(k => <KindEditor key={k.key + tick} k={k} cadences={data?.cadences || []} onSaved={reload} />)}
        </section>
      ))}
      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Add a document</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10 }}>
          <label><span style={lbl}>Name</span><input style={inp} value={newKind.label} onChange={e => setNewKind({ ...newKind, label: e.target.value })} /></label>
          <label><span style={lbl}>Carriers (codes, blank = every carrier)</span><input style={inp} value={newKind.carriers} onChange={e => setNewKind({ ...newKind, carriers: e.target.value })} /></label>
          <label><span style={lbl}>Upload route keys (from the Upload page)</span><input style={inp} value={newKind.upload_types} onChange={e => setNewKind({ ...newKind, upload_types: e.target.value })} /></label>
        </div>
        <button style={{ ...btn, marginTop: 10 }} disabled={!newKind.label.trim()} onClick={addKind}>＋ Add as a required document</button>
        {addMsg && <span style={{ fontSize: 12, marginLeft: 10, color: addMsg.startsWith('❌') ? '#b91c1c' : '#15803d' }}>{addMsg}</span>}
      </div>
      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Walk a company through setup again</div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <select style={{ ...inp, width: 300 }} value={reopenOrg} onChange={e => setReopenOrg(e.target.value)}>
            <option value="">Pick a company…</option>
            {tenants.map(t => <option key={t.org_id} value={t.org_id}>{t.name}</option>)}
          </select>
          <button style={btn} disabled={!reopenOrg} onClick={reopen}>Re-open its setup wizard</button>
        </div>
        {reopenMsg && <div style={{ fontSize: 12, marginTop: 6, color: reopenMsg.startsWith('❌') ? '#b91c1c' : '#15803d' }}>{reopenMsg}</div>}
      </div>
    </div>
  )
}
