"""PROOF — Executive-MTD pay prices TABLET and WATCH activations at their own rate, per EVENT, and an org that
configures no device words pays exactly what it paid before (owner 2026-09-28, index §6n).

Owner, verbatim: *"need to add tablets and watches as a fix and a different commission for those, tablet pay at
$5 and watch at $2, gizmo at $2"* — Gizmo "treat all as watch". The rate card: new phone $10 (port / BYOD
too), phone upgrade $5, tablet $5 (new or upgrade), watch / connected device $2 (new or upgrade).

DB-FREE: the REAL Exec-MTD pay path — `router._commission_mtd_result` → `_exec_mtd` → `_sales_rows_union` /
`_sales_cell_agg` / `_apply_activation_basis` → `_commission_from_mtd_rows` — over an in-memory client serving
the fixture tables. The org's config is the f4f1c16e SHAPE (category / product_desc fields, `count_unit:
'event'`), with and without the device words.

  A. the device dimension (line_class): house = none; enabled → THE one device classifier (the multi-month
     ladder, `installment_category`: tenant rules ahead of the built-ins) over each event's lines; applies_to;
  B. the RATE CARD over fixture events: 10 x phone new (+ port + BYOD) + 5 x phone upgrade + 5 x tablet +
     2 x watch — per EVENT (a tablet invoice's three lines are one event), one category per event;
  C. PRECEDENCE: a mixed invoice (a phone and a tablet on two lines) pays both, once each; an event naming
     both devices takes the classifier's priority ladder (a tenant rule's priority decides); `applies_to` without 'upgrade' pays a device upgrade at
     the upgrade rate; a device hardware line alone is no event;
  D. THE SALES BASIS SPLITS TABLETS (there is no Activation-Details file in the fixture) and a 'folded' plan
     keeps them folded; Total Activation is unchanged by the split;
  E. THE COMPATIBILITY PIN: no device words → Tablet = Watch = 0, every other count and every $ equal to the
     pre-change formula, for the HOUSE rules and for f4f1c16e's CURRENT config; plus an A/B against the base
     branch's router when git can read it;
  F. THE NAME BRIDGE on this basis: an employee-scope assignment under the ROSTER spelling pays the POS spelling.

    python3 backend/harness_exec_mtd_device_rates.py
"""
import copy
import os
import subprocess
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for _k in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "DATABASE_URL"):
    os.environ.pop(_k, None)

from app.modules.commcalc import line_class as LC                      # noqa: E402
from app.modules.commcalc import activation_bucketing as AB            # noqa: E402
from app.modules.commcalc import router as R                           # noqa: E402

ORG = "00000000-0000-0000-0000-0000000d3v01"
PERIOD = "July 2026"
P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:400]))


def section(t):
    print("\n── %s %s" % (t, "─" * max(0, 96 - len(t))))


# ── the in-memory client (reads only) ────────────────────────────────────────────────────────────
class Q:
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, *a, **k):
        return self

    def eq(self, c, v):
        self._rows = [r for r in self._rows if str(r.get(c)) == str(v)]
        return self

    def in_(self, c, vals):
        vs = {str(v) for v in vals}
        self._rows = [r for r in self._rows if str(r.get(c)) in vs]
        return self

    def _noop(self, *a, **k):
        return self
    neq = is_ = gte = lte = gt = lt = ilike = like = order = or_ = not_ = limit = contains = filter = _noop

    def range(self, a, b):
        self._rows = self._rows[a:b + 1]
        return self

    def execute(self):
        return type("Res", (), {"data": copy.deepcopy(self._rows), "count": len(self._rows)})()


class Client:
    def __init__(self, tables):
        self.t = tables

    def schema(self, n):
        t = self.t

        class S:
            def table(self_, name):
                return Q(t.get(n + "." + name, []))

            def rpc(self_, *a, **k):
                return type("X", (), {"execute": lambda s=None: type("R", (), {"data": []})()})()
        return S()

    def table(self, name):
        return Q(self.t.get("public." + name, []))

    def rpc(self, *a, **k):
        return type("X", (), {"execute": lambda s=None: type("R", (), {"data": []})()})()


