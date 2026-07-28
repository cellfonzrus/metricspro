// Shared, display-only interpreter for /upload/{file_type} + /upload-mapped responses so every upload
// surface tells the TRUTH about the two ingest guardrails:
//   • PRICE-COVERAGE GUARD — a degraded/price-less re-delivery of a sales file is REFUSED so it can't
//     clobber the fuller dollars already stored. The backend returns { saved: 0, skipped: 'price_guard',
//     shrink: [{ reason }] } with HTTP 200 (not an error).
//   • ROW-COUNT SHRINK GUARDRAIL — a save that succeeded but ingested far fewer rows than were already
//     stored for that day/period rides back in `shrink` (a truncated/partial export signature).
// Without this, a guard refusal renders as a green "✓ 0 rows" — indistinguishable from a broken upload
// (the luxelink 330-row grouped-file incident, 2026-07-14). This file changes NO money/guard behavior;
// it only maps a response the guard already produced into an honest label + banner.
import type { CSSProperties } from 'react'

export type UploadTone = 'ok' | 'warn' | 'guard'

export interface UploadOutcome {
  tone: UploadTone
  text: string        // one-line honest summary (already carries the right emoji-free wording)
  reason?: string     // the specific guard/shrink reason, when present
  saved: number
  details?: string[]  // per-sheet / per-column forensics (X-report), rendered as a list in the banner
  title?: string      // banner heading override — 'refused, data protected' is wrong for a parse miss
}

// ── X-REPORT (POS tenders) ──────────────────────────────────────────────────────────────────────
// Owner live bug 2026-07-28: a real B2B Soft X-Report uploaded through Data Imports rendered
// "✅ Saved 0 rows." — green, zero rows, zero explanation. The backend now returns a machine-readable
// `skipped` reason + a human `note` + `xreport_diag`; this maps them to an amber banner that names
// the reason, the per-sheet outcome and the tender labels it did not recognize.
const XREPORT_ZERO_FIX: Record<string, string> = {
  no_sheets_matched:
    'The file had no readable rows at all. Re-run the X-Report for ONE day and upload the .xlsx it produces.',
  header_not_found:
    "No sheet carried the tender header we look for ('Tender Types' … 'Net' … 'Refunds' or 'Sub Net'). " +
    'The closest-looking rows are listed below — if your export words that header differently, send those ' +
    'exact cells to the commission owner so the parser can learn the wording.',
  all_labels_unmatched:
    'The tender matrix was found, but none of its tender labels are recognized. Map the labels listed below ' +
    'under Closing → Tender Config (the x_report leg), then upload the same file again.',
  no_flat_columns:
    "This file isn't shaped like an X-Report: it needs a store column (Store / Location / Site / Register), " +
    'a tender column (Tender Type / Payment Type / Media) and an amount column (Amount / Total / Net).',
  all_upserts_failed:
    'The tender rows parsed correctly but the database rejected EVERY write — see the error below. ' +
    'Nothing was stored.',
}

