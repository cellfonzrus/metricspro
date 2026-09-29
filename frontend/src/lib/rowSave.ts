// ONE HOME — "what has this person typed on this row that is NOT saved yet, and which requests save
// ALL of it" (owner report 2026-09-29, index §19.35). Owner: "i just saved hourly salary in vzone but
// it did not save when i came back".
//
// THE DEFECT THIS REPLACES. HR → Employees & Pay gave every row THREE independent 💾 buttons — one
// for pay (in the LAST column, past Email and Phone, scrolled off the right edge of a 13-column
// table), one beside the Lunch control and one beside Face recognition. Each saved ONLY its own
// slice. The owner typed hourly rates, set Lunch to On, pressed the 💾 they could see (Lunch), got a
// ✅, and left. The live access log shows exactly four `PUT …/lunch-config` (200) and NO
// `PATCH /storeops/employees/{id}` at all: the rates never left the browser, and the page gave no
// sign that anything was still pending. Roles & Access had the same shape (row "Save" vs the edit
// panel's "💾 Save details", where Pay $/hr lives).
//
// THE CLASS: a row whose edits persist through more than one save action, so a success shown for one
// action reads as "the row is saved" while the other slices' edits sit only in page state and are
// discarded on the next load. THE RULE this module enforces: a row has ONE save; it plans a request
// for EVERY slice whose fields differ from the last-saved snapshot, runs them all, and reports per
// slice what was saved and what was not. What is still unsaved is countable (for the leave guard in
// `useUnsavedGuard.ts`). `backend/harness_row_save_lock.py` fails the build if an employee-record
// editor writes around this module.
//
// Pure module: no imports, erasable TypeScript only (the node proof `frontend/prove_row_save.mjs`
// loads it as is).

export type RowMethod = 'PATCH' | 'PUT' | 'POST'

/** One request that persists one slice of a row. */
export interface RowWrite {
  slice: string
  label: string
  path: string
  method: RowMethod
  body: Record<string, unknown>
}

/** A slice of a row = the fields ONE endpoint persists, and how to build that endpoint's request. */
export interface RowSlice<R = any, C = any> {
  key: string
  /** Human word used in the result message: "Saved pay, lunch for …". */
  label: string
  /** The row keys this slice persists. Dirty = any of them differs from the snapshot. */
  fields: readonly string[]
  /** The request that saves this slice. Return null when the slice does not apply to this row. */
  build: (row: R, ctx: C) => { path: string; method: RowMethod; body: Record<string, unknown> } | null
  /** Server-owned values to fold back after a successful save (e.g. a consent timestamp). */
  reseat?: (response: any, row: R) => Partial<R> | null | undefined
}

/** The ONE comparison of an edited value against its saved value (null / undefined / '' are equal —
 *  the same rule the StoreOps setup grids have always used, now dereferenced from here). */
export function fieldChanged(row: any, snap: any, f: string): boolean {
  return String((row || {})[f] ?? '') !== String((snap || {})[f] ?? '')
}

/** Is any of `fields` edited? No snapshot = nothing to compare against = not dirty. */
export function fieldsDirty(row: any, snap: any, fields: readonly string[]): boolean {
  if (!snap) return false
  return fields.some(f => fieldChanged(row, snap, f))
}

export function sliceDirty<R>(row: R, snap: R | null | undefined, slice: RowSlice<R>): boolean {
  return fieldsDirty(row, snap, slice.fields)
}

/** The slices of this row with unsaved edits, in spec order. */
export function dirtySlices<R>(row: R, snap: R | null | undefined, slices: readonly RowSlice<R>[]): RowSlice<R>[] {
  return slices.filter(s => sliceDirty(row, snap, s))
}

export function rowDirty<R>(row: R, snap: R | null | undefined, slices: readonly RowSlice<R>[]): boolean {
  return dirtySlices(row, snap, slices).length > 0
}

/** THE plan: one request per dirty slice — never a subset. A slice whose build returns null is
 *  skipped (it does not apply to this row) and so is NOT reported as saved. */
