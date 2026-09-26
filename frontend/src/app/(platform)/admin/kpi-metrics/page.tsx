'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

// KPI DEFINITIONS — the platform-wide registry (owner 2026-09-25: "create a KPI dashboard, which
// will be used platform wide, move the KPI from Boost in that right now and when other KPIs are
// defined they will be there as well").
//
// THE POINT OF THIS PAGE. Which KPIs a tenant has was, until now, a hardcoded list in the frontend:
// commcalc/kpi/page.tsx shipped six core metrics plus a literal EXTRA_METRICS of Zulu / TWP /
// Address Checks to EVERY tenant, whether or not they measure them. That is how a Boost-only estate
// ended up being asked to hand-enter two Total Wireless metrics. The registry
// (commcalc.carrier_kpi_metric, mig 060) already knew better — it lists seven for the house org and
// no Zulu at all — it was simply not what the screens read.
//
// So this page is the ONE place a KPI comes into existence, per org and optionally per carrier, and
// every KPI surface reads it. A metric that is not defined for a tenant does not render for that
// tenant: absence is the default, never a hidden row of zeros.
//
// VISIBILITY (owner, same message): "hidden unless they are defined, the admin can see but the other
// users cannot see unless KPIs are defined and a pathway to upload them is there." So: an admin can
// always open this page, including for an org with nothing defined yet — that is exactly when they
// need it — and the pathway is right here (add one, or paste a set). Everyone else only ever meets a
// KPI through the reports, and only once it exists here.
//
// RULE TWO: no carrier, tenant or product name appears in this file. metric_key, label and target
// are DATA the admin types. The seeded labels are already neutral ("App Attach %", not a brand).
//
// The backend is unchanged and pre-existing: GET/POST/DELETE /commcalc/carrier-kpi-metrics, which
// upsert on (org_id, carrier_id, metric_key). This page adds no endpoint and no migration.

type Metric = {
  id?: string
  org_id?: string
  carrier_id?: string
  metric_key: string
  label: string
  target_default?: number | string | null
  payout_config_col?: string | null
  sort?: number | null
  is_active?: boolean
  source_mode?: string | null
}

// The nil carrier = "this org's default set", the shape mig 060 seeds. A row against a real carrier
// id overrides the default for that carrier only.
const NIL_CARRIER = '00000000-0000-0000-0000-000000000000'

const card: React.CSSProperties = {
  border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', background: 'var(--bg1)',
}
const cell: React.CSSProperties = { padding: '8px 10px', borderBottom: '1px solid var(--border)' }

/** A metric_key is referenced by reports, payout config and the incentive gate, so it is an
 *  identifier, not prose: lowercase, no spaces. Enforced here so a typo cannot create a second
 *  metric that looks like an existing one. */
function normalizeKey(s: string) {
  return (s || '').trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
}

