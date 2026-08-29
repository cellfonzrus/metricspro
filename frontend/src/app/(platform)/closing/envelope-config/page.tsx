'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import EntityPicker, { EntityOption } from '@/components/EntityPicker'

// Envelope payout configuration (mig 507, EEP): what management allows to be taken from the daily
// cash envelope (commission / salary / other approved expenses) and on what cadence. One ORG DEFAULT
// row (store_code=null) plus optional PER-STORE overrides. Anything left off is deposited to the bank.
const sel: React.CSSProperties = { padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const CADENCES = [['daily', 'Daily'], ['weekly', 'Weekly'], ['biweekly', 'Biweekly'], ['monthly', 'Monthly']]
const WEEKDAYS = [['0', 'Monday'], ['1', 'Tuesday'], ['2', 'Wednesday'], ['3', 'Thursday'], ['4', 'Friday'], ['5', 'Saturday'], ['6', 'Sunday']]

type Cfg = {
  store_code: string | null
  take_commission: boolean; take_salary: boolean; take_expenses: boolean
  commission_cadence: string; commission_anchor: string; commission_anchor_date: string
  salary_cadence: string; salary_anchor: string; salary_anchor_date: string
  // Q15 (OWNER DIRECTIVE 2026-08-04): fewest-envelopes stays the objective; this only picks which
  // envelope wins a TIE on available cash — 'oldest_first' (default) | 'newest_first'.
  order_preference: string
  // BUG FIX (owner-reported 2026-08-07, mig 510): OFF by default — opt IN to hard-require an
  // envelope photo on any closing that declares cash > 0. See ClosingSubmitForm + POST /closing/row.
  require_photo_if_cash: boolean
}
const blankCfg = (store_code: string | null = null): Cfg => ({
  store_code, take_commission: true, take_salary: true, take_expenses: true,
  commission_cadence: 'weekly', commission_anchor: '', commission_anchor_date: '',
  salary_cadence: 'weekly', salary_anchor: '', salary_anchor_date: '',
  order_preference: 'oldest_first',
  require_photo_if_cash: false,
})

export default function EnvelopeConfigPage() {
  const [orgCfg, setOrgCfg] = useState<Cfg>(blankCfg())
  const [overrides, setOverrides] = useState<Cfg[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [newStore, setNewStore] = useState<string | null>(null)

  useEffect(() => { apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setStores(Array.isArray(s) ? s : (s?.stores || []))).catch(() => {}) }, [])
  const storeOptions: EntityOption[] = stores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code }))
  const storeLabel = (code: string) => storeOptions.find(o => o.id === code)?.label || code

  const load = useCallback(() => {
    api('/api/v1/closing/envelope-config').then((d: any) => {
      setOrgCfg(d?.org_default ? { ...blankCfg(), ...d.org_default } : blankCfg())
      setOverrides((d?.store_overrides || []).map((r: any) => ({ ...blankCfg(r.store_code), ...r })))
    }).catch((e: any) => setMsg('❌ ' + (e?.message || e)))
  }, [])
  useEffect(() => { load() }, [load])

  async function save(cfg: Cfg) {
    setBusy(true); setMsg('')
    try {
      await api('/api/v1/closing/envelope-config', { method: 'PUT', body: JSON.stringify({
        ...cfg,
        commission_anchor: cfg.commission_anchor === '' ? null : Number(cfg.commission_anchor),
        salary_anchor: cfg.salary_anchor === '' ? null : Number(cfg.salary_anchor),
      }) })
      setMsg('✅ Saved.'); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  function CadenceFields({ label, cadence, anchor, anchorDate, onChange }: {
    label: string; cadence: string; anchor: string; anchorDate: string
    onChange: (patch: { cadence?: string; anchor?: string; anchorDate?: string }) => void
  }) {
    return (
      <div>
        <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>{label} cadence</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <select style={sel} value={cadence} onChange={e => onChange({ cadence: e.target.value })}>
            {CADENCES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          {cadence === 'weekly' && (
            <select style={sel} value={anchor} onChange={e => onChange({ anchor: e.target.value })}>
              <option value="">(today&apos;s weekday)</option>
              {WEEKDAYS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          )}
          {cadence === 'monthly' && (
            <input style={{ ...sel, width: 90 }} type="number" min={1} max={31} placeholder="Day of month"
              value={anchor} onChange={e => onChange({ anchor: e.target.value })} />
          )}
          {cadence === 'biweekly' && (
            <input style={sel} type="date" value={anchorDate} onChange={e => onChange({ anchorDate: e.target.value })} />
          )}
        </div>
      </div>
    )
  }

  function CfgCard({ cfg, onChange, onSave, title }: { cfg: Cfg; onChange: (c: Cfg) => void; onSave: () => void; title: string }) {
    return (
      <div className="card" style={{ padding: 16, marginBottom: 14 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>{title}</div>
        <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 14 }}>
          <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={cfg.take_commission} onChange={e => onChange({ ...cfg, take_commission: e.target.checked })} /> Take commission
          </label>
          <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={cfg.take_salary} onChange={e => onChange({ ...cfg, take_salary: e.target.checked })} /> Take salary
          </label>
          <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={cfg.take_expenses} onChange={e => onChange({ ...cfg, take_expenses: e.target.checked })} /> Take approved expenses
          </label>
        </div>
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 12 }}>
          <CadenceFields label="Commission" cadence={cfg.commission_cadence} anchor={cfg.commission_anchor} anchorDate={cfg.commission_anchor_date}
            onChange={p => onChange({ ...cfg, commission_cadence: p.cadence ?? cfg.commission_cadence, commission_anchor: p.anchor ?? cfg.commission_anchor, commission_anchor_date: p.anchorDate ?? cfg.commission_anchor_date })} />
          <CadenceFields label="Salary" cadence={cfg.salary_cadence} anchor={cfg.salary_anchor} anchorDate={cfg.salary_anchor_date}
            onChange={p => onChange({ ...cfg, salary_cadence: p.cadence ?? cfg.salary_cadence, salary_anchor: p.anchor ?? cfg.salary_anchor, salary_anchor_date: p.anchorDate ?? cfg.salary_anchor_date })} />
        </div>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Envelope selection order (Q15)</div>
          <select style={sel} value={cfg.order_preference} onChange={e => onChange({ ...cfg, order_preference: e.target.value })}>
            <option value="oldest_first">Oldest envelope first (default)</option>
            <option value="newest_first">Newest envelope first</option>
          </select>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4, maxWidth: 480 }}>
            Fewest envelopes is always the objective — this only decides which envelope wins when two or
            more are otherwise an equally-good pick.
          </div>
        </div>
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={cfg.require_photo_if_cash}
              onChange={e => onChange({ ...cfg, require_photo_if_cash: e.target.checked })} />
            Require an envelope photo whenever cash &gt; 0 is declared
          </label>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4, maxWidth: 480 }}>
            OFF by default. When ON, a closing that declares any cash is blocked from submitting until a
            photo of the envelope is attached (a $0-cash closing is never affected).
          </div>
        </div>
        <button className="btn btn-primary" disabled={busy} style={{ fontSize: 13 }} onClick={onSave}>💾 Save</button>
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>✉️ Envelope Payout Configuration</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            What may be paid out in cash from the daily envelope, and on what cadence. Whatever isn&apos;t
            taken is left for the bank deposit. A store override wins over the org default for any field
            it sets.
          </p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>
      {msg && <div style={{ fontSize: 13, marginBottom: 10 }}>{msg}</div>}

      <CfgCard cfg={orgCfg} onChange={setOrgCfg} onSave={() => save(orgCfg)} title="Org default" />

      <div style={{ fontWeight: 700, fontSize: 14, margin: '18px 0 8px' }}>Store overrides</div>
      {overrides.map((o, i) => (
        <CfgCard key={o.store_code || i} cfg={o} onChange={c => setOverrides(os => os.map((x, j) => j === i ? c : x))}
          onSave={() => save(overrides[i])} title={storeLabel(o.store_code || '')} />
      ))}
      <div className="card" style={{ padding: 14, display: 'flex', gap: 10, alignItems: 'center' }}>
        <EntityPicker options={storeOptions.filter(s => !overrides.some(o => o.store_code === s.id))}
          value={newStore} onChange={setNewStore} placeholder="Add a store override…" width={260} />
        <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={!newStore}
          onClick={() => { if (newStore) { setOverrides(os => [...os, blankCfg(newStore)]); setNewStore(null) } }}>＋ Add</button>
      </div>
    </div>
  )
}
