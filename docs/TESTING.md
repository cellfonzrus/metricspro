# Testing and test-driven development — the house standard

**Owner directive, 2026-10-28:** *"We need to define all development we do to follow the following /
Unit testing / Integration testing / Test driven development."*

From that date every change to this repo ships with tests, and the tests run in CI. This page is the
whole rule and the whole how-to. It is written for someone who has not done this before.

---

## 1. The rule, in four lines

1. **Write the failing test first.** Before the fix or the feature, write the test that proves it is
   missing. Run it. Watch it fail.
2. **Write the smallest code that makes it pass.** Run it again. Watch it pass.
3. **Every change ships both kinds**: a *unit* test for the logic, an *integration* test for how it
   behaves through the API.
4. **A PR is not done until those tests run in CI and pass.** CI is `.github/workflows/tests.yml`.

### Why the test comes first

It is tempting to write the code, then write a test that agrees with it. That test can only ever
pass — you wrote it by reading the answer. A test written *first* has to fail for the right reason,
and that failure is the only evidence you ever get that the test is actually connected to the thing
it claims to prove. Most useless test suites in the world are useless for exactly this reason.

This is not theory here. While building this very framework, a test asserted that a stray database
call from a test would be stopped by the socket guard. It failed — and it failed because the
credential blanking stopped it *earlier*. The test was wrong and the system was safer than it
claimed. Writing the test first is what surfaced that; writing it afterwards would have quietly
recorded the wrong reason.

---

## 2. The two kinds of test

| | **Unit** | **Integration** |
|---|---|---|
| Lives in | `backend/tests/unit/`, `frontend/src/**/*.test.ts` | `backend/tests/integration/` |
| Proves | one function's logic | the real app: routing, middleware, request → response |
| Touches | nothing — no app, no I/O | the real `app.main:app` over an **in-memory database double** |
| Speed | milliseconds | a second or two |
| Marker | `unit` (applied automatically by directory) | `integration` (same) |

**"Integration" here does not mean a real database.** There is no staging Supabase in this project —
every environment points at the same live one. So an integration test runs the genuine FastAPI app
against rows the test itself seeded. That is the honest limit of what can be tested offline, and it
is stated here so nobody mistakes a green suite for a verified production query.

> **Checking something against live data is a separate activity.** It is done by *reading*
> production through the Supabase REST API, never by a test, and never by writing. See the project
> memory note `read-production-via-supabase-rest.md`.

---

## 3. Nothing can reach production from a test

This is enforced, not requested. Two independent barriers, both in `backend/tests/conftest.py`:

1. **Credentials are blanked** before anything imports the app, so a client cannot authenticate.
2. **Outbound sockets are blocked** for the whole session. Loopback stays open (the test client needs
   it). Anything else raises `OutboundNetworkBlocked` with a message pointing back here.

The frontend has the mirror of this in `frontend/tests/setup.ts`: `fetch` throws unless a test stubs
it deliberately.

If you see `OutboundNetworkBlocked`, that is not a flake and not a missing fixture. It means the code
under test wants real production data. Seed the double instead, or make the unit smaller.

`backend/harness_test_discipline.py` proves all of this by **running** the guard against a real
production hostname on every CI build, rather than reading the source and hoping.

---

## 4. Running the tests

```bash
# Backend — from the repo root
cd backend
pip install -r requirements-dev.txt     # once
python -m pytest tests                  # everything
python -m pytest tests -m unit          # just the fast ones
python -m pytest tests -m integration   # just the app-level ones
python -m pytest tests/unit/test_supabase_double.py -k delete -vv   # one test, verbose

# Frontend — from the repo root
cd frontend
npm install                             # once
npm run test                            # everything, once
npm run test:watch                      # re-runs as you type — the TDD loop
npm run test:proofs                     # the standalone tools/*-proof.mjs programs
```

---

## 5. Writing your first backend test, step by step

Say you are fixing a bug: a store code is compared without case-folding, so `b-1115` and `B-1115`
become two different stores (this is a real open defect — project memory,
`store-code-case-phantom-store.md`).

**Step 1 — write the test that fails.** Create `backend/tests/unit/test_store_code.py`:

```python
from app.modules.account.store_identity import squash_key


def test_two_spellings_of_one_store_code_share_a_key():
    assert squash_key("b-1115") == squash_key("B-1115")
```

**Step 2 — run it and watch it fail.**

```bash
cd backend && python -m pytest tests/unit/test_store_code.py
```

You want to see a real failure — an `AssertionError`, or an `ImportError` if the function does not
exist yet. A test that passes on its very first run, before you have written anything, is testing
nothing. (This particular one happens to pass, because `squash_key` already upper-cases. The real
defect is one layer up, in a caller that compares raw codes instead of dereferencing this key — and
finding that out by running the test is exactly the point of running it first.)

**Step 3 — write the smallest fix** in the real module, and run it again until it is green.

**Step 4 — add the integration test** in `backend/tests/integration/`, which proves it through the
API the way a user meets it. Replace the route and payload with the real ones for your change:

```python
def test_the_store_report_merges_the_two_spellings(api_client, fake_supabase, monkeypatch):
    fake_supabase.seed("commcalc.store_mapping", [
        {"org_id": ORG, "store_code": "b-1115", "revenue": 100},
        {"org_id": ORG, "store_code": "B-1115", "revenue": 200},
    ])
    # Point the module under test at the double instead of the real client.
    monkeypatch.setattr("app.modules.account.router.get_supabase", lambda: fake_supabase)

    rows = api_client.get("/api/v1/account/stores", params={"org_id": ORG}).json()

    assert len(rows) == 1 and rows[0]["revenue"] == 300
```

