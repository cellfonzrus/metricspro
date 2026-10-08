'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, getActiveOrg } from '@/lib/client'

// MANAGER REPORT CARDS — owner directive 2026-10-08: "create a report card for the Dm based on all the
// items assigned to them per store and a check off by the system if those targets were met or not, the
// same report card will be made for the market manager … so they are also accountable."
//
// The backend decides everything (commcalc/manager_report_card.py + /targets/{period}/report-cards):
// which items are assigned, whether each was met, who owns which store, and the roll-up. This page
// renders. In particular it never turns an untargeted item into a 0 or a pass — `no_target` is its own
// state with its own colour, because "nobody set this target" is not "this manager failed".

// This page takes no filters of its own: the period is the path, and scope comes from the
// caller's keyset inside the summary this endpoint reads. So the only query is the active org.
const orgQuery = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
function thisMonth() { return new Date().toISOString().slice(0, 7) }

interface Tally { met: number; missed: number; no_target: number; checked: number; score_pct: number | null }
interface Item { key: string; label: string; unit: string; state: string; target: number | null; achieved: number | null; detail?: string | null; severity?: string }
interface StoreRow { store_code: string; store: string; market: string | null; district: string | null; items: Item[]; tally: Tally }
interface Card {
  kind: string; employee_id: string; name: string; email?: string | null; level?: string | null
  stores: number; dms?: number; tally: Tally; store_codes: string[]
  store_rows?: StoreRow[]
  dm_rows?: { employee_id: string; name: string; stores: number; tally: Tally }[]
  collective?: any
}
interface Resp {
  period?: string; today?: string
  dm_cards?: Card[]; manager_cards?: Card[]
  unassigned?: { store_code: string; store: string; market: string | null; reason: string; tally: Tally }[]
  coverage?: Record<string, number>
  coverage_note?: string | null
  items?: { key: string; label: string; unit: string; source: string }[]
  errors?: string[]
  error?: string
  setup_hint?: string | null
}

// THE THREE STATES, each with its own look. `no_target` is deliberately grey and not red: the
// platform's doctrine is that absence is never performance, and a card that painted every
// unconfigured item red would accuse managers over configuration.
const STATE: Record<string, { fg: string; bg: string; mark: string; word: string }> = {
  met: { fg: '#166534', bg: '#dcfce7', mark: '✓', word: 'Met' },
  missed: { fg: '#991b1b', bg: '#fee2e2', mark: '✗', word: 'Missed' },
  no_target: { fg: 'var(--text3)', bg: 'var(--surface2, #f3f4f6)', mark: '–', word: 'No target set' },
}

function fmtVal(v: number | null, unit: string): string {
  if (v === null || v === undefined) return '—'
  if (unit === 'money') return '$' + v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (unit === 'pct') return v.toFixed(1) + '%'
  return String(Math.round(v * 100) / 100)
}

function Score({ t }: { t: Tally }) {
  // No checked item → no score. Never 0%, never 100% (see `tally` in the backend module).
  return (
    <span style={{ fontSize: 13 }}>
      <b style={{ color: t.missed ? '#991b1b' : '#166534' }}>
        {t.score_pct === null ? '—' : t.score_pct + '%'}
      </b>
      <span style={{ color: 'var(--text3)', marginLeft: 6 }}>
        {t.met} met · {t.missed} missed{t.no_target ? ` · ${t.no_target} with no target` : ''}
      </span>
    </span>
  )
}

function Pill({ state }: { state: string }) {
  const s = STATE[state] || STATE.no_target
  return (
    <span title={s.word} style={{
      display: 'inline-block', minWidth: 20, textAlign: 'center', borderRadius: 6,
      background: s.bg, color: s.fg, fontSize: 12, fontWeight: 700, padding: '1px 6px',
    }}>{s.mark}</span>
  )
}

