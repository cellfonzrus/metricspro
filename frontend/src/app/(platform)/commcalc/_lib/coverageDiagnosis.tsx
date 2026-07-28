'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, fmt } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import EntityPicker from '@/components/EntityPicker'

/**
 * Plan-coverage DIAGNOSIS surfaces (mod-commission 2026-07-28).
 *
 * The owner reported 15 sellers listed as "sales but NO plan attached" with a BLANK Market and "—" Role,
 * despite having assigned all of them. Three separate identity bridges fail silently:
 *   • NAME  — an employee assignment stores the ROSTER value (epay_salesperson || name); the engine
 *             compares it to raw_sales.salesperson through an EXACT canonical match (comma-flip +
 *             casefold, deliberately not fuzzy). Any spelling difference is a silent miss — and the bulk
 *             roster still shows "current plan ✓" because it compares roster-side to roster-side.
 *   • ROLE  — role resolution reads the roster NAME column, so the same miss erases the rep's role.
 *   • STORE — the market lookup reads commcalc.store_mapping only; the /store-match alias table is never
 *             consulted, so a differently-spelled POS store string yields a blank market.
 * These components RENDER the engine's structured `diagnosis` for each row and offer the remediation as
 * an action (pick-don't-type §3b) instead of a paragraph of prose.
 *
 * Nothing here changes pay. The one money-adjacent lever (Store resolution) lives in the commission
 * settings page and is previewed here read-only.
 */

const th: React.CSSProperties = { textAlign: 'left', padding: '5px 8px', fontSize: 11, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '4px 8px', fontSize: 12, borderTop: '1px solid var(--border)' }
const lbl: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, fontWeight: 600, color: 'var(--text2)' }
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const pill = (bg: string, color: string): React.CSSProperties => ({
  fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 8, background: bg, color, whiteSpace: 'nowrap',
})

export type RosterPerson = {
  id?: string | number; name: string; value: string; role: string; market: string; email: string
  home_store: string; epay_salesperson: string; is_active: boolean
}

