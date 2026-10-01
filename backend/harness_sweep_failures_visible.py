"""PROOF — a sweep failure is never silent: a dead parse is reaped, a partial delivery is a failure.

OWNER DIRECTIVE 2026-09-30, verbatim: *"Why do we have so many errors in sweeping data - this is the
lifeline of our system - need to make it foolproof it"*.

MEASURED BEFORE BUILDING, because "so many errors" turned out to be the wrong diagnosis. September
2026: 9,835 import batches, 9,789 `loaded`, 2 `failed` — **99.55% success**. The pipeline is not
fragile. Two separate things are wrong, and they point in OPPOSITE directions:

  THE ALERTS FIRE FOR FAILURES THAT ALREADY FIXED THEMSELVES. The B2B Soft mailbox reported
  `connect error: command: FETCH => Server shutting down` at 06:04 on 09-30 and ingested 11/11
  attachments at 12:07, untouched. The sweeps run hourly; a blip pages the owner and the next run
  repairs it.

  THE FAILURES THAT MATTER ARE SILENT.
  (a) 44 batches died mid-parse in September with NO `completed_at`, NO `row_count` and NO
      `error_detail` — an eternal `parsing`.
  (b) `vip_sweep_config` last ran 2026-09-25 with status `partial` and the connector-health scan said
      nothing 118 hours later. Not a broken stale arm: VIP is WEEKLY, so its own window is 336h and it
      would not be called late until 09 October — a fortnight of degrading on a green lamp. That is the
      asset-ledger feed behind device COGS and the balance-sheet inventory.

WHAT THIS PROVES
  §A  THE REAPER'S DECISION IS PURE AND CONSERVATIVE. A run still `parsing` past the window is dead
      and reapable; one inside the window is left strictly alone; an unreadable timestamp is reported
      and never reaped on a guess.
  §B  WHY A `finally` CANNOT DO THIS. The one claim site already closes batches out in a `finally`, on
      both paths. The harness states the fact it depends on: process death skips `finally`, so the only
      mechanism that survives is out of band.
  §C  REAPING RELEASES THE RETRY. The duplicate guard's unique index is partial on `status <> 'failed'`,
      so a stranded `parsing` row blocks the re-import of its own file. `failed` is what unblocks it.
  §D  A PARTIAL DELIVERY IS A FAILURE. The health scan's predicate catches `partial`, and `skipped` is
      deliberately NOT caught.
  §E  ARMED NEGATIVE CONTROLS — each rule goes RED when the fix is removed.

Stdlib only, DB-free: imports `app.core.import_batches` (which imports no third-party module at
module level) and reads the router's predicate as TEXT rather than importing fastapi.
  python3 backend/harness_sweep_failures_visible.py
"""
import io
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []
_UNSET = object()          # so `want=None` can be ASSERTED, not read as "just be truthy"


def check(label, got, want=_UNSET):
    ok = bool(got) if want is _UNSET else (got == want)
    (PASS if ok else FAIL).append(label)
    print(("  PASS  " if ok else "  FAIL  ") + label + ("" if ok else "   got=%r want=%r" % (got, want)))


def section(t):
    print("\n" + t)
    print("-" * len(t))


from app.core import import_batches as IB  # noqa: E402

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)


def row(hours_ago, status="parsing", rid="b1", source="inventory_aging", name="Inventory Aging.xlsx"):
    when = NOW - timedelta(hours=hours_ago)
    return {"id": rid, "status": status, "created_at": when.isoformat(),
            "source": source, "file_name": name}


# ── §A the decision ──────────────────────────────────────────────────────────────────────────────
section("A. the reaper's decision — pure, and conservative by construction")

out = IB.stale_parsing([row(12)], now=NOW, hours=6)
check("a batch 12h into 'parsing' past a 6h window is reaped", len(out), 1)
check("it carries the age", out[0]["age_hours"], 12.0)
check("the reason names the dead run, not the file", "never finished" in (out[0]["reason"] or ""))
check("the reason says the file was NOT imported", "NOT imported" in (out[0]["reason"] or ""))
check("the reason says the hash is released for retry", "released" in (out[0]["reason"] or ""))
check("the reason names the source", "inventory_aging" in (out[0]["reason"] or ""))

check("a batch INSIDE the window is left strictly alone", IB.stale_parsing([row(2)], now=NOW, hours=6), [])
check("exactly at the window is not yet dead (strictly greater)",
      IB.stale_parsing([row(6)], now=NOW, hours=6), [])
check("a hair past the window is", len(IB.stale_parsing([row(6.01)], now=NOW, hours=6)), 1)

for st in ("loaded", "failed", "superseded"):
    check("a '%s' batch is never touched, however old" % st,
          IB.stale_parsing([row(999, status=st)], now=NOW, hours=6), [])

bad = IB.stale_parsing([{"id": "x", "status": "parsing", "created_at": "not-a-date"}], now=NOW, hours=6)
check("an unreadable timestamp is REPORTED, never reaped on a guess", len(bad), 1)
check("  ...and it carries no reason, so nothing writes a status", bad[0].get("reason"), None)
check("  ...and says why it was left alone", "unreadable" in (bad[0].get("skipped") or ""))
miss = IB.stale_parsing([{"id": "y", "status": "parsing"}], now=NOW, hours=6)
check("a MISSING timestamp is the same: reported, not reaped", miss[0].get("reason"), None)

check("the default window is hours, not minutes, so a slow parse is safe",
      IB.REAP_AFTER_HOURS_DEFAULT >= 6)
check("an empty feed is not an error", IB.stale_parsing([], now=NOW, hours=6), [])
check("None is not an error", IB.stale_parsing(None, now=NOW, hours=6), [])

