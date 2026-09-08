"""PROOF: who the closer is, and when a partial closing is the DM's problem.

OWNER DIRECTIVE 2026-09-07
    "the rep asad amar has been deleted from the system but it shows that he is still the closer.
     by default the closer will be the person who worked in the store for that day, not a predefined
     person, unless mandated by the tenant … there could be 2 or more people working and they are
     expected to close their own registers … if there are 2 people working and the closing is done by
     one, and the cash tallies up with the register x-report and pos report then it is green, but if
     the total is off and the second person who worked has not done their closing then it should be
     flagged in the DM verify … and the dm should verify."

THE LIVE STATE THAT FOUND IT (house org, org-scoped read 2026-09-07):

    storeops.store_closer            B-1115 -> E008 "Asad Umar"
    storeops.employees               45 rows, and E008 is NOT one of them
    storeops.tenants.closing_mode    'per_rep'

so DM Verify printed "closer: Asad Umar" on B-1115 — a deleted employee, named as the closer, on a
tenant that does not even use a predefined closer. Two defects, one screen:

  1. `delete_employee` cascaded to app_users but NEVER to store_closer, so the pointer outlived the
     person (the same omission the luxelink-parity audit found for logins).
  2. `_closing_summary_for_date` printed `closer_by_store.get(code)` verbatim on every card — in every
     mode, without asking whether that person worked, or still existed.

Note the money path was already safe: `ops_chargebacks._effective_closer` falls back when the
assignee has no punch, and a deleted employee cannot punch — so nobody was ever CHARGED for this. It
was a display-and-attention defect, which is why it survived four months of month-end review.

WHAT THIS PINS
  A. the default: no predefined closer — reality decides, and two workers means no single closer;
  B. 'one_closing' (the tenant MANDATE) honours the assignment only while it still means something;
  C. a stale assignment is always NAMED (off-roster / did-not-work), never silently dropped;
  D. two worked + one closed is GREEN when the cash ties, FLAGGED when it does not;
  E. "no X-report" is a third state, never green — the silent-zero rule.

PURE: stdlib only, no DB, no network.  Run: `cd backend && python3 harness_closer_resolution.py`
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


from app.modules.closing import closer_resolution as CR      # noqa: E402

# The live house-org roster shape: Asad Umar is gone, the others remain.
ROSTER_NAMES = ["Waleed Asghar", "Anjali Sharma", "Edgar", "Sunethri", "Maria Lopez"]
ROSTER_IDS = ["E005", "Anjali", "E014", "E018", "E021"]


def res(**kw):
    kw.setdefault("roster_names", ROSTER_NAMES)
    kw.setdefault("roster_ids", ROSTER_IDS)
    return CR.resolve_closer(**kw)


print("\n== A. THE DEFAULT: the closer is whoever worked, not a predefined person ==")
r = res(closing_mode="per_rep", assigned_name="Asad Umar", assigned_id="E008",
        worked=["Waleed Asghar"], submitted=["Waleed Asghar"])
check("A1 the deleted assignee is NOT named as the closer", r["name"] == "Waleed Asghar", r["name"])
check("A2 and the source says the answer came from who actually did it", r["source"] == "submitted", r)
check("A3 one person worked and closed -> they are the closer",
      res(closing_mode="per_rep", worked=["Edgar"], submitted=["Edgar"])["name"] == "Edgar")
check("A4 one person worked and did NOT close -> still them, sourced from 'worked'",
      res(closing_mode="per_rep", worked=["Edgar"], submitted=[])["name"] == "Edgar"
      and res(closing_mode="per_rep", worked=["Edgar"], submitted=[])["source"] == "worked")
r2 = res(closing_mode="per_rep", worked=["Edgar", "Sunethri"], submitted=["Edgar", "Sunethri"])
check("A5 TWO people worked and both closed -> there is NO single closer to name",
      r2["name"] is None and r2["source"] is None, r2)
check("A6 nobody worked at all -> no closer, and nothing invented",
      res(closing_mode="per_rep", worked=[], submitted=[])["name"] is None)
check("A7 with no assignment there is nothing to call stale",
      res(closing_mode="per_rep", worked=["Edgar"], submitted=["Edgar"])["assigned_off_roster"] is False)


print("\n== B. 'one_closing' — the tenant's mandate is honoured while it still means something ==")
r = res(closing_mode="one_closing", assigned_name="Edgar", assigned_id="E014",
        worked=["Edgar", "Sunethri"], submitted=["Edgar"])
check("B1 the assignee worked -> they ARE the closer, and the source says so",
      r["name"] == "Edgar" and r["source"] == "assigned", r)
check("B2 the assignee worked but did NOT submit -> still the closer (they owe it)",
      res(closing_mode="one_closing", assigned_name="Edgar", assigned_id="E014",
          worked=["Edgar", "Sunethri"], submitted=["Sunethri"])["name"] == "Edgar")
r = res(closing_mode="one_closing", assigned_name="Asad Umar", assigned_id="E008",
        worked=["Waleed Asghar"], submitted=["Waleed Asghar"])
check("B3 a DELETED assignee never holds the role — the day falls back to who did it",
      r["name"] == "Waleed Asghar" and r["source"] == "submitted", r)
check("B4 and the fallback is explained in one sentence, naming the person",
      "Asad Umar" in (r["note"] or "") and "no longer an employee" in (r["note"] or ""), r["note"])
r = res(closing_mode="one_closing", assigned_name="Edgar", assigned_id="E014",
        worked=["Sunethri"], submitted=["Sunethri"])
check("B5 an assignee who simply did not work that day also falls back",
      r["name"] == "Sunethri" and r["assigned_did_not_work"] is True
      and r["assigned_off_roster"] is False, r)
check("B6 and that reason is stated too, distinctly from deletion",
      "did not work this store" in (r["note"] or ""), r["note"])
r = res(closing_mode="one_closing", assigned_name="Asad Umar", assigned_id="E008",
        worked=["Edgar", "Sunethri"], submitted=[])
check("B7 a stale assignment with nobody submitting names no closer, and says so",
      r["name"] is None and "nobody here submitted" in (r["note"] or ""), r)


print("\n== C. A STALE ASSIGNMENT IS ALWAYS NAMED, never silently dropped ==")
for mode in ("per_rep", "one_closing"):
    r = res(closing_mode=mode, assigned_name="Asad Umar", assigned_id="E008",
            worked=["Edgar"], submitted=["Edgar"])
    check(f"C[{mode}] the assignment is still reported verbatim",
          r["assigned_name"] == "Asad Umar" and r["assigned_off_roster"] is True, r)
r = res(closing_mode="per_rep", assigned_name="Asad Umar", assigned_id="E008",
        worked=["Edgar"], submitted=["Edgar"])
check("C3 per_rep says plainly what to do about it",
      "no longer an employee" in (r["note"] or "") and "Cash Setup" in (r["note"] or ""), r["note"])
check("C4 a RENAME is not a deletion — the id still matching keeps the assignment live",
      res(closing_mode="one_closing", assigned_name="Ed Garcia", assigned_id="E014",
          worked=["Ed Garcia"], submitted=["Ed Garcia"])["assigned_off_roster"] is False)
check("C5 an assignment with no id still resolves by NAME against the roster",
      res(closing_mode="one_closing", assigned_name="Sunethri", assigned_id="",
          worked=["Sunethri"], submitted=["Sunethri"])["assigned_off_roster"] is False)
check("C6 a FAILED roster lookup never accuses the config of being stale",
      CR.resolve_closer(closing_mode="one_closing", assigned_name="Asad Umar", assigned_id="E008",
                        worked=["Asad Umar"], submitted=["Asad Umar"],
                        roster_names=[], roster_ids=[])["assigned_off_roster"] is False)
check("C7 and in that case the mandate still stands — a lookup failure must not demote the closer",
      CR.resolve_closer(closing_mode="one_closing", assigned_name="Asad Umar", assigned_id="E008",
                        worked=["Asad Umar"], submitted=["Asad Umar"],
                        roster_names=[], roster_ids=[])["name"] == "Asad Umar")


print("\n== D. TWO WORKED, ONE CLOSED — green when the cash ties, the DM's problem when it does not ==")
W2 = ["Edgar", "Sunethri"]
p = CR.partial_closing(worked=W2, submitted=["Edgar"], closing_mode="per_rep", money_ok=True)
check("D1 cash ties -> NOT flagged, exactly as the owner specified", p["flag"] is False, p)
check("D2 but it still says what happened, so the DM can see it without chasing it",
      "ties to the POS X-report" in (p["note"] or ""), p["note"])
check("D3 and it names who did not close", p["missing"] == ["Sunethri"], p["missing"])
p = CR.partial_closing(worked=W2, submitted=["Edgar"], closing_mode="per_rep", money_ok=False,
                       money_variances=["cash off by $42.00"])
check("D4 money off + a worker who never closed -> FLAGGED for the DM", p["flag"] is True, p)
check("D5 the note carries the count, the name and the amount",
      "2 people worked" in p["note"] and "Sunethri" in p["note"] and "$42.00" in p["note"], p["note"])
check("D6 everyone closed -> nothing to flag however off the money is",
      CR.partial_closing(worked=W2, submitted=W2, closing_mode="per_rep",
                         money_ok=False)["flag"] is False)
check("D7 a ONE-person store is never a partial closing",
      CR.partial_closing(worked=["Edgar"], submitted=["Edgar"], closing_mode="per_rep",
                         money_ok=False)["flag"] is False)
check("D8 in one_closing mode a single closing is what the tenant ASKED for — never flagged",
      CR.partial_closing(worked=W2, submitted=["Edgar"], closing_mode="one_closing",
                         money_ok=False)["flag"] is False)
check("D9 nobody closed at all is NOT reported here (that is its own alarm)",
      CR.partial_closing(worked=W2, submitted=[], closing_mode="per_rep",
                         money_ok=False)["flag"] is False)
check("D10 loose name matching means 'Edgar' closing does not leave 'Edgar Ruiz' owing",
      CR.partial_closing(worked=["Edgar Ruiz", "Sunethri"], submitted=["Edgar"],
                         closing_mode="per_rep", money_ok=True)["missing"] == ["Sunethri"])


print("\n== E. 'NO X-REPORT' IS A THIRD STATE — it is never green ==")
p = CR.partial_closing(worked=W2, submitted=["Edgar"], closing_mode="per_rep", money_ok=None)
check("E1 nothing to tie the cash against -> flagged, not waved through", p["flag"] is True, p)
check("E2 and the note says WHY it cannot be cleared, rather than implying a shortage",
      "no POS X-report" in (p["note"] or "") and "does not tie" not in (p["note"] or ""), p["note"])
check("E3 money_ok travels with the answer so the UI can render three states, not two",
      p["money_ok"] is None
      and CR.partial_closing(worked=W2, submitted=["Edgar"], money_ok=True)["money_ok"] is True
      and CR.partial_closing(worked=W2, submitted=["Edgar"], money_ok=False)["money_ok"] is False)


print("\n== F. RULE TWO — the tenant's mode decides; no carrier or tenant literal in the rule ==")
_src = open("app/modules/closing/closer_resolution.py").read()
import io as _io, tokenize as _tok                                        # noqa: E402
_code = " ".join(t.string for t in _tok.generate_tokens(_io.StringIO(_src).readline)
                 if t.type not in (_tok.COMMENT, _tok.STRING)).lower()
for banned in ("luxelink", "cellfonz", "vzone", "asad", "b-1115", "metro", "t-mobile", "verizon"):
    check(f"F {banned!r} appears nowhere in the rule itself", banned not in _code)
check("F0 the scan really did drop the prose (the docstring's own evidence is not in it)",
      "asad" in _src.lower() and "asad" not in _code)
check("F1 the only mode literal is the documented closing_mode value",
      "one_closing" in _src and "per_rep" in _src)


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