// ── one unassigned seller, with the engine's structured diagnosis ────────────────────────────────
export function UnassignedRow({ u, people, onLinked, onExclude, busy }: {
  u: any
  people: RosterPerson[]
  onLinked: () => void
  onExclude: (rep: string) => void
  busy: boolean
}) {
  const [open, setOpen] = useState(false)
  const [pick, setPick] = useState<string | null>(null)
  const [linking, setLinking] = useState(false)
  const [note, setNote] = useState('')
  const d = u.diagnosis || {}
  const nb = d.name_bridge || {}
  const sb = d.store_bridge || {}
  const art = d.artifact || {}

  // pick-don't-type (§3b): the target is an EXISTING roster person, never a typed name. Candidates the
  // engine already ranked float to the top of the list; the full roster stays available underneath.
  const options = useMemo(() => {
    const cands: any[] = nb.candidates || []
    const rank = new Map<string, number>()
    cands.forEach((c, i) => { if (c.employee_id != null) rank.set(String(c.employee_id), i) })
    const rows = people.map(p => ({
      id: String(p.id ?? p.value),
      label: p.name,
      sublabel: [p.role, p.email, p.epay_salesperson ? `POS: ${p.epay_salesperson}` : ''].filter(Boolean).join(' · '),
      _r: rank.has(String(p.id)) ? rank.get(String(p.id))! : 999,
    }))
    rows.sort((a, b) => a._r - b._r || a.label.localeCompare(b.label))
    return rows.map(({ _r, ...r }) => r)
  }, [people, nb.candidates])

  async function link() {
    if (!pick) return
    setLinking(true); setNote('')
    try {
      // Existing mod-people endpoint (storeops is NOT this agent's tree — we only CALL it).
      await api(`/api/v1/storeops/employees/${encodeURIComponent(pick)}`, {
        method: 'PATCH', body: JSON.stringify({ epay_salesperson: u.rep }),
      })
      setNote(`✅ Linked "${u.rep}" to that employee. Now RE-APPLY their plan on the “Assign to people” tab — an assignment saved before this change still stores the old spelling.`)
      onLinked()
    } catch (e: any) { setNote('❌ ' + (e?.message || e)) } finally { setLinking(false) }
  }

  const statusPill = nb.status === 'name_match' ? pill('#dcfce7', '#166534')
    : nb.status === 'epay_match_only' ? pill('#dbeafe', '#1e40af')
      : nb.status === 'roster_unavailable' ? pill('#f1f5f9', '#475569') : pill('#fee2e2', '#991b1b')

  return (
    <>
      <tr style={{ background: art.suspect ? 'var(--surface2, #fafafa)' : undefined }}>
        <td style={td}>
          <button onClick={() => setOpen(o => !o)} title="why is this rep uncovered?"
            style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, padding: 0, marginRight: 6 }}>
            {open ? '▾' : '▸'}
          </button>
          {u.rep}{' '}
          {art.suspect && <span style={pill('#fef3c7', '#92400e')} title={(art.reasons || []).join('; ')}>likely not a person</span>}
        </td>
        <td style={td}>{u.store || <span style={{ color: 'var(--text3)' }}>—</span>}</td>
        <td style={td}>{u.market || <span style={{ color: '#b45309' }} title={sb.message}>blank</span>}</td>
        <td style={td}>{u.role || '—'}</td>
        <td style={td}>{u.transactions}</td>
        <td style={td}>{u.lines}</td>
        <td style={td}>{fmt(u.ext_price)}</td>
        <td style={{ ...td, maxWidth: 380, fontSize: 11.5, color: 'var(--text2)' }}>{d.conclusion || u.reason}</td>
      </tr>
      {open && (
        <tr>
          <td style={{ ...td, background: 'var(--surface2, #fafafa)' }} colSpan={8}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '4px 2px 8px' }}>

              {/* 1 — NAME BRIDGE */}
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>
                  1 · Name bridge <span style={statusPill}>{nb.status || 'unknown'}</span>
                </div>
                <div style={{ fontSize: 12, color: 'var(--text2)' }}>{nb.message}</div>
                {nb.matched && (
                  <div style={{ fontSize: 12, marginTop: 3 }}>
                    roster row: <b>{nb.matched.name}</b>{nb.matched.role ? ` — ${nb.matched.role}` : ' — no role set'}
                    {nb.matched.email ? ` · ${nb.matched.email}` : ''}
                    {nb.matched.epay_salesperson ? ` · POS name “${nb.matched.epay_salesperson}”` : ''}
                  </div>
                )}
                {(nb.candidates || []).length > 0 && (
                  <div style={{ marginTop: 6 }}>
                    <div style={{ fontSize: 11.5, color: 'var(--text2)', marginBottom: 4 }}>{nb.remediation}</div>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <EntityPicker width={320} placeholder="Pick the employee this seller is…" clearable
                        options={options} value={pick} onChange={v => setPick(v as string | null)}
                        ariaLabel={`Link POS name ${u.rep} to an employee`} />
                      <button className="btn btn-secondary" disabled={!pick || linking || busy} onClick={link}>
                        {linking ? '…' : `Set their POS name to “${u.rep}”`}
                      </button>
                      <a href="/storeops/admin" style={{ fontSize: 11.5, color: 'var(--accent)' }}
                        target="_blank" rel="noreferrer">open the employee roster ↗</a>
                    </div>
                    {note && <div style={{ fontSize: 11.5, marginTop: 5 }}>{note}</div>}
                  </div>
                )}
                {(nb.candidates || []).length === 0 && nb.remediation && (
                  <div style={{ fontSize: 11.5, color: 'var(--text2)', marginTop: 4 }}>
                    {nb.remediation}{' '}
                    <a href="/storeops/admin" style={{ color: 'var(--accent)' }} target="_blank" rel="noreferrer">open the employee roster ↗</a>
                  </div>
                )}
              </div>

              {/* 2 — ASSIGNMENT NEAR-MISS */}
              {(d.assignment_near_miss || []).length > 0 && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>2 · An assignment already exists — under a different spelling</div>
                  {(d.assignment_near_miss || []).map((n: any, i: number) => (
                    <div key={i} style={{ fontSize: 12, color: 'var(--text2)', padding: '2px 0' }}>
                      <span style={pill('#fef3c7', '#92400e')}>{Math.round((n.score || 0) * 100)}%</span>{' '}{n.message}
                    </div>
                  ))}
                </div>
              )}

              {/* 3 — STORE / MARKET BRIDGE */}
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>3 · Store → market</div>
                <div style={{ fontSize: 12, color: 'var(--text2)' }}>
                  POS store string <b>“{sb.raw || u.store || '—'}”</b> →{' '}
                  store_mapping address: <b>{sb.address_hit ? 'hit' : 'miss'}</b> ·{' '}
                  store code: <b>{sb.code_hit ? 'hit' : 'miss'}</b> ·{' '}
                  first token “{sb.first_token}”: <b>{sb.first_token_hit ? 'hit' : 'miss'}</b> ·{' '}
                  /store-match alias: <b>{sb.alias ? `${sb.alias.store_code}${sb.alias_market ? ` → ${sb.alias_market}` : ' (no market)'}` : 'none'}</b>
                </div>
                <div style={{ fontSize: 12, marginTop: 3 }}>{sb.message}</div>
                {(sb.status === 'unmapped') && (
                  <a href="/commcalc/store-match" style={{ fontSize: 11.5, color: 'var(--accent)' }}>map this store at /store-match ↗</a>
                )}
                {(sb.status === 'mapped_no_market') && (
                  <a href="/commcalc/settings" style={{ fontSize: 11.5, color: 'var(--accent)' }}>set the market in Commission settings ↗</a>
                )}
              </div>

              {/* 4 — ALIAS PREVIEW (what flipping Store resolution would fix) */}
              {d.alias_preview && (
                <div style={{ background: d.alias_preview.would_attach ? '#ecfdf5' : 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '6px 8px' }}>
                  <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 2 }}>
                    4 · With “Store resolution = alias” {d.alias_preview.would_attach ? <span style={pill('#dcfce7', '#166534')}>a plan would attach</span> : <span style={pill('#f1f5f9', '#475569')}>still nothing</span>}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text2)' }}>{d.alias_preview.message}</div>
                  {d.alias_preview.would_attach && (
                    <div style={{ fontSize: 11.5, marginTop: 3 }}>
                      This is a <b>read-only preview</b> — no pay changes until an admin switches the setting
                      in <a href="/commcalc/plan-installments" style={{ color: 'var(--accent)' }}>Tenant pay settings</a> AND runs Calculate.
                    </div>
                  )}
                </div>
              )}

              {/* 5 — POS ARTIFACT */}
              {art.suspect && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 3 }}>5 · Does this look like a person?</div>
                  <div style={{ fontSize: 12, color: 'var(--text2)' }}>{(art.reasons || []).join('; ')}.</div>
                  <button className="btn btn-secondary" style={{ fontSize: 12, marginTop: 5 }} disabled={busy}
                    onClick={() => onExclude(u.rep)}>Mark “{u.rep}” as not a commissionable seller</button>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// ── "lines not paying" explorer (Part C) ─────────────────────────────────────────────────────────