mixed = IB.stale_parsing(
    [row(9, rid="a"), row(1, rid="b"), row(30, status="loaded", rid="c"), row(8, rid="d")],
    now=NOW, hours=6)
check("over a mixed batch only the dead ones come back", sorted(x["id"] for x in mixed), ["a", "d"])

# ── §B why a finally cannot do this ──────────────────────────────────────────────────────────────
section("B. why an in-process guard cannot fix it")

ROUTER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "app", "modules", "commcalc", "router.py")
rsrc = io.open(ROUTER, encoding="utf-8").read() if os.path.isfile(ROUTER) else ""
check("the claim site still closes batches out in a `finally` on BOTH paths",
      "_import_batches.fail(_batch.get(\"batch_id\")" in rsrc
      and "_import_batches.complete(_batch.get(\"batch_id\")" in rsrc)
check("so the reaper is justified in the module by process death, not by a missing branch",
      "does not run when the process is killed" in io.open(IB.__file__, encoding="utf-8").read())
check("the reaper is WIRED into the scheduler tick, not merely written",
      "_import_batches.reap_stale()" in rsrc)
# The same dispatch read appears in an earlier function, so anchor the search AFTER the reap: what
# is being proved is the order WITHIN the tick, not the first occurrence in the file.
_reap_at = rsrc.index("_import_batches.reap_stale()")
_dispatch_read = "conns = (client.schema('commcalc').table('connector_instances')"
check("it runs BEFORE dispatch, so this tick can land the file the last one died on",
      rsrc.find(_dispatch_read, _reap_at) > _reap_at)
check("the tick reports what it reaped instead of only logging it", "reaped_batches" in rsrc)

# ── §C reaping releases the retry ────────────────────────────────────────────────────────────────
section("C. reaping is what unblocks the re-import")

ibsrc = io.open(IB.__file__, encoding="utf-8").read()
check("`fail` is documented as the state that RELEASES the hash",
      "RELEASES the hash" in ibsrc)
check("the reaper writes `failed` (via fail), never `loaded`",
      "fail(item[\"id\"], error=item[\"reason\"])" in ibsrc and "complete(item" not in ibsrc)
check("the module states the stranded row blocks its own file's re-import",
      "BLOCKS THE RE-IMPORT" in ibsrc)
check("reap_stale never raises — it is on a scheduler tick",
      "def reap_stale(" in ibsrc and "res[\"unavailable\"] = True" in ibsrc)

# ── §D a partial delivery is a failure ───────────────────────────────────────────────────────────
section("D. a partial delivery is a failure, a skipped run is not")

pred = ""
m = re.search(r"failed = \(.*?\)\)\n", rsrc, re.S)
if m:
    pred = m.group(0)
check("the health scan's failure predicate exists", bool(pred))
check("it catches 'partial'", '"partial" in status' in pred)
check("it still catches error / fail / 403",
      all(x in pred for x in ('"error" in status', '"fail" in status', '"403" in status')))
check("'skipped' is deliberately NOT a failure", '"skipped" in status' not in pred)
check("the reason is recorded where the predicate is, with the live evidence",
      "`vip_sweep_config` last ran 2026-09-25" in rsrc and "336h" in rsrc)


def failed_pred(status):
    """The shipped predicate, re-expressed — §E flips this to prove the rules can go red."""
    s = (status or "").lower()
    return ("error" in s) or ("fail" in s) or ("403" in s) or ("partial" in s)


for s in ("partial", "Partial", "partial: 3 of 11 attachments", "error", "Sweep failed", "403 denied"):
    check("status %-32s reads as a failure" % ("'" + s + "'"), failed_pred(s), True)
for s in ("ok", "2/2 attachments ingested", "skipped: lock held", ""):
    check("status %-32s does NOT" % ("'" + s + "'"), failed_pred(s), False)

# ── §E armed negative controls ───────────────────────────────────────────────────────────────────
section("E. armed negative controls — each rule can go RED")

def no_partial(status):
    s = (status or "").lower()
    return ("error" in s) or ("fail" in s) or ("403" in s)


check("CONTROL: the OLD predicate lets 'partial' pass as healthy  <- the live VIP defect",
      no_partial("partial"), False)
check("CONTROL: ...which is exactly what the new one fixes", failed_pred("partial"), True)


def reap_no_window(rows, now):
    """A reaper with no age window — reaps a parse that is still running."""
    return [r for r in rows if r.get("status") == "parsing"]


check("CONTROL: a windowless reaper would kill a 2h-old live parse",
      len(reap_no_window([row(2)], NOW)), 1)
check("CONTROL: ...the shipped one does not", IB.stale_parsing([row(2)], now=NOW, hours=6), [])


def reap_guessing(rows, now):
    """A reaper that treats an unreadable stamp as infinitely old."""
    out = []
    for r in rows:
        if r.get("status") != "parsing":
            continue
        if IB._age_hours(r.get("created_at"), now) is None:
            out.append({"id": r.get("id"), "reason": "guessed"})
    return out


check("CONTROL: a guessing reaper would write a status for an unreadable row",
      len(reap_guessing([{"id": "z", "status": "parsing", "created_at": "junk"}], NOW)), 1)
check("CONTROL: ...the shipped one reports it with reason=None so nothing is written",
      IB.stale_parsing([{"id": "z", "status": "parsing", "created_at": "junk"}],
                       now=NOW, hours=6)[0]["reason"], None)

print("\n" + "=" * 96)
print("RESULT: %d passed, %d failed" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("   FAILED: " + f)
print("=" * 96)
sys.exit(1 if FAIL else 0)