export function planRowSave<R, C>(row: R, snap: R | null | undefined, slices: readonly RowSlice<R, C>[], ctx: C): RowWrite[] {
  const out: RowWrite[] = []
  for (const s of dirtySlices(row, snap, slices)) {
    const b = s.build(row, ctx)
    if (b) out.push({ slice: s.key, label: s.label, path: b.path, method: b.method, body: b.body })
  }
  return out
}

/** How many rows carry unsaved edits (drives the "N unsaved" banner and the leave guard). */
export function pendingRowCount<R>(rows: readonly R[], snapOf: (r: R) => R | null | undefined, slices: readonly RowSlice<R>[]): number {
  let n = 0
  for (const r of rows) if (rowDirty(r, snapOf(r), slices)) n++
  return n
}

/** Re-seat a grid from a fresh server read WITHOUT dropping what the person has typed: every row with
 *  unsaved edits keeps its edited fields on top of the fresh values (and so stays visibly unsaved);
 *  every other row takes the fresh values. The fresh rows become the new snapshots. */
export function rebaseRows<R>(prev: readonly R[], snapOf: (r: R) => R | null | undefined, fresh: readonly R[],
                              idOf: (r: R) => string, slices: readonly RowSlice<R>[]): R[] {
  const pending = new Map<string, R>()
  for (const r of prev) if (rowDirty(r, snapOf(r), slices)) pending.set(idOf(r), r)
  return fresh.map(f => {
    const p = pending.get(idOf(f))
    if (!p) return f
    const edits: any = {}
    for (const s of dirtySlices(p, snapOf(p), slices)) for (const k of s.fields) edits[k] = (p as any)[k]
    return { ...(f as any), ...edits } as R
  })
}

export interface RowSaveResult {
  saved: { slice: string; label: string; response: any }[]
  failed: { slice: string; label: string; error: string }[]
}

/** Run every planned write. Each slice is its own endpoint, so one failing never stops the others
 *  (a tenant missing an optional migration must not lose a pay save to a lunch-config 500). */
export async function runRowSave(writes: readonly RowWrite[], send: (w: RowWrite) => Promise<any>): Promise<RowSaveResult> {
  const res: RowSaveResult = { saved: [], failed: [] }
  for (const w of writes) {
    try {
      const response = await send(w)
      res.saved.push({ slice: w.slice, label: w.label, response })
    } catch (e: any) {
      res.failed.push({ slice: w.slice, label: w.label, error: String(e?.message || e) })
    }
  }
  return res
}

/** The new snapshot after a save: each SAVED slice's fields now equal what was sent (plus anything
 *  the server reseats); a FAILED slice keeps its old snapshot, so it stays visibly unsaved.
 *  `back` is ONLY the server-reseated values — merge that into the live row, never the whole `row`,
 *  so a keystroke made while the save was in flight is not overwritten. */
export function commitSaved<R>(row: R, snap: R, slices: readonly RowSlice<R>[], result: RowSaveResult): { row: R; snap: R; back: Partial<R> } {
  let nextRow: any = { ...(row as any) }
  let nextSnap: any = { ...(snap as any) }
  let allBack: any = {}
  for (const s of result.saved) {
    const spec = slices.find(x => x.key === s.slice)
    if (!spec) continue
    for (const f of spec.fields) nextSnap[f] = (row as any)[f]
    const back = spec.reseat ? spec.reseat(s.response, row) : null
    if (back) { nextRow = { ...nextRow, ...back }; nextSnap = { ...nextSnap, ...back }; allBack = { ...allBack, ...back } }
  }
  return { row: nextRow as R, snap: nextSnap as R, back: allBack as Partial<R> }
}

/** One sentence that never claims more than was saved. */
export function rowSaveMessage(name: string, result: RowSaveResult): string {
  const ok = result.saved.map(s => s.label)
  const bad = result.failed.map(f => `${f.label} NOT saved (${f.error})`)
  if (!ok.length && !bad.length) return `Nothing to save for ${name}`
  if (!bad.length) return `Saved ${ok.join(', ')} for ${name}`
  if (!ok.length) return `${name}: ${bad.join('; ')}`
  return `Saved ${ok.join(', ')} for ${name} — ${bad.join('; ')}`
}
