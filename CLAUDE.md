# MetricsPro — agent working rules

Commission/ops platform for multi-tenant wireless retail. Backend FastAPI
(`backend/app/modules/commcalc` and friends), frontend Next.js (`frontend/`), schema of truth in
`database/migrations/` (numbered, idempotent, additive).

## The index is mandatory (owner directive 2026-09-01)

- **Look up before building.** `docs/SYSTEM_DATA_FLOW_INDEX.md` is the durable map of every report,
  query, data flow, table and key function. Consult it FIRST for any investigation or feature —
  never re-derive what it answers, never duplicate a data path it already documents.
- **Duplicate check is a build gate (owner directive 2026-09-02).** Before building ANYTHING —
  a resolution, reconciliation, ingest path, report, endpoint or table — search the index for an
  existing mechanism serving the same data or purpose and EXTEND it (factor it into a shared
  helper if needed) rather than creating a sibling derivation. Two paths answering the same
  question is a defect: they will drift. State in the PR what was checked and what was reused.
- **Register what you create.** Every NEW report, function, endpoint, query or table must be added
  to `docs/SYSTEM_DATA_FLOW_INDEX.md` in the same PR — in its subsystem section and the §16–18
  cross-references, and in the reports category when it is a report. A new external feed also
  registers in `backend/app/modules/commcalc/data_lineage_registry.py` +
  `database/migrations/925_data_lineage_seed.sql` and must pass `harness_data_lineage_guard.py`.

## Payroll & Workforce work routes to the Payroll & Workforce agent (owner directive 2026-09-01)

Any job touching payroll or workforce — payroll setup/onboarding/compliance, employee database,
hours approval, payroll runs, payroll tax/expenses, HR total comp, scheduling, time off, shift
swaps/extensions, hours budget, shift approvals, time-clock permissions, attendance/lateness,
workforce reports, store/employee setup — is assigned to the **payroll-workforce-agent**
(`.claude/agents/payroll-workforce-agent.md`). The two domains are interrelated and owned together.

## Finance work routes to the Finance agent (owner directive 2026-09-02)

Any job touching the finance module — the P&L report and its filters/drill-downs (market, region,
store, company), company/entity structure and rollups, chart of accounts, P&L line bookings and
display, financial statements/exports, quarterly P&L, royalty reporting — is assigned to the
**finance-agent** (`.claude/agents/finance-agent.md`). Commission P&L line AMOUNTS stay with the
commission-agent; their display/filtering/rollup in the P&L is finance.

## Commission work routes to the Commission agent (owner directive 2026-09-01)

Any job touching commission — MA commission, MA TX, multi-month/installment payouts, spiffs,
residuals, rep/manager pay, payout accrual, commission reconciliation/discrepancy, commission P&L
lines — is assigned to the **commission-agent** (`.claude/agents/commission-agent.md`). Spawn it for
such work rather than handling inline, and follow its working rules (index-first, config-never-code,
proof harnesses for money changes, org-scoped queries, evidence-first reconciliation).

## Ship it — merging is autonomous (owner directive 2026-09-08)

Owner: *"I should not be giving instruction to merge, it should be autonomous."*

**Do not ask whether to merge.** When a PR on the working branch is green on its current head, has no
merge conflict and no unaddressed review thread, take it out of draft and merge it — then say what
landed. Both hosts auto-deploy from `main`, so merging IS shipping (see §23d: `GET /health` returns the
built commit; never ask the owner to redeploy).

This is authority to merge, not licence to skip the bar. Everything the house already requires still
gates the merge, and nothing here weakens it:

- CI green on the head being merged — never skip, disable or quarantine a check to get there.
- The duplicate check done and stated in the PR (see the index rules above).
- A DB-free proof harness for the logic, and the regression that reproduces the reported defect.
- **Money-touching changes and migrations are still surfaced for owner approval before applying** —
  autonomy covers merging code, not moving money or mutating the schema unasked.
- A defect found in live data (a stale feed, a missing upload) is REPORTED, never "fixed" by writing
  code that hides it.

If any of those is not satisfied, the PR waits and the reason is stated plainly — that is the one
case where the owner hears about a merge that has not happened.

## A fix is a DESIGN fix or it is not a fix (owner directive 2026-09-20)