export default function KpiMetricsAdminPage() {
  const [rows, setRows] = useState<Metric[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  // The organisation defaults to the company you are ACTING AS (the tenant switcher), never the house
  // org: this used to start on ORG_ID, so a super admin standing in another tenant (e.g. a franchise
  // store) was shown — and would have written — the house org's KPIs. Harness: harness_acting_org_default.py.
  const { activeOrg, tenant, tenants } = useAuth()
  const actingOrg = activeOrg || tenant?.org_id || ORG_ID
  const [orgPick, setOrgPick] = useState<string>('')   // '' = follow the acting company
  const orgId = orgPick || actingOrg
  const [draft, setDraft] = useState<Metric>({ metric_key: '', label: '' })
  const [busy, setBusy] = useState(false)
  // The endpoint answers `ready: false` when migration 060 has not been applied — the registry table
  // is absent, so it returns an EMPTY list plus the built-in defaults. That is not the same fact as
  // "this tenant has defined no KPIs", and rendering them identically would be this page's own
  // silent zero: an admin would read a blank slate and start typing into a table that cannot store
  // anything. Tracked separately and said in words.
  const [ready, setReady] = useState(true)

  // The fetch runs in the effect and sets state only in its callbacks; `load()` (Reload / after a save)
  // shows the spinner and bumps `tick` to re-run it. A late answer for a previous org is dropped.
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let alive = true
    api(`/api/v1/commcalc/carrier-kpi-metrics?org_id=${encodeURIComponent(orgId)}`)
      .then((r: Metric[] | { metrics?: Metric[]; rows?: Metric[]; ready?: boolean } | null) => {
        if (!alive) return
        setErr('')
        setRows(Array.isArray(r) ? r : (r?.metrics || r?.rows || []))
        setReady(Array.isArray(r) ? true : r?.ready !== false)
      })
      .catch((e: unknown) => { if (alive) setErr(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [orgId, tick])
  const load = useCallback(() => { setLoading(true); setErr(''); setTick(t => t + 1) }, [])

  const sorted = useMemo(
    () => [...rows].sort((a, b) => (Number(a.sort) || 0) - (Number(b.sort) || 0) || a.metric_key.localeCompare(b.metric_key)),
    [rows])

  async function save(m: Metric) {
    setBusy(true); setMsg(''); setErr('')
    try {
      await api(`/api/v1/commcalc/carrier-kpi-metrics?org_id=${encodeURIComponent(orgId)}`, {
        method: 'POST', body: JSON.stringify(m),
      })
      setMsg(`Saved ${m.label || m.metric_key}.`)
      load()
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }

  async function addDraft() {
    const key = normalizeKey(draft.metric_key)
    if (!key) { setErr('A metric needs a key — the identifier the reports and payout config use.'); return }
    if (!draft.label.trim()) { setErr('A metric needs a label — what people read on the report.'); return }
    if (sorted.some(r => r.metric_key === key)) { setErr(`“${key}” is already defined for this organisation.`); return }
    await save({
      ...draft,
      metric_key: key,
      label: draft.label.trim(),
      carrier_id: NIL_CARRIER,
      sort: (sorted.reduce((n, r) => Math.max(n, Number(r.sort) || 0), 0) || 0) + 1,
      is_active: true,
    })
    setDraft({ metric_key: '', label: '' })
  }

  async function remove(m: Metric) {
    if (!m.id) return
    // Deleting a definition stops every surface showing that KPI for this org. It does NOT delete
    // any measured value — those live in kpi_actual / rep_commissions and are left untouched, so
    // re-adding the metric brings its history back into view.
    if (!confirm(`Remove “${m.label}” (${m.metric_key}) from this organisation's KPIs?\n\n`
      + `It will stop appearing on every KPI surface for this tenant. Measured values are NOT deleted — `
      + `re-adding the metric shows them again.`)) return
    setBusy(true); setErr(''); setMsg('')
    try {
      await api(`/api/v1/commcalc/carrier-kpi-metrics/${encodeURIComponent(m.id)}?org_id=${encodeURIComponent(orgId)}`,
        { method: 'DELETE' })
      setMsg(`Removed ${m.label}.`)
      load()
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }

  return (
    <div style={{ padding: 24, maxWidth: 1200 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>KPI Definitions</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 820 }}>
        Every KPI the platform knows about, per organisation. A KPI defined here appears on that
        tenant&rsquo;s KPI reports, rep scorecards and manual-entry grid; one that is not defined does
        not appear for them at all. This is the only place a KPI comes into existence.
      </p>

      <div style={{ ...card, marginTop: 14, background: '#f0f9ff', borderColor: '#bae6fd' }}>
        <strong style={{ fontSize: 13 }}>Why a tenant sees nothing here</strong>
        <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>
          An organisation with no rows below measures no KPIs yet &mdash; that is a blank slate, not a
          fault, and its users simply see no KPI columns. Define them here and they appear. Nothing is
          shown to a tenant on the strength of another tenant&rsquo;s metrics.
        </div>
      </div>

      <div style={{ marginTop: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Organisation</label>
        <select className="input" style={{ minWidth: 260, fontSize: 13 }}
                value={orgPick} onChange={e => { setLoading(true); setOrgPick(e.target.value) }}>
          <option value="">{(tenant?.name || 'This company') + ' (the company you are in)'}</option>
          {tenants.filter(t => t.org_id !== actingOrg).map(t => (
            <option key={t.org_id} value={t.org_id}>{t.name}</option>
          ))}
        </select>
        <span style={{ fontSize: 11, color: 'var(--text3)', fontFamily: 'monospace' }}>{orgId}</span>
        <button className="btn btn-secondary" onClick={load} disabled={loading}>Reload</button>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>
          {loading ? 'Loading…' : `${sorted.length} metric${sorted.length === 1 ? '' : 's'} defined`}
        </span>
      </div>

      {err && <div style={{ ...card, marginTop: 12, borderColor: '#fecaca', background: '#fef2f2', color: '#b91c1c', fontSize: 13 }}>{err}</div>}
      {msg && <div style={{ ...card, marginTop: 12, borderColor: '#bbf7d0', background: '#f0fdf4', color: '#15803d', fontSize: 13 }}>{msg}</div>}

      {!loading && !ready && (
        <div style={{ ...card, marginTop: 16, borderColor: '#fed7aa', background: '#fff7ed', color: '#9a3412', fontSize: 13 }}>
          <strong>The registry table is not present on this database.</strong> Migration
          {' '}<code>060_carrier_kpi_metrics.sql</code> has not been applied, so nothing can be saved
          here yet &mdash; this is not an organisation with no KPIs, it is a store that cannot answer.
          Apply the migration and reload.
        </div>
      )}

      {!loading && ready && sorted.length === 0 && (
        <div style={{ ...card, marginTop: 16, textAlign: 'center', color: 'var(--text3)', padding: 40 }}>
          No KPIs are defined for this organisation yet. Add the first one below &mdash; its users will
          see it on their reports as soon as it exists.
        </div>
      )}

      {sorted.length > 0 && (
        <div className="table-wrapper" style={{ marginTop: 16 }}>
          <table>
            <thead>
              <tr>
                <th>Key</th><th>Label (what people read)</th>
                <th style={{ textAlign: 'right' }}>Default target</th>
                <th>Per-period target column</th>
                <th style={{ textAlign: 'right' }}>Order</th>
                <th>Active</th><th></th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(m => (
                <tr key={m.id || m.metric_key}>
                  <td style={{ ...cell, fontFamily: 'monospace', fontSize: 12 }}>{m.metric_key}</td>
                  <td style={cell}>
                    <input className="input" defaultValue={m.label} style={{ maxWidth: 260 }}
                           onBlur={e => { const v = e.target.value.trim(); if (v && v !== m.label) save({ ...m, label: v }) }} />
                  </td>
                  <td style={{ ...cell, textAlign: 'right' }}>
                    <input className="input" defaultValue={m.target_default ?? ''} style={{ width: 90, textAlign: 'right' }}
                           onBlur={e => { const v = e.target.value.trim(); if (v !== String(m.target_default ?? '')) save({ ...m, target_default: v === '' ? null : Number(v) }) }} />
                  </td>
                  <td style={{ ...cell, fontFamily: 'monospace', fontSize: 11, color: 'var(--text3)' }}>
                    {m.payout_config_col || <span style={{ fontStyle: 'italic' }}>uses the default target</span>}
                  </td>
                  <td style={{ ...cell, textAlign: 'right' }}>
                    <input className="input" defaultValue={m.sort ?? ''} style={{ width: 64, textAlign: 'right' }}
                           onBlur={e => { const v = e.target.value.trim(); if (v !== String(m.sort ?? '')) save({ ...m, sort: v === '' ? null : Number(v) }) }} />
                  </td>
                  <td style={cell}>
                    <input type="checkbox" checked={m.is_active !== false}
                           onChange={e => save({ ...m, is_active: e.target.checked })} />
                  </td>
                  <td style={{ ...cell, textAlign: 'right' }}>
                    <button className="btn btn-secondary" style={{ fontSize: 12 }}
                            onClick={() => remove(m)} disabled={busy || !m.id}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* THE PATHWAY. The owner asked that defining a KPI be possible from here, not only by running
          SQL — otherwise "hidden unless defined" just means permanently hidden. */}
      <div style={{ ...card, marginTop: 20 }}>
        <strong style={{ fontSize: 13 }}>Define a KPI for this organisation</strong>
        <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr 140px 220px', gap: 12, marginTop: 10, alignItems: 'end' }}>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>Key (identifier)</div>
            <input className="input" value={draft.metric_key} placeholder="e.g. attach_rate"
                   onChange={e => setDraft(d => ({ ...d, metric_key: e.target.value }))} />
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>Label (what people read)</div>
            <input className="input" value={draft.label} placeholder="e.g. Attach Rate %"
                   onChange={e => setDraft(d => ({ ...d, label: e.target.value }))} />
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>Default target</div>
            <input className="input" value={String(draft.target_default ?? '')} placeholder="e.g. 65"
                   onChange={e => setDraft(d => ({ ...d, target_default: e.target.value }))} />
          </div>
          <div>
            <button className="btn btn-primary" onClick={addDraft} disabled={busy}>Add KPI</button>
          </div>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
          The key is what reports, payout config and the incentive gate reference, so it is normalised
          to lowercase with underscores and must be unique for this organisation. The label is free
          text and can be changed at any time without touching anything that reads the key.
        </div>
      </div>
    </div>
  )
}
