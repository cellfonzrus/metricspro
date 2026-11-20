'use client'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, apiUpload } from '@/lib/client'
import DateField from '@/components/DateField'
import { readUploadOutcome, type UploadOutcome } from '../_lib/uploadGuard'
import { safeHref } from '@/lib/safe-url'
import { usePosTerm } from '@/lib/report-labels'
import { scopeState, connectorNotApplicableCopy, type ConnectorScope } from '@/lib/connectors'

// ════════════════════════════════════════════════════════════════════════════════════════════════
// PER-STORE DOWNLOAD CONSOLE (index §COMMCALC-82).
//
// Owner, 2026-11-20: "build these into the system as a browser in browser to download manually so we
// pick each store from the drop down menu and then the browser opens up and we down load the file".
//
// WHY THE PORTAL OPENS IN ITS OWN TAB AND NOT INSIDE THIS PAGE. A back office cannot be framed: it
// sends the headers that forbid it (and the appliance in front of this one refuses automated clients
// outright — §COMMCALC-74/75), so an <iframe> would render an empty box and read as a broken screen.
// So the portal opens in a tab of the SAME browser window, and this page keeps the two things a tab
// cannot: which store the operator is working, and which store-days are still outstanding. Nothing
// here claims to be the portal.
//
// WHAT THIS SCREEN IS NOT. It is not a second ingest. A file dropped here goes to the SAME upload
// door the automated pull lands through (the report's own `upload_path`, declared by the server), so
// a hand-fetched day and a pulled day are indistinguishable once landed.
//
// NO TENANT OR VENDOR NAME IS WRITTEN HERE. The stores, the portal address, the report names and the
// upload door all arrive from the server as this tenant's own declarations.
// ════════════════════════════════════════════════════════════════════════════════════════════════

type CellState = 'have' | 'missing' | 'unknown'
interface Cell { day: string; state: CellState; lines?: number; payment?: number | null; fee?: number | null }
interface StoreRow { store: string; days: Cell[]; have: number; missing: number; complete: boolean }
interface Coverage {
  stores: StoreRow[]; days: string[]; have: number; missing: number
  measured: boolean; note: string; undeclared_stores: string[]
}
interface ReportEntry { key: string; label: string; target: string; arrival_type: string; upload_path: string }
interface Login {
  id: string; processor: string; label: string | null; store_code: string; username: string | null
  portal_url: string | null; enabled: boolean | null; has_password: boolean
  // Whether this login's CONNECTOR is offered to this tenant at all (§CORE-41, mig 1014). The
  // backend resolves it from the registry; this screen reads it and never decides it.
  connector_scope: ConnectorScope | null
  reports: ReportEntry[]
}
interface Payload {
  measured: boolean; logins: Login[]; coverage: Coverage
  date_from: string; date_to: string; note: string
}

// Every colour here is a TOKEN (§CORE-15: one palette, defined for both themes). A literal would
// read correctly in the light theme and be invisible in the dark one — and the build says so.
const STATE_STYLE: Record<CellState, { bg: string; fg: string; mark: string; title: string }> = {
  have:    { bg: 'var(--c-bg-d1fae5)', fg: 'var(--c-fg-15803d)', mark: '●', title: 'Landed' },
  missing: { bg: 'var(--c-bg-fef3c7)', fg: 'var(--c-fg-92400e)', mark: '○', title: 'Still to fetch' },
  // "I could not look" is NOT "there is nothing there": the two send an operator to do opposite
  // things, so they never share a colour or a mark on this grid.
  unknown: { bg: 'var(--surface2)', fg: 'var(--text3)', mark: '?', title: 'Not measured — the feed could not be read' },
}

/** The server's own sentence for a refusal, never a guess at one. */
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e || ''))

const card: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 14,
}

