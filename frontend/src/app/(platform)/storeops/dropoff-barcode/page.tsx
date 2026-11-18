'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'

// DROP-OFF BARCODE RESCUE (index §STOREOPS-12, mig 1127).
//
// A customer brings a return; the printer produces a carrier label; the packages are consolidated at
// the end of the day by scanning each label's reference barcode. When a barcode will not scan, the
// package stays in the back room and is never sent back. The reference is still printed on the
// label, so: photograph it, let the platform read it, confirm it, print a fresh barcode.
//
// THE SCREEN'S ONE JOB BEYOND THAT is to make the person LOOK before printing. A reference read off
// a photograph can be wrong by one character — 1/l/I, 0/O — and a wrong reference prints a barcode
// that scans perfectly into the wrong record, which is worse than the package sitting on the shelf.
// So a text read arrives with its confusable characters highlighted and the Print button says what
// it is about to commit to. A DECODED barcode (the bars themselves, check-verified) is not treated
// that way, because it is a fact rather than a reading.

interface Reading {
  source: 'barcode' | 'text' | null
  reference: string | null
  label_name: string | null
  profile_id: string | null
  matched_declaration: boolean
  confirm_required: boolean
  ambiguous: { index: number; char: string; could_be: string[] }[]
  reason: string | null
}
interface BatchItem { name: string; readings: Reading[]; reason: string | null; skipped?: boolean }
interface Profile { id: string; label_name: string; reference_pattern: string; notes?: string | null }
interface Scan {
  id: string; store_code?: string; read_source?: string; read_reference?: string
  confirmed_reference: string; corrected?: boolean; label_name?: string; read_by?: string
  created_at?: string
}
/** One line of the sheet: the reference on the left, its barcode on the right. */
interface Made {
  reference: string; svg: string; source?: string | null; label_name?: string | null
  symbology?: string; verified_symbology?: boolean
}

/** A reference the person is about to commit to, with where it came from and whether they edited it. */
interface Row {
  key: string; photo: string; original: string; reference: string
  source: 'barcode' | 'text' | null; label_name: string | null
  ambiguous: number[]; include: boolean; reading: Reading
}

const BASE = '/api/v1/storeops/dropoff-label'

// What each reason means in words a store person can act on. The platform never prints a reason
// code at somebody holding a package.
const REASONS: Record<string, string> = {
  no_label_types_declared:
    'No label types have been set up for your company yet, so the printed text cannot be matched. Add one below — or type the reference in, which always works.',
  no_reference_matched_a_declared_label_type:
    'Nothing on this photo matched the label types set up for your company.',
  nothing_readable: 'Nothing readable on this photo — check it shows the barcode or the reference line.',
  not_read_yet: 'Not read — the batch ran out of time. Send this one again on its own.',
}

