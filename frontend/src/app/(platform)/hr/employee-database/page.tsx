'use client'
import { useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { useAuth } from '@/lib/auth-context'
import ReportShell from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, type StandardFilterValue } from '@/lib/standard-filters'
import type { EntityOption } from '@/components/EntityPicker'

// HR · Employee Database (owner directive 2026-07-29) — one exportable row per employee across the
// StoreOps roster + HR onboarding intake + the Documents board. See the backend docstring
// (backend/app/modules/hr/router.py, "EMPLOYEE DATABASE report") for the full field-by-field
// investigation of what's actually collected vs. designed-but-absent (SSN).
//
// PII SAFETY (server-side, never client-only): SSN + direct-deposit routing/account numbers come
// back from `/hr/employee-database` ALREADY masked (last 4 real, rest 'x') unless this page asks
// for `reveal=true` — and the backend re-checks admin/super-admin status on EVERY such call,
// regardless of what this page's local `isAdmin` guess says. Hiding the toggle for a non-admin here
// is UX politeness only, not the real gate. Exports (ReportShell → Excel/PDF/Print/Send) render
// EXACTLY the `rows` this page already holds — masked by default, full only after a successful,
// audited reveal fetch — so "what you see is what exports" extends to the masking level too.
//
// Selection (RULE THREE): the StandardFilterBar's people-picker (`reps`) IS the employee multi-
// select this report needs — one control serves both the RULE FIVE core-filter slot and the
// "select which employees" requirement, rather than two redundant pickers. Store/market (RULE
// FIVE) filter the SAME loaded roster client-side, same pattern as the payroll RULE-FIVE wave-1
// package. Period is omitted (`show.period=false`) — a roster has no time dimension.
//
// Column picker: a plain checkbox list grouped by the backend's field catalog `section` (identity /
// contact / address / personal / sensitive / direct_deposit / onboarding) — not EntityPicker, since
// this selects REPORT COLUMNS (a UI/display concern), not a reference to an existing data entity.

type FieldDef = {
  key: string; label: string; section: string
  sensitive?: boolean; masked?: boolean; designed_absent?: boolean; note?: string
}
type EmpRow = { employee_id: string; [k: string]: any }

const SECTION_LABELS: Record<string, string> = {
  identity: 'Identity', contact: 'Contact', address: 'Address', personal: 'Personal',
  sensitive: 'Sensitive (SSN)', direct_deposit: 'Direct Deposit', onboarding: 'Onboarding / Documents',
}
const SECTION_ORDER = ['identity', 'contact', 'address', 'personal', 'sensitive', 'direct_deposit', 'onboarding']

