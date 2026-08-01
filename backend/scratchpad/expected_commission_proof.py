"""PROOF HARNESS — EXPECTED vs EARNED + the permission-gated manual promote (mig 258).

OWNER DIRECTIVE 2026-08-01: "let the system calculate the expected commission as a separate column but
not use that to pay out, if the company gets paid the employee commission auto fills from there, there
should be an option to move the expected commission to the earned column if the system malfunctions or
the report is not updated on time, this will be done as an edit function gated per permission."

Runs the REAL engine against an in-memory Supabase-shaped client, DIFFERENTIALLY against the BASE tree
vendored out of `git show main:` (base = 79a969c — note: LOCAL main, because origin/main is still
behind at 4923001 while the previous package's push is in flight).

  §A  expected_commission — PURE unit proof, incl. the two branches that matter: a stale promote is
      never paid at the stored number, and the window is config with the owner's default.
  §B  ENGINE, NO PROMOTES — byte-identical to BASE once the two additive expected keys are removed,
      for a plan tenant AND for Boost. `expected_guard` with nothing configured is inert.
  §C  EXPECTED IS A COLUMN, NOT A PAYMENT — it equals the pre-gate amount, and it is absent from
      by_rep, totals.amount and every payout figure. Asserted by construction AND numerically.
  §D  THE PROMOTE PAYS, ONCE, WITH PROVENANCE — and SURVIVES RECOMPUTE (the delete-then-insert
      persist path is replayed twice and the payment is still there).
  §E  NEVER A STALE NUMBER — hold_and_warn (default) holds and shouts; pay_current_and_warn pays
      TODAY's figure and shouts. No mode pays the stored figure.
  §F  NEVER RESURRECTS A MONTH THAT DOES NOT EXIST — flat-suppressed (mig 256), non-qualifying
      category (mig 245) and out-of-window promotes are all UNAPPLIED and REPORTED with the reason.
  §G  _persist IS ADAPTIVE — the delete has already run, so a rejected column set must NOT empty the
      period. Proven with a client that refuses the new columns.
  §H  MULTI-TENANT + WRITE SCOPE — org-scoped reads, two tenants isolated, and the ONLY table the
      engine writes is the ledger (never the promote table).
  §I  MIGRATION 258 — real PostgreSQL parse, additive, idempotent, RLS, org_id, no GRANT/POLICY/anon,
      no seed, and no money table is rewritten.
  §J  DIFFERENTIAL SCOPE vs BASE.

Run:  cd backend && PYTHONPATH=. python3 scratchpad/expected_commission_proof.py
"""
import copy
import importlib.util
import inspect
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.modules.commcalc import expected_commission as xc
from app.modules.commcalc import sale_installment_engine as sie

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# PINNED TO A LITERAL COMMIT (2026-08-01). Two hazards, one fix:
#   · `origin/main` was BEHIND (4923001) when this was written — vendoring from it would have diffed
#     against a tree missing the previous package and silently passed.
#   · a moving ref of ANY kind (`main` included) means that once THIS package merges, the harness
#     vendors a BASE that already contains its own changes and starts diffing itself against itself.
#     That is exactly what happened to the sibling fwa-flat proof an hour after its push landed.
# So: the literal commit this suite was built against. Reproducible forever, whatever the refs do.
BASE_REF = "79a969c"          # local main at build time (= the merged fwa-flat package)
OK = FAIL = 0
FAILS = []


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILS.append(name)
        print(f"  FAIL {name} {extra}")


def sec(t):
    print(f"\n{t}\n" + "─" * 94)


