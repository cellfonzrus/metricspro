'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { repConfined } from '@/lib/rbac'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import { exportCodeSheetToPDF } from '@/lib/export'
import StandardFilterBar from '@/components/StandardFilterBar'
import { SortableTh, useTableSort } from '@/components/SortableTh'
import { FixItNotice, useFixGuide } from '@/lib/fix-it'
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
//
// WHO IS HOLDING THE PHONE (owner 2026-11-20: *"Grant the sales rep this and also give step by step
// instructions on the page when the sales rep is ready to take a picture of the items"*). The person
// who takes the return over the counter is the rep, so this screen is theirs — on a phone, standing
// at the counter, with the package in one hand. Two things follow, and neither is a style choice:
//
//   · THE STEPS ARE ON THE SCREEN, beside the camera button, not in a manual nobody opens. A photo
//     taken at an angle under a ceiling light is the single reason a read fails, and that is fixable
//     by the person taking it IF they are told before they take it.
//   · WHAT A CONFINED EMPLOYEE MAY REACH IS NOT DECIDED HERE. `backend/app/core/rep_surface.py` is
//     THE declaration (index §CORE-33) and the API middleware refuses on it; this page READS the
//     same fact off `/me` (`repConfined`) for one purpose only — not to ask for something it already
//     knows will be refused, so a rep sees their job instead of a red box. A second opinion about
//     who may do what is exactly the browser-side gate that declaration exists to kill, so there is
//     no page list, no role name and no module test anywhere in this file.

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

// ONE DEFINITION OF EACH CELL, read three ways. `SCAN_COLS` is what the table renders, what the
// Excel/PDF/Print/Send bar exports (RULE FOUR: exactly what you see) and what the sort compares —
// so a column cannot say one thing on screen, another in the file and sort by a third. At module
// scope because it depends on no state, which is also what `useTableSort` wants: a fresh closure
// per render would re-run its memo every time.
const sourceLabel = (x: Scan) =>
  x.read_source === 'barcode' ? 'barcode' : x.read_source === 'text' ? 'printed text' : 'typed in'
const SCAN_COLS: ExportColumn[] = [
  { header: 'When', field: 'created_at', type: 'date', get: x => (x.created_at || '').replace('T', ' ').slice(0, 16) },
  { header: 'Store', field: 'store_code', role: 'store', get: x => x.store_code || '' },
  { header: 'Reference printed', field: 'confirmed_reference', get: x => x.confirmed_reference },
  { header: 'Read from', field: 'read_source', get: x => sourceLabel(x) },
  { header: 'What the platform read', field: 'read_reference', get: x => x.read_reference || '' },
  { header: 'Corrected by hand', field: 'corrected', get: x => (x.corrected ? 'yes' : '') },
  { header: 'Label type', field: 'label_name', get: x => x.label_name || '' },
  { header: 'By', field: 'read_by', get: x => x.read_by || '' },
]
/** The sort reads the SAME column definition the table and the export read. */
const scanCell = (r: Scan, f: string) => SCAN_COLS.find(c => c.field === f)?.get(r) ?? ''

// The shapes the four endpoints answer with. Written out rather than left as `any` so that a field
// renamed on the backend fails the type check here instead of rendering as `undefined` at a counter.
interface Blocker { reason?: string }
interface ProfilesResponse { profiles?: Profile[]; available?: boolean; blocker?: Blocker }
interface ScansResponse { scans?: Scan[] }
interface ReadBatchResponse { items?: BatchItem[]; photos?: number }
interface BarcodesResponse { items?: Made[]; refused?: { reference: string; reason?: string }[]; note?: string }
/** One photo on its way up: the file's name, and the file itself as a data URL. */
interface PhotoUpload { name: string; image: string }

/** What a failed call says to the person. A thrown value is not necessarily an Error, which is the
 *  reason this is one function and not an `any` at every catch. */
function errText(e: unknown): string {
  if (e instanceof Error) return e.message
  if (e && typeof e === 'object' && 'message' in e) return String((e as { message: unknown }).message)
  return String(e)
}

