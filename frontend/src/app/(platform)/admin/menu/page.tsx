'use client'
import { useState, useEffect, useMemo } from 'react'
import { api } from '@/lib/client'
import { NAV } from '@/lib/rbac'

// Admin-only: rearrange the sidebar for the whole tenant — MOVE any item to a different group, show a
// DUPLICATE copy of it in additional group(s), hide it, or create brand-new groups. Saved per-org to
// commcalc.ui_label_override (scope='layout') and applied on top of the built-in menu in
// (platform)/layout.tsx via applyNavLayout. Items you don't touch keep their default place, and a
// newly-shipped item still appears automatically. A duplicate is a SECOND LINK to the SAME href — it
// carries the identical permission gate (never a second permission surface).
type ItemOv = { group?: string; sub?: string; hidden?: boolean; also?: string[] }
type Ov = Record<string, ItemOv>
const inp: React.CSSProperties = { padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function MenuLayoutPage() {
  const defaultGroups = useMemo(() => Array.from(new Set(NAV.map(g => g.group))), [])
  // built-in group for each href (used to know an item's DEFAULT placement)
  const defGroupByHref = useMemo(() => {
    const m: Record<string, string> = {}
    NAV.forEach(g => g.items.forEach(it => { m[it.href] = g.group }))
    return m
  }, [])
  const labelByHref = useMemo(() => {
    const m: Record<string, { label: string; icon: string }> = {}
    NAV.forEach(g => g.items.forEach(it => { m[it.href] = { label: it.label, icon: it.icon } }))
    return m
  }, [])

  const [ov, setOv] = useState<Ov>({})
  const [extraGroups, setExtraGroups] = useState<string[]>([])
  // Explicit drag-and-drop order (roadmap #5). All three are ADDITIVE: a group/sub/item the list does not
  // name keeps its built-in position AFTER the named ones, so a newly-shipped page still appears on its
  // own and an admin who drags one group has not implicitly frozen the rest of the menu.
  const [groupOrder, setGroupOrder] = useState<string[]>([])
  const [itemOrder, setItemOrder] = useState<Record<string, string[]>>({})
  const [subOrder, setSubOrder] = useState<Record<string, string[]>>({})
  const [drag, setDrag] = useState<{ kind: 'group' | 'item'; group?: string; key: string } | null>(null)
  // OWNER DIRECTIVE 2026-07-18: hide the categorized "Reports ·" directory entirely. Persisted as
  // layout.hideReportsDirectory in the SAME nav-layout JSON; honored by applyNavLayout. Default = shown.
  const [hideReports, setHideReports] = useState(false)
  const [newGroup, setNewGroup] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  // Move/Duplicate choice dialog when an item is assigned to a DIFFERENT group.
  const [choice, setChoice] = useState<{ href: string; from: string; to: string } | null>(null)

  useEffect(() => {
    api('/api/v1/commcalc/nav-config').then((c: any) => {
      const items: Ov = c?.layout?.items || {}
      setOv(items)
      setHideReports(!!c?.layout?.hideReportsDirectory)
      // Extra groups persist from TWO sources so a group survives a reload even with no items assigned:
      // (1) the saved `layout.groups` list (admin-created, possibly empty); (2) any group referenced by
      // an item override's primary `group` or `also[]` that isn't a built-in group.
      const referenced: string[] = []
      Object.values(items).forEach(v => {
        if (v?.group) referenced.push(v.group)
        ;(v?.also || []).forEach(g => referenced.push(g))
      })
      const saved: string[] = Array.isArray(c?.layout?.groups) ? c.layout.groups : []
      const extra = Array.from(new Set([...saved, ...referenced].filter(g => g && !defaultGroups.includes(g)))) as string[]
      setExtraGroups(extra)
      setGroupOrder(Array.isArray(c?.layout?.groupOrder) ? c.layout.groupOrder : [])
      const om = (x: any) => (x && typeof x === 'object' && !Array.isArray(x)) ? x : {}
      setItemOrder(om(c?.layout?.itemOrder))
      setSubOrder(om(c?.layout?.subOrder))
    }).catch(() => {}).finally(() => setLoading(false))
  }, [defaultGroups])

  const groupOptions = [...defaultGroups, ...extraGroups]
  const primaryOf = (href: string) => ov[href]?.group || defGroupByHref[href] || ''
  const alsoOf = (href: string) => ov[href]?.also || []
  const hiddenOf = (href: string) => !!ov[href]?.hidden

  // MOVE: set the primary group (undefined when it equals the built-in default). Also drop the target
  // from `also` so it never lives in one group twice.
  const moveTo = (href: string, g: string) => setOv(o => {
    const def = defGroupByHref[href]
    const cur = o[href] || {}
    const also = (cur.also || []).filter(x => x !== g)
    const next: ItemOv = { ...cur, group: g === def ? undefined : g, also: also.length ? also : undefined }
    return { ...o, [href]: next }
  })
  // DUPLICATE: add the target group to `also` (unless it's already the primary or already there).
  const addAlso = (href: string, g: string) => setOv(o => {
    const cur = o[href] || {}
    if (g === primaryOf(href) || (cur.also || []).includes(g)) return o
    return { ...o, [href]: { ...cur, also: [...(cur.also || []), g] } }
  })
  const removeAlso = (href: string, g: string) => setOv(o => {
    const cur = o[href] || {}
    const also = (cur.also || []).filter(x => x !== g)
    return { ...o, [href]: { ...cur, also: also.length ? also : undefined } }
  })
  const setHidden = (href: string, h: boolean) => setOv(o => ({ ...o, [href]: { ...o[href], hidden: h || undefined } }))
  const subOf = (href: string) => ov[href]?.sub || ''
  const setSub = (href: string, s: string) => setOv(o => ({ ...o, [href]: { ...o[href], sub: s.trim() || undefined } }))

  // The SAME "listed first, unlisted keep their natural place after" rule applyNavLayout uses. Sharing
  // the rule is the point: the editor must show exactly what the sidebar will render, or the admin is
  // dragging against a preview that lies.
  const rank = (arr: string[], want?: string[]) => {
    if (!want || !want.length) return arr
    return [...arr].sort((a, b) => {
      const ia = want.indexOf(a), ib = want.indexOf(b)
      if (ia === -1 && ib === -1) return arr.indexOf(a) - arr.indexOf(b)
      if (ia === -1) return 1
      if (ib === -1) return -1
      return ia - ib
    })
  }
  // Drop `from` at `to`'s position. Returns the FULL list so the saved order is explicit and stable —
  // saving only the moved key would leave the rest at the mercy of a future code-default change.
  const reorder = (list: string[], from: string, to: string) => {
    const a = [...list]; const i = a.indexOf(from), j = a.indexOf(to)
    if (i < 0 || j < 0 || i === j) return a
    a.splice(j, 0, a.splice(i, 1)[0]); return a
  }

  // When the Group dropdown targets a DIFFERENT group, ASK move-or-duplicate (RULE THREE explicit pick).
  const onGroupPick = (href: string, g: string) => {
    const cur = primaryOf(href)
    if (!g || g === cur) return
    setChoice({ href, from: cur, to: g })
  }

  const addGroup = () => {
    const g = newGroup.trim()
    if (g && !groupOptions.includes(g)) setExtraGroups(x => [...x, g])
    setNewGroup('')
  }
  const removeGroup = (g: string) => {
    const users = allHrefs.filter(h => primaryOf(h) === g || alsoOf(h).includes(g))
    if (users.length && !confirm(`"${g}" still holds ${users.length} item(s). Removing the group will move those items back to their default group / drop the duplicate. Continue?`)) return
    setOv(o => {
      const next: Ov = {}
      Object.entries(o).forEach(([h, v]) => {
        const def = defGroupByHref[h]
        const group = v.group === g ? undefined : v.group  // primary pointing here → revert to default
        const also = (v.also || []).filter(x => x !== g)
        const entry: ItemOv = { ...v, group: group === def ? undefined : group, also: also.length ? also : undefined }
        if (entry.group || entry.hidden || (entry.also && entry.also.length)) next[h] = entry
      })
      return next
    })
    setExtraGroups(x => x.filter(x2 => x2 !== g))
  }

  const allHrefs = useMemo(() => NAV.flatMap(g => g.items.map(it => it.href)), [])
  const dirty = Object.values(ov).filter(v => v && ((v.group || '').trim() || (v.sub || '').trim() || v.hidden || (v.also && v.also.length))).length
    + (groupOrder.length ? 1 : 0) + Object.keys(itemOrder).length + Object.keys(subOrder).length

  function buildPayload() {
    const items: Ov = {}
    Object.entries(ov).forEach(([h, v]) => {
      const g = (v?.group || '').trim()
      const sub = (v?.sub || '').trim()
      const also = (v?.also || []).map(x => (x || '').trim()).filter((x, i, a) => x && x !== g && a.indexOf(x) === i)
      if (g || sub || v?.hidden || also.length) {
        items[h] = { ...(g ? { group: g } : {}), ...(sub ? { sub } : {}), ...(v?.hidden ? { hidden: true } : {}), ...(also.length ? { also } : {}) }
      }
    })
    // Emit an order ONLY where the admin actually set one. An empty array would be indistinguishable from
    // a deliberate wipe on the way back in, and would freeze the menu against future shipped pages.
    const om = (m: Record<string, string[]>) => {
      const out: Record<string, string[]> = {}
      Object.entries(m).forEach(([k, v]) => { if (k && v?.length) out[k] = v })
      return out
    }
    const io = om(itemOrder), so = om(subOrder)
    // hideReportsDirectory rides in the SAME layout object (no new storage). Only emitted when true, so a
    // tenant that never toggles it stores byte-identically to before.
    return {
      items, groups: extraGroups,
      ...(hideReports ? { hideReportsDirectory: true } : {}),
      ...(groupOrder.length ? { groupOrder } : {}),
      ...(Object.keys(so).length ? { subOrder: so } : {}),
      ...(Object.keys(io).length ? { itemOrder: io } : {}),
    }
  }

  async function save() {
    setSaving(true); setMsg('')
    try { await api('/api/v1/commcalc/nav-layout', { method: 'POST', body: JSON.stringify(buildPayload()) }); setMsg('Saved ✓ — reload the page to see the menu update.') }
    catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
    setSaving(false)
  }
  async function resetAll() {
    if (!confirm('Reset the whole menu back to the built-in layout?')) return
    setSaving(true); setMsg('')
    try { await api('/api/v1/commcalc/nav-layout', { method: 'POST', body: JSON.stringify({ items: {}, groups: [] }) }); setOv({}); setExtraGroups([]); setHideReports(false); setGroupOrder([]); setItemOrder({}); setSubOrder({}); setMsg('Reset to defaults — reload to see it.') }
    catch (e: any) { setMsg('Reset failed: ' + (e?.message || e)) }
    setSaving(false)
  }

  const chip = (text: string, onRemove?: () => void, color = 'var(--accent)') => (
    <span style={{ fontSize: 11, color, background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 10, padding: '1px 6px', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      {text}{onRemove && <button onClick={onRemove} title="Remove" style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--text3)', padding: 0, fontSize: 12, lineHeight: 1 }}>✕</button>}
    </span>
  )

  const itemRow = (href: string, homeGroup: string) => {
    const meta = labelByHref[href] || { label: href, icon: '•' }
    const primary = primaryOf(href)
    const also = alsoOf(href)
    const hidden = hiddenOf(href)
    const moved = primary !== homeGroup
    const sub = subOf(href)
    const being = drag?.kind === 'item' && drag.key === href
    return (
      <div key={href} draggable
        onDragStart={e => { e.stopPropagation(); setDrag({ kind: 'item', group: homeGroup, key: href }) }}
        onDragEnd={() => setDrag(null)}
        // Reordering is confined to one group on purpose: a cross-group drag is a MOVE, and a move must
        // go through the move-or-duplicate dialog rather than being inferred from where a row was dropped.
        onDragOver={e => { if (drag?.kind === 'item' && drag.group === homeGroup && drag.key !== href) e.preventDefault() }}
        onDrop={e => {
          if (drag?.kind !== 'item' || drag.group !== homeGroup || drag.key === href) return
          e.preventDefault(); e.stopPropagation()
          setItemOrder(m => ({ ...m, [homeGroup]: reorder(orderedItems(homeGroup), drag.key, href) }))
          setDrag(null)
        }}
        style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid var(--border)', opacity: hidden ? 0.55 : (being ? 0.35 : 1), flexWrap: 'wrap' }}>
        <span title="Drag to reorder within this group" aria-hidden
          style={{ cursor: 'grab', color: 'var(--text3)', userSelect: 'none', fontSize: 13 }}>⠿</span>
        <span style={{ width: 22, textAlign: 'center' }}>{meta.icon}</span>
        <span style={{ flex: 1, fontSize: 13, minWidth: 160 }}>
          {meta.label}
          {moved && <span style={{ fontSize: 11, color: 'var(--accent)', marginLeft: 6 }}>→ {primary}</span>}
          {sub && <span style={{ marginLeft: 6 }}>{chip('in: ' + sub, () => setSub(href, ''))}</span>}
          {also.map(g => <span key={g} style={{ marginLeft: 6 }}>{chip('also: ' + g, () => removeAlso(href, g))}</span>)}
        </span>
        <label style={{ fontSize: 12, color: 'var(--text3)' }}>Group&nbsp;
          <select style={inp} value={primary} onChange={e => onGroupPick(href, e.target.value)}>
            {groupOptions.map(gn => <option key={gn} value={gn}>{gn}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 12, color: 'var(--text3)' }}>Sub&nbsp;
          <select style={inp} value={sub} onChange={e => onSubPick(href, e.target.value)}
            title="Nest this item under a sub-heading inside its group">
            <option value="">— none —</option>
            {subsIn(primary).map(s => <option key={s} value={s}>{s}</option>)}
            <option value="__new__">＋ New sub-category…</option>
          </select>
        </label>
        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
          <input type="checkbox" checked={hidden} onChange={e => setHidden(href, e.target.checked)} /> Hide
        </label>
      </div>
    )
  }

  // items whose PRIMARY group is this group (built-in or admin-created), in the admin's saved order.
  const itemsWithPrimary = (g: string) => allHrefs.filter(h => primaryOf(h) === g)
  const orderedItems = (g: string) => rank(itemsWithPrimary(g), itemOrder[g])
  const itemsAlsoIn = (g: string) => allHrefs.filter(h => primaryOf(h) !== g && alsoOf(h).includes(g))
  // Sub-category names actually in use inside a group — derived from the items, never stored separately,
  // so a sub cannot outlive its last item and go stale.
  const subsIn = (g: string) => {
    const names: string[] = []
    orderedItems(g).forEach(h => { const s = subOf(h); if (s && !names.includes(s)) names.push(s) })
    return rank(names, subOrder[g])
  }
  const onSubPick = (href: string, v: string) => {
    if (v === '__new__') { const n = (prompt('Name the new sub-category') || '').trim(); if (n) setSub(href, n); return }
    setSub(href, v)
  }
  const allGroupsOrdered = rank([...defaultGroups, ...extraGroups], groupOrder)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Menu Layout</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Drag <b>⠿</b> to reorder groups and the items inside them, nest items under a <b>sub-category</b>, move an item to another group, show a duplicate copy, or hide it — applies to everyone in your company. Anything you don&apos;t touch keeps its usual place, and newly released pages still appear on their own.</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{msg}</span>}
          <button className="btn" onClick={resetAll} disabled={saving}>↺ Reset to defaults</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : `💾 Save${dirty ? ` (${dirty})` : ''}`}</button>
        </div>
      </div>

      <div className="card" style={{ padding: 12, marginBottom: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
          <input type="checkbox" checked={!hideReports} onChange={e => setHideReports(!e.target.checked)} />
          Show the Reports directory (categorized report shortcuts below the module groups)
        </label>
        <span style={{ fontSize: 12, color: 'var(--text2)' }}>
          Turn this OFF to hide the entire “Reports · …” shortcut area from the sidebar for everyone in your
          company. Your module groups are unchanged — each report still appears in its own module group.
          Click <b>Save</b> to apply.
        </span>
      </div>

      <div className="card" style={{ padding: 12, marginBottom: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: 'var(--text2)' }}>Need a new menu group? Create one — it stays even before you add items, then assign items to it below:</span>
        <input style={{ ...inp, width: 180 }} placeholder="New group name" value={newGroup} onChange={e => setNewGroup(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') addGroup() }} />
        <button className="btn" onClick={addGroup}>＋ Add group</button>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          {/* ONE ordered list of every group — built-in and admin-created alike. The sidebar renders a
              single ordered list too (applyNavLayout), so splitting them here would let an admin drag a
              group into a position the sidebar cannot reproduce. */}
          {allGroupsOrdered.map(gname => {
            const isExtra = extraGroups.includes(gname)
            const primaries = orderedItems(gname)
            const dups = itemsAlsoIn(gname)
            const subs = subsIn(gname)
            const beingG = drag?.kind === 'group' && drag.key === gname
            return (
              <div key={gname} className="card"
                onDragOver={e => { if (drag?.kind === 'group' && drag.key !== gname) e.preventDefault() }}
                onDrop={e => {
                  if (drag?.kind !== 'group' || drag.key === gname) return
                  e.preventDefault()
                  setGroupOrder(reorder(allGroupsOrdered, drag.key, gname))
                  setDrag(null)
                }}
                style={{ marginBottom: 14, padding: 14, opacity: beingG ? 0.4 : 1, ...(isExtra ? { borderColor: 'var(--accent)' } : {}) }}>
                <div draggable
                  onDragStart={() => setDrag({ kind: 'group', key: gname })}
                  onDragEnd={() => setDrag(null)}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, cursor: 'grab' }}>
                  <span title="Drag to reorder this group in the sidebar" aria-hidden
                    style={{ color: 'var(--text3)', userSelect: 'none', fontSize: 13 }}>⠿</span>
                  <span style={{ fontWeight: 700, fontSize: 14 }}>{gname}</span>
                  {isExtra && <span style={{ fontSize: 11, color: 'var(--accent)', background: 'var(--bg2)', borderRadius: 8, padding: '0 6px' }}>new group</span>}
                  {subs.length > 0 && <span style={{ fontSize: 11, color: 'var(--text3)' }}>{subs.length} sub-categor{subs.length === 1 ? 'y' : 'ies'}: {subs.join(' · ')}</span>}
                  <span style={{ flex: 1 }} />
                  {isExtra && <button className="btn btn-sm" onClick={() => removeGroup(gname)}>🗑 Remove group</button>}
                </div>
                {primaries.length === 0 && dups.length === 0
                  ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>No items yet — use an item&apos;s <b>Group</b> dropdown to <b>move</b> or add a <b>duplicate</b> here. This empty group is saved and will still be here next time.</div>
                  : (
                    <>
                      {primaries.map(h => itemRow(h, gname))}
                      {dups.map(h => {
                        const meta = labelByHref[h] || { label: h, icon: '•' }
                        return (
                          <div key={'dup-' + h} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
                            <span style={{ width: 22, textAlign: 'center' }}>{meta.icon}</span>
                            <span style={{ flex: 1, fontSize: 13 }}>{meta.label} {chip('duplicate — primary in ' + primaryOf(h))}</span>
                            <button className="btn btn-sm" onClick={() => removeAlso(h, gname)}>Remove copy</button>
                          </div>
                        )
                      })}
                    </>
                  )}
              </div>
            )
          })}
        </>
      )}

      {choice && (() => {
        const meta = labelByHref[choice.href] || { label: choice.href, icon: '•' }
        return (
          <div onClick={() => setChoice(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 2000 }}>
            <div onClick={e => e.stopPropagation()} className="card" style={{ padding: 20, maxWidth: 420, width: '90%' }}>
              <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 6 }}>{meta.icon} {meta.label}</div>
              <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 16px' }}>
                Assign to <b>{choice.to}</b> — do you want to <b>move</b> it out of <b>{choice.from}</b>, or keep it in <b>{choice.from}</b> and also show a <b>duplicate</b> copy under <b>{choice.to}</b>?
              </p>
              <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                <button className="btn" onClick={() => setChoice(null)}>Cancel</button>
                <button className="btn" onClick={() => { addAlso(choice.href, choice.to); setChoice(null) }}>＋ Add duplicate in {choice.to}</button>
                <button className="btn btn-primary" onClick={() => { moveTo(choice.href, choice.to); setChoice(null) }}>→ Move to {choice.to}</button>
              </div>
            </div>
          </div>
        )
      })()}
    </div>
  )
}
