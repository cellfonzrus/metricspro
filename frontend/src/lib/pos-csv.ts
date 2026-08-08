// POS module — CSV parser + serializer, ported verbatim from the standalone
// pos-system app (lib/csv.ts). Hand-rolled, no dependencies.
// Parser handles: quoted fields, embedded commas / quotes ("") / newlines,
// CRLF and lone-CR line endings, a leading UTF-8 BOM, and trailing empty lines.

export type CsvCell = string | number | boolean | null | undefined

/**
 * Parse CSV text into an array of rows (each row an array of string cells).
 * - Strips a leading BOM.
 * - Supports RFC-4180 quoting: fields wrapped in double quotes may contain
 *   commas, newlines and escaped quotes ("").
 * - Accepts \n, \r\n and \r as row terminators.
 * - Drops fully-empty trailing rows (a final newline does not create a row).
 */
export function parseCsv(text: string): string[][] {
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1)

  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let inQuotes = false
  let i = 0

  const endField = () => {
    row.push(field)
    field = ''
  }
  const endRow = () => {
    endField()
    rows.push(row)
    row = []
  }

  while (i < text.length) {
    const c = text[i]
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"'
          i += 2
        } else {
          inQuotes = false
          i++
        }
      } else {
        field += c
        i++
      }
      continue
    }
    if (c === '"' && field === '') {
      inQuotes = true
      i++
    } else if (c === ',') {
      endField()
      i++
    } else if (c === '\r') {
      if (text[i + 1] === '\n') i++
      endRow()
      i++
    } else if (c === '\n') {
      endRow()
      i++
    } else {
      field += c
      i++
    }
  }
  if (field !== '' || row.length > 0 || inQuotes) endRow()

  // Trailing empty lines (all cells blank) are noise, not data.
  while (rows.length > 0 && rows[rows.length - 1].every(f => f.trim() === '')) {
    rows.pop()
  }
  return rows
}

/**
 * Serialize rows to CSV text. Cells containing commas, quotes or newlines are
 * quoted, and embedded quotes are doubled. null/undefined become empty cells.
 * Uses CRLF line endings for maximum spreadsheet compatibility.
 */
export function serializeCsv(rows: CsvCell[][]): string {
  const encodeCell = (cell: CsvCell): string => {
    if (cell === null || cell === undefined) return ''
    const s = typeof cell === 'boolean' ? (cell ? 'yes' : 'no') : String(cell)
    return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
  }
  return rows.map(r => r.map(encodeCell).join(',')).join('\r\n') + '\r\n'
}
