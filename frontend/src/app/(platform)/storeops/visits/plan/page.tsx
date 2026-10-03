'use client'
/* DM VISIT PLAN — the market manager's board.
 *
 * Owner ask 2026-10-03: each DM owes 2 store visits a day (per-tenant, editable); the market manager
 * monitors their performance and assigns the stores; if nobody assigned by Friday evening, stores are
 * assigned where performance is low — activations and accessory sales first, then KPI — all tenant
 * configurable, with a carrier-specific dropdown for extra deliverables.
 *
 * Nothing on this page decides anything. The quota, the order and the picks all come from
 * /api/v1/storevisit/* which reads the one homes (the org tree, the Daily Targets summary, the
 * per-carrier KPI registry). The dropdown's options are served, never listed here — RULE TWO.
 */
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { SortableTh, useTableSort } from '@/components/SortableTh'

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '8px 10px', borderBottom: '1px solid var(--border)', fontSize: 13 }
const th: React.CSSProperties = { ...cell, fontWeight: 700, color: 'var(--text2)', fontSize: 12, textAlign: 'left' }
/* The ONE cell accessor both tables sort through — `@/lib/table-sort`'s comparison rules, not a
 * second sort of our own (owner directive 2026-08-10, the table-sort ratchet). */
const getCell = (row: any, field: string) => row?.[field]
const DOW = [['1', 'Monday'], ['2', 'Tuesday'], ['3', 'Wednesday'], ['4', 'Thursday'], ['5', 'Friday'], ['6', 'Saturday'], ['7', 'Sunday']]

