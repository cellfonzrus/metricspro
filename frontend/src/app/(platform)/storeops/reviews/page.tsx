'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { ReportExportBar, type ExportColumn } from '@/components/ReportExportBar'

// Google Reviews — DM/manager dashboard (Phase 1, owner directive 2026-07-27). Every store under the
// caller's span (org-tree/market/store scope — full admin sees every store), rating vs target
// highlighted, with the action-plan review queue for reps at those stores. GET
// /storeops/google-reviews/dm-dashboard already applies the SAME span scoping the rest of the app
// uses (org-hierarchy manager view) — this page never re-derives it client-side.

interface ActionPlan {
  id: string; employee_id: string; employee_name?: string; store_code: string
  status: string; plan_text?: string; dm_comments?: string; due_date?: string
  employee_marked_done_at?: string | null; created_at?: string
}
interface StoreCard {
  store_code: string; address?: string; market?: string; is_active?: boolean
  rating?: number | null; review_count?: number | null; target: number; status: string
  reviews: any[]; action_plans: ActionPlan[]; open_action_plan_count: number
  fetched_at?: string | null
}

const STATUS_LABEL: Record<string, string> = {
  required: 'Needs plan', submitted: 'Awaiting review', pushed_back: 'Sent back — in progress',
  in_progress: 'In progress', completed: 'Completed',
}

// Gate-1 N6 (optional, quick): a rating with no visible staleness signal looks fresher than it may
// be — a store nobody has swept in weeks reads identically to one swept an hour ago. Cheap,
// client-side "time ago" from the same fetched_at the backend already returns per store card.
function timeAgo(iso?: string | null): string {
  if (!iso) return 'never fetched'
  const ms = Date.now() - new Date(iso).getTime()
  if (!Number.isFinite(ms) || ms < 0) return 'just now'
  const mins = Math.floor(ms / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

function PlanRow({ plan, onChanged }: { plan: ActionPlan; onChanged: () => void }) {
  const [dueDate, setDueDate] = useState(plan.due_date || '')
  const [comments, setComments] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const call = useCallback((path: string, body: any) => {
    setBusy(true); setMsg('')
    api(`/api/v1/storeops/action-plans/${plan.id}/${path}`, { method: 'POST', body: JSON.stringify(body) })
      .then(() => onChanged()).catch((e: any) => setMsg('❌ ' + (e?.message || e))).finally(() => setBusy(false))
  }, [plan.id, onChanged])

  return (
    <div style={{ padding: 10, borderRadius: 8, border: '1px solid var(--border)', marginBottom: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6 }}>
        <div style={{ fontWeight: 700, fontSize: 12.5 }}>{plan.employee_name || plan.employee_id} — {plan.store_code}</div>
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text2)' }}>{STATUS_LABEL[plan.status] || plan.status}</span>
      </div>
      {plan.plan_text && <div style={{ fontSize: 12.5, marginTop: 4 }}><b>Plan:</b> {plan.plan_text}</div>}
      {plan.dm_comments && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}><b>Your notes:</b> {plan.dm_comments}</div>}
      {plan.due_date && <div style={{ fontSize: 11.5, color: 'var(--text2)', marginTop: 2 }}>Due {plan.due_date}</div>}

      {plan.status === 'submitted' && (
        <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <input type="date" style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12 }}
            value={dueDate} onChange={e => setDueDate(e.target.value)} />
          <input placeholder="Comments (optional)" style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, flex: 1, minWidth: 120 }}
            value={comments} onChange={e => setComments(e.target.value)} />
          <button className="btn btn-secondary" style={{ fontSize: 11.5, padding: '4px 8px' }} disabled={busy || !dueDate}
            onClick={() => call('push-back', { due_date: dueDate, dm_comments: comments })}>Push back (set due date)</button>
          <button className="btn btn-primary" style={{ fontSize: 11.5, padding: '4px 8px' }} disabled={busy}
            onClick={() => call('approve', { due_date: dueDate || undefined, dm_comments: comments || undefined })}>Approve as-is</button>
        </div>
      )}
      {(plan.status === 'in_progress' || plan.status === 'pushed_back') && plan.employee_marked_done_at && (
        <div style={{ marginTop: 8 }}>
          <button className="btn btn-primary" style={{ fontSize: 11.5, padding: '4px 8px' }} disabled={busy}
            onClick={() => call('dm-confirm-complete', {})}>✅ Confirm complete</button>
        </div>
      )}
      {msg && <div style={{ fontSize: 11.5, color: '#b91c1c', marginTop: 4 }}>{msg}</div>}
    </div>
  )
}

