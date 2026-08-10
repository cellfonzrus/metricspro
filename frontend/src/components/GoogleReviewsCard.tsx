'use client'
import { useEffect, useState, useCallback } from 'react'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

// "My Google Reviews" — self-scoped, self-fetching card for the SIGNED-IN employee, one per store
// they're scheduled at (GET /storeops/google-reviews/my — identity from the auth token, same rule as
// every other self-view widget: MyChargebacks, /timeclock/status, …). Owner directive 2026-07-27
// (Phase 1). Renders nothing at all once loaded with zero stores, so a tenant that hasn't turned the
// integration on sees no new UI.
//
// Phase 1.5 (owner directive 2026-08-06, "google reviews everywhere"): optional `employeeId` lets a
// MANAGER embed this same card for a DIFFERENT employee (GET /storeops/google-reviews/employee/{id}
// instead of /my — backend enforces span/self access, this component never decides who may view
// whom). Optional `compact` renders a tighter variant for embedding on another page (a rating chip
// per store, reviews collapsed behind a toggle) instead of the full self-dashboard card. Neither
// prop changes the DEFAULT (self, /my, full) rendering when omitted.
//
// HONEST LIMITATION (surfaced verbatim from the backend, never hidden): Google Places API returns
// only Google's own curated "most relevant" review subset (typically ~5), not every review ever left.

interface ReviewItem {
  id: string; author_name?: string; rating?: number; review_text?: string
  relative_time?: string; possible_mention?: boolean
}
interface ActionPlan {
  id: string; status: string; plan_text?: string; dm_comments?: string
  due_date?: string; employee_marked_done_at?: string | null
}
interface StoreCard {
  store_code: string; address?: string; market?: string
  rating?: number | null; review_count?: number | null; target: number; reviews_needed?: number | null
  status: 'above' | 'below' | 'unknown'
  reviews: ReviewItem[]
  action_plan: ActionPlan | null
}

const stars = (n?: number) => n == null ? '—' : '★'.repeat(Math.round(n)) + '☆'.repeat(5 - Math.round(n))

const STATUS_LABEL: Record<string, string> = {
  above: 'Above target', below: 'Below target', unknown: 'No rating yet',
}

function ActionPlanBox({ storeCode, plan, onChanged }: { storeCode: string; plan: ActionPlan; onChanged: () => void }) {
  const [text, setText] = useState(plan.plan_text || '')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const submit = useCallback(() => {
    if (!text.trim()) { setMsg('Enter a plan before submitting.'); return }
    setBusy(true); setMsg('')
    api(`/api/v1/storeops/action-plans/${plan.id}/submit`, { method: 'POST', body: JSON.stringify({ plan_text: text }) })
      .then(() => onChanged()).catch((e: any) => setMsg('❌ ' + (e?.message || e))).finally(() => setBusy(false))
  }, [text, plan.id, onChanged])

  const markDone = useCallback(() => {
    setBusy(true); setMsg('')
    api(`/api/v1/storeops/action-plans/${plan.id}/employee-mark-done`, { method: 'POST' })
      .then(() => onChanged()).catch((e: any) => setMsg('❌ ' + (e?.message || e))).finally(() => setBusy(false))
  }, [plan.id, onChanged])

  const box: React.CSSProperties = { marginTop: 10, padding: 10, borderRadius: 8, background: '#fff7e6', border: '1px solid #fde68a' }

  if (plan.status === 'required') {
    return (
      <div style={box}>
        <div style={{ fontWeight: 700, fontSize: 12.5, marginBottom: 6 }}>📝 Action plan needed — {storeCode}'s Google rating is below target</div>
        <textarea value={text} onChange={e => setText(e.target.value)} rows={3} placeholder="What will you do to help improve this store's Google rating?"
          style={{ width: '100%', fontSize: 13, padding: 8, borderRadius: 6, border: '1px solid var(--border)', resize: 'vertical' }} />
        <div style={{ marginTop: 6, display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="btn" disabled={busy} onClick={submit} style={{ fontSize: 12.5 }}>{busy ? 'Submitting…' : 'Submit plan'}</button>
          {msg && <span style={{ fontSize: 12, color: '#b91c1c' }}>{msg}</span>}
        </div>
      </div>
    )
  }
  if (plan.status === 'submitted') {
    return <div style={box}><div style={{ fontWeight: 700, fontSize: 12.5 }}>⏳ Action plan submitted — awaiting manager review</div></div>
  }
  if (plan.status === 'pushed_back' || plan.status === 'in_progress') {
    const alreadyMarked = !!plan.employee_marked_done_at
    return (
      <div style={box}>
        <div style={{ fontWeight: 700, fontSize: 12.5, marginBottom: 4 }}>📌 Action plan — due {plan.due_date || '—'}</div>
        {plan.dm_comments && <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 6 }}>Manager notes: {plan.dm_comments}</div>}
        {alreadyMarked
          ? <div style={{ fontSize: 12.5, color: 'var(--text2)' }}>✅ Marked done — awaiting manager confirmation</div>
          : <button className="btn" disabled={busy} onClick={markDone} style={{ fontSize: 12.5 }}>{busy ? 'Saving…' : 'Mark as done'}</button>}
        {msg && <div style={{ fontSize: 12, color: '#b91c1c', marginTop: 4 }}>{msg}</div>}
      </div>
    )
  }
  return null
}

