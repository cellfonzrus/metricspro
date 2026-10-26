'use client'
/* THE REP'S OWN SHELF — "sell it within N days of receiving it", on the page a sales rep lands on.
 *
 * OWNER 2026-10-26: "invenotry aging in luxeliunk shodul say sell within 10 days of receiving the
 * phones in stock and it should be on every sales reps daSHBOARDS WHEN TEHY LOG IN".
 *
 * WHY HERE. `/commcalc/targets/my` is the configured home for the `sales_rep` role (mig 015), so this
 * is the first screen a rep sees after signing in — not a new page they would have to find. The card
 * sits ABOVE the targets and OUTSIDE the "no store assigned" early return, because the endpoint's
 * store span is evidence-based (where the person was scheduled or clocked in) and therefore answers
 * for a rep whose login carries no store pin: 9 of Luxelink's 32 active rep logins are in that state.
 *
 * EVERY NUMBER IS THE SERVER'S. The window, the age, the bucket and the counts all come from
 * GET /commcalc/my-shelf, which reads them from `inventory_stock_age` + `device_aging_policy`
 * (§COMMCALC-32 / §COMMCALC-26). Nothing is recomputed here — a client that subtracts its own dates
 * is how two surfaces end up one day apart.
 *
 * AND IT SAYS WHAT IT DOES NOT KNOW. A device with no deadline shows its age and the reason the
 * deadline is missing; a device with no age is counted apart; the receipt date is labelled as derived
 * from the POS age-in-stock, because no vendor receipt date exists in this platform (it is NULL on all
 * 10,525 rows). An empty card prints the server's reason rather than a zero.
 */
import { useEffect, useState } from 'react'
import { api, ORG_ID, fmt } from '@/lib/client'
import { LinkedText } from '@/components/ScreenLink'
import { useTableSort, SortableTh } from '@/components/SortableTh'

type Device = {
  imei?: string | null; device_model?: string | null; store?: string | null
  store_code?: string | null; unit_cost?: number | null
  received_date?: string | null; age_basis?: string | null
  days_on_shelf?: number | null; days_left?: number | null
  sell_within_days?: number | null; window_declared?: boolean
  no_window_reason?: string | null; aging_bucket?: string | null; overdue?: boolean
}
type Summary = {
  devices: number; overdue: number; due_today: number; undated: number
  no_window: number; value_overdue: number
}
type Shelf = {
  as_of: string; snapshot?: string | null
  devices: Device[]; stores: (Summary & { store_code: string })[]; summary: Summary
  windows_declared: number[]; span_restricted: boolean; devices_out_of_span: number
  excluded_non_devices: number; excluded_models: [string, number][]
  setup_hint: string[]; received_basis?: string | null
}

/** The server's reason codes, said in a sentence. The card never invents a deadline, so it has to be
 *  able to explain the absence of one — these are the only two reasons the server emits. */
const NO_WINDOW: Record<string, string> = {
  carrier_not_identified:
    'no carrier selling window is declared for this organization yet',
  carrier_windows_differ:
    'this model is not mapped to a carrier, and the carriers here have different selling windows',
}

function tone(d: Device) {
  if (d.days_left == null) return { bg: 'var(--c-bg-f8fafc)', fg: 'var(--text2)', label: '—' }
  if (d.days_left < 0) return { bg: 'var(--c-bg-fee2e2)', fg: 'var(--c-fg-991b1b)', label: `${-d.days_left}d over` }
  if (d.days_left === 0) return { bg: 'var(--c-bg-fffbeb)', fg: 'var(--c-fg-92400e)', label: 'due today' }
  if (d.days_left <= 3) return { bg: 'var(--c-bg-fffbeb)', fg: 'var(--c-fg-92400e)', label: `${d.days_left}d left` }
  return { bg: 'var(--c-bg-f0fdf4)', fg: 'var(--c-fg-166534)', label: `${d.days_left}d left` }
}

/* Click-a-header sorting reads cells through the ONE comparison home (`@/lib/table-sort`, owner
 * directive 2026-08-10) — no asc/desc state and no comparator of our own. Module scope so the memo
 * in `useTableSort` is stable across renders.
 *
 * `days_left` is returned RAW, including null: `compareValues` sorts empties last in both directions,
 * which is the behaviour this column needs — a device with no declared deadline is not a device with
 * zero days left, and flipping the direction must not fill the top of the table with blanks.
 */
function cell(d: Device, field: string): unknown {
  switch (field) {
    case 'days_left': return d.days_left
    case 'device_model': return d.device_model
    case 'imei': return d.imei
    case 'store': return d.store_code || d.store
    case 'received_date': return d.received_date
    case 'days_on_shelf': return d.days_on_shelf
    case 'unit_cost': return d.unit_cost
    default: return null
  }
}

