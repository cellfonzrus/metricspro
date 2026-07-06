'use client'
import { useState, useEffect } from 'react'
import { api } from '@/lib/client'
import { NAV } from '@/lib/rbac'

// Admin-only: rearrange the sidebar for the whole tenant — move any item to a different group or hide
// it. Saved per-org to commcalc.ui_label_override (scope='layout') and applied on top of the built-in
// menu in (platform)/layout.tsx via applyNavLayout. Items you don't touch keep their default place, and
// a newly-shipped item still appears automatically.
type Ov = Record<string, { group?: string; hidden?: boolean }>
const inp: React.CSSProperties = { padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function MenuLayoutPage() {
  const defaultGroups = Array.from(new Set(NAV.map(g => g.group)))
  const [ov, setOv] = useState<Ov>({})
  const [extraGroups, setExtraGroups] = useState<string[]>([])
  const [newGroup, setNewGroup] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    api('/api/v1/commcalc/nav-config').then((c: any) => {
      const items: Ov = c?.layout?.items || {}
      setOv(items)
      const extra = Array.from(new Set(Object.values(items).map(v => v?.group).filter(g => g && !defaultGroups.includes(g)))) as string[]
      setExtraGroups(extra)
    }).catch(() => {}).finally(() => setLoading(false))
  }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  const groupOptions = [...defaultGroups, ...extraGroups]
  const groupOf = (href: string, def: string) => ov[href]?.group || def
  const setGroup = (href: string, def: string, g: string) => setOv(o => ({ ...o, [href]: { ...o[href], group: g === def ? undefined : g } }))
  const setHidden = (href: string, h: boolean) => setOv(o => ({ ...o, [href]: { ...o[href], hidden: h || undefined } }))
  const addGroup = () => { const g = newGroup.trim(); if (g && !groupOptions.includes(g)) setExtraGroups(x => [...x, g]); setNewGroup('') }
  const dirty = Object.values(ov).filter(v => v && ((v.group || '').trim() || v.hidden)).length

  async function save() {
    setSaving(true); setMsg('')
    const items: Ov = {}
    Object.entries(ov).forEach(([h, v]) => {
      const g = (v?.group || '').trim()
      if (g || v?.hidden) items[h] = { ...(g ? { group: g } : {}), ...(v?.hidden ? { hidden: true } : {}) }
    })
    try { await api('/api/v1/commcalc/nav-layout', { method: 'POST', body: JSON.stringify({ items }) }); setMsg('Saved ✓ — reload the page to see the menu update.') }
    catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
    setSaving(false)
  }
  async function resetAll() {
    if (!confirm('Reset the whole menu back to the built-in layout?')) return
    setSaving(true); setMsg('')
    try { await api('/api/v1/commcalc/nav-layout', { method: 'POST', body: JSON.stringify({ items: {} }) }); setOv({}); setExtraGroups([]); setMsg('Reset to defaults — reload to see it.') }
    catch (e: any) { setMsg('Reset failed: ' + (e?.message || e)) }
    setSaving(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Menu Layout</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Move any sidebar item to a different group or hide it — applies to everyone in your company.</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{msg}</span>}
          <button className="btn" onClick={resetAll} disabled={saving}>↺ Reset to defaults</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : `💾 Save${dirty ? ` (${dirty})` : ''}`}</button>
        </div>
      </div>

      <div className="card" style={{ padding: 12, marginBottom: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: 'var(--text2)' }}>Need a new menu group? Create one, then assign items to it:</span>
        <input style={{ ...inp, width: 180 }} placeholder="New group name" value={newGroup} onChange={e => setNewGroup(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') addGroup() }} />
        <button className="btn" onClick={addGroup}>＋ Add group</button>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : NAV.map(g => (
        <div key={g.group} className="card" style={{ marginBottom: 14, padding: 14 }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>{g.group}</div>
          {g.items.map(it => {
            const tgt = groupOf(it.href, g.group)
            const hidden = !!ov[it.href]?.hidden
            const moved = tgt !== g.group
            return (
              <div key={it.href} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid var(--border)', opacity: hidden ? 0.55 : 1 }}>
                <span style={{ width: 22, textAlign: 'center' }}>{it.icon}</span>
                <span style={{ flex: 1, fontSize: 13 }}>{it.label}{moved && <span style={{ fontSize: 11, color: 'var(--accent)', marginLeft: 6 }}>→ {tgt}</span>}</span>
                <label style={{ fontSize: 12, color: 'var(--text3)' }}>Group&nbsp;
                  <select style={inp} value={tgt} onChange={e => setGroup(it.href, g.group, e.target.value)}>
                    {groupOptions.map(gn => <option key={gn} value={gn}>{gn}</option>)}
                  </select>
                </label>
                <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                  <input type="checkbox" checked={hidden} onChange={e => setHidden(it.href, e.target.checked)} /> Hide
                </label>
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}
