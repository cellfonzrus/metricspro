# Family H — the 14 harnesses that RAN and DISAGREED WITH THE PRODUCT

These fourteen were different from the crashes the other agents handled: every one of them executed
to completion and then *asserted something that turned out to be false*. Each failure was therefore
either a real defect or an expectation the product had deliberately moved past, and the only way to
tell was to read both sides. This log gives the verdict for each, with the evidence.

**Before: 14 files failing. After: 12 green, 2 deliberately left with one failing assertion each,
naming a real open defect.**

---

## READ THIS FIRST — security and money findings

### 1. OPEN DEFECT (not fixed — needs your decision): AI usage metering blocks the event loop

This is the SEV-1 of 2026-07-30 coming back through a side door, at smaller scale.

`backend/app/modules/billing/ai_meter.py::record()` is an ordinary **synchronous** function. It ends
in a real PostgREST round trip:

    get_supabase_admin().schema("core").table("ai_call_audit").insert(row).execute()

It is called **bare, un-awaited, from inside `async def` handlers** at four sites:

| module | function |
| --- | --- |
| `backend/app/modules/commcalc/agency.py` | `_ocr_parse_transfer_async` |
| `backend/app/modules/closing/router.py` | `_ocr_bank_deposit_slip` |
| `backend/app/modules/helpdesk/router.py` | `ai_assist` |
| `backend/app/modules/remediation/router.py` | `_ai_diagnose` |

Because the backend runs one uvicorn worker, there is one event loop. A synchronous HTTP call made
from a coroutine occupies that loop for its entire duration, and **nothing else in the product can
progress meanwhile — `/health` included.** That is verbatim the mechanism described in
`backend/app/modules/account/ai_limits.py`.

Normally the insert is tens of milliseconds. The worst case is what matters: the postgrest client
timeout is **120 s** by default (`postgrest.constants.DEFAULT_POSTGREST_CLIENT_TIMEOUT`), and
`db_resilience` deliberately **never retries a POST**. So when the database is slow or hanging —
precisely the outage `harness_identity_backend_503.py` exists to handle gracefully — one invoice OCR
or one Ask-AI question freezes the whole platform for up to two minutes. Two orders of magnitude
smaller than the original thirty-minute freeze, same failure mode.

**Why I did not fix it.** The one-line repair works and I verified it: wrapping the call as
`await asyncio.to_thread(_ai_meter.record, ...)` turns the assertion green. But the same bare call
appears at four sites in four modules, so the right repair is one shared off-loop path owned by the
billing module, not four hand-patches — and three other agents were editing this worktree at the
time. Per the brief, a real defect that is a judgement call is left **failing and named** rather than
half-fixed. The two assertions are `H1` in `harness_agency_ocr_async.py` and
`harness_closing_ocr_async.py`. Please do not silence them by deleting them; they go green the moment
the metering call goes off the loop.

**Deploy note:** this is pre-existing, already in production, and not made worse by anything here. It
does not by itself block a deploy, but it should be scheduled deliberately rather than discovered
during the next database slowdown.

### 2. FIXED: a privilege-escalation alarm that had been silently asserting nothing

