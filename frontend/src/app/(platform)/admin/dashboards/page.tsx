'use client'
// ── Dashboard Designer (dashboard-builder Phase D2, owner spec 2026-09-01) ───────────────────────
// Drag-and-drop designer for every module group's tiled /hub dashboard. Left panel = the chosen
// group's pages; right canvas = the MASTER tiles. Drag a page onto a tile to nest it, onto the
// footer zone to start a new tile; drag tiles to reorder and items within/between tiles. Saved via
// PUT /commcalc/tile-layout (backend tile_layout.py):
//   · 'Platform default' (super admin only)  → the HOUSE row every tenant + future module inherits
//   · a chosen tenant   (super admin only)   → that tenant's override        (?org_id=<tenant>)
//   · my company                             → this tenant's override (super admin OR 'menu_layout')
// Revert-to-inherited sends layout:null (DELETE — back to the platform default, then the built-in
// auto-derived tiling). Every write is authoritatively gated SERVER-SIDE (tile_write_gate); the
// client-side guard below (isSuperAdmin OR canEditSettingArea 'menu_layout') mirrors /admin/menu's
// posture and only decides what renders.
//
// DnD is the HOUSE-IDIOM hand-rolled HTML5 kind (admin/menu/page.tsx + crm/pipeline precedents —
// deliberately NO dnd library): ONE drag union state, targets opt in via onDragOver/preventDefault.
// Every drag has a keyboard-reachable fallback (▲▼ move buttons, an "add to tile…" select), so the
// designer is usable without a pointer.
import { useState, useEffect, useMemo, useCallback } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { invalidateApiCache } from '@/lib/cache'
import { useAuth } from '@/lib/auth-context'
import { NAV, canSeeItem, isSuperAdmin, canEditSettingArea, type NavItem } from '@/lib/rbac'
import HubTiles, { type HubGroup, type HubItem } from '@/components/HubTiles'
import { slugGroup, defaultHubGroups, layoutToHubGroups, hubGroupsToLayout,
         type TileLayout } from '@/lib/tile-hubs'

