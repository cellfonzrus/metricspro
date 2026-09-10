"""PROOF: work the DM entered on Cash Pickup is never destroyed and never silently discarded.

REPORTED BY RAJIV via the owner, 2026-09-10, verbatim: *"Cash pickup / Check mark and input amount
entered / Doesn't save / We have to fix it today it is urgent"*.

MEASURED IN PRODUCTION BEFORE ANY CODE WAS CHANGED — the numbers that made the diagnosis:
  · commcalc.cash_pickup    217 rows, ALL picked_up, **0** with actual_picked_amount, **0** opened
  · commcalc.billpay_pickup  56 rows, ALL picked_up, **0** with actual_picked_amount, **0** opened
  · `note` — which rides the SAME items[] payload — DID persist (9 + 19 rows), so requests were
    reaching the server and writing; only these two fields never arrived.
  · confirmations exist for two people only; the reporter had never had one save at all.
So the count has never once been recorded, for anyone, since the column shipped.

TWO DEFECTS, ONE SYMPTOM. Both destroy entered work, and neither is visible to `tsc` or a build:
every value is a legal string and the page renders perfectly.

  1. RELOAD UNTICKED THE ROW. `load()` ran `setSel({})` on EVERY refetch. The page refetches far
     more often than it looks — the store roster lands asynchronously, the scope auto-applies, any
     filter moves — so a DM who ticked a row and then typed its count was silently unticked while
     the typed number stayed on screen. Confirm then submitted nothing, or refused with "Select at
     least one envelope" over a screen full of entered amounts.
     It was defended as safety ("a stale checkbox could confirm an envelope no longer on screen"),
     but the safety never rested on it: `selectedKeys` is `ready.filter(...)`, an INTERSECTION with
     the rows currently loaded, so a selection for a vanished or already-picked-up envelope is
     dropped on its own. The clear bought nothing and cost the DM their work.

  2. A COUNT ON AN UNTICKED ROW WAS DROPPED ON THE FLOOR. The page accepts a count (or an "opened"
     tick) on any row, but `confirm()` maps over SELECTED rows only — so that work vanished with no
     message. "I entered the amount and it didn't save" is a literal description of it.

WHAT THIS PINS
  A. `load()` no longer clears the selection, and the intersection that makes that safe still exists;
  B. entered work on an unticked row is DETECTED (`strandedEdits`), NAMED, and BLOCKS the confirm —
     never silently dropped, and never auto-ticked (ticking a row is the DM saying they physically
     took that envelope; no amount of typing may say it for them);
  C. the warning is on screen beside the button, not only after Confirm is pressed;
  D. the pre-existing opened-needs-a-count gate still stands, client and server;
  E. the typed counts/notes still survive a reload (the 2026-09-08 fix is not regressed);
  F. the server still writes the count for every realistic input — the field the client sends is
     stored, so a fix on the page reaches the database.

PURE / DB-FREE: parses the real .tsx as text and calls the real pure server module. stdlib only.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PAGE = os.path.join(HERE, '..', 'frontend', 'src', 'app', '(platform)',
                    'closing', 'pickup', 'page.tsx')

failures, checks = [], 0


def check(label, cond):
    global checks
    checks += 1
    if not cond:
        failures.append(label)


with open(PAGE, encoding='utf-8') as fh:
    raw = fh.read()


def strip_comments(js):
    """Thin alias over harnesslib.js_code_only — THE shared stripper (2026-09-10).

    The reasoning that put it here is unchanged and now lives in that module: this harness's first
    draft grepped the raw file for `setSel({})` / `setActuals({})` and failed on the COMMENT
    explaining why those calls were removed. It was the first of three harnesses to hit that, which
    is why the six lines moved somewhere all of them can share."""
    from harnesslib import js_code_only
    return js_code_only(js)


src = strip_comments(raw)

load_body = src.split('const load = useCallback(', 1)[1].split('}, [rangeMode', 1)[0]

# ── A. the reload no longer unticks the DM's rows ──────────────────────────────────────────────
check('A1 load() no longer clears the selection', 'setSel({})' not in load_body)
check('A2 ... and nothing else clears it either', src.count('setSel({})') == 0)
# the intersection that makes keeping the selection safe must still be there
check('A3 selectedKeys is still an INTERSECTION with the rows currently loaded',
      re.search(r'selectedKeys\s*=\s*ready\.filter\(', src) is not None)
check('A4 ready is still only envelopes not already picked up',
      re.search(r'ready\s*=\s*envelopes\.filter\(\s*e\s*=>\s*!e\.picked_up\s*\)', src) is not None)

# ── E. the 2026-09-08 fix is not regressed ─────────────────────────────────────────────────────
check('E1 load() does not wipe the typed counts', 'setActuals({})' not in load_body)
check('E2 load() does not wipe the typed notes', 'setNotes({})' not in load_body)
check('E3 load() does not wipe the opened ticks', 'setOpened({})' not in load_body)

# ── B. entered work on an unticked row is detected, named and blocking ─────────────────────────
check('B1 stranded entered work is computed', 'strandedEdits' in src)
stranded = src.split('const strandedEdits', 1)[1][:400]
check('B2 ... over rows that are NOT selected', '!sel_[k]' in stranded)
check('B3 ... counting a typed amount', 'actuals[k]' in stranded)
check('B4 ... and an opened tick', 'opened[k]' in stranded)

confirm_body = src.split('async function confirm()', 1)[1].split('async function saveCfg', 1)[0]
check('B5 confirm() refuses while entered work would be discarded',
      'strandedEdits.length' in confirm_body and 'return' in confirm_body)
guard_pos = confirm_body.index('strandedEdits.length')
send_pos = confirm_body.index("api('/api/v1/closing/pickup'")
check('B6 ... and refuses BEFORE the request is sent', guard_pos < send_pos)
check('B7 the message NAMES the envelopes rather than just counting them',
      'strandedEdits.map(' in confirm_body)
# never auto-select: that would confirm an envelope the DM never ticked
check('B8 stranded rows are never auto-ticked into the selection',
      not re.search(r'strandedEdits[^\n]*setSel', src))
check('B9 the Confirm button is disabled while work would be discarded',
      'strandedEdits.length > 0' in src.split('disabled={busy', 1)[1][:200])

# ── C. the warning is visible before Confirm is pressed ────────────────────────────────────────
check('C1 a banner renders beside the button when work would be discarded',
      'strandedEdits.length > 0 && (' in src)
check('C2 ... and says the work would not be saved',
      re.search(r'would not be saved', src) is not None)

# ── D. the opened-needs-a-count gate still stands ──────────────────────────────────────────────
check('D1 the client still gates opened-without-a-count', 'openedNoCount' in src)
check('D2 ... and still blocks the confirm', 'openedNoCount.length' in confirm_body)

# ── F. the server still stores what the client sends ───────────────────────────────────────────
from app.modules.closing import pickup_actual as pa  # noqa: E402

for declared, actual, want in ((500.0, 480.0, 480.0), (500.0, '480.50', 480.5),
                               (500.0, 0, 0.0), (0.0, 100.0, 100.0), (500.0, 500.0, 500.0)):
    vf = pa.variance_fields(declared, actual)
    got = vf['actual'] if vf else None
    check('F1 server stores %r counted against %r (got %r)' % (actual, declared, got), got == want)
check('F2 a blank count is still NOT RECORDED rather than a fake zero',
      pa.variance_fields(500.0, None) is None and pa.variance_fields(500.0, '') is None)
check('F3 the server still blocks an opened envelope with no count',
      len(pa.gate_items([{'store_code': 'X', 'amount': 500, 'envelope_opened': True}])) == 1)
check('F4 ... and still allows a count on a sealed envelope',
      pa.gate_items([{'store_code': 'X', 'amount': 500, 'actual_amount': 480}]) == [])

print('%s  harness_pickup_entered_work: %d checks, %d failed'
      % ('FAIL' if failures else 'OK  ', checks, len(failures)))
for f in failures:
    print('   FAILED: %s' % f)
sys.exit(1 if failures else 0)