export default function ManagerReportCardsPage() {
  const [period, setPeriod] = useState(thisMonth())
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState<Record<string, boolean>>({})

  const load = useCallback(() => {
    setLoading(true)
    api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/report-cards${orgQuery()}`)
      .then(setData)
      .catch(e => setData({ error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [period])
  useEffect(() => { load() }, [load])

  const items = data?.items || []
  const dmCards = data?.dm_cards || []
  const mgrCards = data?.manager_cards || []
  const unassigned = data?.unassigned || []

  return (
    <div style={{ padding: '18px 22px', maxWidth: 1500 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
        <h1 style={{ fontSize: 20, fontWeight: 800, margin: 0 }}>Manager Report Cards</h1>
        <input type="month" value={period} onChange={e => setPeriod(e.target.value)} style={sel} />
        {data?.today && <span style={{ fontSize: 12, color: 'var(--text3)' }}>as at {data.today}</span>}
      </div>
      <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 14, maxWidth: 900 }}>
        Every item assigned to a district manager for each of their stores, with the system&apos;s own
        check-off of whether it was met. The card below it rolls the same check-off up to the manager
        above, a row per DM, so accountability does not stop at the district.
      </div>

      {/* The org tree is what puts a store on a card, so when it cannot answer, that is said FIRST —
          above the cards, not in a footnote. A card that looks complete while most of the org is
          unassigned is the misleading version. */}
      {!loading && data?.coverage_note &&
        <div className="card" style={{ padding: '12px 14px', marginBottom: 14, background: '#fef3c7', color: '#92400e', fontSize: 13 }}>
          ⚠︎ {data.coverage_note}
        </div>}
      {!loading && (data?.errors || []).length > 0 &&
        <div className="card" style={{ padding: '12px 14px', marginBottom: 14, background: '#fee2e2', color: '#991b1b', fontSize: 13 }}>
          {(data?.errors || []).map((e, i) => <div key={i}>⚠︎ {e}</div>)}
        </div>}
      {!loading && data?.setup_hint &&
        <div className="card" style={{ padding: '12px 14px', marginBottom: 14, fontSize: 13, color: 'var(--text2)' }}>
          {data.setup_hint}
        </div>}
      {data?.error &&
        <div className="card" style={{ padding: 18, color: '#991b1b' }}>{data.error}</div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          {/* ── DISTRICT MANAGERS ─────────────────────────────────────────────────────────────── */}
          <h2 style={{ fontSize: 15, fontWeight: 800, margin: '4px 0 10px' }}>District managers</h2>
          {dmCards.length === 0 ? (
            <div className="card" style={{ padding: 18, fontSize: 13, color: 'var(--text3)' }}>
              No district manager in the org tree owns a store this month, so there is no card to show.
            </div>
          ) : dmCards.map(c => {
            const isOpen = !!open[c.employee_id]
            return (
              <div key={c.employee_id} className="card" style={{ padding: 0, marginBottom: 12, overflow: 'hidden' }}>
                <button onClick={() => setOpen(o => ({ ...o, [c.employee_id]: !o[c.employee_id] }))}
                        style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px', background: 'none', border: 0, cursor: 'pointer', textAlign: 'left' }}>
                  <span style={{ color: 'var(--text3)', fontSize: 12 }}>{isOpen ? '▾' : '▸'}</span>
                  <b style={{ fontSize: 14 }}>{c.name}</b>
                  <span style={{ fontSize: 12, color: 'var(--text3)' }}>
                    {c.stores} store{c.stores === 1 ? '' : 's'}{c.level ? ` · ${c.level}` : ''}
                  </span>
                  <span style={{ marginLeft: 'auto' }}><Score t={c.tally} /></span>
                </button>
                {isOpen && (
                  <div style={{ overflowX: 'auto', borderTop: '1px solid var(--border)' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                      <thead>
                        <tr style={{ background: 'var(--surface2, #f9fafb)' }}>
                          <th style={{ textAlign: 'left', padding: '7px 10px' }}>Store</th>
                          {items.map(i => (
                            <th key={i.key} title={i.source} style={{ textAlign: 'center', padding: '7px 10px', whiteSpace: 'nowrap' }}>{i.label}</th>
                          ))}
                          <th style={{ textAlign: 'right', padding: '7px 10px' }}>Score</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(c.store_rows || []).map(r => (
                          <tr key={r.store_code} style={{ borderTop: '1px solid var(--border)' }}>
                            <td style={{ padding: '7px 10px' }}>
                              <b>{r.store_code}</b>
                              <span style={{ color: 'var(--text3)', marginLeft: 6 }}>{r.market || ''}</span>
                            </td>
                            {r.items.map(it => (
                              <td key={it.key} title={it.detail || undefined} style={{ textAlign: 'center', padding: '7px 10px', whiteSpace: 'nowrap' }}>
                                <Pill state={it.state} />
                                <span style={{ marginLeft: 6, color: 'var(--text3)', fontSize: 12 }}>
                                  {it.state === 'no_target' ? '' : `${fmtVal(it.achieved, it.unit)} / ${fmtVal(it.target, it.unit)}`}
                                </span>
                              </td>
                            ))}
                            <td style={{ textAlign: 'right', padding: '7px 10px' }}><Score t={r.tally} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )
          })}

          {/* ── THE LEVEL ABOVE ──────────────────────────────────────────────────────────────── */}
          {mgrCards.length > 0 && <>
            <h2 style={{ fontSize: 15, fontWeight: 800, margin: '20px 0 10px' }}>Market managers and above</h2>
            {mgrCards.map(c => (
              <div key={c.employee_id} className="card" style={{ padding: '12px 14px', marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                  <b style={{ fontSize: 14 }}>{c.name}</b>
                  <span style={{ fontSize: 12, color: 'var(--text3)' }}>
                    {c.dms} district manager{c.dms === 1 ? '' : 's'} · {c.stores} store{c.stores === 1 ? '' : 's'}
                    {c.level ? ` · ${c.level}` : ''}
                  </span>
                  <span style={{ marginLeft: 'auto' }}><Score t={c.tally} /></span>
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginTop: 10 }}>
                  <thead>
                    <tr style={{ background: 'var(--surface2, #f9fafb)' }}>
                      <th style={{ textAlign: 'left', padding: '6px 10px' }}>District manager</th>
                      <th style={{ textAlign: 'right', padding: '6px 10px' }}>Stores</th>
                      <th style={{ textAlign: 'right', padding: '6px 10px' }}>Score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(c.dm_rows || []).map(d => (
                      <tr key={d.employee_id} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '6px 10px' }}>{d.name}</td>
                        <td style={{ textAlign: 'right', padding: '6px 10px' }}>{d.stores}</td>
                        <td style={{ textAlign: 'right', padding: '6px 10px' }}><Score t={d.tally} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </>}

          {/* ── STORES ON NOBODY'S CARD ─────────────────────────────────────────────────────────
              Shown, never dropped: a store missing from every card reads as a store with nothing to
              answer for. Its own check-off is kept so the work is visible even while the owner is
              unknown. */}
          {unassigned.length > 0 && (
            <div className="card" style={{ padding: '12px 14px', marginTop: 20 }}>
              <div style={{ fontSize: 14, fontWeight: 800, marginBottom: 2 }}>
                On nobody&apos;s card ({unassigned.length})
              </div>
              <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>
                These stores have items and results, but the org tree does not say who owns them.
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2, #f9fafb)' }}>
                    <th style={{ textAlign: 'left', padding: '6px 10px' }}>Store</th>
                    <th style={{ textAlign: 'left', padding: '6px 10px' }}>Market</th>
                    <th style={{ textAlign: 'left', padding: '6px 10px' }}>Why</th>
                    <th style={{ textAlign: 'right', padding: '6px 10px' }}>Score</th>
                  </tr>
                </thead>
                <tbody>
                  {unassigned.map(u => (
                    <tr key={u.store_code} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '6px 10px' }}><b>{u.store_code}</b></td>
                      <td style={{ padding: '6px 10px' }}>{u.market || '—'}</td>
                      <td style={{ padding: '6px 10px', color: 'var(--text3)' }}>{u.reason}</td>
                      <td style={{ textAlign: 'right', padding: '6px 10px' }}><Score t={u.tally} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* What the check-off is measured against, named once rather than guessed at per column. */}
          {items.length > 0 && (
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 16 }}>
              <b>What is checked:</b>{' '}
              {items.map((i, n) => <span key={i.key}>{n ? ' · ' : ''}{i.label}</span>)}
              <div style={{ marginTop: 4 }}>
                A dash means no target was set for that item — it is not a miss, and it is left out of
                the score rather than counted against anyone.
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
