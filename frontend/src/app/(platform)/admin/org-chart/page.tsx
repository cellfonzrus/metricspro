'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'

// Employee Org Chart — the PEOPLE view of the org tree. Each unit shows its manager(s) and the
// employees who roll up to it (via their home store, or a direct placement). Complements /admin/org
// (which edits the unit tree, levels, stores). Reassign anyone to a unit; "unplaced" = no home-store
// unit and no direct placement.
type Level = { id: number; name: string; rank: number }
type Manager = { employee_id: string; name: string }
type Unit = { id: string; parent_id: string | null; level_id: number | null; name: string; managers: Manager[]; store_count: number }
type Emp = {
  id: string; employee_id: string | null; name: string; home_store: string | null; role: string | null; is_active: boolean
  org_unit_id: string | null; resolved_unit_id: string | null; placed_by: string | null; is_manager: boolean
}

export default function OrgChartPage() {
  const [units, setUnits] = useState<Unit[]>([])
  const [levels, setLevels] = useState<Level[]>([])
  const [emps, setEmps] = useState<Emp[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [inactive, setInactive] = useState(false)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const reload = useCallback(async () => {
    setErr('')
    try {
      const [t, e] = await Promise.all([
        api('/api/v1/storeops/org/tree'),
        api(`/api/v1/storeops/org/employees${inactive ? '?include_inactive=true' : ''}`),
      ])
      setUnits(t?.units || []); setLevels(t?.levels || []); setEmps(e?.employees || [])
    } catch (ex: any) { setErr(ex?.message || 'Failed to load org chart') }
    finally { setLoading(false) }
  }, [inactive])
  useEffect(() => { reload() }, [reload])

  const assign = (row_id: string, unit_id: string) =>
    (async () => {
      setBusy(true); setErr('')
      try { await api(`/api/v1/storeops/org/employees/${encodeURIComponent(row_id)}/unit`, { method: 'PUT', body: JSON.stringify({ unit_id: unit_id || null }) }); await reload() }
      catch (ex: any) { setErr(ex?.message || 'Assign failed') } finally { setBusy(false) }
    })()

  if (loading) return <div style={{ padding: 24, color: 'var(--text3)' }}>Loading org chart…</div>

  const levelById: Record<number, Level> = {}; levels.forEach(l => { levelById[l.id] = l })
  const levelName = (u: Unit) => (u.level_id && levelById[u.level_id] ? levelById[u.level_id].name : '—')
  const rankOf = (u: Unit) => (u.level_id && levelById[u.level_id] ? levelById[u.level_id].rank : 99)

  const childrenOf: Record<string, Unit[]> = {}; const roots: Unit[] = []
  units.forEach(u => { if (u.parent_id) (childrenOf[u.parent_id] ||= []).push(u); else roots.push(u) })

  const empByUnit: Record<string, Emp[]> = {}
  const unplaced: Emp[] = []
  emps.forEach(e => { if (e.resolved_unit_id) (empByUnit[e.resolved_unit_id] ||= []).push(e); else unplaced.push(e) })

  const unitOptions = [...units].sort((a, b) => rankOf(a) - rankOf(b) || a.name.localeCompare(b.name))
  const moveSelect = (e: Emp) => (
    <select disabled={busy} value={e.org_unit_id || ''} onChange={ev => assign(e.id, ev.target.value)}
      style={{ fontSize: 12, padding: '2px 6px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)' }}>
      <option value="">{e.placed_by === 'home_store' ? 'via home store' : 'unplaced'}</option>
      {unitOptions.map(u => <option key={u.id} value={u.id}>{levelName(u)}: {u.name}</option>)}
    </select>
  )

  const empRow = (e: Emp) => (
    <div key={e.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', flexWrap: 'wrap' }}>
      <span style={{ fontWeight: e.is_manager ? 700 : 500 }}>{e.is_manager ? '★ ' : ''}{e.name}</span>
      {e.role && <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {e.role}</span>}
      {e.home_store && <span style={{ fontSize: 11, color: 'var(--text3)' }}>· 🏬 {e.home_store}</span>}
      {e.org_unit_id && <span style={{ fontSize: 10, color: '#1E3A5F', background: '#e7eefc', padding: '0 5px', borderRadius: 8 }}>pinned</span>}
      <span style={{ flex: 1 }} />
      {moveSelect(e)}
    </div>
  )

  const renderNode = (u: Unit, depth: number) => {
    const kids = childrenOf[u.id] || []
    const people = empByUnit[u.id] || []
    const isOpen = collapsed[u.id] !== true
    return (
      <div key={u.id}>
        <div style={{ marginLeft: depth * 20, padding: '8px 8px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <button onClick={() => setCollapsed(s => ({ ...s, [u.id]: !isOpen }))}
              style={{ width: 18, border: 'none', background: 'none', cursor: 'pointer', color: 'var(--text3)' }}>{(kids.length || people.length) ? (isOpen ? '▾' : '▸') : '•'}</button>
            <span style={{ fontSize: 11, padding: '1px 7px', borderRadius: 10, background: 'var(--bg2)', color: 'var(--text3)' }}>{levelName(u)}</span>
            <span style={{ fontWeight: 700 }}>{u.name}</span>
            {u.managers.length > 0 && <span style={{ fontSize: 12, color: 'var(--text2)' }}>· 👤 {u.managers.map(m => m.name).join(', ')}</span>}
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>· {people.length} {people.length === 1 ? 'person' : 'people'}</span>
          </div>
          {isOpen && people.length > 0 && (
            <div style={{ marginLeft: 26, marginTop: 4, borderLeft: '2px solid var(--border)', paddingLeft: 12 }}>
              {people.slice().sort((a, b) => (b.is_manager ? 1 : 0) - (a.is_manager ? 1 : 0) || (a.name || '').localeCompare(b.name || '')).map(empRow)}
            </div>
          )}
        </div>
        {isOpen && kids.sort((a, b) => rankOf(a) - rankOf(b) || a.name.localeCompare(b.name)).map(c => renderNode(c, depth + 1))}
      </div>
    )
  }

  const placed = emps.length - unplaced.length

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>👥 Employee Org Chart</h1>
        <span style={{ flex: 1 }} />
        <label style={{ fontSize: 13, color: 'var(--text3)' }}>
          <input type="checkbox" checked={inactive} onChange={e => setInactive(e.target.checked)} /> include inactive
        </label>
        <a href="/admin/org" className="btn btn-sm">Edit structure →</a>
      </div>
      <p style={{ color: 'var(--text3)', fontSize: 13, marginTop: 0 }}>
        {placed} of {emps.length} employees placed. ★ = manages a unit. People roll up via their home store; pin
        someone to a different unit (managers / roving staff) with the dropdown. Edit units &amp; managers on <a href="/admin/org">Org Structure</a>.
      </p>
      {err && <div className="card" style={{ borderColor: '#c0392b', color: '#c0392b', padding: 12, marginBottom: 12 }}>{err}</div>}

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {roots.length === 0
          ? <div style={{ padding: 20, color: 'var(--text3)' }}>No org units yet — build the tree on <a href="/admin/org">Org Structure</a> first.</div>
          : roots.sort((a, b) => rankOf(a) - rankOf(b) || a.name.localeCompare(b.name)).map(r => renderNode(r, 0))}
      </div>

      <div className="card" style={{ marginTop: 16, padding: 16 }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Unplaced employees {unplaced.length ? `(${unplaced.length})` : ''}</div>
        {unplaced.length === 0
          ? <div style={{ color: 'var(--text3)', fontSize: 13 }}>Everyone is placed in the chart. ✅</div>
          : unplaced.map(e => (
            <div key={e.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 600 }}>{e.name}</span>
              {e.role && <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {e.role}</span>}
              <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {e.home_store ? `🏬 ${e.home_store} (store not in tree)` : 'no home store'}</span>
              <span style={{ flex: 1 }} />
              {moveSelect(e)}
            </div>
          ))}
      </div>
    </div>
  )
}