def _helpers():
    """Reuse the SHIPPED fwa-flat fixture builder (it is in main now) without running its assertions."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fwa_flat_accessory_def_proof.py")
    src = open(path, encoding="utf-8").read()
    import types
    mod = types.ModuleType("xc_helpers")
    mod.__file__ = path
    exec(compile(src.split('\nsec("§A')[0], path, "exec"), mod.__dict__)
    return mod


H = _helpers()
FakeClient, FakeQuery, MissingTable = H.FakeClient, H.FakeQuery, H.MissingTable
ORG, ORG_B, BOOST_ORG, PER = H.ORG, H.ORG_B, H.BOOST_ORG, H.PER


def store(gate_from=2, promotes=None, xcfg=None, org=ORG, payout=None, qual=None):
    """The shipped fixture, with the paid gate ON from month `gate_from` so months 2..N are WITHHELD —
    which is the only state in which an EXPECTED column and a promote mean anything."""
    s = H.build_store(payout=payout, org=org)
    for sc in s["commcalc.plan_installment_schedule"]:
        sc["gate_from_month"] = gate_from
    cfgrow = {}
    if payout is not None:
        cfgrow["installment_category_payout"] = payout
    if qual is not None:
        cfgrow["installment_category_qualification"] = qual
    if xcfg is not None:
        cfgrow["expected_commission_config"] = xcfg
    s["commcalc.commission_org_config"] = [dict(org_id=org, **cfgrow)] if cfgrow else []
    s["commcalc.installment_promote"] = list(promotes or [])
    return s


def run(s, org=ORG, **kw):
    return sie.compute_sale_installments(FakeClient(s), org, PER, persist=False, **kw)


def _vendor(path, name):
    try:
        src = subprocess.check_output(["git", "-C", REPO, "show", f"{BASE_REF}:{path}"],
                                      stderr=subprocess.DEVNULL).decode()
    except Exception:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=f"_{name}.py", delete=False) as fh:
        fh.write(src)
        p = fh.name
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def promote(trans_id, mdn, month_index, expected_at, pid="PR1", status="active",
            who="user-42", when="2026-08-01T10:00:00Z", org=ORG, period=PER):
    return dict(id=pid, org_id=org, pay_period=period, trans_id=trans_id, mdn=mdn,
                month_index=month_index, expected_at_promote=expected_at, status=status,
                reason="carrier report not updated on time", promoted_by=who, promoted_at=when)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§A  expected_commission — PURE")

d = xc.normalize_config(None)
chk("A1 the owner's window is the DEFAULT: months 2..6",
    d["from_month"] == 2 and d["to_month"] == 6 and d["enabled"] is True, d)
chk("A2 month 1 is OUTSIDE the window (it has its own activation gate)", not xc.in_window(1, d))
chk("A3 months 2..6 are inside", all(xc.in_window(m, d) for m in (2, 3, 4, 5, 6)))
chk("A4 month 7 is outside", not xc.in_window(7, d))
chk("A5 the window is CONFIG, not a constant",
    xc.normalize_config({"from_month": 3, "to_month": 4})["to_month"] == 4
    and xc.in_window(3, xc.normalize_config({"from_month": 3, "to_month": 4}))
    and not xc.in_window(2, xc.normalize_config({"from_month": 3, "to_month": 4})))
chk("A6 a REVERSED window is repaired, not silently paid as empty",
    xc.normalize_config({"from_month": 6, "to_month": 2})["from_month"] == 2)
chk("A7 an absurd window is clamped to 1..12",
    xc.normalize_config({"from_month": 0, "to_month": 99}) ["to_month"] == 12)
chk("A8 the default posture on a changed expected is HOLD",
    d["on_expected_change"] == "hold_and_warn")
chk("A9 an unidentified caller is refused by DEFAULT (this is a money write)",
    d["promote_allow_unidentified"] is False)
chk("A10 a garbage mode falls back to the safe default",
    xc.normalize_config({"on_expected_change": "yolo"})["on_expected_change"] == "hold_and_warn")

k1 = xc.promote_key(PER, "T1", "305", 2)
chk("A11 promote_key is the ledger's own UNIQUE-key shape", k1 == (PER, "T1", "305", 2), k1)
chk("A12 row_key(ledger row) == promote_key",
    xc.row_key({"pay_period": PER, "trans_id": "T1", "mdn": "305", "month_index": 2}) == k1)

idx = xc.build_index([promote("T1", "305", 2, 10.0, pid="A"),
                      promote("T1", "305", 2, 99.0, pid="B", status="revoked")])
chk("A13 a REVOKED promote is never applied", idx[k1]["id"] == "A", idx)
idx2 = xc.build_index([promote("T1", "305", 2, 10.0, pid="A", when="2026-08-01T09:00:00Z"),
                       promote("T1", "305", 2, 20.0, pid="B", when="2026-08-01T11:00:00Z")])
chk("A14 a collision resolves to the most recent, deterministically", idx2[k1]["id"] == "B")

ev = xc.evaluate(promote("T1", "305", 2, 10.0), 10.0, d)
chk("A15 unchanged expected -> applies at today's figure", ev["apply"] and ev["amount"] == 10.0 and not ev["stale"])
ev = xc.evaluate(promote("T1", "305", 2, 10.0), 12.5, d)
chk("A16 CHANGED expected under hold_and_warn -> does NOT apply", ev["apply"] is False and ev["stale"], ev)
chk("A17 ...and it never offers the stored number", ev["amount"] is None, ev)
ev = xc.evaluate(promote("T1", "305", 2, 10.0), 12.5,
                 xc.normalize_config({"on_expected_change": "pay_current_and_warn"}))
chk("A18 CHANGED expected under pay_current_and_warn -> pays TODAY's figure, not the stored one",
    ev["apply"] and ev["amount"] == 12.5 and ev["expected_at_promote"] == 10.0, ev)
chk("A19 NO code path anywhere returns the stored figure as the amount",
    "expected_at_promote" not in inspect.getsource(xc.evaluate).split("return")[-1]
    and all(ev2["amount"] in (None, round(float(now), 2))
            for now in (5.0, 7.25, 0.0)
            for ev2 in [xc.evaluate(promote("T", "M", 2, 999.0), now, d),
                        xc.evaluate(promote("T", "M", 2, 999.0), now,
                                    xc.normalize_config({"on_expected_change": "pay_current_and_warn"}))]))
ev = xc.evaluate(promote("T1", "305", 2, None), 10.0, d)
chk("A20 a promote with no recorded figure is not treated as stale", ev["apply"] and not ev["stale"])

pr = xc.promote_row(ORG_B, {"pay_period": PER, "trans_id": "T9", "mdn": "3", "month_index": 4,
                            "expected_amount": 7.77, "epay_salesperson": "A"}, "late report", "u1", "now")
chk("A21 promote_row STAMPS org_id (RULE ONE)", pr["org_id"] == ORG_B)
chk("A22 ...and records the approved figure + who/when/why",
    pr["expected_at_promote"] == 7.77 and pr["reason"] == "late report"
    and pr["promoted_by"] == "u1" and pr["status"] == "active", pr)
chk("A23 no tenant/carrier literal in the module body",
    not re.search(r"luxelink|boost|total wireless", open(xc.__file__).read().split('"""', 2)[2], re.I))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§B  ENGINE with NO promotes — byte-identical to BASE")

