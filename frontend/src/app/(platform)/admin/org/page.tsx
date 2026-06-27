'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'

type Level = { id: number; name: string; rank: number }
type Manager = { employee_id: string; name: string }
type Unit = {
  id: string; parent_id: string | null; level_id: number | null; name: string
  code: string | null; sort_order: number; store_count: number; managers: Manager[]
}
type Store = { store_code: string; address: string | null; market: string | null; org_unit_id: string | null }
type Emp = { employee_id: string | null; name: string }
type Tree = { levels: Level[]; units: Unit[]; unassigned_stores: Store[] }

export default function OrgStructurePage() {
  const [tree, setTree] = useState<Tree | null>(null)
  const [emps, setEmps] = useState<Emp[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [actionsFor, setActionsFor] = useState<string | null>(null)
  const [showLevels, setShowLevels] = useState(false)

  const reload = useCallback(async () => {
    setErr('')
    try {
      const [t, e] = await Promise.all([
        api('/api/v1/storeops/org/tree'),
        api('/api/v1/storeops/employees'),
      ])
      setTree(t)
      setEmps((e || []).filter((x: Emp) => x.employee_id))
    } catch (ex: any) {
      setErr(ex?.message || 'Failed to load org tree')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  const run = async (fn: () => Promise<any>) => {
    setBusy(true); setErr('')
    try { await fn(); await reload() }
    catch (ex: any) { setErr(ex?.message || 'Action failed') }
    finally { setBusy(false) }
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--text3)' }}>Loading org structure…</div>

  const levels = tree?.levels || []
  const units = tree?.units || []
  const levelById: Record<number, Level> = {}
  levels.forEach(l => { levelById[l.id] = l })
  const rankOf = (u: Unit) => (u.level_id && levelById[u.level_id] ? levelById[u.level_id].rank : 99)
  const levelName = (u: Unit) => (u.level_id && levelById[u.level_id] ? levelById[u.level_id].name : '—')

  // children map
  const childrenOf: Record<string, Unit[]> = {}
  const roots: Unit[] = []
  units.forEach(u => {
    if (u.parent_id) (childrenOf[u.parent_id] ||= []).push(u)
    else roots.push(u)
  })
  const sortU = (a: Unit, b: Unit) => (a.sort_order - b.sort_order) || a.name.localeCompare(b.name)
  roots.sort(sortU); Object.values(childrenOf).forEach(arr => arr.sort(sortU))

  const descendantIds = (id: string): Set<string> => {
    const out = new Set<string>()
    const walk = (x: string) => (childrenOf[x] || []).forEach(c => { out.add(c.id); walk(c.id) })
    walk(id)
    return out
  }

  // ── actions ──
  const seed = () => {
    if (!confirm('Rebuild Company → Market → Stores from your stores list?\nManual placements you already made are kept.')) return
    run(() => api('/api/v1/storeops/org/seed', { method: 'POST', body: '{}' }))
  }
  const addChild = (parent: Unit | null) => {
    const name = prompt(parent ? `New unit under "${parent.name}":` : 'New top-level unit name:')
    if (!name?.trim()) return
    const wantRank = parent ? rankOf(parent) + 1 : 0
    const lvl = levels.find(l => l.rank === wantRank)
    run(() => api('/api/v1/storeops/org/units', {
      method: 'POST',
      body: JSON.stringify({ name: name.trim(), parent_id: parent?.id || null, level_id: lvl?.id || null }),
    }))
  }
  const rename = (u: Unit) => {
    const name = prompt('Rename unit:', u.name)
    if (!name?.trim() || name.trim() === u.name) return
    run(() => api(`/api/v1/storeops/org/units/${u.id}`, { method: 'PUT', body: JSON.stringify({ name: name.trim() }) }))
  }
  const move = (u: Unit, parentId: string) =>
    run(() => api(`/api/v1/storeops/org/units/${u.id}`, { method: 'PUT', body: JSON.stringify({ parent_id: parentId || null }) }))
  const setLevel = (u: Unit, levelId: string) =>
    run(() => api(`/api/v1/storeops/org/units/${u.id}`, { method: 'PUT', body: JSON.stringify({ level_id: levelId ? Number(levelId) : null }) }))
  const del = (u: Unit) => {
    const kids = (childrenOf[u.id] || []).length
    if (!confirm(`Delete "${u.name}"${kids ? ` and its ${kids} child unit(s)` : ''}?\nAny stores under it become Unassigned.`)) return
    run(() => api(`/api/v1/storeops/org/units/${u.id}`, { method: 'DELETE' }))
  }
  const addMgr = (u: Unit, eid: string) => {
    if (!eid) return
    run(() => api(`/api/v1/storeops/org/units/${u.id}/managers`, { method: 'POST', body: JSON.stringify({ employee_id: eid }) }))
  }
  const removeMgr = (u: Unit, eid: string) =>
    run(() => api(`/api/v1/storeops/org/units/${u.id}/managers/${encodeURIComponent(eid)}`, { method: 'DELETE' }))
  const assignStore = (store_code: string, unitId: string) => {
    if (!unitId) return
    run(() => api(`/api/v1/storeops/org/stores/${encodeURIComponent(store_code)}/unit`, { method: 'PUT', body: JSON.stringify({ unit_id: unitId }) }))
  }

  const moveTargets = (u: Unit): Unit[] => {
    const bad = descendantIds(u.id); bad.add(u.id)
    return units.filter(x => !bad.has(x.id)).sort((a, b) => rankOf(a) - rankOf(b) || a.name.localeCompare(b.name))
  }

  const renderNode = (u: Unit, depth: number) => {
    const kids = childrenOf[u.id] || []
    const isOpen = expanded[u.id] !== false // default open
    const showA = actionsFor === u.id
    return (
      <div key={u.id}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 8px',
          marginLeft: depth * 22, borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
          <button onClick={() => setExpanded(s => ({ ...s, [u.id]: !isOpen }))}
            style={{ width: 18, border: 'none', background: 'none', cursor: kids.length ? 'pointer' : 'default',
              color: 'var(--text3)' }}>{kids.length ? (isOpen ? '▾' : '▸') : '•'}</button>
          <span style={{ fontSize: 11, padding: '1px 7px', borderRadius: 10, background: 'var(--bg2)',
            color: 'var(--text3)', whiteSpace: 'nowrap' }}>{levelName(u)}</span>
          <span style={{ fontWeight: 600 }}>{u.name}</span>
          {u.store_count > 0 && <span style={{ fontSize: 12, color: 'var(--text3)' }}>· {u.store_count} store{u.store_count > 1 ? 's' : ''}</span>}
          {u.managers.map(m => (
            <span key={m.employee_id} style={{ fontSize: 12, padding: '1px 6px', borderRadius: 10,
              background: 'var(--accent-soft, #eef)', color: 'var(--text2)' }}>
              👤 {m.name} <button title="Remove manager" onClick={() => removeMgr(u, m.employee_id)}
                style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--text3)' }}>×</button>
            </span>
          ))}
          <span style={{ flex: 1 }} />
          <button className="btn btn-sm" disabled={busy} onClick={() => setActionsFor(showA ? null : u.id)}
            style={{ fontSize: 12 }}>{showA ? 'Close' : '⋯'}</button>
        </div>
        {showA && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', padding: '8px 8px 12px',
            marginLeft: depth * 22 + 26, background: 'var(--bg2)', borderBottom: '1px solid var(--border)' }}>
            <button className="btn btn-sm" disabled={busy} onClick={() => addChild(u)}>+ Add child</button>
            <button className="btn btn-sm" disabled={busy} onClick={() => rename(u)}>Rename</button>
            <button className="btn btn-sm" disabled={busy} onClick={() => del(u)} style={{ color: '#c0392b' }}>Delete</button>
            <label style={{ fontSize: 12, color: 'var(--text3)' }}>Move under:
              <select disabled={busy} defaultValue="" onChange={e => move(u, e.target.value)} style={{ marginLeft: 4 }}>
                <option value="">(top level)</option>
                {moveTargets(u).map(t => <option key={t.id} value={t.id}>{levelName(t)}: {t.name}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12, color: 'var(--text3)' }}>Level:
              <select disabled={busy} value={u.level_id || ''} onChange={e => setLevel(u, e.target.value)} style={{ marginLeft: 4 }}>
                <option value="">—</option>
                {levels.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12, color: 'var(--text3)' }}>+ Manager:
              <select disabled={busy} value="" onChange={e => { addMgr(u, e.target.value); e.target.value = '' }} style={{ marginLeft: 4 }}>
                <option value="">pick employee…</option>
                {emps.map(e => <option key={e.employee_id!} value={e.employee_id!}>{e.name}</option>)}
              </select>
            </label>
          </div>
        )}
        {isOpen && kids.map(c => renderNode(c, depth + 1))}
      </div>
    )
  }

  const unassigned = tree?.unassigned_stores || []

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🌳 Org Structure</h1>
        <span style={{ flex: 1 }} />
        <button className="btn" disabled={busy} onClick={() => setShowLevels(s => !s)}>Manage levels</button>
        <button className="btn" disabled={busy} onClick={() => addChild(null)}>+ Top-level unit</button>
        <button className="btn btn-primary" disabled={busy} onClick={seed}>Seed from stores</button>
      </div>
      <p style={{ color: 'var(--text3)', fontSize: 13, marginTop: 0 }}>
        A configurable tree of org units. Assign a manager to any node and they see every store + rep in that
        node’s subtree. Stores attach to a node; reps follow their home store. Levels are user-defined — add a
        “Region” or “District” anytime.
      </p>
      {err && <div className="card" style={{ borderColor: '#c0392b', color: '#c0392b', padding: 12, marginBottom: 12 }}>{err}</div>}

      {showLevels && <LevelsPanel levels={levels} busy={busy} run={run} />}

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {roots.length === 0
          ? <div style={{ padding: 20, color: 'var(--text3)' }}>No units yet. Click <b>Seed from stores</b> to build Company → Market → Stores from your store list, then customize.</div>
          : roots.map(r => renderNode(r, 0))}
      </div>

      <div className="card" style={{ marginTop: 16, padding: 16 }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Unassigned stores {unassigned.length ? `(${unassigned.length})` : ''}</div>
        {unassigned.length === 0
          ? <div style={{ color: 'var(--text3)', fontSize: 13 }}>Every store is placed in the tree. ✅</div>
          : unassigned.map(s => (
            <div key={s.store_code} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0',
              borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 600 }}>{s.store_code}</span>
              <span style={{ color: 'var(--text3)', fontSize: 13 }}>{s.address}</span>
              {s.market && <span style={{ fontSize: 12, color: 'var(--text3)' }}>· {s.market}</span>}
              <span style={{ flex: 1 }} />
              <select disabled={busy} defaultValue="" onChange={e => assignStore(s.store_code, e.target.value)}>
                <option value="">move to unit…</option>
                {units.sort((a, b) => rankOf(a) - rankOf(b) || a.name.localeCompare(b.name))
                  .map(u => <option key={u.id} value={u.id}>{levelName(u)}: {u.name}</option>)}
              </select>
            </div>
          ))}
      </div>
    </div>
  )
}