/** Per-sheet / per-column forensics from the backend's `xreport_diag`, as display lines. */
function xreportDetails(r: any): string[] {
  const d = r?.xreport_diag
  if (!d) return []
  const out: string[] = []
  const path = d.parser_path === 'multi_sheet' ? 'multi-sheet workbook (one sheet per store)'
    : d.parser_path === 'flat' ? 'flat single-sheet columns' : 'neither parser matched'
  out.push(`Parser used: ${path}. Sheets read: ${d.sheets_read ?? 0}; tender header found on ${d.headers_found ?? 0}.`)
  for (const s of (Array.isArray(d.sheets) ? d.sheets : []).slice(0, 12)) {
    if (s.outcome === 'rows' || s.outcome === 'no_labels_matched') {
      out.push(
        `Sheet "${s.sheet}": header at row ${s.header_row} (${s.header_wording}) — ` +
        `${s.matched} tender row(s) matched, ${s.skipped} skipped` +
        (s.skipped_labels?.length ? `: ${s.skipped_labels.join(', ')}` : '') + '.')
    } else if (s.outcome === 'header_not_found') {
      const near = [s.closest_row, ...(s.closest_row?.others || [])].filter(Boolean).slice(0, 2)
      out.push(
        `Sheet "${s.sheet}" (${s.rows} rows): no tender header found.` +
        (near.length ? ` Closest row(s) — ${near.map((n: any) => `row ${n.row}: ${(n.cells || []).join(' | ')}`).join(' ;; ')}` : ''))
    } else {
      out.push(`Sheet "${s.sheet}": empty.`)
    }
  }
  if (d.unmatched_labels?.length) {
    out.push(`Tender labels NOT recognized (nothing was ingested for these): ${d.unmatched_labels.join(', ')}. ` +
      `${d.config_label_count ?? 0} label(s) are mapped for this tenant today.`)
  }
  if (d.flat) {
    out.push(`Flat fallback: ${d.flat.rows} row(s); store column = ${d.flat.store_col || 'NOT FOUND'}, ` +
      `tender column = ${d.flat.tender_col || 'NOT FOUND'}, amount column = ${d.flat.amount_col || 'NOT FOUND'}. ` +
      `Columns seen: ${(d.flat.columns || []).join(', ') || '(none)'}.`)
  }
  if (d.upsert_attempts) {
    out.push(`Database writes: ${d.upsert_attempts} attempted, ${d.save_failures || 0} failed` +
      (d.first_error ? ` — first error: ${d.first_error}` : '') + '.')
  }
  return out
}

