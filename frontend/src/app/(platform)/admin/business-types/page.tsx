'use client'
// Business Types (index §35) — the platform super-admin's editor for WHAT EACH KIND OF BUSINESS SEES.
// Owner 2026-09-25: "no hard coded — all should be programmable platform based".
//
// Everything here is platform DATA (core.tenant_vertical + core.module_catalog.applies_to_vertical), read by the
// one reader core/verticals.py and every gate after it; the mig-1020/1024 seed is only the starting rows. A single
// company's exceptions stay on its own overrides (Display Labels → business-type overrides, the cap mechanism).
// This file names no business type: the list, the pages, the closing inputs and the modules all come from the
// backend payload and the NAV registry.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { TENANT_NAV, hrefHiddenByVertical } from '@/lib/rbac'

type Vertical = {
  key: string; label: string; is_default: boolean; uses_carriers: boolean
  nav_hidden: string[]; closing_hidden: string[]; sort_order: number; tenants?: number
}
type Section = { key: string; label: string; kind: string }
type Module = { key: string; label: string; applies_to_vertical: string[] }
type Payload = { registry_ready: boolean; verticals: Vertical[]; closing_sections: Section[]; modules: Module[] }

const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, padding: 14, background: 'var(--surface)', marginBottom: 14 }
const inp: React.CSSProperties = { padding: '6px 9px', fontSize: 13, border: '1px solid var(--border)', borderRadius: 7 }
const btn: React.CSSProperties = { padding: '6px 14px', fontSize: 13, borderRadius: 7, border: '1px solid var(--border)', cursor: 'pointer', background: 'var(--surface)' }
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

// Every page in the menu, once (a page listed under two groups is shown under its first).
const PAGES: { group: string; href: string; label: string }[] = (() => {
  const seen = new Set<string>()
  const out: { group: string; href: string; label: string }[] = []
  for (const g of TENANT_NAV) for (const it of g.items) {
    if (seen.has(it.href)) continue
    seen.add(it.href)
    out.push({ group: g.group, href: it.href, label: it.label })
  }
  return out
})()

// Toggle one page in a hidden list. Pages the editor adds are EXACT entries ('href$'); un-hiding a page that is
// hidden by a subtree entry removes that entry and re-adds exact entries for its other pages, so no other page
// changes state as a side effect.
function togglePage(hidden: string[], href: string, hide: boolean): string[] {
  const exact = href + '$'
  let next = hidden.filter(h => h !== exact && h !== href)
  if (hide) return hrefHiddenByVertical(href, next) ? next : [...next, exact]
  const covering = next.filter(h => !h.endsWith('$') && hrefHiddenByVertical(href, [h]))
  for (const c of covering) {
    next = next.filter(h => h !== c)
    for (const p of PAGES) if (p.href !== href && hrefHiddenByVertical(p.href, [c])) next.push(p.href + '$')
  }
  return Array.from(new Set(next)).sort()
}