const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, padding: 14, marginBottom: 14, background: 'var(--surface)' }
const btn: React.CSSProperties = { padding: '6px 11px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, cursor: 'pointer', background: 'var(--surface)' }

function fmtCell(f: FieldDef, v: any): string {
  if (v == null) return ''
  if (f.key === 'is_active') return v === false ? 'No' : 'Yes'
  if (f.key === 'date_of_birth' || f.key === 'hire_date' || f.key === 'docs_sent_at') return String(v).slice(0, 10)
  return String(v)
}

export default function EmployeeDatabasePage() {
  const { user } = useAuth()
  const isAdminUI = !!(user?.super_admin || (user?.role || '').toLowerCase() === 'admin')

  const [fieldsCatalog, setFieldsCatalog] = useState<FieldDef[]>([])
  const [selectedFields, setSelectedFields] = useState<string[]>([])
  const [maskedById, setMaskedById] = useState<Record<string, EmpRow>>({})
  const [revealedById, setRevealedById] = useState<Record<string, EmpRow>>({})
  const [revealOn, setRevealOn] = useState(false)
  const [revealBusy, setRevealBusy] = useState(false)
  const [revealError, setRevealError] = useState('')
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [includeInactive, setIncludeInactive] = useState(true)

  const [storeOpts, setStoreOpts] = useState<EntityOption[]>([])
  const [marketOpts, setMarketOpts] = useState<EntityOption[]>([])
  const [storeToMarket, setStoreToMarket] = useState<Record<string, string>>({})
  const [repOpts, setRepOpts] = useState<EntityOption[]>([])
  const [filter, setFilter] = useState<StandardFilterValue>(emptyStandardFilter())

  async function loadCatalogAndRoster() {
    setLoading(true); setMsg('')
    try {
      const [fr, rr, stores] = await Promise.all([
        api('/api/v1/hr/employee-database/fields'),
        api(`/api/v1/hr/employee-database?include_inactive=${includeInactive ? 'true' : 'false'}`),
        // This is an audit/historical roster (defaults to including inactive EMPLOYEES) — the store
        // filter should be able to reference a since-closed store too, so it always requests the full
        // set (GET /stores now defaults to active-only, 2026-08-06 disabled-T-store fix).
        apiCached('/api/v1/storeops/stores?include_inactive=true', LOOKUP).catch(() => []),
      ])
      const fields: FieldDef[] = fr?.fields || []
      setFieldsCatalog(fields)
      setSelectedFields(s => s.length ? s : fields.map(f => f.key))  // default: everything, once
      const rows: EmpRow[] = rr?.employees || []
      setMaskedById(Object.fromEntries(rows.map(r => [r.employee_id, r])))
      // Revealed cache is stale the moment the underlying roster reloads.
      setRevealedById({})
      setRevealOn(false)

      const stRows = (stores || []) as any[]
      const s2m: Record<string, string> = {}
      const stOpts: EntityOption[] = []
      const mSet = new Map<string, string>()
      for (const s of stRows) {
        if (!s.store_code) continue
        s2m[s.store_code] = s.market || ''
        const inactiveTag = s.is_active === false ? ' (inactive)' : ''
        stOpts.push({ id: s.store_code, label: (s.address ? `${s.store_code} — ${String(s.address).slice(0, 28)}` : s.store_code) + inactiveTag })
        if (s.market) { const k = s.market.trim().toLowerCase(); if (!mSet.has(k)) mSet.set(k, s.market.trim()) }
      }
      setStoreToMarket(s2m)
      setStoreOpts(stOpts.sort((a, b) => a.label.localeCompare(b.label)))
      setMarketOpts([...mSet.values()].sort().map(m => ({ id: m, label: m })))
      setRepOpts(rows.filter(r => r.employee_id).map(r => ({
        id: r.employee_id, label: (r.name || r.employee_id) + (r.is_active === false ? ' (inactive)' : ''),
        sublabel: r.email || undefined,
      })).sort((a, b) => a.label.localeCompare(b.label)))
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
    setLoading(false)
  }
  useEffect(() => { loadCatalogAndRoster() }, [includeInactive])  // eslint-disable-line react-hooks/exhaustive-deps

  // The currently FILTERED (store/market/employee) roster — what's on-screen AND what exports.
  const baseRows = useMemo(() => Object.values(maskedById), [maskedById])
  const filtered = useMemo(() => filterRows(baseRows, filter, {
    store: r => r.home_store, market: r => storeToMarket[r.home_store] || '', rep: r => r.employee_id,
  }), [baseRows, filter, storeToMarket])

  // Effective rows: revealed values merged in for whichever employees have been revealed (only when
  // the toggle is ON — turning it back off reverts to the masked copy already held in maskedById,
  // no extra fetch needed).
  const effectiveRows = useMemo(() => {
    if (!revealOn) return filtered
    return filtered.map(r => revealedById[r.employee_id] || r)
  }, [filtered, revealOn, revealedById])

  // Reveal fetch: ONLY the currently-visible (filtered) employees not already revealed — keeps the
  // audited scope exactly matched to what the admin is actually looking at, per employee_ids.
  async function doReveal() {
    const need = filtered.map(r => r.employee_id).filter(id => !revealedById[id])
    if (!need.length) { setRevealOn(true); return }
    setRevealBusy(true); setRevealError('')
    try {
      const r = await api(`/api/v1/hr/employee-database?reveal=true&include_inactive=${includeInactive ? 'true' : 'false'}&employee_ids=${encodeURIComponent(need.join(','))}`)
      const rows: EmpRow[] = r?.employees || []
      setRevealedById(m => ({ ...m, ...Object.fromEntries(rows.map(x => [x.employee_id, x])) }))
      setRevealOn(true)
    } catch (e: any) { setRevealError(e?.message || 'Reveal failed — admin/super-admin only.'); setRevealOn(false) }
    setRevealBusy(false)
  }
  function onToggleReveal(on: boolean) {
    if (on) doReveal(); else setRevealOn(false)
  }
  // If the filter narrows to newly-visible employees while reveal is already ON, fetch just the gap.
  useEffect(() => { if (revealOn) doReveal() }, [filter.stores.join(','), filter.markets.join(','), filter.reps.join(',')])  // eslint-disable-line react-hooks/exhaustive-deps

  const selectedDefs = useMemo(
    () => fieldsCatalog.filter(f => selectedFields.includes(f.key)),
    [fieldsCatalog, selectedFields],
  )
  const cols: ExportColumn[] = useMemo(() => selectedDefs.map(f => ({
    header: f.label, field: f.key,
    role: f.key === 'name' ? 'rep' : f.key === 'home_store' ? 'store' : undefined,
    get: (r: EmpRow) => fmtCell(f, r[f.key]),
  })), [selectedDefs])

  const bySection = useMemo(() => {
    const m: Record<string, FieldDef[]> = {}
    for (const f of fieldsCatalog) (m[f.section] ||= []).push(f)
    return m
  }, [fieldsCatalog])

  function toggleField(k: string) {
    setSelectedFields(s => s.includes(k) ? s.filter(x => x !== k) : [...s, k])
  }
  function toggleSection(section: string, on: boolean) {
    const keys = (bySection[section] || []).map(f => f.key)
    setSelectedFields(s => on ? Array.from(new Set([...s, ...keys])) : s.filter(k => !keys.includes(k)))
  }

  const anySensitiveSelected = selectedDefs.some(f => f.masked)

  return (
    <div style={{ padding: 24, maxWidth: 1200 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🗄️ Employee Database</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 14 }}>
        Every employee record collected across the roster + HR onboarding intake, in one exportable table.
        SSN and direct-deposit numbers are masked to the last 4 digits by default — see the notice below.
      </p>
      {msg && <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '10px 14px', fontSize: 13, marginBottom: 14 }}>{msg}</div>}

      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', marginBottom: 10 }}>
          <StandardFilterBar value={filter} onChange={setFilter}
            show={{ period: false, stores: true, markets: true, reps: true }}
            storeOptions={storeOpts} marketOptions={marketOpts} repOptions={repOpts}
            repLabel="Employees…" storeLabel="Stores…" marketLabel="Markets…" />
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text2)' }}>
            <input type="checkbox" checked={includeInactive} onChange={e => setIncludeInactive(e.target.checked)} /> Include inactive employees
          </label>
        </div>

        {anySensitiveSelected && (
          <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 12.5, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span>🔒 SSN and direct-deposit routing/account numbers are masked to the last 4 digits by default.</span>
            {isAdminUI ? (
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
                <input type="checkbox" checked={revealOn} disabled={revealBusy} onChange={e => onToggleReveal(e.target.checked)} />
                {revealBusy ? 'Revealing…' : '🔓 Show full values (admin — every reveal is logged)'}
              </label>
            ) : (
              <span style={{ color: 'var(--text3)' }}>Only an admin/super-admin can reveal the full values.</span>
            )}
            {revealError && <span style={{ color: '#dc2626' }}>{revealError}</span>}
          </div>
        )}

        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
          {SECTION_ORDER.filter(s => bySection[s]?.length).map(section => {
            const defs = bySection[section]
            const allOn = defs.every(f => selectedFields.includes(f.key))
            return (
              <div key={section} style={{ minWidth: 190 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <strong style={{ fontSize: 12 }}>{SECTION_LABELS[section] || section}</strong>
                  <button style={{ ...btn, fontSize: 10, padding: '2px 6px' }} onClick={() => toggleSection(section, !allOn)}>
                    {allOn ? 'Clear' : 'All'}
                  </button>
                </div>
                {defs.map(f => (
                  <label key={f.key} title={f.note} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, padding: '2px 0' }}>
                    <input type="checkbox" checked={selectedFields.includes(f.key)} onChange={() => toggleField(f.key)} />
                    {f.label}{f.masked && ' 🔒'}{f.designed_absent && ' ⚠️'}
                  </label>
                ))}
              </div>
            )
          })}
        </div>
      </div>

      <ReportShell
        title="Employee Database"
        subtitle={effectiveRows.length !== baseRows.length ? `${effectiveRows.length} of ${baseRows.length} employee(s) (filtered)` : undefined}
        columns={cols}
        rows={effectiveRows}
      />
      {loading && <div style={{ color: 'var(--text3)', fontSize: 13, marginTop: 8 }}>Loading…</div>}
    </div>
  )
}