// What each reason means in words a store person can act on. The platform never prints a reason
// code at somebody holding a package.
const REASONS: Record<string, string> = {
  // WRITTEN FOR WHOEVER IS READING IT, which after 2026-11-20 includes a sales rep who CANNOT add
  // one — declaring a label type is an admin act (§CORE-33.1). The old wording told them to "add
  // one below", which is a dead end dressed as an instruction, and a dead end is the thing
  // §CORE-39 exists to stop. It now names the two ways that work for everybody and points at the
  // notice further down, which already asks the SERVER whether this viewer may close the gap and
  // offers to ask whoever can. One string, no role test on the page, right for both readers.
  no_label_types_declared:
    'The printed text could not be matched, because no label types have been set up for your company yet. Reading the barcode in the photo still works, and so does typing the reference in by hand — and there is a note further down about getting this set up.',
  no_reference_matched_a_declared_label_type:
    'Nothing on this photo matched the label types set up for your company.',
  nothing_readable: 'Nothing readable on this photo — check it shows the barcode or the reference line.',
  // AN UNREAD PHOTO IS NOT AN EMPTY ONE. Said separately from `nothing_readable` because the two
  // ask different things of the reader: one means take a better photograph, the other means do not
  // bother — nobody looked. Printing the first when the second is true sends a store back to
  // re-shoot a label that was never going to be read (2026-11-20). The server decides which it is,
  // in ONE place (`dropoff_label_api.unread_reason`); this page only names them.
  text_read_did_not_run:
    'The barcode could not be read, and reading the printed characters did not run on this photo — so this is not a problem with your photograph. Type the reference in by hand to carry on, and tell your manager the photo reading is not switched on.',
  not_read_yet: 'Not read — the batch ran out of time. Send this one again on its own.',
}

