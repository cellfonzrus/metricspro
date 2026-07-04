'use client'
import { useEffect, useState, useCallback } from 'react'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

// One-click carrier picker (Carrier workstream C2). Selecting a carrier writes commcalc.carrier for
// this tenant; the nav then shows only that carrier's areas (C1 gate) — Boost stores see Boost reports,
// Total stores see Total, shared reports show for all. Refreshes auth so the sidebar updates instantly.
type Carrier = { id: string; name: string; code: string | null; is_default: boolean }
const PRESETS = [
  { name: 'Boost Mobile', code: 'boost' },
  { name: 'Total Wireless', code: 'total' },
  { name: 'Metro by T-Mobile', code: 'metro' },
  { name: 'Cricket Wireless', code: 'cricket' },
  { name: 'Ultra Mobile', code: 'ultra' },
  { name: 'AT&T Prepaid', code: 'att' },
]

export default function CarrierPicker({ canEdit }: { canEdit: boolean }) {
  const { refresh } = useAuth()
  const [carriers, setCarriers] = useState<Carrier[]>([])
  const [custom, setCustom] = useState('')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    api('/api/v1/commcalc/carriers').then((d: any) => setCarriers(Array.isArray(d) ? d : [])).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  const matchOf = (p: { name: string; code: string }) =>
    carriers.find(c => (c.code || '').toLowerCase() === p.code
      || (c.name || '').toLowerCase() === p.name.toLowerCase()
      || (c.name || '').toLowerCase().includes(p.code))

  async function toggle(p: { name: string; code: string }) {
    if (!canEdit) return
    const existing = matchOf(p)
    setBusy(p.code); setMsg('')
    try {
      if (existing) await api(`/api/v1/commcalc/carriers/${existing.id}`, { method: 'DELETE' })
      else await api('/api/v1/commcalc/carriers', { method: 'POST', body: JSON.stringify({ name: p.name, code: p.code, is_default: carriers.length === 0 }) })
      load(); await refresh()
      setMsg('✅ Updated — the menu now shows only what applies to your carrier(s).')
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
  }
  async function addCustom() {
    const nm = custom.trim()
    if (!nm || !canEdit) return
    setBusy('custom'); setMsg('')
    try {
      await api('/api/v1/commcalc/carriers', { method: 'POST', body: JSON.stringify({ name: nm, code: nm.toLowerCase().replace(/[^a-z0-9]+/g, ''), is_default: carriers.length === 0 }) })
      setCustom(''); load(); await refresh()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
  }
  async function removeCarrier(c: Carrier) {
    if (!canEdit) return
    setBusy(c.id)
    try { await api(`/api/v1/commcalc/carriers/${c.id}`, { method: 'DELETE' }); load(); await refresh() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy('') }
  }

  const chip = (selected: boolean) => ({
    padding: '8px 14px', borderRadius: 999, fontSize: 14, cursor: canEdit ? 'pointer' : 'default',
    border: `1.5px solid ${selected ? 'var(--accent)' : 'var(--border)'}`,
    background: selected ? 'var(--accent)' : 'var(--surface)', color: selected ? '#fff' : 'var(--text1)',
    fontWeight: selected ? 700 : 500, opacity: canEdit ? 1 : 0.75,
  })
  const extras = carriers.filter(c => !PRESETS.some(p => matchOf(p)?.id === c.id))

  return (
    <div className="card" style={{ padding: 18, marginTop: 16 }}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>📡 Carriers</div>
      <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 12px' }}>
        Pick the carrier(s) this company sells — one click each. The app then shows only the areas that
        apply, so you’re not overwhelmed by configuration. {!canEdit && '(Admin only.)'}
      </p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: extras.length ? 12 : 0 }}>
        {PRESETS.map(p => {
          const sel = !!matchOf(p)
          return <button key={p.code} style={chip(sel)} disabled={!canEdit || busy === p.code} onClick={() => toggle(p)}>
            {busy === p.code ? '…' : (sel ? '✓ ' : '') + p.name}</button>
        })}
      </div>
      {extras.length > 0 && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
          {extras.map(c => <span key={c.id} style={{ ...chip(true), display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            ✓ {c.name}{canEdit && <span onClick={() => removeCarrier(c)} style={{ cursor: 'pointer', opacity: 0.85 }}>✕</span>}</span>)}
        </div>
      )}
      {canEdit && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input placeholder="Add another carrier…" value={custom} onChange={e => setCustom(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') addCustom() }}
            style={{ padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', width: 220 }} />
          <button className="btn btn-sm" disabled={!custom.trim() || busy === 'custom'} onClick={addCustom}>Add</button>
        </div>
      )}
      {msg && <div style={{ fontSize: 12, marginTop: 8 }}>{msg}</div>}
    </div>
  )
}
