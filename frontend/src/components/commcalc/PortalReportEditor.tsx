'use client'
import { Fragment, useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { useConnectors } from '@/lib/connectors'

// WHICH REPORTS THIS LOGIN PULLS, AND HOW — edited here, stored as config (index §COMMCALC-48).
//
// Nothing about a tenant's reports is in the code: the rows below ARE the declaration, read from and
// written to `data_source.portal_reports` + `portal_calibration` through
// GET/PUT /commcalc/data-sources/{id}/reports. The backend derives these rows from the two columns
// the pull already reads and derives them back on save, so what you see is what the pull will run.
//
// The connector this login is for is named as the REGISTRY names it (@/lib/connectors), never as the
// page that opened this modal happens to spell it — one connector wearing two names on two screens is
// the defect that hook exists to stop (harness_connector_scope_lock.py fails the build if a connector
// surface keeps its own list).
//
// "Read this page" asks the live session to look at the portal and SUGGEST each report's filters
// (§COMMCALC-39). It never overwrites a selector somebody has corrected — a capture is evidence about
// a page, not a decision about this tenant's configuration.

type Param = { selector: string; kind: string; value: string | null; label: string }
type Row = {
  key: string; label: string; nav: string[]; export: string
  params: Param[]; line_map?: Record<string, string>
  per_day?: boolean; problems?: string[]
}
type Loaded = {
  source_id: string; label: string; processor: string; portal_url: string
  reports: Row[]; param_kinds: string[]; date_tokens: string[]; exports: string[]
}
type Saved = { reports: Row[]; count: number }
/** What the §COMMCALC-39 capture answers with.
 *
 *  THE ROWS COME BACK ALREADY MERGED. This used to be a list of suggestions that this component
 *  folded into its own rows — a second copy of `report_declaration.merge_suggestion`, which is the
 *  declared home for that fold and had no caller. The two diverged where it mattered: a per-report
 *  result names its report `report` (the backend's convention for every per-report result), and the
 *  copy here matched on `key`, the spelling an editor ROW uses, so `find(x => x.key === row.key)`
 *  never matched and a capture of fourteen report pages added nothing while announcing it had read
 *  them. The merge now happens once, server-side, through `pos_report_sweep.spec_key`; this file
 *  displays what came back and never re-decides it.
 *
 *  `capture` is what each page actually said (§COMMCALC-44: an absence needs the right reason). */
type CaptureRow = {
  report?: string; label?: string | null; reached?: boolean; controls_seen?: number | null
  suggested?: number; added?: number; error?: string | null
  unrecognised?: { label?: string | null; selector?: string | null; why?: string | null }[]
}
type Capture = {
  ok?: boolean; error?: string; reports?: Row[]; capture?: CaptureRow[]
  reached?: number; pages?: number; added?: number; note?: string
}

/** The message off a thrown value, without widening it to `any` first. */
function why(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

const BOX: React.CSSProperties = {
  width: '100%', padding: '4px 6px', fontSize: 12, border: '1px solid var(--border)',
  borderRadius: 6, background: 'var(--surface)', color: 'var(--text)',
}

export default function PortalReportEditor(
  { sourceId, label, processor, onClose }:
  { sourceId: string; label: string; processor?: string | null; onClose: () => void },
) {
  const connectors = useConnectors()
  const [meta, setMeta] = useState<Loaded | null>(null)
  const [rows, setRows] = useState<Row[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)   // the key whose filters are expanded
  // What each report page said on the last capture — shown rather than summed away (§COMMCALC-44).
  const [capture, setCapture] = useState<CaptureRow[] | null>(null)

  // Every state write happens in the fetch's own CALLBACK, never in the effect body: a setState run
  // synchronously inside an effect cascades a render for nothing. `alive` drops a reply that arrives
  // after the modal has closed, so a slow read cannot write into an unmounted editor.
  useEffect(() => {
    let alive = true
    api(`/api/v1/commcalc/data-sources/${sourceId}/reports`)
      .then(r => { if (!alive) return; const d = r as Loaded; setErr(null); setMeta(d); setRows(d.reports || []) })
      .catch((e: unknown) => { if (alive) setErr(why(e)) })
    return () => { alive = false }
  }, [sourceId])

  function patch(i: number, next: Partial<Row>) {
    setRows(rs => rs.map((r, k) => (k === i ? { ...r, ...next } : r)))
  }
  function patchParam(i: number, j: number, next: Partial<Param>) {
    setRows(rs => rs.map((r, k) => (k === i
      ? { ...r, params: r.params.map((p, m) => (m === j ? { ...p, ...next } : p)) } : r)))
  }

  async function save() {
    setBusy('save'); setErr(null); setNote(null); setCapture(null)
    try {
      const r = await api(`/api/v1/commcalc/data-sources/${sourceId}/reports`,
        { method: 'PUT', body: JSON.stringify({ reports: rows }) }) as Saved
      setRows(r.reports || [])
      setNote(`Saved — this login now pulls ${r.count} report${r.count === 1 ? '' : 's'}.`)
    } catch (e: unknown) { setErr(why(e)) } finally { setBusy(null) }
  }

  // The capture runs on the live session, so it needs one to be open and signed in. What comes back
  // is this login's rows with every suggestion ALREADY folded in by the one function that knows how
  // (`report_declaration.merge_suggestion`, server-side) — nothing here re-merges, and nothing is
  // saved until Save. The rows on screen are sent along so an unsaved edit is merged into, not over.
  async function readPage() {
    setBusy('read'); setErr(null); setNote(null); setCapture(null)
    try {
      const r = await api(`/api/v1/commcalc/data-sources/${sourceId}/live-login/calibrate`,
        { method: 'POST', body: JSON.stringify({ reports: rows }) }) as Capture
      if (r?.ok === false) { setErr(r.error || 'The report pages could not be read.'); return }
      if (r.reports) setRows(r.reports)
      setCapture(r.capture || [])
      const pages = r.pages ?? 0, reached = r.reached ?? 0, added = r.added ?? 0
      setNote(`Reached ${reached} of ${pages} report page${pages === 1 ? '' : 's'} and added `
        + `${added} filter${added === 1 ? '' : 's'}. ${r.note || ''}`.trim())
    } catch (e: unknown) { setErr(why(e)) } finally { setBusy(null) }
  }

  // the registry's own name for this connector — blank until the registry has loaded, and blank for a
  // login whose processor the registry does not carry, because a lookup that fails must not invent one
  const connectorName = connectors.byKey(processor || meta?.processor || '')?.label || null

  const kinds = meta?.param_kinds || ['fill', 'select', 'check', 'uncheck', 'click']
  const exports = meta?.exports || ['csv']
  const token = (meta?.date_tokens || ['{date}'])[0]

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.45)', zIndex: 70,
      display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: 24, overflow: 'auto' }}>
      <div className="card" style={{ padding: 18, maxWidth: 1040, width: '100%' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
          <div style={{ fontWeight: 700, fontSize: 15 }}>
            📄 Reports this login pulls — <span style={{ fontWeight: 400 }}>{label}</span>
            {connectorName && <span style={{ fontWeight: 400, color: 'var(--text3)' }}> · {connectorName}</span>}
          </div>
          <span style={{ flex: 1 }} />
          <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={!!busy}
                  onClick={readPage}>{busy === 'read' ? 'Reading…' : '👁 Read this page'}</button>
          <button className="btn" style={{ fontSize: 12 }} disabled={!!busy} onClick={save}>
            {busy === 'save' ? 'Saving…' : '💾 Save'}
          </button>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={onClose}>Close</button>
        </div>
        <div style={{ color: 'var(--text3)', fontSize: 12, marginBottom: 10 }}>
          Each row is one report on the portal. Nothing here is built into the software — add, change or
          remove rows and press Save, and the daily pull follows them. Use <code>{token}</code> as a date
          value and the report runs once per day.
        </div>

        {err && <div style={{ background: 'var(--c-bg-fef2f2)', color: 'var(--c-fg-991b1b)', borderRadius: 8, padding: '8px 10px',
          fontSize: 12, marginBottom: 10 }}>{err}</div>}
        {note && <div style={{ background: 'var(--c-bg-f0fdf4)', color: 'var(--c-fg-166534)', borderRadius: 8, padding: '8px 10px',
          fontSize: 12, marginBottom: 10 }}>{note}</div>}

        {/* WHAT EACH PAGE SAID. A capture that reached a page and found no filter, a capture that
            could not reach the page at all, and a capture that saw controls whose purpose the page
            does not state are three different answers — summing them to "read 14 pages" is the
            absence-with-no-reason §COMMCALC-44 names. Each line says which one happened, and the
            controls nobody could name are listed so a human can declare them by hand. */}
        {capture && capture.length > 0 && (
          <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px',
            fontSize: 12, marginBottom: 10 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>What each report page said</div>
            {capture.map((c, i) => (
              <div key={c.report || i} style={{ borderTop: i ? '1px solid var(--border)' : undefined, padding: '4px 0' }}>
                <span style={{ fontWeight: 600 }}>{c.label || c.report}</span>
                {' — '}
                {c.reached
                  ? <span>reached it{typeof c.controls_seen === 'number' ? `, ${c.controls_seen} control${c.controls_seen === 1 ? '' : 's'} on the page` : ''}
                      {`, ${c.suggested ?? 0} filter${(c.suggested ?? 0) === 1 ? '' : 's'} recognised, ${c.added ?? 0} added`}</span>
                  : <span style={{ color: 'var(--c-fg-991b1b)' }}>could not reach it</span>}
                {c.error && <div style={{ color: 'var(--text3)' }}>{c.error}</div>}
                {(c.unrecognised || []).length > 0 && (
                  <div style={{ color: 'var(--text3)', marginTop: 2 }}>
                    Controls whose purpose this page does not state — declare them by hand if you need them:
                    <ul style={{ margin: '2px 0 0 16px' }}>
                      {(c.unrecognised || []).map((u, j) => (
                        <li key={j}><code style={{ fontFamily: 'monospace' }}>{u.selector}</code>
                          {u.label ? ` · ${u.label}` : ''}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {!meta ? <div style={{ color: 'var(--text3)', fontSize: 13 }}>Loading…</div> : (
          <>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              {/* These cells LABEL the columns of a form; they are deliberately not a sortable
                  header row, and so not marked up as one (harness_table_sort_lock: "a DATA table has
                  a header row to click"). The row order here IS part of the configuration — it is the
                  order the pull runs the reports in — so offering a sort would present a tenant's
                  declaration as one view of it and lose what the order means. Nothing to sort. */}
              <tbody>
                <tr style={{ fontSize: 11, color: 'var(--text3)', textAlign: 'left' }}>
                  <td style={{ padding: '4px 6px' }}>Report name</td>
                  <td style={{ padding: '4px 6px' }}>Menu words that reach it</td>
                  <td style={{ padding: '4px 6px', width: 90 }}>Export</td>
                  <td style={{ padding: '4px 6px', width: 86 }}>Filters</td>
                  <td style={{ padding: '4px 6px', width: 78 }}>Per day</td>
                  <td style={{ padding: '4px 6px', width: 40 }} />
                </tr>
                {rows.map((r, i) => (
                  <Fragment key={r.key || i}>
                    <tr style={{ borderTop: '1px solid var(--border)', fontSize: 12 }}>
                      <td style={{ padding: '5px 6px' }}>
                        <input style={BOX} value={r.label}
                               onChange={e => patch(i, { label: e.target.value, key: r.key || e.target.value })} />
                        <div style={{ color: 'var(--text3)', fontSize: 10, marginTop: 2 }}>{r.key}</div>
                      </td>
                      <td style={{ padding: '5px 6px' }}>
                        <input style={BOX} value={(r.nav || []).join(' > ')} placeholder="Reporting > POS > Item Sales"
                               onChange={e => patch(i, { nav: e.target.value.split('>').map(w => w.trim()).filter(Boolean) })} />
                      </td>
                      <td style={{ padding: '5px 6px' }}>
                        <select style={BOX} value={r.export} onChange={e => patch(i, { export: e.target.value })}>
                          {exports.map(x => <option key={x} value={x}>{x}</option>)}
                        </select>
                      </td>
                      <td style={{ padding: '5px 6px' }}>
                        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 7px' }}
                                onClick={() => setOpen(open === r.key ? null : r.key)}>
                          {(r.params || []).length} {open === r.key ? '▴' : '▾'}
                        </button>
                      </td>
                      <td style={{ padding: '5px 6px', color: 'var(--text3)' }}>{r.per_day ? 'yes' : 'no'}</td>
                      <td style={{ padding: '5px 6px', textAlign: 'right' }}>
                        <button className="btn btn-secondary" title="Remove this report"
                                style={{ fontSize: 11, padding: '2px 7px' }}
                                onClick={() => setRows(rs => rs.filter((_, k) => k !== i))}>✕</button>
                      </td>
                    </tr>
                    {(r.problems || []).length > 0 && (
                      <tr>
                        <td colSpan={6} style={{ padding: '0 6px 5px 6px', fontSize: 11, color: 'var(--c-fg-b45309)' }}>
                          {(r.problems || []).join(' · ')}
                        </td>
                      </tr>
                    )}
                    {open === r.key && (
                      <tr>
                        <td colSpan={6} style={{ padding: '0 6px 10px 6px' }}>
                          <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 8 }}>
                            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6 }}>
                              What must be set on this report&apos;s page before the export is pressed. A filter
                              that cannot be set stops the export rather than exporting the wrong figures.
                            </div>
                            {(r.params || []).map((p, j) => (
                              <div key={j} style={{ display: 'flex', gap: 6, marginBottom: 5 }}>
                                <input style={{ ...BOX, flex: 2 }} value={p.label} placeholder="What it is (From date)"
                                       onChange={e => patchParam(i, j, { label: e.target.value })} />
                                <input style={{ ...BOX, flex: 2, fontFamily: 'monospace' }} value={p.selector}
                                       placeholder="#the-control-on-the-page"
                                       onChange={e => patchParam(i, j, { selector: e.target.value })} />
                                <select style={{ ...BOX, flex: 1 }} value={p.kind}
                                        onChange={e => patchParam(i, j, { kind: e.target.value })}>
                                  {kinds.map(k => <option key={k} value={k}>{k}</option>)}
                                </select>
                                <input style={{ ...BOX, flex: 2 }} value={p.value ?? ''} placeholder={token}
                                       onChange={e => patchParam(i, j, { value: e.target.value })} />
                                <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 7px' }}
                                        onClick={() => patch(i, { params: r.params.filter((_, m) => m !== j) })}>✕</button>
                              </div>
                            ))}
                            <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }}
                                    onClick={() => patch(i, { params: [...(r.params || []),
                                      { label: '', selector: '', kind: 'fill', value: '' }] })}>+ filter</button>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
            {rows.length === 0 && (
              <div style={{ color: 'var(--text3)', fontSize: 12, padding: '10px 6px' }}>
                No report is declared on this login yet, so the daily pull has nothing to fetch. Add one
                below — or open the live login, sign in, and press Read this page.
              </div>
            )}
            <div style={{ marginTop: 10 }}>
              <button className="btn btn-secondary" style={{ fontSize: 12 }}
                      onClick={() => setRows(rs => [...rs, { key: '', label: '', nav: [], export: 'csv', params: [] }])}>
                + Add a report
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
