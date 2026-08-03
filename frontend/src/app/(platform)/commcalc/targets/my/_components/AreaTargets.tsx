'use client'
// DM COLLECTIVE TARGETS + PER-STORE DRILL-DOWN (owner 2026-08-03: "under my targets the dm should
// have a collective target for all the stores assigned to them with a drill down for each area
// assigning them per store").
//
// SCOPE COMES FROM THE SERVER, NOT FROM HERE. `/targets/{period}/summary` already returns exactly the
// caller's stores (RULE FIVE filters + the RBAC `scope_keyset` span), plus an additive `collective`
// roll-up that is a straight SUM of those same rows, and a `scope` block saying whether the caller
// may edit targets at all. This component renders those; it computes no scope and no permission.
//
// EDITING: the per-store inputs are shown ONLY when `scope.can_edit_targets` is true, and they save
// through the SAME `PUT /targets/{period}` Target Settings uses — which is itself gated on the
// existing 'targets' settings area AND the caller's store span. Nobody gains edit rights here who did
// not already have them; a DM simply gets a place to use the ones they have.
//
// RULE FIVE: the standard store / market / rep filters drive the collective total, the drill-down and
// the exports together (they are applied SERVER-side, so all three are one set of numbers).
// RULE FOUR: ReportShell-equivalent exports via ExportButtons + SendReportButton over what's on screen.
import { useMemo, useState } from 'react'
import { api, ORG_ID, fmt, fmtN } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

const CATS = [
  { key: 'activations', label: 'Activations', unit: 'count' },
  { key: 'upgrades', label: 'Upgrades', unit: 'count' },
  { key: 'byod', label: 'BYOD', unit: 'count' },
  { key: 'accessories', label: 'Accessories', unit: 'dollars' },
] as const

// Target Settings columns, in the same order and with the same meaning as that page — the drill-down
// is a second doorway to the same rows, not a second definition of them.
const EDIT_FIELDS = [
  { key: 'activations_monthly', label: 'Activations' },
  { key: 'upgrades_monthly', label: 'Upgrades' },
  { key: 'accessories_monthly', label: 'Accessories $' },
  { key: 'byod_pct', label: 'BYOD %' },
] as const

const val = (unit: string, n: any) => (unit === 'dollars' ? fmt(Number(n) || 0) : fmtN(Number(n) || 0, 1))
const th: React.CSSProperties = { textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)', fontWeight: 700 }
const td: React.CSSProperties = { padding: '6px 8px', fontSize: 13 }
const numInp: React.CSSProperties = {
  width: 92, padding: '4px 7px', borderRadius: 7, border: '1px solid var(--border)',
  fontSize: 13, background: 'var(--surface)', textAlign: 'right',
}

type Props = {
  period: string
  stores: any[]
  collective: any
  scope: any
  filterBar?: React.ReactNode
  onSaved?: () => void
}

