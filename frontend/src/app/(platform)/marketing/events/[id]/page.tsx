'use client'
// The event workspace — one screen for one event: people and backups, logistics, the checklist,
// outside parties, creative links, giveaways, goals vs results, GPS check-in and the debrief.
//
// Everything is loaded in ONE call (`GET /marketing/events/{id}`), which also returns the staffing,
// transport, checklist and giveaway ANALYSES computed server-side by the same pure functions the
// attention providers use. That is deliberate: computing "who has no backup" a second time in the
// browser is how a screen ends up disagreeing with the notification that sent someone to it.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { api } from '@/lib/client'
import {
  panel, input, label, btn, btnPrimary, btnDanger, th, cell,
  STATUS_COLOR, SEVERITY_COLOR, CONFIRM_COLOR, DECISION_COLOR, DECISION_LABEL,
  optionLabel, fmtMoney, fmtMetric, fmtDateTime, fmtDate, fmtTime, relTime,
  toLocalInput, fromLocalInput, readPositionOnce,
  type EventWorkspace, type ActualsResponse, type EventStaff, type MarketingOption,
} from '@/lib/marketing'

type Tab = 'overview' | 'people' | 'logistics' | 'checklist' | 'parties' | 'creative' | 'giveaways' | 'results' | 'debrief'
const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'people', label: 'People & backups' },
  { key: 'logistics', label: 'Getting there' },
  { key: 'checklist', label: 'Checklist' },
  { key: 'parties', label: 'Outside parties' },
  { key: 'creative', label: 'Creative links' },
  { key: 'giveaways', label: 'Giveaways' },
  { key: 'results', label: 'Goals & results' },
  { key: 'debrief', label: 'Debrief' },
]

