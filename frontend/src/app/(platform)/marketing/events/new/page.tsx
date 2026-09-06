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
  panel, input, label, btn, btnPrimary,
  fromLocalInput, fmtMoney,
  type MarketingOption, type MarketingConfig, type OptionList,
} from '@/lib/marketing'

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

  const Picker = ({ listKey, value, onChange, name }: {
    listKey: string; value: string; onChange: (v: string) => void; name: string
  }) => (
    <div>
      <span style={label}>{name}</span>
      <div style={{ display: 'flex', gap: 6 }}>
        <select style={input} value={value} onChange={e => onChange(e.target.value)}>
          <option value="">—</option>
          {(lists[listKey] || []).map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
        </select>
        {/* THE "+" the owner asked for. */}
        <Link href={`/marketing/settings?list=${listKey}`} title={`Add a ${name.toLowerCase()}`}
              style={{ ...btn, padding: '7px 11px', textDecoration: 'none', lineHeight: 1.2 }}>＋</Link>
      </div>
    </div>
  )

  const Field = ({ k, name, type = 'text', ph }: { k: keyof typeof f; name: string; type?: string; ph?: string }) => (
    <div>
      <span style={label}>{name}</span>
      <input style={input} type={type} value={f[k]} placeholder={ph} onChange={e => set(k, e.target.value)} />
    </div>
  )

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
          <Field k="title" name="Event title" ph="Back-to-school table at Lincoln High" />
          <Picker listKey="theme" name="Theme" value={f.theme_key} onChange={v => set('theme_key', v)} />
          <Field k="market" name="Market" />
          <Field k="primary_store_code" name="Primary store code" />
          <Field k="store_codes" name="All stores working it" ph="comma separated" />
        </div>
        <div style={{ marginTop: 12 }}>
          <span style={label}>Description</span>
          <textarea style={{ ...input, minHeight: 60 }} value={f.description}
                    onChange={e => set('description', e.target.value)} />
        </div>
      </section>

      <section style={{ ...panel, marginBottom: 14 }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 10px' }}>Where</h2>
        <div style={grid}>
          <Field k="venue_name" name="Venue" />
          <Picker listKey="venue_type" name="Venue type" value={f.venue_type_key} onChange={v => set('venue_type_key', v)} />
          <Field k="address" name="Address" />
          <Field k="city" name="City" />
          <Field k="state" name="State" />
          <Field k="postal_code" name="ZIP" />
          <Field k="geo_lat" name="Map pin — latitude" type="number" ph="40.9300" />
          <Field k="geo_lng" name="Map pin — longitude" type="number" ph="-73.9000" />
          <Field k="checkin_radius_m" name="Check-in radius (m)"
                 type="number" ph={String(config?.default_checkin_radius_m ?? 150)} />
        </div>
        <p style={{ fontSize: 12, color: 'var(--text2)', margin: '10px 0 0' }}>
          The map pin is what a staff check-in is verified against. Without one, check-ins are still
          recorded — they are just marked &ldquo;no venue pin set&rdquo; rather than confirmed, and nobody is
          counted as absent for it.
        </p>
        <div style={{ ...grid, marginTop: 12 }}>
          <div>
            <span style={label}>Setup notes</span>
            <textarea style={{ ...input, minHeight: 50 }} value={f.setup_notes}
                      onChange={e => set('setup_notes', e.target.value)} />
          </div>
          <div>
            <span style={label}>Parking notes</span>
            <textarea style={{ ...input, minHeight: 50 }} value={f.parking_notes}
                      onChange={e => set('parking_notes', e.target.value)} />
          </div>
        </div>
      </section>

      <section style={{ ...panel, marginBottom: 14 }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 10px' }}>When</h2>
        <div style={grid}>
          <Field k="event_start" name="Event starts" type="datetime-local" />
          <Field k="event_end" name="Event ends" type="datetime-local" />
          {/* Its own field, by owner directive — not derived from the event start. */}
          <Field k="staff_call_at" name="Staff have to be there by" type="datetime-local" />
          <Field k="setup_start_at" name="Setup starts" type="datetime-local" />
          <Field k="teardown_end_at" name="Teardown done by" type="datetime-local" />
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
          <Field k="planned_spend" name="Planned spend" type="number" ph="0.00" />
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