// Read-only one-liner for a compact embed — a manager viewing someone ELSE's card sees the plan's
// status, never the employee's own submit/mark-done controls (those stay self-service, gated
// server-side anyway, but showing them out of context on someone else's row is confusing UX).
function ActionPlanChip({ plan }: { plan: ActionPlan }) {
  const label = plan.status === 'required' ? 'Action plan needed'
    : plan.status === 'submitted' ? 'Plan submitted — awaiting review'
    : plan.status === 'pushed_back' ? `Plan due ${plan.due_date || '—'}`
    : plan.status === 'in_progress' ? (plan.employee_marked_done_at ? 'Marked done — awaiting confirmation' : `In progress — due ${plan.due_date || '—'}`)
    : null
  if (!label) return null
  return (
    <span style={{ background: '#fff7e6', color: '#92400e', borderRadius: 6, padding: '2px 8px', fontSize: 10.5, fontWeight: 700 }}>
      📝 {label}
    </span>
  )
}

function CompactStoreRow({ s }: { s: StoreCard }) {
  const [showReviews, setShowReviews] = useState(false)
  const above = s.status === 'above'
  const known = s.status !== 'unknown'
  const fg = !known ? '#475569' : above ? '#166534' : '#b91c1c'
  const bg = !known ? '#f1f5f9' : above ? '#e7f6ec' : '#fdeaea'
  return (
    <div style={{ borderRadius: 8, border: `1px solid ${fg}22`, padding: '8px 10px', background: bg }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 12.5, color: fg }}>{s.store_code}{s.market ? ` · ${s.market}` : ''}</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ color: '#d97706', fontSize: 12 }}>{stars(s.rating ?? undefined)}</span>
          <span style={{ fontWeight: 800, fontSize: 13, color: fg }}>{s.rating != null ? Number(s.rating).toFixed(1) : '—'}</span>
          {s.reviews_needed != null && s.reviews_needed > 0 && (
            <span title={`${s.reviews_needed} more 5-star reviews to reach ${Number(s.target).toFixed(1)}`}
              style={{ fontSize: 11, fontWeight: 700, color: fg }}>+{s.reviews_needed}⭐</span>
          )}
          <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 10, color: fg, border: `1px solid ${fg}44` }}>
            {STATUS_LABEL[s.status]}
          </span>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 10.5, color: 'var(--text3)' }}>target {Number(s.target).toFixed(1)} · {s.review_count ?? '—'} reviews</span>
        {s.action_plan && <ActionPlanChip plan={s.action_plan} />}
        {s.reviews.length > 0 && (
          <button onClick={() => setShowReviews(v => !v)} style={{ fontSize: 10.5, color: 'var(--accent,#2563eb)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
            {showReviews ? 'Hide reviews' : `Show ${s.reviews.length} review${s.reviews.length > 1 ? 's' : ''}`}
          </button>
        )}
      </div>
      {showReviews && (
        <div style={{ marginTop: 6 }}>
          {s.reviews.slice(0, 5).map(r => (
            <div key={r.id} style={{ padding: '4px 0', borderTop: '1px solid var(--border)', fontSize: 11.5 }}>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ color: '#d97706' }}>{stars(r.rating)}</span>
                <span style={{ color: 'var(--text2)', fontSize: 10.5 }}>{r.author_name || 'Google user'}{r.relative_time ? ` · ${r.relative_time}` : ''}</span>
                {r.possible_mention && <span style={{ background: '#e0e7ff', color: '#3730a3', borderRadius: 6, padding: '1px 5px', fontSize: 9.5, fontWeight: 700 }}>possible mention</span>}
              </div>
              {r.review_text && <div style={{ marginTop: 2 }}>{r.review_text}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function GoogleReviewsCard({ token, employeeId, compact, compactTitle }:
  { token?: string | null; employeeId?: string; compact?: boolean; compactTitle?: string }) {
  const auth = useAuth()
  const tok = token ?? auth?.token
  const [stores, setStores] = useState<StoreCard[] | null>(null)
  const [employeeName, setEmployeeName] = useState('')
  const [note, setNote] = useState('')

  const load = useCallback(() => {
    if (!tok) { setStores(null); return }
    const path = employeeId
      ? `/api/v1/storeops/google-reviews/employee/${encodeURIComponent(employeeId)}`
      : '/api/v1/storeops/google-reviews/my'
    api(path, { headers: { Authorization: `Bearer ${tok}` } })
      .then((r: any) => { setStores(Array.isArray(r?.stores) ? r.stores : []); setNote(r?.note || ''); setEmployeeName(r?.employee_name || '') })
      .catch(() => setStores([]))
  }, [tok, employeeId])
  useEffect(() => { load() }, [load])

  if (!stores || stores.length === 0) return null

  if (compact) {
    return (
      <div>
        {compactTitle && <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text2)', marginBottom: 6 }}>{compactTitle}</div>}
        <div style={{ display: 'grid', gap: 8 }}>
          {stores.map(s => <CompactStoreRow key={s.store_code} s={s} />)}
        </div>
      </div>
    )
  }

  const title = employeeId ? `⭐ ${employeeName || 'Employee'}'s Google Reviews` : "⭐ My Store's Google Reviews"

  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>{title}</div>
      {note && <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 10 }}>{note}</div>}
      <div style={{ display: 'grid', gap: 12 }}>
        {stores.map(s => {
          const above = s.status === 'above'
          const known = s.status !== 'unknown'
          const bg = !known ? '#f1f5f9' : above ? '#e7f6ec' : '#fdeaea'
          const fg = !known ? '#475569' : above ? '#166534' : '#b91c1c'
          return (
            <div key={s.store_code} style={{ borderRadius: 10, border: `1px solid ${fg}22`, overflow: 'hidden' }}>
              <div style={{ background: bg, padding: '10px 12px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 13.5, color: fg }}>{s.store_code}{s.market ? ` · ${s.market}` : ''}</div>
                  <div style={{ fontSize: 11.5, color: 'var(--text2)' }}>{s.address || ''}</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: 20, fontWeight: 800, color: fg }}>{s.rating != null ? Number(s.rating).toFixed(1) : '—'}</div>
                  <div style={{ fontSize: 11, color: 'var(--text2)' }}>target {Number(s.target).toFixed(1)} · {s.review_count ?? '—'} reviews</div>
                  {/* THE ACTIONABLE NUMBER (owner 2026-08-10): below target, say exactly how many more
                      5.0 reviews close the gap — a rating on its own tells nobody what to do. */}
                  {s.reviews_needed != null && s.reviews_needed > 0 && (
                    <div style={{ fontSize: 11.5, fontWeight: 700, color: fg, marginTop: 3 }}>
                      ⭐ {s.reviews_needed} more 5-star review{s.reviews_needed === 1 ? '' : 's'} to reach {Number(s.target).toFixed(1)}
                    </div>
                  )}
                </div>
              </div>
              <div style={{ padding: 12 }}>
                {s.reviews.length === 0 && <div style={{ fontSize: 12.5, color: 'var(--text3)' }}>No reviews pulled yet.</div>}
                {s.reviews.slice(0, 5).map(r => (
                  <div key={r.id} style={{ padding: '6px 0', borderBottom: '1px solid var(--border)', fontSize: 12.5 }}>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                      <span style={{ color: '#d97706' }}>{stars(r.rating)}</span>
                      <span style={{ color: 'var(--text2)', fontSize: 11 }}>{r.author_name || 'Google user'}{r.relative_time ? ` · ${r.relative_time}` : ''}</span>
                      {r.possible_mention && (
                        <span style={{ background: '#e0e7ff', color: '#3730a3', borderRadius: 6, padding: '1px 6px', fontSize: 10, fontWeight: 700 }}>
                          possible mention of you
                        </span>
                      )}
                    </div>
                    {r.review_text && <div style={{ marginTop: 3 }}>{r.review_text}</div>}
                  </div>
                ))}
                {s.action_plan && (employeeId
                  ? <ActionPlanChip plan={s.action_plan} />
                  : <ActionPlanBox storeCode={s.store_code} plan={s.action_plan} onChanged={load} />)}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
