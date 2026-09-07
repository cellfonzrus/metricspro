# Harness repair — families C–F: async drift, framework-API drift, missing dep, source drift

Date: 2026-09-06. 11 proof harnesses, four different systemic causes.

Before: **0 of 11 passing**, and — the part that matters more — most of them were not *failing*, they
were *dying*, part-way through, with an exception that reads like a broken test rather than a broken
product. After: **11 of 11 passing** from both `backend/` and the repo root, 1,132 assertions
executing, 11 of 11 self-tested by breaking something each one claims to prove.

---

## Read this first: the one real product defect

**`GET /commcalc/flags/{period}` was `async def` while doing blocking database I/O.**

`sb()` is the *synchronous* supabase client (`app/core/database.py`, `create_client`, blocking
httpx). FastAPI runs a plain `def` handler in a worker thread, so a blocking call there costs one
thread. It runs an `async def` handler **on the event loop**, so the same blocking call stalls every
other in-flight request for its whole duration. `get_flags` was `async def` and awaited nothing.

That is, verbatim, the SEV-1 of 2026-07-30 (`account/ai_limits.py`: a sync client called from an
async endpoint froze the backend). Every load of the Flags & Compliance page was stalling the whole
API for the length of a supabase round-trip.

Fixed, because it is two lines and unambiguous: `async def get_flags` → `def get_flags`
(`backend/app/modules/commcalc/router.py`), and its single internal caller
`backend/app/modules/notify/report_registry.py::_flags` dropped its `await`. Verified no other
caller awaits it. It is now pinned by a new section 7 in
`harness_chargeback_flags_span_store.py`, so re-adding `async` fails loudly.

### The same defect exists in 45 more places — NOT fixed, needs a decision

A sweep of every route handler in `backend/app/` finds **46 `async def` handlers that touch `sb()`
and contain no `await` at all**, of which `get_flags` was one. Each is the same latency and
availability bug. A sample:

```
app/modules/account/router.py:636   get_pl
app/modules/account/router.py:653   get_bs
app/modules/account/router.py:668   get_cf
app/modules/asset/router.py:596     resync_market
app/modules/asset/router.py:610     get_asset_summary
app/modules/asset/router.py:1196    inventory_recon
app/modules/asset/router.py:1409    get_owed_weekly
app/modules/asset/oninv_recon.py:182 get_oninv_3way_recon
app/modules/billing/pricing.py:51   public_pricing
… 37 more
```

I deliberately did not change these. Removing `async` from 45 handlers across nine modules is a
product change of real blast radius (each has its own callers to check) and it is not a harness
repair — it needs owner sign-off and its own PR with a proof harness. **The finding is the
deliverable here.** Note the codebase already knows the right pattern where someone applied it:
`router.py:27500` reads
`files = await _asyncio.to_thread(_email.fetch_new_attachments, cfg, already, _unrouted)` with the
comment *"to_thread (2026-08-04 outage fix): fetch_new_attachments is SYNCHRONOUS imaplib network
I/O"*. That is the correct treatment when a handler must stay async; a plain `def` is the correct
treatment when it needn't.

**Recommended detector:** the AST check is ten lines and finds all 46 with no false positives. It
belongs in CI next to the org-scope guard.

### The reportlab question — answered: NOT a production defect

The brief asked me to determine this before anything else.

- The product **needs** it: `notify/render.py::build_pdf`, `commcalc/payout_structure.py`,
  `commcalc/commission_statement.py` and one more import reportlab lazily inside live render calls.
- The product **declares** it: `backend/requirements.txt` line 13, `reportlab>=4.2.0`, uncommented.
- The deployment **installs** it: `backend/Dockerfile` line 4,
  `RUN pip install --no-cache-dir -r requirements.txt`.

So the deployed image has reportlab and no PDF export is broken. What was missing was **this harness
container's copy** — it is short seven declared dependencies (`uvicorn`, `reportlab`, `pdfplumber`,
`segno`, `anthropic`, `playwright`, `pywebpush`). That is an environment provisioning gap, not a
shipped-code defect. Worth fixing in whatever builds the harness container, since seven declared
deps missing means other harnesses are silently degraded too.

---

## Per-file verdicts

