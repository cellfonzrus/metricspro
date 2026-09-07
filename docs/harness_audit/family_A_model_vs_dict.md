# Harness repair — family A: "dict passed where a pydantic model is expected"

Date: 2026-09-06. 18 proof harnesses, all failing the same way on `main`.

## The root cause

Each of these harnesses calls an endpoint function directly, passing a plain `dict` as the request
body. Those endpoints were migrated from `body: dict` to a declared pydantic request model (the
"item 15" typed-body rollout), so the handler reads `body.<field>` — and sometimes
`body.model_fields_set`. A dict raises `AttributeError` on the first field access, which means the
handler died **before reaching the logic the harness was written to prove**.

The damage was worse than a red harness. Every assertion after the first endpoint call never ran, so
these files had stopped proving anything at all — including two that guard security behaviour.
`harness_impersonation.py` died at its very first `start()` probe, leaving sections F through K (the
whole default-deny impersonation chain, the reauth gate, the ASGI middleware smoke) unexecuted.

The repair follows `harness_ssrf_import_gate.py` (commit `564c171f`): build the handler's declared
request model instead of a dict, via a local

```python
def _body(model, d):
    return model.model_validate(d)
```

`model_validate` reproduces FastAPI's own call shape, including which fields count as explicitly set
— which several handlers branch on.

## Result

All 18 pass from both `backend/` and the repo root. 1,186 assertions now execute where previously
every one of these files aborted mid-run.

## Real product defect found

**`POST /crm/leads/dedupe-check` returned a 500 whenever a duplicate actually existed.**
`backend/app/modules/crm/router.py` passed its pydantic body straight into
`pipeline_core.is_duplicate(lead, ...)`, which calls `lead.get("phone")`. `pipeline_core` is
deliberately FastAPI-free and takes plain dicts, so the call raised
`AttributeError: 'DedupeCheckIn' object has no attribute 'get'`.

It failed on exactly the case the endpoint exists to report. With no candidate rows the loop never
runs, so an empty result stayed healthy and the live duplicate warning 500'd only when a real
duplicate was found — the failure hid in the common path.

Introduced by the same `body: dict` → typed-model migration. That change explicitly deferred the
other handlers that hand a raw body to a helper; this one was missed. Fixed in place
(`body.model_dump()`), one line, with the reason recorded at the call site. Covered by
`harness_crm_pipeline.py` check K5.

No migration was needed. No other call site of this shape exists in `crm/router.py`.

## Per-file findings

| Harness | Verdict | What was wrong beyond the dict, and what changed |
|---|---|---|
| `harness_asset_b2b_inventory_value.py` | stale | Also wrapped every call in `asyncio.run`; `upload_b2b_inventory` is no longer `async def`. `_run` now awaits a coroutine and passes a plain result through. |
| `harness_closer_chargebacks.py` | stale | Dict only (body is the 2nd positional arg). |
| `harness_crm_pipeline.py` | **REAL DEFECT** | See above — `dedupe-check` 500. Product fixed. |
| `harness_deposit_recon.py` | stale | Dict only (`bank_deposit`, `update_bank_deposit_meta`). |
| `harness_eep_retail_ops.py` | stale | Also a stale test double: the product now sends `headers=_sib_headers(...)` on the internal P&L system-line push, and the fake `requests.post` had a fixed signature. The resulting `TypeError` was swallowed by the push's own `try/except` into `{"pushed": False}`, so the P&L assertions failed while the product was fine. Fake now takes `**kw` and records it. |
| `harness_envelope_photo_required_gate.py` | stale | Dict only. |
| `harness_failure_triage.py` | stale | Dict only (3 handlers). |
| `harness_fix_pipeline.py` | stale | Also two FastAPI-upgrade failures and a cwd dependency. FastAPI no longer flattens `include_router` into `app.routes`; it stores an `_IncludedRouter` node that matches every scope and carries no `.path`/`.name`. F2a compared a 4KB router repr against a handler name and F2b counted zero pipeline routes. Replaced with a recursive descent over included routers; all 8 endpoints resolve to their own handlers and exactly 8 are registered. Source reads anchored to the file's own directory (it previously died with `FileNotFoundError` from the repo root). |
| `harness_hr_letters.py` | stale | Dict only (5 handlers; `approve_letter` branches on `model_fields_set`). |
| `harness_impersonation.py` | stale — **no product defect** | The security chain is intact and now actually exercised: 134/134 including default-deny, cross-tenant refusal, super-admin-untouchable, no-escalation-chain, fail-closed audit, and nesting refusal. Separately, the same FastAPI `_IncludedRouter` change broke the route-surface check — and note it would have failed in the dangerous direction, silently reporting the impersonation routes as MISSING. Fixed with the same recursive descent. |
| `harness_mpc_confirm_creates.py` | stale | Dict only. |
| `harness_payroll_lunch_adjustment.py` | stale | Also assumed the approvals board hands back `pay_effective`. The board strips every pay figure when the caller may not see pay scales (owner 2026-08-11, mig 434) and fails closed on an unresolvable caller, which the fake `"Bearer t"` is. The caller is now resolved as one who may see pay — and the gate is not merely switched off: new check B11 asserts the pay columns are still stripped when it denies. |
| `harness_payroll_row_merge.py` | stale (3 distinct causes) | See the section below. |
| `harness_referral.py` | stale | Also time-dependent: every fixture row is stamped at a fixed `NOW` (2026-08-13) but the redeem-deadline checks compared against the real wall clock, so the QR-redeem assertions passed only while the harness was younger than the 48h redeem window and have been failing on the calendar ever since. `rr._now` is now pinned to `NOW`. |
| `harness_store_mapping_dedupe.py` | stale | Dict only. |
| `harness_storeops_market_dropdown.py` | stale | Dict only. `create_store`/`update_store` in the storeops router still take dicts and were correctly left alone. |
| `harness_tech_support.py` | stale | Dict only (4 handlers). |
| `harness_tender_config_validation.py` | stale | Dict only. |

