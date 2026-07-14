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
}

/** Interpret a raw /upload* response into a display outcome. Safe on any shape (defensive). */
export function readUploadOutcome(r: any, unit = 'row(s)'): UploadOutcome {
  const shrink: any[] = Array.isArray(r?.shrink) ? r.shrink : []
  const saved = Number(r?.saved ?? r?.rows_saved ?? r?.rows ?? r?.count ?? 0) || 0
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
        {guard ? '⚠️ Upload refused — existing data protected' : '⚠️ Saved, but with a data warning'}
      </div>
      <div>{outcome.reason || outcome.text}</div>
    </div>
  )
}