| # | Harness | Verdict | Why it failed | Self-tested |
|---|---------|---------|---------------|-------------|
| C1 | `harness_chargeback_flags_span_store.py` | **stale test + REAL DEFECT** | hard-coded `run_until_complete()`; `get_chargebacks` had correctly become sync. Reviewing the shape found `get_flags` async-over-blocking | yes |
| C2 | `harness_commission_leg_split.py` | stale test | 15 hard-coded `run_until_complete()`; also passed a dict where `SetCommissionLegLabelIn` is declared | yes |
| C3 | `harness_core_grant_universe.py` | stale test | hard-coded `run_until_complete()`; plus assertion 12b predated the orphan-store rule | yes |
| C4 | `harness_multifile_docs.py` | stale test | hard-coded `await` on `_do_onboard_delete_document`, now correctly sync | yes |
| D1 | `harness_training_center.py` | stale test | walked `app.routes` for `.path`; fastapi 0.141 keeps included routers as `_IncludedRouter` | yes |
| D2 | `harness_whats_new.py` | stale test | same, plus `SEED_VERSION == 9` literal (it is 14) | yes |
| E1 | `harness_export_xss_upload.py` | env gap + stale test | reportlab absent; then dict-vs-model in two places, and stale literals in F1/G1b/G-block | yes |
| E2 | `harness_payout_structure.py` | env gap | reportlab absent | yes |
| F1 | `harness_ai_assist_async.py` | stale test | `NameError: AiAssistIn` — handler gained a declared body model; killed the LIVE SEV-1 proof | yes |
| F2 | `harness_attention_clears.py` | stale test | split on the literal `"async def add_store_alias"`; it is a plain `def` now | yes |
| F3 | `harness_sweep_honesty.py` | stale test | 2-arg stub for a 3-arg fetcher; differential anchored to the moving ref `origin/main` | yes |

No harness was left with a weakened, skipped or deleted assertion in order to go green. Where an
assertion could not run I made it a **counted, printed SKIP** — never a pass — and left a way to run
it (below). Where a literal had rotted I re-expressed it as the property it was standing for, in the
manner of commit `564c171f`.

### Assertion counts (all previously dead)

| Harness | Assertions now running |
|---|---|
| `harness_training_center.py` | 295 |
| `harness_whats_new.py` | 158 |
| `harness_commission_leg_split.py` | 148 |
| `harness_attention_clears.py` | 105 |
| `harness_export_xss_upload.py` | 104 (+7 skipped) |
| `harness_payout_structure.py` | 87 |
| `harness_sweep_honesty.py` | 84 |
| `harness_ai_assist_async.py` | 49 |
| `harness_multifile_docs.py` | 47 |
| `harness_core_grant_universe.py` | 33 |
| `harness_chargeback_flags_span_store.py` | 22 |

---

## Family C — async/sync mismatch (4 files)

**What happened.** Four handlers stopped being `async def`. That change was *correct*: all of them
drive the synchronous supabase client and await nothing, so a plain `def` gets them off the event
loop and into a worker thread. The harnesses had hard-coded
`asyncio.get_event_loop().run_until_complete(handler(...))`, which raises `TypeError: An
asyncio.Future, a coroutine or an awaitable is required` the moment the handler returns a plain
value — on the *first* call, taking every assertion in the file with it.

**The systemic cause.** These harnesses coupled themselves to a handler's *async/sync shape*, which
is not part of its behavioural contract. Shape is an implementation decision that should be free to
change — and in this codebase it *must* be free to change, because changing it correctly is how the
SEV-1 class gets fixed. A test that breaks when the product is improved is a tax on improving it.

**The fix.** Every call now goes through a small shape-agnostic helper:

```python
def run(result):
    if inspect.isawaitable(result):
        return asyncio.run(_drain(result))
    return result
```

**How to stop it recurring.** Two things. (1) Never write `run_until_complete(handler(...))` in a
harness; use the helper. (2) Where the shape genuinely *is* the contract — a handler doing blocking
I/O must not be `async def` — assert it *explicitly and separately*, as section 7 of
`harness_chargeback_flags_span_store.py` now does with an AST check. That is the difference between
a harness that breaks when shape changes and one that *guards* shape. The first is noise; the second
is what caught the `get_flags` defect.