export default function ShelfAging() {
  const [data, setData] = useState<Shelf | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState<string | null>(null)
  const [open, setOpen] = useState(true)
  const [showAll, setShowAll] = useState(false)

  // `loading` starts true rather than being set here: a setState in the effect BODY runs during the
  // commit and renders twice (react-hooks/set-state-in-effect). The card is loading from its first
  // frame anyway, so the initial value says so and only the resolution needs a write. The effect runs
  // once, and `live` keeps a resolution that arrives after unmount from writing to a dead component.
  useEffect(() => {
    let live = true
    api(`/commcalc/my-shelf?org_id=${ORG_ID}`)
      .then((r: Shelf) => { if (live) { setData(r); setFailed(null) } })
      // A FAILED READ IS SAID, not swallowed into an empty shelf. "You have nothing aging" and "we
      // could not ask" are different sentences, and only one of them is reassuring.
      .catch((e: unknown) => {
        if (!live) return
        // Narrowed rather than typed `any`: an Error's message, else whatever the thrown value
        // stringifies to, else a plain sentence — a thrown `undefined` must not read as "undefined".
        const msg = e instanceof Error ? e.message : String(e ?? '')
        setFailed(msg.trim() || 'could not be loaded')
      })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [])

  /* Sorted BEFORE the 12-row slice, never after: sorting a slice would only reorder the rows that
   * happen to be visible and quietly hide the worst device. The initial state is null, so the
   * server's own worst-first order is what a rep sees until they click a header. */
  const { sorted, sort, toggle } = useTableSort(data?.devices || [], cell)

  if (loading) {
    return <div className="card" style={{ padding: 16, marginBottom: 18, fontSize: 13, color: 'var(--text3)' }}>
      Loading your shelf…
    </div>
  }
  if (failed) {
    return <div className="card" style={{ padding: 16, marginBottom: 18, fontSize: 13, color: 'var(--text2)' }}>
      <strong>Inventory aging</strong> could not be loaded ({failed}). This does not mean your shelf is clear.
    </div>
  }
  if (!data) return null

  const s = data.summary
  const win = data.windows_declared.length === 1 ? data.windows_declared[0] : null
  const shown = showAll ? sorted : sorted.slice(0, 12)
  const nothing = data.devices.length === 0

  return (
    <div className="card" style={{ padding: 0, marginBottom: 18, overflow: 'hidden' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
          padding: '12px 16px', background: 'none', border: 'none', cursor: 'pointer',
          textAlign: 'left', borderBottom: open ? '1px solid var(--border)' : 'none',
        }}>
        <span style={{ fontWeight: 700, fontSize: 15 }}>
          {win ? `Sell within ${win} days of receiving` : 'Inventory aging'}
        </span>
        {s.overdue > 0 && (
          <span style={{ background: 'var(--c-bg-fee2e2)', color: 'var(--c-fg-991b1b)', borderRadius: 999,
                         padding: '2px 10px', fontSize: 12, fontWeight: 700 }}>
            {s.overdue} past the window
          </span>
        )}
        {s.due_today > 0 && (
          <span style={{ background: 'var(--c-bg-fffbeb)', color: 'var(--c-fg-92400e)', borderRadius: 999,
                         padding: '2px 10px', fontSize: 12, fontWeight: 700 }}>
            {s.due_today} due today
          </span>
        )}
        <span style={{ fontSize: 12.5, color: 'var(--text2)' }}>
          {s.devices} on your shelf
          {s.overdue > 0 ? ` · ${fmt(s.value_overdue)} of it already past the window` : ''}
        </span>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text3)' }}>{open ? '▴' : '▾'}</span>
      </button>

      {open && (
        <div style={{ padding: '12px 16px 16px' }}>
          {data.setup_hint.length > 0 && (
            <div style={{ background: 'var(--c-bg-fffbeb)', border: '1px solid var(--c-bg-fde68a)',
                          borderRadius: 8, padding: '8px 12px', marginBottom: 12, fontSize: 12.5,
                          color: 'var(--c-fg-92400e)' }}>
              {data.setup_hint.map((h, i) => <div key={i}>⚙️ <LinkedText text={h} /></div>)}
            </div>
          )}

          {nothing ? (
            <div style={{ fontSize: 13, color: 'var(--text2)' }}>
              {data.setup_hint.length === 0
                ? 'Nothing on your shelf is aging right now.'
                : 'No devices to show — see above.'}
            </div>
          ) : (
            <>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                  <thead>
                    <tr style={{ textAlign: 'left', color: 'var(--text3)' }}>
                      <SortableTh field="days_left" sort={sort} onSort={toggle} style={{ padding: '6px 8px' }}>Days left</SortableTh>
                      <SortableTh field="device_model" sort={sort} onSort={toggle} style={{ padding: '6px 8px' }}>Device</SortableTh>
                      <SortableTh field="imei" sort={sort} onSort={toggle} style={{ padding: '6px 8px' }}>IMEI</SortableTh>
                      <SortableTh field="store" sort={sort} onSort={toggle} style={{ padding: '6px 8px' }}>Store</SortableTh>
                      <SortableTh field="received_date" sort={sort} onSort={toggle} style={{ padding: '6px 8px', textAlign: 'right' }}>Received</SortableTh>
                      <SortableTh field="days_on_shelf" sort={sort} onSort={toggle} style={{ padding: '6px 8px', textAlign: 'right' }}>On shelf</SortableTh>
                      <SortableTh field="unit_cost" sort={sort} onSort={toggle} style={{ padding: '6px 8px', textAlign: 'right' }}>Cost</SortableTh>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((d, i) => {
                      const t = tone(d)
                      return (
                        <tr key={`${d.imei || i}`} style={{ borderTop: '1px solid var(--border)' }}>
                          <td style={{ padding: '6px 8px' }}>
                            <span style={{ background: t.bg, color: t.fg, borderRadius: 6,
                                           padding: '2px 8px', fontWeight: 700, whiteSpace: 'nowrap' }}>
                              {t.label}
                            </span>
                            {/* The missing deadline explains itself, in the row that lacks it. */}
                            {d.days_left == null && d.no_window_reason && (
                              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>
                                {NO_WINDOW[d.no_window_reason] || d.no_window_reason}
                              </div>
                            )}
                          </td>
                          <td style={{ padding: '6px 8px' }}>{d.device_model || '—'}</td>
                          <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono, monospace)', fontSize: 11.5 }}>
                            {d.imei || '—'}
                          </td>
                          <td style={{ padding: '6px 8px' }}>{d.store_code || d.store || '—'}</td>
                          <td style={{ padding: '6px 8px', textAlign: 'right' }}>{d.received_date || '—'}</td>
                          <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                            {d.days_on_shelf == null ? 'unknown' : `${d.days_on_shelf}d`}
                          </td>
                          <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                            {d.unit_cost == null ? '—' : fmt(d.unit_cost)}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              {data.devices.length > shown.length && (
                <button className="btn btn-secondary" style={{ fontSize: 12, marginTop: 10 }}
                        onClick={() => setShowAll(true)}>
                  Show all {data.devices.length}
                </button>
              )}
            </>
          )}

          {/* WHERE THESE NUMBERS COME FROM. Said on the card, because "received" here is derived from
              the POS age-in-stock and not from a receipt document — claiming otherwise would be the
              kind of quiet overstatement the house rules treat as a defect. */}
          <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--border)',
                        fontSize: 11.5, color: 'var(--text3)', lineHeight: 1.6 }}>
            <div>
              From the POS inventory-aging snapshot{data.snapshot ? ` of ${data.snapshot}` : ''}, as of {data.as_of}.
              {data.received_basis === 'age_at_snapshot'
                ? ' “Received” is taken from each unit’s age in stock on that snapshot — this platform holds no vendor receipt date.'
                : ''}
            </div>
            {(s.undated > 0 || s.no_window > 0) && (
              <div>
                {s.undated > 0 ? `${s.undated} unit${s.undated === 1 ? '' : 's'} carry no age and are not counted above. ` : ''}
                {s.no_window > 0 ? `${s.no_window} carry no selling window.` : ''}
              </div>
            )}
            {data.excluded_non_devices > 0 && (
              <div>
                {data.excluded_non_devices} SIM kit{data.excluded_non_devices === 1 ? '' : 's'} and accessor
                {data.excluded_non_devices === 1 ? 'y' : 'ies'} are on hand and not listed
                {data.excluded_models.length > 0
                  ? ` (${data.excluded_models.slice(0, 2).map(([m, n]) => `${m} ×${n}`).join(', ')})`
                  : ''}.
              </div>
            )}
            {data.devices_out_of_span > 0 && (
              <div>{data.devices_out_of_span} more units are on hand at stores outside your visibility.</div>
            )}
            {data.windows_declared.length > 1 && (
              <div>
                More than one selling window is declared here ({data.windows_declared.join(' and ')} days),
                so a unit shows a deadline only where its model is mapped to a carrier.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