export default function DropoffBarcodePage() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [declaredBlocked, setDeclaredBlocked] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [rows, setRows] = useState<Row[]>([])
  const [unread, setUnread] = useState<{ name: string; reason: string | null }[]>([])
  const [storeCode, setStoreCode] = useState('')
  const [made, setMade] = useState<Made[]>([])
  const [scans, setScans] = useState<Scan[]>([])

  const loadProfiles = useCallback(() => {
    api(`${BASE}/profiles`).then((r: any) => {
      setProfiles(r.profiles || [])
      setDeclaredBlocked(r.available === false ? (r.blocker?.reason || 'Not set up yet.') : '')
    }).catch((e: any) => setDeclaredBlocked(e?.message || String(e)))
  }, [])
  const loadScans = useCallback(() => {
    api(`${BASE}/scans?limit=200`).then((r: any) => setScans(r.scans || [])).catch(() => setScans([]))
  }, [])
  useEffect(() => { loadProfiles(); loadScans() }, [loadProfiles, loadScans])

  async function onPhotos(files: FileList) {
    setBusy(true); setMsg(''); setMade([]); setRows([]); setUnread([])
    try {
      const photos = await Promise.all(Array.from(files).slice(0, 25).map(f => new Promise<any>((resolve, reject) => {
        const fr = new FileReader()
        fr.onload = () => resolve({ name: f.name, image: String(fr.result || '') })
        fr.onerror = () => reject(new Error(`could not read ${f.name}`))
        fr.readAsDataURL(f)
      })))
      const r: any = await api(`${BASE}/read-batch`, { method: 'POST', body: JSON.stringify({ photos }) })
      const next: Row[] = []
      const missed: { name: string; reason: string | null }[] = []
      ;(r.items || []).forEach((it: BatchItem, i: number) => {
        if (!it.readings?.length) { missed.push({ name: it.name, reason: it.reason }); return }
        it.readings.forEach((rd, j) => next.push({
          key: `${i}-${j}`, photo: it.name, original: rd.reference || '', reference: rd.reference || '',
          source: rd.source, label_name: rd.label_name,
          ambiguous: (rd.ambiguous || []).map(a => a.index), include: true, reading: rd,
        }))
      })
      setRows(next); setUnread(missed)
      setMsg(next.length
        ? `Read ${next.length} reference${next.length === 1 ? '' : 's'} from ${r.photos} photo${r.photos === 1 ? '' : 's'}.`
        : 'Nothing readable in those photos.')
    } catch (e: any) {
      setMsg('❌ ' + (e?.message || e))
    } finally { setBusy(false) }
  }

  function setRef(key: string, value: string) {
    setRows(rs => rs.map(r => (r.key === key ? { ...r, reference: value } : r)))
  }
  function toggle(key: string) {
    setRows(rs => rs.map(r => (r.key === key ? { ...r, include: !r.include } : r)))
  }
  function addBlank() {
    setRows(rs => [...rs, {
      key: `manual-${rs.length}-${Date.now()}`, photo: 'typed in', original: '', reference: '',
      source: null, label_name: null, ambiguous: [], include: true,
      reading: { source: null, reference: null, label_name: null, profile_id: null,
                 matched_declaration: false, confirm_required: true, ambiguous: [], reason: null },
    }])
  }

  async function makeAll() {
    const chosen = rows.filter(r => r.include && r.reference.trim())
    if (!chosen.length) return
    setBusy(true); setMsg('')
    try {
      const r: any = await api(`${BASE}/barcodes`, {
        method: 'POST',
        body: JSON.stringify({
          store_code: storeCode.trim() || undefined,
          items: chosen.map(c => ({ reference: c.reference.trim(), reading: c.reading, photo_name: c.photo })),
        }),
      })
      setMade(r.items || [])
      const unproven = (r.items || []).filter((m: Made) => m.verified_symbology === false).length
      const refused = (r.refused || []).length
      setMsg([
        `${(r.items || []).length} barcode${(r.items || []).length === 1 ? '' : 's'} ready.`,
        refused ? `${refused} could not be printed: ${(r.refused || []).map((x: any) => x.reference).join(', ')}` : '',
        unproven ? `${unproven} is built from the reference we read rather than a proven copy of the original code — scan it before you rely on it.` : '',
        r.note || '',
      ].filter(Boolean).join(' '))
      loadScans()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  function printSheet() {
    if (!made.length) return
    const w = window.open('', '_blank', 'width=820,height=900')
    if (!w) { setMsg('Your browser blocked the print window — allow pop-ups for this site.'); return }
    // The sheet the owner asked for: reference on the LEFT, barcode on the RIGHT, one package per
    // row, each row cut-able. `page-break-inside: avoid` keeps a barcode from being split across two
    // sheets of paper, which would make it unscannable — the whole point of re-printing it.
    const rowsHtml = made.map(m => (
      `<tr><td class="ref">${escapeHtml(m.reference)}` +
      `${m.label_name ? `<div class="sub">${escapeHtml(m.label_name)}</div>` : ''}</td>` +
      `<td class="bc">${m.svg}</td></tr>`
    )).join('')
    w.document.write(
      `<!doctype html><title>Drop-off barcodes</title><style>` +
      `body{font-family:system-ui;margin:18px}` +
      `table{width:100%;border-collapse:collapse}` +
      `tr{page-break-inside:avoid;border-bottom:1px dashed #999}` +
      `td{padding:10px 8px;vertical-align:middle}` +
      `.ref{font-family:monospace;font-size:15px;letter-spacing:1px;white-space:nowrap;width:1%}` +
      `.sub{font-family:system-ui;font-size:11px;color:#555;letter-spacing:0}` +
      `.bc{text-align:right}.bc svg{max-width:100%}` +
      `</style><body><table>${rowsHtml}</table></body>`)
    w.document.close(); w.focus(); w.print()
  }

  const chosenCount = rows.filter(r => r.include && r.reference.trim()).length
  const needsEyes = rows.some(r => r.include && r.source !== 'barcode')

  // ── The rescued-packages report: standard filters + the export/send bar (project rule 2026-10-30)
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const filtOpts = useMemo(() => optionsFromRows(scans, { store: x => x.store_code }), [scans])
  const shown = useMemo(() => filterRows(scans, filt, {
    store: x => x.store_code || '', date: x => (x.created_at || '').slice(0, 10),
  }), [scans, filt])
  const sourceLabel = (x: Scan) =>
    x.read_source === 'barcode' ? 'barcode' : x.read_source === 'text' ? 'printed text' : 'typed in'
  const cols: ExportColumn[] = [
    { header: 'When', field: 'created_at', type: 'date', get: x => (x.created_at || '').replace('T', ' ').slice(0, 16) },
    { header: 'Store', field: 'store_code', role: 'store', get: x => x.store_code || '' },
    { header: 'Reference printed', field: 'confirmed_reference', get: x => x.confirmed_reference },
    { header: 'Read from', field: 'read_source', get: x => sourceLabel(x) },
    { header: 'What the platform read', field: 'read_reference', get: x => x.read_reference || '' },
    { header: 'Corrected by hand', field: 'corrected', get: x => (x.corrected ? 'yes' : '') },
    { header: 'Label type', field: 'label_name', get: x => x.label_name || '' },
    { header: 'By', field: 'read_by', get: x => x.read_by || '' },
  ]

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Drop-off barcode rescue</h1>
      <p style={{ color: 'var(--c-fg-6b7280)', marginBottom: 20 }}>
        For return packages whose label barcode will not scan. Photograph the labels — as many as you
        like at once — check the references, then print one sheet and scan them in.
      </p>

      {/* 1 — the photos */}
      <section style={card}>
        <h2 style={h2}>1. Photograph the labels</h2>
        <input type="file" accept="image/*" capture="environment" multiple disabled={busy}
               onChange={e => { const f = e.target.files; if (f && f.length) onPhotos(f) }} />
        <input value={storeCode} onChange={e => setStoreCode(e.target.value)} placeholder="Store (optional)"
               style={{ ...input, width: 180, marginLeft: 12 }} />
        {busy && <span style={{ marginLeft: 12 }}>Reading…</span>}
        <p style={{ color: 'var(--c-fg-6b7280)', marginTop: 8, marginBottom: 0, fontSize: 13 }}>
          Up to 25 photos at a time. One photo with several labels in it is fine too.
        </p>
      </section>

      {/* 2 — check */}
      {(rows.length > 0 || unread.length > 0) && (
        <section style={card}>
          <h2 style={h2}>2. Check the references</h2>
          {needsEyes && (
            <p style={{ ...note, background: 'var(--c-bg-fef3c7)', color: 'var(--c-fg-92400e)' }}>
              Rows marked <b>printed text</b> were read off the printing, not the bars.
              <b> Compare those with the label before printing</b> — highlighted characters are the
              ones a camera gets wrong.
            </p>
          )}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
            <thead><tr>
              <th style={th}>Print</th><th style={th}>Reference</th><th style={th}>Read from</th>
              <th style={th}>Label type</th><th style={th}>Photo</th>
            </tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.key}>
                  <td style={td}><input type="checkbox" checked={r.include} onChange={() => toggle(r.key)} /></td>
                  <td style={td}>
                    <input value={r.reference} onChange={e => setRef(r.key, e.target.value)}
                           style={{ ...input, fontFamily: 'monospace', fontSize: 15, width: 220 }} />
                    {r.source === 'text' && !!r.ambiguous.length && (
                      <div style={{ fontFamily: 'monospace', fontSize: 13, letterSpacing: 2, marginTop: 2 }}>
                        {r.original.split('').map((c, i) => (
                          <span key={i} style={r.ambiguous.includes(i)
                            ? { background: 'var(--c-bg-fef3c7)', color: 'var(--c-fg-92400e)', borderRadius: 3 }
                            : undefined}>{c}</span>
                        ))}
                      </div>
                    )}
                    {r.original && r.reference.trim() !== r.original && (
                      <div style={{ fontSize: 12, color: 'var(--c-fg-92400e)' }}>
                        edited — the original reading is kept on the record
                      </div>
                    )}
                  </td>
                  <td style={td}>{r.source === 'barcode'
                    ? <span style={{ color: 'var(--c-fg-166534)' }}>barcode ✓</span>
                    : r.source === 'text' ? 'printed text' : 'typed in'}</td>
                  <td style={td}>{r.label_name || '—'}</td>
                  <td style={{ ...td, color: 'var(--c-fg-6b7280)' }}>{r.photo}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!!unread.length && (
            <div style={{ ...note, background: 'var(--c-bg-fee2e2)', color: 'var(--c-fg-991b1b)', marginTop: 10 }}>
              <b>Not read:</b>
              <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                {unread.map(u => (
                  <li key={u.name}>{u.name} — {REASONS[u.reason || ''] || 'could not be read.'}</li>
                ))}
              </ul>
            </div>
          )}
          <div style={{ marginTop: 12 }}>
            <button onClick={makeAll} disabled={busy || !chosenCount} style={button}>
              Make {chosenCount || ''} barcode{chosenCount === 1 ? '' : 's'}
            </button>
            <button onClick={addBlank} style={{ ...button, marginLeft: 8 }}>Add one by hand</button>
          </div>
        </section>
      )}

      {/* 3 — the sheet */}
      {!!made.length && (
        <section style={card}>
          <h2 style={h2}>3. Print the sheet and scan them in</h2>
          <button onClick={printSheet} style={{ ...button, marginBottom: 12 }}>Print {made.length} barcode{made.length === 1 ? '' : 's'}</button>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              {made.map(m => (
                <tr key={m.reference} style={{ borderBottom: '1px dashed var(--c-border-e5e7eb)' }}>
                  <td style={{ ...td, fontFamily: 'monospace', fontSize: 15, whiteSpace: 'nowrap', width: '1%' }}>
                    {m.reference}
                    {m.verified_symbology === false && (
                      <div style={{ fontFamily: 'system-ui', fontSize: 11, color: 'var(--c-fg-92400e)' }}>
                        rebuilt from the reference, not a proven copy
                      </div>
                    )}
                  </td>
                  <td style={{ ...td, textAlign: 'right' }} dangerouslySetInnerHTML={{ __html: m.svg }} />
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {msg && <p style={{ margin: '12px 0' }}>{msg}</p>}

      {/* label types — the tenant's own declarations */}
      <LabelTypes profiles={profiles} blocked={declaredBlocked} reload={loadProfiles} />

      {/* the record */}
      <section style={card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <h2 style={{ ...h2, marginBottom: 0 }}>Packages rescued</h2>
          <ReportExportBar title="Drop-off barcode rescues" columns={cols} rows={shown} />
        </div>
        <StandardFilterBar value={filt} onChange={setFilt} show={{ period: false, markets: false, reps: false }}
                           storeOptions={filtOpts.stores} />
        {!shown.length && <p style={{ color: 'var(--c-fg-6b7280)' }}>Nothing rescued yet.</p>}
        {!!shown.length && (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
            <thead><tr>{cols.map(c => <th key={c.field} style={th}>{c.header}</th>)}</tr></thead>
            <tbody>
              {shown.map(s => (
                <tr key={s.id}>
                  <td style={td}>{(s.created_at || '').replace('T', ' ').slice(0, 16)}</td>
                  <td style={td}>{s.store_code || '—'}</td>
                  <td style={{ ...td, fontFamily: 'monospace' }}>{s.confirmed_reference}</td>
                  <td style={td}>{sourceLabel(s)}</td>
                  <td style={{ ...td, fontFamily: 'monospace' }}>{s.read_reference || '—'}</td>
                  <td style={td}>{s.corrected ? 'yes' : ''}</td>
                  <td style={td}>{s.label_name || '—'}</td>
                  <td style={td}>{s.read_by || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}

/** The print window is built as a string, so anything that goes in it is escaped. A reference comes
 *  off a photograph of somebody else's paperwork — it is never markup, and must never become any. */
function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function LabelTypes({ profiles, blocked, reload }: { profiles: Profile[]; blocked: string; reload: () => void }) {
  const [name, setName] = useState('')
  const [pattern, setPattern] = useState('')
  const [msg, setMsg] = useState('')

  async function add() {
    try {
      await api(`${BASE}/profiles`, { method: 'POST', body: JSON.stringify({ label_name: name.trim(), reference_pattern: pattern.trim() }) })
      setName(''); setPattern(''); setMsg('✅ Added.'); reload()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function retire(id: string) {
    try { await api(`${BASE}/profiles/${id}`, { method: 'DELETE' }); reload() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  return (
    <section style={card}>
      <h2 style={h2}>Your label types</h2>
      <p style={{ color: 'var(--c-fg-6b7280)', marginBottom: 10 }}>
        These describe YOUR paperwork, and only yours. They are what lets the platform find the
        reference in the printed text when the bars cannot be read — reading the bars works without
        them. Nothing here is shared with, or taken from, another company.
      </p>
      {blocked && <p style={{ ...note, background: 'var(--c-bg-fef3c7)', color: 'var(--c-fg-92400e)' }}>{blocked}</p>}
      {!profiles.length && !blocked && (
        <p style={{ ...note, background: 'var(--c-bg-fef3c7)', color: 'var(--c-fg-92400e)' }}>
          None set up yet, so the printed text cannot be matched. Reading the barcode in the photo
          still works, and so does typing the reference in.
        </p>
      )}
      {!!profiles.length && (
        <ul style={{ margin: '0 0 12px', paddingLeft: 18 }}>
          {profiles.map(p => (
            <li key={p.id} style={{ marginBottom: 4 }}>
              <b>{p.label_name}</b> <code style={{ fontSize: 12 }}>{p.reference_pattern}</code>
              <button onClick={() => retire(p.id)} style={{ ...button, marginLeft: 10, padding: '2px 8px', fontSize: 12 }}>Remove</button>
            </li>
          ))}
        </ul>
      )}
      <input value={name} onChange={e => setName(e.target.value)} placeholder="What you call this label"
             style={{ ...input, width: 240 }} />
      <input value={pattern} onChange={e => setPattern(e.target.value)} placeholder="What its reference looks like, e.g. D[A-Za-z0-9]{7}RRMA"
             style={{ ...input, width: 340, marginLeft: 8, fontFamily: 'monospace' }} />
      <button onClick={add} disabled={!name.trim() || !pattern.trim()} style={{ ...button, marginLeft: 8 }}>Add</button>
      {msg && <span style={{ marginLeft: 10 }}>{msg}</span>}
    </section>
  )
}

const card: React.CSSProperties = {
  border: '1px solid var(--c-border-e5e7eb)', borderRadius: 10, padding: 16, marginBottom: 16,
}
const h2: React.CSSProperties = { fontSize: 16, fontWeight: 600, marginBottom: 10 }
const note: React.CSSProperties = { padding: '8px 10px', borderRadius: 6, marginBottom: 10 }
const input: React.CSSProperties = {
  padding: '6px 8px', border: '1px solid var(--c-border-e5e7eb)', borderRadius: 6,
}
const button: React.CSSProperties = {
  padding: '6px 14px', borderRadius: 6, border: '1px solid var(--c-border-e5e7eb)', cursor: 'pointer',
}
const th: React.CSSProperties = { textAlign: 'left', padding: '6px 8px', borderBottom: '1px solid var(--c-border-e5e7eb)' }
const td: React.CSSProperties = { padding: '6px 8px', borderBottom: '1px solid var(--c-border-e5e7eb)' }