**Also found here.** `harness_core_grant_universe.py` assertion 12b predated the orphan-store rule
(`app/core/scope.login_grant_breakdown`, owner "fix by design" 2026-08): a store in no market in
either vocabulary is folded into any login holding a market grant. In the degraded fixture the one
table that knows P301's market is down, so P301 becomes an orphan and is correctly picked up. Rather
than loosen the assertion I split it in three — survival, no cross-tenant bleed, and an explicit
**KNOWN FAIL-OPEN** naming the widening — plus a non-vacuity check (12b-iv) proving the same store
*is* excluded when the market vocabulary is healthy.

---

## Family D — FastAPI introspection (2 files)

**What happened.** Both files enumerated the route surface with
`{r.path for r in app.routes}`. That only ever worked because FastAPI happened to *flatten*
`include_router()` into one list. As of fastapi 0.141 an included router stays in `app.routes` as a
lazy `_IncludedRouter` wrapper with no `.path`, so the walk raised `AttributeError`.

**The systemic cause.** The harnesses asserted against a **private structure** of a third-party
framework. `app.routes` is not a documented enumeration API; its contents are an implementation
detail, and it moved. This is the lesson worth recording from this family: *reach for a framework's
public contract, never its internal storage.* The tell is that nothing in the product changed — the
routes were all exactly where they should be — yet both harnesses died.

The measurement was not merely broken, it was **misleading**: `len(app.routes)` now reads **31** for
an app exposing **1,285 paths**. Two assertions were pinning a total route count off that number.

**The fix.** Both files now enumerate via `app.openapi()["paths"]` — the supported, public
description of an app's route surface, which resolves prefixes and nested includes and is the same
document the frontend and the docs page consume. It also yields methods, so "the package adds
exactly 7 routes" is now counted as (path, method) pairs, which is what it always meant. Each file
gained a **non-vacuity check** (`I6` / `I9`) asserting the enumeration is non-empty and contains a
known route — because an enumeration that silently returns nothing would let every route assertion
pass vacuously, which is precisely how this class hides.

**How to stop it recurring.** Grep for `app.routes`, `router.routes`, `.routes[` in harnesses and
route them all through `openapi()`. More generally: if an assertion reads an attribute you would not
find in the framework's documentation, it is a future silent failure.

---

## Family E — missing dependency (2 files)

Root cause and verdict are in "the reportlab question" above: environment gap, not a product defect.

**The systemic cause of the *harness* problem** is subtler and worth stating. The obvious repair —
wrap the import in `try/except ImportError: pass` — would have been **worse than the crash**.
`harness_export_xss_upload.py` C1 is a *negative control*: it asserts the pre-fix renderer **crashes**
on hostile tenant markup, by catching `Exception`. `ModuleNotFoundError` is an `Exception`. So with
reportlab absent, C1 was already being recorded as a **PASS** — the control "fired" without the code
under test ever being reached. A blanket try/except would have quietly extended that lie across the
whole section.

**The fix** is a gate that distinguishes the two cases that look identical from inside a test run:

- installed → run the assertions for real;
- missing **but declared in `requirements.txt`** → SKIP, counted and printed, never a pass;
- missing **and undeclared** → **FAIL loudly**, because shipped code importing a package the
  deployment does not install *is* a production defect.

That third branch is the point: the gate now detects the real version of this failure rather than
tolerating it. Both harnesses also gained **backend-independent** halves of the same guarantees, so a
missing PDF backend can never again take the whole escaping proof with it — the escaping is pure
string work and is asserted from source either way (`C4`/`C5`, `H4b`–`H4d`).

I then installed reportlab and confirmed the *run* branch: `harness_payout_structure.py` 87/87 with
no skips, `harness_export_xss_upload.py` 104 passed with the PDF section fully executing including
its negative controls. Both branches of the gate are therefore exercised.

**Worth knowing:** with escaping deliberately removed from `payout_structure.render_pdf`, the live
`H4` render assertion still passed under reportlab 5.0.1 — only the new source-level `H4c` caught it.
`H4` is weaker than it looks; `H4c` is what actually guards that behaviour now.

**Also repaired in `harness_export_xss_upload.py`** (all surfaced only once the reportlab crash
stopped masking them):