## `harness_payroll_row_merge.py` in detail

Three separate stale causes surfaced once the dict problem was cleared, plus one thing worth the
owner's attention.

**It was making a live database call.** `_require_manager` resolves the caller through
`tenant_middleware.caller_app_user`, which builds its *own* client from `app.core.database` rather
than the router's. Left unstubbed, the harness issued a real PostgREST request to whatever
`SUPABASE_URL` the environment carried — it failed with `invalid input syntax for type uuid:
"uid-mgr"`, i.e. the fake uid reached a real database. That accessor now points at the same fake, so
the manager gate still runs for real against the seeded `app_users` row.

**Force-clockout (E4a/E4b, and E5a/E5b as knock-ons).** The fixture clocked in at "now", which is
*after* the shift's scheduled end. A punch starting past its own scheduled end is a SECOND session
(a re-clock-in after an earlier auto-close) and `_do_force_clockout` deliberately leaves it open,
because stamping it would put the clock-out before the clock-in. So the sweep correctly closed
nothing. The fixture now clocks in before the scheduled end — a genuine overdue first session, which
is what E4 means to test — and a new check **E4c** pins the second-session rule the old fixture was
tripping over by accident.

**Stale-punch auto-stamp (H3a).** Hard-coded `hours <= 8.0`, the value from before the owner
directive of 2026-08-14 that changed the stamp from the bare scheduled end to scheduled end + grace.
Restated against `FORCE_CLOCKOUT_GRACE_MIN` rather than a fresh literal, keeping the property that
matters: the punch is stamped off the schedule, never the raw now-minus-clock-in diff.

**The double-count checks (I24/I25/I25b) — money, so stated plainly.** These expected 40h and $880
after the mig-415 backfill. The measured result is 42.5h and $935. This is a deliberate, documented
product change, not a regression: under the punch-driven model (`_shift_actual_contribution` /
`_punch_counts_for_pay`) a closed punch REPLACES its day's schedule, so the shift contributes 0 and
the real punched hours are what count. The checks were written under the older "a scheduled shift
covers the day" rule.

The double count itself is gone either way, which is what the section exists to prove: 82.5h (40
scheduled + 42.5 punched, summed) drops to 42.5h. Worth noting for the owner: the store *amount* was
$935 both before and after the backfill — it was always priced off the punch — so what the backfill
corrects is the inflated HOURS, not the dollars. The checks are now expressed as properties against
the punch total rather than fresh literals, so the next fixture change cannot silently rot them.

## Self-testing

Every one of the 18 was verified by deliberately breaking something it asserts in the **product**
(not the harness), confirming the harness went red, then restoring. Examples: removing the tender
off-axis validation (8/8 → 4/8), forcing `require_photo_if_cash` false (3 failures), zeroing the
no-double-count rule (payroll row merge back to 82.5h/$1815), removing the super-admin-only gate in
the fix pipeline, removing "a super-admin can never be impersonated" (F8 fails), breaking the
duplicate-send race guard in HR letters (3 failures), and making an internal note visible to the
tenant in tech support.

Each was also confirmed to run from both `backend/` and the repo root.

## One caution for the other agents

Self-tests were done by editing a product file, running, and restoring it from a copy taken seconds
earlier. Three agents share this worktree. If a sibling had an uncommitted edit in one of these files
inside that window it would have been reverted: `storeops/router.py`, `closing/router.py`,
`asset/router.py`, `crm/router.py`, `helpdesk/router.py`, `hr/letters.py`, `referral/router.py`,
`core/impersonation_api.py`, `core/fix_pipeline.py`, `storeops/payroll_approval.py`,
`commcalc/router.py`. The commcalc `get_flags` async→sync fix was checked and is intact; the others
are at HEAD. Worth a glance if a sibling's change seems to have vanished.
