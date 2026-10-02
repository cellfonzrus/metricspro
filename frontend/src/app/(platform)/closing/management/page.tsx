'use client'
import { useState, useEffect, useMemo, useCallback } from 'react'
import Link from 'next/link'
import { apiCached, LOOKUP } from '@/lib/cache'
import StandardFilterBar from '@/components/StandardFilterBar'
import type { StandardFilterValue } from '@/lib/standard-filters'
import { marketsFromStores, type StoreOpt } from '@/lib/market-store-cascade'
import { api, fmt, localToday } from '@/lib/client'
import { usePosTerm } from '@/lib/report-labels'
import ScreenLink from '@/components/ScreenLink'
import { setupFailed } from '@/lib/setupNotice'

// Management Review (permission-gated: super-admin / company-wide scope / explicit /closing/management
// grant — DMs excluded). Shows the 3-try close-attempt log: every value a rep entered before a close
// was accepted, with the true POS variance the rep never saw. Reads GET /api/v1/closing/attempts.

export default function ClosingManagementPage() {
  const { pos } = usePosTerm()   // the tenant's POS name in copy (lib/report-labels.ts)
  const [mode, setMode] = useState<'date' | 'period'>('period')
  const [date, setDate] = useState(() => localToday())
  const [period, setPeriod] = useState(() => localToday().slice(0, 7))
  const [onlyReview, setOnlyReview] = useState(true)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  // THE STANDARD FILTERS (owner 2026-09-26: "i cannt pick one stroe or one market whicle doing cash
  // recon"). This dashboard had NO picker at all, and /closing/attempts took a singular `store=` with
  // no market — so there was nothing to pick with and nowhere to send it. Both ends now speak the
  // three standard filters, through the shared bar and the shared resolvers.
  const [flt, setFlt] = useState<StandardFilterValue>({ stores: [], markets: [], reps: [] })
  const [roster, setRoster] = useState<StoreOpt[]>([])
  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP)
      .then((r: any) => setRoster((Array.isArray(r) ? r : []).filter((x: any) => x.store_code)
        .map((x: any) => ({ id: x.store_code, label: x.store_address || x.store_code, market: x.market || null }))))
      .catch(() => setRoster([]))
  }, [])
  // Markets come from the roster itself — pick-don't-type, and the '(no market)' bucket appears only
  // when a store actually lacks one, so a store can never silently vanish behind a market pick.
  const marketOptions = useMemo(() => marketsFromStores(roster), [roster])
  const [relBusy, setRelBusy] = useState<Record<string, boolean>>({})
  const [relMsg, setRelMsg] = useState<Record<string, string>>({})

  function load() {
    setLoading(true); setErr('')
    const q = [
      mode === 'date' ? `date=${date}` : `period=${encodeURIComponent(period)}`,
      `only_review=${onlyReview}`,
      flt.markets.length && `markets=${encodeURIComponent(flt.markets.join(','))}`,
      flt.stores.length && `stores=${encodeURIComponent(flt.stores.join(','))}`,
      flt.reps.length && `employees=${encodeURIComponent(flt.reps.join(','))}`,
    ].filter(Boolean).join('&')
    api(`/api/v1/closing/attempts?${q}`)
      .then(setData)
      .catch(e => { setErr(e?.message || String(e)); setData(null) })
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [mode, date, period, onlyReview, flt])

  // MANAGEMENT OVERRIDE (mig 502, retail-ops-7 item 1): unlock a submitted closing row for ONE
  // corrected resubmit. Never creates a second row — the rep's next submit for that store/day UPDATES
  // this exact row. Every release is audited (released_by/at) on the row itself.
  async function toggleRelease(g: any, released: boolean) {
    const key = `${g.close_date}|${g.store_code}|${g.employee_name}`
    if (!g.row_id) { setRelMsg(m => ({ ...m, [key]: '❌ ' + setupFailed('No matching closing record for this row') })); return }
    setRelBusy(b => ({ ...b, [key]: true })); setRelMsg(m => ({ ...m, [key]: '' }))
    try {
      await api(`/api/v1/closing/row/${g.row_id}/release`, { method: 'POST', body: JSON.stringify({ released }) })
      setRelMsg(m => ({ ...m, [key]: released ? '✅ Released — the rep can resubmit once (corrects this row, never a duplicate).' : '✅ Re-locked.' }))
      load()
    } catch (e: any) { setRelMsg(m => ({ ...m, [key]: '❌ ' + (e?.message || e) })) }
    finally { setRelBusy(b => ({ ...b, [key]: false })) }
  }

  const groups: any[] = data?.groups || []
  const forbidden = /permission|403|restricted/i.test(err)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🛡️ Closing — Management Review</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 720 }}>
            Every value a rep entered before a close was accepted — including the tries that were
            <strong> over or short</strong>, the ones <strong>auto-accepted after 3 attempts</strong>, and the
            submits that were <strong>turned away before anything was stored</strong>. Reps
            only ever see “over” or “short”; here you see the amounts and the true system variance.
            <span style={{ color: 'var(--text3)' }}> DMs can’t see this page.</span>
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <button className="btn" style={{ borderRadius: 0, border: 'none', background: mode === 'date' ? 'var(--accent)' : 'transparent', color: mode === 'date' ? 'white' : 'var(--text2)' }} onClick={() => setMode('date')}>Day</button>
            <button className="btn" style={{ borderRadius: 0, border: 'none', background: mode === 'period' ? 'var(--accent)' : 'transparent', color: mode === 'period' ? 'white' : 'var(--text2)' }} onClick={() => setMode('period')}>Month</button>
          </div>
          {mode === 'date'
            ? <input className="select" type="date" value={date} onChange={e => setDate(e.target.value)} />
            : <input className="select" placeholder="2026-07" value={period} onChange={e => setPeriod(e.target.value)} style={{ width: 110 }} />}
          <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={onlyReview} onChange={e => setOnlyReview(e.target.checked)} /> Needs review only
          </label>
          <Link href="/closing/duplicates" className="btn btn-secondary" style={{ fontSize: 12 }}>🧾 Duplicate submissions</Link>
        </div>
      </div>

      <StandardFilterBar
        value={flt} onChange={setFlt}
        storeOptions={roster} marketOptions={marketOptions}
        repOptions={(data?.rep_options || []).map((n: string) => ({ id: n, label: n }))}
        storeLabel="Stores…" marketLabel="Markets…" repLabel="Employees…"
      />
      {data?.market_filter_skipped && (
        <div className="card" style={{ padding: '8px 12px', margin: '10px 0', fontSize: 12, background: '#fff8e6', border: '1px solid #f3d98b' }}>
          ⚠️ Your market filter could not be applied (store roster unavailable) — showing all markets rather than silently dropping stores.
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : forbidden ? (
        <div className="card" style={{ padding: 24, textAlign: 'center' }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>🔒 Restricted</div>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 6 }}>Management Review is limited to company-wide leadership. Ask an admin to grant your role the “Closing: Management Review” page at <ScreenLink to="roles_access">Administration → Roles</ScreenLink>.</div>
        </div>
      ) : err ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {err}</div>
      ) : groups.length === 0 ? (
        <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>
          Nothing to review for {mode === 'date' ? date : period}{onlyReview ? ' — no multi-try or auto-accepted closings.' : '.'}
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 10 }}>
          {groups.map((g: any, i: number) => {
            const key = `${g.close_date}|${g.store_code}|${g.employee_name}`
            const isOpen = open[key]
            return (
              <div key={i} className="card" style={{ padding: 0, overflow: 'hidden', border: g.auto_accepted ? '1px solid #f3b4b4' : undefined }}>
                <div role="button" tabIndex={0} onClick={() => setOpen(o => ({ ...o, [key]: !o[key] }))}
                  onKeyDown={e => { if (e.key === 'Enter') setOpen(o => ({ ...o, [key]: !o[key] })) }}
                  style={{ width: '100%', textAlign: 'left', background: g.auto_accepted ? '#fffafa' : 'var(--surface)', border: 'none', cursor: 'pointer', padding: '11px 14px', display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                  <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 13, fontWeight: 700 }}>{g.store_address || g.store_code}</span>
                    <span style={{ fontSize: 13 }}>{g.employee_name || '—'}</span>
                    <span style={{ fontSize: 12, color: 'var(--text3)' }}>{g.close_date}</span>
                    <span className="badge" style={{ fontSize: 11, background: g.attempts >= 3 ? '#fbe4e4' : 'var(--surface2)', color: g.attempts >= 3 ? '#b42318' : 'var(--text2)' }}>{g.attempts === 0 ? 'never counted' : `${g.attempts} attempt${g.attempts > 1 ? 's' : ''}`}</span>
                    {g.auto_accepted && <span className="badge" style={{ fontSize: 11, background: '#b42318', color: '#fff' }}>⚑ auto-accepted — review</span>}
                    {/* A REFUSED submit (index §29.11): the rep tried and NO closing was stored. Before
                        this existed the attempt left no trace at all, so a missing closing could not be
                        told apart from one never submitted. */}
                    {g.refusals > 0 && <span className="badge" style={{ fontSize: 11, background: '#fef3c7', color: '#92400e' }}>⚠ {g.refusals} submit{g.refusals > 1 ? 's' : ''} turned away — {REFUSAL_WORDS[g.last_refusal_code] || 'see tries'}</span>}
                    {g.released_at && <span className="badge" style={{ fontSize: 11, background: '#dbeafe', color: '#1d4ed8' }}>🔓 released by {g.released_by || 'management'}{g.correction_count ? ` · corrected ${g.correction_count}×` : ''}</span>}
                  </div>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    {/* MANAGEMENT OVERRIDE (mig 502): release this exact daily_closing row so the rep can
                        resubmit ONCE — a corrected UPDATE, never a second row. Audited on the row itself. */}
                    <button className="btn btn-secondary" style={{ fontSize: 11, padding: '3px 8px' }}
                      disabled={!g.row_id || relBusy[key]}
                      onClick={e => { e.stopPropagation(); toggleRelease(g, !g.released_at) }}
                      title={g.row_id ? undefined : setupFailed('No matching closing record for this row')}>
                      {relBusy[key] ? '⏳' : g.released_at ? '🔒 Re-lock' : '🔓 Release for correction'}
                    </button>
                    <span style={{ fontSize: 12, color: 'var(--text3)' }}>{isOpen ? '▲ hide tries' : '▼ show tries'}</span>
                  </div>
                </div>
                {relMsg[key] && <div style={{ fontSize: 11, padding: '0 14px 8px', color: relMsg[key].startsWith('❌') ? '#b91c1c' : 'var(--text2)' }}>{relMsg[key]}</div>}
                {isOpen && (
                  <div style={{ borderTop: '1px solid var(--border)', padding: '10px 14px' }}>
                    <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
                      System ({pos}) target: <b>cash {g.b2b?.cash != null ? fmt(g.b2b.cash) : '—'}</b> · <b>credit {g.b2b?.credit != null ? fmt(g.b2b.credit) : '—'}</b>
                    </div>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                      <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                        <th style={{ textAlign: 'left', padding: '5px 8px' }}>Try</th>
                        <th style={{ textAlign: 'right', padding: '5px 8px' }}>Cash entered</th>
                        <th style={{ textAlign: 'left', padding: '5px 8px' }}>Cash</th>
                        <th style={{ textAlign: 'right', padding: '5px 8px' }}>Credit entered</th>
                        <th style={{ textAlign: 'left', padding: '5px 8px' }}>Credit</th>
                        <th style={{ textAlign: 'left', padding: '5px 8px' }}>Outcome</th>
                      </tr></thead>
                      <tbody>
                        {g.tries.map((t: any, j: number) => (
                          <tr key={j} style={{ borderTop: '1px solid var(--border)' }}>
                            <td style={{ padding: '5px 8px', fontSize: 12 }}>#{t.attempt_no}</td>
                            <td style={{ padding: '5px 8px', fontSize: 12, textAlign: 'right' }}>{fmt(t.entered_cash)}</td>
                            <td style={{ padding: '5px 8px', fontSize: 12 }}><Dir d={t.cash_dir} /></td>
                            <td style={{ padding: '5px 8px', fontSize: 12, textAlign: 'right' }}>{fmt(t.entered_credit)}</td>
                            <td style={{ padding: '5px 8px', fontSize: 12 }}><Dir d={t.credit_dir} credit /></td>
                            <td style={{ padding: '5px 8px', fontSize: 12 }}>
                              {t.refused ? <span style={{ color: '#92400e', fontWeight: 600 }} title={t.refusal_detail || undefined}>turned away — {REFUSAL_WORDS[t.refusal_code] || t.refusal_code}</span>
                                : t.blocked ? <span style={{ color: '#b45309' }}>recount</span>
                                  : t.auto_accepted ? <span style={{ color: '#b42318', fontWeight: 600 }}>auto-accepted</span>
                                    : <span style={{ color: '#15803d' }}>accepted</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>
                      Tender split (final try): cash {fmt(lastTry(g).t_cash)} · credit {fmt(lastTry(g).t_credit)} · ext CC {fmt(lastTry(g).t_ext_cc)} · gift {fmt(lastTry(g).t_gift)} · store acct {fmt(lastTry(g).t_store_acct)} · zelle {fmt(lastTry(g).t_zelle)} · financing {fmt(lastTry(g).t_acima)}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// What each refusal CODE means in a manager's words. Presentation only — the authority is
// backend/app/modules/closing/submit_refusal.REFUSALS, and harness_closing_submit_refusal.py fails
// the build if a code exists there with no words here (index §29.11).
const REFUSAL_WORDS: Record<string, string> = {
  bad_close_date: 'the date could not be read',
  closer_not_permitted: 'submitted under another person’s name',
  closer_no_name: 'the submitter has no name on file',
  envelope_upload_failed: 'the envelope photo would not save',
  envelope_photo_required: 'cash declared with no envelope photo',
  duplicate_already_submitted: 'already submitted for the day',
  duplicate_multiple: 'more than one closing already exists',
  duplicate_race: 'submitted twice at once',
  expense_description_required: 'an expense had no description',
  expense_line_invalid: 'an expense line was incomplete',
  identity_missing: 'no store or no employee name',
}

function lastTry(g: any) { return g.tries?.[g.tries.length - 1] || {} }

function Dir({ d, credit }: { d?: string; credit?: boolean }) {
  if (!d || d === 'ok') return <span style={{ color: '#15803d' }}>ok</span>
  const bad = credit ? d === 'over' : d === 'short'
  return <span style={{ color: bad ? '#b91c1c' : '#b45309', fontWeight: 600 }}>{d}</span>
}
