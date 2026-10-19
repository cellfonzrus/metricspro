// THE one home for "the tile row at the top of a report, as something you can SEND".
//
// OWNER ASK (2026-10-17, verbatim): *"on the sales comparison dashboard it shoudl give an option to
// send teh snapshot of the top part to to the desired people"*.
//
// WHY THIS FILE EXISTS RATHER THAN A BLOCK ON THE PAGE. Sending already has ONE home —
// `lib/send-report.SendReportButton` — and it already does everything the ask names: the recipient
// picker over employees + saved contacts + free-typed addresses, the channel ladder, the optional
// message, and delivery through `POST /notify/send-file` so the send lands in the Notify history.
// Nothing about sending needed building. What was missing is CONTENT: that button can only send an
// `ExportPayload`, and a report's SUMMARY TILES were never turned into one. `<ReportShell>` hands it
// the TABLE, so every report could mail its rows and no report could mail the figures a manager
// actually reads.
//
// THE CLASS, not the instance: *a number on screen that nobody can send is a number that gets
// retyped into an email.* The fix is therefore the converter, shared, rather than a snapshot block
// inside the Sales Comparison page — the next report that wants this calls `tileSnapshotSheet` and
// gets the same columns, the same "not measured" rule and the same ordering for free.
//
// HONESTY, carried through from the screen (and this is the whole reason `changePct` is a tri-state):
//   · `changePct: number`  — a measured change.
//   · `changePct: null`    — there is no base to divide by: a brand-new item. Rendered "new".
//   · `measured: false`    — the change CANNOT be measured, and the reason is printed. A ±0% in a
//                            mailed snapshot reads as "this held steady"; when the two periods are
//                            in fact the same figures carried forward from one sheet, that is the
//                            absence-as-a-result lie the house rules forbid (§19.49's class). A tile
//                            that says so on screen must say so in the email, so the flag travels.
//
// PURE and DB-free: no React, no fetch, no clock. BEHAVIOUR proved by `frontend/prove_tile_snapshot.mjs`,
// which drives this very module under Node 22; the WIRING (the page dereferences it, no second
// converter exists, and the honesty flag travels from the backend predicate into the mailed cell) is
// locked by `backend/harness_tile_snapshot.py`. Both run in CI — index §COMMCALC-19.
import type { ExportColumn, ExportSheet } from '@/lib/export'

export type SnapshotTile = {
  label: string                     // what the tile is called on screen, verbatim
  value: string | number | null     // the headline figure for the base period
  previous?: string | number | null // the same figure for the compared period
  changePct?: number | null         // see the tri-state above
  measured?: boolean                // false = the change cannot be measured; `note` says why
  note?: string                     // the small print under the tile, or the reason it is unmeasured
  money?: boolean                   // the figure is dollars (the export formats it as currency)
}

export const SNAPSHOT_SHEET_NAME = 'Snapshot'

/** PURE: a measured percent → the signed label the screen shows; the two absent cases say which
 *  they are instead of printing a number nobody can act on. */
export function snapshotChangeLabel(t: SnapshotTile): string {
  if (t.measured === false) return 'not measured'
  const p = t.changePct
  if (p == null) return 'new'
  const arrow = p > 0 ? '▲' : p < 0 ? '▼' : '±'
  return `${arrow} ${p > 0 ? '+' : ''}${p}%`
}

/** PURE: the tile row → ONE export sheet. Column order is fixed here so every report that sends its
 *  snapshot mails the same shape, and a reader comparing two reports is not re-learning the layout.
 *  `money` is honoured per ROW rather than per column, because a tile row legitimately mixes units
 *  and dollars — so the figures are pre-formatted as text and the sheet declares no money column. */
export function tileSnapshotSheet(tiles: SnapshotTile[], name: string = SNAPSHOT_SHEET_NAME): ExportSheet {
  const fmtMoney = (v: string | number | null | undefined) =>
    v == null || v === '' ? '' :
      new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(v) || 0)
  const cell = (t: SnapshotTile, v: string | number | null | undefined) => {
    // An ABSENT figure is blank, never 0 — the same rule `lib/export.money` already applies to a
    // withheld money cell. A snapshot outlives the screen and carries no note, so a fabricated zero
    // there is worse than on the page.
    if (v == null || v === '') return ''
    if (t.money && typeof v !== 'string') return fmtMoney(v)
    return String(v)
  }
  const columns: ExportColumn[] = [
    { header: 'Measure', get: (r: SnapshotTile) => r.label },
    { header: 'This period', get: (r: SnapshotTile) => cell(r, r.value), align: 'right' },
    { header: 'Compared period', get: (r: SnapshotTile) => cell(r, r.previous), align: 'right' },
    { header: 'Change', get: (r: SnapshotTile) => snapshotChangeLabel(r), align: 'right' },
    { header: 'Note', get: (r: SnapshotTile) => r.note || '' },
  ]
  return { name, columns, rows: tiles }
}
