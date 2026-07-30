'use client'
// AUTO-FIX PIPELINE BOARD — Phase 1 (mig 718, design docs/designs/auto-fix-pipeline.md §2f).
// Super-admin only (same gate as /admin/tenants: user.super_admin from /core/me; the backend 403s
// independently, so this is a courtesy gate, never the security boundary).
//
// PHASE 1 IS READ-ONLY ON PURPOSE. There is NO approve button anywhere on this page. A parked fix waits
// for the owner's push approval IN CHAT (Gate 2); a super-admin then records that decision through the
// API. Phase 2 (an in-app Approve action) is a separate owner decision — see §4 of the design note.
//
// RULE FOUR: the board renders through <ReportShell> → Excel / PDF / Print / Send (email + WhatsApp) for
//   free, over the CURRENTLY FILTERED rows.
// MIG 719 — "FIXED, and here is what YOU still have to do" (owner directive 2026-07-30). A shipped fix is
//   often INERT until a human acts outside the codebase (run the SQL, set the env var, correct a mapping
//   row, re-upload an export). Those steps live on the row as `user_actions`; a pushed fix with any step
//   outstanding is amber "Action required (N)", never a clean green FIXED. Ticking a step is a SUPER-ADMIN
//   action (the pipeline's service secret may WRITE the checklist but can never claim a human did it) and
//   every tick lands in the row's audit trail.
// RULE FIVE: <StandardFilterBar> supplies the universal period filter; store / market / rep are omitted
//   with an explicit, documented deviation — a fix request is a property of the CODE, not of a store or a
//   rep, so those three controls would be permanently empty here. The meaningful "who" dimension on a
//   platform surface is the affected TENANT, which is appended as a pick-don't-type control (RULE THREE)
//   alongside status / classification / module. Filter state drives the table, the rollup tile AND the
//   exports — one filtered array feeds all three.
// /api/v1 on every call (the curl-verified-≠-UI-verified trap: a bare /core/... path 404s silently here).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import ReportShell from '@/components/ReportShell'
import StandardFilterBar from '@/components/StandardFilterBar'
import EntityPicker from '@/components/EntityPicker'
import type { ExportColumn } from '@/lib/export'
import { emptyStandardFilter, matchesStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'

// One step a HUMAN must take for a shipped fix to actually work (mig 719).
type UserAction = {
  id: string
  kind: 'sql' | 'env' | 'config' | 'data' | 'other'
  instruction: string
  status: 'pending' | 'done'
  done_by: string | null
  done_at: string | null
}
type Fix = {
  id: string; org_id: string; signature: string; first_ref: string | null
  occurrence_count: number; sample_path: string | null; exc_type: string | null
  failure_ids: string[] | null; affected_orgs: { org_id: string; count: number }[] | null
  title: string | null; status: string; classification: string | null; module_agent: string | null
  branch: string | null; commit_sha: string | null; worktree: string | null
  triage_summary: string | null; proofs_summary: string | null; model: string | null
  tokens_triage: number; tokens_build: number; tokens_review: number; tokens_total?: number
  cost_usd: number | null; cost_basis: any
  approved_by: string | null; approved_at: string | null
  pushed_commit: string | null; pushed_at: string | null
  audit: { at: string; actor: string; actor_kind: string; from: string | null; to: string | null; note: string }[] | null
  created_by: string | null; created_at: string; updated_at: string
  // mig 719 — the server DERIVES action_required/pending_actions so the UI never recomputes them.
  user_actions: UserAction[] | null; pending_actions?: number; action_required?: boolean
  resolved_note: string | null
}
type Rollup = { fixes: number; tokens: number; cost_usd: number; priced: number; unpriced: number; shipped: number; parked: number; by_status: Record<string, number>; action_required?: number; pending_actions?: number }
type Candidate = {
  signature: string; sample_path: string; exc_type: string; category: string | null
  module_hint: string; label: string; count: number; latest_at: string | null
  first_ref: string | null; severity: string; failure_ids: string[]
  affected_orgs: { org_id: string; count: number }[]; sample_message: string | null
  sample_traceback: string | null
}
type Rate = {
  id: string; org_id: string; model: string; label: string | null
  usd_per_mtok_in: number; usd_per_mtok_out: number; effective_date: string
  output_share: number; is_active: boolean; notes: string | null
  blended_usd_per_mtok: number | null; updated_by: string | null
}

const STATUS_COLOR: Record<string, string> = {
  reported: '#6b7280', triaged: '#2563eb', building: '#7c3aed', gate1_parked: '#d97706',
  approved: '#0891b2', pushed: '#16a34a', rejected: '#dc2626', not_code: '#6b7280',
}
const STATUS_LABEL: Record<string, string> = {
  reported: 'Reported', triaged: 'Triaged', building: 'Building', gate1_parked: 'Parked — Gate 1',
  // 'pushed' reads as FIXED everywhere — on the chip, in the table and in every export. "Pushed" is
  // pipeline jargon; the owner asked to see that the thing they reported is FIXED.
  approved: 'Approved (recorded)', pushed: 'FIXED', rejected: 'Rejected', not_code: 'Not a code bug',
}
const CLASS_LABEL: Record<string, string> = {
  code_bug: 'Code bug', config: 'Config', data: 'Data', transient: 'Transient',
  duplicate: 'Duplicate', money_touching: 'Money-touching (owner-first)',
}
// The five user-action kinds the backend validates against (fix_pipeline.USER_ACTION_KINDS). Kept as a
// lookup, so an unknown kind from a future backend still renders (falls back to `other`) instead of
// blanking the row.
const KIND: Record<string, { label: string; bg: string; fg: string; head: string }> = {
  sql: { label: 'SQL', bg: '#ede9fe', fg: '#6d28d9', head: 'Run this SQL in the Supabase SQL editor' },
  env: { label: 'ENV VAR', bg: '#e0f2fe', fg: '#0369a1', head: 'Set this environment variable (Railway → Variables)' },
  config: { label: 'CONFIG', bg: '#fef3c7', fg: '#b45309', head: 'Fix this setting in the app' },
  data: { label: 'DATA', bg: '#dcfce7', fg: '#15803d', head: 'Correct / re-upload this data' },
  other: { label: 'ACTION', bg: '#f1f5f9', fg: '#475569', head: 'Do this' },
}
const kindOf = (k: string) => KIND[k] || KIND.other
const pendingOf = (r: Fix) =>
  r.pending_actions ?? (r.user_actions || []).filter(a => a.status !== 'done').length
const needsAction = (r: Fix) => r.action_required ?? (r.status === 'pushed' && pendingOf(r) > 0)
const when = (iso?: string | null) => { if (!iso) return '—'; try { return new Date(iso).toLocaleString() } catch { return iso } }
const usd = (n: number | null | undefined) =>
  n == null ? '—' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 4 }).format(n)