export default function GoogleReviewsDashboardPage() {
  const [stores, setStores] = useState<StoreCard[]>([])
  const [targetDefault, setTargetDefault] = useState(4.7)
  const [canEdit, setCanEdit] = useState(false)
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    api('/api/v1/storeops/google-reviews/dm-dashboard').then((r: any) => {
      setStores(r?.stores || []); setTargetDefault(r?.target_default ?? 4.7)
    }).catch(console.error).finally(() => setLoading(false))
    api('/api/v1/storeops/google-reviews/config').then((r: any) => setCanEdit(!!r?.can_edit)).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return stores
    return stores.filter(s => s.store_code.toLowerCase().includes(needle) || (s.market || '').toLowerCase().includes(needle))
  }, [stores, q])

  const allPlans = useMemo(() =>
    filtered.flatMap(s => (s.action_plans || []).map(p => ({ ...p, _store: s.store_code })))
      .filter(p => p.status !== 'completed')
      .sort((a, b) => (a.status === 'submitted' ? -1 : 1) - (b.status === 'submitted' ? -1 : 1)),
    [filtered])

  const exportCols: ExportColumn[] = [
    { header: 'Store', get: (r: StoreCard) => r.store_code },
    { header: 'Market', get: (r: StoreCard) => r.market || '' },
    { header: 'Rating', get: (r: StoreCard) => r.rating ?? '' },
    { header: 'Target', get: (r: StoreCard) => r.target },
    { header: 'Status', get: (r: StoreCard) => r.status },
    { header: 'Review count', get: (r: StoreCard) => r.review_count ?? '' },
    { header: 'Open action plans', get: (r: StoreCard) => r.open_action_plan_count },
  ]

  if (loading) return <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>⭐ Google Reviews</h1>
          <p style={{ color: 'var(--text2)', fontSize: 13.5, margin: '4px 0 0' }}>
            Rating vs target (default {targetDefault.toFixed(1)}) for every store in your span, and the action-plan review queue.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input placeholder="Search store / market…" value={q} onChange={e => setQ(e.target.value)}
            style={{ padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13.5 }} />
          <ReportExportBar title="Google Reviews — Store Ratings" columns={exportCols} rows={filtered} compact />
          {canEdit && <Link href="/storeops/reviews/config" className="btn btn-secondary" style={{ fontSize: 13 }}>⚙️ Settings</Link>}
        </div>
      </div>

      {allPlans.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>📝 Action-plan review queue ({allPlans.length})</div>
          {allPlans.map(p => <PlanRow key={p.id} plan={p} onChanged={load} />)}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
        {filtered.map(s => {
          const above = s.status === 'above'
          const known = s.status !== 'unknown'
          const bg = !known ? '#f1f5f9' : above ? '#e7f6ec' : '#fdeaea'
          const fg = !known ? '#475569' : above ? '#166534' : '#b91c1c'
          return (
            <div key={s.store_code} className="card" style={{ padding: 0, overflow: 'hidden' }}>
              <div style={{ background: bg, padding: '10px 14px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 14, color: fg }}>{s.store_code}</div>
                  <div style={{ fontSize: 11, color: 'var(--text2)' }}>{s.market || ''}{s.is_active === false ? ' · inactive' : ''}</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: 22, fontWeight: 800, color: fg }}>{s.rating != null ? Number(s.rating).toFixed(1) : '—'}</div>
                  <div style={{ fontSize: 10.5, color: 'var(--text2)' }}>target {s.target.toFixed(1)}</div>
                </div>
              </div>
              <div style={{ padding: 12, fontSize: 12.5 }}>
                <div style={{ color: 'var(--text2)', marginBottom: 2 }}>{s.review_count ?? '—'} reviews on file</div>
                <div style={{ color: 'var(--text3)', fontSize: 11, marginBottom: 6 }}>Updated {timeAgo(s.fetched_at)}</div>
                {s.open_action_plan_count > 0 && (
                  <div style={{ display: 'inline-block', background: '#fff7e6', color: '#92400e', borderRadius: 6, padding: '2px 8px', fontWeight: 700, fontSize: 11 }}>
                    {s.open_action_plan_count} open action plan{s.open_action_plan_count > 1 ? 's' : ''}
                  </div>
                )}
              </div>
            </div>
          )
        })}
        {filtered.length === 0 && <div style={{ color: 'var(--text3)', padding: 20 }}>No stores in view.</div>}
      </div>
    </div>
  )
}