function LevelsPanel({ levels, busy, run }:
  { levels: Level[]; busy: boolean; run: (fn: () => Promise<any>) => Promise<void> }) {
  const add = () => {
    const name = prompt('New level name (e.g. Region, District):')
    if (!name?.trim()) return
    run(() => api('/api/v1/storeops/org/levels', { method: 'POST', body: JSON.stringify({ name: name.trim() }) }))
  }
  const rename = (l: Level) => {
    const name = prompt('Rename level:', l.name)
    if (!name?.trim() || name.trim() === l.name) return
    run(() => api(`/api/v1/storeops/org/levels/${l.id}`, { method: 'PUT', body: JSON.stringify({ name: name.trim() }) }))
  }
  const setRank = (l: Level, rank: number) =>
    run(() => api(`/api/v1/storeops/org/levels/${l.id}`, { method: 'PUT', body: JSON.stringify({ rank }) }))
  const del = (l: Level) => {
    if (!confirm(`Delete level "${l.name}"? (only if no units use it)`)) return
    run(() => api(`/api/v1/storeops/org/levels/${l.id}`, { method: 'DELETE' }))
  }
  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontWeight: 700 }}>Levels (depth order)</div>
        <span style={{ flex: 1 }} />
        <button className="btn btn-sm" disabled={busy} onClick={add}>+ Add level</button>
      </div>
      {[...levels].sort((a, b) => a.rank - b.rank).map(l => (
        <div key={l.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '5px 0', borderBottom: '1px solid var(--border)' }}>
          <input type="number" defaultValue={l.rank} disabled={busy} style={{ width: 56 }}
            onBlur={e => { const r = Number(e.target.value); if (r !== l.rank) setRank(l, r) }} />
          <span style={{ fontWeight: 600 }}>{l.name}</span>
          <span style={{ flex: 1 }} />
          <button className="btn btn-sm" disabled={busy} onClick={() => rename(l)}>Rename</button>
          <button className="btn btn-sm" disabled={busy} onClick={() => del(l)} style={{ color: '#c0392b' }}>Delete</button>
        </div>
      ))}
      {levels.length === 0 && <div style={{ color: 'var(--text3)', fontSize: 13 }}>No levels yet — “Seed from stores” creates Company + Market.</div>}
    </div>
  )
}