/** Interpret a raw /upload* response into a display outcome. Safe on any shape (defensive). */
export function readUploadOutcome(r: any, unit = 'row(s)'): UploadOutcome {
  const shrink: any[] = Array.isArray(r?.shrink) ? r.shrink : []
  // `tenders` is the X-report's own count — it never returned `saved`, so a GOOD X-report upload
  // still printed "Saved 0 rows" (the backend now sends both; the fallback keeps older payloads honest).
  const saved = Number(r?.saved ?? r?.rows_saved ?? r?.rows ?? r?.count ?? r?.tenders ?? 0) || 0
  if (r?.file_type === 'x_report' && r?.skipped) {
    const details = xreportDetails(r)
    const fix = XREPORT_ZERO_FIX[r.skipped as string]
    if (fix) {
      const reason = `${r?.note || 'The X-Report saved 0 tender rows.'} — ${fix}`
      return { tone: 'guard', reason, saved: 0, details,
               title: `X-Report saved 0 tender rows — ${r.skipped}`,
               text: `X-Report saved 0 tender rows (${r.skipped}). ${reason}` }
    }
    // x_report_partial_save / x_report_unmapped_labels — rows DID land, with a caveat.
    const reason = (r?.note as string) || 'Some tender rows were not stored.'
    return { tone: 'warn', reason, saved, details,
             title: r.skipped === 'x_report_unmapped_labels'
               ? 'Saved — but some tender labels are unmapped (their dollars are missing from the recon)'
               : 'Saved — but some tender rows failed to store',
             text: `Saved ${saved.toLocaleString()} tender row(s), with a warning: ${reason}` }
  }
  if (r?.skipped === 'price_guard') {
    const reason = (shrink[0]?.reason as string) ||
      'Refused to protect existing data: this file carries far fewer priced (Ext Price) rows than are ' +
      'already stored for that day — a degraded / price-less export. The existing dollars were kept ' +
      'unchanged. Ensure the scheduled b2bsoft report keeps the Ext Price + GP columns.'
    return { tone: 'guard', reason, saved: 0, text: 'Upload refused to protect existing data. ' + reason }
  }
  if (r?.skipped === 'price_guard_partial') {
    // PARTIAL guard: some day(s) in a multi-day file were refused (kept as stored) while the file's fresh
    // day(s) DID ingest. Report both halves honestly — a warn, not a hard refusal (rows were saved).
    const guarded: string[] = Array.isArray(r?.guarded_dates) ? r.guarded_dates.map(String) : []
    const reason = (shrink[0]?.reason as string) ||
      (`Kept existing data for ${guarded.join(', ') || 'some day(s)'} — a degraded / price-less export carried ` +
       'far fewer priced (Ext Price) rows for those day(s) than already stored. The file\'s fresh day(s) were ' +
       'ingested. Ensure the scheduled b2bsoft report keeps the Ext Price + GP columns.')
    return {
      tone: 'warn', reason, saved,
      text: `Ingested ${saved.toLocaleString()} ${unit} for the file's fresh day(s); kept existing data for ` +
            `${guarded.join(', ') || 'the degraded day(s)'}. ${reason}`,
    }
  }
  if (r?.skipped === 'inventory_no_stores') {
    // HONEST-ZERO for an Inventory Aging upload: the file was read but produced 0 per-store values
    // (renamed/unknown store or value column, or an unhandled layout). Show it as a refusal, not a
    // green "✓ 0 rows" — the backend `note` names the columns we expected vs the ones actually found.
    const reason = (r?.note as string) ||
      'Parsed 0 stores — the file needs a store column (Store / Store Name / Location / Site) and a ' +
      'value column (Cost / Ext Cost / Total Value). Nothing was written (existing data kept).'
    return { tone: 'guard', reason, saved: 0, text: 'Inventory Aging parsed 0 stores. ' + reason }
  }
  if (r?.skipped === 'inventory_devices_only') {
    // DEVICE-ONLY Inventory Aging: the export is per-DEVICE (no store column), so 0 per-store values were
    // written while N per-device rows DID save. A real ingest with a caveat → 'warn', never a green
    // "Saved 0 rows" (which reads as broken) and never a hard refusal (rows WERE written). The device
    // count lives on `devices`; `saved` keeps its store-count meaning.
    const devices = Number(r?.devices ?? 0) || 0
    const reason = (r?.note as string) ||
      'This Inventory Aging export has no store column, so no per-store inventory value was written. ' +
      'The per-device rows (cost + aging) were saved.'
    return {
      tone: 'warn', reason, saved: devices,
      text: `Saved ${devices.toLocaleString()} device row(s); 0 stores. ${reason}`,
    }
  }
  if (shrink.length) {
    const s = shrink[0]
    const reason = (s?.reason as string) ||
      `Only ${s?.new} row(s) ingested for ${s?.key} — far fewer than the ${s?.prior} previously stored. ` +
      'This is the signature of a truncated / partial export; verify the source file is complete.'
    return { tone: 'warn', reason, saved, text: `Saved ${saved.toLocaleString()} ${unit}, but with a warning: ${reason}` }
  }
  return { tone: 'ok', saved, text: `Saved ${saved.toLocaleString()} ${unit}.` }
}

/** Prominent amber panel for a guard refusal / shrink warning. Renders nothing for a clean save. */
export function UploadGuardBanner({ outcome, style }: { outcome: UploadOutcome | null; style?: CSSProperties }) {
  if (!outcome || outcome.tone === 'ok') return null
  const guard = outcome.tone === 'guard'
  return (
    <div
      role="alert"
      style={{
        marginTop: 10, padding: '10px 12px', borderRadius: 8, fontSize: 13,
        border: '1px solid #fcd34d', background: '#fffbeb', color: '#92400e', ...style,
      }}
    >
      <div style={{ fontWeight: 700, marginBottom: 2 }}>
        {outcome.title ? `⚠️ ${outcome.title}`
          : guard ? '⚠️ Upload refused — existing data protected' : '⚠️ Saved, but with a data warning'}
      </div>
      <div>{outcome.reason || outcome.text}</div>
      {!!outcome.details?.length && (
        <details style={{ marginTop: 8 }}>
          <summary style={{ cursor: 'pointer', fontWeight: 600 }}>What the importer actually saw</summary>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {outcome.details.map((d, i) => (
              <li key={i} style={{ marginBottom: 3, fontFamily: 'var(--font-mono, monospace)', fontSize: 12 }}>{d}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