export default function PerStoreDownloadsPage() {
  const { pos, posDeclared } = usePosTerm()   // the tenant's own POS word, for the registry's sentence
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [storeCode, setStoreCode] = useState('')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [outcome, setOutcome] = useState<UploadOutcome | null>(null)
  const [copied, setCopied] = useState('')
  // The password the owner asked to SEE (2026-11-20). Held in state for this one store, cleared the
  // moment the store changes or the minute is up — a credential on screen is a credential someone
  // can walk past, so it leaves by itself rather than waiting to be dismissed.
  const [shown, setShown] = useState('')
  const [shownFor, setShownFor] = useState('')
  const [revealErr, setRevealErr] = useState('')
  const fileRef = useRef<HTMLInputElement | null>(null)

  const load = useCallback((from?: string, to?: string) => {
    setLoading(true); setErr('')
    const qs = new URLSearchParams()
    if (from) qs.set('date_from', from)
    if (to) qs.set('date_to', to)
    api(`/api/v1/commcalc/per-store-downloads${qs.toString() ? '?' + qs.toString() : ''}`)
      .then((d: Payload) => {
        setData(d)
        setDateFrom(d.date_from); setDateTo(d.date_to)
        setStoreCode(prev => prev || (d.logins[0]?.store_code ?? ''))
      })
      .catch((e: unknown) => setErr(errText(e) || 'Could not load'))
      .finally(() => setLoading(false))
  }, [])

  // The first load runs from a timer, not from the effect body: calling it synchronously there sets
  // state during the effect, which React (and the lint rule that pins it) treats as a cascading
  // render. The cleanup cancels it, so a fast unmount never fires a request nobody is waiting for.
  useEffect(() => {
    const t = setTimeout(() => load(), 0)
    return () => clearTimeout(t)
  }, [load])

  const login = useMemo(
    () => (data?.logins || []).find(l => l.store_code === storeCode) || null,
    [data, storeCode])
  const storeRow = useMemo(
    () => (data?.coverage.stores || []).find(s => s.store === storeCode) || null,
    [data, storeCode])
  // The report a dropped file arrives under. A login may declare several; the first with an arrival
  // door is the one this screen posts to, and a login whose reports have none says so instead.
  const report = useMemo(
    () => (login?.reports || []).find(r => r.upload_path) || null, [login])
  // A connector the registry does not offer this tenant is not fed from here: the login is still
  // listed (it exists, and hiding it would be its own kind of lie), but it carries the registry's
  // own sentence and nothing to press. The decision is the registry's; this screen only reads it.
  const withheld = login ? scopeState(login.connector_scope, true) === 'not_defined' : false

  // A password on screen belongs to the store it was asked for, so switching stores hides it. Done
  // in the ONE place the store changes rather than in an effect watching it: an effect that sets
  // state while reacting to state is the cascading render the lint rule pins.
  const pickStore = (code: string) => {
    setStoreCode(code); setShown(''); setShownFor(''); setRevealErr('')
  }

  useEffect(() => {
    if (!shown) return
    const t = setTimeout(() => { setShown(''); setShownFor('') }, 60_000)
    return () => clearTimeout(t)
  }, [shown])

  async function reveal() {
    if (!login) return
    setRevealErr('')
    try {
      const res = await api(`/api/v1/commcalc/data-sources/${login.id}/reveal-password`,
        { method: 'POST', body: JSON.stringify({ context: 'per-store download screen' }) })
      setShown(String(res?.password || '')); setShownFor(login.store_code)
    } catch (e: unknown) {
      // The server's own sentence — it says WHY (no record could be written, nothing stored, not
      // allowed) and each of those needs a different thing done about it.
      setRevealErr(errText(e) || 'The password could not be shown.')
    }
  }

  // The portal address is a DECLARED value, so it goes through the house's one home for "is this
  // href safe to follow" before a new tab is opened on it — a `javascript:` address typed into a
  // settings field would otherwise run in a window that holds this session.
  const portalHref = safeHref(login?.portal_url)

  function openPortal() {
    if (!portalHref) return
    window.open(portalHref, '_blank', 'noopener,noreferrer')
  }

  async function copy(text: string, what: string) {
    try { await navigator.clipboard.writeText(text); setCopied(what) } catch { setCopied('') }
    setTimeout(() => setCopied(''), 2500)
  }

  async function send(file: File) {
    if (!login || !report?.upload_path) return
    setBusy(file.name); setMsg(''); setOutcome(null)
    const form = new FormData(); form.append('file', file)
    try {
      const qs = new URLSearchParams({ store_code: login.store_code, trace_source: 'manual' })
      const res = await apiUpload(`${report.upload_path}?${qs.toString()}`, form)
      const o = readUploadOutcome(res, 'transaction row(s)')
      setOutcome(o)
      setMsg((o.tone === 'ok' ? '✅ ' : '⚠️ ') + o.text)
      load(dateFrom, dateTo)   // the grid is the receipt: the day must turn green by itself
    } catch (e: unknown) {
      setMsg('❌ ' + (errText(e) || 'Upload failed'))
    }
    setBusy('')
    if (fileRef.current) fileRef.current.value = ''
  }

  const cov = data?.coverage
  return (
    <div style={{ padding: 20, maxWidth: 1180, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, margin: '0 0 4px' }}>Per-store report download</h1>
      <p style={{ color: 'var(--text3)', fontSize: 13, margin: '0 0 16px', maxWidth: 860 }}>
        Pick a store, open its back office in a new tab, download the day you need, then drop the file
        here. It lands exactly where an automatic pull would land it, so the reconciliation cannot tell
        the difference. The grid below shows which store-days have already arrived.
      </p>

      {err && <div style={{ ...card, borderColor: 'var(--red)', marginBottom: 14 }}>❌ {err}</div>}

      {data && !data.measured && (
        <div style={{ ...card, borderColor: 'var(--c-fg-92400e)', marginBottom: 14 }}>
          ⚠️ {data.note}
        </div>
      )}
      {data?.measured && !data.logins.length && (
        <div style={{ ...card, marginBottom: 14 }}>
          {data.note}{' '}
          <a href="/commcalc/email-imports#portal-logins">Open the portal-logins screen →</a>
        </div>
      )}

      <div style={{ ...card, display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 14 }}>
        <label style={{ fontSize: 12 }}>Store
          <select value={storeCode} onChange={e => pickStore(e.target.value)}
                  style={{ display: 'block', minWidth: 260, padding: '7px 9px', marginTop: 4,
                           borderRadius: 7, border: '1px solid var(--border)', fontSize: 14 }}>
            <option value="">— pick a store —</option>
            {(data?.logins || []).map(l => (
              <option key={l.id} value={l.store_code}>
                {l.store_code}{l.label ? ' — ' + l.label : ''}
              </option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 12 }}>From
          <DateField value={dateFrom} onChange={e => setDateFrom(e.target.value)}
                     style={{ display: 'block', padding: '7px 9px', marginTop: 4, borderRadius: 7,
                              border: '1px solid var(--border)', fontSize: 14 }} />
        </label>
        <label style={{ fontSize: 12 }}>To
          <DateField value={dateTo} onChange={e => setDateTo(e.target.value)}
                     style={{ display: 'block', padding: '7px 9px', marginTop: 4, borderRadius: 7,
                              border: '1px solid var(--border)', fontSize: 14 }} />
        </label>
        <button onClick={() => load(dateFrom, dateTo)} disabled={loading}
                style={{ padding: '8px 14px', borderRadius: 7, border: '1px solid var(--border)',
                         cursor: 'pointer', fontSize: 13 }}>
          {loading ? 'Loading…' : 'Show these days'}
        </button>
      </div>

      {login && (
        <div style={{ ...card, marginBottom: 14 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            <button onClick={openPortal} disabled={!portalHref || withheld}
                    style={{ padding: '10px 16px', borderRadius: 8, border: 'none', cursor: 'pointer',
                             background: 'var(--accent)', color: 'var(--c-fg-ffffff)', fontSize: 14 }}>
              🌐 Open {login.store_code} back office in a new tab
            </button>
            {login.username && (
              <button onClick={() => copy(login.username as string, 'username')}
                      style={{ padding: '8px 12px', borderRadius: 7, border: '1px solid var(--border)',
                               cursor: 'pointer', fontSize: 13 }}>
                {copied === 'username' ? '✓ Copied' : `Copy username (${login.username})`}
              </button>
            )}
            {login.has_password ? (
              shown && shownFor === login.store_code ? (
                <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
                  <code style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
                                 background: 'var(--surface2)', userSelect: 'all' }}>{shown}</code>
                  <button onClick={() => copy(shown, 'password')}
                          style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
                                   cursor: 'pointer', fontSize: 12 }}>
                    {copied === 'password' ? '✓ Copied' : 'Copy'}
                  </button>
                  <button onClick={() => { setShown(''); setShownFor('') }}
                          style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
                                   cursor: 'pointer', fontSize: 12 }}>Hide</button>
                  <span style={{ fontSize: 11, color: 'var(--text3)' }}>
                    Recorded, and hidden again in a minute.
                  </span>
                </span>
              ) : (
                <button onClick={reveal}
                        style={{ padding: '8px 12px', borderRadius: 7, border: '1px solid var(--border)',
                                 cursor: 'pointer', fontSize: 13 }}>
                  Show password
                </button>
              )
            ) : (
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>
                No password is stored for this login yet.
              </span>
            )}
          </div>
          {withheld && (
            <p style={{ fontSize: 13, color: 'var(--text2)', margin: '10px 0 0' }}>
              🔌 {connectorNotApplicableCopy({ scope: login.connector_scope, pos, posDeclared })}
            </p>
          )}
          {revealErr && (
            <p style={{ fontSize: 12, color: 'var(--c-fg-92400e)', margin: '10px 0 0' }}>⚠️ {revealErr}</p>
          )}
          {!portalHref && (
            <p style={{ fontSize: 12, color: 'var(--c-fg-92400e)', margin: '10px 0 0' }}>
              {login.portal_url
                ? 'This login’s portal address is not a web address this platform will open. Correct it on the portal-logins screen.'
                : 'This login has no portal address declared, so there is nothing to open. Add it on the portal-logins screen.'}
            </p>
          )}
          {storeRow && cov?.measured && (
            <p style={{ fontSize: 13, margin: '12px 0 0' }}>
              {storeRow.missing === 0
                ? `Every day in this range has landed for ${storeRow.store}.`
                : `${storeRow.missing} day(s) of ${storeRow.days.length} still to fetch for ${storeRow.store}: ` +
                  storeRow.days.filter(c => c.state === 'missing').map(c => c.day).join(', ')}
            </p>
          )}
        </div>
      )}

      {login && (
        <div style={{ ...card, marginBottom: 18 }}>
          <h2 style={{ fontSize: 15, margin: '0 0 8px' }}>Drop the downloaded file here</h2>
          {withheld ? (
            <p style={{ fontSize: 13, color: 'var(--text2)', margin: 0 }}>
              This login’s connector is not offered to this tenant, so a file dropped here would have
              nowhere to land.
            </p>
          ) : report ? (
            <>
              <input ref={fileRef} type="file" accept=".csv,.xls,.xlsx,.txt"
                     disabled={!!busy}
                     onChange={e => { const f = e.target.files?.[0]; if (f) send(f) }} />
              <p style={{ fontSize: 12, color: 'var(--text3)', margin: '8px 0 0' }}>
                Arrives as “{report.label}” for store {login.store_code}. Re-uploading the same day
                adds nothing — each transaction lands once.
              </p>
            </>
          ) : (
            <p style={{ fontSize: 13, color: 'var(--c-fg-92400e)', margin: 0 }}>
              No report on this login declares where its rows land, so a file dropped here could not
              be stored. Declare the report (and its landing target) on the portal-logins screen
              first — then this box accepts the file.
            </p>
          )}
          {busy && <p style={{ fontSize: 13 }}>Uploading {busy}…</p>}
          {msg && <p style={{ fontSize: 13, marginBottom: 0 }}>{msg}</p>}
          {outcome?.reason && (
            <p style={{ fontSize: 12, color: 'var(--c-fg-92400e)', margin: '4px 0 0' }}>{outcome.reason}</p>
          )}
        </div>
      )}

      <div style={card}>
        <h2 style={{ fontSize: 15, margin: '0 0 2px' }}>What has landed, per store and day</h2>
        {cov && !cov.measured ? (
          <p style={{ fontSize: 13, color: 'var(--c-fg-92400e)' }}>{cov.note}</p>
        ) : (
          <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>
            {cov?.have ?? 0} store-day(s) landed, {cov?.missing ?? 0} still to fetch.
            Click a day to work that store.
          </p>
        )}
        {cov?.undeclared_stores?.length ? (
          <p style={{ fontSize: 12, color: 'var(--c-fg-92400e)' }}>
            Rows have landed for store code(s) no login declares: {cov.undeclared_stores.join(', ')}.
            A file uploaded under the wrong store code looks like this.
          </p>
        ) : null}
        {cov && cov.stores.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', padding: '6px 10px', position: 'sticky', left: 0,
                               background: 'var(--surface)' }}>Store</th>
                  {cov.days.map(d => (
                    <th key={d} style={{ padding: '6px 4px', fontWeight: 500, whiteSpace: 'nowrap' }}>
                      {d.slice(5)}
                    </th>
                  ))}
                  <th style={{ padding: '6px 10px' }}>To fetch</th>
                </tr>
              </thead>
              <tbody>
                {cov.stores.map(s => (
                  <tr key={s.store} style={{ background: s.store === storeCode ? 'var(--surface2)' : undefined }}>
                    <td style={{ padding: '4px 10px', whiteSpace: 'nowrap', position: 'sticky', left: 0,
                                 background: 'var(--surface)' }}>
                      <button onClick={() => pickStore(s.store)}
                              style={{ border: 'none', background: 'none', cursor: 'pointer',
                                       padding: 0, font: 'inherit', textDecoration: 'underline' }}>
                        {s.store}
                      </button>
                    </td>
                    {s.days.map(c => {
                      const st = STATE_STYLE[c.state]
                      return (
                        <td key={c.day} style={{ padding: 2 }}>
                          <button onClick={() => pickStore(s.store)}
                                  title={`${s.store} ${c.day} — ${st.title}${c.lines ? ` (${c.lines} lines)` : ''}`}
                                  style={{ width: 26, height: 22, borderRadius: 4, cursor: 'pointer',
                                           border: '1px solid var(--border)', background: st.bg,
                                           color: st.fg, fontSize: 11 }}>
                            {st.mark}
                          </button>
                        </td>
                      )
                    })}
                    <td style={{ padding: '4px 10px', textAlign: 'right' }}>
                      {cov.measured ? s.missing : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 0 }}>
          ● landed · ○ still to fetch · ? not measured (the feed could not be read, so no day is
          reported as missing)
        </p>
      </div>
    </div>
  )
}