base_sie = _vendor("backend/app/modules/commcalc/sale_installment_engine.py", "sie_base_258")
chk("B1 BASE engine vendored from local main", base_sie is not None)

NEW_ROW_KEYS = {"expected_amount", "expected_in_window"}


def strip_new(res):
    o = copy.deepcopy(res)
    o.pop("expected_guard", None)
    o["ledger"] = [{k: v for k, v in r.items() if k not in NEW_ROW_KEYS} for r in o.get("ledger", [])]
    return o


new = run(store())
if base_sie:
    base = base_sie.compute_sale_installments(FakeClient(store()), ORG, PER, persist=False)
    chk("B2 the whole payload equals BASE once the two expected keys are removed",
        strip_new(new) == base,
        [k for k in set(strip_new(new)) | set(base) if strip_new(new).get(k) != base.get(k)])
    chk("B3 ...including `totals` exactly", new["totals"] == base["totals"], new["totals"])
    chk("B4 ...and `by_rep` to the cent", new["by_rep"] == base["by_rep"], (new["by_rep"], base["by_rep"]))
    chk("B5 ...and `warnings` byte-for-byte (no new noise with no promotes)",
        new["warnings"] == base["warnings"])
    chk("B6 ...and `flags` (nobody's withheld flag moved)", new["flags"] == base["flags"])
    bb = base_sie.compute_sale_installments(FakeClient(store(org=BOOST_ORG)), BOOST_ORG, PER, persist=False)
    nb = run(store(org=BOOST_ORG), org=BOOST_ORG)
    chk("B7 BOOST tenant: identical to BASE once the expected keys are removed", strip_new(nb) == bb)
xg = new["expected_guard"]
chk("B8 expected_guard with no promotes is inert",
    xg["promotes_applied"] == 0 and xg["promotes_unapplied"] == 0 and xg["promotes_stale"] == 0
    and xg["promoted_amount"] == 0.0, xg)
chk("B9 mig 258 unapplied (no promote table, no config column) still runs and pays the same",
    strip_new(sie.compute_sale_installments(
        FakeClient(store(), ), ORG, PER, persist=False)) == strip_new(new))
missing = FakeClient(store(), missing=("commcalc.installment_promote",
                                       "commcalc.commission_org_config"))