# ── the fixture: one store, rep "Rep A" (plus "Rep B" for the name bridge), July 2026 ───────────────
def L(tid, product, category, serial="", rep="Rep A", ext=0.0):
    return {"org_id": ORG, "period": PERIOD, "trans_id": tid, "trans_date": "2026-07-02", "store": "Store 1",
            "salesperson": rep, "user_login": rep, "department": "Activations",
            "category": ">> Activations (Price Sheet) >> Carrier >> " + category, "product_desc": product,
            "serial_1": serial, "mdn": "", "ext_price": ext, "gp": ext, "voided": "No", "trans_type": None,
            "contract_type": "", "quantity": 1.0}


HW = ">> Cellular Equipment >> Pull Through >> "
SALES = [
    # T1 — one phone activation, two lines on one phone line → ONE event
    L("T1", "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", "7185550001", ext=150),
    L("T1", "32066 My Biz Plan - New Act", "Additional Spiffs", "7185550001", ext=45),
    # T2 — two phone lines on one invoice → TWO events
    L("T2", "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", "7185550002"),
    L("T2", "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", "7185550003"),
    # T3 — one phone upgrade
    L("T3", "DPA Upgrade iPhone (Rate Plan Rebate)", "Rate Plan Rebates", "7185550004"),
    # T4 — ONE tablet activation carried on three lines + the tablet hardware (device serial, not activation-type)
    L("T4", "DPA New Act Tablet (Rate Plan Rebate)", "Rate Plan Rebates", "7185550005"),
    L("T4", "ADD A LINE TABLET KICKER", "Kickers", "7185550005"),
    L("T4", "Tablet Rate Plan (DPA)", "Rate Plans", "7185550005"),
    L("T4", "TABLET 11IN 128GB", HW + "Tablets", "354198260632397", ext=399),
    # T5 — a tablet UPGRADE
    L("T5", "DPA Upgrade Tablet (Rate Plan Rebate)", "Rate Plan Rebates", "7185550006"),
    # T6 — a watch / connected-device activation (two lines, one line number)
    L("T6", "DPA New Act Connected Devices (Rate Plan Rebate)", "Rate Plan Rebates", "7185550007"),
    L("T6", "CONNECTED DEVICE ADDITIONAL COMPENSATION", "Kickers", "7185550007"),
    # T7 — a watch UPGRADE
    L("T7", "DPA Upgrade Connected Devices (Rate Plan Rebate)", "Rate Plan Rebates", "7185550008"),
    # T8 — a MIXED invoice: a phone line AND a tablet line → one phone event + one tablet event
    L("T8", "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", "7185550009"),
    L("T8", "DPA New Act Tablet (Rate Plan Rebate)", "Rate Plan Rebates", "7185550010"),
    # T9 — BYOD phone; T10 — port-in phone
    L("T9", "Customer Owned Device (START PAW)", "Customer Provided Device", "7185550011"),
    L("T10", "DPA New Act iPhone Port In", "Rate Plan Rebates", "7185550012"),
    # T11 — watch HARDWARE alone (no activation-type line) → no event
    L("T11", "SMART WATCH 42MM", HW + "Connected Devices >> Wearables", "354198260632399", ext=249),
    # Rep B (the name-bridge case): one phone activation
    L("T12", "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", "7185550013", rep="Rep B Longname"),
]
CURRENT_RULES = {"event": {"count_unit": "event"}, "fields": ["category", "product_desc"],
                 "tokens": {"byod": ["customer provided", "byod", "customer owned"], "port": ["port"],
                            "upgrade": ["upgrade"], "activation": ["new activation", "add a line", "new act", "prepaid"],
                            "hardware_only": ["hardware only", "prepaid"]}}
# the device dimension ON; tablets come from the classifier's BUILT-IN ladder, watches from the TENANT's rule rows
DEVICES = {"enabled": True}
WATCH_RULES = [{"org_id": ORG, "category_key": "watch", "match_field": "product_desc", "match_op": "contains",
                "match_value": "connected device", "priority": 25, "is_active": True},
               {"org_id": ORG, "category_key": "watch", "match_field": "category", "match_op": "contains",
                "match_value": "wearables", "priority": 25, "is_active": True}]
