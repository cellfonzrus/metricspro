#!/usr/bin/env python3
"""DB-FREE proof for the data assistant's SEMANTIC LAYER — `core/data_qa_registry`.

What it proves, and why each one is here rather than being left to review:

  §A  every registered question is well-formed (a path, a declared envelope, an index reference,
      and no parameter of a kind the validator does not know)
  §B  `validate()` refuses what must be refused: an unregistered question, an unlisted parameter,
      a value of the wrong shape, a caller-supplied `org_id`, a missing required parameter, and a
      path param left unfilled — so no model output can become an arbitrary URL
  §C  `validate()` accepts and shapes what must be accepted, including both month spellings and
      repeated (RULE FIVE) parameters
  §D  `answerable()` / `catalog()` narrow by entitlement and NEVER leak a path to the model
  §E  `rows_from()` reads only the declared envelope keys, and `columns_of()` discovers columns
      rather than the registry declaring them (the no-second-copy property)
  §F  the injection shapes that matter: SQL, a traversal, a scheme, a newline, a control character
      and an over-long value are all refused as PARAMETER VALUES

Run: python3 backend/harness_data_qa_registry.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.core import data_qa_registry as reg   # noqa: E402

FAILS = []
CHECKS = 0


def ok(cond, label):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(label)


def section(name):
    print(f"\n── {name}")


# ── §A every question is well-formed ────────────────────────────────────────────────────────────
section("A. registry shape")
ok(len(reg.keys()) >= 8, "A0 at least the eight shipped questions are registered")
for k in reg.keys():
    q = reg.question(k)
    ok(isinstance(q.get("label"), str) and q["label"], f"A1 {k} has a label")
    ok(isinstance(q.get("answers"), str) and len(q["answers"]) > 20,
       f"A2 {k} says what it answers, in the owner's words")
    ok(str(q.get("path", "")).startswith("/api/v1/"), f"A3 {k} names a real API path")
    ok(isinstance(q.get("rows_at"), tuple), f"A4 {k} declares its response envelope")
    ok(bool(q.get("index")), f"A5 {k} cites the index section that documents it")
    ok(isinstance(q.get("grain"), str) and q["grain"], f"A6 {k} states its grain")
    for name, spec in (q.get("params") or {}).items():
        ok(spec["kind"] in reg.PARAM_KINDS, f"A7 {k}.{name} uses a known parameter kind")
    # Every path placeholder must be declared as a path param, or validate() can never fill it.
    for seg in str(q["path"]).split("{")[1:]:
        ok(seg.split("}")[0] in (q.get("path_params") or ()),
           f"A8 {k} declares its path parameter {seg.split('}')[0]!r}")
# THE no-write property, proven rather than asserted: the registry is the only source of paths, and
# nothing in it may be a mutation route.
for k in reg.keys():
    ok("method" not in reg.question(k), f"A9 {k} declares no method — the reader only ever GETs")

# ── §B refusals ─────────────────────────────────────────────────────────────────────────────────
section("B. validate() refuses")
p, qy, e = reg.validate("no_such_question", {})
ok(p is None and e and "not a registered question" in e[0], "B1 an unregistered question is refused")

p, qy, e = reg.validate("sales_by_store_rep_day", {"limit": "5"})
ok(p is None and any("not a parameter" in x for x in e), "B2 an unlisted parameter is refused")

p, qy, e = reg.validate("sales_by_store_rep_day", {"period": "last month"})
ok(p is None and e, "B3 a period that is not a month is refused")

p, qy, e = reg.validate("sales_by_store_rep_day", {"org_id": "00000000-0000-0000-0000-000000000002"})
ok(p is None and any("never set by a caller" in x for x in e),
   "B4 a caller-supplied org_id is refused — the token decides the tenant")

p, qy, e = reg.validate("sales_by_store_rep_day", {"authorization": "Bearer x"})
ok(p is None and e, "B5 a caller-supplied authorization is refused")

p, qy, e = reg.validate("profit_and_loss_range", {})
ok(p is None and any("period_from" in x for x in e), "B6 a missing required parameter is refused")

p, qy, e = reg.validate("executive_mtd", {})
ok(p is None and e, "B7 an unfilled path parameter is refused, never left as '{period}'")

p, qy, e = reg.validate("executive_mtd", {"period": "2026-09", "stores": ["B-1", "B-2"],
                                          "markets": "NY", "today": "not-a-date"})
ok(p is None and e, "B8 one bad value refuses the whole call (no partial read)")

p, qy, e = reg.validate("daily_targets_summary", {"period": "2026-09", "today": ["a", "b"]})
ok(p is None and any("takes one value" in x for x in e),
   "B9 a repeated value on a single-valued parameter is refused")

# ── §C acceptance ───────────────────────────────────────────────────────────────────────────────
section("C. validate() accepts")
p, qy, e = reg.validate("sales_by_store_rep_day", {"period": "2026-09"})
ok(not e and p == "/api/v1/commcalc/sales-report" and qy == {"period": "2026-09"},
   "C1 the numeric month spelling is accepted")

p, qy, e = reg.validate("sales_by_store_rep_day", {"period": "September 2026"})
ok(not e and qy == {"period": "September 2026"},
   "C2 the written month spelling is accepted — both are what the reports already take")

p, qy, e = reg.validate("executive_mtd", {"period": "2026-09", "stores": ["B-1115", "B 2022"],
                                          "reps": ["Abid K."]})
ok(not e and p == "/api/v1/commcalc/exec-mtd/2026-09",
   "C3 a path parameter is substituted from the registry")
ok(qy.get("stores") == ["B-1115", "B 2022"], "C4 a repeatable parameter stays a list (RULE FIVE)")
ok(qy.get("reps") == ["Abid K."], "C5 a person's name with a dot and a space is accepted")

p, qy, e = reg.validate("sales_by_store_rep_day", {})
ok(not e and qy == {}, "C6 an optional parameter may be omitted (the report's own default applies)")

p, qy, e = reg.validate("profit_and_loss", {"period": "2026-08", "scope": "consolidated"})
ok(not e and p == "/api/v1/account/pl/2026-08", "C7 the P&L path is built from the registry")

# ── §D the catalog the model is shown ───────────────────────────────────────────────────────────
section("D. entitlement narrowing")
all_keys = set(reg.keys())
ok(set(reg.answerable(None)) == all_keys,
   "D1 an unknown entitlement keeps every question (the endpoint is still the gate)")
no_fin = set(reg.answerable([]))
ok("profit_and_loss" not in no_fin and "profit_and_loss_range" not in no_fin,
   "D2 a tenant without the finance module is not offered the P&L questions")
ok("sales_by_store_rep_day" in no_fin,
   "D3 a question with no module gate is always offered")
with_fin = set(reg.answerable(["finance"]))
ok("profit_and_loss" in with_fin,
   "D4 switching the module on makes its questions answerable — the 'improves as data lands' path")
ok(no_fin < all_keys and no_fin <= with_fin, "D5 narrowing is monotone, never arbitrary")

cat = reg.catalog([])
ok(cat and all({"question", "label", "answers", "grain", "parameters"} == set(c) for c in cat),
   "D6 the catalog carries exactly what the model needs to choose")
blob = repr(cat)
ok("/api/v1/" not in blob,
   "D7 THE CATALOG LEAKS NO PATH — the model picks a question, so no model output is ever a route")
mtd = [c for c in reg.catalog(None) if c["question"] == "executive_mtd"][0]
ok(mtd["parameters"].get("period", {}).get("required") is True,
   "D8 a path parameter is presented as required")
ok(mtd["parameters"].get("stores", {}).get("repeatable") is True,
   "D9 a repeatable parameter is presented as repeatable")

# ── §E envelope declared, columns discovered ────────────────────────────────────────────────────
section("E. envelope vs columns")
ok(reg.rows_from("sales_by_store_rep_day", {"rows": [{"a": 1}]}) == [{"a": 1}],
   "E1 rows are read from the declared envelope key")
ok(reg.rows_from("sales_by_store_rep_day", {"stores": [{"a": 1}]}) == [],
   "E2 an UNDECLARED envelope key is not guessed at")
ok(reg.rows_from("executive_mtd", {"reps": [{"a": 1}]}) == [{"a": 1}],
   "E3 the first declared key that holds a list wins, in declared order")
ok(reg.rows_from("sales_by_store_rep_day", [{"a": 1}]) == [{"a": 1}],
   "E4 a bare list response is its own rows")
ok(reg.rows_from("sales_movement_summary", {"headline": "up 4%"}) == [],
   "E5 a summary-shaped question has no rows, and says so rather than inventing one")
ok(reg.rows_from("sales_by_store_rep_day", None) == [], "E6 a non-dict payload is empty, not a crash")

cols = reg.columns_of([{"store": "B-1", "rev": 1}, {"store": "B-2", "gp": 2}])
ok(cols == ("store", "rev", "gp"), "E7 columns are DISCOVERED from the rows, in first-seen order")
# The property that matters is structural, not textual: no entry may DECLARE a report's columns or
# measures. (Prose like "one column per month" in `grain` is a description of the report, not a copy
# of it, so a bare word search is the wrong test — this checks the keys.)
_BANNED_KEYS = {"columns", "column", "measures", "fields", "schema", "sql"}
ok(not any(_BANNED_KEYS & set(reg.question(k)) for k in reg.keys()),
   "E8 no question declares columns, measures or SQL — no second copy of any report's shape")

# ── §F injection shapes, as parameter values ────────────────────────────────────────────────────
section("F. a value is a value, never a query")
HOSTILE = [
    "2026-09'; DROP TABLE commcalc.raw_sales;--",
    "2026-09 OR 1=1",
    "../../../../etc/passwd",
    "../../account/pl/2026-09",
    "http://evil.example/x",
    "file:///etc/passwd",
    "2026-09\nx: y",
    "2026-09\x00",
    "%2e%2e%2f",
    "{period}",
    "9" * 200,
    "<script>alert(1)</script>",
]
for bad in HOSTILE:
    p, qy, e = reg.validate("sales_by_store_rep_day", {"period": bad})
    ok(p is None and e, f"F1 refused as a period: {bad[:30]!r}")
    p2, q2, e2 = reg.validate("executive_mtd", {"period": bad})
    ok(p2 is None and e2, f"F2 refused as a path parameter: {bad[:30]!r}")
for bad in HOSTILE:
    p, qy, e = reg.validate("action_plan", {"period": "2026-09", "store_code": bad})
    ok(p is None and e, f"F3 refused as a store code: {bad[:30]!r}")
# And the positive control: the refusals above are the PATTERN working, not the validator refusing
# everything.
p, qy, e = reg.validate("action_plan", {"period": "2026-09", "store_code": "B-1115"})
ok(not e and qy["store_code"] == "B-1115", "F4 a real store code still passes")

# ── §G a rep is offered only what narrows to them ───────────────────────────────────────────────
# OWNER DIRECTIVE 2026-10-05: a rep may ask the assistant about *"only their own commission, only
# their action plan"*. This section proves the OFFER side. The enforcement side is each endpoint's
# own narrowing (backend/harness_payout_audience.py §K) plus the agent's `authorized_questions`
# check (backend/harness_data_qa_lock.py §C) — three different files on purpose, because a single
# place that both decides and enforces is a place one edit can open.
section("G. a self-scoped rep is offered only the self-safe questions")
safe = set(reg.self_safe_keys())
rep_offer = set(reg.answerable(None, True))
ok(rep_offer == safe, "G1 a rep is offered exactly the self_safe questions")
ok(safe == {"my_commission", "my_commission_range", "action_plan"},
   f"G2 and those are their own commission and their own action plan, nothing else (got {sorted(safe)})")
ok(rep_offer < set(reg.keys()), "G3 a rep is offered strictly LESS than a manager, never more")
mgr_offer = set(reg.answerable(None, False))
ok(rep_offer <= mgr_offer, "G4 and never a question a manager is not offered")
for k in ("sales_by_store_rep_day", "profit_and_loss", "store_roster", "executive_mtd",
          "daily_targets_summary"):
    ok(k not in rep_offer, f"G5 a rep is NOT offered {k}")

# FAIL-CLOSED. A new question that says nothing about self scope must be manager-only, so that
# adding a report can never silently widen what a rep can ask. Armed: the same assertion run
# against a question that DOES declare it must come out the other way.
_bak = dict(reg.DATA_QUESTIONS)
try:
    reg.DATA_QUESTIONS["zz_new_report"] = {
        "label": "A report somebody added", "answers": "anything", "path": "/api/v1/zz",
        "params": {}, "rows_at": ("rows",), "grain": "x", "module": None, "index": ("0",)}
    ok("zz_new_report" not in set(reg.answerable(None, True)),
       "G6 a question that does not DECLARE self_safe is not offered to a rep (fail-closed)")
    ok("zz_new_report" in set(reg.answerable(None, False)),
       "G7 …while a manager is offered it as usual")
    reg.DATA_QUESTIONS["zz_new_report"]["self_safe"] = True
    ok("zz_new_report" in set(reg.answerable(None, True)),
       "G8 NEGATIVE CONTROL: declaring it self_safe DOES offer it — so G6 is the rule working, "
       "not the function refusing everything")
    reg.DATA_QUESTIONS["zz_new_report"]["self_safe"] = "yes"
    ok("zz_new_report" not in set(reg.answerable(None, True)),
       "G9 only a real True counts — a truthy string does not open the door")
finally:
    reg.DATA_QUESTIONS.clear()
    reg.DATA_QUESTIONS.update(_bak)
ok(set(reg.keys()) == set(_bak), "G10 the registry is left exactly as it was found")

# The two narrowings compose, and the module gate still applies on top of the self gate.
ok(set(reg.answerable([], True)) == safe,
   "G11 a tenant with no modules still offers a rep the ungated self-safe questions")

# Every self_safe question must SAY WHY, naming the mechanism — a bare True is a claim nobody can
# check, and this file is the one deciding who may read pay.
for k in sorted(safe):
    q = reg.question(k)
    note = str(q.get("self_note") or "")
    ok(len(note) > 40, f"G12 {k} declares HOW its endpoint narrows ({len(note)} chars)")
    ok("payout_audience" in note or "SAME handler" in note or "same handler" in note.lower()
       or "_caller_rep_keys" in note or "_caller_self_keyset" in note,
       f"G13 {k}'s note names the mechanism, not just an assurance")

# And the rep's catalog is still a catalog of QUESTIONS, carrying no route.
rep_cat = reg.catalog(None, True)
ok(rep_cat and len(rep_cat) == len(safe), "G14 the rep's catalog holds exactly those questions")
ok("/api/v1/" not in repr(rep_cat), "G15 the rep's catalog contains no URL either")
ok(all("self_note" not in c for c in rep_cat),
   "G16 the internal note is NOT shown to the model (it is a reviewer's fact, not a prompt)")

# ── report ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{'=' * 78}")
if FAILS:
    print(f"FAILED {len(FAILS)} of {CHECKS} checks:")
    for f in FAILS:
        print(f"  ✗ {f}")
    sys.exit(1)
print(f"OK — {CHECKS} checks passed (data-qa semantic layer)")
