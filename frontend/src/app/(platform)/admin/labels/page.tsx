'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { invalidateApiCache } from '@/lib/cache'
import { NAV, NAV_CARRIERS } from '@/lib/rbac'
import { POS_GATED_SURFACES } from '@/lib/carrier-scope'
import { useAuth } from '@/lib/auth-context'

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
  const [caps, setCaps] = useState<Record<string, boolean | null>>({})   // capability overrides ('carrier:<href>' / 'pos:<surface>')
  // RE-GRANTING is super-admin only (owner directive 2026-09-13). The SERVER is the enforcement — this
  // only decides whether to offer a control that would 403, and explains why when it does not.
  const { user } = useAuth()
  const isSuper = !!user?.super_admin

  useEffect(() => {
    api('/api/v1/commcalc/nav-config')
      .then(c => {
        const l = (c?.labels as Record<string, string>) || {}; setOver(l); setDraft(l)
        setCaps((c?.capabilities as Record<string, boolean | null>) || {})
      })
      .catch(() => {})
      .finally(() => setLoaded(true))
  }, [])

  async function setCap(href: string, val: 'auto' | 'show' | 'hide', ns: 'carrier' | 'pos' = 'carrier') {
    const key = ns + ':' + href
    try {
      await api('/api/v1/commcalc/nav-labels', { method: 'POST', body: JSON.stringify({ scope: 'cap', key, label: val === 'auto' ? '' : val }) })
      invalidateApiCache('nav-config')   // sidebar (layout) caches nav-config → refresh it after this write
      setCaps(p => { const n = { ...p }; if (val === 'auto') delete n[key]; else n[key] = val === 'show'; return n })
      setMsg(val === 'auto' ? (ns === 'pos' ? 'Reset to follow the POS setting' : 'Reset to carrier default')
                            : val === 'show' ? 'Always shown' : 'Always hidden')
      setTimeout(() => setMsg(''), 3000)
    } catch (e: any) { setMsg(e?.message || 'Save failed') }
  }

  async function save(scope: 'nav' | 'group', key: string) {
    const label = (draft[key] || '').trim()
    if (label === (over[key] || '')) return   // unchanged
    try {
      await api('/api/v1/commcalc/nav-labels', { method: 'POST', body: JSON.stringify({ scope, key: scope === 'group' ? key.replace(/^group:/, '') : key, label }) })
      invalidateApiCache('nav-config')   // sidebar (layout) caches nav-config → refresh it after this write
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
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 4 }}>
        Rename any sidebar group or page for your company. Leave a field blank to keep the built-in name.
        This changes display text only — never a route, table, or data path.
      </p>
      <p style={{ color: 'var(--text3)', fontSize: 12, marginBottom: 18 }}>
        Needs migration <code>068_ui_label_override.sql</code>. Edits show on the next sidebar load.
      </p>
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 14 }}>{msg}</div>}

      {/* POS-GATED SURFACES (owner directive 2026-09-13) — "the super admin should have a full role
          permission exclusiveluy for super admin to assign to the new or existing tenants which have
          been gated out due to carrier or pos settings". These are surfaces hidden by the tenant's own
          POS setting rather than by a carrier. Same override store, same endpoint, one more key
          namespace — and the same asymmetry: anyone here may HIDE or reset, only a platform
          super-admin may turn one back ON. */}
      {loaded && (
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div style={{ fontWeight: 700, fontSize: 14, textTransform: 'uppercase', letterSpacing: '0.05em',
            color: 'var(--text2)', marginBottom: 4 }}>POS-gated options</div>
          <div style={{ color: 'var(--text3)', fontSize: 12, marginBottom: 10 }}>
            Hidden because they belong to a different POS than the one this company has declared.
            {!isSuper && ' Turning one back on is reserved for the platform team — you can still hide it or reset it.'}
          </div>
          {POS_GATED_SURFACES.map(sfc => {
            const cur = caps['pos:' + sfc.key]
            const v = cur === true ? 'show' : cur === false ? 'hide' : 'auto'
            return (
              <div key={sfc.key} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, padding: '5px 0' }}>
                <div style={{ fontSize: 13, color: 'var(--text)' }}>
                  {sfc.label}
                  <span style={{ color: 'var(--text3)', fontSize: 11, marginLeft: 8 }}>{sfc.why}</span>
                </div>
                <select value={v} onChange={e => setCap(sfc.key, e.target.value as 'auto' | 'show' | 'hide', 'pos')}
                  title="POS visibility — Auto follows this company's declared POS"
                  style={{ padding: '5px 7px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)' }}>
                  <option value="auto">Auto (follow our POS)</option>
                  {(isSuper || v === 'show') && <option value="show">Always show</option>}
                  <option value="hide">Always hide</option>
                </select>
              </div>
            )
          })}
        </div>
      )}

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
                      {(isSuper || v === 'show') && <option value="show">Always show</option>}
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
