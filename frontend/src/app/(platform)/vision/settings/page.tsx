'use client'
// Vision — SETTINGS. The one page that answers "why is nothing being recorded", in the order the
// gates actually bind: deployment switch → company switch → Google link → cameras → analyzers →
// consent. Each section states what it controls and what turning it on means.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { panel, btn, btnPrimary, cell, th, cameraName, fmtDateTime, type Camera, type VisionConfig, visionError,
  idsBlocker, authorizeBlocker, oauthReturn, syncMessage, storeOptions, withCurrent, type EdgeAgent,
  type GoogleLinkState, type StoreOption,
} from '@/lib/vision'

// The redirect URI is part of the OAuth signature: the value sent when building the consent URL and
// the value sent when redeeming the code must match byte for byte, or Google rejects the exchange.
// One helper, used by both, so they cannot drift.
const REDIRECT_PATH = '/vision/settings'
// The analyzer needs the API base, and an operator should not have to work out what to paste.
function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_URL || '<api url>'
}
function redirectUri(): string {
  return typeof window === 'undefined' ? '' : `${window.location.origin}${REDIRECT_PATH}`
}

export default function VisionSettingsPage() {
  const [status, setStatus] = useState<any>(null)
  const [cameras, setCameras] = useState<Camera[]>([])
  const [consent, setConsent] = useState<any[]>([])
  const [agents, setAgents] = useState<EdgeAgent[]>([])
  const [homes, setHomes] = useState<any[] | null>(null)
  // null = the store list could not be read. StorePick falls back to a free-text box in that case
  // rather than rendering an empty dropdown nobody can pick from.
  const [stores, setStores] = useState<StoreOption[] | null>(null)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  // What registration and rotation now hand back: a short-lived code, never a secret.
  const [newSecret, setNewSecret] = useState<{
    agent_key: string; enroll_code: string; expires_at?: string; ttl_minutes?: number
  } | null>(null)

  const load = useCallback(async () => {
    setErr('')
    try {
      const [s, c] = await Promise.all([
        api('/api/v1/vision/status'),
        api('/api/v1/vision/cameras'),
      ])
      setStatus(s); setCameras(c.cameras || [])
      try { setConsent((await api('/api/v1/vision/consent')).consent || []) } catch { setConsent([]) }
      try { setAgents((await api('/api/v1/vision/edge-agents')).agents || []) } catch { setAgents([]) }
      // The company's real stores, so a camera is ASSIGNED rather than typed at. A mistyped code
      // does not error — it silently attributes a store's traffic to a store that does not exist.
      try { setStores(storeOptions(await api('/api/v1/storeops/stores'))) } catch { setStores(null) }
      // Only meaningful once Google is linked; a failure here is not a page failure.
      try { setHomes((await api('/api/v1/vision/structures')).structures || []) } catch { setHomes(null) }
    } catch (e: any) { setErr(visionError(e)) }
  }, [])

  // Google sends the operator back to THIS url with ?code=... — finish the link right here. The
  // first build instead asked them to copy the code into a box that only existed in the tab they had
  // just navigated away from, so the round trip could never complete. Nothing to paste now.
  useEffect(() => {
    const strip = () => window.history.replaceState({}, '', REDIRECT_PATH)
    const back = oauthReturn(window.location.search)
    if (back.none) { void load(); return }
    if (back.error) {
      setErr(`Google did not authorize the connection: ${back.error}`)
      strip(); void load(); return
    }
    setBusy(true)
    void (async () => {
      try {
        // No client id or secret here: they are already on the server from the save that built the
        // consent url, and the secret is write-only by design — it cannot be re-sent from a page.
        await api('/api/v1/vision/google/link', {
          method: 'POST',
          body: JSON.stringify({ code: back.code, redirect_uri: redirectUri() }),
        })
        setMsg('Google connected. Sync cameras below to pull in the ones this account can see.')
      } catch (e: any) { setErr(visionError(e)) }
      finally { strip(); setBusy(false); await load() }
    })()
  }, [load])

  async function act(fn: () => Promise<any>, ok: string) {
    setBusy(true); setMsg(''); setErr('')
    // Only stamp the caller's message when there IS one. Passing '' means "fn writes its own",
    // and the unguarded setMsg(ok) wiped it — which is how a working Sync button came to look dead.
    try { await fn(); if (ok) setMsg(ok); await load() }
    catch (e: any) { setErr(visionError(e)) }
    finally { setBusy(false) }
  }

  const saveConfig = (patch: Partial<VisionConfig>) => act(
    () => api('/api/v1/vision/config', { method: 'PUT', body: JSON.stringify(patch) }),
    'Saved.')

  if (!status) return <div style={{ padding: 20, color: 'var(--text2)' }}>{err || 'Loading…'}</div>

  const cfg: VisionConfig = status.config
  const canEdit: boolean = status.can_edit

  return (
    <div style={{ padding: 20, maxWidth: 1000 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>⚙️ Vision Settings</h1>
        <Link href="/vision" style={{ ...btn, textDecoration: 'none' }}>📹 Live Cameras</Link>
      </div>

      {msg && <div style={{ ...panel, marginBottom: 12, borderLeft: '3px solid #16a34a', fontSize: 13 }}>{msg}</div>}
      {err && <div style={{ ...panel, marginBottom: 12, borderColor: '#dc2626', color: '#dc2626', fontSize: 13 }}>{err}</div>}
      {!canEdit && (
        <div style={{ ...panel, marginBottom: 12, fontSize: 13, color: 'var(--text2)' }}>
          You can view this page but not change it. Editing needs a company-wide role or the
          <b> vision</b> settings permission.
        </div>
      )}

      {!cfg.available && (
        <Section title="Not installed">
          <div style={{ fontSize: 13.5, color: 'var(--text2)' }}>
            Migration <code>900_vision_camera_analytics.sql</code> has not been run on this database.
            Until it is, this module stores nothing and every switch below is inert.
          </div>
        </Section>
      )}

      {/* 1. Master switch */}
      <Section title="1 · Company master switch"
        note="Off by default for every company. Off here means off everywhere — no live view, no counting, no transcripts.">
        <Toggle label="Camera analytics enabled" checked={cfg.enabled} disabled={!canEdit || !cfg.available}
          onChange={v => saveConfig({ enabled: v })} />
        {cfg.enabled_at && (
          <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 6 }}>
            Turned on {fmtDateTime(cfg.enabled_at)} by {cfg.enabled_by || 'unknown'}
          </div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(230px,1fr))', gap: 8, marginTop: 12 }}>
          <Toggle label="Live view" checked={cfg.live_view_enabled} disabled={!canEdit || !cfg.enabled}
            onChange={v => saveConfig({ live_view_enabled: v })} hint="Watching a camera from this app" />
          <Toggle label="Customers in / out" checked={cfg.traffic_enabled} disabled={!canEdit || !cfg.enabled}
            onChange={v => saveConfig({ traffic_enabled: v })} hint="Door counting" />
          <Toggle label="Heat map" checked={cfg.heatmap_enabled} disabled={!canEdit || !cfg.enabled}
            onChange={v => saveConfig({ heatmap_enabled: v })} hint="Where people stand" />
        </div>
      </Section>

      {/* 2. Voice — the one with legal weight */}
      <Section title="2 · Voice transcripts & coaching"
        note="Separate from everything above, and off by default. This is the switch that starts recording what your staff say at work.">
        {cfg.audio_kill_switch ? (
          <div style={{ fontSize: 13.5, color: 'var(--text2)' }}>
            🔒 <b>Disabled for this deployment.</b> Voice capture is switched off at the server
            (<code>VISION_AUDIO_ENABLED</code> is not set), so it is off for every company regardless of
            the settings here. Turning it on is a deliberate server change, not a checkbox — most of the
            states these stores operate in require every party to a recorded conversation to consent.
          </div>
        ) : (
          <>
            <Toggle label="Capture voice transcripts" checked={cfg.audio_analytics_enabled}
              disabled={!canEdit || !cfg.enabled} onChange={v => saveConfig({ audio_analytics_enabled: v })}
              hint="Employee speech only — the customer's half is discarded at ingest and never stored" />
            <Toggle label="Score behaviour from transcripts" checked={cfg.behavior_scoring_enabled}
              disabled={!canEdit || !cfg.enabled} onChange={v => saveConfig({ behavior_scoring_enabled: v })}
              hint="Coaching numbers. Never used in any pay calculation." />
            <div style={{ marginTop: 10, fontSize: 13 }}>
              <span style={{ color: 'var(--text2)' }}>Consent policy: </span>
              <select value={cfg.audio_consent_mode} disabled={!canEdit}
                onChange={e => saveConfig({ audio_consent_mode: e.target.value as any })}
                style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)' }}>
                <option value="required">Require a signed consent record per employee (recommended)</option>
                <option value="off">We hold our own signed releases outside this system</option>
              </select>
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 6 }}>
              An employee who has <b>declined</b> or <b>withdrawn</b> is never recorded, under either
              policy. Choosing the second option is written to the audit log with your name.
            </div>
          </>
        )}
      </Section>

      {/* 3. Google */}
      <Section title="3 · Google (Nest) connection"
        note="Store cameras are reached through Google's Smart Device Management API. One Device Access project per company; the account that owns the cameras authorizes it once.">
        <div style={{ fontSize: 13.5, marginBottom: 10 }}>
          Status: <b style={{ color: status.google.linked ? '#16a34a' : '#f39c12' }}>
            {status.google.linked ? 'Connected' : 'Not connected'}
          </b>
          {status.google.project_id && <span style={{ color: 'var(--text3)' }}> · project {status.google.project_id}</span>}
          {status.google.last_error && (
            <div style={{ color: '#dc2626', fontSize: 12.5, marginTop: 6 }}>{status.google.last_error}</div>
          )}
        </div>
        <GoogleLink canEdit={canEdit} google={status.google} onSaved={m => { setMsg(m); void load() }} />
        <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button style={btnPrimary} disabled={!canEdit || !status.google.linked || busy}
            onClick={() => act(async () => {
              setMsg(syncMessage(await api('/api/v1/vision/cameras/sync', { method: 'POST' })))
            }, '')}>
            Sync cameras from Google
          </button>
          {status.google.linked && (
            <button style={btn} disabled={!canEdit || busy}
              onClick={() => act(() => api('/api/v1/vision/google/link', { method: 'DELETE' }), 'Disconnected.')}>
              Disconnect
            </button>
          )}
        </div>
      </Section>

      {/* 3b. Homes — which of this Google account's homes belong to THIS company */}
      {status.google.linked && (
        <Section title="3b · Which homes belong to this company"
          note="A Google account can own several homes. Only the ones you connect here contribute cameras — anything unconnected imports nothing, including a new home added in the Google Home app later.">
          {homes === null ? (
            <div style={{ fontSize: 13.5, color: 'var(--text2)' }}>
              Could not read the homes on this Google account. Reconnect Google above, then reload.
            </div>
          ) : homes.length === 0 ? (
            <div style={{ fontSize: 13.5, color: 'var(--text2)' }}>No homes found on this account.</div>
          ) : (
            <>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={th}>Home</th><th style={th}>Connect to this company</th>
                    <th style={th}>Default store code</th>
                  </tr>
                </thead>
                <tbody>
                  {homes.map(h => (
                    <tr key={h.structure_id}>
                      <td style={{ ...cell, fontWeight: 600 }}>
                        {h.structure_name}
                        {h.claimed_by_another_company && (
                          <div style={{ fontSize: 11, color: '#f39c12', fontWeight: 400 }}>
                            already connected to another company
                          </div>
                        )}
                      </td>
                      <td style={cell}>
                        <Check checked={h.enabled} disabled={!canEdit || h.claimed_by_another_company}
                          onChange={v => setHomes(homes.map(x => x.structure_id === h.structure_id
                            ? { ...x, enabled: v, assigned: v } : x))} />
                      </td>
                      <td style={cell}>
                        <StorePick stores={stores} value={h.default_store_code}
                          disabled={!canEdit || !h.enabled} emptyLabel="— optional —"
                          onPick={v => setHomes(homes.map(x => x.structure_id === h.structure_id
                            ? { ...x, default_store_code: v } : x))} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ fontSize: 11.5, color: 'var(--text3)', margin: '10px 0' }}>
                A default store code pre-fills the store on cameras newly synced from that home. It
                never overwrites a store you have already set by hand.
              </div>
              <button style={btnPrimary} disabled={!canEdit || busy}
                onClick={() => act(() => api('/api/v1/vision/structures', {
                  method: 'PUT',
                  body: JSON.stringify({ structures: homes.filter(h => h.enabled) }),
                }), 'Home assignments saved.')}>
                Save home assignments
              </button>
            </>
          )}
        </Section>
      )}

      {/* 4. Cameras */}
      <Section title="4 · Cameras"
        note="A newly synced camera contributes nothing until you assign it to a store. Mark exactly one camera per store as the entrance — that is the one that counts customers in and out.">
        {cameras.length === 0 ? (
          <div style={{ fontSize: 13.5, color: 'var(--text2)' }}>No cameras yet — connect Google and sync.</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 720 }}>
              <thead>
                <tr>
                  <th style={th}>Camera</th><th style={th}>Store</th><th style={th}>Analytics</th>
                  <th style={th}>Entrance</th><th style={th}>Audio</th><th style={th}>Status</th>
                </tr>
              </thead>
              <tbody>
                {cameras.map(c => (
                  <tr key={c.id}>
                    <td style={{ ...cell, fontWeight: 600 }}>
                      {/* Google's name stands unless this company decides otherwise. Blank the box
                          to fall back to it — a rename is a per-company preference, never a rewrite
                          of what the device is actually called. */}
                      <input defaultValue={c.label || ''} disabled={!canEdit}
                        placeholder={c.display_name || c.device_name.split('/').pop() || 'Camera'}
                        title="Leave blank to keep the name Google gives this camera"
                        onFocus={e => { e.target.style.borderColor = 'var(--border)'; e.target.style.background = 'var(--surface)' }}
                        onBlur={e => {
                          e.target.style.borderColor = 'transparent'; e.target.style.background = 'transparent'
                          const v = e.target.value.trim()
                          if (v !== (c.label || '')) act(
                            () => api(`/api/v1/vision/cameras/${c.id}`, { method: 'PATCH', body: JSON.stringify({ label: v }) }),
                            v ? 'Camera renamed.' : 'Reverted to the name Google gives this camera.')
                        }}
                        style={{ width: 150, padding: '3px 6px', borderRadius: 5, border: '1px solid transparent',
                          background: 'transparent', color: 'var(--text)', fontSize: 13, fontWeight: 600 }} />
                      <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>
                        {c.stream_protocol.toUpperCase()}
                        {(c as any).structure_name ? ` · ${(c as any).structure_name}` : ''}
                        {c.room ? ` · ${c.room}` : ''}
                      </div>
                    </td>
                    <td style={cell}>
                      <StorePick stores={stores} value={c.store_code} disabled={!canEdit}
                        onPick={v => act(
                          () => api(`/api/v1/vision/cameras/${c.id}`, { method: 'PATCH', body: JSON.stringify({ store_code: v }) }),
                          'Camera updated.')} />
                    </td>
                    <td style={cell}><Check checked={c.analytics_enabled} disabled={!canEdit}
                      onChange={v => act(() => api(`/api/v1/vision/cameras/${c.id}`, { method: 'PATCH', body: JSON.stringify({ analytics_enabled: v }) }), 'Camera updated.')} /></td>
                    <td style={cell}><Check checked={c.is_entrance} disabled={!canEdit}
                      onChange={v => act(() => api(`/api/v1/vision/cameras/${c.id}`, { method: 'PATCH', body: JSON.stringify({ is_entrance: v }) }), 'Camera updated.')} /></td>
                    <td style={cell}><Check checked={c.audio_enabled} disabled={!canEdit || !c.supports_audio || cfg.audio_kill_switch}
                      onChange={v => act(() => api(`/api/v1/vision/cameras/${c.id}`, { method: 'PATCH', body: JSON.stringify({ audio_enabled: v }) }), 'Camera updated.')} /></td>
                    <td style={{ ...cell, color: c.status === 'online' ? '#16a34a' : 'var(--text3)' }}>{c.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* 4b. Google's own events — free, every camera, no analyzer */}
      {status.google.linked && (
        <Section title="4b · Busy hours from Google (no analyzer needed)"
          note="Nest cameras detect people themselves. Google can push us an event each time — free, every camera, no video and no edge box. It gives ACTIVITY, not direction: a customer leaving looks the same as one arriving, so this is busy hours and staffing, never a footfall count.">
          <Toggle label="Accept Google camera events" checked={cfg.google_events_enabled}
            disabled={!canEdit || !cfg.enabled}
            onChange={v => saveConfig({ google_events_enabled: v })}
            hint="Person sightings pushed by Google" />
          <div style={{ ...panel, marginTop: 12, fontSize: 13 }}>
            {/* A push subscription that is wrong does not announce itself — Google retries into the
                void for days. So this reports what ARRIVED, not what is configured. */}
            {status.events?.last_7d ? (
              <>
                <b style={{ color: '#16a34a' }}>{status.events.last_7d} event(s)</b> in the last 7 days
                {status.events.last_event_at && <> · most recent {fmtDateTime(status.events.last_event_at)}</>}
              </>
            ) : (
              <span style={{ color: 'var(--text2)' }}>
                No events received yet. Until some arrive this is set up but not working — the
                subscription below has to be created on Google&apos;s side.
              </span>
            )}
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 12 }}>
            In the Google Cloud console, create a <b>Pub/Sub push subscription</b> on the Device
            Access topic, pointing at:
            <code style={{ display: 'block', margin: '6px 0', fontSize: 12, wordBreak: 'break-all' }}>
              {apiBase()}/api/v1/vision/google/events
            </code>
            Enable <b>authentication</b> on the subscription and pick a service account — the
            endpoint refuses anything that is not signed by it. Then set{' '}
            <code>VISION_PUBSUB_AUDIENCE</code> and <code>VISION_PUBSUB_SA_EMAIL</code> on the API
            server to match. Both unset means every push is refused, which is deliberate.
          </div>
        </Section>
      )}

      {/* 5. Analyzers */}
      <Section title="5 · Edge analyzers"
        note="Video is never processed on our servers. A small box in each store holds the live feed and posts only derived numbers. Register one per store; the signing secret is shown once.">
        <div style={{ fontSize: 13.5, marginBottom: 10 }}>
          {status.edge_agents.total} registered · <b style={{ color: status.edge_agents.online ? '#16a34a' : '#f39c12' }}>
            {status.edge_agents.online} online</b>
          {status.edge_agents.last_ingest_at && <span style={{ color: 'var(--text3)' }}> · last data {fmtDateTime(status.edge_agents.last_ingest_at)}</span>}
        </div>
        <NewAgent canEdit={canEdit} stores={stores} onCreated={s => { setNewSecret(s); void load() }} />
        {agents.length > 0 && (
          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 12 }}>
            <thead><tr><th style={th}>Analyzer</th><th style={th}>Store</th><th style={th}>Last seen</th><th style={th}></th></tr></thead>
            <tbody>
              {agents.map(a => (
                <tr key={a.id}>
                  <td style={cell}>{a.label || a.agent_key}</td>
                  <td style={cell}>{a.store_code || '—'}</td>
                  <td style={cell}>
                    {a.awaiting_enrollment
                      ? <span style={{ color: '#f39c12' }}>waiting to enroll</span>
                      : a.last_seen_at ? fmtDateTime(a.last_seen_at) : 'never'}
                  </td>
                  <td style={cell}>
                    {/* A secret that has been pasted anywhere it should not be is only fixed by
                        replacing it. Rotating issues a new one and the old stops working at once. */}
                    <button style={btn} disabled={!canEdit || busy}
                      onClick={() => act(async () => setNewSecret(
                        await api(`/api/v1/vision/edge-agents/${a.id}/rotate`, { method: 'POST' })), '')}>
                      {a.awaiting_enrollment ? 'New code' : 'Rotate secret'}
                    </button>
                    <button style={{ ...btn, marginLeft: 6 }} disabled={!canEdit || busy}
                      onClick={() => act(() => api(`/api/v1/vision/edge-agents/${a.id}`, { method: 'DELETE' }), 'Analyzer removed.')}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {newSecret && (
          <div style={{ ...panel, marginTop: 10, borderLeft: '3px solid #16a34a' }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>
              Enrollment code for {newSecret.agent_key}
            </div>
            {/* A CODE, not a credential. It works once, expires in half an hour, and authorises
                nothing on its own — so it can be read aloud, typed at a store, or left on a screen
                without any of the consequences a signing secret would carry. */}
            <code style={{ display: 'block', fontSize: 20, letterSpacing: 2, fontWeight: 700, marginBottom: 8 }}>
              {newSecret.enroll_code}
            </code>
            <div style={{ fontSize: 12, marginBottom: 8 }}>
              On the analyzer machine, run this once:
              <code style={{ display: 'block', marginTop: 4, fontSize: 11.5, wordBreak: 'break-all' }}>
                python3 backend/vision_edge_analyzer.py --api {apiBase()} --enroll {newSecret.enroll_code}
              </code>
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>
              Works once, and expires
              {newSecret.expires_at ? ` at ${fmtDateTime(newSecret.expires_at)}` : ` in ${newSecret.ttl_minutes || 30} minutes`}.
              The machine mints its own signing secret and stores it owner-only — nobody has to
              handle it, and it is never shown here. If the code lapses, press Rotate secret for a
              new one.
              <div style={{ marginTop: 5 }}>
                No timezone argument: each camera&apos;s zone comes from its store, which is what lets one
                analyzer serve stores in different timezones. Check the machine first with{' '}
                <code>--benchmark</code>.
              </div>
            </div>
          </div>
        )}
      </Section>

      {/* 6. Consent */}
      <Section title="6 · Consent register"
        note="Who has agreed to have their speech transcribed. 'Not asked' is the answer that matters most, so everyone is listed — not only the people with a record.">
        <div style={{ fontSize: 13.5, marginBottom: 10 }}>
          {status.consent.signed} signed · {status.consent.declined} declined ·
          {' '}{status.consent.withdrawn} withdrawn
        </div>
        {consent.length > 0 && (
          <div style={{ maxHeight: 280, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr><th style={th}>Employee</th><th style={th}>Store</th><th style={th}>Consent</th><th style={th}>When</th></tr></thead>
              <tbody>
                {consent.map(r => (
                  <tr key={r.employee_id}>
                    <td style={cell}>{r.name}</td>
                    <td style={cell}>{r.store_code || '—'}</td>
                    <td style={{ ...cell, color: r.status === 'signed' ? '#16a34a' : r.status === 'not_asked' ? 'var(--text3)' : '#dc2626' }}>
                      {r.status.replace('_', ' ')}
                    </td>
                    <td style={cell}>{fmtDateTime(r.signed_at || r.withdrawn_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* 7. Retention */}
      <Section title="7 · Retention"
        note="Nothing is kept forever. Per-sample occupancy and transcripts expire quickly; the rolled-up aggregates carry no per-person detail and are kept for year-on-year comparison.">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 10 }}>
          <NumField label="Occupancy samples" value={cfg.presence_retention_days} disabled={!canEdit}
            onSave={v => saveConfig({ presence_retention_days: v })} />
          <NumField label="Voice transcripts" value={cfg.transcript_retention_days} disabled={!canEdit}
            onSave={v => saveConfig({ transcript_retention_days: v })} />
          <NumField label="Customer visits" value={cfg.visit_retention_days} disabled={!canEdit}
            onSave={v => saveConfig({ visit_retention_days: v })} />
          <NumField label="Heat map (aggregate)" value={cfg.heat_retention_days} disabled={!canEdit}
            onSave={v => saveConfig({ heat_retention_days: v })} />
        </div>
        <div style={{ marginTop: 10 }}>
          <button style={btn} disabled={!canEdit || busy}
            onClick={() => act(async () => {
              const p = await api('/api/v1/vision/retention/purge', { method: 'POST', body: JSON.stringify({}) })
              setMsg(`Dry run: ${p.total} row(s) are past their retention window. Purge again with confirm to delete.`)
            }, '')}>
            Preview purge
          </button>
          <button style={{ ...btn, marginLeft: 8 }} disabled={!canEdit || busy}
            onClick={() => act(() => api('/api/v1/vision/retention/purge', { method: 'POST', body: JSON.stringify({ confirm: true }) }), 'Expired data deleted.')}>
            Purge expired data
          </button>
        </div>
      </Section>
    </div>
  )
}

function GoogleLink({ canEdit, google, onSaved }: {
  canEdit: boolean; google: GoogleLinkState; onSaved: (m: string) => void
}) {
  // null means "not edited here" — the saved value shows through. That keeps the form honest with the
  // server after every reload without an effect copying state back and forth, and without a later
  // refresh wiping out something half-typed.
  const [projectEdit, setProjectEdit] = useState<string | null>(null)
  const [clientIdEdit, setClientIdEdit] = useState<string | null>(null)
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const project = projectEdit ?? (google.project_id || '')
  const clientId = clientIdEdit ?? (google.client_id || '')
  const form = { project, clientId, secret }
  const cantSave = idsBlocker(google, form)
  const cantAuthorize = authorizeBlocker(google, form)
  const dirty = (projectEdit !== null && projectEdit.trim() !== (google.project_id || '').trim())
    || (clientIdEdit !== null && clientIdEdit.trim() !== (google.client_id || '').trim())

  /** Save the ids alone. No secret required — this is the step that kept getting lost. */
  async function saveIds() {
    setBusy(true); setErr('')
    try {
      await api('/api/v1/vision/google/link', {
        method: 'POST',
        body: JSON.stringify({ project_id: project.trim(), client_id: clientId.trim() }),
      })
      setProjectEdit(null); setClientIdEdit(null)
      onSaved('Project id and client id saved. They stay saved — you can close this and come back.')
    } catch (e) { setErr(visionError(e)) }
    finally { setBusy(false) }
  }

  async function authorize() {
    setBusy(true); setErr('')
    try {
      // The secret goes up with this request and is stored encrypted, because Google needs it again
      // every time the access token is refreshed. It is never sent back down.
      await api('/api/v1/vision/google/link', {
        method: 'POST',
        body: JSON.stringify({ project_id: project.trim(), client_id: clientId.trim(), client_secret: secret.trim() }),
      })
      const r = await api(`/api/v1/vision/google/auth-url?redirect_uri=${encodeURIComponent(redirectUri())}`)
      // Same tab, on purpose. A new tab means the code comes back somewhere the operator was not
      // looking, and on a phone it means a tab they cannot easily get back to.
      window.location.href = r.url
    } catch (e: any) { setErr(visionError(e)); setBusy(false) }
  }

  if (google.linked) return <div style={{ fontSize: 12.5, color: 'var(--text3)' }}>
    The refresh token is stored encrypted and cannot be read back. To change accounts, disconnect and link again.
  </div>

  return (
    <div style={{ display: 'grid', gap: 8, maxWidth: 520 }}>
      {google.project_id && !dirty && (
        <div style={{ ...panel, fontSize: 12.5, borderLeft: '3px solid #f39c12' }}>
          Saved. This Google account has not authorized us yet — enter the client secret below and
          authorize. The ids do not need retyping.
        </div>
      )}
      <Text label="Device Access project id" value={project} onChange={setProjectEdit} disabled={!canEdit} />
      <Text label="OAuth client id" value={clientId} onChange={setClientIdEdit} disabled={!canEdit} />
      <div>
        <button style={btn} onClick={saveIds} disabled={!canEdit || busy || !!cantSave}>
          {busy ? 'Working…' : 'Save these two'}
        </button>
        {cantSave && canEdit && <span style={{ fontSize: 12, color: 'var(--text3)', marginLeft: 8 }}>{cantSave}</span>}
      </div>

      <div style={{ height: 1, background: 'var(--border)', margin: '4px 0' }} />

      <Text label="OAuth client secret" value={secret} onChange={setSecret} disabled={!canEdit} type="password" />
      <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>
        Typed here each time you authorize — never filled in for you.
        {google.has_secret && ' (One is already on file from a previous attempt.)'}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>
        Add <code>{redirectUri()}</code> as an authorized redirect URI on the OAuth client, or Google will
        refuse the consent step.
      </div>
      <div><button style={btnPrimary} onClick={authorize} disabled={!canEdit || busy || !!cantAuthorize}>
        {busy ? 'Working…' : 'Authorize with Google'}
      </button></div>
      {cantAuthorize && canEdit && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{cantAuthorize}</div>}
      {err && <div style={{ color: '#dc2626', fontSize: 12.5 }}>{err}</div>}
    </div>
  )
}


function StorePick({ stores, value, onPick, disabled, emptyLabel }: {
  stores: StoreOption[] | null
  value?: string | null
  onPick: (v: string) => void
  disabled?: boolean
  emptyLabel?: string
}) {
  const box: React.CSSProperties = {
    minWidth: 130, padding: '4px 7px', borderRadius: 5, border: '1px solid var(--border)',
    background: 'var(--surface)', color: 'var(--text)', fontSize: 12.5,
  }
  // No store list (endpoint down, or a login with no store access): fall back to typing rather than
  // showing a dropdown with nothing in it. A degraded control beats an unusable one.
  if (stores === null) {
    return <input defaultValue={value || ''} placeholder="store code" disabled={disabled}
      onBlur={e => e.target.value !== (value || '') && onPick(e.target.value.trim())} style={box} />
  }
  const opts = withCurrent(stores, value)
  return (
    <select value={value || ''} disabled={disabled} style={box}
      onChange={e => e.target.value !== (value || '') && onPick(e.target.value)}>
      <option value="">{emptyLabel || '— unassigned —'}</option>
      {opts.map(o => <option key={o.code} value={o.code}>{o.label}</option>)}
    </select>
  )
}


function NewAgent({ canEdit, onCreated, stores }: {
  canEdit: boolean; onCreated: (s: any) => void; stores: StoreOption[] | null
}) {
  const [label, setLabel] = useState('')
  const [store, setStore] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
      <input placeholder="Label (e.g. Main St analyzer)" value={label} onChange={e => setLabel(e.target.value)}
        style={{ padding: '6px 9px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />
      <StorePick stores={stores} value={store} disabled={!canEdit}
        emptyLabel="— pick a store —" onPick={setStore} />
      <button style={btn} disabled={!canEdit || busy || !store} onClick={async () => {
        setBusy(true)
        try { onCreated(await api('/api/v1/vision/edge-agents', { method: 'POST', body: JSON.stringify({ label, store_code: store }) })) }
        finally { setBusy(false) }
      }}>Register analyzer</button>
    </div>
  )
}

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <div style={{ ...panel, marginBottom: 14 }}>
      <div style={{ fontWeight: 700, fontSize: 15, marginBottom: note ? 4 : 10 }}>{title}</div>
      {note && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>{note}</div>}
      {children}
    </div>
  )
}

function Toggle({ label, checked, onChange, disabled, hint }: {
  label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; hint?: string
}) {
  return (
    <label style={{ display: 'block', cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.55 : 1, marginBottom: 6 }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <input type="checkbox" checked={!!checked} disabled={disabled} onChange={e => onChange(e.target.checked)} />
        <span style={{ fontSize: 13.5, fontWeight: 500 }}>{label}</span>
      </span>
      {hint && <span style={{ fontSize: 11.5, color: 'var(--text3)', marginLeft: 24 }}>{hint}</span>}
    </label>
  )
}

function Check({ checked, onChange, disabled }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return <input type="checkbox" checked={!!checked} disabled={disabled} onChange={e => onChange(e.target.checked)} />
}

function Text({ label, value, onChange, disabled, type }: {
  label: string; value: string; onChange: (v: string) => void; disabled?: boolean; type?: string
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text3)' }}>{label}</span>
      <input type={type || 'text'} value={value} disabled={disabled} onChange={e => onChange(e.target.value)}
        style={{ padding: '6px 9px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />
    </label>
  )
}

function NumField({ label, value, onSave, disabled }: {
  label: string; value: number; onSave: (v: number) => void; disabled?: boolean
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text3)' }}>{label}</span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <input type="number" min={0} defaultValue={value} disabled={disabled}
          onBlur={e => Number(e.target.value) !== value && onSave(Number(e.target.value))}
          style={{ width: 78, padding: '6px 9px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>days</span>
      </span>
    </label>
  )
}