export default function AreaTargets({ period, stores, collective, scope, filterBar, onSaved }: Props) {
  const [draft, setDraft] = useState<Record<string, Record<string, string>>>({})
  const [saving, setSaving] = useState('')
  const [msg, setMsg] = useState('')
  const canEdit = !!scope?.can_edit_targets
  const editable = useMemo(() => new Set<string>(scope?.editable_store_codes || []), [scope])

  const cats = collective?.categories || {}

  function set(code: string, field: string, v: string) {
    setDraft(p => ({ ...p, [code]: { ...(p[code] || {}), [field]: v } }))
  }

  async function save(row: any) {
    const code = row.store_code
    const d = draft[code] || {}
    const body: any = { store_code: code }
    for (const f of EDIT_FIELDS) {
      const raw = d[f.key]
      body[f.key] = raw === undefined || raw === '' ? (row[f.key] ?? (f.key === 'byod_pct' ? '' : 0)) : Number(raw)
    }
    setSaving(code); setMsg('')
    try {
      await api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}?org_id=${ORG_ID}`,
        { method: 'PUT', body: JSON.stringify(body) })
      setMsg(`Saved ${row.address || code}.`)
      setDraft(p => { const n = { ...p }; delete n[code]; return n })
      onSaved?.()
    } catch (e: any) {
      // The backend names the exact permission / store problem in `detail`; show it verbatim rather
      // than a generic failure, so "why can't I save this store" is answerable without a log dive.
      setMsg(String(e?.message || e))
    } finally { setSaving('') }
  }

  function buildPayload(): ExportPayload {
    const collRows = CATS.map(c => {
      const m = cats[c.key] || {}
      return {
        category: c.label,
        monthly: val(c.unit, m.monthly), achieved: val(c.unit, m.achieved_mtd),
        need: val(c.unit, m.need), today: val(c.unit, m.today_target),
        pace: val(c.unit, m.pace),
        attainment: m.attainment_pct == null ? '—' : `${m.attainment_pct}%`,
      }
    })
    const storeRows = stores.map(s => ({
      store: s.address || s.store_code, market: s.market || '',
      ...Object.fromEntries(CATS.flatMap(c => {
        const m = (s.categories || {})[c.key] || {}
        return [[`${c.key}_monthly`, val(c.unit, m.monthly)], [`${c.key}_achieved`, val(c.unit, m.achieved_mtd)]]
      })),
    }))
    return {
      title: `Area Targets — ${scope?.stores_in_scope || stores.length} stores`,
      subtitle: `${period} · collective total = sum of the stores below`,
      filename: `Area-Targets-${period.replace(/\s+/g, '-')}`,
      sheets: [
        {
          name: 'Collective',
          columns: [
            { header: 'Category', get: (r: any) => r.category },
            { header: 'Monthly', get: (r: any) => r.monthly, align: 'right' as const },
            { header: 'Achieved', get: (r: any) => r.achieved, align: 'right' as const },
            { header: 'Need', get: (r: any) => r.need, align: 'right' as const },
            { header: 'Today', get: (r: any) => r.today, align: 'right' as const },
            { header: 'Pace/day', get: (r: any) => r.pace, align: 'right' as const },
            { header: 'Attainment', get: (r: any) => r.attainment, align: 'right' as const },
          ],
          rows: collRows,
        },
        {
          name: 'By store',
          columns: [
            { header: 'Store', get: (r: any) => r.store },
            { header: 'Market', get: (r: any) => r.market },
            ...CATS.flatMap(c => ([
              { header: `${c.label} target`, get: (r: any) => r[`${c.key}_monthly`], align: 'right' as const },
              { header: `${c.label} MTD`, get: (r: any) => r[`${c.key}_achieved`], align: 'right' as const },
            ])),
          ],
          rows: storeRows,
        },
      ],
    }
  }

  return (
    <div style={{ marginBottom: 22 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end',
                    gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
        <div>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>🗺️ My Area — collective target</h2>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
            All {collective?.stores ?? stores.length} stores assigned to you, added together.
            {' '}Drill down below for each store.
          </div>
        </div>
        <><ExportButtons payload={buildPayload} compact /><SendReportButton exportPayload={buildPayload} compact /></>
      </div>

      {filterBar}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 14 }}>
        {CATS.map(c => {
          const m = cats[c.key] || {}
          const pct = m.attainment_pct
          const good = pct != null && pct >= 100
          return (
            <div key={c.key} className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
                <span style={{ fontWeight: 700, fontSize: 14 }}>{c.label}</span>
                <span style={{ fontSize: 11, color: 'var(--text3)' }}>
                  {m.stores_on_track ?? 0}/{m.stores_with_target ?? 0} stores on target
                </span>
              </div>
              <div style={{ fontSize: 26, fontWeight: 800, lineHeight: 1.1, color: good ? 'var(--green)' : 'var(--accent)' }}>
                {val(c.unit, m.achieved_mtd)}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text2)' }}>
                of {val(c.unit, m.monthly)}{pct != null ? ` · ${pct}%` : ''}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', rowGap: 5, fontSize: 12,
                            borderTop: '1px solid var(--border)', marginTop: 9, paddingTop: 8 }}>
                <span style={{ color: 'var(--text2)' }}>Today (area)</span>
                <span style={{ fontWeight: 600 }}>{val(c.unit, m.today_target)}</span>
                <span style={{ color: 'var(--text2)' }}>Still needed</span>
                <span style={{ fontWeight: 600, color: Number(m.need) > 0 ? '#b45309' : 'var(--green)' }}>{val(c.unit, m.need)}</span>
                <span style={{ color: 'var(--text2)' }}>Pace /day</span>
                <span style={{ fontWeight: 600 }}>{val(c.unit, m.pace)}</span>
              </div>
            </div>
          )
        })}
      </div>

      {collective?.conversion?.billpays > 0 && (
        <div className="card" style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>
            Area conversion <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>
              · boxes ÷ bill-payments across your stores · target {collective.conversion.target}%</span>
          </div>
          <div style={{ fontSize: 26, fontWeight: 800, lineHeight: 1.1,
                        color: collective.conversion.rate >= collective.conversion.target ? 'var(--green)' : '#dc2626' }}>
            {collective.conversion.rate}%
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)' }}>
            {collective.conversion.boxes} boxes / {collective.conversion.billpays} bill-pays
          </div>
        </div>
      )}

      <div className="card" style={{ marginTop: 16, padding: 0 }}>
        <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--border)',
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>Drill down — each store</div>
          <div style={{ fontSize: 11, color: 'var(--text3)' }}>
            {canEdit ? 'Set each store’s monthly target below and Save.'
                     : 'Read-only — you don’t have the ‘Target Settings’ permission.'}
          </div>
        </div>
        {msg && <div style={{ padding: '8px 14px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)' }}>{msg}</div>}
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                <th style={th}>Store</th>
                <th style={th}>Market</th>
                {CATS.map(c => <th key={c.key} style={{ ...th, textAlign: 'right' }}>{c.label}<div style={{ fontWeight: 400, color: 'var(--text3)' }}>MTD / target</div></th>)}
                {canEdit && EDIT_FIELDS.map(f => <th key={f.key} style={{ ...th, textAlign: 'right' }}>Set {f.label}</th>)}
                {canEdit && <th style={th} />}
              </tr>
            </thead>
            <tbody>
              {stores.map(s => {
                const code = s.store_code
                const mine = canEdit && (editable.size === 0 || editable.has(code))
                const d = draft[code] || {}
                return (
                  <tr key={code} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={td}>{s.address || code}</td>
                    <td style={{ ...td, color: 'var(--text3)' }}>{s.market || '—'}</td>
                    {CATS.map(c => {
                      const m = (s.categories || {})[c.key] || {}
                      const hit = Number(m.achieved_mtd || 0) >= Number(m.monthly || 0) && Number(m.monthly || 0) > 0
                      return (
                        <td key={c.key} style={{ ...td, textAlign: 'right' }}>
                          <span style={{ fontWeight: 600, color: hit ? 'var(--green)' : undefined }}>{val(c.unit, m.achieved_mtd)}</span>
                          <span style={{ color: 'var(--text3)' }}> / {val(c.unit, m.monthly)}</span>
                        </td>
                      )
                    })}
                    {canEdit && EDIT_FIELDS.map(f => (
                      <td key={f.key} style={{ ...td, textAlign: 'right' }}>
                        <input type="number" min={0} style={numInp} disabled={!mine}
                          aria-label={`${s.address || code} — ${f.label}`}
                          value={d[f.key] ?? (s[f.key] ?? '')}
                          onChange={e => set(code, f.key, e.target.value)} />
                      </td>
                    ))}
                    {canEdit && (
                      <td style={{ ...td, textAlign: 'right' }}>
                        <button className="btn btn-secondary" style={{ fontSize: 12 }}
                          disabled={!mine || saving === code || !draft[code]}
                          onClick={() => save(s)}>
                          {saving === code ? 'Saving…' : 'Save'}
                        </button>
                      </td>
                    )}
                  </tr>
                )
              })}
              {stores.length === 0 && (
                <tr><td style={{ ...td, color: 'var(--text3)' }} colSpan={9}>No stores in your area for this month.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