export default function DmVisitPlanPage() {
  const [board, setBoard] = useState<any>(null)
  const [cfg, setCfg] = useState<any>(null)
  const [rules, setRules] = useState<any>(null)
  const [options, setOptions] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<any>(null)
  const [pick, setPick] = useState({ option: '', weight: '1', direction: 'low_first', sort: '100' })

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      api('/api/v1/storevisit/dm-visit-performance?days=14').catch(e => ({ error: String(e?.message || e) })),
      api('/api/v1/storevisit/visit-quota-config').catch(() => null),
      api('/api/v1/storevisit/visit-priority-rules').catch(() => null),
      api('/api/v1/storevisit/visit-priority-options').catch(() => null),
    ]).then(([b, c, r, o]) => { setBoard(b); setCfg(c); setRules(r); setOptions(o) })
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  async function saveConfig(patch: any) {
    setBusy(true)
    try {
      const d = await api('/api/v1/storevisit/visit-quota-config', { method: 'PUT', body: JSON.stringify(patch) })
      setCfg(d)
    } catch (e: any) { alert('Could not save: ' + (e?.message || e)) }
    finally { setBusy(false) }
  }

  async function addRule() {
    const opt = (options?.options || []).find((o: any) => `${o.basis}:${o.metric_key}` === pick.option)
    if (!opt) { alert('Pick a deliverable to add to the priority list.'); return }
    setBusy(true)
    try {
      await api('/api/v1/storevisit/visit-priority-rules', {
        method: 'PUT',
        body: JSON.stringify({
          basis: opt.basis, metric_key: opt.metric_key, label: opt.label,
          weight: Number(pick.weight) || 1, direction: pick.direction,
          sort: Number(pick.sort) || 100,
          carrier_id: opt.carrier_specific ? (options?.carrier_id || null) : null,
        }),
      })
      setPick({ option: '', weight: '1', direction: 'low_first', sort: '100' })
      load()
    } catch (e: any) { alert('Could not add: ' + (e?.message || e)) }
    finally { setBusy(false) }
  }

  async function removeRule(id: string, label: string) {
    if (!id) { alert('That is a house default, not a saved rule — add your own rules to change the order.'); return }
    if (!confirm(`Remove "${label}" from the priority list?`)) return
    setBusy(true)
    try { await api(`/api/v1/storevisit/visit-priority-rules/${id}`, { method: 'DELETE' }); load() }
    catch (e: any) { alert('Could not remove: ' + (e?.message || e)) }
    finally { setBusy(false) }
  }

  async function runPreview(send: boolean) {
    if (send && !confirm('Assign these stores now? Each DM-day under quota gets topped up; nothing a manager already assigned is replaced.')) return
    setBusy(true)
    try {
      const d = await api(`/api/v1/storevisit/visit-assignments/auto-fill?force=1&send=${send ? 1 : 0}`, { method: 'POST' })
      setPreview(d)
      if (send) load()
    } catch (e: any) { alert('Could not run: ' + (e?.message || e)) }
    finally { setBusy(false) }
  }

  const c = cfg?.config || {}
  const quota = board?.quota || {}
  const rows: any[] = quota.rows || []
  const activeRules: any[] = rules?.rules || []
  const opts = options?.options || []
  // Click-a-header sorting on both tables. A market manager with twenty DMs wants the shortfalls at
  // the top; the default order (day, then name) is preserved until they click.
  const qSort = useTableSort(rows, getCell)
  const rSort = useTableSort(activeRules, getCell)

  return (
    <div style={{ maxWidth: 1080 }}>
      <Link href="/storeops/visits" style={{ fontSize: 13, color: 'var(--accent)' }}>← Store visits</Link>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 4px' }}>🗺️ DM Visit Plan</h1>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 18px' }}>
        How many store visits each district manager owes a day, which stores they are assigned, and — when nobody
        assigned them by the deadline — which stores the priority order picks.
      </p>

      {loading && <p style={{ color: 'var(--text2)' }}>Loading…</p>}
      {board?.error && <div className="card" style={{ padding: 14, marginBottom: 18, color: 'var(--danger)' }}>{board.error}</div>}

      {/* ── The quota ───────────────────────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 16, marginBottom: 18 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 10px' }}>The daily quota</h2>
        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label style={{ fontSize: 13 }}>
            <div style={{ color: 'var(--text2)', marginBottom: 4 }}>Visits per DM per day</div>
            <input type="number" min={0} max={50} defaultValue={c.quota_per_day ?? 2} style={{ ...sel, width: 90 }}
              onBlur={e => { const v = Number(e.target.value); if (v !== c.quota_per_day) saveConfig({ dm_visit_quota_per_day: v }) }} />
          </label>
          <label style={{ fontSize: 13 }}>
            <div style={{ color: 'var(--text2)', marginBottom: 4 }}>Assignment deadline</div>
            <select style={sel} value={String(c.deadline_dow ?? 5)}
              onChange={e => saveConfig({ dm_visit_assign_deadline_dow: Number(e.target.value) })}>
              {DOW.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 13 }}>
            <div style={{ color: 'var(--text2)', marginBottom: 4 }}>at</div>
            <input type="time" defaultValue={c.deadline_time || '17:00'} style={{ ...sel, width: 120 }}
              onBlur={e => { if (e.target.value && e.target.value !== c.deadline_time) saveConfig({ dm_visit_assign_deadline_time: e.target.value }) }} />
          </label>
          <label style={{ fontSize: 13 }}>
            <div style={{ color: 'var(--text2)', marginBottom: 4 }}>Days ahead to fill</div>
            <input type="number" min={1} max={31} defaultValue={c.horizon_days ?? 7} style={{ ...sel, width: 90 }}
              onBlur={e => { const v = Number(e.target.value); if (v !== c.horizon_days) saveConfig({ dm_visit_assign_horizon_days: v }) }} />
          </label>
          <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={!!c.quota_enabled}
              onChange={e => saveConfig({ dm_visit_quota_enabled: e.target.checked })} /> Quota required
          </label>
          <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={!!c.auto_enabled}
              onChange={e => saveConfig({ dm_visit_assign_auto_enabled: e.target.checked })} /> Assign automatically after the deadline
          </label>
        </div>
        <p style={{ color: 'var(--text2)', fontSize: 12, margin: '10px 0 0' }}>
          {cfg?.is_default
            ? 'Using the house defaults — 2 visits a day, Monday to Friday, assign by Friday 17:00. Change anything above and it becomes this company’s own setting.'
            : 'These are this company’s own settings.'}
          {board?.deadline_reached ? ' The deadline for this week has passed.' : ' The deadline for this week has not passed yet.'}
        </p>
      </div>

      {/* ── The priority order + the carrier-specific dropdown ───────────────────────── */}
      <div className="card" style={{ padding: 16, marginBottom: 18 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 4px' }}>Priority order</h2>
        <p style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 12px' }}>
          When nobody has assigned a DM’s week, stores are offered in this order — the heavier the weight, the more it
          decides. {rules?.is_default ? 'This is the starting order; add a rule and it becomes yours.' : 'This is your order.'}
        </p>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr>
            <SortableTh field="sort" sort={rSort.sort} onSort={rSort.toggle} style={th}>Order</SortableTh>
            <SortableTh field="label" sort={rSort.sort} onSort={rSort.toggle} style={th}>Deliverable</SortableTh>
            <SortableTh field="basis" sort={rSort.sort} onSort={rSort.toggle} style={th}>Where it comes from</SortableTh>
            <SortableTh field="weight" sort={rSort.sort} onSort={rSort.toggle} style={th}>Weight</SortableTh>
            <SortableTh field="direction" sort={rSort.sort} onSort={rSort.toggle} style={th}>Priority when</SortableTh>
            <SortableTh field="_actions" sort={rSort.sort} onSort={rSort.toggle} style={th} disabled />
          </tr></thead>
          <tbody>
            {rSort.sorted.map((r: any, i: number) => (
              <tr key={`${r.basis}:${r.metric_key}:${i}`}>
                <td style={cell}>{r.sort}</td>
                <td style={{ ...cell, fontWeight: 600 }}>{r.label}</td>
                <td style={{ ...cell, color: 'var(--text2)' }}>
                  {r.basis === 'target_category' ? 'Daily targets' : r.basis === 'kpi_all' ? 'All KPI metrics' : 'KPI metric'}
                </td>
                <td style={cell}>{r.weight}</td>
                <td style={cell}>{r.direction === 'low_first' ? 'below target' : 'above target'}</td>
                <td style={cell}>
                  <button className="btn" disabled={busy} onClick={() => removeRule(r.id, r.label)}
                    style={{ fontSize: 12, padding: '3px 8px' }}>Remove</button>
                </td>
              </tr>
            ))}
            {!activeRules.length && <tr><td style={cell} colSpan={6}>No priority rules.</td></tr>}
          </tbody>
        </table>

        <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--border)' }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Add another deliverable to the priority list</div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <label style={{ fontSize: 13 }}>
              <div style={{ color: 'var(--text2)', marginBottom: 4 }}>Deliverable</div>
              <select style={{ ...sel, minWidth: 280 }} value={pick.option}
                onChange={e => setPick(p => ({ ...p, option: e.target.value }))}>
                <option value="">Pick one…</option>
                {['Daily Targets', 'KPI'].map(group => {
                  const inGroup = opts.filter((o: any) => o.group === group && !o.already_used)
                  if (!inGroup.length) return null
                  return (
                    <optgroup key={group} label={group}>
                      {inGroup.map((o: any) => (
                        <option key={`${o.basis}:${o.metric_key}`} value={`${o.basis}:${o.metric_key}`}>
                          {o.label}{o.carrier_specific ? ' (this carrier)' : ''}
                        </option>
                      ))}
                    </optgroup>
                  )
                })}
              </select>
            </label>
            <label style={{ fontSize: 13 }}>
              <div style={{ color: 'var(--text2)', marginBottom: 4 }}>Weight</div>
              <input type="number" min={0.1} step={0.1} style={{ ...sel, width: 80 }} value={pick.weight}
                onChange={e => setPick(p => ({ ...p, weight: e.target.value }))} />
            </label>
            <label style={{ fontSize: 13 }}>
              <div style={{ color: 'var(--text2)', marginBottom: 4 }}>Priority when</div>
              <select style={sel} value={pick.direction} onChange={e => setPick(p => ({ ...p, direction: e.target.value }))}>
                <option value="low_first">below target</option>
                <option value="high_first">above target</option>
              </select>
            </label>
            <label style={{ fontSize: 13 }}>
              <div style={{ color: 'var(--text2)', marginBottom: 4 }}>Order</div>
              <input type="number" style={{ ...sel, width: 80 }} value={pick.sort}
                onChange={e => setPick(p => ({ ...p, sort: e.target.value }))} />
            </label>
            <button className="btn btn-primary" disabled={busy} onClick={addRule}>Add</button>
          </div>
          <p style={{ color: 'var(--text2)', fontSize: 12, margin: '10px 0 0' }}>
            {options?.note || ''} {options?.kpi_registry_ready === false
              ? 'The KPI metric list could not be read, so only the daily-target categories are offered.' : ''}
          </p>
        </div>
      </div>

      {/* ── The monitor ──────────────────────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 16, marginBottom: 18 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 4px' }}>DM performance against the quota</h2>
        <p style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 12px' }}>
          {board ? `${board.date_from} to ${board.date_to}` : ''} · required {quota.required_per_day ?? 0} a day
          {quota.totals ? ` · ${quota.totals.completed} of ${quota.totals.required} visits done` : ''}
        </p>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr>
            <SortableTh field="visit_date" sort={qSort.sort} onSort={qSort.toggle} style={th}>Day</SortableTh>
            <SortableTh field="dm_name" sort={qSort.sort} onSort={qSort.toggle} style={th}>District manager</SortableTh>
            <SortableTh field="required" sort={qSort.sort} onSort={qSort.toggle} style={th}>Required</SortableTh>
            <SortableTh field="assigned" sort={qSort.sort} onSort={qSort.toggle} style={th}>Assigned</SortableTh>
            <SortableTh field="completed" sort={qSort.sort} onSort={qSort.toggle} style={th}>Done</SortableTh>
            <SortableTh field="shortfall" sort={qSort.sort} onSort={qSort.toggle} style={th}>Short</SortableTh>
            <SortableTh field="assigned_not_visited" sort={qSort.sort} onSort={qSort.toggle} style={th} disabled>Not visited</SortableTh>
          </tr></thead>
          <tbody>
            {qSort.sorted.map((r: any, i: number) => (
              <tr key={i} style={r.shortfall ? { background: 'color-mix(in srgb, var(--danger) 7%, transparent)' } : undefined}>
                <td style={cell}>{r.visit_date}</td>
                <td style={{ ...cell, fontWeight: 600 }}>{r.dm_name || r.dm_email || r.dm_key}</td>
                <td style={cell}>{r.required}</td>
                <td style={cell}>{r.assigned}</td>
                <td style={cell}>{r.completed}</td>
                <td style={cell}>{r.shortfall || '—'}</td>
                <td style={{ ...cell, color: 'var(--text2)' }}>
                  {(r.assigned_not_visited || []).join(', ') || '—'}
                  {(r.unassigned_visits || []).length ? ` (visited unassigned: ${r.unassigned_visits.join(', ')})` : ''}
                </td>
              </tr>
            ))}
            {!rows.length && !loading && <tr><td style={cell} colSpan={7}>
              No district manager is resolved from the org tree yet, so there is nobody to hold a quota. Set the
              district level and its managers under Store Admin → Org structure.
            </td></tr>}
          </tbody>
        </table>
      </div>

      {/* ── What the order would pick, and the findings it refuses to hide ───────────── */}
      <div className="card" style={{ padding: 16, marginBottom: 18 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 4px' }}>What the priority order would pick now</h2>
        <p style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 12px' }}>
          Each DM’s own stores, worst first, with the reason. Preview assigns nothing.
        </p>
        {Object.entries(board?.suggestions || {}).map(([dm, picks]: any) => {
          const name = rows.find((r: any) => r.dm_key === dm)?.dm_name || dm
          return (
            <div key={dm} style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>{name}</div>
              <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, color: 'var(--text2)' }}>
                {(picks as any[]).map((p: any) => (
                  <li key={p.store_code}><strong style={{ color: 'var(--text)' }}>{p.store_code}</strong> — {p.reason}</li>
                ))}
                {!(picks as any[]).length && <li>No store in this DM’s span could be measured.</li>}
              </ol>
            </div>
          )
        })}
        <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
          <button className="btn" disabled={busy} onClick={() => runPreview(false)}>Preview the fill</button>
          <button className="btn btn-primary" disabled={busy} onClick={() => runPreview(true)}>Assign now</button>
        </div>
        {preview && (
          <div style={{ marginTop: 12, fontSize: 13 }}>
            <div style={{ fontWeight: 600 }}>
              {preview.skipped ? `Nothing to do: ${preview.skipped}`
                : preview.dry_run ? `${preview.planned ?? (preview.plan || []).length} store(s) would be assigned.`
                  : `${preview.written} store(s) assigned.`}
            </div>
            <ul style={{ margin: '6px 0 0', paddingLeft: 20, color: 'var(--text2)' }}>
              {(preview.plan || []).map((a: any, i: number) => (
                <li key={i}>{a.visit_date} · {a.dm_name || a.dm_employee_id} · <strong style={{ color: 'var(--text)' }}>{a.store_code}</strong> — {a.priority_reason}</li>
              ))}
            </ul>
            {!!(preview.short || []).length && (
              <p style={{ color: 'var(--text2)', marginTop: 8 }}>
                {preview.short.length} DM-day group(s) could not be filled: {preview.short[0].reason}
              </p>
            )}
          </div>
        )}
      </div>

      {/* The findings this page refuses to hide. */}
      {!!board && (
        <div className="card" style={{ padding: 16, marginBottom: 24 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 8px' }}>What this board cannot answer for</h2>
          <ul style={{ margin: 0, paddingLeft: 20, fontSize: 13, color: 'var(--text2)' }}>
            <li><strong style={{ color: 'var(--text)' }}>{(board.unowned_stores || []).length}</strong> store(s) have no
              district manager in the org tree, so nobody can be assigned them
              {(board.unowned_stores || []).length ? `: ${board.unowned_stores.slice(0, 8).join(', ')}` : ''}.</li>
            <li><strong style={{ color: 'var(--text)' }}>{(board.unmeasured_stores || []).length}</strong> store(s) have no
              target and no measured value on any priority rule, so they are ranked last rather than first or hidden.</li>
            {board.span_divergence?.disagrees && (
              <li>Two settings disagree about which stores a DM owns — the org tree says
                {' '}{board.span_divergence.org_tree_dms} DM(s), the markets granted on logins say
                {' '}{board.span_divergence.market_grant_dms}. This board uses the org tree.</li>
            )}
            {!!(board.rules_rejected || []).length && (
              <li>{board.rules_rejected.length} saved priority rule(s) could not be read and are not being scored.</li>
            )}
          </ul>
        </div>
      )}
    </div>
  )
}