const nf = (n: number | null | undefined) => (n == null ? '—' : Number(n).toLocaleString())

// ── The per-fix checklist (mig 719) ───────────────────────────────────────────────────────────────
// Used in BOTH places a checklist appears — the "Action required" panel at the top and the row detail —
// so the two can never drift. `sql` instructions render in a copyable <pre> (the owner pastes them into
// the Supabase SQL editor); everything else renders as wrapped text.
function ActionChecklist({ fix, busyId, onMark }: {
  fix: Fix
  busyId: string | null
  onMark: (fix: Fix, a: UserAction, status: 'done' | 'pending') => void
}) {
  const actions = fix.user_actions || []
  const [copied, setCopied] = useState('')
  if (actions.length === 0) {
    return <div style={{ fontSize: 12.5, color: 'var(--text3)' }}>
      No user actions were recorded for this fix — there is nothing for you to do.
    </div>
  }
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {actions.map(a => {
        const done = a.status === 'done'
        const k = kindOf(a.kind)
        const lines = String(a.instruction || '').split('\n')
        const isSql = a.kind === 'sql'
        const head = isSql ? k.head : (lines[0] || k.head)
        const body = isSql ? a.instruction : lines.slice(1).join('\n').trim()
        return (
          <div key={a.id} style={{
            border: `1px solid ${done ? 'var(--border)' : '#fcd34d'}`, borderRadius: 9,
            padding: '9px 11px', background: done ? 'var(--surface2)' : 'var(--surface)',
          }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.4, padding: '2px 7px', borderRadius: 8, background: k.bg, color: k.fg }}>{k.label}</span>
              <b style={{ fontSize: 13, textDecoration: done ? 'line-through' : 'none', color: done ? 'var(--text3)' : 'inherit' }}>{head}</b>
              <span style={{ flex: 1 }} />
              {done && (
                <span style={{ fontSize: 11.5, color: '#16a34a' }}>
                  ✅ done{a.done_by ? ` · ${a.done_by}` : ''}{a.done_at ? ` · ${when(a.done_at)}` : ''}
                </span>
              )}
              <button className={done ? 'btn btn-sm' : 'btn btn-sm btn-primary'} disabled={busyId === a.id}
                onClick={() => onMark(fix, a, done ? 'pending' : 'done')}>
                {busyId === a.id ? '…' : done ? 'Undo' : '✓ Mark done'}
              </button>
            </div>
            {isSql ? (
              <div style={{ marginTop: 6 }}>
                <pre style={{ fontSize: 11.5, background: 'var(--surface2)', padding: 10, borderRadius: 8, overflow: 'auto', maxHeight: 260, whiteSpace: 'pre-wrap', margin: 0 }}>{a.instruction}</pre>
                <button className="btn btn-sm" style={{ marginTop: 5 }}
                  onClick={() => {
                    navigator.clipboard?.writeText(a.instruction)
                      .then(() => { setCopied(a.id); setTimeout(() => setCopied(''), 1800) })
                      .catch(() => { })
                  }}>{copied === a.id ? '✅ Copied' : '📋 Copy SQL'}</button>
              </div>
            ) : body ? (
              <div style={{ marginTop: 5, fontSize: 12.5, color: 'var(--text2)', whiteSpace: 'pre-wrap' }}>{body}</div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

export default function FixRequestsBoard() {
  const { user, loading: authLoading } = useAuth()
  const isSuper = !!user?.super_admin

  const [rows, setRows] = useState<Fix[]>([])
  const [serverRollup, setServerRollup] = useState<Rollup | null>(null)
  const [notes, setNotes] = useState<{ approval: string; cost: string }>({ approval: '', cost: '' })
  const [hint, setHint] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [allOrgs, setAllOrgs] = useState(true)          // platform view by default (a code bug spans tenants)
  const [sel, setSel] = useState<Fix | null>(null)
  const [detail, setDetail] = useState<{ failures: any[]; tracebacks: any[] } | null>(null)
  const [detailBusy, setDetailBusy] = useState(false)

  // Universal filters (RULE FIVE) + appended pipeline filters.
  const [filters, setFilters] = useState<StandardFilterValue>(emptyStandardFilter(''))
  const [status, setStatus] = useState('')
  const [classification, setClassification] = useState('')
  const [moduleAgent, setModuleAgent] = useState('')
  const [tenant, setTenant] = useState('')
  const [onlyAction, setOnlyAction] = useState(false)     // "show me only what I still have to do"

  // Checklist (mig 719): which item is mid-flight + the last result message.
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [actionMsg, setActionMsg] = useState('')

  // Feed (not-yet-registered signatures) + rate editor
  const [feed, setFeed] = useState<Candidate[]>([])
  const [feedMeta, setFeedMeta] = useState<{ scanned: number; skipped: number }>({ scanned: 0, skipped: 0 })
  const [rates, setRates] = useState<Rate[]>([])
  const [rateModels, setRateModels] = useState<string[]>([])
  const [blendNote, setBlendNote] = useState('')
  const [rateForm, setRateForm] = useState({ model: '', usd_per_mtok_in: '', usd_per_mtok_out: '', output_share: '0.2', effective_date: '' })
  const [rateMsg, setRateMsg] = useState('')

  const load = useCallback(() => {
    setLoading(true); setErr('')
    api(`/api/v1/core/fix-pipeline/requests?all_orgs=${allOrgs ? 1 : 0}&limit=1000`)
      .then((d: any) => {
        setRows(d.fix_requests || [])
        setServerRollup(d.rollup || null)
        setNotes({ approval: d.approval_note || '', cost: d.cost_note || '' })
        setHint(d.hint || '')
      })
      .catch((e: any) => setErr(e?.message || String(e)))
      .finally(() => setLoading(false))
  }, [allOrgs])

  const loadFeed = useCallback(() => {
    api(`/api/v1/core/fix-pipeline/feed?all_orgs=${allOrgs ? 1 : 0}&limit=800`)
      .then((d: any) => {
        setFeed(d.candidates || [])
        setFeedMeta({ scanned: d.scanned || 0, skipped: d.skipped_already_registered || 0 })
      })
      .catch(() => { setFeed([]) })
  }, [allOrgs])

  const loadRates = useCallback(() => {
    api('/api/v1/core/fix-pipeline/token-rates')
      .then((d: any) => {
        setRates(d.token_rates || [])
        setBlendNote(d.blend_note || '')
        const models = Array.from(new Set([...(d.models_known || []), ...(d.models_in_use || [])])) as string[]
        setRateModels(models)
      })
      .catch(() => {})
  }, [])

  useEffect(() => { if (isSuper) { load(); loadFeed(); loadRates() } }, [isSuper, load, loadFeed, loadRates])

  // ── Client-side filtering: ONE filtered array drives the table, the tile and every export ──────────
  const filtered = useMemo(() => rows.filter(r => {
    if (status && r.status !== status) return false
    if (classification && (r.classification || '') !== classification) return false
    if (moduleAgent && (r.module_agent || '') !== moduleAgent) return false
    if (tenant && !(r.org_id === tenant || (r.affected_orgs || []).some(a => a.org_id === tenant))) return false
    if (onlyAction && !needsAction(r)) return false
    return matchesStandardFilter(r, filters, { date: (x: Fix) => x.created_at })
  }), [rows, status, classification, moduleAgent, tenant, onlyAction, filters])

  const tile = useMemo(() => {
    let tokens = 0, cost = 0, priced = 0, unpriced = 0, parked = 0, shipped = 0, blocked = 0, steps = 0
    for (const r of filtered) {
      tokens += (r.tokens_triage || 0) + (r.tokens_build || 0) + (r.tokens_review || 0)
      if (r.cost_usd == null) unpriced++; else { cost += Number(r.cost_usd) || 0; priced++ }
      if (r.status === 'pushed') shipped++
      else if (r.status === 'gate1_parked' || r.status === 'approved') parked++
      if (needsAction(r)) { blocked++; steps += pendingOf(r) }
    }
    return { fixes: filtered.length, tokens, cost: Math.round(cost * 10000) / 10000, priced, unpriced, parked, shipped, blocked, steps }
  }, [filtered])

  // Shipped fixes that are NOT actually working yet, newest first — the panel at the top of the board.
  const blockedRows = useMemo(() => filtered.filter(needsAction), [filtered])

  const statusOpts = useMemo(() => Array.from(new Set(rows.map(r => r.status))).sort(), [rows])
  const classOpts = useMemo(() => Array.from(new Set(rows.map(r => r.classification).filter(Boolean))) as string[], [rows])
  const moduleOpts = useMemo(() => Array.from(new Set(rows.map(r => r.module_agent).filter(Boolean))) as string[], [rows])
  const tenantOpts = useMemo(() => {
    const s = new Set<string>()
    rows.forEach(r => { if (r.org_id) s.add(r.org_id); (r.affected_orgs || []).forEach(a => a.org_id && s.add(a.org_id)) })
    return Array.from(s).sort().map(id => ({ id, label: id.slice(0, 8) + '…', sublabel: id }))
  }, [rows])

  function openDetail(r: Fix) {
    setSel(r); setDetail(null); setDetailBusy(true)
    api(`/api/v1/core/fix-pipeline/requests/${r.id}?all_orgs=${allOrgs ? 1 : 0}`)
      .then((d: any) => setDetail({ failures: d.failures || [], tracebacks: d.tracebacks || [] }))
      .catch(() => setDetail({ failures: [], tracebacks: [] }))
      .finally(() => setDetailBusy(false))
  }

  // Tick ONE checklist step done / undone (mig 719). OPTIMISTIC: the UI flips immediately and reverts on
  // failure, so ticking twenty SQL steps never feels like twenty round trips.
  //   • org_id is an explicit QUERY PARAM carrying the ROW's own tenant (never a constant) and `all_orgs`
  //     mirrors the board's current scope — the backend still stamps the write with the row's org.
  //   • /api/v1 prefix: a bare /core/... path passes a curl check and 404s silently here.
  async function markAction(fix: Fix, a: UserAction, next: 'done' | 'pending') {
    const apply = (r: Fix): Fix => {
      if (r.id !== fix.id) return r
      const ua = (r.user_actions || []).map(x => x.id === a.id
        ? { ...x, status: next, done_by: next === 'done' ? (user?.email || 'you') : null, done_at: next === 'done' ? new Date().toISOString() : null }
        : x)
      const pending = ua.filter(x => x.status !== 'done').length
      return { ...r, user_actions: ua, pending_actions: pending, action_required: r.status === 'pushed' && pending > 0 }
    }
    const before = rows
    const selBefore = sel
    setBusyAction(a.id); setActionMsg('')
    setRows(rs => rs.map(apply))
    setSel(s => (s ? apply(s) : s))
    try {
      await api(`/api/v1/core/fix-pipeline/requests/${fix.id}/actions/${encodeURIComponent(a.id)}?org_id=${encodeURIComponent(fix.org_id)}&all_orgs=${allOrgs ? 1 : 0}`,
        { method: 'PATCH', body: JSON.stringify({ status: next }) })
      setActionMsg(next === 'done' ? '✅ Marked done — recorded against this fix with your name and the time.' : '↩︎ Reopened.')
    } catch (e) {
      setRows(before)                                  // revert: the server is the truth
      setSel(selBefore)
      setActionMsg('❌ ' + String((e as Error)?.message || e))
    } finally { setBusyAction(null) }
  }

  async function saveRate() {
    setRateMsg('')
    try {
      const body: any = {
        model: rateForm.model.trim(),
        usd_per_mtok_in: Number(rateForm.usd_per_mtok_in),
        usd_per_mtok_out: Number(rateForm.usd_per_mtok_out),
        output_share: Number(rateForm.output_share),
      }
      if (rateForm.effective_date) body.effective_date = rateForm.effective_date
      const d = await api('/api/v1/core/fix-pipeline/token-rates', { method: 'PUT', body: JSON.stringify(body) })
      setRateMsg(`✅ Saved ${d.model} @ ${d.effective_date} — blended ${usd(d.blended_usd_per_mtok)}/MTok. The whole board re-prices.`)
      loadRates(); load()
    } catch (e: any) { setRateMsg('❌ ' + (e?.message || e)) }
  }

  const cols: ExportColumn[] = [
    { header: 'Registered', field: 'created_at', type: 'date', get: (r: Fix) => when(r.created_at) },
    { header: 'Signature', field: 'signature', get: (r: Fix) => r.signature },
    { header: 'Exception', field: 'exc_type', get: (r: Fix) => r.exc_type || '' },
    { header: 'Occurrences', field: 'occurrence_count', align: 'right', get: (r: Fix) => r.occurrence_count ?? 0 },
    { header: 'Classification', field: 'classification', get: (r: Fix) => CLASS_LABEL[r.classification || ''] || r.classification || '' },
    { header: 'Module agent', field: 'module_agent', get: (r: Fix) => r.module_agent || '' },
    { header: 'Status', field: 'status', get: (r: Fix) => STATUS_LABEL[r.status] || r.status },
    // What the OWNER still has to do for a shipped fix to actually work (mig 719) — on screen and in
    // every export, so an emailed board is as actionable as the page.
    {
      header: 'Your actions', field: 'action_required', get: (r: Fix) =>
        needsAction(r) ? `⚠️ Action required (${pendingOf(r)})`
          : (r.user_actions || []).length ? '✅ All done'
            : r.status === 'pushed' ? 'Nothing to do' : ''
    },
    { header: 'What shipped', field: 'resolved_note', get: (r: Fix) => r.resolved_note || '' },
    { header: 'Branch', field: 'branch', get: (r: Fix) => r.branch || '' },
    { header: 'Commit', field: 'commit_sha', get: (r: Fix) => (r.commit_sha || '').slice(0, 10) },
    { header: 'Proofs', field: 'proofs_summary', get: (r: Fix) => r.proofs_summary || '' },
    { header: 'Model', field: 'model', get: (r: Fix) => r.model || '' },
    { header: 'Tokens (triage)', field: 'tokens_triage', align: 'right', get: (r: Fix) => r.tokens_triage || 0 },
    { header: 'Tokens (build)', field: 'tokens_build', align: 'right', get: (r: Fix) => r.tokens_build || 0 },
    { header: 'Tokens (review)', field: 'tokens_review', align: 'right', get: (r: Fix) => r.tokens_review || 0 },
    { header: 'Tokens (total)', field: 'tokens_total', align: 'right', get: (r: Fix) => (r.tokens_triage || 0) + (r.tokens_build || 0) + (r.tokens_review || 0) },
    { header: 'Cost', field: 'cost_usd', money: true, get: (r: Fix) => (r.cost_usd == null ? 0 : r.cost_usd) },
    { header: 'Tenant', field: 'org_id', get: (r: Fix) => r.org_id },
    { header: 'Pushed commit', field: 'pushed_commit', get: (r: Fix) => r.pushed_commit || '' },
  ]

  if (authLoading) return <div style={{ padding: 24, color: 'var(--text3)' }}>Loading…</div>
  if (!isSuper) return (
    <div style={{ padding: 24, maxWidth: 620 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700 }}>🛠️ Auto-Fix Pipeline</h1>
      <div className="card" style={{ padding: 16, color: 'var(--text2)' }}>
        This board is for <b>platform super-admins</b> only — it tracks system errors that were triaged into
        code fixes, the branch each fix is parked on, and what the AI work cost. Ask a super-admin if you
        need access. Your company’s own error log lives at <a href="/failures">Failure Logs</a>.
      </div>
    </div>
  )

  const sub: React.CSSProperties = { fontSize: 12, color: 'var(--text3)' }
  const tileBox: React.CSSProperties = { padding: '10px 14px', borderRadius: 10, border: '1px solid var(--border)', background: 'var(--surface)', minWidth: 132 }

  return (
    <div style={{ padding: 20 }}>
      <div style={{ marginBottom: 12 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🛠️ Auto-Fix Pipeline</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 900 }}>
          Every distinct system error that was triaged into a fix, one row per problem (50 occurrences of one
          bug = one row). Each row shows where the fix is parked, what proved it, and what the AI work cost.
          <b> Nothing here deploys anything:</b> a parked fix ships only when you say “push it” in chat, and
          that decision is then recorded on the row. There is deliberately no approve button on this page.
          {' '}Once a fix ships it shows as <b style={{ color: '#16a34a' }}>FIXED</b> — and if it needs
          something from you before it actually works (run a SQL block, set an environment variable, correct
          a setting, re-upload data), it stays <b style={{ color: '#b45309' }}>Action required</b> with a
          checklist you tick off.
        </p>
      </div>

      {hint && <div className="card" style={{ padding: 12, marginBottom: 12, background: '#fffbeb', borderColor: '#fde68a', fontSize: 13 }}>{hint}</div>}
      {err && <div className="card" style={{ padding: 12, marginBottom: 12, color: '#dc2626' }}>{err}</div>}

      {/* Period rollup tile — computed over the FILTERED rows, so it always agrees with the table + export */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
        <div style={tileBox}><div style={sub}>Fixes in view</div><div style={{ fontSize: 21, fontWeight: 700 }}>{nf(tile.fixes)}</div></div>
        <div style={tileBox}><div style={sub}>Tokens</div><div style={{ fontSize: 21, fontWeight: 700 }}>{nf(tile.tokens)}</div></div>
        <div style={tileBox}><div style={sub}>Cost (blended)</div><div style={{ fontSize: 21, fontWeight: 700 }}>{usd(tile.cost)}</div>
          {tile.unpriced > 0 && <div style={{ ...sub, color: '#b45309' }}>{tile.unpriced} unpriced (no rate for that model)</div>}</div>
        <div style={tileBox}><div style={sub}>Awaiting your push</div><div style={{ fontSize: 21, fontWeight: 700, color: '#d97706' }}>{nf(tile.parked)}</div></div>
        <div style={tileBox}><div style={sub}>Fixed</div><div style={{ fontSize: 21, fontWeight: 700, color: '#16a34a' }}>{nf(tile.shipped)}</div></div>
        <div style={{ ...tileBox, ...(tile.blocked > 0 ? { borderColor: '#fbbf24', background: '#fffbeb' } : {}) }}>
          <div style={sub}>Needs YOU</div>
          <div style={{ fontSize: 21, fontWeight: 700, color: tile.blocked > 0 ? '#b45309' : 'inherit' }}>{nf(tile.blocked)}</div>
          <div style={sub}>{tile.blocked > 0 ? `${nf(tile.steps)} step${tile.steps === 1 ? '' : 's'} outstanding` : 'nothing outstanding'}</div>
        </div>
      </div>

      {/* The $ caveat, stated on-page and never hidden (design §2e) */}
      <div className="card" style={{ padding: '9px 12px', marginBottom: 12, fontSize: 12, color: 'var(--text2)' }}>
        💵 <b>How the $ is worked out:</b> {notes.cost || 'Cost uses a blended input/output rate from the editable token-rate table.'}
        {' '}Rates are data, not code — edit them below; the board re-prices instantly. A row with no matching
        rate shows “—” rather than a guessed number.
        {serverRollup && <> {' · '}Registry total (unfiltered): {nf(serverRollup.fixes)} fixes · {nf(serverRollup.tokens)} tokens · {usd(serverRollup.cost_usd)}</>}
      </div>

      {/* RULE FIVE bar (period) + appended pipeline filters (pick-don't-type) */}
      <StandardFilterBar
        value={filters} onChange={setFilters} periodMode="range"
        show={{ period: true, stores: false, markets: false, reps: false }}
        right={
          <>
            <label style={{ fontSize: 12, color: 'var(--text2)' }}>Status{' '}
              <select value={status} onChange={e => setStatus(e.target.value)}
                style={{ padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
                <option value="">All</option>
                {statusOpts.map(s => <option key={s} value={s}>{STATUS_LABEL[s] || s}</option>)}
              </select></label>
            <label style={{ fontSize: 12, color: 'var(--text2)' }}>Class{' '}
              <select value={classification} onChange={e => setClassification(e.target.value)}
                style={{ padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
                <option value="">All</option>
                {classOpts.map(s => <option key={s} value={s}>{CLASS_LABEL[s] || s}</option>)}
              </select></label>
            {moduleOpts.length > 0 && (
              <label style={{ fontSize: 12, color: 'var(--text2)' }}>Agent{' '}
                <select value={moduleAgent} onChange={e => setModuleAgent(e.target.value)}
                  style={{ padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
                  <option value="">All</option>
                  {moduleOpts.map(s => <option key={s} value={s}>{s}</option>)}
                </select></label>
            )}
            {tenantOpts.length > 1 && (
              <EntityPicker options={tenantOpts} value={tenant} onChange={v => setTenant(v || '')}
                placeholder="Affected company…" width={190} ariaLabel="Filter by affected company" />
            )}
            {/* mig 719: "show me only the fixes that still need something from me" */}
            <label style={{
              fontSize: 12, display: 'inline-flex', gap: 5, alignItems: 'center', padding: '4px 8px',
              borderRadius: 8, fontWeight: onlyAction ? 700 : 400,
              border: `1px solid ${onlyAction ? '#fbbf24' : 'var(--border)'}`,
              background: onlyAction ? '#fffbeb' : 'transparent', color: onlyAction ? '#b45309' : 'var(--text2)',
            }}>
              <input type="checkbox" checked={onlyAction} onChange={e => setOnlyAction(e.target.checked)} />
              ⚠️ Action required only
            </label>
            <label style={{ fontSize: 12, color: 'var(--text2)', display: 'inline-flex', gap: 5, alignItems: 'center' }}>
              <input type="checkbox" checked={allOrgs} onChange={e => setAllOrgs(e.target.checked)} />
              All companies
            </label>
            <button className="btn btn-sm" onClick={() => { load(); loadFeed() }}>Refresh</button>
          </>
        }
      />

      {/* ── FIXED, but not working yet: the things only YOU can do (mig 719) ─────────────────────── */}
      {blockedRows.length > 0 && (
        <div className="card" style={{ padding: 16, margin: '12px 0', background: '#fffbeb', borderColor: '#fcd34d' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
            <b style={{ fontSize: 15, color: '#b45309' }}>⚠️ {blockedRows.length} fix{blockedRows.length === 1 ? '' : 'es'} shipped but not live yet — {nf(tile.steps)} step{tile.steps === 1 ? '' : 's'} need you</b>
            <span style={{ flex: 1 }} />
            {actionMsg && <span style={{ fontSize: 12.5 }}>{actionMsg}</span>}
          </div>
          <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '4px 0 10px', maxWidth: 900 }}>
            The code is deployed, but each of these needs something done outside the app before it actually
            works — a SQL block run in Supabase, an environment variable set, a setting corrected, or data
            re-uploaded. Tick each step off as you do it; your name and the time are recorded on the fix.
          </p>
          {blockedRows.map(r => (
            <details key={r.id} className="card" style={{ padding: 12, marginTop: 8, background: 'var(--surface)' }} open={blockedRows.length === 1}>
              <summary style={{ cursor: 'pointer', fontSize: 13.5, fontWeight: 600 }}>
                {r.title || r.signature}
                <span style={{ fontSize: 11, marginLeft: 8, padding: '1px 7px', borderRadius: 8, background: '#fef3c7', color: '#b45309', fontWeight: 700 }}>
                  {pendingOf(r)} step{pendingOf(r) === 1 ? '' : 's'} left
                </span>
              </summary>
              {r.resolved_note && <div style={{ fontSize: 12.5, color: 'var(--text2)', margin: '7px 0' }}><b>What shipped:</b> {r.resolved_note}</div>}
              <div style={{ marginTop: 8 }}>
                <ActionChecklist fix={r} busyId={busyAction} onMark={markAction} />
              </div>
            </details>
          ))}
        </div>
      )}

      {loading ? <div className="card" style={{ padding: 16 }}>Loading…</div> : (
        <ReportShell
          title="Auto-Fix Pipeline" subtitle="One row per distinct problem · tokens and cost per fix"
          filename="fix_requests" columns={cols} rows={filtered} totals stickyHeader
          onRowClick={openDetail}
          rowStyle={(r: Fix) => (needsAction(r) ? { background: '#fffbeb' } : undefined)}
        />
      )}

      {/* ── Detail: the row + the TRACEBACK (design §2a — it was in the DB since mig 112, never rendered) ── */}
      {sel && (
        <div className="card" style={{ padding: 16, marginTop: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
            <span style={{ width: 9, height: 9, borderRadius: 9, background: STATUS_COLOR[sel.status] || '#888' }} />
            <b style={{ fontSize: 15 }}>{sel.title || sel.signature}</b>
            <span style={{ fontSize: 11, padding: '1px 7px', borderRadius: 8, background: (STATUS_COLOR[sel.status] || '#888') + '22', color: STATUS_COLOR[sel.status] || '#888', fontWeight: 700 }}>
              {sel.status === 'pushed' ? '✅ ' : ''}{STATUS_LABEL[sel.status] || sel.status}
            </span>
            {needsAction(sel) && (
              <span style={{ fontSize: 11, padding: '1px 7px', borderRadius: 8, background: '#fef3c7', color: '#b45309', fontWeight: 700 }}>
                ⚠️ Action required ({pendingOf(sel)})
              </span>
            )}
            {sel.classification && <span style={{ ...sub }}>· {CLASS_LABEL[sel.classification] || sel.classification}</span>}
            <span style={{ flex: 1 }} />
            <button className="btn btn-sm" onClick={() => { setSel(null); setDetail(null) }}>Close</button>
          </div>

          {/* Phase-1 approval notice — a NOTE, never a button */}
          {(sel.status === 'gate1_parked') && (
            <div style={{ padding: '9px 12px', borderRadius: 8, background: '#fffbeb', border: '1px solid #fde68a', fontSize: 13, marginBottom: 10 }}>
              ⏳ <b>Awaiting push approval — approve in chat.</b> This fix is built and parked on its branch.
              Nothing merges or deploys until you say “push it” in chat; a super-admin then records that
              approval against this row. {notes.approval}
            </div>
          )}
          {sel.status === 'approved' && (
            <div style={{ padding: '9px 12px', borderRadius: 8, background: '#ecfeff', border: '1px solid #a5f3fc', fontSize: 13, marginBottom: 10 }}>
              ✔️ Your chat approval was recorded by <b>{sel.approved_by || '—'}</b> at {when(sel.approved_at)}. The
              merge/push itself is still done by the operator — this board never deploys.
            </div>
          )}
          {/* FIXED banner + the "what shipped" note (mig 719) */}
          {sel.status === 'pushed' && (
            <div style={{
              padding: '9px 12px', borderRadius: 8, fontSize: 13, marginBottom: 10,
              background: needsAction(sel) ? '#fffbeb' : '#f0fdf4',
              border: `1px solid ${needsAction(sel) ? '#fcd34d' : '#bbf7d0'}`,
            }}>
              {needsAction(sel)
                ? <><b>✅ FIXED — but not working yet.</b> The code shipped{sel.pushed_at ? ` ${when(sel.pushed_at)}` : ''}
                  {sel.pushed_commit ? <> (<code style={{ fontSize: 11.5 }}>{sel.pushed_commit.slice(0, 10)}</code>)</> : null}
                  , and {pendingOf(sel)} step{pendingOf(sel) === 1 ? '' : 's'} below still need you.</>
                : <><b>✅ FIXED.</b> Shipped{sel.pushed_at ? ` ${when(sel.pushed_at)}` : ''}
                  {sel.pushed_commit ? <> (<code style={{ fontSize: 11.5 }}>{sel.pushed_commit.slice(0, 10)}</code>)</> : null}
                  {(sel.user_actions || []).length ? ' — and every step you had to take is ticked off.' : ' — nothing was needed from you.'}</>}
              {sel.resolved_note && <div style={{ marginTop: 5 }}>{sel.resolved_note}</div>}
            </div>
          )}
          {sel.status !== 'pushed' && sel.resolved_note && (
            <div style={{ fontSize: 13, marginBottom: 10 }}><b>What shipped:</b> {sel.resolved_note}</div>
          )}
          {sel.classification === 'money_touching' && (
            <div style={{ padding: '9px 12px', borderRadius: 8, background: '#fef2f2', border: '1px solid #fecaca', fontSize: 13, marginBottom: 10 }}>
              💰 <b>Money-touching — owner-first.</b> Automation is never allowed to build this one; it waits
              for your explicit go-ahead (AGENT_CONTRACT §7).
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 10, fontSize: 13 }}>
            <div><div style={sub}>Signature</div><code style={{ fontSize: 12 }}>{sel.signature}</code></div>
            <div><div style={sub}>Occurrences</div>{nf(sel.occurrence_count)}{sel.first_ref ? <span style={sub}> · first ref {sel.first_ref}</span> : null}</div>
            <div><div style={sub}>Example path</div><code style={{ fontSize: 12 }}>{sel.sample_path || '—'}</code></div>
            <div><div style={sub}>Module agent</div>{sel.module_agent || '—'}</div>
            <div><div style={sub}>Branch (parked)</div><code style={{ fontSize: 12 }}>{sel.branch || '—'}</code></div>
            <div><div style={sub}>Commit</div><code style={{ fontSize: 12 }}>{sel.commit_sha || '—'}</code></div>
            <div><div style={sub}>Worktree</div><code style={{ fontSize: 12 }}>{sel.worktree || '—'}</code></div>
            <div><div style={sub}>Companies affected</div>{(sel.affected_orgs || []).length ? (sel.affected_orgs || []).map(a => `${a.org_id.slice(0, 8)}… (${a.count})`).join(', ') : sel.org_id.slice(0, 8) + '…'}</div>
            <div><div style={sub}>Tokens</div>triage {nf(sel.tokens_triage)} · build {nf(sel.tokens_build)} · review {nf(sel.tokens_review)} = <b>{nf((sel.tokens_triage || 0) + (sel.tokens_build || 0) + (sel.tokens_review || 0))}</b></div>
            <div><div style={sub}>Cost</div><b>{usd(sel.cost_usd)}</b>{sel.model ? <span style={sub}> · {sel.model}</span> : null}</div>
          </div>

          {sel.cost_basis && (
            <details style={{ marginTop: 10 }}>
              <summary style={{ fontSize: 12.5, cursor: 'pointer', color: 'var(--text2)' }}>How this $ was calculated</summary>
              <pre style={{ fontSize: 11.5, background: 'var(--surface2)', padding: 10, borderRadius: 8, overflow: 'auto', marginTop: 6 }}>
{JSON.stringify(sel.cost_basis, null, 2)}
              </pre>
            </details>
          )}

          {/* The per-row checklist (mig 719) — the same component the top panel uses */}
          <details style={{ marginTop: 12 }} open={(sel.user_actions || []).length > 0}>
            <summary style={{ fontSize: 12.5, cursor: 'pointer', color: 'var(--text2)' }}>
              What YOU have to do ({(sel.user_actions || []).filter(a => a.status !== 'done').length} of {(sel.user_actions || []).length} outstanding)
            </summary>
            <div style={{ marginTop: 8 }}>
              <ActionChecklist fix={sel} busyId={busyAction} onMark={markAction} />
              {actionMsg && <div style={{ fontSize: 12.5, marginTop: 7 }}>{actionMsg}</div>}
            </div>
          </details>

          {sel.triage_summary && <div style={{ marginTop: 10, fontSize: 13 }}><b>Triage:</b> {sel.triage_summary}</div>}
          {sel.proofs_summary && <div style={{ marginTop: 6, fontSize: 13 }}><b>Proofs:</b> {sel.proofs_summary}</div>}

          {/* Audit trail — every status change, who and when */}
          <details style={{ marginTop: 10 }} open>
            <summary style={{ fontSize: 12.5, cursor: 'pointer', color: 'var(--text2)' }}>
              Audit trail ({(sel.audit || []).length})
            </summary>
            <div style={{ marginTop: 6 }}>
              {(sel.audit || []).length === 0 && <div style={sub}>No entries.</div>}
              {(sel.audit || []).map((a, i) => (
                <div key={i} style={{ fontSize: 12.5, borderTop: '1px solid var(--border)', padding: '5px 0' }}>
                  <span style={{ color: 'var(--text3)' }}>{when(a.at)}</span>{' · '}
                  <b>{a.actor}</b> <span style={sub}>({a.actor_kind})</span>{' · '}
                  {a.from || '—'} → {a.to || '—'}{a.note ? ` · ${a.note}` : ''}
                </div>
              ))}
            </div>
          </details>

          {/* THE TRACEBACK */}
          <details style={{ marginTop: 10 }} open>
            <summary style={{ fontSize: 12.5, cursor: 'pointer', color: 'var(--text2)' }}>
              Technical detail / traceback {detailBusy ? '(loading…)' : `(${(detail?.tracebacks || []).length} occurrence${(detail?.tracebacks || []).length === 1 ? '' : 's'})`}
            </summary>
            {(detail?.tracebacks || []).length === 0 && !detailBusy && (
              <div style={{ ...sub, marginTop: 6 }}>No stored occurrence rows for this request (the failure log rows may have aged out, or none were folded in).</div>
            )}
            {(detail?.tracebacks || []).map((t: any) => (
              <div key={t.id} style={{ marginTop: 8 }}>
                <div style={{ fontSize: 12, color: 'var(--text3)' }}>{when(t.created_at)}{t.ref ? ` · ref ${t.ref}` : ''} — {t.message}</div>
                {t.traceback
                  ? <pre style={{ fontSize: 11.5, background: 'var(--surface2)', padding: 10, borderRadius: 8, overflow: 'auto', maxHeight: 320, whiteSpace: 'pre-wrap' }}>{t.traceback}</pre>
                  : <div style={sub}>No traceback stored for this occurrence.</div>}
              </div>
            ))}
          </details>
        </div>
      )}

      {/* ── Feed: signatures NOT yet registered (read-only; the triage routine folds these in) ────────── */}
      <details className="card" style={{ padding: 16, marginTop: 14 }}>
        <summary style={{ fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>
          📥 Not yet triaged ({feed.length}) — errors with no fix request yet
        </summary>
        <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '8px 0', maxWidth: 860 }}>
          Unreviewed entries in the system error log, grouped by the same signature the pipeline dedupes on.
          Scanned {nf(feedMeta.scanned)} log rows; {nf(feedMeta.skipped)} already belong to a fix request above.
          This list is informational — registering and building is done by the scheduled triage routine (or by
          you, in chat). Nothing on this page starts a build.
        </p>
        <div className="table-wrapper">
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Signature', 'Kind', 'Occurrences', 'Latest', 'Companies', 'Example'].map(h =>
                <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {feed.length === 0 && <tr><td colSpan={6} style={{ padding: '10px', fontSize: 13, color: 'var(--text3)' }}>Nothing unregistered. 🎉</td></tr>}
              {feed.map(c => (
                <tr key={c.signature}>
                  <td style={{ padding: '6px 10px', fontSize: 12, borderTop: '1px solid var(--border)' }}><code>{c.signature}</code></td>
                  <td style={{ padding: '6px 10px', fontSize: 12.5, borderTop: '1px solid var(--border)' }}>{c.label}<span style={sub}> · {c.module_hint}</span></td>
                  <td style={{ padding: '6px 10px', fontSize: 12.5, borderTop: '1px solid var(--border)' }}>{nf(c.count)}</td>
                  <td style={{ padding: '6px 10px', fontSize: 12, borderTop: '1px solid var(--border)', whiteSpace: 'nowrap' }}>{when(c.latest_at)}</td>
                  <td style={{ padding: '6px 10px', fontSize: 12, borderTop: '1px solid var(--border)' }}>{(c.affected_orgs || []).map(a => `${a.org_id.slice(0, 8)}…`).join(', ')}</td>
                  <td style={{ padding: '6px 10px', fontSize: 12, borderTop: '1px solid var(--border)' }}>{c.sample_message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

      {/* ── AI token rates (config-as-data, RULE TWO) ─────────────────────────────────────────────── */}
      <details className="card" style={{ padding: 16, marginTop: 14 }}>
        <summary style={{ fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>💵 AI token rates ({rates.length})</summary>
        <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '8px 0', maxWidth: 880 }}>
          {blendNote || 'Cost = total tokens × (input × (1 − output share) + output × output share).'} Rates are
          seeded from the published Anthropic price list and are yours to change — including any negotiated
          rate. Saving a rate with a future date keeps the old one for past months (rate history).
        </p>
        <div className="table-wrapper" style={{ marginBottom: 12 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Model', 'Effective', '$/MTok in', '$/MTok out', 'Output share', 'Blended $/MTok', 'Active', 'Last edited by'].map(h =>
                <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {rates.length === 0 && <tr><td colSpan={8} style={{ padding: 10, fontSize: 13, color: 'var(--text3)' }}>No rates yet — run migration 718, then add one below.</td></tr>}
              {rates.map(r => (
                <tr key={r.id} style={{ opacity: r.is_active ? 1 : 0.55 }}>
                  <td style={{ padding: '6px 10px', fontSize: 12.5, borderTop: '1px solid var(--border)' }}>{r.label || r.model}<div style={sub}><code>{r.model}</code></div></td>
                  <td style={{ padding: '6px 10px', fontSize: 12.5, borderTop: '1px solid var(--border)' }}>{String(r.effective_date).slice(0, 10)}</td>
                  <td style={{ padding: '6px 10px', fontSize: 12.5, borderTop: '1px solid var(--border)' }}>{usd(r.usd_per_mtok_in)}</td>
                  <td style={{ padding: '6px 10px', fontSize: 12.5, borderTop: '1px solid var(--border)' }}>{usd(r.usd_per_mtok_out)}</td>
                  <td style={{ padding: '6px 10px', fontSize: 12.5, borderTop: '1px solid var(--border)' }}>{Math.round(Number(r.output_share) * 100)}%</td>
                  <td style={{ padding: '6px 10px', fontSize: 12.5, borderTop: '1px solid var(--border)' }}>{usd(r.blended_usd_per_mtok)}</td>
                  <td style={{ padding: '6px 10px', fontSize: 12.5, borderTop: '1px solid var(--border)' }}>{r.is_active ? 'yes' : 'no'}</td>
                  <td style={{ padding: '6px 10px', fontSize: 12, borderTop: '1px solid var(--border)' }}>{r.updated_by || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Model<br />
            {/* pick-don't-type (RULE THREE): choose from models the registry/rate table already knows,
                with an explicit create affordance for a brand-new model name. */}
            <EntityPicker options={rateModels.map(m => ({ id: m, label: m }))} value={rateForm.model}
              onChange={v => setRateForm(f => ({ ...f, model: v || '' }))}
              allowCreate onCreate={v => setRateForm(f => ({ ...f, model: v }))}
              placeholder="Model…" width={210} ariaLabel="Model" />
          </label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>$/MTok in<br />
            <input type="number" step="0.01" min="0" value={rateForm.usd_per_mtok_in}
              onChange={e => setRateForm(f => ({ ...f, usd_per_mtok_in: e.target.value }))}
              style={{ padding: '7px 9px', borderRadius: 8, border: '1px solid var(--border)', width: 110 }} />
          </label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>$/MTok out<br />
            <input type="number" step="0.01" min="0" value={rateForm.usd_per_mtok_out}
              onChange={e => setRateForm(f => ({ ...f, usd_per_mtok_out: e.target.value }))}
              style={{ padding: '7px 9px', borderRadius: 8, border: '1px solid var(--border)', width: 110 }} />
          </label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Output share (0–1)<br />
            <input type="number" step="0.05" min="0" max="1" value={rateForm.output_share}
              onChange={e => setRateForm(f => ({ ...f, output_share: e.target.value }))}
              style={{ padding: '7px 9px', borderRadius: 8, border: '1px solid var(--border)', width: 120 }} />
          </label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Effective from<br />
            <input type="date" value={rateForm.effective_date}
              onChange={e => setRateForm(f => ({ ...f, effective_date: e.target.value }))}
              style={{ padding: '7px 9px', borderRadius: 8, border: '1px solid var(--border)' }} />
          </label>
          <button className="btn btn-sm btn-primary" disabled={!rateForm.model || !rateForm.usd_per_mtok_in || !rateForm.usd_per_mtok_out} onClick={saveRate}>Save rate</button>
          {rateMsg && <span style={{ fontSize: 12.5 }}>{rateMsg}</span>}
        </div>
      </details>
    </div>
  )
}
