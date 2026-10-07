// ChartNote — the ONE home for a chart's plain-language caption (owner request 2026-10-07:
// "how to read the Trends report in finance, explain under each graph what that means for a lay man").
//
// WHY THIS IS A COMPONENT AND NOT FOUR INLINE PARAGRAPHS. A caption that is typed into each chart
// card drifts: one card gets a bigger font, the next one a different colour, the one added next
// quarter gets no caption at all and nobody notices. The caption is a product affordance with one
// definition — what the reader sees under EVERY chart on a report — so it has one implementation and
// a lock (`backend/harness_trends_chart_notes.py`) that fails the build when a chart card stops
// dereferencing it.
//
// NOT `.pg-note`. The `.pg-note` gate (globals.css, help-context) hides explanatory text from
// everyone but a Master admin who has turned help on, which is right for a developer-ish page banner
// and wrong here: the people reading a Finance chart and needing "up means what?" are exactly the
// market managers who can never flip that switch. These captions are always rendered.
//
// RULE TWO: this component carries no copy of its own — every word is passed in by the page, so no
// carrier/tenant/product name can live here.
import { ReactNode } from 'react'

export default function ChartNote({ children }: { children: ReactNode }) {
  return (
    <p className="chart-note" style={{
      color: 'var(--text2)', fontSize: 11.5, lineHeight: 1.5, margin: '2px 6px 4px',
      maxWidth: 680,
    }}>{children}</p>
  )
}
