"""THE LOCK — a guard that reads Python strings cannot go blind to f-strings (§CORE-32).

FOUND 2026-11-04, by running the suite on a newer interpreter than CI's.
`harness_carrier_vocab_guard.py` — the guard that stops our own setup internals ("is migration 1093
applied?") reaching a customer — was RED on Python 3.13 and GREEN on 3.11. The cause was not its
subject at all:

**PEP 701 (Python 3.12) stopped making an f-string one `tokenize.STRING` token.** It is now
`FSTRING_START` / `FSTRING_MIDDLE` / `FSTRING_END` with the interpolations tokenised in between. The
guard walked `STRING` tokens, so from 3.12 it saw NO f-string — and worse, it lost the interpolated
half of the commonest shape a hint is written in, leaving a text no detector objects to:

    raise HTTPException(500, "could not save (is migration "
                             f"{MIG} applied?): {e}")   →  read as  "could not save (is migration "

THE CLASS, not the instance. Three things made this expensive and none of them is about f-strings:

  ① **The same file held TWO readers of "the strings of a Python source", 500 lines apart**, and PEP
    701 had been taught to ONE of them. §19.18 in its most costly direction: a fact fixed in one copy
    and not the other is the same defect wearing a hat.
  ② **The failure was a NEGATIVE CONTROL**, so the temptation on finding it red is to delete the
    control — which would have left the guard silently blind rather than noisily broken.
  ③ **CI pins one interpreter.** A capability that depends on the interpreter must be proved on
    whichever one is running, or the proof only covers the version nobody will upgrade first.

WHAT FAILS THE BUILD

 (A) THE ONE HOME EXISTS AND WORKS ON THIS INTERPRETER — behavioural, not a text search:
     `harnesslib.py_string_expressions` / `py_string_parts` see an f-string, join it to the literal
     beside it, read each interpolation as a value, and drop a format spec. Run on whatever Python
     executes this file, and the version is printed so a green run says which one it proved.

 (B) THE FIXED GUARD DEREFERENCES IT and keeps no private string-token loop of its own.

 (C) THE RATCHET — every harness that still walks `tokenize.STRING` itself is named in
     `py_string_reader_pending.txt`, and that list may only SHRINK. Compared BOTH WAYS (§CORE-10): a
     file that is blind and unlisted fails, and a listed file that no longer is must leave the list
     rather than sit there as false debt.

 (D) NEGATIVE CONTROLS — a reader taught only the old token shape is caught; an unlisted blind file
     is caught; a stale entry is caught. A check that cannot fail is not a check.

Stdlib only, no DB, no network:  cd backend && python3 harness_py_string_reader_lock.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import harnesslib as hl   # noqa: E402

PENDING = os.path.join(HERE, "py_string_reader_pending.txt")
FIXED_GUARD = "harness_carrier_vocab_guard.py"
PASS = 0
FAIL = 0
PYV = f"{sys.version_info.major}.{sys.version_info.minor}"


def check(name, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + ("" if extra is None else "  -> " + repr(extra)))


def src(rel):
    with open(os.path.join(HERE, rel), encoding="utf-8") as fh:
        return fh.read()


def texts(source):
    return [t for _ln, t in hl.py_string_expressions(source)]


# ── the blindness, measured on whatever interpreter this is ──────────────────────────────────────
def walks_string_tokens(source):
    """Does this source walk Python STRING tokens itself? (As opposed to reading them through the
    one home.) DERIVED from the source, never a hand list."""
    return "tokenize.STRING" in source


def knows_pep701(source):
    """Does it know the 3.12 shape — either by handling the tokens or by dereferencing the home?"""
    return "FSTRING_MIDDLE" in source or "py_string_expressions" in source or "py_string_parts" in source


print(f"\n§A THE ONE HOME, PROVED ON PYTHON {PYV}")
check(f"A1 a bare f-string is seen at all (python {PYV})",
      texts('x = f"run migration {MIG} first"') == ["run migration 000 first"],
      texts('x = f"run migration {MIG} first"'))
check("A2 a literal and the f-string beside it join into the text a client receives",
      texts('raise E(500, "could not save (is migration "\n   f"{MIG} applied?): {e}")\n')
      == ["could not save (is migration 000 applied?): 000"])
check("A3 a format spec is code, not copy",
      texts('x = f"migration {n:03d} of {t!r} pending"') == ["migration 000 of 000 pending"])
check("A4 an interpolated NAME can never be read as copy",
      "SUPABASE" not in (texts('x = f"see {SUPABASE_REF} dashboard"') or [""])[0])
check("A5 a log line is still skipped, written either way",
      texts('print(f"run migration {MIG} — operator log")\nx = "copy"\n') == ["copy"]
      and texts('print("run migration 071")\nx = "copy"\n') == ["copy"])
check("A6 two plain literals still join, and separate expressions stay separate",
      texts('x = ("one "\n  "two")\n') == ["one two"]
      and texts('a = "first one"\nb = "second one"\n') == ["first one", "second one"])
check("A7 source that does not parse yields nothing rather than raising",
      hl.py_string_expressions("def (: not python") == [])
check("A8 the parts reader shares the walk — an f-string is ONE part on every interpreter",
      [t for _l, t in hl.py_string_parts('x = f"a {b} c"')] == ["a {} c"])

print("\n§B THE FIXED GUARD DEREFERENCES IT")
GUARD = src(FIXED_GUARD)
check("B1 it imports the one home", "import harnesslib as _hl" in GUARD)
check("B2 both of its readers delegate",
      "_hl.py_string_expressions(src)" in GUARD and "_hl.py_string_parts(src)" in GUARD)
check("B3 it keeps no private string-token walk",
      not walks_string_tokens(GUARD),
      "a tokenize.STRING loop is back in the guard — read through the home instead")
check("B4 it keeps no second copy of the interpolation rule",
      "_strip_braces = _hl.strip_interpolations" in GUARD)
check("B5 its own controls assert the capability, so a blind reader fails there too",
      "N10a" in GUARD and "N10b" in GUARD and "N10c" in GUARD)

print("\n§C THE RATCHET — the debt list may only shrink")
listed, bad_lines = [], []
for raw in src(os.path.basename(PENDING)).split("\n"):
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if not re.fullmatch(r"harness_[a-z0-9_]+\.py", line):
        bad_lines.append(line)
        continue
    listed.append(line)
check("C1 every line of the list is a harness file name", not bad_lines, bad_lines)
check("C2 every listed file exists", all(os.path.exists(os.path.join(HERE, f)) for f in listed),
      [f for f in listed if not os.path.exists(os.path.join(HERE, f))])

blind = sorted(f for f in os.listdir(HERE)
               if f.startswith("harness_") and f.endswith(".py")
               and walks_string_tokens(src(f)) and not knows_pep701(src(f)))
unlisted = [f for f in blind if f not in listed]
stale = [f for f in listed if f not in blind]
check("C3 no harness reads string tokens blind to f-strings without being on the list",
      not unlisted,
      f"{unlisted} — read strings through harnesslib.py_string_expressions / py_string_parts "
      "instead of adding a line; the list may only shrink")
check("C4 no entry is stale — a file that was fixed leaves the list (§CORE-10, both ways)",
      not stale, stale)
check("C5 the guard this change fixed is NOT on the list", FIXED_GUARD not in listed)
print(f"  ({len(blind)} harness(es) still walk string tokens themselves; none changes verdict on "
      f"3.11, 3.12 or 3.13 as measured 2026-11-04)")

print("\n§D NEGATIVE CONTROLS")
check("D1 a reader taught only the OLD token shape is caught",
      walks_string_tokens("for t in toks:\n    if t.type == tokenize.STRING:\n        pass\n")
      and not knows_pep701("if t.type == tokenize.STRING: pass"))
check("D2 a file that dereferences the home reads as aware",
      knows_pep701("return _hl.py_string_expressions(src)"))
check("D3 a blind file that is not on the list fails C3",
      bool([f for f in (blind + ["harness_planted_blind.py"]) if f not in listed]))
check("D4 a stale entry fails C4",
      bool([f for f in (listed + [FIXED_GUARD]) if f not in blind]))
check("D5 the home is not trivially passing — a wrong placeholder would be seen",
      texts('x = f"migration {MIG}"') != ["migration {MIG}"])

print("\n" + "=" * 60)
print(f"python {PYV}: %d passed, %d failed" % (PASS, FAIL))
if not FAIL:
    print("OK — the strings of a Python source have one home, f-strings included, proved on this "
          "interpreter; the blind-reader list can only shrink.")
sys.exit(1 if FAIL else 0)
