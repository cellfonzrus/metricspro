# Ask bar (natural-language query)

A global **Ask bar** — type a plain question and get an answer or jump to the right report. Opened with
**⌘/ (Ctrl-/)** or the **🔎 Ask…** button in the nav.

## Deterministic by design

Like the narrative banners, the ask bar is **not** LLM-backed. It recognises a **metric intent** and a
**period** from the words you type, then fetches the number from the **same endpoints the reports use** — so
a figure it shows can never disagree with the report, needs no API key, and can't be hallucinated. Anything
it doesn't recognise falls through to a ranked list of reports to open.

## What it does

1. **Quick answer.** Detects a metric intent + period and shows the value inline with a *View report →* link:
   - **net income**, **gross profit** → consolidated P&L (`/account/overview/{period}`)
   - **revenue** → Sales Report narrative facts
   - **activations** → Executive MTD narrative facts
   - **incentive payout** → summed commissions
   - Period parsing understands "last month", "this month", a month name ("sales august"), and `YYYY-MM`;
     it defaults to the app's current period.
2. **Jump to a report.** Ranks the **permission-filtered** report catalogue (`lib/reports.ts`
   `REPORT_CATEGORIES`, gated by `canSeeItem`) against the typed words. **Enter** opens the top hit.

## Extending

- **New metric answer** — add an entry to `INTENTS` in `components/AskBar.tsx`: keywords + a `resolve(period, orgQ)`
  that calls an existing endpoint and returns `{ value, href }`.
- **New report** — nothing to do; it comes from `REPORT_CATEGORIES`, which already backs the Reports Index.

## Notes

- The nav's existing **⌘K** menu-filter is unchanged; the ask bar uses **⌘/** so the two don't collide.
- Quick-answer intents hit permission-gated endpoints; if a user can't see a metric it fails gracefully to
  "open the report", and reports they can't access never appear in the jump list.