CARD = {"activation": 10, "port": 10, "byod": 10, "upgrade": 5, "tablet": 5, "watch": 2,
        "home_internet": 0, "edge": 0}


def tables(adr, cat_rules=None):
    return {
        "commcalc.accessory_config": [{"org_id": ORG, "activation_details_rules": adr, "contract_type_map": {},
                                       "activation_rules": []}],
        "commcalc.raw_sales": copy.deepcopy(SALES),
        "commcalc.rep_aliases": [{"org_id": ORG, "alias": "Rep B Longname", "canonical": "RepB"}],
        "commcalc.installment_category_rule": copy.deepcopy(WATCH_RULES if cat_rules is None else cat_rules),
    }


def rules_of(adr, cat_rules=None):
    """The org's resolved line_class rules exactly as the loader builds them (router._line_rules_resolve)."""
    c = Client(tables(adr, cat_rules))
    return R._line_rules_resolve(c, ORG, adr, {})


def plan(rates=None, assignments=None, basis_policy=None):
    mr = dict(CARD if rates is None else rates, accessory_pct=0)
    if basis_policy:
        mr["activation_basis"] = basis_policy
    return {"id": "P1", "name": "Flat", "carrier_id": None, "rules": [], "mtd_rates": mr,
            "assignments": assignments if assignments is not None else
            [{"scope": "employee", "scope_value": "Rep A"}, {"scope": "employee", "scope_value": "RepB"}]}


def pay(adr, p=None, router=R, cat_rules=None):
    c = Client(tables(adr, cat_rules))
    router.sb = lambda: c
    router._invalidate_accessory_config(ORG)
    res = router._commission_mtd_result(c, ORG, PERIOD, p or plan(), today="2026-08-15")
    return {r["employee"]: r for r in res["by_rep"]}, res


def cats(row):
    return {k: v["count"] for k, v in (row.get("by_category") or {}).items()}


# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("A. the device predicate — one home, per-org words")
house = LC.resolve_rules(None)
check("A1 house rules: no device dimension", house["devices"]["configured"] is False
      and LC.device_of({"product_desc": "tablet"}, house) is None)
check("A1b the org's CURRENT config (devices not enabled): no rules loaded, no dimension",
      rules_of(CURRENT_RULES)["devices"]["configured"] is False)
r_dev = rules_of(dict(CURRENT_RULES, devices=DEVICES))
check("A2 enabled: the classifier's rules are the multi-month ladder (tenant watch rows + the built-ins), "
      "applying to every activation class",
      r_dev["devices"]["configured"] and any(x.get("category_key") == "watch" and x.get("source") == "tenant"
                                             for x in r_dev["devices"]["rules"])
      and any(x.get("category_key") == "tablet" and x.get("source") == "builtin" for x in r_dev["devices"]["rules"])
      and r_dev["devices"]["applies_to"] == list(LC.ACTIVATION_TYPE_CLASSES))
check("A3 device_of: a tablet line / a connected-device line / a phone line",
      (LC.device_of(SALES[5], r_dev), LC.device_of(SALES[10], r_dev), LC.device_of(SALES[0], r_dev))
      == ("tablet", "watch", None))
check("A4 pay_category: device over its class; no device → the class",
      (LC.pay_category("upgrade", "tablet", r_dev), LC.pay_category("byod", "watch", r_dev),
       LC.pay_category("activation", None, r_dev)) == ("tablet", "watch", "activation"))
ev = LC.activation_events(SALES, r_dev)
by_tid = {}
for e in ev["events"]:
    by_tid.setdefault(e["trans_id"], []).append((e["cls"], e.get("device"), e.get("pay_category")))
check("A5 events carry their device: T4 = one tablet event (3 lines), T6 = one watch event, T8 = phone + tablet",
      by_tid["T4"] == [("activation", "tablet", "tablet")] and by_tid["T6"] == [("activation", "watch", "watch")]
      and sorted(x[1] or "phone" for x in by_tid["T8"]) == ["phone", "tablet"], by_tid)
