'use client'
// SUPER-ADMIN TENANT GATES — which modules, connectors and report kinds each tenant is offered.
//
// OWNER (2026-11-10): "why is vidapay connector showing in boost ? we need to get these things under
// control before we roll out for outside customers — have an agent asses and create a dashboard for
// the super admin whereby these things can be gated out for the respective tenants right from the
// get go".
//
// THIS PAGE DECIDES NOTHING. Every verdict, every reason and every count on it is computed by
// `backend/app/modules/core/feature_gate.py` (the one truth table, proven DB-free by
// harness_feature_gate.py) and arrives on the payload. The page renders rows and posts toggles. That
// is deliberate and it is locked: `harness_feature_gate_lock.py` fails the build if this file grows
// its own list of modules / connectors / report kinds, re-implements the truth table, or spells a
// vendor, carrier or tenant name (RULE TWO).
//
// NOT A REPORT, so it carries no date filters and no Send bar: it is a control surface, like
// /admin/roles and /admin/business-types. The platform's report rule applies to reports.
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { SortableTh, useTableSort } from '@/components/SortableTh'

type Row = {
  kind: string; key: string; label: string
  allowed: boolean; basis: string; decided: boolean
  grant: boolean | null; by_rule: boolean; grant_home?: string
  applies_to_pos?: string[]; applies_to_carrier?: string[]; registry_ready?: boolean
}
type Summary = { total: number; on: number; off: number; decided: number; by_kind: Record<string, number> }
type Tenant = { org_id: string; name: string; is_active: boolean; posture: string; summary?: Summary | null }
type Board = {
  postures: string[]; posture_default_new_tenant: string; kinds: string[]
  grant_homes: Record<string, string>; bases: string[]
  tenants: Tenant[]
  org?: string; posture?: string; rows?: Row[]; summary?: Summary
}

// Click-a-header sorting through the ONE hook (owner directive 2026-08-10). Both readers are at
// module scope so the memo does not recompute on every render.
const tenantCell = (t: Tenant, f: string): string | number | boolean =>
  f === 'name' ? t.name
  : f === 'posture' ? t.posture
  : f === 'on' ? (t.summary?.on ?? -1)
  : f === 'off' ? (t.summary?.off ?? -1)
  : f === 'decided' ? (t.summary?.decided ?? -1)
  : ''
const rowCell = (r: Row, f: string): string | number | boolean =>
  f === 'label' ? r.label
  : f === 'allowed' ? r.allowed
  : f === 'basis' ? r.basis
  : f === 'by_rule' ? r.by_rule
  : f === 'grant' ? (r.grant === null ? '' : (r.grant ? 'granted' : 'gated off'))
  : ''

const emsg = (e: unknown) => (e as Error)?.message || String(e)

const KIND_LABEL: Record<string, string> = {
  module: 'Modules', connector: 'Connectors', report_kind: 'Report kinds',
}