export default function DropoffBarcodePage() {
  // THE declaration's answer, served by /me. `confined` is true for an individual contributor and
  // false for everyone else, including a store manager — who is not confined and keeps the record.
  const { permissions } = useAuth()
  const confined = repConfined(permissions)
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
    api(`${BASE}/profiles`).then((r: ProfilesResponse) => {
      setProfiles(r.profiles || [])
      setDeclaredBlocked(r.available === false ? (r.blocker?.reason || 'Not set up yet.') : '')
    }).catch((e: unknown) => setDeclaredBlocked(errText(e)))
  }, [])
  const loadScans = useCallback(() => {
    api(`${BASE}/scans?limit=200`).then((r: ScansResponse) => setScans(r.scans || [])).catch(() => setScans([]))
  }, [])
  // The organisation-wide rescue record is a REPORT and is not granted to a confined employee
  // (`rep_surface`: `/dropoff-label/scans` is not in their surface). Not asked for, rather than
  // asked for and refused: a 403 in the console at somebody holding a package teaches them that the
  // screen is broken. Their own sheet is in step 3, which is the part of it that is theirs.
  useEffect(() => { loadProfiles(); if (!confined) loadScans() }, [loadProfiles, loadScans, confined])

  async function onPhotos(files: FileList) {
    setBusy(true); setMsg(''); setMade([]); setRows([]); setUnread([])
    try {
      const photos = await Promise.all(Array.from(files).slice(0, 25).map(f => new Promise<PhotoUpload>((resolve, reject) => {
        const fr = new FileReader()
        fr.onload = () => resolve({ name: f.name, image: String(fr.result || '') })
        fr.onerror = () => reject(new Error(`could not read ${f.name}`))
        fr.readAsDataURL(f)
      })))
      const r: ReadBatchResponse = await api(`${BASE}/read-batch`, { method: 'POST', body: JSON.stringify({ photos }) })
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
    } catch (e: unknown) {
      setMsg('❌ ' + errText(e))
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
      const r: BarcodesResponse = await api(`${BASE}/barcodes`, {
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
        refused ? `${refused} could not be printed: ${(r.refused || []).map(x => x.reference).join(', ')}` : '',
        unproven ? `${unproven} is built from the reference we read rather than a proven copy of the original code — scan it before you rely on it.` : '',
        r.note || '',
      ].filter(Boolean).join(' '))
      loadScans()
    } catch (e: unknown) { setMsg('❌ ' + errText(e)) } finally { setBusy(false) }
  }

  // SAVE, not print (owner 2026-11-20: *"How will they print from the phone, it should save as a
  // pdf on their phone to be used when they re by the ups system"*). A store phone has no printer
  // paired, and the print window below is a pop-up a phone browser blocks as often as it opens. A
  // PDF download lands in the share sheet, and "Save to Files" keeps it on the device — which is
  // what the job needs: the codes are shown at the UPS system later, possibly out of signal.
  //
  // PRINT IS KEPT BESIDE IT, not replaced: a store with a desktop and a label printer still wants
  // paper in one tap. Both render from the SAME `made` array the screen is showing, so the two
  // destinations cannot disagree about which packages are on the sheet.
  async function savePdf() {
    setBusy(true)
    try {
      const r = await exportCodeSheetToPDF({
        report: 'Drop-off barcode rescue',
        title: 'Drop-off barcodes',
        subtitle: storeCode.trim() ? `Store ${storeCode.trim()}` : undefined,
        filename: `dropoff-barcodes-${new Date().toISOString().slice(0, 10)}`,
        items: made.map(m => ({
          reference: m.reference,
          svg: m.svg,
          note: m.verified_symbology === false
            ? 'rebuilt from the reference, not a proven copy' : undefined,
        })),
        footnote: 'Cut along the dashed lines. Check each code against its package before scanning it in.',
      })
      setMsg(r.saved
        ? `Saved ${r.drawn} barcode${r.drawn === 1 ? '' : 's'} as a PDF.` +
          (r.failed.length ? ` ${r.failed.length} could not be drawn: ${r.failed.join(', ')}.` : '') +
          ' On a phone, choose Save to Files to keep it.'
        : ('\u274c ' + (r.message || 'The PDF could not be made.')))
    } catch (e: unknown) { setMsg('\u274c ' + errText(e)) } finally { setBusy(false) }
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
      // No colour LITERAL anywhere in this stylesheet, and none of the app's theme tokens either:
      // this is a separate document printed on paper, so `var(--c-fg-…)` would resolve to nothing
      // here, and the dark-theme lock is right to refuse a hex. `currentColor` for the cut line and
      // `opacity` for the label name give the same look out of the printer with neither.
      `tr{page-break-inside:avoid;border-bottom:1px dashed}` +
      `td{padding:10px 8px;vertical-align:middle}` +
      `.ref{font-family:monospace;font-size:15px;letter-spacing:1px;white-space:nowrap;width:1%}` +
      `.sub{font-family:system-ui;font-size:11px;opacity:.65;letter-spacing:0}` +
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
  // Click-a-header sorting through the shared hook (owner directive 2026-08-10, §CORE-26) rather
  // than a sort of this page's own. It sorts what the filters LEFT, so the two compose.
  const scanSort = useTableSort(shown, scanCell)

  return (
    <div className="dbr">
      {/* THE LAYOUT IS CSS, not a width this component measures. A hook that reads window width
          renders the desktop shape first and then swaps, which on a phone at a counter is a visible
          jump and, during the server render, the WRONG shape in the HTML. A media query has decided
          before the first paint. The class names are prefixed so nothing here can reach another
          screen, and every colour is a theme token or `currentColor` — never a literal, which is
          what the dark-theme lock refuses and rightly. */}
      <style>{`
        .dbr { padding: 24px; max-width: 1100px; }
        .dbr-card { border: 1px solid var(--border); border-radius: 10px; padding: 16px; margin-bottom: 16px; }
        .dbr-steps { margin: 0 0 14px; padding-left: 22px; font-size: 14px; line-height: 1.55; }
        .dbr-steps li { margin-bottom: 6px; }
        .dbr-shoot { display: inline-flex; align-items: center; justify-content: center; gap: 8px;
          min-height: 48px; padding: 0 20px; border-radius: 10px; font-size: 16px; font-weight: 600;
          border: 1px solid var(--border); cursor: pointer; }
        .dbr-shoot input { display: none; }
        .dbr-row { display: grid; grid-template-columns: 28px 1fr; gap: 10px; align-items: start;
          padding: 12px 0; border-bottom: 1px solid var(--border); }
        .dbr-ref { font-family: monospace; font-size: 17px; width: 100%; max-width: 300px;
          padding: 10px; border: 1px solid var(--border); border-radius: 8px;
          background: transparent; color: inherit; }
        .dbr-meta { font-size: 12px; color: var(--c-fg-6b7280); margin-top: 5px; }
        .dbr-made { display: grid; grid-template-columns: max-content 1fr; gap: 8px 16px;
          align-items: center; padding: 12px 0; border-bottom: 1px dashed var(--border); }
        .dbr-bc { text-align: right; }
        .dbr-bc svg { max-width: 100%; height: auto; }
        .dbr-scroll { overflow-x: auto; }
        .dbr-btn { min-height: 44px; padding: 0 18px; border-radius: 8px; font-size: 15px;
          border: 1px solid var(--border); background: transparent; color: inherit; cursor: pointer; }
        .dbr-btn[disabled] { opacity: .5; cursor: default; }
        /* A PHONE AT THE COUNTER is this screen's normal case, not its exception: the person holding
           the package is the person taking the photograph. So below 760px every tap target fills the
           width, the reference list stops being two columns beside a checkbox, and the barcode sits
           UNDER its reference rather than being squeezed next to it — a barcode narrowed to fit a
           phone column is a barcode that will not scan, which is the whole point of printing it. */
        @media (max-width: 760px) {
          .dbr { padding: 14px 12px 36px; }
          .dbr-card { padding: 12px; border-radius: 12px; }
          .dbr-shoot, .dbr-btn { width: 100%; }
          .dbr-btn { margin: 8px 0 0; }
          .dbr-made { grid-template-columns: 1fr; }
          .dbr-bc { text-align: left; }
          .dbr-store { width: 100%; margin: 10px 0 0; }
        }
      `}</style>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Drop-off barcode rescue</h1>
      <p style={{ color: 'var(--c-fg-6b7280)', marginBottom: 20 }}>
        For return packages whose label barcode will not scan. Photograph the labels — as many as you
        like at once — check the references, then print one sheet and scan them in.
      </p>

      {/* 1 — the photos. THE STEPS ARE HERE, above the button, because they are only useful before
          the photograph is taken. Each one names a thing the person can do differently, in the order
          they would do it; none of them explains how the platform works, which is not their job. */}
      <section className="dbr-card">
        <h2 style={h2}>1. Photograph the labels</h2>
        <p style={{ margin: '0 0 8px', fontSize: 14 }}><b>With the package in your hand:</b></p>
        <ol className="dbr-steps">
          <li>Put the package on the counter with the label facing up, and smooth the label flat if it is creased or curled.</li>
          <li>Stand so the ceiling light is <b>behind you</b>, not bouncing off the label. Glare across the barcode is the most common reason a photo cannot be read.</li>
          <li>Hold the phone <b>square to the label</b>, not at an angle, and fill the frame with it. Both the barcode and the line of printed characters need to be in the shot.</li>
          <li>Tap the screen to focus, wait until it looks sharp, then take the photo. One package per photo is best — several labels laid out flat in one photo works too.</li>
          <li>Do every stranded package, then press <b>Take photos of the labels</b> and pick them all at once. Up to 25 in one go.</li>
          <li>On the next step, <b>read each reference against the label</b> before you print. A highlighted character is one a camera often gets wrong.</li>
        </ol>
        <label className="dbr-shoot">
          📷 Take photos of the labels
          <input type="file" accept="image/*" capture="environment" multiple disabled={busy}
                 onChange={e => { const f = e.target.files; if (f && f.length) onPhotos(f) }} />
        </label>
        <input className="dbr-store" value={storeCode} onChange={e => setStoreCode(e.target.value)}
               placeholder="Store (optional)" style={{ ...input, width: 180, marginLeft: 12 }} />
        {busy && <span style={{ marginLeft: 12 }}>Reading…</span>}
        <p style={{ color: 'var(--c-fg-6b7280)', marginTop: 10, marginBottom: 0, fontSize: 13 }}>
          Nothing is printed until you have checked it. If a photo cannot be read at all, you can
          always type the reference in by hand.
        </p>
      </section>

      {/* 2 — check */}
      {(rows.length > 0 || unread.length > 0) && (
        <section className="dbr-card">
          <h2 style={h2}>2. Check the references</h2>
          {needsEyes && (
            <p style={{ ...note, background: 'var(--c-bg-fef3c7)', color: 'var(--c-fg-92400e)' }}>
              Rows marked <b>printed text</b> were read off the printing, not the bars.
              <b> Compare those with the label before printing</b> — highlighted characters are the
              ones a camera gets wrong.
            </p>
          )}
          {/* ONE CARD PER PACKAGE, not a row of a seven-column table. The columns that table had
              — where the reading came from, which label type matched, which photo it came off — are
              facts ABOUT the reference, so they read as one line under it and need no header. On a
              phone the table version put a 220px monospace input in a column roughly 90px wide,
              which is the one field on this screen a person must be able to read character by
              character before they commit to it. */}
          <div>
            {rows.map(r => (
              <div className="dbr-row" key={r.key}>
                <div>
                  <input type="checkbox" checked={r.include} onChange={() => toggle(r.key)}
                         style={{ width: 20, height: 20 }}
                         aria-label={`Print ${r.reference || 'this reference'}`} />
                </div>
                <div>
                  <input className="dbr-ref" value={r.reference} onChange={e => setRef(r.key, e.target.value)}
                         spellCheck={false} autoCapitalize="characters" aria-label="Reference to print" />
                  {r.source === 'text' && !!r.ambiguous.length && (
                    <div style={{ fontFamily: 'monospace', fontSize: 14, letterSpacing: 2, marginTop: 4 }}>
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
                  <div className="dbr-meta">
                    {r.source === 'barcode'
                      ? <span style={{ color: 'var(--c-fg-166534)' }}>read from the barcode ✓</span>
                      : r.source === 'text' ? 'read from the printed text — check it against the label'
                      : 'typed in by hand'}
                    {r.label_name ? ` · ${r.label_name}` : ''}
                    {r.photo ? ` · ${r.photo}` : ''}
                  </div>
                </div>
              </div>
            ))}
          </div>
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
            <button onClick={makeAll} disabled={busy || !chosenCount} className="dbr-btn">
              Make {chosenCount || ''} barcode{chosenCount === 1 ? '' : 's'}
            </button>
            <button onClick={addBlank} className="dbr-btn" style={{ marginLeft: 8 }}>Add one by hand</button>
          </div>
        </section>
      )}

      {/* 3 — the sheet */}
      {!!made.length && (
        <section className="dbr-card">
          <h2 style={h2}>3. Print the sheet and scan them in</h2>
          <p style={{ margin: '0 0 10px', fontSize: 14 }}>
            <b>On a phone:</b> press <b>Save as a PDF</b>, then choose <b>Save to Files</b> (or
            Downloads). The sheet stays on the phone, so you can open it at the UPS system even
            without signal. <b>On a computer</b> with a printer, <b>Print instead</b> is one tap.
            Either way: the reference on the left, its code on the right, one package per row, cut
            along the dashed lines.
          </p>
          {/* The PDF is FIRST and is the one that works everywhere; print is the desktop shortcut. */}
          <button onClick={savePdf} disabled={busy} className="dbr-btn" style={{ marginBottom: 12 }}>
            💾 Save {made.length} barcode{made.length === 1 ? '' : 's'} as a PDF
          </button>
          <button onClick={printSheet} className="dbr-btn" style={{ marginBottom: 12, marginLeft: 8 }}>Print instead</button>
          {made.map(m => (
            <div className="dbr-made" key={m.reference}>
              <div style={{ fontFamily: 'monospace', fontSize: 16 }}>
                {m.reference}
                {m.verified_symbology === false && (
                  <div style={{ fontFamily: 'system-ui', fontSize: 11, color: 'var(--c-fg-92400e)' }}>
                    rebuilt from the reference, not a proven copy
                  </div>
                )}
              </div>
              <div className="dbr-bc" dangerouslySetInnerHTML={{ __html: m.svg }} />
            </div>
          ))}
        </section>
      )}

      {msg && <p style={{ margin: '12px 0' }}>{msg}</p>}

      {/* label types — the tenant's own declarations. A confined employee READS them (the screen
          matches printed text with them) and does not declare or retire one: that is an admin act,
          carved out of their surface and separately refused by the two handlers. `canDeclare` is
          not a second opinion about their reach — it is the SAME fact off /me, used to show them
          the list instead of a form that would 403. */}
      <LabelTypes profiles={profiles} blocked={declaredBlocked} reload={loadProfiles} canDeclare={!confined} />

      {/* the record — the whole organisation's rescues, across every store. Not an employee's:
          `/dropoff-label/scans` is not in their surface, so there is nothing to render and nothing
          was asked for. They are told their work IS recorded, because the alternative reading of a
          missing section is that it was not. */}
      {confined && (
        <section className="dbr-card">
          <h2 style={h2}>Your rescues are recorded</h2>
          <p style={{ color: 'var(--c-fg-6b7280)', margin: 0, fontSize: 14 }}>
            Every code you print is saved with the reference you confirmed, what the platform read
            before you confirmed it, and your name. Your manager can see the record for the store.
          </p>
        </section>
      )}
      {!confined && (
      <section className="dbr-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <h2 style={{ ...h2, marginBottom: 0 }}>Packages rescued</h2>
          <ReportExportBar title="Drop-off barcode rescues" columns={SCAN_COLS} rows={shown} />
        </div>
        <StandardFilterBar value={filt} onChange={setFilt} show={{ period: false, markets: false, reps: false }}
                           storeOptions={filtOpts.stores} />
        {!shown.length && <p style={{ color: 'var(--c-fg-6b7280)' }}>Nothing rescued yet.</p>}
        {!!shown.length && (
          <div className="dbr-scroll">
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
            <thead><tr>{SCAN_COLS.map(c => (
              <SortableTh key={c.field} field={c.field!} sort={scanSort.sort} onSort={scanSort.toggle} style={th}>{c.header}</SortableTh>
            ))}</tr></thead>
            <tbody>
              {scanSort.sorted.map(s => (
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
          </div>
        )}
      </section>
      )}
    </div>
  )
}

/** The print window is built as a string, so anything that goes in it is escaped. A reference comes
 *  off a photograph of somebody else's paperwork — it is never markup, and must never become any. */
function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function LabelTypes({ profiles, blocked, reload, canDeclare }:
                    { profiles: Profile[]; blocked: string; reload: () => void; canDeclare: boolean }) {
  // Asked for ONLY while the gap is open, so a company that has declared its labels fetches nothing.
  const labelTypeFix = useFixGuide(profiles.length ? null : 'dropoff_label_types', 'Drop-off Barcode Rescue')
  const [name, setName] = useState('')
  const [pattern, setPattern] = useState('')
  const [msg, setMsg] = useState('')

  async function add() {
    try {
      await api(`${BASE}/profiles`, { method: 'POST', body: JSON.stringify({ label_name: name.trim(), reference_pattern: pattern.trim() }) })
      setName(''); setPattern(''); setMsg('✅ Added.'); reload()
    } catch (e: unknown) { setMsg('❌ ' + errText(e)) }
  }
  async function retire(id: string) {
    try { await api(`${BASE}/profiles/${id}`, { method: 'DELETE' }); reload() }
    catch (e: unknown) { setMsg('❌ ' + errText(e)) }
  }

  return (
    <section className="dbr-card">
      <h2 style={h2}>Your label types</h2>
      <p style={{ color: 'var(--c-fg-6b7280)', marginBottom: 10 }}>
        These describe YOUR paperwork, and only yours. They are what lets the platform find the
        reference in the printed text when the bars cannot be read — reading the bars works without
        them. Nothing here is shared with, or taken from, another company.
      </p>
      {blocked && <p style={{ ...note, background: 'var(--c-bg-fef3c7)', color: 'var(--c-fg-92400e)' }}>{blocked}</p>}
      {/* A gap is never a dead end (§CORE-39): the shared notice carries the SERVER's own steps,
          asks the server whether THIS viewer may close it, and offers to ask whoever can when they
          may not. The wording lives in core/setup_fix.py and is never respelled here. A rep who
          cannot add a label type still reads what is needed and can send it to their manager. */}
      {!profiles.length && <FixItNotice fix={labelTypeFix} lead="Reading the barcode in the photo still works without this, and so does typing the reference in." />}
      {!!profiles.length && (
        <ul style={{ margin: '0 0 12px', paddingLeft: 18 }}>
          {profiles.map(p => (
            <li key={p.id} style={{ marginBottom: 4 }}>
              <b>{p.label_name}</b> <code style={{ fontSize: 12 }}>{p.reference_pattern}</code>
              {canDeclare && (
                <button onClick={() => retire(p.id)} style={{ ...button, marginLeft: 10, padding: '2px 8px', fontSize: 12 }}>Remove</button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canDeclare && (
        <>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="What you call this label"
                 style={{ ...input, width: 240 }} />
          <input value={pattern} onChange={e => setPattern(e.target.value)} placeholder="What its reference looks like, e.g. D[A-Za-z0-9]{7}RRMA"
                 style={{ ...input, width: 340, marginLeft: 8, fontFamily: 'monospace' }} />
          <button onClick={add} disabled={!name.trim() || !pattern.trim()} style={{ ...button, marginLeft: 8 }}>Add</button>
          {msg && <span style={{ marginLeft: 10 }}>{msg}</span>}
        </>
      )}
    </section>
  )
}

const h2: React.CSSProperties = { fontSize: 16, fontWeight: 600, marginBottom: 10 }
const note: React.CSSProperties = { padding: '8px 10px', borderRadius: 6, marginBottom: 10 }
const input: React.CSSProperties = {
  padding: '6px 8px', border: '1px solid var(--border)', borderRadius: 6,
}
const button: React.CSSProperties = {
  padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)', cursor: 'pointer',
}
const th: React.CSSProperties = { textAlign: 'left', padding: '6px 8px', borderBottom: '1px solid var(--border)' }
const td: React.CSSProperties = { padding: '6px 8px', borderBottom: '1px solid var(--border)' }