export default function EventWorkspace() {
  const { id } = useParams<{ id: string }>()
  const [w, setW] = useState<EventWorkspace | null>(null)
  const [act, setAct] = useState<ActualsResponse | null>(null)
  const [tab, setTab] = useState<Tab>('overview')
  const [msg, setMsg] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setMsg('')
    try { setW(await api(`/api/v1/marketing/events/${id}`)) }
    catch (e: any) { setMsg(e?.message || String(e)) }
  }, [id])
  useEffect(() => { if (id) load() }, [id, load])

  useEffect(() => {
    if (tab !== 'results' || !id) return
    api(`/api/v1/marketing/events/${id}/actuals`).then(setAct).catch(e => setMsg(e?.message || String(e)))
  }, [tab, id])

  async function call(path: string, opts: RequestInit) {
    setBusy(true); setMsg(''); setNote('')
    try { const r = await api(path, opts); await load(); return r }
    catch (e: any) { setMsg(e?.message || String(e)); return null }
    finally { setBusy(false) }
  }

  const addChild = (c: string, data: Record<string, unknown>) =>
    call(`/api/v1/marketing/events/${id}/${c}`, { method: 'POST', body: JSON.stringify({ data }) })
  const patchChild = (c: string, rowId: string, data: Record<string, unknown>) =>
    call(`/api/v1/marketing/events/${id}/${c}/${rowId}`, { method: 'PATCH', body: JSON.stringify({ data }) })
  const delChild = (c: string, rowId: string) =>
    call(`/api/v1/marketing/events/${id}/${c}/${rowId}`, { method: 'DELETE' })
  const patchEvent = (data: Record<string, unknown>) =>
    call(`/api/v1/marketing/events/${id}`, { method: 'PATCH', body: JSON.stringify(data) })

  if (!w) {
    return <div style={{ padding: 20 }}>{msg ? <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626' }}>{msg}</div> : 'Loading…'}</div>
  }

  const e = w.event
  const opts = (k: string): MarketingOption[] => (w.options?.[k] || [])
  const editable = ['draft', 'approved', 'live'].includes(e.status)
  const staffById = Object.fromEntries(w.staff.map(s => [s.id, s]))

  // ── check-in (the only place the browser is asked for a position) ──────────────────────────────
  async function doCheckin() {
    setBusy(true); setMsg(''); setNote('')
    const pos = await readPositionOnce()
    try {
      const r = await api(`/api/v1/marketing/events/${id}/checkin`, {
        method: 'POST',
        body: JSON.stringify({ check_in_lat: pos.lat, check_in_lng: pos.lng, check_in_accuracy: pos.accuracy }),
      })
      setNote(`${r.verdict?.note || 'Checked in.'} ${r.retention_note || ''}`)
      await load()
    } catch (err: any) { setMsg(err?.message || String(err)) }
    setBusy(false)
  }

  const Picker = ({ listKey, value, onChange, width = 160 }: {
    listKey: string; value: string; onChange: (v: string) => void; width?: number
  }) => (
    <select style={{ ...input, width }} value={value || ''} onChange={ev => onChange(ev.target.value)}>
      <option value="">—</option>
      {opts(listKey).map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
    </select>
  )

  return (
    <div style={{ padding: 20, maxWidth: 1400 }}>
      {/* ── header ─────────────────────────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
        <Link href="/marketing" style={{ fontSize: 13, color: 'var(--text2)' }}>← Marketing</Link>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>{e.title}</h1>
        <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 20, color: '#fff',
                       background: STATUS_COLOR[e.status] || '#6b7280', textTransform: 'capitalize' }}>{e.status}</span>
        <div style={{ flex: 1 }} />
        {w.allowed_transitions.map(t => (
          <button key={t} style={t === 'cancelled' ? btnDanger : btn} disabled={busy}
                  onClick={() => call(`/api/v1/marketing/events/${id}/status`,
                                      { method: 'POST', body: JSON.stringify({ status: t }) })}>
            {t === 'live' ? 'Go live' : t === 'closed' ? 'Close event' : `Mark ${t}`}
          </button>
        ))}
        <button style={btnPrimary} disabled={busy} onClick={doCheckin}>📍 Check in</button>
      </div>
      <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 12 }}>
        {e.theme_label || optionLabel(opts('theme'), e.theme_key)} · {e.venue_name || 'No venue'}
        {e.city ? `, ${e.city}` : ''} · {fmtDate(e.event_start)} {fmtTime(e.event_start)}–{fmtTime(e.event_end)}
        {' · '}<strong>Staff by {fmtTime(e.staff_call_at)}</strong> ({relTime(e.staff_call_at)})
      </div>

      {msg && <div style={{ ...panel, marginBottom: 12, borderColor: '#dc2626', color: '#dc2626' }}>{msg}</div>}
      {note && <div style={{ ...panel, marginBottom: 12, borderColor: '#16a34a' }}>{note}</div>}

      {/* Approval only appears when it applies — an off-by-default workflow should be invisible. */}
      {e.approval_state !== 'not_required' && (
        <div style={{ ...panel, marginBottom: 12, borderColor: e.approval_state === 'approved' ? '#16a34a' : '#f39c12' }}>
          <strong style={{ textTransform: 'capitalize' }}>Approval: {e.approval_state}</strong>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 3 }}>{e.approval_reason}</div>
          {e.approved_by && <div style={{ fontSize: 12, color: 'var(--text2)' }}>by {e.approved_by} · {fmtDateTime(e.approved_at)}</div>}
          {e.approval_state === 'pending' && (
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button style={btnPrimary} disabled={busy} onClick={() => call(`/api/v1/marketing/events/${id}/approval`,
                { method: 'POST', body: JSON.stringify({ action: 'approve' }) })}>Approve</button>
              <button style={btnDanger} disabled={busy} onClick={() => call(`/api/v1/marketing/events/${id}/approval`,
                { method: 'POST', body: JSON.stringify({ action: 'reject' }) })}>Reject</button>
            </div>
          )}
        </div>
      )}

      {!!w.readiness.issues.length && (
        <div style={{ ...panel, marginBottom: 12, borderColor: '#f39c12' }}>
          <strong>This event needs attention</strong>
          {w.readiness.issues.map((i, n) => (
            <div key={n} style={{ fontSize: 13, marginTop: 4, color: SEVERITY_COLOR[i.severity] }}>
              {i.severity === 'error' ? '●' : '▲'} {i.detail}
            </div>
          ))}
        </div>
      )}

      {/* ── tabs ───────────────────────────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 14, borderBottom: '1px solid var(--border)' }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)} style={{
            ...btn, border: 'none', borderRadius: 0, background: 'transparent',
            borderBottom: `2px solid ${tab === t.key ? '#2563eb' : 'transparent'}`,
            fontWeight: tab === t.key ? 700 : 400,
          }}>{t.label}</button>
        ))}
      </div>

      {tab === 'overview' && <Overview w={w} />}

      {tab === 'people' && (
        <People w={w} editable={editable} opts={opts} Picker={Picker} busy={busy}
                addChild={addChild} patchChild={patchChild} delChild={delChild} staffById={staffById} />
      )}

      {tab === 'logistics' && (
        <Logistics w={w} editable={editable} opts={opts} Picker={Picker} busy={busy}
                   patchChild={patchChild} staffById={staffById} />
      )}

      {tab === 'checklist' && (
        <Checklist w={w} id={id} editable={editable} busy={busy}
                   addChild={addChild} patchChild={patchChild} delChild={delChild} call={call} />
      )}

      {tab === 'parties' && (
        <Parties w={w} editable={editable} Picker={Picker} busy={busy}
                 addChild={addChild} patchChild={patchChild} delChild={delChild} opts={opts} />
      )}

      {tab === 'creative' && (
        <Creative w={w} editable={editable} Picker={Picker} busy={busy}
                  addChild={addChild} delChild={delChild} opts={opts} />
      )}

      {tab === 'giveaways' && (
        <Giveaways w={w} editable={editable} Picker={Picker} busy={busy}
                   addChild={addChild} patchChild={patchChild} delChild={delChild} opts={opts} />
      )}

      {tab === 'results' && <Results w={w} act={act} opts={opts} busy={busy} addChild={addChild} delChild={delChild} />}

      {tab === 'debrief' && <Debrief w={w} busy={busy} patchEvent={patchEvent} />}
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════════════════════
function Section({ title, children, sub }: { title: string; children: React.ReactNode; sub?: string }) {
  return (
    <section style={{ ...panel, marginBottom: 14 }}>
      <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 2px' }}>{title}</h2>
      {sub && <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>{sub}</div>}
      {!sub && <div style={{ height: 8 }} />}
      {children}
    </section>
  )
}

function Overview({ w }: { w: EventWorkspace }) {
  const e = w.event
  const c = w.staffing.counts
  const r = w.checklist_readiness
  const Row = ({ k, v }: { k: string; v: React.ReactNode }) => (
    <div style={{ display: 'flex', gap: 10, padding: '4px 0', borderBottom: '1px solid var(--border)' }}>
      <div style={{ width: 190, color: 'var(--text2)', fontSize: 12 }}>{k}</div>
      <div style={{ fontSize: 13 }}>{v}</div>
    </div>
  )
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(320px,1fr))', gap: 14 }}>
      <Section title="Where">
        <Row k="Venue" v={e.venue_name || '—'} />
        <Row k="Type" v={e.venue_type_label || '—'} />
        <Row k="Address" v={[e.address, e.city, e.state, e.postal_code].filter(Boolean).join(', ') || '—'} />
        <Row k="Map pin" v={e.geo_lat != null && e.geo_lng != null
          ? `${e.geo_lat}, ${e.geo_lng} (check-in within ${e.checkin_radius_m || w.config.default_checkin_radius_m} m)`
          : 'Not set — check-ins are recorded but cannot be verified'} />
        <Row k="Setup notes" v={e.setup_notes || '—'} />
        <Row k="Parking" v={e.parking_notes || '—'} />
      </Section>
      <Section title="When">
        <Row k="Setup starts" v={fmtDateTime(e.setup_start_at)} />
        <Row k="Staff have to be there" v={<strong>{fmtDateTime(e.staff_call_at)}</strong>} />
        <Row k="Event runs" v={`${fmtDateTime(e.event_start)} – ${fmtTime(e.event_end)}`} />
        <Row k="Teardown done by" v={fmtDateTime(e.teardown_end_at)} />
      </Section>
      <Section title="Readiness">
        <Row k="Staff planned" v={`${c.planned} (${c.confirmed} confirmed, ${c.unconfirmed} unconfirmed, ${c.declined} not coming)`} />
        <Row k="With a named backup" v={`${c.with_backup} of ${c.planned}`} />
        <Row k="Slots with nobody" v={c.uncovered ? <span style={{ color: '#dc2626' }}>{c.uncovered}</span> : '0'} />
        <Row k="Arrived (checked in)" v={c.arrived} />
        <Row k="Checklist packed" v={r.total ? `${r.packed} of ${r.total} (${r.pct_packed}%)` : 'No checklist yet'} />
        <Row k="Outside parties" v={`${w.vendors.length} (${w.vendors.filter(v => v.confirm_state === 'confirmed').length} confirmed)`} />
        <Row k="Planned spend" v={fmtMoney(e.planned_spend)} />
      </Section>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════════════════════════
function People({ w, editable, opts, Picker, busy, addChild, patchChild, delChild, staffById }: any) {
  const [n, setN] = useState({ employee_name: '', employee_id: '', role_key: '', is_backup: false, backup_for_staff_id: '' })
  const primaries: EventStaff[] = w.staff.filter((s: EventStaff) => !s.is_backup)
  return (
    <>
      <Section title="Who is working this event"
               sub="Each planned person can have a named backup. A backup only counts as cover if they have not declined themselves.">
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr>
              <th style={th}>Person</th><th style={th}>Role</th><th style={th}>Confirmed</th>
              <th style={th}>Call time</th><th style={th}>Backup</th><th style={th}>Arrived</th><th style={th} />
            </tr></thead>
            <tbody>
              {w.staffing.roster.map((s: any) => (
                <tr key={s.id}>
                  <td style={cell}>
                    <strong>{s.employee_name || s.employee_id || '—'}</strong>
                    {!s.is_covered && ['declined', 'no_show'].includes(s.confirm_state) &&
                      <div style={{ fontSize: 11, color: '#dc2626' }}>Not coming and no backup</div>}
                  </td>
                  <td style={cell}>{optionLabel(opts('event_role'), s.role_key)}</td>
                  <td style={cell}>
                    <select style={{ ...input, width: 130 }} value={s.confirm_state} disabled={!editable || busy}
                            onChange={ev => patchChild('staff', s.id, { confirm_state: ev.target.value })}>
                      {['planned', 'confirmed', 'declined', 'no_show'].map(v =>
                        <option key={v} value={v}>{v.replace('_', ' ')}</option>)}
                    </select>
                  </td>
                  <td style={cell}>
                    <input style={{ ...input, width: 190 }} type="datetime-local" disabled={!editable || busy}
                           value={toLocalInput(s.call_time_override || s.resolved_call_time)}
                           onChange={ev => patchChild('staff', s.id, { call_time_override: fromLocalInput(ev.target.value) })} />
                    <div style={{ fontSize: 10, color: 'var(--text2)' }}>
                      {s.call_time_source === 'personal' ? 'their own call time'
                        : s.call_time_source === 'event' ? 'the event call time'
                        : s.call_time_source === 'event_start_fallback' ? 'no call time set — showing event start'
                        : 'no time set'}
                    </div>
                  </td>
                  <td style={cell}>
                    {s.backup
                      ? <span style={{ color: CONFIRM_COLOR[s.backup.confirm_state] }}>
                          {s.backup.employee_name} ({s.backup.confirm_state})
                        </span>
                      : <span style={{ color: '#f39c12' }}>none</span>}
                  </td>
                  <td style={cell}>{s.arrived ? '✓' : '—'}</td>
                  <td style={cell}>
                    {editable && <button style={btnDanger} disabled={busy}
                                         onClick={() => delChild('staff', s.id)}>Remove</button>}
                  </td>
                </tr>
              ))}
              {!w.staffing.roster.length && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={7}>Nobody planned yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </Section>

      {!!w.staffing.unassigned_backups.length && (
        <Section title="Backups pointing at nobody"
                 sub="These were named as a backup for someone who is no longer on the event. Re-point or remove them — they are not covering anything today.">
          {w.staffing.unassigned_backups.map((b: EventStaff) => (
            <div key={b.id} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '4px 0' }}>
              <span style={{ flex: 1 }}>{b.employee_name || b.employee_id}</span>
              <select style={{ ...input, width: 200 }} defaultValue="" disabled={busy}
                      onChange={ev => patchChild('staff', b.id, { backup_for_staff_id: ev.target.value || null })}>
                <option value="">back up for…</option>
                {primaries.map(p => <option key={p.id} value={p.id}>{p.employee_name || p.employee_id}</option>)}
              </select>
              <button style={btnDanger} disabled={busy} onClick={() => delChild('staff', b.id)}>Remove</button>
            </div>
          ))}
        </Section>
      )}

      {editable && (
        <Section title="Add a person">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
            <div><span style={label}>Name</span>
              <input style={{ ...input, width: 180 }} value={n.employee_name}
                     onChange={ev => setN({ ...n, employee_name: ev.target.value })} /></div>
            <div><span style={label}>Employee ID</span>
              <input style={{ ...input, width: 130 }} value={n.employee_id}
                     onChange={ev => setN({ ...n, employee_id: ev.target.value })} /></div>
            <div><span style={label}>Role</span>
              <Picker listKey="event_role" value={n.role_key} onChange={(v: string) => setN({ ...n, role_key: v })} /></div>
            <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center', paddingBottom: 8 }}>
              <input type="checkbox" checked={n.is_backup}
                     onChange={ev => setN({ ...n, is_backup: ev.target.checked })} /> is a backup
            </label>
            {n.is_backup && (
              <div><span style={label}>Backup for</span>
                <select style={{ ...input, width: 180 }} value={n.backup_for_staff_id}
                        onChange={ev => setN({ ...n, backup_for_staff_id: ev.target.value })}>
                  <option value="">—</option>
                  {primaries.map(p => <option key={p.id} value={p.id}>{p.employee_name || p.employee_id}</option>)}
                </select></div>
            )}
            <button style={btnPrimary} disabled={busy || !n.employee_name.trim()}
                    onClick={async () => {
                      await addChild('staff', { ...n, backup_for_staff_id: n.backup_for_staff_id || null })
                      setN({ employee_name: '', employee_id: '', role_key: '', is_backup: false, backup_for_staff_id: '' })
                    }}>Add</button>
          </div>
        </Section>
      )}

      <Section title="Check-ins" sub="One location reading per person, taken when they pressed check-in. Nothing is recorded between check-ins.">
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>
            <th style={th}>Person</th><th style={th}>Checked in</th><th style={th}>Verified</th>
            <th style={th}>Distance</th><th style={th}>Checked out</th><th style={th}>Kept until</th>
          </tr></thead>
          <tbody>
            {w.checkins.map((c: any) => (
              <tr key={c.id}>
                <td style={cell}>{c.employee_name || c.employee_id}</td>
                <td style={cell}>{fmtDateTime(c.checked_in_at)}</td>
                <td style={{ ...cell, color: DECISION_COLOR[c.decision] || 'var(--text2)' }}>
                  {DECISION_LABEL[c.decision] || c.decision || '—'}
                </td>
                <td style={cell}>{c.distance_m != null ? `${c.distance_m} m` : '—'}</td>
                <td style={cell}>{c.checked_out_at ? fmtDateTime(c.checked_out_at) : '—'}</td>
                <td style={cell}>{c.purge_after_date || '—'}</td>
              </tr>
            ))}
            {!w.checkins.length && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={6}>Nobody has checked in yet.</td></tr>}
          </tbody>
        </table>
      </Section>
    </>
  )
}

// ══════════════════════════════════════════════════════════════════════════════════════════════
function Logistics({ w, editable, opts, Picker, busy, patchChild, staffById }: any) {
  return (
    <>
      {!!w.transport.problems.length && (
        <div style={{ ...panel, marginBottom: 14, borderColor: '#f39c12' }}>
          <strong>Rides that will not work</strong>
          {w.transport.problems.map((p: any, i: number) => (
            <div key={i} style={{ fontSize: 13, marginTop: 4, color: '#f39c12' }}>▲ {p.detail}</div>
          ))}
        </div>
      )}
      <Section title="How everyone is getting there"
               sub="Set a transport mode per person, and a driver for anyone being picked up. Whether a mode needs a pickup is a property of the option itself, so a mode you add later behaves correctly straight away.">
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr>
              <th style={th}>Person</th><th style={th}>Transport</th><th style={th}>Picked up by</th>
              <th style={th}>Pickup time</th><th style={th}>Pickup place</th>
            </tr></thead>
            <tbody>
              {w.staff.map((s: EventStaff) => (
                <tr key={s.id}>
                  <td style={cell}>{s.employee_name || s.employee_id}{s.is_backup && <span style={{ fontSize: 11, color: 'var(--text2)' }}> (backup)</span>}</td>
                  <td style={cell}>
                    <select style={{ ...input, width: 190 }} value={s.transport_mode_key || ''} disabled={!editable || busy}
                            onChange={ev => patchChild('staff', s.id, { transport_mode_key: ev.target.value || null })}>
                      <option value="">—</option>
                      {opts('transport_mode').map((o: MarketingOption) => <option key={o.key} value={o.key}>{o.label}</option>)}
                    </select>
                  </td>
                  <td style={cell}>
                    <select style={{ ...input, width: 180 }} value={s.pickup_by_staff_id || ''} disabled={!editable || busy}
                            onChange={ev => patchChild('staff', s.id, { pickup_by_staff_id: ev.target.value || null })}>
                      <option value="">nobody</option>
                      {w.staff.filter((o: EventStaff) => o.id !== s.id).map((o: EventStaff) =>
                        <option key={o.id} value={o.id}>{o.employee_name || o.employee_id}</option>)}
                    </select>
                  </td>
                  <td style={cell}>
                    <input style={{ ...input, width: 190 }} type="datetime-local" disabled={!editable || busy}
                           value={toLocalInput(s.pickup_at)}
                           onChange={ev => patchChild('staff', s.id, { pickup_at: fromLocalInput(ev.target.value) })} />
                  </td>
                  <td style={cell}>
                    <input style={{ ...input, width: 180 }} defaultValue={s.pickup_location || ''} disabled={!editable || busy}
                           onBlur={ev => patchChild('staff', s.id, { pickup_location: ev.target.value })} />
                  </td>
                </tr>
              ))}
              {!w.staff.length && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={5}>Add people first.</td></tr>}
            </tbody>
          </table>
        </div>
      </Section>
      <Section title="Who is driving whom">
        {Object.keys(w.transport.rides).length === 0
          ? <div style={{ fontSize: 13, color: 'var(--text2)' }}>Nobody is picking anybody up.</div>
          : Object.entries(w.transport.rides).map(([driverId, riders]: any) => (
              <div key={driverId} style={{ padding: '4px 0', fontSize: 13 }}>
                <strong>{staffById[driverId]?.employee_name || 'Someone'}</strong> drives{' '}
                {riders.map((r: EventStaff) => r.employee_name || r.employee_id).join(', ')}
              </div>
            ))}
      </Section>
    </>
  )
}

// ══════════════════════════════════════════════════════════════════════════════════════════════
function Checklist({ w, id, editable, busy, addChild, patchChild, delChild, call }: any) {
  const [n, setN] = useState({ label: '', category: '', qty: '', is_returnable: true })
  const [templates, setTemplates] = useState<any[]>([])
  const [tpl, setTpl] = useState('')
  const r = w.checklist_readiness
  useEffect(() => { api('/api/v1/marketing/checklist-templates').then(x => setTemplates(x.templates || [])).catch(() => {}) }, [])
  return (
    <>
      <Section title={`Checklist — ${r.packed} of ${r.total} packed`}
               sub={r.outstanding_returns ? `${r.outstanding_returns} item(s) went out and have not come back.` : 'Tick items as they are packed, and again as they come back.'}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>
            <th style={th}>Packed</th><th style={th}>Item</th><th style={th}>Category</th>
            <th style={th}>Qty</th><th style={th}>Back</th><th style={th} />
          </tr></thead>
          <tbody>
            {w.checklist.map((i: any) => (
              <tr key={i.id}>
                <td style={cell}>
                  <input type="checkbox" checked={!!i.is_packed} disabled={busy}
                         onChange={ev => patchChild('checklist', i.id, { is_packed: ev.target.checked })} />
                </td>
                <td style={cell}>{i.label}{i.packed_by && <div style={{ fontSize: 10, color: 'var(--text2)' }}>packed by {i.packed_by}</div>}</td>
                <td style={cell}>{i.category || '—'}</td>
                <td style={cell}>{i.qty ?? '—'}</td>
                <td style={cell}>
                  {i.is_returnable
                    ? <input type="checkbox" checked={!!i.is_returned} disabled={busy}
                             onChange={ev => patchChild('checklist', i.id, { is_returned: ev.target.checked })} />
                    : <span style={{ fontSize: 11, color: 'var(--text2)' }}>n/a</span>}
                </td>
                <td style={cell}>{editable && <button style={btnDanger} disabled={busy} onClick={() => delChild('checklist', i.id)}>×</button>}</td>
              </tr>
            ))}
            {!w.checklist.length && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={6}>Nothing on the list yet.</td></tr>}
          </tbody>
        </table>
      </Section>

      {editable && (
        <Section title="Add items">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end', marginBottom: 12 }}>
            <div><span style={label}>Item</span>
              <input style={{ ...input, width: 220 }} value={n.label} onChange={ev => setN({ ...n, label: ev.target.value })} /></div>
            <div><span style={label}>Category</span>
              <input style={{ ...input, width: 140 }} value={n.category} onChange={ev => setN({ ...n, category: ev.target.value })} /></div>
            <div><span style={label}>Qty</span>
              <input style={{ ...input, width: 80 }} type="number" value={n.qty} onChange={ev => setN({ ...n, qty: ev.target.value })} /></div>
            <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center', paddingBottom: 8 }}>
              <input type="checkbox" checked={n.is_returnable}
                     onChange={ev => setN({ ...n, is_returnable: ev.target.checked })} /> comes back
            </label>
            <button style={btnPrimary} disabled={busy || !n.label.trim()} onClick={async () => {
              await addChild('checklist', { ...n, qty: n.qty === '' ? null : Number(n.qty) })
              setN({ label: '', category: '', qty: '', is_returnable: true })
            }}>Add</button>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'end', borderTop: '1px solid var(--border)', paddingTop: 12 }}>
            <div><span style={label}>Or start from a template</span>
              <select style={{ ...input, width: 260 }} value={tpl} onChange={ev => setTpl(ev.target.value)}>
                <option value="">—</option>
                {templates.map(t => <option key={t.id} value={t.id}>{t.name}{t.source === 'house' ? ' (standard)' : ''}</option>)}
              </select></div>
            <button style={btn} disabled={busy || !tpl} onClick={() => call(
              `/api/v1/marketing/events/${id}/apply-checklist-template`,
              { method: 'POST', body: JSON.stringify({ template_id: tpl }) })}>Add template items</button>
          </div>
        </Section>
      )}
    </>
  )
}

// ══════════════════════════════════════════════════════════════════════════════════════════════
function Parties({ w, editable, Picker, busy, addChild, patchChild, delChild, opts }: any) {
  const [n, setN] = useState({ vendor_name: '', party_type_key: '', contact_name: '', contact_phone: '', cost: '' })
  return (
    <>
      <Section title="Outside parties" sub="DJ, food truck, table host, photographer — whoever is coming who does not work for you.">
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>
            <th style={th}>Who</th><th style={th}>Type</th><th style={th}>Contact</th>
            <th style={th}>Cost</th><th style={th}>Confirmed</th><th style={th} />
          </tr></thead>
          <tbody>
            {w.vendors.map((v: any) => (
              <tr key={v.id}>
                <td style={cell}>{v.vendor_name || '—'}</td>
                <td style={cell}>{optionLabel(opts('party_type'), v.party_type_key)}</td>
                <td style={cell}>{[v.contact_name, v.contact_phone].filter(Boolean).join(' · ') || '—'}</td>
                <td style={cell}>{fmtMoney(v.cost)}</td>
                <td style={cell}>
                  <select style={{ ...input, width: 130 }} value={v.confirm_state} disabled={!editable || busy}
                          onChange={ev => patchChild('vendors', v.id, { confirm_state: ev.target.value })}>
                    {['planned', 'confirmed', 'declined', 'cancelled'].map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </td>
                <td style={cell}>{editable && <button style={btnDanger} disabled={busy} onClick={() => delChild('vendors', v.id)}>×</button>}</td>
              </tr>
            ))}
            {!w.vendors.length && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={6}>No outside parties.</td></tr>}
          </tbody>
        </table>
        <p style={{ fontSize: 12, color: 'var(--text2)', marginTop: 10 }}>
          Cost here is for planning only — it is not booked to the P&amp;L by this screen.
        </p>
      </Section>
      {editable && (
        <Section title="Add an outside party">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
            <div><span style={label}>Who</span>
              <input style={{ ...input, width: 200 }} value={n.vendor_name} onChange={ev => setN({ ...n, vendor_name: ev.target.value })} /></div>
            <div><span style={label}>Type</span>
              <Picker listKey="party_type" value={n.party_type_key} onChange={(v: string) => setN({ ...n, party_type_key: v })} /></div>
            <div><span style={label}>Contact</span>
              <input style={{ ...input, width: 150 }} value={n.contact_name} onChange={ev => setN({ ...n, contact_name: ev.target.value })} /></div>
            <div><span style={label}>Phone</span>
              <input style={{ ...input, width: 140 }} value={n.contact_phone} onChange={ev => setN({ ...n, contact_phone: ev.target.value })} /></div>
            <div><span style={label}>Cost</span>
              <input style={{ ...input, width: 100 }} type="number" value={n.cost} onChange={ev => setN({ ...n, cost: ev.target.value })} /></div>
            <button style={btnPrimary} disabled={busy || !n.vendor_name.trim()} onClick={async () => {
              await addChild('vendors', { ...n, cost: n.cost === '' ? null : Number(n.cost) })
              setN({ vendor_name: '', party_type_key: '', contact_name: '', contact_phone: '', cost: '' })
            }}>Add</button>
          </div>
        </Section>
      )}
    </>
  )
}

// ══════════════════════════════════════════════════════════════════════════════════════════════
function Creative({ w, editable, Picker, busy, addChild, delChild, opts }: any) {
  const [n, setN] = useState({ channel_key: '', label: '', url: '', planned_post_at: '' })
  return (
    <>
      <Section title="Planned creative"
               sub="One row per planned post or piece. This phase records the plan and the link; pulling the artwork itself from a creative gallery or a company marketing portal is a later phase.">
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>
            <th style={th}>Channel</th><th style={th}>What</th><th style={th}>Link</th>
            <th style={th}>Planned for</th><th style={th}>Status</th><th style={th} />
          </tr></thead>
          <tbody>
            {w.links.map((l: any) => (
              <tr key={l.id}>
                <td style={cell}>{optionLabel(opts('link_channel'), l.channel_key)}</td>
                <td style={cell}>{l.label || '—'}</td>
                <td style={cell}>
                  {/* rel=noreferrer: these are links a user typed, opened from an authenticated page. */}
                  {l.url ? <a href={l.url} target="_blank" rel="noopener noreferrer">open</a> : '—'}
                </td>
                <td style={cell}>{fmtDateTime(l.planned_post_at)}</td>
                <td style={cell}>{l.status}</td>
                <td style={cell}>{editable && <button style={btnDanger} disabled={busy} onClick={() => delChild('links', l.id)}>×</button>}</td>
              </tr>
            ))}
            {!w.links.length && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={6}>Nothing planned.</td></tr>}
          </tbody>
        </table>
      </Section>
      {editable && (
        <Section title="Add a planned link">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
            <div><span style={label}>Channel</span>
              <Picker listKey="link_channel" value={n.channel_key} onChange={(v: string) => setN({ ...n, channel_key: v })} /></div>
            <div><span style={label}>What is it</span>
              <input style={{ ...input, width: 200 }} value={n.label} onChange={ev => setN({ ...n, label: ev.target.value })} /></div>
            <div><span style={label}>Link</span>
              <input style={{ ...input, width: 260 }} value={n.url} onChange={ev => setN({ ...n, url: ev.target.value })} /></div>
            <div><span style={label}>Planned for</span>
              <input style={{ ...input, width: 190 }} type="datetime-local" value={n.planned_post_at}
                     onChange={ev => setN({ ...n, planned_post_at: ev.target.value })} /></div>
            <button style={btnPrimary} disabled={busy} onClick={async () => {
              await addChild('links', { ...n, planned_post_at: fromLocalInput(n.planned_post_at) })
              setN({ channel_key: '', label: '', url: '', planned_post_at: '' })
            }}>Add</button>
          </div>
        </Section>
      )}
    </>
  )
}