export default function BusinessTypesPage() {
  const [data, setData] = useState<Payload | null>(null)
  const [pick, setPick] = useState('')
  const [draft, setDraft] = useState<Vertical | null>(null)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [newLabel, setNewLabel] = useState('')

  // Apply a fresh payload and keep the chosen business type selected.
  const apply = useCallback((d: Payload, keep?: string) => {
    setData(d)
    const k = keep || d.verticals[0]?.key || ''
    setPick(k)
    setDraft(d.verticals.find(v => v.key === k) || null)
    setErr('')
  }, [])
  const load = useCallback(async (keep?: string) => {
    try { apply((await api('/api/v1/core/verticals/admin')) as Payload, keep) } catch (e) { setErr(errText(e)) }
  }, [apply])
  useEffect(() => {
    let alive = true
    api('/api/v1/core/verticals/admin')
      .then((d: Payload) => { if (alive) apply(d) })
      .catch((e: unknown) => { if (alive) setErr(errText(e)) })
    return () => { alive = false }
  }, [apply])

  const choose = (k: string) => { setPick(k); setDraft(data?.verticals.find(v => v.key === k) || null); setMsg('') }
  const groups = useMemo(() => Array.from(new Set(PAGES.map(p => p.group))), [])

  const save = async () => {
    if (!draft) return
    setBusy(true); setMsg(''); setErr('')
    try {
      await api(`/api/v1/core/verticals/${encodeURIComponent(draft.key)}`, {
        method: 'PUT',
        body: JSON.stringify({ label: draft.label, uses_carriers: draft.uses_carriers, sort_order: draft.sort_order,
                               nav_hidden: draft.nav_hidden, closing_hidden: draft.closing_hidden }),
      })
      setMsg('Saved — companies of this type see the change on their next page load.')
      await load(draft.key)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const makeDefault = async () => {
    if (!draft) return
    try {
      await api(`/api/v1/core/verticals/${encodeURIComponent(draft.key)}`, { method: 'PUT', body: JSON.stringify({ is_default: true }) })
      setMsg(`${draft.label} is now the default (companies with no type chosen read as this).`)
      await load(draft.key)
    } catch (e) { setErr(errText(e)) }
  }
  const toggleModule = async (m: Module, include: boolean) => {
    if (!draft) return
    try {
      const r = (await api(`/api/v1/core/module-verticals/${encodeURIComponent(m.key)}`, {
        method: 'PUT', body: JSON.stringify({ vertical: draft.key, include }),
      })) as { tenants_resynced?: number }
      setMsg(`${m.label}: ${include ? 'added to' : 'removed from'} ${draft.label} (${r.tenants_resynced ?? 0} companies updated).`)
      await load(draft.key)
    } catch (e) { setErr(errText(e)) }
  }
  const create = async () => {
    setBusy(true); setErr(''); setMsg('')
    try {
      await api('/api/v1/core/verticals', { method: 'POST', body: JSON.stringify({ key: newKey.trim(), label: newLabel.trim() }) })
      setNewKey(''); setNewLabel('')
      setMsg('Created — it hides nothing yet. Configure it below.')
      await load(newKey.trim())
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const modIncluded = (m: Module) => !m.applies_to_vertical.length || (!!draft && m.applies_to_vertical.includes(draft.key))

  return (
    <div style={{ padding: '18px 22px', maxWidth: 1100 }}>
      <h1 style={{ fontSize: 20, fontWeight: 800, margin: 0 }}>Business Types</h1>
      <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '6px 0 14px', maxWidth: 800 }}>
        What each kind of business sees: which menu pages exist for it, which modules belong to it and which daily-closing
        inputs its reps are asked for. A company picks its type in the Setup Wizard; one company&rsquo;s exceptions are set on
        its own Display Labels page.
      </p>
      {err && <div style={{ ...card, borderColor: '#fecaca', color: '#991b1b' }}>{err}</div>}
      {msg && <div style={{ ...card, borderColor: '#bbf7d0', color: '#166534' }}>{msg}</div>}
      {data && !data.registry_ready && (
        <div style={card}>Business types are not set up on this database yet (migration 1020). Showing the built-in defaults; saving is disabled.</div>
      )}

      {data && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
          {data.verticals.map(v => (
            <button key={v.key} onClick={() => choose(v.key)}
                    style={{ ...btn, fontWeight: v.key === pick ? 700 : 400, borderColor: v.key === pick ? 'var(--accent, #2563eb)' : 'var(--border)' }}>
              {v.label}{v.is_default ? ' · default' : ''} <span style={{ color: 'var(--text3)' }}>({v.tenants ?? 0})</span>
            </button>
          ))}
        </div>
      )}

      {draft && data && (
        <>
          <div style={card}>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
              <label style={{ fontSize: 13 }}>Name <input style={{ ...inp, width: 260 }} value={draft.label}
                onChange={e => setDraft({ ...draft, label: e.target.value })} /></label>
              <label style={{ fontSize: 13 }}><input type="checkbox" checked={draft.uses_carriers}
                onChange={e => setDraft({ ...draft, uses_carriers: e.target.checked })} /> Sells on wireless carriers (carrier menus and the carrier switcher)</label>
              <label style={{ fontSize: 13 }}>Order <input style={{ ...inp, width: 70 }} type="number" value={draft.sort_order}
                onChange={e => setDraft({ ...draft, sort_order: Number(e.target.value) || 0 })} /></label>
              {!draft.is_default && <button style={btn} onClick={makeDefault}>Make default</button>}
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>key: {draft.key}</span>
            </div>
          </div>

          <div style={card}>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>Daily closing inputs this type does not use</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 4 }}>
              {data.closing_sections.map(s => (
                <label key={s.key} style={{ fontSize: 13 }}>
                  <input type="checkbox" checked={draft.closing_hidden.includes(s.key)}
                    onChange={e => setDraft({ ...draft, closing_hidden: e.target.checked
                      ? [...draft.closing_hidden, s.key] : draft.closing_hidden.filter(k => k !== s.key) })} /> Hide: {s.label}
                </label>
              ))}
            </div>
          </div>

          <div style={card}>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>Modules that belong to this type <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text3)' }}>(saved immediately)</span></div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 4 }}>
              {data.modules.map(m => (
                <label key={m.key} style={{ fontSize: 13 }}>
                  <input type="checkbox" checked={modIncluded(m)} disabled={!data.registry_ready}
                    onChange={e => void toggleModule(m, e.target.checked)} /> {m.label}
                  {!m.applies_to_vertical.length && <span style={{ color: 'var(--text3)', fontSize: 11 }}> · every type</span>}
                </label>
              ))}
            </div>
          </div>

          <div style={card}>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>Menu pages this type does not see <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text3)' }}>({PAGES.filter(p => hrefHiddenByVertical(p.href, draft.nav_hidden)).length} hidden)</span></div>
            {groups.map(g => (
              <div key={g} style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text2)', margin: '6px 0 2px' }}>{g}</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 2 }}>
                  {PAGES.filter(p => p.group === g).map(p => (
                    <label key={p.href} style={{ fontSize: 12.5 }} title={p.href}>
                      <input type="checkbox" checked={hrefHiddenByVertical(p.href, draft.nav_hidden)}
                        onChange={e => setDraft({ ...draft, nav_hidden: togglePage(draft.nav_hidden, p.href, e.target.checked) })} /> Hide: {p.label}
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <button style={{ ...btn, fontWeight: 700 }} disabled={busy || !data.registry_ready} onClick={save}>{busy ? 'Saving…' : 'Save business type'}</button>
        </>
      )}

      <div style={{ ...card, marginTop: 20 }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Add a business type</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input style={inp} placeholder="key (e.g. print_shop)" value={newKey} onChange={e => setNewKey(e.target.value)} />
          <input style={{ ...inp, width: 260 }} placeholder="Name shown to companies" value={newLabel} onChange={e => setNewLabel(e.target.value)} />
          <button style={btn} disabled={busy || !newKey.trim() || !newLabel.trim() || !data?.registry_ready} onClick={create}>Create</button>
        </div>
      </div>
    </div>
  )
}