- **`D5`, a security assertion, had silently stopped guarding.** It probed the portal-report href
  gate with a dict where `SetPortalReportIn` is declared, so every probe raised `AttributeError` —
  and a bare `except Exception: pass` swallowed it, leaving `rejected = 0`. The gate itself is
  **fine**: with the declared model built, all 22 unsafe hrefs are rejected with 400. I narrowed the
  blanket except so a harness-side error is now reported (`D5a`) instead of counted as "not
  rejected".
- **`F1`** asserted "there are NO direct `openpyxl.load_workbook` calls in `backend/app`". Two have
  since been added; **both pass `read_only=True, data_only=True`**, so the H5 hardening is intact.
  Re-expressed as the property it meant — *every* direct call passes `read_only=True` — plus
  `data_only=True` and a non-vacuity check. A third safe call site can no longer break it; an unsafe
  one now fails, which the old absolute could not distinguish.
- **`G1b`** pinned the middleware stack as an exact list of five; two unrelated middlewares were
  registered since. Re-expressed as the four **relative ordering** constraints it encoded, all of
  which still hold.
- **`G2`–`G6b`** are PR-review blast-radius checks (see below).

---

## Family F — source drift (3 files)

**What happened.** These harnesses read router *source text* and split it on string literals. When
the anchor moved, `split(...)[1]` raised `IndexError` and the file died.

- `harness_attention_clears.py` split on `"async def add_store_alias"`. The function is a plain
  `def` now — i.e. **family C's change is what broke family F**. Died at line 185 of 800.
- `harness_ai_assist_async.py` exec'd the extracted `ai_assist` against stub globals; the handler
  gained a declared `AiAssistIn` body, whose annotation is evaluated at `def` time, so exec raised
  `NameError`. This killed section **D — the LIVE proof that a stalled model call no longer freezes
  the event loop**. The harness written to guard a SEV-1 had stopped guarding it.
- `harness_sweep_honesty.py` — see its own note below.

**The systemic cause.** Two compounding habits. First, **string-splitting source on a literal that
includes incidental syntax** (`async`, decorators, argument spelling). Second, and far more
damaging, **no anchor check**: the harness assumed the split succeeded. A missing anchor therefore
surfaced as an opaque `IndexError`/`NameError` three frames deep, which reads as *"this test is
broken"* rather than *"this assertion no longer holds"* — so it gets triaged as noise, and every
assertion below it silently retires.

**The fix**, applied to all three:

1. **Parse, don't split.** `_func_src()` locates a function by walking the AST, so it is immune to
   `async`, decorators, whitespace and argument reflow. Same for `AiAssistIn`, which is now lifted
   out of the router by AST and exec'd against a minimal `LaxModel` stand-in — keeping the file's
   deliberate "no app-package import, no DB, no settings" property while the field names and
   defaults stay the product's own.
2. **A missing anchor is a named, recorded FAILURE, not a crash** — and crucially **not fatal**. It
   reports `HARNESS ANCHOR GONE: <file> no longer defines <name> — renamed, moved or deleted.
   Repoint this harness; do NOT delete the assertions it feeds`, then returns `""` so the dependent
   assertions go red rather than passing vacuously on an empty string, and **the remaining ~95
   assertions still run**. Verified by renaming `add_store_alias`: 103 of 106 still executed and the
   three affected went red with that sentence.
3. Every remaining crash-prone `seq[0]` in `harness_sweep_honesty.py` goes through `first(seq,
   what)`, which records a failure and returns a falsy stand-in. Verified: with a green-lie
   reintroduced into `_sweep_ingest_outcome`, the file used to die at check ~30 with an
   `IndexError`; it now runs to completion and reports **47 passed, 40 failed**.

### `harness_sweep_honesty.py` deserves its own paragraph

This is the harness whose entire purpose is to prove the sweep never reports a green lie. It was
reporting a green lie about itself, in two independent ways.

