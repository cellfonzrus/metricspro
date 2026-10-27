"""PROOF — A LENGTH CAP DOES NOT DECIDE WHICH PART OF A STATUS SURVIVES.
stdlib only, DB-free, no framework. Run: python3 backend/harness_status_detail.py

FOUND 2026-10-27, from the owner's report: *"Is all the data being ingested properly for kpi, Ismail
reported that data was off"*.

The §COMMCALC-16 diagnostic WORKED. The DLAR sweep's 2026-10-26 run detected that the carrier had
renamed the store report's unit-count fields, and the line it composed named them AND named the fields
the portal is sending that nothing reads — the new spelling, which is the whole point. What was stored
on `dlar_sweep_config.last_detail`, measured live, was 600 characters ending:

    … store (DLAR): 'upgrades' NOT SENT,

mid-list, with the conclusion gone. `router._sweep_set_status` wrote `detail[:600]`: a HEAD cut, on a
line whose finding is written LAST. The detection worked and the finding died on the way to the column.

WHAT THIS PROVES (the interesting cases, not the easy ones)
 A. A line that already fits is returned UNCHANGED — a healthy status is byte-identical to before.
 B. THE REGRESSION, from the real 2026-10-26 line: over the cap, the conclusion at the END survives,
    and the number of dropped characters is stated rather than left to read as a trailing sentence.
 C. THE MIDDLE is what goes — and the middle is exactly what grows (one entry per changed column, one
    per store), so no amount of growth can push the conclusion out.
 D. THE CAP IS NEVER EXCEEDED, at any length, including the lengths where the marker's own digit count
    changes the arithmetic.
 E. A STATUS WRITER NEVER RAISES — None, a number, an object, a junk limit; the last place in the
    system that may throw is the one recording what went wrong.
 F. A cap too small for both ends degrades to the plain head cut rather than returning nonsense.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.core import status_detail as sd                     # noqa: E402

P = F = 0


def check(label, ok, got=None):
    global P, F
    if ok:
        P += 1
        print("  PASS  " + label)
    else:
        F += 1
        print("  FAIL  " + label + ("" if got is None else "  got=%r" % (got,)))


# The real line, reconstructed: what was STORED (600 chars, cut mid-list) plus the tail it lost.
STORED = (
    "⚠ PARTIAL — October 2026: 28 stores written as of 2026-10-24 — 7 day(s) SHORT of "
    "October 2026 · 45 reps written as of 2026-10-24 — 7 day(s) SHORT of October 2026 · "
    "⚠ COLUMN STOPPED ARRIVING: raw_dlar_store.gross_adds (0 of 28 rows), "
    "raw_dlar_store.total_upgrades (0 of 28 rows), raw_dlar_store.total_acts (0 of 28 rows), "
    "raw_dlar_rep.store (0 of 45 rows), raw_dlar_rep.ga_prepaid (0 of 45 rows) · "
    "⚠ PORTAL FIELD CHANGED: store (DLAR): 'gross_activation_quantity' NOT SENT, "
    "store (DLAR): 'prepaid_activations' NOT SENT, store (DLAR): 'postpaid_activations' NOT SENT, "
    "store (DLAR): 'upgrades' NOT SENT,"
)
# The invented new spellings are deliberately NOT substrings of any old field name: the first draft of
# this fixture used 'activation_quantity', which is inside 'gross_activation_quantity', so the pre-fix
# control passed for the wrong reason — it found the OLD name and called it the answer.
THE_ANSWER = (" FIELDS THE PORTAL SENDS THAT NOTHING READS (the new spelling is in here): "
              "store (DLAR): 'activation_qty', store (DLAR): 'upgrade_qty'")
REAL_LINE = STORED + " rep (Advocate): 'address' NOT SENT ·" + THE_ANSWER

print("A. a line that already fits is untouched")
check("A1 a short line is returned unchanged", sd.fit("OK — September 2026: 28 stores", 600)
      == "OK — September 2026: 28 stores")
check("A2 a line exactly at the cap is unchanged", sd.fit("x" * 600, 600) == "x" * 600)
check("A3 nothing is elided when nothing must be", "elided" not in sd.fit("x" * 599, 600))

print("\nB. the regression — the 2026-10-26 DLAR line, whose finding was at the end")
check("B0 the real line is over the cap (so the cut was real, not hypothetical)", len(REAL_LINE) > 600,
      len(REAL_LINE))
check("B1 PRE-FIX CONTROL — the head cut dropped the answer entirely, mid-list",
      "activation_qty" not in REAL_LINE[:600] and "NOTHING READS" not in REAL_LINE[:600]
      and REAL_LINE[:600].endswith("'upgrades' NOT SENT,"), REAL_LINE[:600][-40:])
kept = sd.fit(REAL_LINE, 600)
check("B2 the new spelling SURVIVES the cap", "activation_qty" in kept and
      "upgrade_qty" in kept, kept[-120:])
check("B3 ... and so does the label that tells the reader what it is",
      "NOTHING READS" in kept)
check("B4 the opening is still readable — the operator still sees which run this is",
      kept.startswith("⚠ PARTIAL — October 2026: 28 stores written"))
check("B5 the elision states HOW MUCH went, so a cut cannot read as a finished sentence",
      "elided" in kept and str(len(REAL_LINE) - 600) in kept, kept)
check("B6 the stored line is within the column's cap", len(kept) <= 600, len(kept))

print("\nC. the middle is what goes — and the middle is what grows")
grown = (STORED + " · " + ", ".join("raw_dlar_store.col%d (0 of 28 rows)" % i for i in range(40))
         + " ·" + THE_ANSWER)
kept_grown = sd.fit(grown, 600)
check("C1 a middle four times longer still cannot push the conclusion out",
      "activation_qty" in kept_grown and "upgrade_qty" in kept_grown)
check("C2 ... and the head is still the head", kept_grown.startswith("⚠ PARTIAL"))
check("C3 the dropped count grows with the line, it is not a fixed string",
      sd.fit(grown, 600) != sd.fit(REAL_LINE, 600))

print("\nD. the cap is never exceeded, at any length")
bad = [n for n in range(1, 1400)
       if len(sd.fit("H" * 40 + "m" * n + "TAIL", 600)) > 600]
check("D1 no input length produces an over-cap result", not bad, bad[:5])
bad2 = [lim for lim in range(1, 200) if len(sd.fit("x" * 5000, lim)) > lim]
check("D2 nor does any cap", not bad2, bad2[:5])
check("D3 the marker's own digit count is accounted for (a 4-, 5- and 6-digit drop all fit)",
      all(len(sd.fit("H" * 50 + "m" * n + "TAIL", 600)) <= 600
          for n in (1000, 12000, 150000)))

print("\nE. a status writer never raises")
for val in (None, 0, 12345, [], {}, object(), b"bytes"):
    try:
        sd.fit(val, 600)
        ok = True
    except Exception as exc:                                   # noqa: BLE001
        ok = False
        print("    raised on %r: %r" % (val, exc))
    check("E a %s is recorded, not raised on" % type(val).__name__, ok)
check("E8 None is the empty status, not the word None", sd.fit(None, 600) == "")
for lim in (None, "nonsense", -1, 0, 1.5):
    try:
        sd.fit("a long line " * 200, lim)
        ok = True
    except Exception as exc:                                   # noqa: BLE001
        ok = False
        print("    raised on limit %r: %r" % (lim, exc))
    check("E a junk limit (%r) does not raise" % (lim,), ok)
check("E13 a zero cap stores nothing rather than guessing", sd.fit("anything", 0) == "")

print("\nF. a cap too small for both ends degrades to the plain head cut")
tiny = sd.fit(REAL_LINE, 20)
check("F1 a 20-char cap gives 20 characters of head", tiny == REAL_LINE[:20], tiny)
check("F2 ... and does not emit a half-written marker", "elided" not in tiny)

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — a status keeps both ends, the elision says what it dropped, and the conclusion of a "
      "diagnostic cannot be the part a cap discards.")