check("A6 house events carry NO device key (byte-identical event dicts)",
      not any("device" in e for e in LC.activation_events(SALES, LC.resolve_rules(CURRENT_RULES))["events"]))
check("A7 the category list is ONE list, with tablet and watch", AB.MTD_CATEGORIES == R._MTD_ACT_CATEGORIES
      and {"tablet", "watch"} <= set(AB.MTD_CATEGORIES) and R.commission_mtd_categories()["categories"][0]["key"] == "activation")

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE RATE CARD, per event — 10 / 5 / 5 / 2")
rows, res = pay(dict(CURRENT_RULES, devices=DEVICES))
a = rows["Rep A"]
want = {"activation": 4, "port": 1, "byod": 1, "tablet": 3, "watch": 2, "upgrade": 1, "home_internet": 0, "edge": 0}
check("B1 Rep A's events by category: 4 phone new, 1 port, 1 BYOD, 1 phone upgrade, 3 tablet, 2 watch",
      cats(a) == want, cats(a))
check("B2 pay == 10x4 + 10x1 + 10x1 + 5x1 + 5x3 + 2x2 = $84.00 (per event, one category each)",
      a["activation_pay"] == 84.0 and a["commission"] == 84.0, (a["activation_pay"], a["commission"]))
check("B3 the tablet's three lines paid ONCE ($5), the tablet upgrade $5, the watch and the watch upgrade $2 each",
      a["by_category"]["tablet"]["pay"] == 15.0 and a["by_category"]["watch"]["pay"] == 4.0)
check("B4 the preview names Tablet and Watch as categories (the editor's list)",
      [c["key"] for c in res["categories"]] == list(AB.MTD_CATEGORIES))

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("C. precedence — one event, one category, one rate")
both = [L("TX", "DPA New Act Tablet Connected Devices", "Rate Plan Rebates", "7185559999")]
r_w = rules_of(dict(CURRENT_RULES, devices=DEVICES), [dict(x, priority=15) for x in WATCH_RULES])
check("C1 an event naming both devices takes the classifier's priority ladder: tablet (built-in 20) over a "
      "watch rule at 25; the watch when the tenant ranks it 15",
      LC.activation_events(both, r_dev)["events"][0]["device"] == "tablet"
      and LC.activation_events(both, r_w)["events"][0]["device"] == "watch")
rows_nu, _ = pay(dict(CURRENT_RULES, devices=dict(DEVICES, applies_to=["activation", "port", "byod"])))
check("C2 applies_to without 'upgrade': a tablet / watch UPGRADE pays the upgrade rate (3 upgrades x $5)",
      cats(rows_nu["Rep A"])["upgrade"] == 3 and cats(rows_nu["Rep A"])["tablet"] == 2
      and cats(rows_nu["Rep A"])["watch"] == 1 and rows_nu["Rep A"]["commission"] == 40 + 10 + 10 + 15 + 10 + 2,
      (cats(rows_nu["Rep A"]), rows_nu["Rep A"]["commission"]))
check("C3 the watch HARDWARE line alone (T11) is no event", "T11" not in by_tid)
check("C4 the mixed invoice pays its phone ($10) AND its tablet ($5) — never the tablet as a phone",
      sorted((x[2] or x[0]) for x in by_tid["T8"]) == ["activation", "tablet"])

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("D. the SALES basis splits tablets; Total Activation unchanged; 'folded' keeps them folded")


def exec_rows(adr):
    c = Client(tables(adr))
    R.sb = lambda: c
    R._invalidate_accessory_config(ORG)
    return {r["employee"]: r for r in (R._exec_mtd(c, ORG, PERIOD, today=__import__("datetime").date(2026, 8, 15))
                                       .get("by_employee") or {}).get("rows") or []}


emp = exec_rows(dict(CURRENT_RULES, devices=DEVICES))
emp0 = exec_rows(CURRENT_RULES)
check("D1 no Activation-Details file, yet Exec MTD shows Tablet 3 and Watch 2 for Rep A",
      (emp["Rep A"]["tablet"], emp["Rep A"]["watch"]) == (3, 2), emp["Rep A"])