export default function TenantGatesPage() {
  const [board, setBoard] = useState<Board | null>(null)
  const [org, setOrg] = useState('')
  const [detail, setDetail] = useState<Board | null>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  // Not `async`: a load that CAN setState synchronously is a cascading render, and the lint gate
  // refuses it. The same .then/.catch shape lib/report-kinds.ts already uses for its fetch-once hook.
  const loadBoard = useCallback(() => {
    api('/api/v1/core/tenant-gates')
      .then((b: Board) => setBoard(b))
      .catch((e: unknown) => setMsg('❌ ' + emsg(e)))
  }, [])
  // No synchronous clear on an empty org, for the same reason: the render below asks whether the
  // payload in hand is THIS company's (`detail.org === org`), so a stale grid is never shown.
  const loadDetail = useCallback((o: string) => {
    if (!o) return
    api(`/api/v1/core/tenant-gates?org=${encodeURIComponent(o)}`)
      .then((d: Board) => setDetail(d))
      .catch((e: unknown) => setMsg('❌ ' + emsg(e)))
  }, [])

  useEffect(() => { loadBoard() }, [loadBoard])
  useEffect(() => { loadDetail(org) }, [org, loadDetail])

  async function setPosture(o: string, posture: string) {
    setBusy(true); setMsg('⏳ Saving…')
    try {
      await api('/api/v1/core/tenant-gates/posture', { method: 'PUT', body: JSON.stringify({ org: o, posture }) })
      setMsg('✅ Saved')
      await loadBoard(); await loadDetail(org)
    } catch (e: unknown) { setMsg('❌ ' + emsg(e)) } finally { setBusy(false) }
  }

  async function setGrant(o: string, kind: string, key: string, grant: boolean | null) {
    setBusy(true); setMsg('⏳ Saving…')
    try {
      await api('/api/v1/core/tenant-gates/grant', { method: 'PUT', body: JSON.stringify({ org: o, kind, key, grant }) })
      setMsg('✅ Saved')
      await loadDetail(o); await loadBoard()
    } catch (e: unknown) { setMsg('❌ ' + emsg(e)) } finally { setBusy(false) }
  }

  const shown = detail && detail.org === org ? detail : null
  const rows = shown?.rows || []
  const posture = shown?.posture
  // Sorted ONCE over every row, then sliced per kind: a filter keeps the sorted order, and a hook
  // cannot live inside the per-kind map.
  const ts = useTableSort(board?.tenants || [], tenantCell)
  const rs = useTableSort(rows, rowCell)

  return (
    <div style={{ maxWidth: 1180 }}>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🚪 Tenant Gates</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          What each company is OFFERED — modules, connectors and report kinds. A company on{' '}
          <b>inherit</b> gets the platform defaults and its own declaration, exactly as before this page
          existed. A company on <b>gated</b> is offered <b>only what is granted here</b>, and that is what
          a <b>newly created company starts on</b>. Clearing a decision puts the thing back under the
          posture and the company&apos;s own declaration rather than leaving the opposite on record.
        </p>
        {/* No setup internals in rendered copy: the migration name belongs to the platform
            super-admin setup notice (lib/setupNotice.tsx), which is where it already is. The posture
            write itself returns the reason when the column is not there yet. */}
        {board && <p className="pg-note" style={{ color: 'var(--text3)', fontSize: 12, margin: '6px 0 0' }}>
          A new company starts <b>{board.posture_default_new_tenant}</b>. A grant is stored in the
          platform&apos;s existing override rows, so every surface that already read them answers the
          same way.
        </p>}
      </div>

      {msg && <div style={{ fontSize: 13, marginBottom: 10 }}>{msg}</div>}

      {/* ── the board: every company, its posture and how much is on ────────────────────────── */}
      <div className="card" style={{ padding: 0, overflowX: 'auto', marginBottom: 18 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr style={{ textAlign: 'left', color: 'var(--text3)' }}>
            <SortableTh field="name" sort={ts.sort} onSort={ts.toggle} style={th}>Company</SortableTh>
            <SortableTh field="posture" sort={ts.sort} onSort={ts.toggle} style={th}>Posture</SortableTh>
            <SortableTh field="on" sort={ts.sort} onSort={ts.toggle} style={thR}>On</SortableTh>
            <SortableTh field="off" sort={ts.sort} onSort={ts.toggle} style={thR}>Off</SortableTh>
            <SortableTh field="decided" sort={ts.sort} onSort={ts.toggle} style={thR}>Decided</SortableTh>
            <th style={th} />
          </tr></thead>
          <tbody>
            {ts.sorted.map(t => (
              <tr key={t.org_id} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={td}>
                  <b>{t.name}</b>
                  {!t.is_active && <span style={{ ...pill, marginLeft: 6 }}>inactive</span>}
                </td>
                <td style={td}>
                  <select value={t.posture} disabled={busy} style={fin}
                          onChange={e => setPosture(t.org_id, e.target.value)}>
                    {(board?.postures || []).map(p => <option key={p} value={p}>{p}</option>)}
                  </select>
                </td>
                <td style={tdR}>{t.summary ? t.summary.on : '—'}</td>
                <td style={tdR}>{t.summary ? t.summary.off : '—'}</td>
                <td style={tdR}>{t.summary ? t.summary.decided : '—'}</td>
                <td style={td}>
                  <button className="btn btn-secondary" onClick={() => setOrg(t.org_id === org ? '' : t.org_id)}>
                    {t.org_id === org ? 'Hide' : 'Open'}
                  </button>
                </td>
              </tr>
            ))}
            {!board && <tr><td style={td} colSpan={6}>Loading…</td></tr>}
            {board && !(board.tenants || []).length && <tr><td style={td} colSpan={6}>No companies.</td></tr>}
          </tbody>
        </table>
      </div>

      {/* ── one company's grid ───────────────────────────────────────────────────────────────── */}
      {org && shown && (
        <div>
          <h2 style={{ fontSize: 17, fontWeight: 700, margin: '0 0 4px' }}>
            {(board?.tenants || []).find(t => t.org_id === org)?.name || org}
            <span style={{ ...pill, marginLeft: 8 }}>{posture}</span>
          </h2>
          {posture === 'gated' && <p className="pg-note" style={{ color: 'var(--text3)', fontSize: 12, margin: '0 0 10px' }}>
            This company is offered only what is granted below. Everything not granted is off, and every
            surface says so in those words rather than reporting that nothing was declared.
          </p>}
          {(shown.kinds || []).map(kind => {
            const kr = rs.sorted.filter(r => r.kind === kind)
            if (!kr.length) return null
            return (
              <div key={kind} className="card" style={{ padding: 0, overflowX: 'auto', marginBottom: 14 }}>
                <div style={{ padding: '8px 12px', fontWeight: 700 }}>
                  {KIND_LABEL[kind] || kind}
                  <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12, marginLeft: 8 }}>
                    {kr.filter(r => r.allowed).length} of {kr.length} on
                  </span>
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead><tr style={{ textAlign: 'left', color: 'var(--text3)' }}>
                    <SortableTh field="label" sort={rs.sort} onSort={rs.toggle} style={th}>Thing</SortableTh>
                    <SortableTh field="allowed" sort={rs.sort} onSort={rs.toggle} style={th}>Offered?</SortableTh>
                    <SortableTh field="basis" sort={rs.sort} onSort={rs.toggle} style={th}>Why</SortableTh>
                    <SortableTh field="by_rule" sort={rs.sort} onSort={rs.toggle} style={th}>Platform rule</SortableTh>
                    <SortableTh field="grant" sort={rs.sort} onSort={rs.toggle} style={th}>Decision</SortableTh>
                  </tr></thead>
                  <tbody>
                    {kr.map(r => (
                      <tr key={r.key} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={td}><b>{r.label}</b>
                          <div style={{ color: 'var(--text3)', fontSize: 11 }}>{r.key}</div>
                        </td>
                        <td style={td}>{r.allowed ? '🟢 on' : '⚪ off'}</td>
                        <td style={{ ...td, color: 'var(--text2)' }}>{r.basis}</td>
                        <td style={td}>{r.by_rule ? 'would apply' : 'does not apply'}</td>
                        <td style={td}>
                          <select value={r.grant === null ? '' : (r.grant ? 'on' : 'off')} disabled={busy} style={fin}
                                  onChange={e => setGrant(org, kind, r.key,
                                    e.target.value === '' ? null : e.target.value === 'on')}>
                            <option value="">no decision</option>
                            <option value="on">granted</option>
                            <option value="off">gated off</option>
                          </select>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

const th: React.CSSProperties = { padding: '8px 12px', fontWeight: 600, whiteSpace: 'nowrap' }
const thR: React.CSSProperties = { ...th, textAlign: 'right' }
const td: React.CSSProperties = { padding: '8px 12px', verticalAlign: 'top' }
const tdR: React.CSSProperties = { ...td, textAlign: 'right' }
const fin: React.CSSProperties = { padding: '4px 6px', borderRadius: 6, border: '1px solid var(--border)' }
const pill: React.CSSProperties = {
  background: 'var(--c-bg-eef2ff)', color: 'var(--c-fg-3730a3)', padding: '0 6px',
  borderRadius: 6, fontSize: 12,
}
