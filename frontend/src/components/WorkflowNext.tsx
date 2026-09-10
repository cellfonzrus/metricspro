'use client'
// ── WHAT DO YOU WANT TO DO NEXT ─────────────────────────────────────────────────────────────────
// Owner directive 2026-09-10, verbatim: "the modules for these should be stacked properly based on
// thr work flow in one tile so the user does not have to loo for the next module it is user friendly
// and also the current module should ask the chart what do they want to do next — so changes needed
// in the dm verify modules also".
//
// A screen that finishes its job and says nothing leaves the user to go back to a menu and remember
// what the next module is called. This asks the FLOWCHART — literally: `stagesAfter(here)` reads the
// same `stages` array the runbook and the stacked hub tile read — and offers the next screen by name,
// with the one line that says what happens there.
//
// ONE DEFINITION OF THE ORDER (RULE TWO's spirit, applied to navigation). The sequence is not
// repeated here, not repeated in the tile layout, and not repeated in the runbook prose. All three
// ask `lib/flowcharts`. A second copy would drift the moment a step is inserted, and the version a
// user is walked through would stop matching the version they were trained on.
//
// THE GATE IS THE DESTINATION'S OWN NAV ENTRY. `useCanOpen` is the SAME predicate ScreenLink and the
// sidebar use (owner directive 2026-09-08 — one mechanism for "copy names a screen, that name is a
// link"). So this can never advertise a screen the viewer would be bounced out of. When the very
// next stage is closed to them — a rep finishing a close has no business on DM Verify — it offers
// the first stage after it that they CAN open, rather than a dead end or nothing at all.
//
// IT NEVER CLAIMS THE WORK IS FINISHED. The heading asks; it does not congratulate. This component
// has no idea whether the user actually completed anything on the screen it sits at the foot of, and
// a "✓ Done!" over an unfinished job is exactly the kind of confident-but-wrong statement the rest of
// this codebase is careful to avoid.
import Link from 'next/link'
import { useCanOpen } from '@/components/ScreenLink'
import { runbookForScreen, stagesAfter } from '@/lib/flowcharts'

export function WorkflowNext({ here }: { here: string }) {
  const canOpen = useCanOpen()
  const after = stagesAfter(here)
  const book = runbookForScreen(here)

  // The next stage this viewer may actually open. Skipping a closed stage is deliberate: the point
  // of the prompt is to remove the hunt, and offering a 403 removes nothing.
  const next = after.find(s => canOpen(s.href))
  const skipped = next ? after.indexOf(next) : 0
  if (!next && !book) return null

  return (
    <section aria-label="What to do next" style={{
      marginTop: 26, padding: '14px 16px', border: '1px solid var(--border)', borderRadius: 10,
      background: 'var(--surface)', display: 'grid', gap: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.09em',
          color: 'var(--text3)' }}>
          Next in this workflow
        </span>
        {book && (
          <Link href={`/training/flowcharts/${book.slug}`}
            style={{ fontSize: 12, color: 'var(--text3)', textDecoration: 'none', marginLeft: 'auto' }}
            title={`See the whole ${book.title.replace(' Runbook', '')} flow`}>
            🗺️ See the whole flow →
          </Link>
        )}
      </div>

      {next ? (
        <>
          <Link href={next.href} style={{ textDecoration: 'none', color: 'inherit', display: 'grid', gap: 3 }}>
            <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--accent)' }}>{next.label} →</span>
            <span style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.5 }}>{next.does}</span>
          </Link>
          <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>
            {next.who}
            {/* Say what is being handed over. "Go here next" is an instruction; "you are handing them
                the verified figures" is a reason, and a reason survives a busy evening. */}
            {next.handoff ? <> · you are handing over {next.handoff}</> : null}
            {/* Never silently skip a step: if the immediate next stage is closed to this viewer, say
                so, so the chain still reads as a chain rather than appearing to have a gap. */}
            {skipped > 0 ? <> · {skipped} earlier step{skipped > 1 ? 's are' : ' is'} not yours</> : null}
          </div>
        </>
      ) : (
        <div style={{ fontSize: 13, color: 'var(--text2)' }}>
          This is the last step of the workflow that is yours.
        </div>
      )}
    </section>
  )
}