check("D2 Total Activation is the SAME with and without the device split (units moved, never added)",
      emp["Rep A"]["total_activation"] == emp0["Rep A"]["total_activation"],
      (emp["Rep A"]["total_activation"], emp0["Rep A"]["total_activation"]))
rows_f, _ = pay(dict(CURRENT_RULES, devices=DEVICES), plan(basis_policy="folded"))
check("D3 a plan stating the 'folded' basis keeps devices inside their classes (Tablet = Watch = 0)",
      (cats(rows_f["Rep A"])["tablet"], cats(rows_f["Rep A"])["watch"]) == (0, 0))

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("E. THE COMPATIBILITY PIN — no device words → byte-identical pay")
rows0, _ = pay(CURRENT_RULES)
a0 = rows0["Rep A"]
want0 = {"activation": 7, "port": 1, "byod": 1, "tablet": 0, "watch": 0, "upgrade": 3, "home_internet": 0, "edge": 0}
check("E1 f4f1c16e's CURRENT config: tablets and watches stay phone activations / upgrades (as today)",
      cats(a0) == want0, cats(a0))
check("E2 ...and pay is today's formula: 10 x 7 + 10 + 10 + 5 x 3 = $105.00", a0["commission"] == 105.0, a0["commission"])
rows_h, _ = pay(None)
check("E3 the HOUSE rules (no org row): Tablet = Watch = 0 for every rep",
      all(cats(r)["tablet"] == 0 and cats(r)["watch"] == 0 for r in rows_h.values()), {k: cats(v) for k, v in rows_h.items()})


def _base_router():
    """The base branch's router (origin/main), loaded as its own module — None when git cannot read it."""
    try:
        base = subprocess.run(["git", "merge-base", "HEAD", "origin/main"], capture_output=True, text=True,
                              cwd=os.path.dirname(os.path.abspath(__file__)), timeout=30).stdout.strip()
        if not base:
            return None
        src = subprocess.run(["git", "show", f"{base}:backend/app/modules/commcalc/router.py"], capture_output=True,
                             text=True, cwd=os.path.dirname(os.path.abspath(__file__)), timeout=60).stdout
        if "def _commission_mtd_result" not in src:
            return None
        mod = types.ModuleType("router_base")
        mod.__file__ = R.__file__
        exec(compile(src, "router_base.py", "exec"), mod.__dict__)
        return mod
    except Exception:
        return None


BASE = _base_router()
if BASE is None:
    print("  SKIP  E4/E5 A/B against the base router (git cannot read origin/main here) — E1-E3 still pin it")
else:
    for label, adr in (("f4f1c16e CURRENT config", CURRENT_RULES), ("HOUSE rules", None)):
        new_rows, _ = pay(adr, plan(assignments=[{"scope": "employee", "scope_value": "Rep A"}]))
        old_rows, _ = pay(adr, plan(assignments=[{"scope": "employee", "scope_value": "Rep A"}]), router=BASE)
        R.sb = lambda: None
        check(f"E4 A/B vs the base router, {label}: every rep's commission identical to the cent",
              {k: v["commission"] for k, v in new_rows.items()} == {k: v["commission"] for k, v in old_rows.items()},
              ({k: v["commission"] for k, v in new_rows.items()}, {k: v["commission"] for k, v in old_rows.items()}))

# ═════════════════════════════════════════════════════════════════════════════════════════════════
section("F. the name bridge reaches the Exec-MTD basis")
check("F1 'RepB' (roster spelling) is assigned; the POS spelling 'Rep B Longname' is paid ($10 new phone)",
      "Rep B Longname" in rows and rows["Rep B Longname"]["commission"] == 10.0, sorted(rows))
rows_nb, _ = pay(dict(CURRENT_RULES, devices=DEVICES), plan(assignments=[{"scope": "employee", "scope_value": "Rep A"}]))
check("F2 ...and a rep who is not assigned (directly or through an alias) is not paid", "Rep B Longname" not in rows_nb)

print("\n%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
