#!/usr/bin/env python3
"""LOCK — an AI-guard verdict is read in ONE place, and every call site dereferences it.

DB-free, stdlib only, static on purpose: a call site that reads the verdict wrongly still imports,
still type-checks and still ships. It simply never calls the model, and says nothing about it.

WHY THIS EXISTS (2026-11-20). `ai_guard_decision` returns `{"allow": ...}`. One call site tested
`decision.get("allowed")` — wrapped in `getattr(decision, "allowed", ...)`, which made the wrong
spelling look like defensive code rather than a typo. `.get("allowed")` is None, `not None` is True,
so the branch taken was ALWAYS the refusal branch: reading a drop-off label's printed characters
was skipped for every caller in every tenant from the day it shipped, and the screen reported it as
an unreadable photograph. A store re-took photos of a label nothing was ever going to read.

CLAUDE.md, "one fact, one home, dereferenced — never copied": the fact "did the guard allow this"
now lives in `control_box.decision_allows` and callers ASK it. This harness fails the build if a
caller goes back to spelling the field itself, which is what made the defect invisible.

    python3 backend/harness_ai_gate_one_reader.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from app.modules.core import control_box as cb          # noqa: E402

P = F = 0


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print("  ok  %s" % name)
    else:
        F += 1
        print("  FAIL %s   %s" % (name, detail))


def read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as fh:
        return fh.read()


def no_comments(src):
    """Drop whole-line comments so a `#`-quoted example of the banned shape is not a finding."""
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


# Every file that asks the guard for a verdict. Derived, not hand-listed: a NEW call site is found
# by the same scan that proves the existing ones, so this cannot go stale by somebody adding one.
# Two ways to obtain a verdict, and BOTH are scanned: through the I/O seam (`ai_gate.decide`) and
# straight from the pure function (`ai_guard_decision`, which the control box calls directly). The
# first draft scanned only the seam and so missed `control_box_api.py` entirely — a reader the scan
# cannot see is a reader this lock does not protect.
GATE_CALL = re.compile(
    r"\b(?:(?:_?ai_?gate|_gate)\.decide(?:_async)?|(?:\w+\.)?ai_guard_decision)\s*\(")
# EVERY file that asks the guard for a verdict, DECLARED — not a count, and not a lower bound.
# A size assertion would let a call site vanish silently (the house rule: "a guard that goes blind
# fails CI"). Written out, a site that leaves NAMES ITSELF, and a NEW adopter fails this line until
# somebody adds it here — which is the review this list exists to force, since adopting the guard
# without dereferencing the one reader is the exact defect of 2026-11-20.
# The file that DEFINES the verdict is not a caller of it. `control_box.py` is the one place the
# field may be spelled — inside `decision_allows` itself, which is the home — so it is excluded here
# BY NAME and proven separately in section C, which checks that control_box's own internals (the
# audit row) go through the home rather than around it. Excluding it silently would be a hole;
# excluding it by name, with its own section, is the point.
DEFINES_THE_VERDICT = "app/modules/core/control_box.py"

EXPECTED_CALLERS = {
    "app/modules/core/ai_gate.py",                  # the seam itself; its docstring IS the recipe
    "app/modules/core/control_box_api.py",
    "app/modules/core/data_qa_agent.py",
    "app/modules/core/data_qa_api.py",
    "app/modules/remediation/router.py",
    "app/modules/storeops/dropoff_label_api.py",
    "app/modules/storeops/router.py",
}
# The banned shapes: reading the verdict's field at the call site instead of asking the one home.
SPELLS_FIELD = re.compile(r"""decision\s*(?:\.get\(\s*["']allow(?:ed)?["']|\[\s*["']allow(?:ed)?["'])""")
GETATTR_DANCE = re.compile(r"""getattr\s*\(\s*decision\s*,\s*["']allow(?:ed)?["']""")

print("A. the one home behaves — a verdict, and anything that is not one")
check("an allow reads as allowed", cb.decision_allows({"allow": True, "code": "ok"}) is True)
check("a refusal reads as refused", cb.decision_allows({"allow": False, "code": "no_key"}) is False)
check("THE ORIGINAL DEFECT: the near-miss spelling is not an allowance",
      cb.decision_allows({"allowed": True}) is False)
check("a truthy non-True value is not an allowance (no accidental coercion)",
      cb.decision_allows({"allow": "yes"}) is False and cb.decision_allows({"allow": 1}) is False)
for junk in (None, "allow", 0, [], object()):
    check("a guard that returned %r refuses" % (junk,), cb.decision_allows(junk) is False)
check("a dict with no verdict at all refuses", cb.decision_allows({}) is False)
check("the refusal SENTENCE comes from the same home",
      cb.decision_reason({"allow": False, "reason": "No AI key is configured."})
      == "No AI key is configured.")
check("a refusal with no sentence still names its code",
      cb.decision_reason({"allow": False, "code": "rate_limited"}) == "rate_limited")
check("an allowed verdict has no refusal sentence",
      cb.decision_reason({"allow": True, "code": "ok"}) == "")

print("\nB. every call site dereferences it — none spells the field itself")
roots = [os.path.join(HERE, "app")]
callers, offenders = [], []
for root in roots:
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, HERE)
            src = read(rel)
            if not GATE_CALL.search(src):
                continue
            if rel == DEFINES_THE_VERDICT:
                continue
            callers.append(rel)
            body = no_comments(src)
            if SPELLS_FIELD.search(body) or GETATTR_DANCE.search(body):
                offenders.append(rel)

check("the call sites are EXACTLY the declared set — one leaving names itself",
      set(callers) == EXPECTED_CALLERS,
      {"only_in_tree": sorted(set(callers) - EXPECTED_CALLERS),
       "only_declared": sorted(EXPECTED_CALLERS - set(callers))})
check("every call site asks `decision_allows` instead of spelling the field",
      not offenders, offenders)
for rel in sorted(callers):
    check("%s dereferences the one home" % rel,
          "decision_allows(" in no_comments(read(rel)), rel)

print("\nC. the one home is not bypassed inside control_box itself")
cbx_src = no_comments(read("app", "modules", "core", "control_box.py"))
audit = cbx_src[cbx_src.index("def ai_audit_row("):]
audit = audit[:audit.index("\ndef ", 1)] if "\ndef " in audit[1:] else audit
check("the AUDIT row reads the verdict through the one home too",
      "decision_allows(" in audit and not SPELLS_FIELD.search(audit), audit[:200])

print("\nD. negative controls — each line can actually go red")
check("control: the banned `.get(\"allow\")` shape IS matched",
      bool(SPELLS_FIELD.search('if not decision.get("allow"):')))
check("control: the banned `.get(\"allowed\")` shape IS matched",
      bool(SPELLS_FIELD.search('if not decision.get("allowed"):')))
check("control: the subscript shape IS matched", bool(SPELLS_FIELD.search('decision["allow"]')))
check("control: the original getattr dance IS matched",
      bool(GETATTR_DANCE.search('getattr(decision, "allowed", False)')))
check("control: asking the one home is NOT matched",
      not SPELLS_FIELD.search("if not cbx.decision_allows(decision):")
      and not GETATTR_DANCE.search("if not cbx.decision_allows(decision):"))
check("control: a COMMENT quoting the banned shape is not a finding",
      not SPELLS_FIELD.search(no_comments('# decision.get("allowed") was the bug\nx = 1')))
check("control: the gate-call scan really finds a call site, by EITHER route",
      bool(GATE_CALL.search("decision, _cfg = _ai_gate.decide(client, org_id=o)"))
      and bool(GATE_CALL.search("await _gate.decide_async(client)"))
      and bool(GATE_CALL.search("decision = cbx.ai_guard_decision(caller, purpose=p)"))
      and bool(GATE_CALL.search("d = ai_guard_decision(caller)")))
check("control: an unrelated `.decide(` is not counted as a gate call",
      not GATE_CALL.search("self.policy.decide(x)"))
check("control: the definition site is excluded BY NAME, and is proven in section C instead",
      DEFINES_THE_VERDICT not in EXPECTED_CALLERS
      and os.path.exists(os.path.join(HERE, DEFINES_THE_VERDICT)))
check("control: the declared set is a SET of paths, not a count (a size pin cannot go blind)",
      isinstance(EXPECTED_CALLERS, set) and all("/" in c for c in EXPECTED_CALLERS))
check("control: a call site DISAPPEARING would be caught, not silently tolerated",
      (EXPECTED_CALLERS - (EXPECTED_CALLERS - {"app/modules/core/ai_gate.py"}))
      == {"app/modules/core/ai_gate.py"})

print("\n%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