`harness_privesc_rbac_gates.py` guards the RBAC endpoints against a logged-in non-admin escalating
their own role, and against the cross-tenant version of that attack (role ids are a global
`BIGSERIAL`, so an unscoped `UPDATE` lets one tenant rewrite another tenant's role).

**The product is correct.** `update_role` still scopes its write to `.eq("id", role_id).eq("org_id",
org_id)`.

**The alarm was dead.** Commit `021827c3` ("core/router roles + settings → typed models") changed
these handlers from `body: dict` to typed Pydantic models. The harness still passed raw dicts, so the
handler died on `AttributeError` at `body.model_fields_set` — inside a bare `except Exception: pass`.
No write was ever recorded, so:

* the cross-tenant org-scoping assertion had been passing over an empty list, proving nothing;
* the **negative control** — the check that proves the gate is what stops the attack — was also dead.

This is the same defect class already found in `harness_ssrf_import_gate` (commit `564c171f`). The
assertions were not weakened: they now build the real request models, and I hardened the harness so
this cannot recur silently — a `TypeError`/`AttributeError` from a signature mismatch is re-raised as
a wiring error instead of being scored as "the gate let me through" or "no write happened".

Self-tested: removing `.eq("org_id", org_id)` from `update_role` makes it fail; restored, it passes.
**52 → 55 assertions, all green.**

### 3. FIXED: a real event-loop defect — `connect_tenant` ran blocking I/O on the loop

`backend/app/modules/core/router.py::connect_tenant` was declared `async def`, awaits nothing, and
its body is blocking Supabase I/O. That is exactly the shape `harness_nav_perf.py` exists to prevent
and that its package converted 124 handlers away from; this endpoint was added afterwards and missed.
Converted to a plain `def` (FastAPI then dispatches it to a worker thread). No internal caller awaits
it, so the change is contained. Self-tested both ways.

### 4. Money: no calculation drift found, and one owner-directed change confirmed

Every money-related failure traced to a deliberate, documented change — none was a wrong number.
The GP report's `acc_gp` reading higher than expected is **owner directive 2026-09-02**: *"Acc Gp
should show the price at which the accessories were sold not the Gross profit as they are not entered
correct … renamed to Acc Sales"* (commit `8ce5570d`, mig 932). It is a per-org config column
(`accessory_config.gp_acc_basis`) with a house default of `sales`, **not** a tenant branch — RULE TWO
is satisfied. I checked: `luxelink` appears in the backend only inside explanatory comments, never as
a behaviour condition.

---

## The one pattern behind most of these failures

Eleven of the fourteen failed for the same underlying reason, worth stating plainly because it will
keep happening:

> **These harnesses were written to guard a pull request, and they were pinned to the state of the
> world on the day that pull request landed.** They assert things like "only these two files differ
> from `origin/main`", "this function is byte-identical to base", "exactly these three reports are
> registered", "the route count is 1042". Every one of those claims stops being true the moment the
> package merges into main, or another package lands on the same long-lived branch.

A frozen claim like that cannot distinguish "someone broke this" from "the change shipped and the
world moved on" — and it fails *precisely when the work is fully landed*. Left red, it trains
everyone to skip the file's output, which is how the dead privilege-escalation control above went
unnoticed. Wherever I found one, I kept the durable half and retired the per-PR half **with the
reasoning written into the file**, so nobody has to re-derive it. Where a per-PR check had no durable
form at all (merge-hygiene assertions about which files a branch touches), I removed it and said why.

---

## Per-file verdicts

| # | Harness | Failing assertion | Verdict | Self-tested |
| --- | --- | --- | --- | --- |
| 1 | `privesc_rbac_gates` | negative control + cross-tenant org scoping | **Dead alarm** — `021827c3` retyped bodies; harness fed dicts, writes never happened. Product correct. | yes |
| 2 | `identity_backend_503` | `_reject_401` byte-identity, `_is_public` byte-identity, 2 webhook deltas | **Superseded** — package merged to main; asymmetric slice markers and "delta still pending" checks. 401 bytes verified identical. | yes |
| 3 | `finance_sync_in_async` | `/compute` hops to threadpool + 3 "only X changed" | **Superseded** — `386a196d` re-pointed `/compute` at `statement_engine.compute_and_store`; still threadpool-hopped. SEV-1 protection intact. | yes |
| 4 | `agency_ocr_async` | F1/F2 + G1/G2/G3a-d/G5/G6a/G7 | **Superseded** (package merged; branch-scope checks) **+ 1 REAL DEFECT** (on-loop metering, left failing) | yes |
| 5 | `closing_ocr_async` | F2 route surface, F3 shared-file grep | **Superseded** — a route added by another package; F3 fails identically on main. **+ same open defect** | yes |
| 6 | `commcalc_recompute_guard` | body byte-identity + file-list, `'flags'` count | **Superseded** — base pinned to `da961df`, 20+ commits back. Money vocabulary re-verified. | yes |
| 7 | `comp_daily_sweep` | H5/H6/H9/H10/H12/H13 | **Superseded** — `8a5b419b` registered a 4th report (`epay_daily_tx`), due every tick by design. | yes |
| 8 | `import_batches` | 1a degrade-open, 5a sweep status | **Harness safety defect + superseded** — see below. Product correct. | yes |
| 9 | `gp_luxelink_columns` | 1b, 4a, 9d, 7 (`acc_gp`) | **Superseded** — owner directive 2026-09-02 / mig 932, config-driven. | yes |
| 10 | `nav_perf` | E1/E2 route count, B1/B2/B3 keyword-only | **Harness broke on FastAPI 0.141** (see below) **+ 1 REAL DEFECT fixed** (`connect_tenant`) | yes |
| 11 | `notify_failure_leads` | F5/F6 registry membership | **Superseded** — closing_*/storeops_* report families registered since. | yes |
| 12 | `people_attention` | C (4 checks), F heavy providers | **Superseded** — mig 420 + global kill switch gate the kiosk face provider. | yes |
| 13 | `pos_onboarding` | P11 route resolution | **Harness broke on FastAPI 0.141** — every probe resolved to `None`. Routing correct. | yes |
| 14 | `team_snapshot_perf` | 1.1, 4.1, 9.x byte-identity; 6.2 org-scoping; 8.1/8.2 read budget | **Superseded / detection too narrow** — see below. No value drifted. | yes |

### Notes on the ones that needed real judgement

**`import_batches` — a "no database" test that was writing to a live database.** PART 1 is documented
as *"OFFLINE (no database, no keys; runs anywhere, including CI)"*, and assertion 1a relied on there
being no `SUPABASE_KEY` in the environment to make the database unreachable. That is an assumption
about the shell, not a property of the test. This container **does** export `SUPABASE_URL` and
`SUPABASE_SERVICE_KEY`, so `claim()` built a real client and ran a real `INSERT` into
`import_batches`, then reported `duplicate` — the row it had written on an earlier run. The database
is now made unreachable **by construction** (the client factory is patched to raise), which is both
the harsher test and genuinely offline. Assertion 5a expected status `'skipped'`; commit `c5a5cc93`
deliberately gave duplicates their own **terminal** status because `'skipped'` is the *retryable*
bucket and b2b's hourly re-send was spamming the history. Re-expressed to assert the new status *and*
that it is not the retryable one — stronger than the original.

**`nav_perf` / `pos_onboarding` — FastAPI 0.141 stopped flattening routes.** `include_router` now
contributes one `_IncludedRouter` object holding its children rather than splicing them into
`app.routes`. So `len(app.routes)` read **31** where the harness expected 1042, and
`/api/v1/core/attention` was "missing" — while the app in fact serves **1285 OpenAPI paths** and that
endpoint is present. `pos_onboarding` P11 failed the same way: it scanned for `path_regex`, found
none, and reported the catch-all as shadowing every literal prefix. Both now collect routes in a
version-independent way (OpenAPI schema; recursive walk that preserves declaration order). P11 is
strictly better than before — it now detects the exact `/{module_key}/{anything}` shadowing its own
comment warns about, which the broken version could never have caught.

**`nav_perf` B1-B3 replaced with something that cannot rot.** They asserted the sweep converted
exactly 119 handlers and that re-inserting `async ` reproduced each base file byte-for-byte. Those
files have since taken unrelated edits, so the reconstruction can never match again. Replaced with
the invariant the sweep exists to create, which needs no baseline: **no `async def` handler in the
owned surface may lack an `await`.** That is what found `connect_tenant`.

**`team_snapshot_perf` — the performance numbers, measured.** Two things were conflated.

*Read budget.* `8.1/8.2` asserted `new_total < old_total`. Measured today: manager **69 (OLD) → 70
(NEW cache miss) → 1 (memo hit)**; owner the same. The +1 is not the snapshot getting slower. `OLD` is
a frozen reference path and the two are no longer feature-equivalent — per table, NEW collapses
`carrier_kpi_metric` from **13 reads to 1** (exactly the N+1 the pushdown removed) while *adding*
tables OLD never read at all: `metric_source_of_truth` (2), `carrier` (2), `payout_exclusion_map` (1),
`raw_custom_import` (1), `store_merchant_id` (1), `commission_org_config` (1). **Verdict: the budget
was measuring feature drift between two different code paths, not a regression.** It now asserts what
the package actually promised, still as hard numbers: the memo hit collapses the chain to a constant
(measured 1 read), and the N+1 stays collapsed (`carrier_kpi_metric` read O(1) times, measured 1
across 6 stores, versus 13 on the old path). Per-store passes still shrink 6 → 2 for a manager.

*Byte-identity.* `1.1`, `4.1` and `9.x` compared whole payloads. The only differences are **additive
new fields** — `acc_sales_ex_setup`, `is_active`, `setup_fee_mtd_exec`, `trending_acc_target` — with
no existing value changed. Replaced with a recursive comparator asserting **every value the reference
produced is identical**, additions listed. Self-tested with a one-cent perturbation of
`money_on_table`: it fails and names each drifted field and path, which byte-identity never did.

*Org scoping (`6.2`).* Read `exec_metric_config` via `.in_('org_id', [org, house_default])` — the
mig 962 config-inheritance pattern — and the check only recognised `.eq`. The read **is** scoped; 6.3
(two tenants sharing tables) already proved isolation independently. Now checks the *values*, so an
`.in_` naming any third org still fails.

---

## How each was proven to genuinely execute

Every file was verified by breaking something it asserts, confirming the failure, then restoring —
all 14, no exceptions. The sabotages were chosen to be the real regression, not a cosmetic edit:
removing `.eq("org_id")` from `update_role`; adding a rogue path to the public allowlist and altering
the 401 responder; removing the `/compute` threadpool hop *and* separately adding a second un-hopped
call (which the old literal check could not have caught); hoisting a wholesale `flags` DELETE onto the
normal path; making `comp_report` an every-tick report; putting duplicates back in the retryable
bucket; making the accessory basis ignore its config; reverting `connect_tenant` to `async def`;
adding `/{module_key}/{anything}`; dropping `wants_auth` from the `gp` report; removing the mig-420
face gate; and perturbing `money_on_table` by one cent.

All product files were restored afterwards; the only intentional product change in this branch is the
`connect_tenant` conversion.

## Housekeeping

* All 14 run from **both** `backend/` and the repo root (the pattern from `564c171f`).
  `notify_failure_leads` needed anchoring — it used `sys.path.insert(0, ".")`, relative `open("app/…")`
  and `cwd=".."`, so from the root it imported nothing and died on its first `git show`.
* No migrations were needed.
* `docs/SYSTEM_DATA_FLOW_INDEX.md` deliberately untouched.