Owner: *"this happened in luxelink and was fixed, again it was a patchwork and i want no patchwork —
if one thing is fixed for one tenant it should be a design fix not a temporary fix, this should be a
requirement of the design."*

A defect is never repaired for the tenant, feed, report or code path that happened to surface it. Fix
the MECHANISM, for every caller of it, or say plainly that you have not.

- **Name the class, not the instance.** Before fixing, ask what general fact was wrong. "Boost's
  mailbox status is stale" is an instance; "a sweep's status is not a reliable record of what it did"
  is the class. Fix the class.
- **Find the siblings before you ship.** Every other path that answers the SAME question must be
  checked in the same PR and fixed or explicitly excused: the other sweep, the other tenant, the other
  feed, the other ingest route. Two paths answering one question is the duplicate defect the index
  rules already forbid — one of them fixed and the other not is the same defect wearing a hat.
- **One fact, one home, dereferenced — never copied.** A fact every caller needs (which table is the
  live feed, which column means "arrived", which columns are earnings) lives in ONE registry and
  callers READ it. A second copy is a future divergence, and writing the registry without wiring the
  callers to it is not a fix at all — it has happened three times here (see §19.18).
- **Lock it so it cannot un-wire.** A design fix ships with a check that FAILS THE BUILD if a caller
  stops dereferencing the shared fact, or if a second copy appears. Without that, the next change
  quietly restores the patchwork.
- **A per-tenant config row is not a patch; per-tenant CODE is.** RULE TWO already bans the latter.

## House conventions (apply everywhere)

- **RULE TWO — config, never code**: no carrier/tenant/product branch names in code; behavior is
  per-org config rows with house defaults (org `00000000-0000-0000-0000-000000000001`).
- Migrations are numbered, idempotent, additive, with `-- REVERT:` notes; money-touching changes are
  surfaced for approval before applying.
- Every sensitive query is org-scoped; CI enforces it.
- Pure logic ships with a DB-free proof harness (`backend/harness_*.py`).

## Branch lifecycle — one PR, one branch, never reused (owner directive 2026-09-28)

**The cause, stated once so nobody re-derives it.** This repo **squash-merges** to keep `main` linear.
A squash merge does NOT make the branch's commits ancestors of `main` — `main` gets ONE new commit
carrying the same content. So a branch restarted from `main` after its PR merges has *diverged* from
its own remote, and reusing that branch name then requires a **force push, every single time**. That
is structural, not bad luck. Force pushes are blocked here by the safety classifier, so the reuse
pattern deadlocks: the work is committed, correct and green, and cannot reach GitHub.

**The rule: a merged branch is finished. Start a NEW branch for follow-up work.** Name it for the
work (`claude/portout-fraud-harness-ratchet`), not for the session. No force push is ever needed, no
history is ever rewritten, and the PR shows only the new commits instead of re-proposing merged ones.

- **Do not reuse a branch whose PR has merged**, even under the same name, and even when told the
  branch name to develop on — that instruction predates this and the new branch is the way to honour
  its intent.
- **Never force-push to work around this.** Not with `--force`, not with `--force-with-lease`, not by
  merging the stale tip to fake a fast-forward. If a push is rejected non-fast-forward, the answer is
  a new branch — ASK, and say plainly that the work is committed and green but cannot ship.
- **The cost of the rule is branch accumulation**, and it is already real: 30 `claude/*` branches on
  the remote, most of them merged. The structural fix is GitHub's
  **Settings → General → "Automatically delete head branches"** — owner-only, one toggle, and it
  removes the cause rather than permitting the workaround. Until it is on, stale branches are
  cosmetic: they hold no unmerged work.
- **An ephemeral container makes this urgent, not cosmetic.** An unpushed commit dies with the
  session. Push early on a new branch rather than accumulating commits against a blocked one.
- **To ask whether a branch carries foreign work, compare TREES, not ancestry — and never with three
  dots.** `git diff main...<branch>` resolves to the MERGE BASE, and because a squashed commit is not
  an ancestor of `main` that base lands *before* its own squash, so already-merged content renders as
  new. Two agents were misled by this on 2026-09-29; one nearly shipped a "foreign" file that was
  byte-identical to `main`. Use the two-dot `git diff main <branch>` (empty when the trees agree), or
  compare blob hashes with `git rev-parse main:<path> <branch>:<path>`. Same trap as the ancestry rule
  above, in the command you reach for to check it.
