# High-density data grid (`<DataGrid>`)

A reusable high-density table for the big reports, built on **TanStack Table v8** (headless) but driven by
the app's existing `ExportColumn[]` — the same columns a report already builds for export — so a page
adopts it **without redefining its columns**.

## What it adds over a plain `<table>`

- **Sticky header** — the column-header ribbon stays put while the body scrolls.
- **Pinned first column** — the store / rep / label column stays visible while a wide table scrolls
  sideways (TanStack column pinning computes the offsets; the header, body and totals cells stay aligned).
- **Click-to-sort** on every column (numeric columns sort numerically), and **drag-to-resize** column widths.
- **Pinned totals footer** — pass `totalRow` and it renders a sticky footer row.

## Usage

```tsx
import DataGrid from '@/components/DataGrid'

<DataGrid
  columns={cols}          // ExportColumn[] — the same array you build for export
  rows={rows}
  totalRow={total}        // optional: a row object rendered as a pinned TOTAL footer
  pinFirst                // default true — keep the first column visible on horizontal scroll
  maxHeight="72vh"
/>
```

### Per-column display formatting

`DataGrid` formats cells from each column's own `get` (money → currency, `type:'number'` → localized int,
else text). When a report needs its own formatting (percent, 2-dp, an em-dash for "not entered"), give the
column a **`render`** — a display-only function ignored by export:

```ts
{ header: 'Conv.', field: 'conv', type: 'number', get: r => r.conv, render: r => pct(r.conv) }
```

`ExportColumn` also takes an optional **`tip`** (header tooltip). Both are display-only; the exported values
still come from `get`, so the grid can never disagree with the export.

## Where it's live

- **Executive MTD** (`/commcalc/exec/mtd`) — 20+ metric columns with the Store/Employee column pinned, a
  sticky header, and a pinned TOTAL row. Reference implementation.

## Adopting it on another report

If the page already builds `ExportColumn[]` for export (most reports do, via `ReportShell` or an export bar),
pass that same array to `<DataGrid>`. Add `render`/`tip` only where a column needs custom formatting or a
header tooltip. Reach for `<DataGrid>` on the wide, hand-rolled tables that **don't** use `ReportShell`.

## Column controls (both grids)

- **Fit-content widths** — columns size to their content and are **not stretched** to fill leftover space
  (`width:auto` + `tableLayout:auto`). A wide table overflows into the horizontal scroll; a narrow one sits
  at its content width. `<DataGrid>` keeps a user-resized column's width; others stay content-fit.
- **Show / hide columns** — a **▦ Columns** menu lets each user pick which columns they see. Hidden columns
  leave both the table and the export (what you see is what you export). `ReportShell` persists the choice
  per report (keyed by filename/title); `<DataGrid>` persists it when given a `storageKey`. The last visible
  column can't be hidden.
- **Pinned first column** — on by default (see below).

## Reports that DO use `ReportShell`

`ReportShell` already has sort, resize, sticky header (opt-in), sticky totals and grouping — so don't swap it
for `<DataGrid>` (you'd lose its filtering / grouping / export / Send). It now also has the **pinned first
column** via the **`pinFirst`** prop, **default on** (owner 2026-08-29 "batch onto the wide reports"): it
only has a visible effect when a table actually scrolls sideways, so narrow reports are unaffected. Pass
`pinFirst={false}` to opt a report out.

```tsx
<ReportShell columns={cols} rows={rows} totals stickyHeader defaultGroupBy="Store" … />   // pinned by default
```

Live everywhere `ReportShell` is used; e.g. the **Sales Report** (`/commcalc/sales-report`).