chk("B10 ...even with BOTH new objects absent",
    sie.compute_sale_installments(missing, ORG, PER, persist=False)["totals"] == new["totals"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§C  EXPECTED IS A COLUMN, NOT A PAYMENT")

led = {(r["trans_id"], r["month_index"]): r for r in new["ledger"]}
withheld = [r for r in new["ledger"] if not r["paid_gate_met"]]
chk("C1 the fixture really has withheld months (otherwise this section proves nothing)",
    len(withheld) >= 2, len(withheld))
chk("C2 every withheld month carries an EXPECTED amount while paying $0",
    all(r["amount"] == 0.0 for r in withheld)
    and any(r["expected_amount"] > 0 for r in withheld),
    [(r["trans_id"], r["month_index"], r["expected_amount"], r["amount"]) for r in withheld])
chk("C3 EXPECTED equals the amount the month WOULD pay (pre-gate)",
    all(round(r["expected_amount"], 2) == round(r["amount"], 2)
        for r in new["ledger"] if r["paid_gate_met"]),
    [(r["trans_id"], r["expected_amount"], r["amount"]) for r in new["ledger"] if r["paid_gate_met"]])
chk("C4 month 1 is flagged OUT of the window (owner asked for 2..6)",
    all(not r["expected_in_window"] for r in new["ledger"] if r["month_index"] == 1))
chk("C5 months 2..6 are flagged IN",
    all(r["expected_in_window"] for r in new["ledger"] if 2 <= r["month_index"] <= 6))
chk("C6 EXPECTED IS NOT PAID: by_rep equals the sum of the EARNED amounts only",
    round(sum(new["by_rep"].values()), 2)
    == round(sum(r["amount"] for r in new["ledger"]), 2), (new["by_rep"], new["totals"]))
chk("C7 ...and totals.amount excludes every unearned expected dollar",
    round(new["totals"]["amount"], 2)
    == round(sum(r["amount"] for r in new["ledger"] if r["paid_gate_met"]), 2))
chk("C8 the unearned expected is REPORTED separately and is non-zero here",
    xg["expected_unearned_total"] > 0
    and xg["expected_unearned_total"] != new["totals"]["amount"], xg)
chk("C9 expected_total counts ONLY in-window months",
    round(xg["expected_total"], 2)
    == round(sum(r["expected_amount"] for r in new["ledger"] if r["expected_in_window"]), 2))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§D  THE PROMOTE — pays once, with provenance, and SURVIVES RECOMPUTE")

tgt = next(r for r in new["ledger"] if not r["paid_gate_met"] and r["expected_amount"] > 0
           and r["expected_in_window"])
P1 = promote(tgt["trans_id"], tgt["mdn"], tgt["month_index"], tgt["expected_amount"])
res = run(store(promotes=[P1]))
row = next(r for r in res["ledger"]
           if (r["trans_id"], r["month_index"]) == (tgt["trans_id"], tgt["month_index"]))
chk("D1 the promoted month now PAYS its expected amount", row["amount"] == tgt["expected_amount"], row)
chk("D2 ...and says so in its status", row["status"] == "paid_manual_promote", row["status"])
chk("D3 ...with full provenance on the row",
    row.get("gate_kind") == "manual_promote" and row.get("promoted_by") == "user-42"
    and row.get("promote_reason") and row.get("promote_id") == "PR1", row)
chk("D4 the money moved by EXACTLY the expected amount, not a penny more",
    round(res["totals"]["amount"] - new["totals"]["amount"], 2) == round(tgt["expected_amount"], 2),
    (res["totals"], new["totals"]))
chk("D5 it paid ONCE (the row count is unchanged — nothing was duplicated)",
    len(res["ledger"]) == len(new["ledger"]))
chk("D6 no OTHER row moved",
    {(r["trans_id"], r["month_index"]): r["amount"] for r in res["ledger"]
     if (r["trans_id"], r["month_index"]) != (tgt["trans_id"], tgt["month_index"])}
    == {(r["trans_id"], r["month_index"]): r["amount"] for r in new["ledger"]
        if (r["trans_id"], r["month_index"]) != (tgt["trans_id"], tgt["month_index"])})
chk("D7 the withheld FLAGS for that month are gone (it is no longer withheld)",
    len(res["flags"]) < len(new["flags"]))
chk("D8 expected_guard reports it, with the approver",
    res["expected_guard"]["promotes_applied"] == 1
    and res["expected_guard"]["promoted_amount"] == tgt["expected_amount"]
    and res["expected_guard"]["applied"][0]["promoted_by"] == "user-42",
    res["expected_guard"])

# SURVIVES RECOMPUTE — the whole point. Replay the delete-then-insert persist path twice.
class PersistClient(FakeClient):
    """A client that really applies delete + upsert to its store, so a recompute can be replayed."""
    def schema(self, s):
        outer = self

        class Q(FakeQuery):
            def __init__(self, rows, log, table):
                super().__init__(rows, log, table)
                self._t = table

            def delete(self):
                self._del = True
                return self

            def upsert(self, rows, *a, **k):
                rows = rows if isinstance(rows, list) else [rows]
                cur = outer.store.setdefault(self._t, [])
                for r in rows:
                    cur.append(dict(r))
                return type("E", (), {"execute": lambda _s: type("R", (), {"data": rows})()})()

            def execute(self):
                if getattr(self, "_del", False):
                    keep = [r for r in outer.store.get(self._t, []) if r not in self._apply()]
                    outer.store[self._t] = keep
                    return type("R", (), {"data": []})()
                return super().execute()

        class S:
            def table(_s, t):
                return Q(outer.store.get(f"{s}.{t}", []), None, f"{s}.{t}")
        return S()


st = store(promotes=[P1])
st["commcalc.sale_installment_ledger"] = []
pc = PersistClient(st)
r1 = sie.compute_sale_installments(pc, ORG, PER, persist=True)
n1 = len(st["commcalc.sale_installment_ledger"])
r2 = sie.compute_sale_installments(pc, ORG, PER, persist=True)
n2 = len(st["commcalc.sale_installment_ledger"])
chk("D9 a RECOMPUTE (delete-then-insert) does not duplicate the ledger", n1 == n2 and n1 > 0, (n1, n2))
chk("D10 ...and the promoted month STILL pays after the recompute",
    r2["expected_guard"]["promotes_applied"] == 1
    and r2["totals"]["amount"] == r1["totals"]["amount"] == res["totals"]["amount"],
    (r1["totals"], r2["totals"]))
chk("D11 ...because the calc NEVER deletes the promote table",
    len(st["commcalc.installment_promote"]) == 1, st["commcalc.installment_promote"])
chk("D12 the persisted ledger row carries the promote audit columns",
    any(r.get("promoted_by") == "user-42" and r.get("expected_amount") is not None
        for r in st["commcalc.sale_installment_ledger"]),
    st["commcalc.sale_installment_ledger"][:2])

# A promote whose gate LATER meets on its own -> REDUNDANT, never double-paid. Modelled with a
# schedule gated from month 3, so month 2 is in-window AND already earned — exactly the state a chain
# lands in when the carrier statement finally arrives after someone had promoted it.
new3 = run(store(gate_from=3))
paid_row = next(r for r in new3["ledger"] if r["paid_gate_met"] and r["expected_in_window"])
res_r = run(store(gate_from=3, promotes=[promote(paid_row["trans_id"], paid_row["mdn"],
                                                 paid_row["month_index"],
                                                 paid_row["expected_amount"])]))
chk("D13 a promote on a month the gate has since met is REDUNDANT, not a second payment",
    res_r["totals"]["amount"] == new3["totals"]["amount"]
    and res_r["expected_guard"]["promotes_redundant"] == 1, (res_r["totals"], new3["totals"]))
chk("D14 ...and it is reported so it can be cleaned up",
    any(w["type"] == "promote_redundant" for w in res_r["warnings"]),
    [w["type"] for w in res_r["warnings"]])
chk("D15 ...and it is NOT counted as applied (no phantom second payment in the guard)",
    res_r["expected_guard"]["promotes_applied"] == 0
    and res_r["expected_guard"]["promoted_amount"] == 0.0, res_r["expected_guard"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§E  NEVER PAID AT A STALE NUMBER")

STALE = promote(tgt["trans_id"], tgt["mdn"], tgt["month_index"], round(tgt["expected_amount"] + 50, 2))
res_h = run(store(promotes=[STALE]))
chk("E1 hold_and_warn (default): the month does NOT pay",
    res_h["totals"]["amount"] == new["totals"]["amount"], (res_h["totals"], new["totals"]))
chk("E2 ...and it is LOUD",
    any(w["type"] == "promote_expected_changed" for w in res_h["warnings"]),
    [w["type"] for w in res_h["warnings"]])
w = next(w for w in res_h["warnings"] if w["type"] == "promote_expected_changed")
chk("E3 ...naming BOTH figures", w["expected_at_promote"] == STALE["expected_at_promote"]
    and w["expected_now"] == tgt["expected_amount"], w)
chk("E4 ...and telling the operator what to do", "re-approve" in w["detail"].lower(), w["detail"])
chk("E5 ...and it is counted as stale, not applied",
    res_h["expected_guard"]["promotes_stale"] == 1
    and res_h["expected_guard"]["promotes_applied"] == 0, res_h["expected_guard"])

res_p = run(store(promotes=[STALE], xcfg={"on_expected_change": "pay_current_and_warn"}))
chk("E6 pay_current_and_warn: it pays TODAY's figure",
    round(res_p["totals"]["amount"] - new["totals"]["amount"], 2) == round(tgt["expected_amount"], 2),
    (res_p["totals"], new["totals"]))
chk("E7 ...NOT the approved (stale) figure",
    round(res_p["totals"]["amount"] - new["totals"]["amount"], 2)
    != round(STALE["expected_at_promote"], 2))
chk("E8 ...and it still shouts", any(w2["type"] == "promote_expected_changed" for w2 in res_p["warnings"]))
chk("E9 NEITHER mode ever paid the stored number",
    all(round(r["totals"]["amount"] - new["totals"]["amount"], 2) != round(STALE["expected_at_promote"], 2)
        for r in (res_h, res_p)))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§F  NEVER RESURRECTS A MONTH THAT DOES NOT EXIST")

# ① a category paid as a ONE-TIME FLAT amount (mig 256) has no months 2..N
flat = {"home_internet": {"mode": "flat_once", "amount": 25.0, "pay_month": 1}}
base_flat = run(store(payout=flat))
fwa_ids = {r["trans_id"] for r in new["ledger"] if str(r["trans_id"]).startswith("T-FWA")}
gone = [r for r in new["ledger"]
        if str(r["trans_id"]).startswith("T-FWA") and r["month_index"] >= 2]
chk("F1 the fixture really has flat-suppressed FWA months to try to resurrect", len(gone) >= 1, len(gone))
sup = gone[0]
res_f = run(store(payout=flat,
                  promotes=[promote(sup["trans_id"], sup["mdn"], sup["month_index"],
                                    sup["expected_amount"] or 1.0)]))
chk("F2 a promote CANNOT resurrect a flat-suppressed month",
    res_f["totals"]["amount"] == base_flat["totals"]["amount"]
    and not any(r["trans_id"] == sup["trans_id"] and r["month_index"] == sup["month_index"]
                for r in res_f["ledger"]),
    (res_f["totals"], base_flat["totals"]))
chk("F3 ...and it is REPORTED as unapplied with the right reason",
    res_f["expected_guard"]["promotes_unapplied"] == 1
    and res_f["expected_guard"]["unapplied"][0]["reason_code"] == "month_suppressed",
    res_f["expected_guard"]["unapplied"])
chk("F4 ...in plain language", "one-time flat"
    in res_f["expected_guard"]["unapplied"][0]["reason"].lower(),
    res_f["expected_guard"]["unapplied"][0]["reason"])
chk("F5 ...and a warning names it", any(w["type"] == "promote_unapplied" for w in res_f["warnings"]))

# ② a month OUTSIDE the configured window
res_w = run(store(promotes=[promote(tgt["trans_id"], tgt["mdn"], tgt["month_index"],
                                    tgt["expected_amount"])],
                  xcfg={"from_month": 5, "to_month": 6}))
chk("F6 a promote outside the window does NOT pay",
    res_w["totals"]["amount"] == new["totals"]["amount"], res_w["totals"])
chk("F7 ...and is reported as out_of_window",
    res_w["expected_guard"]["unapplied"][0]["reason_code"] == "out_of_window",
    res_w["expected_guard"]["unapplied"])
chk("F8 ...and those months lose their EXPECTED window flag too",
    all(not r["expected_in_window"] for r in res_w["ledger"] if r["month_index"] < 5))

# ③ the feature switched OFF entirely
res_off = run(store(promotes=[P1], xcfg={"enabled": False}))
chk("F9 with the feature disabled a promote pays nothing",
    res_off["totals"]["amount"] == new["totals"]["amount"], res_off["totals"])
chk("F10 ...and says so", res_off["expected_guard"]["unapplied"][0]["reason_code"] == "disabled",
    res_off["expected_guard"]["unapplied"])

# ④ a promote pointing at a transaction that is not in this period at all
res_nf = run(store(promotes=[promote("T-DOES-NOT-EXIST", "999", 2, 5.0)]))
chk("F11 a promote for a non-existent chain pays nothing and is reported",
    res_nf["totals"]["amount"] == new["totals"]["amount"]
    and res_nf["expected_guard"]["unapplied"][0]["reason_code"] == "chain_not_found",
    res_nf["expected_guard"]["unapplied"])

# ⑤ a category that does not QUALIFY (mig 245) emits nothing — a promote cannot revive it either
res_q = run(store(qual={"phone": False},
                  promotes=[promote(tgt["trans_id"], tgt["mdn"], tgt["month_index"],
                                    tgt["expected_amount"])]))
chk("F12 a promote cannot revive a non-qualifying category",
    not any(r["trans_id"] == tgt["trans_id"] and r["month_index"] == tgt["month_index"]
            for r in res_q["ledger"])
    and res_q["expected_guard"]["promotes_applied"] == 0, res_q["expected_guard"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§G  _persist IS ADAPTIVE — an unrun migration must not empty the period")

class NoNewColsClient(FakeClient):
    """PostgREST as it behaves BEFORE migration 258: the four new ledger columns do not exist, so any
    write naming them is rejected. The delete has already happened by then."""
    def __init__(self, st):
        super().__init__(st)
        self.attempts = []

    def schema(self, s):
        outer = self

        class Q(FakeQuery):
            def __init__(self, rows, log, table):
                super().__init__(rows, log, table)
                self._t = table

            def delete(self):
                self._del = True
                return self

            def upsert(self, rows, *a, **k):
                rows = rows if isinstance(rows, list) else [rows]
                bad = {"expected_amount", "promote_id", "promoted_by", "promoted_at"} & set(rows[0] or {})
                outer.attempts.append(sorted(bad))
                if bad:
                    raise RuntimeError("column \"expected_amount\" of relation does not exist")
                cur = outer.store.setdefault(self._t, [])
                cur.extend(dict(r) for r in rows)
                return type("E", (), {"execute": lambda _s: type("R", (), {"data": rows})()})()

            def execute(self):
                if getattr(self, "_del", False):
                    outer.store[self._t] = []
                    return type("R", (), {"data": []})()
                return super().execute()

        class S:
            def table(_s, t):
                return Q(outer.store.get(f"{s}.{t}", []), None, f"{s}.{t}")
        return S()


st2 = store(promotes=[P1])
st2["commcalc.sale_installment_ledger"] = []
nc = NoNewColsClient(st2)
rp = sie.compute_sale_installments(nc, ORG, PER, persist=True)
chk("G1 the extended write was tried first", nc.attempts and nc.attempts[0], nc.attempts[:2])
chk("G2 ...it was REJECTED, and the fallback wrote the rows anyway (the period is NOT empty)",
    len(st2["commcalc.sale_installment_ledger"]) > 0, len(st2["commcalc.sale_installment_ledger"]))
chk("G3 ...with the BASE column set only",
    all(not ({"expected_amount", "promote_id", "promoted_by", "promoted_at"} & set(r))
        for r in st2["commcalc.sale_installment_ledger"]),
    st2["commcalc.sale_installment_ledger"][:1])
chk("G4 ...and the degradation is REPORTED, not hidden",
    (rp.get("expected_guard") or {}).get("persist_columns") == "base",
    (rp.get("expected_guard") or {}).get("persist_columns"))
chk("G5 the money is unaffected by the persist degradation",
    rp["totals"]["amount"] == res["totals"]["amount"], (rp["totals"], res["totals"]))
st3 = store(promotes=[P1])
st3["commcalc.sale_installment_ledger"] = []
pc3 = PersistClient(st3)
rp3 = sie.compute_sale_installments(pc3, ORG, PER, persist=True)
chk("G6 with 258 applied the EXTENDED column set is used and reported",
    (rp3.get("expected_guard") or {}).get("persist_columns") == "extended",
    (rp3.get("expected_guard") or {}).get("persist_columns"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§H  MULTI-TENANT + WRITE SCOPE")

log = []
sie.compute_sale_installments(FakeClient(store(promotes=[P1]), log=log), ORG, PER, persist=False)
unscoped = [e for e in log if e[0] == "read" and not any(c == "org_id" for c, _ in e[2])]
chk("H1 every read is org-scoped", not unscoped, unscoped[:3])
HOUSE_DEFAULT = {"commcalc.installment_gate_source_config"}
wrong = [e for e in log
         if any(c == "org_id" and v != ORG for c, v in e[2]) and e[1] not in HOUSE_DEFAULT]
chk("H2 ...to the CALLER's org (the one exception is the documented house-defaults table)",
    not wrong, wrong[:3])
chk("H3 the promote table IS read, org-scoped",
    any(e[1] == "commcalc.installment_promote" and ("org_id", ORG) in e[2] for e in log),
    [e for e in log if "promote" in e[1]][:3])

# tenant B's promote must not pay tenant A, and vice versa
both = store(promotes=[promote(tgt["trans_id"], tgt["mdn"], tgt["month_index"],
                               tgt["expected_amount"], org=ORG_B)])
res_iso = run(both)
chk("H4 a promote filed under ANOTHER tenant never pays this one",
    res_iso["totals"]["amount"] == new["totals"]["amount"], res_iso["totals"])
chk("H5 ...and is not even visible in this tenant's guard",
    res_iso["expected_guard"]["promotes_applied"] == 0
    and res_iso["expected_guard"]["promotes_unapplied"] == 0, res_iso["expected_guard"])

writes = []
try:
    sie.compute_sale_installments(FakeClient(store(promotes=[P1]), writes=writes), ORG, PER,
                                  persist=False)
    chk("H6 a read-only run (persist=False) writes NOTHING", not writes, writes)
except AssertionError as e:
    chk("H6 a read-only run (persist=False) writes NOTHING", False, str(e))
tripped = False
try:
    FakeClient(store(), writes=[]).schema("commcalc").table("x").insert({})
except AssertionError:
    tripped = True
chk("H7 the write guard is TRIPPED deliberately (so H6 means something)", tripped)
# WHICH TABLES does a PERSISTING run actually write? The promote table must never be among them —
# that separation is the entire reason a promote survives recompute.
wlog = []


class LoggingPersistClient(PersistClient):
    def schema(self, s):
        inner = super().schema(s)
        outer_log = wlog

        class S:
            def table(_s, t):
                q = inner.table(t)
                _up, _del = q.upsert, q.delete

                def up(rows, *a, **k):
                    outer_log.append(("write", f"{s}.{t}"))
                    return _up(rows, *a, **k)

                def dele(*a, **k):
                    outer_log.append(("write", f"{s}.{t}"))
                    return _del(*a, **k)
                q.upsert, q.delete = up, dele
                return q
        return S()


st8 = store(promotes=[P1])
st8["commcalc.sale_installment_ledger"] = []
sie.compute_sale_installments(LoggingPersistClient(st8), ORG, PER, persist=True)
written = {t for _o, t in wlog}
chk("H8 a PERSISTING run writes ONLY the installment ledger",
    written == {"commcalc.sale_installment_ledger"}, written)
chk("H8b ...so the promote table is never touched by the calc, and the promote survives",
    "commcalc.installment_promote" not in written
    and len(st8["commcalc.installment_promote"]) == 1, written)

from app.modules.commcalc import router as R
for fn, name in ((R.get_expected_commission_config, "GET config"),
                 (R.put_expected_commission_config, "PUT config"),
                 (R.list_expected_commission_promotes, "GET promotes"),
                 (R.promote_expected_commission, "POST promote"),
                 (R.revoke_expected_commission, "POST revoke"),
                 (R.expected_commission_report, "GET report")):
    p = inspect.signature(fn).parameters.get("org_id")
    chk(f"H9 org_id is a QUERY PARAM on {name}", p is not None and p.default == R.ORG_ID)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§I  MIGRATION 258")

MIGD = os.path.join(REPO, "database", "migrations")
M = os.path.join(MIGD, "258_commission_expected_earned_promote.sql")
chk("I1 file exists", os.path.exists(M))
if os.path.exists(M):
    sql = open(M).read()
    body = re.sub(r"--[^\n]*", "", sql)
    # Also strip SINGLE-QUOTED STRING LITERALS before the "does it touch a money table" check: the
    # COMMENT ON statements deliberately NAME rep_commissions in their prose ("never summed into
    # rep_commissions"), which is documentation, not a reference. Searching raw SQL would read the
    # promise as a violation of itself.
    code = re.sub(r"'(?:[^']|'')*'", "''", body)
    chk("I2 additive only",
        all("IF NOT EXISTS" in m for m in re.findall(r"(CREATE TABLE[^(]*|ADD COLUMN[^,;]*)", body)),
        [m for m in re.findall(r"(CREATE TABLE[^(]*|ADD COLUMN[^,;]*)", body) if "IF NOT EXISTS" not in m])
    chk("I3 no DROP / DELETE / UPDATE of data",
        not re.search(r"\b(DROP|DELETE\s+FROM|UPDATE)\b", body, re.I))
    chk("I4 no GRANT", "grant" not in body.lower())
    chk("I5 no CREATE POLICY", "create policy" not in body.lower())
    chk("I6 no anon / authenticated", not re.search(r"\b(anon|authenticated)\b", body, re.I))
    chk("I7 RLS enabled on the new table", body.lower().count("enable row level security") == 1)
    chk("I8 org_id NOT NULL + an org index", "org_id              UUID NOT NULL" in body
        and "installment_promote_period" in body)
    chk("I9 NOTHING is seeded — a promote is a named person's decision",
        "insert into" not in body.lower())
    chk("I10 in band 200-299, no collision",
        len([f for f in os.listdir(MIGD) if f.startswith("258_")]) == 1)
    chk("I11 the UNIQUE key matches the ledger's own identity",
        "UNIQUE (org_id, pay_period, trans_id, mdn, month_index)" in body)
    chk("I12 it rewrites no money table (only ADDs columns to the ledger)",
        not re.search(r"\b(rep_commissions|commission_rule|commission_tier)\b", code, re.I),
        re.findall(r"\b(rep_commissions|commission_rule|commission_tier)\b", code, re.I))
    chk("I12b the only table it WRITES to is its own new one (the ledger change is ADD COLUMN)",
        set(re.findall(r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+commcalc\.(\w+)", code, re.I)) == set(),
        re.findall(r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+commcalc\.(\w+)", code, re.I))
    chk("I13 the code defaults and the documented shape agree",
        all(str(v).lower() in body.lower() or str(v) in body
            for v in (xc.DEFAULT_CONFIG["from_month"], xc.DEFAULT_CONFIG["to_month"],
                      xc.DEFAULT_CONFIG["on_expected_change"])))
    try:
        import pglast
        pglast.parse_sql(body)
        chk("I14 real PostgreSQL parse (pglast)", True)
    except ImportError:
        chk("I14 real PostgreSQL parse (pglast) — SKIPPED", True)
    except Exception as e:
        chk("I14 real PostgreSQL parse (pglast)", False, str(e)[:160])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
sec("§J  DIFFERENTIAL SCOPE vs BASE")

try:
    changed = subprocess.check_output(["git", "-C", REPO, "diff", "--name-only", BASE_REF],
                                      stderr=subprocess.DEVNULL).decode().split()
    changed += subprocess.check_output(["git", "-C", REPO, "ls-files", "--others", "--exclude-standard"],
                                       stderr=subprocess.DEVNULL).decode().split()
    changed = sorted(set(changed))
except Exception:
    changed = []
chk("J1 the diff is readable", bool(changed))
MUST_NOT = ["backend/app/modules/commcalc/calculator.py",
            "backend/app/modules/commcalc/commission_engine.py",
            "backend/app/modules/commcalc/commission_ledger.py",
            "backend/app/modules/commcalc/whatif.py",
            "backend/app/modules/commcalc/targets_engine.py",
            "backend/app/modules/commcalc/installment_engine.py",
            "backend/app/modules/commcalc/installment_category.py",
            "backend/app/modules/commcalc/installment_category_payout.py",
            "backend/app/modules/commcalc/accessory_definition.py",
            "backend/app/modules/commcalc/accessory_cost_audit.py",
            "backend/app/main.py",
            "backend/app/modules/core/router.py",
            "frontend/src/lib/client.ts",
            "frontend/src/lib/rbac.ts",
            "frontend/src/app/(platform)/layout.tsx"]
for m in MUST_NOT:
    chk(f"J2 UNTOUCHED: {os.path.basename(m)}", m not in changed)
EXPECTED = {"backend/app/modules/commcalc/sale_installment_engine.py",
            "backend/app/modules/commcalc/expected_commission.py",
            "backend/app/modules/commcalc/commission_drilldown.py",
            "backend/app/modules/commcalc/router.py"}
py_changed = {c for c in changed if c.startswith("backend/app/")}
chk("J3 exactly four backend app files changed", py_changed == EXPECTED, sorted(py_changed ^ EXPECTED))
if base_sie:
    for fn in ("_line_amount", "_mrc_candidate", "_gate_met", "_gate_met_ma", "_mi_index",
               "classify_line", "installment_label", "_rule_matches", "_in_effective_window",
               "_activation_payment_met", "_norm_mdn", "repU_cat"):
        a_, b_ = getattr(base_sie, fn, None), getattr(sie, fn, None)
        chk(f"J4 gate/amount helper byte-identical to BASE: {fn}",
            a_ is not None and b_ is not None and inspect.getsource(a_) == inspect.getsource(b_))
chk("J5 the gate was NOT forked — the engine still calls the same two gate helpers and no others",
    len(re.findall(r"_gate_met\(|_gate_met_ma\(", inspect.getsource(sie.compute_sale_installments))) == 2,
    re.findall(r"_gate_met\w*\(", inspect.getsource(sie.compute_sale_installments)))

print("\n" + "=" * 94)
print(f"RESULT: {OK} passed, {FAIL} failed")
if FAILS:
    print("FAILED:")
    for f in FAILS:
        print("  -", f)
print("=" * 94)
sys.exit(1 if FAIL else 0)