// ══════════════════════════════════════════════════════════════════════════════════════════════
function Giveaways({ w, editable, Picker, busy, addChild, patchChild, delChild, opts }: any) {
  const [n, setN] = useState({ item_label: '', giveaway_type_key: '', qty_out: '' })
  const rec = w.giveaway_reconciliation
  return (
    <>
      <Section title="Giveaways taken and brought back" sub={rec.note}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>
            <th style={th}>Item</th><th style={th}>Type</th><th style={th}>Out</th>
            <th style={th}>Given</th><th style={th}>Back</th><th style={th}>Unaccounted</th><th style={th} />
          </tr></thead>
          <tbody>
            {rec.items.map((g: any) => (
              <tr key={g.id}>
                <td style={cell}>{g.item_label}</td>
                <td style={cell}>{optionLabel(opts('giveaway_type'), g.giveaway_type_key)}</td>
                <td style={cell}>{g.qty_out ?? '—'}</td>
                <td style={cell}>
                  <input style={{ ...input, width: 80 }} type="number" defaultValue={g.qty_given ?? ''} disabled={busy}
                         onBlur={ev => patchChild('giveaways', g.id, { qty_given: ev.target.value === '' ? null : Number(ev.target.value) })} />
                </td>
                <td style={cell}>
                  <input style={{ ...input, width: 80 }} type="number" defaultValue={g.qty_returned ?? ''} disabled={busy}
                         onBlur={ev => patchChild('giveaways', g.id, { qty_returned: ev.target.value === '' ? null : Number(ev.target.value) })} />
                </td>
                <td style={{ ...cell, color: (g.unaccounted || 0) > 0 ? '#dc2626' : 'var(--text2)' }}>
                  {g.counted ? g.unaccounted : 'not counted back'}
                </td>
                <td style={cell}>{editable && <button style={btnDanger} disabled={busy} onClick={() => delChild('giveaways', g.id)}>×</button>}</td>
              </tr>
            ))}
            {!rec.items.length && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={7}>Nothing taken.</td></tr>}
          </tbody>
        </table>
      </Section>
      {editable && (
        <Section title="Take something to the event">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
            <div><span style={label}>Item</span>
              <input style={{ ...input, width: 200 }} value={n.item_label} onChange={ev => setN({ ...n, item_label: ev.target.value })} /></div>
            <div><span style={label}>Type</span>
              <Picker listKey="giveaway_type" value={n.giveaway_type_key} onChange={(v: string) => setN({ ...n, giveaway_type_key: v })} /></div>
            <div><span style={label}>Qty out</span>
              <input style={{ ...input, width: 90 }} type="number" value={n.qty_out} onChange={ev => setN({ ...n, qty_out: ev.target.value })} /></div>
            <button style={btnPrimary} disabled={busy || !n.item_label.trim()} onClick={async () => {
              await addChild('giveaways', { ...n, qty_out: n.qty_out === '' ? null : Number(n.qty_out) })
              setN({ item_label: '', giveaway_type_key: '', qty_out: '' })
            }}>Add</button>
          </div>
        </Section>
      )}
    </>
  )
}