**One.** Its section 2 differential extracted the pre-change email ladder from **`origin/main`** — a
*moving* ref. The honest-zero fix merged, `origin/main` absorbed it, the old text ceased to exist,
`str.index` raised, and a bare `except` printed a parenthetical `(could not extract…)` and set
`_old_ladder = None`. The following `if _old_ladder:` then skipped **five assertions** while the run
still reported all-green. Repaired by anchoring to **`f66139f2^`** — the immutable commit
immediately before *"fix(commcalc): FTP/email sweep honest-zero + dedup parity"* — so the
differential is runnable forever, and by making a failed extraction a **loud recorded failure**
rather than a silent skip. It now genuinely runs and reproduces the exact old-vs-new divergences
(`mapped_zero`, `custom_empty`, `ignored_type`, `future_marker`: OLD `'ok'`/0 rows → NEW with a named
reason).

**Two.** Its `fetch_new_attachments` test double took two arguments; the sweep calls it with three
(`unrouted`, a genuine later feature — the list that surfaces "the email HAS the data but no import
rule matched"). Every email sweep therefore died *inside the harness's own driver* with "takes 2
positional arguments but 3 were given", recorded nothing, and section 3 then blew up on
`email_processed[0]`. An `IndexError` was standing in for a stale test double.

**Also found:** with the sweeps running again, the org-scope assertion flagged one unscoped read. It
is `_table_has_column()`'s schema probe — `select(<col>).limit(1)`, result discarded, returns a
bool, generic over tables of which some have no `org_id`. That is a schema question, not a
tenant-data read, so it is **not** a leak. I did not weaken the rule to accommodate it: the
exemption is defined by **shape** (exactly one column, limit 1), not by table name, so it cannot
widen into "this table may skip org scoping", and two further assertions pin that every
org-unscoped read is a bare probe and that the check saw real reads.

---

## Cross-cutting: literals frozen against a moving target

Three of the four families each produced at least one assertion of the same shape — a value captured
at review time that the world then moved past:

- `EXPECT_ROUTES = 1018` / `1054`, whole-app route totals (now 1,569; and measured off the broken
  `app.routes` walk);
- `SEED_VERSION == 9` (now 14);
- `BASE_REF = 6aadb14` blast-radius checks — *"this package touched no money file, added no
  migration, added no dependency"* — which today assert **"nothing in the entire product has changed
  since 6aadb14"**;
- `origin/main` as a differential counterparty.

These were all correct and valuable **while the changeset was under review**. Merged, they become
instruments outliving their measurement, and their steady red trains everyone to ignore the file.

I did not delete any of them. Each is now either re-expressed as the durable property it stood for
(`SEED_VERSION >= 9`; relative middleware order; "every `load_workbook` passes `read_only=True`"), or
kept as an **opt-in** that reports rather than fails, and skips **visibly and counted**:

```bash
EXPECT_ROUTES=1569 python3 harness_training_center.py           # pin the whole-app route surface
XSS_BASE_REF=$(git merge-base HEAD main) python3 harness_export_xss_upload.py   # live blast radius
SWEEP_BASE_REF=<ref> python3 harness_sweep_honesty.py           # differential vs another base
```

**The general rule this suggests:** a proof harness should assert *invariants of the product*, not
*facts about one changeset*. Where a changeset-scoped check is genuinely wanted, it must name the ref
it measures against and fail loudly when it cannot — never skip in silence.

---

## Runnability and self-tests

All 11 run identically from `backend/` and from the repo root. Several were reading source with
cwd-relative paths and died with `FileNotFoundError` from the root — again "not run" rather than
"failed". Every source read is now anchored to the file's own directory via `_HERE`, per commit
`564c171f`. One of these was worse than a crash: `harness_attention_clears.py` F5 resolves deep links
against the frontend tree with a relative `FRONT` path, so from the repo root it silently found
**zero** pages and reported six broken links that are all fine.

Every file was self-tested by breaking something it asserts, confirming the failure, and restoring —
the product file, not the assertion, in all 11 cases. Two of these are worth noting because they
caught what the old versions could not: breaking the market backfill in `app/core/scope.py` fired
five assertions in `harness_core_grant_universe.py` **including the new non-vacuity guard 12b-iv**,
and reintroducing a blocking call into `ai_assist` fired `D2`/`D11` (`ticks=0` — the event loop had
stopped serving), which is the SEV-1 proof itself.

Regressions green after the `get_flags` change: `flag_review_persistence` 45, `org_scope_guard` 25,
`cross_tenant_isolation` 23. `notify_failure_leads` reports 87/2, and both failures pre-date this
work (confirmed by stashing the product change and re-running); they belong to another family.