const HOUSE = ORG_ID   // the platform-default row's org (house)
const inp: React.CSSProperties = { padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

// One drag in flight, whatever is being dragged (union — the house DnD idiom, extended).
type Drag =
  | { kind: 'page'; href: string }
  | { kind: 'tile'; from: number }
  | { kind: 'item'; fromTile: number; index: number }

type TileResp = { module: string; layout: TileLayout | null; resolved_from: 'tenant' | 'house' | null }
type TenantRow = { org_id: string; name: string }

const navItemToHub = (it: NavItem): HubItem => ({ href: it.href, icon: it.icon, label: it.label, desc: '' })

export default function DashboardDesignerPage() {
  const { user, permissions, tenants, activeOrg } = useAuth()
  // Page guard — mirror of how /admin/menu gates (module 'admin' nav entry) widened to the
  // 'menu_layout' settings-grant holders the backend accepts. Server gates are authoritative.
  const allowed = isSuperAdmin(permissions) || canEditSettingArea(permissions, 'menu_layout', user?.role)
  // TRUE platform super admin = the membership flag (TenantMembership.super_admin), NOT modules.admin
  // — only these callers may target the house row or a foreign tenant (server enforces the same).
  const platformSA = tenants.some(t => t.super_admin)

  const groupNames = useMemo(
    () => NAV.map(g => g.group).filter(n => n !== 'Configuration' && n !== 'Reports'), [])
  const [groupName, setGroupName] = useState(groupNames[0] || '')
  // '' = my company · '__house__' = platform default (house row) · else a specific tenant org_id
  const [tenantSel, setTenantSel] = useState('')
  const [tenantList, setTenantList] = useState<TenantRow[]>([])
  const [tiles, setTiles] = useState<HubGroup[]>([])
  const [resolvedFrom, setResolvedFrom] = useState<'tenant' | 'house' | null>(null)
  const [drag, setDrag] = useState<Drag | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const slug = slugGroup(groupName)
  const navGroup = useMemo(() => NAV.find(g => g.group === groupName) || null, [groupName])
  // The pages available to place — the group's real items (never the '/hub/…' entry itself),
  // canSeeItem-filtered (a formality for the super admins this page serves, but kept so a
  // menu_layout-granted manager never designs with pages their own role cannot see).
  const pages = useMemo(
    () => (navGroup?.items || []).filter(it => !it.href.startsWith('/hub/'))
      .filter(it => canSeeItem(permissions, it)),
    [navGroup, permissions])
  const pageByHref = useMemo(() => new Map(pages.map(p => [p.href, p])), [pages])
  const placed = useMemo(() => new Set(tiles.flatMap(t => t.items.map(i => i.href))), [tiles])

  // Tenant picker roster (super-admin only; endpoint 403s everyone else server-side).
  useEffect(() => {
    if (!platformSA) return
    api('/api/v1/core/tenants')
      .then(r => setTenantList((r?.tenants || []).map((t: any) => ({ org_id: t.org_id, name: t.name || t.org_id }))))
      .catch(() => setTenantList([]))
  }, [platformSA])

  const setTilesDirty = useCallback((fn: (t: HubGroup[]) => HubGroup[]) => {
    setTiles(fn); setDirty(true); setMsg('')
  }, [])

  // ── Load the chosen (group, tenant) layout ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!allowed || !navGroup) { setLoading(false); return }
    let alive = true
    setLoading(true); setErr(''); setMsg('')
    const org = tenantSel === '__house__' ? HOUSE : tenantSel
    // NOTE (house preview): client.ts substitutes the house-literal org_id with the caller's active
    // org when acting AS a non-house tenant — so previewing 'Platform default' while switched into a
    // tenant shows that tenant's RESOLVED view instead of the raw house row. The provenance chip
    // reflects what actually came back, and SAVES are unaffected (target='house' pins the house row
    // server-side regardless of org_id).
    api(`/api/v1/commcalc/tile-layout?module=${encodeURIComponent(slug)}${org ? `&org_id=${encodeURIComponent(org)}` : ''}`)
      .then((r: TileResp) => {
        if (!alive) return
        setResolvedFrom(r?.resolved_from ?? null)
        const items = (navGroup.items || []).filter(it => !it.href.startsWith('/hub/'))
        if (r?.layout) {
          // keepUnknown: loading + resaving must never silently trim hrefs this NAV no longer names.
          setTiles(layoutToHubGroups(r.layout, items, { keepUnknown: true }))
        } else {
          setTiles(defaultHubGroups(navGroup.group, items))
        }
        setDirty(false)
      })
      .catch(e => {
        if (!alive) return
        setErr(String(e?.message || e))
        setTiles(defaultHubGroups(navGroup.group, (navGroup.items || []).filter(it => !it.href.startsWith('/hub/'))))
        setResolvedFrom(null); setDirty(false)
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [allowed, navGroup, slug, tenantSel])

  // ── Mutations (each has a drag path AND a button path) ────────────────────────────────────────
  const addPageToTile = (href: string, ti: number) => {
    const p = pageByHref.get(href); if (!p) return
    setTilesDirty(ts => ts.map((t, i) =>
      i !== ti || t.items.some(x => x.href === href) ? t : { ...t, items: [...t.items, navItemToHub(p)] }))
  }
  const addPageToNewTile = (href: string) => {
    const p = pageByHref.get(href); if (!p) return
    setTilesDirty(ts => [...ts, { title: p.label, icon: p.icon, desc: '', items: [navItemToHub(p)] }])
  }
  const removeItem = (ti: number, index: number) =>
    setTilesDirty(ts => ts.map((t, i) => i !== ti ? t : { ...t, items: t.items.filter((_, j) => j !== index) }))
  const removeTile = (ti: number) => setTilesDirty(ts => ts.filter((_, i) => i !== ti))
  const editTile = (ti: number, patch: Partial<HubGroup>) =>
    setTilesDirty(ts => ts.map((t, i) => (i === ti ? { ...t, ...patch } : t)))
  const moveTile = (from: number, to: number) => setTilesDirty(ts => {
    if (to < 0 || to >= ts.length || from === to) return ts
    const a = [...ts]; a.splice(to, 0, a.splice(from, 1)[0]); return a
  })
  // Move one interior item to (tile, index). index -1 = append. Works within and between tiles.
  const moveItem = (fromTile: number, index: number, toTile: number, toIndex: number) =>
    setTilesDirty(ts => {
      const item = ts[fromTile]?.items[index]
      if (!item) return ts
      const a = ts.map(t => ({ ...t, items: [...t.items] }))
      a[fromTile].items.splice(index, 1)
      const dest = a[toTile].items
      if (fromTile === toTile && toIndex > index) toIndex--   // removal shifted the target left
      dest.splice(toIndex < 0 ? dest.length : toIndex, 0, item)
      return a
    })

  // ── Save / revert ─────────────────────────────────────────────────────────────────────────────
  const target = tenantSel === '__house__' ? 'house' : 'tenant'
  const orgQS = tenantSel && tenantSel !== '__house__' ? `?org_id=${encodeURIComponent(tenantSel)}` : ''
  const friendly403 = (e: any) => (e?.status === 403
    ? (e?.message || '') + ' — you need the “Menu & dashboard layout designer” grant (an administrator assigns it on Roles & Access), and only a platform super-admin can save for other tenants or the platform default.'
    : String(e?.message || e))

  async function save() {
    setSaving(true); setErr(''); setMsg('')
    try {
      const body = { module: slug, layout: tiles.length ? hubGroupsToLayout(tiles) : null, target }
      await api(`/api/v1/commcalc/tile-layout${orgQS}`, { method: 'PUT', body: JSON.stringify(body) })
      invalidateApiCache('tile-layout')
      setDirty(false)
      setResolvedFrom(target === 'house' ? 'house' : 'tenant')
      setMsg(tiles.length
        ? `Saved ✓ — ${target === 'house' ? 'this is now the platform default every tenant inherits.' : 'the dashboard now uses this layout.'}`
        : 'Saved with no tiles — this reverts to the inherited layout.')
    } catch (e: any) { setErr(friendly403(e)) }
    setSaving(false)
  }
  async function revert() {
    const what = target === 'house'
      ? 'Remove the PLATFORM DEFAULT layout for this module? Every tenant without their own override falls back to the built-in automatic tiling.'
      : 'Remove this tenant\'s override for this module? The dashboard reverts to the platform default (or the built-in automatic tiling if none exists).'
    if (!confirm(what)) return
    setSaving(true); setErr(''); setMsg('')
    try {
      await api(`/api/v1/commcalc/tile-layout${orgQS}`, {
        method: 'PUT', body: JSON.stringify({ module: slug, layout: null, target }) })
      invalidateApiCache('tile-layout')
      setMsg('Reverted to the inherited layout ✓')
      // Re-pull so the canvas shows what viewers now actually see.
      setDirty(false)
      const org = tenantSel === '__house__' ? HOUSE : tenantSel
      const r: TileResp = await api(`/api/v1/commcalc/tile-layout?module=${encodeURIComponent(slug)}${org ? `&org_id=${encodeURIComponent(org)}` : ''}`)
      setResolvedFrom(r?.resolved_from ?? null)
      const items = (navGroup?.items || []).filter(it => !it.href.startsWith('/hub/'))
      setTiles(r?.layout ? layoutToHubGroups(r.layout, items, { keepUnknown: true })
                         : defaultHubGroups(groupName, items))
    } catch (e: any) { setErr(friendly403(e)) }
    setSaving(false)
  }

  // ── Guard rendering ───────────────────────────────────────────────────────────────────────────
  if (!allowed) {
    return (
      <div className="card" style={{ maxWidth: 560, margin: '60px auto', padding: 32, textAlign: 'center' }}>
        <div style={{ fontSize: 28, marginBottom: 8 }}>🎛️</div>
        <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 6 }}>Dashboard Designer</div>
        <p style={{ fontSize: 13.5, color: 'var(--text2)', margin: 0, lineHeight: 1.55 }}>
          Designing module dashboards needs the <b>Menu &amp; dashboard layout designer</b> grant —
          an administrator can assign it to your role under <b>Roles &amp; Access → Settings
          editing</b>. Super admins always have access.
        </p>
      </div>
    )
  }

  const provenance = resolvedFrom === 'tenant' ? '🏢 Tenant override'
    : resolvedFrom === 'house' ? '🌐 Inherited from platform default' : '✨ Auto-generated (no saved layout)'
  const myTenantName = tenants.find(t => t.org_id === activeOrg)?.name || tenants[0]?.name || 'My company'

  // Shared drop-acceptance: what may land on tile `ti`'s card body.
  const tileAccepts = (ti: number) =>
    !!drag && (drag.kind === 'page' || (drag.kind === 'tile' && drag.from !== ti) || drag.kind === 'item')
  const dropOnTile = (ti: number) => {
    if (!drag) return
    if (drag.kind === 'page') addPageToTile(drag.href, ti)
    else if (drag.kind === 'tile') moveTile(drag.from, ti)
    else moveItem(drag.fromTile, drag.index, ti, -1)
    setDrag(null)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🎛️ Dashboard Designer</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            Design each module&apos;s tiled dashboard: drag pages from the left onto a <b>master
            tile</b> (or the &ldquo;new tile&rdquo; zone), drag tiles and pages to reorder, and edit
            a tile&apos;s title / icon / description in place. Pages a layout doesn&apos;t mention
            still show under a trailing &ldquo;More&rdquo; tile, so newly released pages are never
            lost. Saving as the <b>platform default</b> applies to every company that hasn&apos;t
            customized its own.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{msg}</span>}
          <button className="btn" onClick={revert} disabled={saving || loading}>↺ Revert to inherited</button>
          <button className="btn btn-primary" onClick={save} disabled={saving || loading || !dirty}>
            {saving ? '…' : `💾 Save${target === 'house' ? ' platform default' : ''}`}
          </button>
        </div>
      </div>

      {/* Controls: module/group picker · tenant picker (platform super admins) · provenance chip */}
      <div className="card" style={{ padding: 12, marginBottom: 16, display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12.5, color: 'var(--text3)', display: 'flex', alignItems: 'center', gap: 6 }}>
          Module
          <select style={inp} value={groupName} onChange={e => setGroupName(e.target.value)}>
            {groupNames.map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        {platformSA && (
          <label style={{ fontSize: 12.5, color: 'var(--text3)', display: 'flex', alignItems: 'center', gap: 6 }}>
            Designing for
            <select style={inp} value={tenantSel} onChange={e => setTenantSel(e.target.value)}>
              <option value="__house__">🌐 Platform default (all tenants)</option>
              <option value="">🏢 {myTenantName} (my company)</option>
              {tenantList.filter(t => t.org_id !== activeOrg && t.org_id !== HOUSE).map(t => (
                <option key={t.org_id} value={t.org_id}>🏢 {t.name}</option>
              ))}
            </select>
          </label>
        )}
        <span title="Where the layout you are editing came from"
          style={{ fontSize: 11.5, border: '1px solid var(--border)', borderRadius: 10, padding: '2px 9px', color: 'var(--text2)' }}>
          {provenance}
        </span>
        {(groupName === 'Workforce' || groupName === 'Payroll & HR') && (
          <span style={{ fontSize: 11.5, color: 'var(--text3)' }}>
            Note: this module&apos;s sidebar links to its curated dashboard
            ({groupName === 'Workforce' ? '/storeops' : '/payroll'}); this layout applies to its
            generic /hub page.
          </span>
        )}
        {err && <span style={{ fontSize: 12, color: '#dc2626', flexBasis: '100%' }}>{err}</span>}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          {/* ── LEFT: the module's pages ── */}
          <div className="card" style={{ padding: 12, flex: '0 1 300px', minWidth: 260, position: 'sticky', top: 72 }}>
            <div style={{ fontWeight: 700, fontSize: 13.5, marginBottom: 2 }}>Pages in {groupName}</div>
            <div style={{ fontSize: 11.5, color: 'var(--text3)', marginBottom: 8 }}>
              Drag a page onto a tile — or use its &ldquo;add&rdquo; picker. Dimmed pages are already placed.
            </div>
            {pages.length === 0 && <div style={{ fontSize: 12.5, color: 'var(--text3)' }}>No pages in this group.</div>}
            {pages.map(p => {
              const isPlaced = placed.has(p.href)
              const being = drag?.kind === 'page' && drag.href === p.href
              return (
                <div key={p.href} draggable
                  onDragStart={() => setDrag({ kind: 'page', href: p.href })}
                  onDragEnd={() => setDrag(null)}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 4px',
                    borderBottom: '1px solid var(--border)', opacity: being ? 0.35 : isPlaced ? 0.45 : 1, cursor: 'grab' }}>
                  <span aria-hidden style={{ color: 'var(--text3)', fontSize: 12, userSelect: 'none' }}>⠿</span>
                  <span style={{ width: 20, textAlign: 'center' }}>{p.icon}</span>
                  <span style={{ flex: 1, fontSize: 12.5, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    title={p.href}>{p.label}</span>
                  {/* Keyboard fallback for the drag: pick a destination tile explicitly. */}
                  <select aria-label={`Add ${p.label} to a tile`} value="" style={{ ...inp, padding: '2px 4px', fontSize: 11.5, maxWidth: 76 }}
                    onChange={e => {
                      const v = e.target.value
                      if (v === '__new__') addPageToNewTile(p.href)
                      else if (v !== '') addPageToTile(p.href, Number(v))
                    }}>
                    <option value="">＋ add…</option>
                    {tiles.map((t, i) => <option key={i} value={i}>→ {t.title}</option>)}
                    <option value="__new__">→ new tile</option>
                  </select>
                </div>
              )
            })}
          </div>

          {/* ── RIGHT: the canvas of master tiles ── */}
          <div style={{ flex: '1 1 480px', minWidth: 340 }}>
            {tiles.map((t, ti) => {
              const beingT = drag?.kind === 'tile' && drag.from === ti
              return (
                <div key={ti} className="card"
                  onDragOver={e => { if (tileAccepts(ti)) e.preventDefault() }}
                  onDrop={e => { if (!tileAccepts(ti)) return; e.preventDefault(); e.stopPropagation(); dropOnTile(ti) }}
                  style={{ marginBottom: 12, padding: 12, opacity: beingT ? 0.4 : 1 }}>
                  <div draggable
                    onDragStart={e => { e.stopPropagation(); setDrag({ kind: 'tile', from: ti }) }}
                    onDragEnd={() => setDrag(null)}
                    style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, cursor: 'grab', flexWrap: 'wrap' }}>
                    <span title="Drag to reorder this tile" aria-hidden style={{ color: 'var(--text3)', userSelect: 'none', fontSize: 13 }}>⠿</span>
                    <input aria-label="Tile icon (emoji)" title="Tile icon — type any emoji" value={t.icon}
                      onChange={e => editTile(ti, { icon: e.target.value })}
                      style={{ ...inp, width: 46, textAlign: 'center', fontSize: 16, padding: '3px 4px' }} />
                    <input aria-label="Tile title" value={t.title} placeholder="Tile title"
                      onChange={e => editTile(ti, { title: e.target.value })}
                      style={{ ...inp, fontWeight: 700, flex: '1 1 140px', minWidth: 120 }} />
                    <span style={{ fontSize: 11, color: 'var(--text3)' }}>{t.items.length} page{t.items.length === 1 ? '' : 's'}</span>
                    <button className="btn btn-sm" onClick={() => moveTile(ti, ti - 1)} disabled={ti === 0}
                      title="Move tile up" aria-label={`Move tile ${t.title} up`}>▲</button>
                    <button className="btn btn-sm" onClick={() => moveTile(ti, ti + 1)} disabled={ti === tiles.length - 1}
                      title="Move tile down" aria-label={`Move tile ${t.title} down`}>▼</button>
                    <button className="btn btn-sm" onClick={() => removeTile(ti)}
                      title="Remove this tile (its pages return to the panel on the left)"
                      aria-label={`Remove tile ${t.title}`}>✕</button>
                  </div>
                  <input aria-label="Tile description" value={t.desc} placeholder="One-line description (optional)"
                    onChange={e => editTile(ti, { desc: e.target.value })}
                    style={{ ...inp, width: '100%', marginBottom: 8, fontSize: 12.5 }} />
                  {t.items.length === 0 && (
                    <div style={{ fontSize: 12, color: 'var(--text3)', border: '1px dashed var(--border)', borderRadius: 8, padding: 10 }}>
                      Empty tile — drop a page here. (Empty tiles are not shown on the dashboard.)
                    </div>
                  )}
                  {t.items.map((it, ii) => {
                    const beingI = drag?.kind === 'item' && drag.fromTile === ti && drag.index === ii
                    const known = pageByHref.has(it.href)
                    return (
                      <div key={it.href + ii} draggable
                        onDragStart={e => { e.stopPropagation(); setDrag({ kind: 'item', fromTile: ti, index: ii }) }}
                        onDragEnd={() => setDrag(null)}
                        onDragOver={e => { if (drag?.kind === 'item' && !(drag.fromTile === ti && drag.index === ii)) { e.preventDefault(); e.stopPropagation() } }}
                        onDrop={e => {
                          if (drag?.kind !== 'item' || (drag.fromTile === ti && drag.index === ii)) return
                          e.preventDefault(); e.stopPropagation()
                          moveItem(drag.fromTile, drag.index, ti, ii)
                          setDrag(null)
                        }}
                        style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 4px',
                          borderBottom: '1px solid var(--border)', opacity: beingI ? 0.35 : 1, cursor: 'grab' }}>
                        <span aria-hidden style={{ color: 'var(--text3)', fontSize: 12, userSelect: 'none' }}>⠿</span>
                        <span style={{ width: 20, textAlign: 'center' }}>{it.icon}</span>
                        <span style={{ flex: 1, fontSize: 12.5, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={it.href}>
                          {it.label}
                          {!known && <span title="This page is no longer in the menu — the dashboard hides it, but it stays in the saved layout."
                            style={{ marginLeft: 6, fontSize: 10.5, color: 'var(--text3)' }}>(not in menu)</span>}
                        </span>
                        <button className="btn btn-sm" onClick={() => moveItem(ti, ii, ti, ii - 1)} disabled={ii === 0}
                          title="Move up within the tile" aria-label={`Move ${it.label} up`}>▲</button>
                        <button className="btn btn-sm" onClick={() => moveItem(ti, ii, ti, ii + 2)} disabled={ii === t.items.length - 1}
                          title="Move down within the tile" aria-label={`Move ${it.label} down`}>▼</button>
                        <button className="btn btn-sm" onClick={() => removeItem(ti, ii)}
                          title="Remove from this tile" aria-label={`Remove ${it.label} from ${t.title}`}>✕</button>
                      </div>
                    )
                  })}
                </div>
              )
            })}

            {/* New-master-tile drop zone */}
            <div
              onDragOver={e => { if (drag?.kind === 'page' || drag?.kind === 'item') e.preventDefault() }}
              onDrop={e => {
                if (!drag || drag.kind === 'tile') return
                e.preventDefault()
                if (drag.kind === 'page') addPageToNewTile(drag.href)
                else {
                  const item = tiles[drag.fromTile]?.items[drag.index]
                  if (item) {
                    const ft = drag.fromTile, ix = drag.index
                    setTilesDirty(ts => {
                      const a = ts.map(t => ({ ...t, items: [...t.items] }))
                      a[ft].items.splice(ix, 1)
                      return [...a, { title: item.label, icon: item.icon, desc: '', items: [item] }]
                    })
                  }
                }
                setDrag(null)
              }}
              style={{ border: '2px dashed var(--border)', borderRadius: 12, padding: 18, textAlign: 'center',
                fontSize: 13, color: 'var(--text3)', marginBottom: 12,
                background: drag && drag.kind !== 'tile' ? 'var(--bg2)' : 'transparent' }}>
              ＋ New master tile — drop a page here to start one
              <button className="btn btn-sm" style={{ marginLeft: 10 }}
                onClick={() => setTilesDirty(ts => [...ts, { title: 'New tile', icon: '🗂️', desc: '', items: [] }])}>
                or click to add empty
              </button>
            </div>

            {/* ── Live preview — exactly what the /hub page renders from this working state ── */}
            <div style={{ margin: '20px 0 8px', fontWeight: 700, fontSize: 14 }}>
              👁️ Live preview <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text3)' }}>
                — how /hub/{slug} will look (empty tiles hidden; viewers only see pages their role allows)</span>
            </div>
            <HubTiles groups={tiles.filter(t => t.items.length > 0)} />
          </div>
        </div>
      )}
    </div>
  )
}
