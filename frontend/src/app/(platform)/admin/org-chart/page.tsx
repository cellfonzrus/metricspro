'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api } from '@/lib/client'

// Employee Org Chart — the PEOPLE view of the org tree, rendered as a GRAPHIC top-down chart: each
// org unit is a box (level, name, manager(s), people + sub-unit counts) connected to its parent by
// CSS-drawn connector lines (no external/CDN chart library — pure HTML/CSS pseudo-element connectors).
// Subtrees collapse; the chart scrolls horizontally INSIDE its own container (never the page body);
// clicking a unit or a person opens a detail card; Print/PDF exports just the chart. Complements
// /admin/org (which edits the unit tree, levels, stores). Data is org-scoped exactly as before
// (org_id resolved from the session by the tenant middleware — the api() calls carry no explicit org).
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
  const [detailUnit, setDetailUnit] = useState<string | null>(null)
  const [detailEmp, setDetailEmp] = useState<string | null>(null)

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

  // ── derived maps (memoized on the raw data) ─────────────────────────────────────────────────────
  const levelById = useMemo(() => { const m: Record<number, Level> = {}; levels.forEach(l => { m[l.id] = l }); return m }, [levels])
  const levelName = useCallback((u: Unit) => (u.level_id && levelById[u.level_id] ? levelById[u.level_id].name : 'Unit'), [levelById])
  const rankOf = useCallback((u: Unit) => (u.level_id && levelById[u.level_id] ? levelById[u.level_id].rank : 99), [levelById])

  const { childrenOf, roots } = useMemo(() => {
    const c: Record<string, Unit[]> = {}; const r: Unit[] = []
    units.forEach(u => { if (u.parent_id) (c[u.parent_id] ||= []).push(u); else r.push(u) })
    const sort = (a: Unit, b: Unit) => rankOf(a) - rankOf(b) || a.name.localeCompare(b.name)
    Object.values(c).forEach(list => list.sort(sort)); r.sort(sort)
    return { childrenOf: c, roots: r }
  }, [units, rankOf])

  const { empByUnit, unplaced, unitById } = useMemo(() => {
    const by: Record<string, Emp[]> = {}; const up: Emp[] = []; const ub: Record<string, Unit> = {}
    units.forEach(u => { ub[u.id] = u })
    emps.forEach(e => { if (e.resolved_unit_id) (by[e.resolved_unit_id] ||= []).push(e); else up.push(e) })
    Object.values(by).forEach(list => list.sort((a, b) => (b.is_manager ? 1 : 0) - (a.is_manager ? 1 : 0) || (a.name || '').localeCompare(b.name || '')))
    return { empByUnit: by, unplaced: up, unitById: ub }
  }, [emps, units])

  // manager employee_id → units they manage (for a person's "direct reports" count)
  const unitsManagedBy = useMemo(() => {
    const m: Record<string, Unit[]> = {}
    units.forEach(u => u.managers.forEach(mg => { (m[mg.employee_id] ||= []).push(u) }))
    return m
  }, [units])
  const empByEmployeeId = useMemo(() => { const m: Record<string, Emp> = {}; emps.forEach(e => { if (e.employee_id) m[e.employee_id] = e }); return m }, [emps])
  const empByRowId = useMemo(() => { const m: Record<string, Emp> = {}; emps.forEach(e => { m[e.id] = e }); return m }, [emps])
  const directReportsOf = useCallback((employee_id: string | null) => {
    if (!employee_id) return 0
    return (unitsManagedBy[employee_id] || []).reduce((n, u) => n + (empByUnit[u.id]?.length || 0), 0)
  }, [unitsManagedBy, empByUnit])

  const unitOptions = useMemo(() => [...units].sort((a, b) => rankOf(a) - rankOf(b) || a.name.localeCompare(b.name)), [units, rankOf])

  // Every unit with children (for expand/collapse-all)
  const branchIds = useMemo(() => units.filter(u => (childrenOf[u.id]?.length || 0) > 0).map(u => u.id), [units, childrenOf])
  const expandAll = () => setCollapsed({})
  const collapseAll = () => setCollapsed(Object.fromEntries(branchIds.map(id => [id, true])))

  const placed = emps.length - unplaced.length

  // ── graphic node ────────────────────────────────────────────────────────────────────────────────
  const renderNode = (u: Unit) => {
    const kids = childrenOf[u.id] || []
    const people = empByUnit[u.id] || []
    const isOpen = collapsed[u.id] !== true
    const hasKids = kids.length > 0
    return (
      <li key={u.id}>
        <div className={'node' + (u.managers.length ? '' : ' mgr0')} onClick={() => { setDetailUnit(u.id); setDetailEmp(null) }} role="button" tabIndex={0}
          onKeyDown={ev => { if (ev.key === 'Enter') { setDetailUnit(u.id); setDetailEmp(null) } }}>
          <div className="lvl">{levelName(u)}</div>
          <div className="nm">{u.name}</div>
          {u.managers.length > 0
            ? <div className="mgr">👤 {u.managers[0].name}{u.managers.length > 1 ? ` +${u.managers.length - 1}` : ''}</div>
            : <div className="mgr none">no manager</div>}
          <div className="meta">
            <span>{people.length} {people.length === 1 ? 'person' : 'people'}</span>
            {hasKids && <span>· {kids.length} {kids.length === 1 ? 'unit' : 'units'}</span>}
          </div>
          {hasKids && (
            <button className="toggle" title={isOpen ? 'Collapse' : 'Expand'}
              onClick={ev => { ev.stopPropagation(); setCollapsed(s => ({ ...s, [u.id]: isOpen })) }}>
              {isOpen ? '▾ collapse' : `▸ expand (${kids.length})`}
            </button>
          )}
        </div>
        {hasKids && isOpen && <ul>{kids.map(renderNode)}</ul>}
      </li>
    )
  }

  const drawerUnit = detailUnit ? unitById[detailUnit] : null
  const drawerEmp = detailEmp ? empByRowId[detailEmp] : null

  return (
    <div style={{ padding: 24 }}>
      <style>{`
        .mp-oc-scroll { overflow: auto; max-width: 100%; padding: 14px 6px 26px; }
        .mp-oc ul { display: flex; justify-content: center; padding-top: 20px; list-style: none; margin: 0; position: relative; }
        .mp-oc li { list-style: none; position: relative; padding: 20px 8px 0; text-align: center; }
        .mp-oc li::before, .mp-oc li::after { content: ''; position: absolute; top: 0; right: 50%; border-top: 2px solid var(--border); width: 50%; height: 20px; }
        .mp-oc li::after { right: auto; left: 50%; border-left: 2px solid var(--border); }
        .mp-oc li:only-child::before, .mp-oc li:only-child::after { display: none; }
        .mp-oc li:only-child { padding-top: 20px; }
        .mp-oc li:first-child::before, .mp-oc li:last-child::after { border: 0 none; }
        .mp-oc li:last-child::before { border-right: 2px solid var(--border); border-radius: 0 6px 0 0; }
        .mp-oc li:first-child::after { border-radius: 6px 0 0 0; }
        .mp-oc ul ul::before { content: ''; position: absolute; top: 0; left: 50%; border-left: 2px solid var(--border); width: 0; height: 20px; }
        .mp-oc > ul { padding-top: 0; }
        .mp-oc > ul > li { padding-top: 0; }
        .mp-oc > ul > li::before, .mp-oc > ul > li::after { display: none; }
        .mp-oc .node { display: inline-flex; flex-direction: column; gap: 2px; text-align: left; vertical-align: top;
          min-width: 150px; max-width: 210px; padding: 8px 11px; border: 1px solid var(--border); border-radius: 10px;
          background: var(--surface); cursor: pointer; box-shadow: 0 1px 2px rgba(0,0,0,0.06); transition: border-color .1s, box-shadow .1s; }
        .mp-oc .node:hover { border-color: var(--accent); box-shadow: 0 3px 10px rgba(0,0,0,0.13); }
        .mp-oc .node.mgr0 { border-style: dashed; }
        .mp-oc .node .lvl { font-size: 10px; text-transform: uppercase; letter-spacing: .04em; color: var(--text3); }
        .mp-oc .node .nm { font-size: 14px; font-weight: 700; line-height: 1.2; }
        .mp-oc .node .mgr { font-size: 12px; color: var(--text2); }
        .mp-oc .node .mgr.none { color: var(--text3); font-style: italic; }
        .mp-oc .node .meta { font-size: 11px; color: var(--text3); margin-top: 2px; }
        .mp-oc .node .toggle { margin-top: 6px; align-self: flex-start; font-size: 11px; border: 1px solid var(--border);
          background: var(--bg2); color: var(--text2); border-radius: 6px; padding: 1px 7px; cursor: pointer; }
        @media print {
          body * { visibility: hidden !important; }
          .mp-oc-print-root, .mp-oc-print-root * { visibility: visible !important; }
          .mp-oc-print-root { position: absolute; left: 0; top: 0; width: 100%; }
          .mp-oc-scroll { overflow: visible !important; }
          .mp-oc .node { box-shadow: none; }
          .mp-oc .node .toggle, .oc-noprint { display: none !important; }
        }
      `}</style>

      <div className="oc-noprint" style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>👥 Employee Org Chart</h1>
        <span style={{ flex: 1 }} />
        <button className="btn btn-sm" onClick={expandAll}>Expand all</button>
        <button className="btn btn-sm" onClick={collapseAll}>Collapse all</button>
        <button className="btn btn-sm" onClick={() => window.print()}>🖨 Print / PDF</button>
        <label style={{ fontSize: 13, color: 'var(--text3)' }}>
          <input type="checkbox" checked={inactive} onChange={e => setInactive(e.target.checked)} /> include inactive
        </label>
        <a href="/admin/org" className="btn btn-sm">Edit structure →</a>
      </div>
      <p className="oc-noprint" style={{ color: 'var(--text3)', fontSize: 13, marginTop: 0 }}>
        {placed} of {emps.length} employees placed across {units.length} unit{units.length === 1 ? '' : 's'}. Click a box for its
        people &amp; manager(s); dashed = no manager assigned. People roll up via their home store; pin someone to a different
        unit from the detail card. Edit units &amp; managers on <a href="/admin/org">Org Structure</a>.
      </p>
      {err && <div className="card oc-noprint" style={{ borderColor: '#c0392b', color: '#c0392b', padding: 12, marginBottom: 12 }}>{err}</div>}

      {loading ? (
        <div style={{ padding: 24, color: 'var(--text3)' }}>Loading org chart…</div>
      ) : (
        <div className="mp-oc-print-root">
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            {roots.length === 0
              ? <div style={{ padding: 20, color: 'var(--text3)' }}>No org units yet — build the tree on <a href="/admin/org">Org Structure</a> first.</div>
              : (
                <div className="mp-oc-scroll">
                  <div className="mp-oc"><ul>{roots.map(renderNode)}</ul></div>
                </div>
              )}
          </div>

          <div className="card oc-noprint" style={{ marginTop: 16, padding: 16 }}>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>Unplaced employees {unplaced.length ? `(${unplaced.length})` : ''}</div>
            {unplaced.length === 0
              ? <div style={{ color: 'var(--text3)', fontSize: 13 }}>Everyone is placed in the chart. ✅</div>
              : unplaced.map(e => (
                <div key={e.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
                  <button onClick={() => { setDetailEmp(e.id); setDetailUnit(null) }} style={{ border: 'none', background: 'none', cursor: 'pointer', fontWeight: 600, color: 'var(--accent)', padding: 0 }}>{e.name}</button>
                  {e.role && <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {e.role}</span>}
                  <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {e.home_store ? `🏬 ${e.home_store} (store not in tree)` : 'no home store'}</span>
                  <span style={{ flex: 1 }} />
                  <select disabled={busy} value={e.org_unit_id || ''} onChange={ev => assign(e.id, ev.target.value)}
                    style={{ fontSize: 12, padding: '2px 6px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)' }}>
                    <option value="">unplaced</option>
                    {unitOptions.map(u => <option key={u.id} value={u.id}>{levelName(u)}: {u.name}</option>)}
                  </select>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* ── Unit detail card ─────────────────────────────────────────────────────────── */}
      {drawerUnit && (
        <Drawer onClose={() => setDetailUnit(null)} title={`${levelName(drawerUnit)} · ${drawerUnit.name}`}>
          <Section label="Manager(s)">
            {drawerUnit.managers.length === 0 ? <Muted>None assigned</Muted> : drawerUnit.managers.map(m => (
              <button key={m.employee_id} className="lnk" onClick={() => { const e = empByEmployeeId[m.employee_id]; if (e) { setDetailEmp(e.id); setDetailUnit(null) } }}>
                ★ {m.name}<span style={{ color: 'var(--text3)', fontSize: 11 }}> · {directReportsOf(m.employee_id)} report(s)</span>
              </button>
            ))}
          </Section>
          <Section label={`People (${(empByUnit[drawerUnit.id] || []).length})`}>
            {(empByUnit[drawerUnit.id] || []).length === 0 ? <Muted>No one rolls up here yet.</Muted> : (empByUnit[drawerUnit.id] || []).map(e => (
              <div key={e.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', flexWrap: 'wrap', borderBottom: '1px solid var(--border)' }}>
                <button className="lnk" onClick={() => { setDetailEmp(e.id); setDetailUnit(null) }} style={{ fontWeight: e.is_manager ? 700 : 500 }}>{e.is_manager ? '★ ' : ''}{e.name}</button>
                {e.role && <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {e.role}</span>}
                {e.home_store && <span style={{ fontSize: 11, color: 'var(--text3)' }}>· 🏬 {e.home_store}</span>}
                {e.org_unit_id && <span style={{ fontSize: 10, color: '#1E3A5F', background: '#e7eefc', padding: '0 5px', borderRadius: 8 }}>pinned</span>}
              </div>
            ))}
          </Section>
          {(childrenOf[drawerUnit.id]?.length || 0) > 0 && (
            <Section label={`Sub-units (${childrenOf[drawerUnit.id].length})`}>
              {childrenOf[drawerUnit.id].map(c => (
                <button key={c.id} className="lnk" onClick={() => setDetailUnit(c.id)}>{levelName(c)}: {c.name}</button>
              ))}
            </Section>
          )}
          <style>{`.lnk{display:block;border:none;background:none;text-align:left;cursor:pointer;color:var(--accent);padding:2px 0;font-size:13px}`}</style>
        </Drawer>
      )}

      {/* ── Person detail card ───────────────────────────────────────────────────────── */}
      {drawerEmp && (
        <Drawer onClose={() => setDetailEmp(null)} title={`${drawerEmp.is_manager ? '★ ' : ''}${drawerEmp.name}`}>
          <Row k="Role" v={drawerEmp.role || '—'} />
          <Row k="Home store" v={drawerEmp.home_store || '—'} />
          <Row k="Placed in" v={drawerEmp.resolved_unit_id && unitById[drawerEmp.resolved_unit_id]
            ? `${levelName(unitById[drawerEmp.resolved_unit_id])}: ${unitById[drawerEmp.resolved_unit_id].name}${drawerEmp.placed_by === 'home_store' ? ' (via home store)' : ' (pinned)'}`
            : 'Unplaced'} />
          {drawerEmp.is_manager && <Row k="Direct reports" v={String(directReportsOf(drawerEmp.employee_id))} />}
          {drawerEmp.employee_id && (unitsManagedBy[drawerEmp.employee_id]?.length || 0) > 0 &&
            <Row k="Manages" v={unitsManagedBy[drawerEmp.employee_id].map(u => u.name).join(', ')} />}
          <Section label="Reassign to unit">
            <select disabled={busy} value={drawerEmp.org_unit_id || ''} onChange={ev => assign(drawerEmp.id, ev.target.value)}
              style={{ fontSize: 13, padding: '4px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', width: '100%' }}>
              <option value="">{drawerEmp.placed_by === 'home_store' ? 'via home store (default)' : 'unplaced'}</option>
              {unitOptions.map(u => <option key={u.id} value={u.id}>{levelName(u)}: {u.name}</option>)}
            </select>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>Pin managers / roving staff to a specific unit; blank falls back to their home-store rollup.</div>
          </Section>
        </Drawer>
      )}
    </div>
  )
}

function Drawer({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="oc-noprint" onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', zIndex: 2000, display: 'flex', justifyContent: 'flex-end' }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{ width: 380, maxWidth: '92%', height: '100%', borderRadius: 0, padding: 20, overflowY: 'auto', boxShadow: '-4px 0 20px rgba(0,0,0,0.15)' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 14 }}>
          <h2 style={{ fontSize: 17, fontWeight: 700, margin: 0, flex: 1 }}>{title}</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 20, color: 'var(--text3)', lineHeight: 1 }}>✕</button>
        </div>
        {children}
      </div>
    </div>
  )
}
function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.04em', color: 'var(--text3)', marginBottom: 4 }}>{label}</div>
      {children}
    </div>
  )
}
function Row({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: 'flex', gap: 10, padding: '5px 0', borderBottom: '1px solid var(--border)', fontSize: 13 }}>
      <span style={{ color: 'var(--text3)', minWidth: 110 }}>{k}</span><span style={{ fontWeight: 500 }}>{v}</span>
    </div>
  )
}
function Muted({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 13, color: 'var(--text3)' }}>{children}</div>
}