// ══════════════════════════════════════════════════════════════════════════════════════════════
function Results({ w, act, opts, busy, addChild, delChild }: any) {
  const [n, setN] = useState({ metric_key: '', target_value: '' })
  return (
    <>
      <Section title="Goals">
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end', marginBottom: 12 }}>
          <div><span style={label}>Metric</span>
            <select style={{ ...input, width: 220 }} value={n.metric_key} onChange={ev => setN({ ...n, metric_key: ev.target.value })}>
              <option value="">—</option>
              {opts('goal_metric').map((o: MarketingOption) => <option key={o.key} value={o.key}>{o.label}</option>)}
            </select></div>
          <div><span style={label}>Target</span>
            <input style={{ ...input, width: 110 }} type="number" value={n.target_value}
                   onChange={ev => setN({ ...n, target_value: ev.target.value })} /></div>
          <button style={btnPrimary} disabled={busy || !n.metric_key} onClick={async () => {
            await addChild('goals', { metric_key: n.metric_key, target_value: n.target_value === '' ? null : Number(n.target_value) })
            setN({ metric_key: '', target_value: '' })
          }}>Set goal</button>
        </div>
        {!w.goals.length && <div style={{ fontSize: 13, color: 'var(--text2)' }}>No goals set for this event.</div>}
      </Section>

      {/* THE attribution caption. Rendered as a visible block, never a tooltip: the whole point is
          that nobody reads these numbers as "sales the event caused". */}
      {act?.attribution && (
        <div style={{ ...panel, marginBottom: 14, borderColor: '#f39c12', background: 'var(--surface)' }}>
          <strong style={{ fontSize: 13 }}>{act.attribution.headline}</strong>
          <p style={{ fontSize: 12, color: 'var(--text2)', margin: '6px 0 0', lineHeight: 1.5 }}>{act.attribution.detail}</p>
          <p style={{ fontSize: 12, color: 'var(--text2)', margin: '6px 0 0', lineHeight: 1.5 }}>{act.attribution.grain_note}</p>
          <p style={{ fontSize: 11, color: 'var(--text2)', margin: '6px 0 0' }}>
            Window: {act.attribution.event_days.join(', ') || '—'} · Baseline: {act.attribution.baseline_method} ·
            Stores: {act.attribution.stores.join(', ') || 'none attached'}
          </p>
          <p style={{ fontSize: 11, color: 'var(--text2)', margin: '4px 0 0' }}>Source: {act.attribution.source}</p>
          {act.attribution.source_note && <p style={{ fontSize: 11, color: '#f39c12', margin: '4px 0 0' }}>{act.attribution.source_note}</p>}
        </div>
      )}

      <Section title="Goal vs what the stores did">
        {act && !act.available && <div style={{ fontSize: 13, color: '#f39c12', marginBottom: 10 }}>{act.reason}</div>}
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>
            <th style={th}>Metric</th><th style={th}>Goal</th><th style={th}>Event window</th>
            <th style={th}>vs goal</th><th style={th}>Baseline / day</th><th style={th}>Difference / day</th><th style={th} />
          </tr></thead>
          <tbody>
            {(act?.goals || []).map((g: any) => {
              const goalRow = w.goals.find((x: any) => x.metric_key === g.metric_key)
              return (
                <tr key={g.metric_key}>
                  <td style={cell}>{g.label}
                    {g.source_label && <div style={{ fontSize: 10, color: 'var(--text2)' }}>{g.source_label}</div>}</td>
                  <td style={cell}>{fmtMetric(g.target_value, g.unit)}</td>
                  <td style={cell}>
                    {g.derivable ? <strong>{fmtMetric(g.actual_value, g.unit)}</strong>
                      : <span style={{ color: 'var(--text2)', fontSize: 12 }}>no automatic actual</span>}
                  </td>
                  <td style={{ ...cell, color: (g.variance ?? 0) >= 0 ? '#16a34a' : '#dc2626' }}>
                    {g.derivable && g.variance !== null ? `${g.variance >= 0 ? '+' : ''}${fmtMetric(g.variance, g.unit)}` : '—'}
                    {g.pct_of_goal !== null && g.pct_of_goal !== undefined && <div style={{ fontSize: 11 }}>{g.pct_of_goal}% of goal</div>}
                  </td>
                  <td style={cell}>{g.derivable ? fmtMetric(g.baseline_per_day, g.unit) : '—'}</td>
                  <td style={cell}>
                    {g.derivable && g.diff_per_day !== null && g.diff_per_day !== undefined
                      ? <>{g.diff_per_day >= 0 ? '+' : ''}{fmtMetric(g.diff_per_day, g.unit)}
                          {g.pct_change_vs_baseline !== null && g.pct_change_vs_baseline !== undefined &&
                            <div style={{ fontSize: 11, color: 'var(--text2)' }}>{g.pct_change_vs_baseline}%</div>}
                        </>
                      : <span style={{ fontSize: 12, color: 'var(--text2)' }}>no baseline</span>}
                  </td>
                  <td style={cell}>
                    {!g.derivable && <div style={{ fontSize: 11, color: 'var(--text2)' }}>{g.reason}</div>}
                    {goalRow && <button style={btnDanger} disabled={busy} onClick={() => delChild('goals', goalRow.id)}>×</button>}
                  </td>
                </tr>
              )
            })}
            {!act && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={7}>Loading results…</td></tr>}
            {act && !act.goals.length && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={7}>Set a goal above to compare against.</td></tr>}
          </tbody>
        </table>
      </Section>
    </>
  )
}

