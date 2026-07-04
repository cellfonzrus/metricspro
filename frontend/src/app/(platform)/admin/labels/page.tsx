'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { NAV, NAV_CARRIERS } from '@/lib/rbac'

// Display Labels — per-tenant nicknames for the sidebar. Rename what you SEE ("Distributors"→"Suppliers",
// "Payment Processor"→"VidaPay") without touching code or DB column names. Display-only: changing a label
// here never renames a route, table, column, report_key or any data path. Backed by commcalc.ui_label_override
// (migration 068). Blank = revert to the built-in label. Edits apply on the next page load of the sidebar.

const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', width: 240 }

export default function DisplayLabelsPage() {
  const [over, setOver] = useState<Record<string, string>>({})   // key -> nickname ('group:Name' for groups)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [caps, setCaps] = useState<Record<string, boolean | null>>({})   // capability overrides (carrier:<href>)

  useEffect(() => {
    api('/api/v1/commcalc/nav-config')
      .then(c => {
        const l = (c?.labels as Record<string, string>) || {}; setOver(l); setDraft(l)
        setCaps((c?.capabilities as Record<string, boolean | null>) || {})
      })
      .catch(() => {})
      .finally(() => setLoaded(true))
  }, [])

  async function setCap(href: string, val: 'auto' | 'show' | 'hide') {
    const key = 'carrier:' + href
    try {
      await api('/api/v1/commcalc/nav-labels', { method: 'POST', body: JSON.stringify({ scope: 'cap', key, label: val === 'auto' ? '' : val }) })
      setCaps(p => { const n = { ...p }; if (val === 'auto') delete n[key]; else n[key] = val === 'show'; return n })
      setMsg(val === 'auto' ? 'Reset to carrier default' : val === 'show' ? 'Always shown' : 'Always hidden')
      setTimeout(() => setMsg(''), 3000)
    } catch (e: any) { setMsg(e?.message || 'Save failed') }
  }

  async function save(scope: 'nav' | 'group', key: string) {
    const label = (draft[key] || '').trim()
    if (label === (over[key] || '')) return   // unchanged
    try {
      await api('/api/v1/commcalc/nav-labels', { method: 'POST', body: JSON.stringify({ scope, key: scope === 'group' ? key.replace(/^group:/, '') : key, label }) })
      setOver(p => { const n = { ...p }; if (label) n[key] = label; else delete n[key]; return n })
      setMsg(label ? `Saved "${label}"` : 'Reverted to default')
    } catch (e: any) {
      setMsg(e?.message || 'Save failed — is migration 068_ui_label_override.sql applied?')
      setDraft(p => ({ ...p, [key]: over[key] || '' }))   // roll back the field
    }
    setTimeout(() => setMsg(''), 3500)
  }

  // NOTE: a plain element-returning function, NOT a nested component — a nested <Field/> would remount
  // on every keystroke (draft state change) and the input would lose focus after one character.
  const field = (scope: 'nav' | 'group', k: string, placeholder: string) => (
    <input style={inp} placeholder={placeholder} value={draft[k] ?? ''}
      onChange={e => setDraft(p => ({ ...p, [k]: e.target.value }))}
      onBlur={() => save(scope, k)} onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} />
  )

  return (
    <div style={{ padding: 24, maxWidth: 820 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🏷️ Display Labels</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 4 }}>
        Rename any sidebar group or page for your company. Leave a field blank to keep the built-in name.
        This changes display text only — never a route, table, or data path.
      </p>
      <p style={{ color: 'var(--text3)', fontSize: 12, marginBottom: 18 }}>
        Needs migration <code>068_ui_label_override.sql</code>. Edits show on the next sidebar load.
      </p>
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 14 }}>{msg}</div>}

      {!loaded ? <div style={{ color: 'var(--text3)' }}>Loading…</div> : NAV.map(g => (
        <div key={g.group} className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, marginBottom: 10, paddingBottom: 10, borderBottom: '1px solid var(--border)' }}>
            <div style={{ fontWeight: 700, fontSize: 14, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text2)' }}>{g.group}</div>
            {field('group', 'group:' + g.group, g.group)}
          </div>
          {g.items.map(it => (
            <div key={it.href} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, padding: '5px 0' }}>
              <div style={{ fontSize: 13, color: 'var(--text)' }}><span style={{ marginRight: 8 }}>{it.icon}</span>{it.label}
                <span style={{ color: 'var(--text3)', fontSize: 11, marginLeft: 8 }}>{it.href}</span></div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {NAV_CARRIERS[it.href] && (() => {
                  const cur = caps['carrier:' + it.href]
                  const v = cur === true ? 'show' : cur === false ? 'hide' : 'auto'
                  return (
                    <select value={v} onChange={e => setCap(it.href, e.target.value as 'auto' | 'show' | 'hide')}
                      title="Carrier visibility — Auto follows the tenant's carrier; override to always show or hide"
                      style={{ padding: '5px 7px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)' }}>
                      <option value="auto">Auto ({NAV_CARRIERS[it.href].join('/')})</option>
                      <option value="show">Always show</option>
                      <option value="hide">Always hide</option>
                    </select>
                  )
                })()}
                {field('nav', it.href, it.label)}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
