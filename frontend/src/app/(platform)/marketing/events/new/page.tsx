'use client'
// Plan an event — the create form.
//
// Every picker here is rendered from `/marketing/options`. There is not one hard-coded theme, venue
// type or metric in this file: that is the owner's "options pre added with plus sign to add more as
// per user discretion" requirement, and the "+" beside each picker links straight to the settings
// screen so adding one never means leaving the flow permanently.
//
// The form tells the planner up-front whether the event will need approval, using the same rule the
// backend applies, so the requirement is never a surprise at go-live.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/client'
import {
  panel, btnPrimary, btn,
  fromLocalInput, fmtMoney,
  type MarketingOption, type MarketingConfig, type OptionList,
} from '@/lib/marketing'
// Field / Picker / Area live at MODULE SCOPE in their own file, and that is the whole point — see
// that file's note. Declaring them inside this component made each keystroke remount the input and
// drop focus (owner bug 2026-10-21, "only letter at a time is entered").
import { Field, Area, Picker } from '@/lib/marketingForm'

export default function NewMarketingEvent() {
  const router = useRouter()
  const [lists, setLists] = useState<Record<string, MarketingOption[]>>({})
  const [config, setConfig] = useState<MarketingConfig | null>(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  const [f, setF] = useState({
    title: '', description: '', theme_key: '', market: '', primary_store_code: '', store_codes: '',
    venue_name: '', venue_type_key: '', address: '', city: '', state: '', postal_code: '',
    geo_lat: '', geo_lng: '', checkin_radius_m: '',
    setup_notes: '', parking_notes: '',
    event_start: '', event_end: '', staff_call_at: '', setup_start_at: '', teardown_end_at: '',
    planned_spend: '',
  })
  const set = (k: keyof typeof f, v: string) => setF(p => ({ ...p, [k]: v }))

  const load = useCallback(async () => {
    try {
      const [o, c] = await Promise.all([
        api('/api/v1/marketing/options'),
        api('/api/v1/marketing/config'),
      ])
      const map: Record<string, MarketingOption[]> = {}
      for (const l of (o.lists || []) as OptionList[]) map[l.list_key] = l.options
      setLists(map)
      setConfig(c.config)
    } catch (e: any) { setMsg(e?.message || String(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  // The SAME rule the backend applies (event_logic.approval_decision), shown as a preview only —
  // the server decides. Mirroring it here is what stops "why does this need approval?" arriving
  // after the plan is finished.
  const spend = f.planned_spend === '' ? null : Number(f.planned_spend)
  const willNeedApproval = (() => {
    if (!config?.approval_required) return { need: false, why: 'Event approval is switched off for your organisation.' }
    const t = config.approval_spend_threshold
    if (t === null || t === undefined) return { need: true, why: 'Your organisation requires every event to be approved.' }
    if (spend === null || Number.isNaN(spend)) return { need: true, why: `Approval is required above ${fmtMoney(t)}, and no planned spend has been entered.` }
    return spend <= t
      ? { need: false, why: `Planned spend is at or under the ${fmtMoney(t)} approval threshold.` }
      : { need: true, why: `Planned spend is above the ${fmtMoney(t)} approval threshold.` }
  })()

  async function save() {
    if (!f.title.trim()) { setMsg('Give the event a title.'); return }
    setSaving(true); setMsg('')
    try {
      const body: Record<string, unknown> = {
        ...f,
        event_start: fromLocalInput(f.event_start),
        event_end: fromLocalInput(f.event_end),
        staff_call_at: fromLocalInput(f.staff_call_at),
        setup_start_at: fromLocalInput(f.setup_start_at),
        teardown_end_at: fromLocalInput(f.teardown_end_at),
        planned_spend: f.planned_spend === '' ? null : Number(f.planned_spend),
        geo_lat: f.geo_lat === '' ? null : Number(f.geo_lat),
        geo_lng: f.geo_lng === '' ? null : Number(f.geo_lng),
        checkin_radius_m: f.checkin_radius_m === '' ? null : Number(f.checkin_radius_m),
        store_codes: f.store_codes.split(',').map(s => s.trim()).filter(Boolean),
      }
      const r = await api('/api/v1/marketing/events', { method: 'POST', body: JSON.stringify(body) })
      router.push(`/marketing/events/${r.event.id}`)
    } catch (e: any) { setMsg(e?.message || String(e)); setSaving(false) }
  }

  const grid: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 12 }

  return (
    <div style={{ padding: 20, maxWidth: 1100 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Plan an event</h1>
        <div style={{ flex: 1 }} />
        <Link href="/marketing" style={{ ...btn, textDecoration: 'none' }}>Cancel</Link>
        <button style={btnPrimary} onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Create as draft'}</button>
      </div>

      {msg && <div style={{ ...panel, marginBottom: 14, borderColor: '#dc2626', color: '#dc2626' }}>{msg}</div>}

      <section style={{ ...panel, marginBottom: 14 }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 10px' }}>What and who for</h2>
        <div style={grid}>
          <Field name="Event title" ph="Back-to-school table at Lincoln High"  value={f.title} onChange={v => set('title', v)} />
          <Picker options={lists['theme'] || []} name="Theme" value={f.theme_key} onChange={v => set('theme_key', v)} addHref="/marketing/settings?list=theme" />
          <Field name="Market"  value={f.market} onChange={v => set('market', v)} />
          <Field name="Primary store code"  value={f.primary_store_code} onChange={v => set('primary_store_code', v)} />
          <Field name="All stores working it" ph="comma separated"  value={f.store_codes} onChange={v => set('store_codes', v)} />
        </div>
        <div style={{ marginTop: 12 }}>
          <Area name="Description" value={f.description} onChange={v => set('description', v)} />
        </div>
      </section>

      <section style={{ ...panel, marginBottom: 14 }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 10px' }}>Where</h2>
        <div style={grid}>
          <Field name="Venue"  value={f.venue_name} onChange={v => set('venue_name', v)} />
          <Picker options={lists['venue_type'] || []} name="Venue type" value={f.venue_type_key} onChange={v => set('venue_type_key', v)} addHref="/marketing/settings?list=venue_type" />
          <Field name="Address"  value={f.address} onChange={v => set('address', v)} />
          <Field name="City"  value={f.city} onChange={v => set('city', v)} />
          <Field name="State"  value={f.state} onChange={v => set('state', v)} />
          <Field name="ZIP"  value={f.postal_code} onChange={v => set('postal_code', v)} />
          <Field name="Map pin — latitude" type="number" ph="40.9300"  value={f.geo_lat} onChange={v => set('geo_lat', v)} />
          <Field name="Map pin — longitude" type="number" ph="-73.9000"  value={f.geo_lng} onChange={v => set('geo_lng', v)} />
          <Field name="Check-in radius (m)"
                 type="number" ph={String(config?.default_checkin_radius_m ?? 150)}  value={f.checkin_radius_m} onChange={v => set('checkin_radius_m', v)} />
        </div>
        <p style={{ fontSize: 12, color: 'var(--text2)', margin: '10px 0 0' }}>
          The map pin is what a staff check-in is verified against. Without one, check-ins are still
          recorded — they are just marked &ldquo;no venue pin set&rdquo; rather than confirmed, and nobody is
          counted as absent for it.
        </p>
        <div style={{ ...grid, marginTop: 12 }}>
          <Area name="Setup notes" value={f.setup_notes} onChange={v => set('setup_notes', v)} minHeight={50} />
          <Area name="Parking notes" value={f.parking_notes} onChange={v => set('parking_notes', v)} minHeight={50} />
        </div>
      </section>

      <section style={{ ...panel, marginBottom: 14 }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 10px' }}>When</h2>
        <div style={grid}>
          <Field name="Event starts" type="datetime-local"  value={f.event_start} onChange={v => set('event_start', v)} />
          <Field name="Event ends" type="datetime-local"  value={f.event_end} onChange={v => set('event_end', v)} />
          {/* Its own field, by owner directive — not derived from the event start. */}
          <Field name="Staff have to be there by" type="datetime-local"  value={f.staff_call_at} onChange={v => set('staff_call_at', v)} />
          <Field name="Setup starts" type="datetime-local"  value={f.setup_start_at} onChange={v => set('setup_start_at', v)} />
          <Field name="Teardown done by" type="datetime-local"  value={f.teardown_end_at} onChange={v => set('teardown_end_at', v)} />
        </div>
        <p style={{ fontSize: 12, color: 'var(--text2)', margin: '10px 0 0' }}>
          &ldquo;Staff have to be there by&rdquo; is separate from the event start on purpose — it is what the
          roster, the reminders and each person&rsquo;s own call time are read from. Individual people can
          be given an earlier call time on the event&rsquo;s People tab.
        </p>
      </section>

      <section style={{ ...panel, marginBottom: 14 }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 10px' }}>Budget</h2>
        <div style={grid}>
          <Field name="Planned spend" type="number" ph="0.00"  value={f.planned_spend} onChange={v => set('planned_spend', v)} />
        </div>
        <div style={{
          marginTop: 10, padding: 10, borderRadius: 7, fontSize: 12,
          background: 'var(--surface)', border: '1px solid var(--border)',
          color: willNeedApproval.need ? '#f39c12' : 'var(--text2)',
        }}>
          {willNeedApproval.need ? '▲ This event will need approval before it can go live. ' : '✓ No approval needed. '}
          {willNeedApproval.why}
        </div>
      </section>

      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
        <Link href="/marketing" style={{ ...btn, textDecoration: 'none' }}>Cancel</Link>
        <button style={btnPrimary} onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Create as draft'}</button>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text2)', marginTop: 10 }}>
        Staff, backups, transport, the checklist, outside parties, creative links, giveaways and goals
        are all added on the event once it exists.
      </p>
    </div>
  )
}