// ══════════════════════════════════════════════════════════════════════════════════════════════
function Debrief({ w, busy, patchEvent }: any) {
  const e = w.event
  const [d, setD] = useState({
    debrief_what_worked: e.debrief_what_worked || '',
    debrief_what_didnt: e.debrief_what_didnt || '',
    debrief_notes: e.debrief_notes || '',
  })
  return (
    <Section title="Debrief" sub={e.debrief_at ? `Last written ${fmtDateTime(e.debrief_at)}` : 'Write this while it is still fresh — it is the record of what actually happened.'}>
      {(['debrief_what_worked', 'debrief_what_didnt', 'debrief_notes'] as const).map(k => (
        <div key={k} style={{ marginBottom: 12 }}>
          <span style={label}>{k === 'debrief_what_worked' ? 'What worked' : k === 'debrief_what_didnt' ? 'What did not' : 'Anything else'}</span>
          <textarea style={{ ...input, minHeight: 70 }} value={d[k]} onChange={ev => setD({ ...d, [k]: ev.target.value })} />
        </div>
      ))}
      <button style={btnPrimary} disabled={busy} onClick={() => patchEvent(d)}>Save debrief</button>
      <p style={{ fontSize: 12, color: 'var(--text2)', marginTop: 10 }}>
        The debrief stays editable after the event is closed; the plan does not.
      </p>
    </Section>
  )
}