type ExpFilters = {
  rep: string[]; store: string[]; market: string[]
  department: string[]; category: string[]; contract_type: string[]; why: string[]
  product: string
}
const emptyFilters = (): ExpFilters => ({ rep: [], store: [], market: [], department: [], category: [], contract_type: [], why: [], product: '' })

const WHY_LABEL: Record<string, string> = {
  rep_unassigned: 'seller has no plan attached',
  no_rule_matched: 'plan attached, but no rule matched',
}

export function UnmatchedExplorer({ period }: { period: string }) {
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [f, setF] = useState<ExpFilters>(emptyFilters())
  const [groupBy, setGroupBy] = useState('category')
  const [limit, setLimit] = useState(500)
  const [showLines, setShowLines] = useState(false)

  const load = useCallback(async () => {
    setBusy(true); setErr('')
    try {
      const q = new URLSearchParams()
      q.set('period', period); q.set('group_by', groupBy); q.set('limit', String(limit))
      // repeatable params, NOT comma-joined — a POS seller name IS "Last, First".
      ;(['rep', 'store', 'market', 'department', 'category', 'contract_type', 'why'] as const)
        .forEach(k => (f[k] || []).forEach(v => q.append(k, v)))
      if (f.product.trim()) q.set('product', f.product.trim())
      setData(await api(`/api/v1/commcalc/commission-plans/coverage-unmatched?${q.toString()}`))
    } catch (e: any) { setErr(e?.message || String(e)); setData(null) } finally { setBusy(false) }
  }, [period, groupBy, limit, f])

  useEffect(() => { setData(null); setF(emptyFilters()) }, [period])

  const facet = (name: string) => ((data?.facets || {})[name] || [])
    .map((o: any) => ({ id: o.value, label: o.value, sublabel: `${o.lines} line${o.lines === 1 ? '' : 's'}` }))

  function payload(): ExportPayload {
    return {
      title: 'Lines not considered for commission',
      subtitle: `${period} — read-only diagnostic · ${data?.totals?.lines ?? 0} line(s), ${fmt(data?.totals?.ext_price || 0)}`,
      filename: `lines-not-paying-${String(period).replace(/\s+/g, '-')}`,
      sheets: [
        {
          name: 'By group', rows: data?.groups || [], columns: [
            { header: 'Group', get: (r: any) => r.label },
            { header: 'Lines', get: (r: any) => r.lines },
            { header: 'No plan attached', get: (r: any) => r.rep_unassigned_lines },
            { header: 'No rule matched', get: (r: any) => r.no_rule_matched_lines },
            { header: 'Reps', get: (r: any) => r.reps },
            { header: 'Ext price', get: (r: any) => r.ext_price, money: true },
            { header: 'GP', get: (r: any) => r.gp, money: true },
            { header: 'Rules that match', get: (r: any) => (r.matching_rules || []).map((h: any) => `${h.plan_name}/${h.label}`).join('; ') },
            { header: 'What to do', get: (r: any) => r.suggestion },
          ],
        },
        {
          name: `Lines (max ${data?.line_cap ?? 0})`, rows: data?.lines || [], columns: [
            { header: 'Why', get: (r: any) => WHY_LABEL[r.why] || r.why },
            { header: 'Date', get: (r: any) => r.date }, { header: 'Rep', get: (r: any) => r.rep },
            { header: 'Store', get: (r: any) => r.store }, { header: 'Market', get: (r: any) => r.market },
            { header: 'Plan', get: (r: any) => r.plan_name }, { header: 'Trans', get: (r: any) => r.trans_id },
            { header: 'Department', get: (r: any) => r.department }, { header: 'Category', get: (r: any) => r.category },
            { header: 'Contract type', get: (r: any) => r.contract_type },
            { header: 'Product', get: (r: any) => r.product_desc },
            { header: 'Ext price', get: (r: any) => r.ext_price, money: true },
            { header: 'GP', get: (r: any) => r.gp, money: true },
          ],
        },
      ],
    }
  }

  const anyFilter = (['rep', 'store', 'market', 'department', 'category', 'contract_type', 'why'] as const)
    .some(k => (f[k] || []).length > 0) || !!f.product.trim()

  return (
    <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 13 }}>🧾 Lines not paying
          <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text3)' }}> (every sale line NOT considered for commission — from the pay engine itself)</span>
        </div>
        <span style={{ flex: 1 }} />
        <button className="btn btn-secondary" disabled={busy || !period} onClick={load}>{busy ? '…' : `Load ${period}`}</button>
        {data && <><ExportButtons payload={payload} /><SendReportButton exportPayload={payload} compact /></>}
      </div>
      {err && <div style={{ fontSize: 12.5, color: '#b91c1c', marginBottom: 8 }}>❌ {err}</div>}
      {!data && !err && <div style={{ fontSize: 12.5, color: 'var(--text3)' }}>Read-only. Nothing is written and no calculation is triggered.</div>}
      {data && (<>
        {/* RULE FIVE standardized filter bar — core set (period is the panel's period) + product facets */}
        <div className="card" style={{ padding: 10, marginBottom: 10, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label style={lbl}>Rep
            <EntityPicker multi width={190} placeholder="All reps…" clearable options={facet('rep')}
              value={f.rep} onChange={v => setF({ ...f, rep: v as string[] })} ariaLabel="Filter by rep" /></label>
          <label style={lbl}>Store
            <EntityPicker multi width={190} placeholder="All stores…" clearable options={facet('store')}
              value={f.store} onChange={v => setF({ ...f, store: v as string[] })} ariaLabel="Filter by store" /></label>
          <label style={lbl}>Market
            <EntityPicker multi width={150} placeholder="All markets…" clearable options={facet('market')}
              value={f.market} onChange={v => setF({ ...f, market: v as string[] })} ariaLabel="Filter by market" /></label>
          <label style={lbl}>Department
            <EntityPicker multi width={170} placeholder="All departments…" clearable options={facet('department')}
              value={f.department} onChange={v => setF({ ...f, department: v as string[] })} ariaLabel="Filter by department" /></label>
          <label style={lbl}>Category
            <EntityPicker multi width={170} placeholder="All categories…" clearable options={facet('category')}
              value={f.category} onChange={v => setF({ ...f, category: v as string[] })} ariaLabel="Filter by category" /></label>
          <label style={lbl}>Contract type
            <EntityPicker multi width={170} placeholder="All contract types…" clearable options={facet('contract_type')}
              value={f.contract_type} onChange={v => setF({ ...f, contract_type: v as string[] })} ariaLabel="Filter by contract type" /></label>
          <label style={lbl}>Why
            <EntityPicker multi width={210} placeholder="Both reasons…" clearable
              options={facet('why').map((o: any) => ({ ...o, label: WHY_LABEL[o.id] || o.id }))}
              value={f.why} onChange={v => setF({ ...f, why: v as string[] })} ariaLabel="Filter by reason" /></label>
          <label style={lbl}>Product contains
            <input style={{ ...sel, width: 170 }} placeholder="e.g. screen protector" value={f.product}
              onChange={e => setF({ ...f, product: e.target.value })} aria-label="Filter by product text" /></label>
          <label style={lbl}>Group by
            <select style={sel} value={groupBy} onChange={e => setGroupBy(e.target.value)} aria-label="Group by">
              <option value="category">Department + Category</option>
              <option value="product">Department + Category + Product</option>
              <option value="department">Department</option>
              <option value="contract_type">Contract type</option>
              <option value="rep">Rep</option>
              <option value="store">Store</option>
            </select></label>
          <label style={lbl}>Line rows
            <select style={sel} value={limit} onChange={e => setLimit(Number(e.target.value))} aria-label="Line row cap">
              {[100, 500, 2000, 5000].map(n => <option key={n} value={n}>{n}</option>)}
            </select></label>
          <button className="btn btn-primary" disabled={busy} onClick={load}>{busy ? '…' : 'Apply'}</button>
          {anyFilter && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setF(emptyFilters())}>Clear filters</button>}
        </div>

        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 12.5, marginBottom: 8 }}>
          <span>lines shown: <b>{data.totals?.lines ?? 0}</b> of {data.totals?.lines_unfiltered ?? 0}</span>
          <span>value: <b>{fmt(data.totals?.ext_price || 0)}</b> (GP {fmt(data.totals?.gp || 0)})</span>
          {Object.entries(data.totals?.by_why || {}).map(([k, v]: any) => (
            <span key={k}>{WHY_LABEL[k] || k}: <b>{v.lines}</b> ({fmt(v.ext_price)})</span>
          ))}
          {!!data.totals?.excluded_seller_lines && (
            <span title="sellers this tenant marked as not commissionable">excluded sellers: <b>{data.totals.excluded_seller_lines}</b> lines</span>
          )}
        </div>

        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>{['Group', 'Lines', 'No plan', 'No rule', 'Reps', 'Ext price', 'GP', 'What to do'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
            <tbody>{(data.groups || []).map((g: any, i: number) => (
              <tr key={i}>
                <td style={td}>{g.label}</td>
                <td style={td}>{g.lines}</td>
                <td style={td}>{g.rep_unassigned_lines || ''}</td>
                <td style={{ ...td, color: (g.no_rule_matched_lines || 0) ? '#b45309' : undefined }}>{g.no_rule_matched_lines || ''}</td>
                <td style={td}>{g.reps}</td>
                <td style={td}>{fmt(g.ext_price)}</td>
                <td style={td}>{fmt(g.gp)}</td>
                <td style={{ ...td, maxWidth: 460, fontSize: 11.5, color: 'var(--text2)' }}>
                  {g.suggestion}
                  {(g.matching_rules || []).length > 0 && (
                    <div style={{ marginTop: 3 }}>
                      {(g.matching_rules || []).map((h: any, k: number) => (
                        <span key={k} style={{ ...pill('#e0e7ff', '#3730a3'), marginRight: 4 }}>
                          {h.plan_name} · {h.match_field} {h.match_op} “{h.match_value}”
                        </span>
                      ))}
                    </div>
                  )}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        {(data.groups || []).length === 0 && <div style={{ fontSize: 12.5, color: 'var(--text3)', padding: '6px 0' }}>Nothing matches these filters — every line in this period is considered for commission.</div>}

        <div style={{ marginTop: 10 }}>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setShowLines(s => !s)}>
            {showLines ? 'Hide' : 'Show'} line detail ({Math.min(data.line_total || 0, data.line_cap || 0)} of {data.line_total || 0})
          </button>
          {data.truncated && (
            <span style={{ fontSize: 11.5, color: '#b45309', marginLeft: 8 }}>
              showing the first {data.line_cap} of {data.line_total} lines — narrow the filters or raise “Line rows”. (Group totals above cover ALL {data.line_total}.)
            </span>
          )}
          {showLines && (
            <div style={{ overflowX: 'auto', marginTop: 8 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr>{['Why', 'Date', 'Rep', 'Store', 'Plan', 'Trans', 'Department', 'Category', 'Contract type', 'Product', 'Ext price', 'GP'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                <tbody>{(data.lines || []).map((l: any, i: number) => (
                  <tr key={i}>
                    <td style={td}><span style={l.why === 'rep_unassigned' ? pill('#fee2e2', '#991b1b') : pill('#fef3c7', '#92400e')}>{WHY_LABEL[l.why] || l.why}</span></td>
                    <td style={td}>{l.date}</td><td style={td}>{l.rep}</td><td style={td}>{l.store}</td>
                    <td style={td}>{l.plan_name || '—'}</td><td style={td}>{l.trans_id}</td>
                    <td style={td}>{l.department}</td><td style={td}>{l.category}</td>
                    <td style={td}>{l.contract_type || '—'}</td><td style={td}>{l.product_desc}</td>
                    <td style={td}>{fmt(l.ext_price)}</td><td style={td}>{fmt(l.gp)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </div>
      </>)}
    </div>
  )
}

// ── orphan assignments · store bridge · excluded sellers ────────────────────────────────────────
export function OrphanAssignments({ rows }: { rows: any[] }) {
  const [open, setOpen] = useState(false)
  if (!rows?.length) return null
  return (
    <div style={{ marginBottom: 12 }}>
      <button onClick={() => setOpen(o => !o)} style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0, fontWeight: 700, fontSize: 13 }}>
        {open ? '▾' : '▸'} {rows.length} plan assignment{rows.length === 1 ? '' : 's'} attached to a name nobody sold under
      </button>
      <div style={{ fontSize: 11.5, color: 'var(--text2)', margin: '2px 0 4px' }}>
        The “Assign to people” tab shows these as <b>current plan ✓</b> because it compares roster values to
        roster values — it never checks the sales side. The engine pays from the POS name, so these
        assignments attach to nobody.
      </div>
      {open && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>{['Assigned name', 'Plan', 'Why it never attaches'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
            <tbody>{rows.map((o: any, i: number) => (
              <tr key={i}><td style={td}>{o.scope_value}</td><td style={td}>{o.plan_name}</td>
                <td style={{ ...td, fontSize: 11.5, color: 'var(--text2)' }}>{o.message}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function StoreBridgePanel({ stores }: { stores: any }) {
  const [open, setOpen] = useState(false)
  const rows: any[] = stores?.rows || []
  if (!rows.length) return null
  const bad = rows.filter(r => !r.market)
  return (
    <div style={{ marginBottom: 12 }}>
      <button onClick={() => setOpen(o => !o)} style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0, fontWeight: 700, fontSize: 13 }}>
        {open ? '▾' : '▸'} Store → market: {stores.distinct} POS store string{stores.distinct === 1 ? '' : 's'},{' '}
        <span style={{ color: bad.length ? '#b91c1c' : undefined }}>{stores.unresolved} without a market</span>
        {stores.would_resolve_with_alias ? ` · ${stores.would_resolve_with_alias} would resolve with alias resolution` : ''}
      </button>
      <div style={{ fontSize: 11.5, color: 'var(--text2)', margin: '2px 0 4px' }}>
        Resolution mode: <b>{stores.mode}</b>. A rep with a blank market can never match a market-scope
        assignment.{stores.would_resolve_with_alias ? ' Switching Store resolution to “alias” is money-adjacent — it takes effect on the next Calculate.' : ''}
      </div>
      {open && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>{['POS store string', 'Lines', 'Market today', 'With alias', 'What to do'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
            <tbody>{rows.map((r: any, i: number) => (
              <tr key={i}>
                <td style={td}>{r.store}</td><td style={td}>{r.lines}</td>
                <td style={{ ...td, color: r.market ? undefined : '#b91c1c' }}>{r.market || 'blank'}</td>
                <td style={td}>{r.would_resolve_with_alias ? `${r.alias?.store_code} → ${r.alias_market}` : (r.alias ? r.alias.store_code : '—')}</td>
                <td style={{ ...td, fontSize: 11.5, color: 'var(--text2)' }}>{r.message}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function ExcludedSellers({ cov, onChange, busy }: { cov: any; onChange: (sellers: string[]) => void; busy: boolean }) {
  const [open, setOpen] = useState(false)
  const rows: any[] = cov?.excluded_reps || []
  const configured: string[] = cov?.excluded_config?.sellers || []
  if (!rows.length && !configured.length) return null
  return (
    <div style={{ marginBottom: 12 }}>
      <button onClick={() => setOpen(o => !o)} style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0, fontWeight: 700, fontSize: 13 }}>
        {open ? '▾' : '▸'} {rows.length} seller{rows.length === 1 ? '' : 's'} excluded as “not commissionable”
        {rows.length ? ` (${fmt(cov.excluded_ext_price || 0)} of sales)` : ''}
      </button>
      <div style={{ fontSize: 11.5, color: 'var(--text2)', margin: '2px 0 4px' }}>
        Excluded sellers are removed from the uncovered list, never from the data — and they cannot change
        anyone&apos;s pay (they have no plan attached either way).
      </div>
      {open && (
        <div>
          {rows.map((r: any, i: number) => (
            <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 12, padding: '3px 0' }}>
              <b>{r.rep}</b><span style={{ color: 'var(--text2)' }}>{r.store}</span>
              <span style={{ color: 'var(--text2)' }}>{r.lines} lines · {fmt(r.ext_price)}</span>
              <button className="btn btn-secondary" style={{ fontSize: 11 }} disabled={busy}
                onClick={() => onChange(configured.filter(s => s.trim().toLowerCase() !== String(r.rep).trim().toLowerCase()))}>
                put back on the list
              </button>
            </div>
          ))}
          {configured.filter(c => !rows.some((r: any) => String(r.rep).trim().toLowerCase() === c.trim().toLowerCase())).map((c, i) => (
            <div key={`c${i}`} style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 12, padding: '3px 0', color: 'var(--text2)' }}>
              <b>{c}</b><span>no sales this period</span>
              <button className="btn btn-secondary" style={{ fontSize: 11 }} disabled={busy}
                onClick={() => onChange(configured.filter(s => s.trim().toLowerCase() !== c.trim().toLowerCase()))}>remove</button>
            </div>
          ))}
          {cov?.excluded_config?.ready === false && (
            <div style={{ fontSize: 11.5, color: '#b45309', marginTop: 4 }}>
              ⚠️ Migration 248 has not been run — this list cannot be saved yet.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