**Step 5 — commit both tests with the fix.** Not after. The test and the change are one unit of work.

### The fixtures you get for free

| Fixture | What it is |
|---|---|
| `api_client` | the real FastAPI app under Starlette's `TestClient` — real routes, real middleware |
| `fake_supabase` | the in-memory database double; `.seed("schema.table", rows)` to fill it, `.rows("schema.table")` to assert on what a route *wrote* |
| `monkeypatch` | pytest's own — use it to point a module's `get_supabase` at the double |

The double lives in `backend/tests/fakes/supabase.py` and is itself unit-tested
(`tests/unit/test_supabase_double.py`), because every integration test's honesty rests on it. **Do
not write a second stub in your own test file.** Two doubles answering the same question will drift,
and the day they disagree the suite lies in the direction nobody is looking. If the double is missing
a verb you need, add it there — with a test.

One thing the double refuses on purpose: an `update()` or `delete()` with **no filter**. A rebuild
that lost its filter once emptied 11,000 live payable rows (index §PAYABLES-01). The double will not
certify that shape as working.

---

## 6. Writing your first frontend test

Tests sit next to the code, as `*.test.ts` (logic) or `*.test.tsx` (components).

```ts
// src/lib/__tests__/my-thing.test.ts
import { describe, it, expect } from 'vitest'
import { myThing } from '../my-thing'

describe('myThing', () => {
  it('does the thing', () => {
    expect(myThing('in')).toBe('out')
  })
})
```

For a component, render it and assert on the DOM:

```tsx
import { render, screen } from '@testing-library/react'
import MyCard from '../MyCard'

it('shows the title', () => {
  render(<MyCard title="Hello" />)
  expect(screen.getByText('Hello')).toBeTruthy()
})
```

Imports are explicit (`globals: false`) so the editor and `tsc --noEmit` both understand them.

Run `npm run test:watch` while you work: it re-runs the affected file on every save, which is what
makes the write-fail-pass loop fast enough to actually keep doing.

---

## 7. Harnesses and tests — how they fit together

This repo already has **597 `backend/harness_*.py` programs** and 12 `frontend/tools/*-proof.mjs`
programs. They are not going away and they are not replaced.

| | **Harnesses / proofs** | **The test suites** |
|---|---|---|
| Shape | a standalone program, run directly | files collected by pytest / vitest |
| Job | prove a *design rule* holds and **fail the build if it comes undone** — one home, no second copy, no per-tenant branch | prove *this function* and *this endpoint* behave |
| Written | once per design fix, per the CLAUDE.md design-fix rule | continuously, with every change |

Both are required and they answer different questions. A harness asks *"is the architecture still
the architecture?"*; a test asks *"does this code do what it says?"* A money-touching change still
needs its DB-free proof harness — that rule is unchanged.

They stay in separate lanes deliberately: `pytest.ini` does not collect `harness_*.py`, so the
inner-loop test run stays fast and the heavy proofs keep their own CI jobs.

**Two shrink-only registers guard the debt**, both in the house's established shape:

- `backend/harness_unrun_pending.txt` — harnesses no workflow runs (330 today). A new harness must be
  run by a workflow; the list may only get shorter.
- `frontend/tools/proof_red_pending.txt` — frontend proofs that are currently red (4 of 12). A proof
  not on the list must pass; one on it that starts passing fails the build until its line is deleted.

---

## 8. What CI runs, and what blocks a merge

`.github/workflows/tests.yml`, five jobs, all **enforcing** — a red job blocks the merge:

| Job | Command |
|---|---|
| Backend unit tests | `python -m pytest tests -m unit` |
| Backend integration tests | `python -m pytest tests -m integration` |
| Frontend unit and component tests | `npm run test` |
| Frontend proofs | `npm run test:proofs` |
| The test framework stays wired (lock) | `python harness_test_discipline.py` |

The last one is the lock on everything above it. It fails the build if `pytest.ini` stops pointing at
the suite, if the network guard is removed or stops working, if a credential quietly leaves the
blanking list, if either suite empties out, if a second database double appears, or if a CI job
installs a runner and never calls it. Without that lock the rest of this page is a wish; with it the
framework cannot come undone quietly.

---

## 9. Two tests that are worth more than the rest

**The regression test.** When a defect is reported, the first test you write reproduces *the reported
defect*, with the real numbers from the report. That test is the proof the fix works, and it is the
alarm if it ever comes back.

**The sibling test.** The CLAUDE.md rule *"a fix is a DESIGN fix or it is not a fix"* applies to
tests directly: if a bug was found in one feed, one tenant or one report, write the test for the
*other* paths that answer the same question too. One path fixed and its sibling not is the same
defect wearing a hat — and an untested sibling is how it gets back in.

---

## 10. Common problems

| What you see | What it means |
|---|---|
| `OutboundNetworkBlocked` | the code wants production. Seed `fake_supabase`, or test a smaller unit. |
| `'<verb>' not implemented` from the double | add that verb to `tests/fakes/supabase.py`, with a test for it. |
| `single() expected exactly 1 row` | the same as postgrest's behaviour — your seeded data matched 0 or 2 rows. |
| A test passes the very first time you run it | it is probably not connected to anything. Break the code on purpose and confirm it goes red. |
| `npm run test` cannot find a module | check the `@/` alias — it resolves to `frontend/src` in `vitest.config.ts`. |
| CI red on `test-discipline` | read the FAIL lines: each one names the piece of the framework that came undone. |
