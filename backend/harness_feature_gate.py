"""PROOF — the per-tenant feature gate: the truth table, the degrade direction, and the three
mechanisms reading it instead of each keeping its own answer. DB-FREE (in-memory doubles only).

OWNER (2026-11-10): *"why is vidapay connector showing in boost ? … have an agent asses and create a
dashboard for the super admin whereby these things can be gated out for the respective tenants right
from the get go"*.

WHAT THIS PROVES, in the order a reader needs it:
  A  the truth table — all four rows, and that a grant beats a posture in BOTH directions
  B  the degrade direction — an unknown / absent / unapplied posture answers as the platform did
     before this module existed, never by refusing
  C  module entitlement dereferences it (`core/entitlements.effective_modules`)
  D  the connector registry dereferences it — the OWNER'S CASE, both as it is today and with the
     tenant gated, on each tenant's own declaration
  E  report kinds dereference it through the SAME predicate, so there is no second answer
  F  EVERY TENANT (CLAUDE.md): each tenant's own declaration produces its own answer, and one
     tenant's grant never reaches another's read
  G  the grid + its summary are folded from the rows, and a new tenant's starter grants are rows

    python3 backend/harness_feature_gate.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from app.modules.core import feature_gate as FG          # noqa: E402
from app.modules.commcalc import report_kinds as RK      # noqa: E402
from app.modules.commcalc import connector_registry as CR  # noqa: E402

P = F = 0
HOUSE = FG and "00000000-0000-0000-0000-000000000001"
# The two live tenants this was measured against on 2026-11-10, by org id only (RULE TWO: the names
# are in the PR and the index, never in a code path).
ORG_HOUSE = HOUSE                                   # declares carrier 'boost'
ORG_OTHER = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"  # declares carrier 'total'


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {name}")
    else:
        F += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def ctl(name, cond, detail=""):
    check("CONTROL " + name, cond, detail)


def raises(fn, *a, **k):
    try:
        fn(*a, **k)
    except Exception:
        return True
    return False


# ── the in-memory double: only the tables these readers touch ────────────────────────────────────
class _Q:
    def __init__(self, rows):
        self._rows = [dict(r) for r in rows]

    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col)) == str(val)]
        return self

    def in_(self, col, vals):
        vs = {str(v) for v in vals}
        self._rows = [r for r in self._rows if str(r.get(col)) in vs]
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _Schema:
    def __init__(self, tables, schema):
        self.t, self.s = tables, schema

    def table(self, name):
        return _Q(self.t.get((self.s, name), []))

    def rpc(self, *a, **k):
        raise RuntimeError("no rpc in the double")


class Fake:
    def __init__(self, tables):
        self.t = tables

    def schema(self, s):
        return _Schema(self.t, s)

    def table(self, name):
        return _Q(self.t.get(("public", name), []))


def tenants(posture_by_org):
    return [{"org_id": o, "name": o[:8], FG.COLUMN: p} for o, p in posture_by_org.items()]


print("\nA. the truth table — one table, four rows")
check("A1 no grant + gated posture = OFF, and says it was never granted",
      FG.decide(grant=None, posture=FG.POSTURE_GATED) ==
      {"allowed": False, "basis": FG.BASIS_GATED_DEFAULT, "decided": False})
check("A2 no grant + inherit posture = whatever the mechanism already answered",
      FG.allowed(grant=None, posture=FG.POSTURE_INHERIT, by_rule=True) is True and
      FG.allowed(grant=None, posture=FG.POSTURE_INHERIT, by_rule=False) is False)
check("A3 a GRANT opens a gated tenant, and is recorded as a human's decision",
      FG.decide(grant=True, posture=FG.POSTURE_GATED) ==
      {"allowed": True, "basis": FG.BASIS_GRANT, "decided": True})
check("A4 a DENY shuts a thing the declaration would have shown — a gate a rule cannot re-open",
      FG.decide(grant=False, posture=FG.POSTURE_INHERIT, by_rule=True) ==
      {"allowed": False, "basis": FG.BASIS_DENY, "decided": True})
check("A5 an inbound value reads as the same three states",
      (FG.grant_state("show"), FG.grant_state("hide"), FG.grant_state(None),
       FG.grant_state(True), FG.grant_state(False), FG.grant_state("whatever")) ==
      (True, False, None, True, False, None))
check("A6 a STORED label is read STRICTLY — only the two words this module writes are a decision, "
      "because the grant home is shared with another writer",
      (FG.stored_grant("show"), FG.stored_grant("hide"), FG.stored_grant("yes"),
       FG.stored_grant("on"), FG.stored_grant("1"), FG.stored_grant(True),
       FG.stored_grant(None)) == (True, False, None, None, None, None, None))
ctl("A can go red", FG.allowed(grant=None, posture=FG.POSTURE_GATED) is False)

print("\nB. the degrade direction — a gate must never start refusing on its own")
for bad in (None, "", "   ", "GATED-ish", "all_access", 0, [], "inherit "):
    check(f"B1 posture({bad!r}) is inherit, not a refusal", FG.posture(bad) == FG.POSTURE_INHERIT)
check("B2 'gated' is recognised however it is cased or padded",
      FG.gated(" GATED ") and FG.gated("gated") and not FG.gated("inherit"))
check("B3 an unreachable tenants table degrades to inherit",
      FG.load_posture(Fake({}), ORG_HOUSE) == FG.POSTURE_INHERIT)
check("B4 a tenant with no row degrades to inherit",
      FG.load_posture(Fake({("storeops", "tenants"): tenants({ORG_OTHER: "gated"})}), ORG_HOUSE)
      == FG.POSTURE_INHERIT)
check("B5 save refuses a value the vocabulary does not name rather than storing inherit quietly",
      raises(FG.save_posture, Fake({}), ORG_HOUSE, "off"))

print("\nC. module entitlement dereferences the gate (core/entitlements)")
from app.modules.core import entitlements as EN  # noqa: E402

CAT = [{"key": k, "label": v, "sort_order": i} for i, (k, v) in enumerate(EN.MODULE_CATALOG.items())]


def _mod_tables(posture, grants, org=None):
    org = org or ORG_HOUSE
    return {
        ("core", "module_catalog"): CAT,
        ("storeops", "tenants"): tenants({ORG_HOUSE: posture, ORG_OTHER: "inherit"}),
        ("storeops", "billing_plan"): [],
        # the ONE grant home — ui_label_override scope 'cap', namespace 'module:'
        ("commcalc", "ui_label_override"): [
            {"org_id": org, "scope": FG.CAP_SCOPE, "key": FG.CAP_PREFIX[FG.KIND_MODULE] + k,
             "label": FG.GRANT_ON if v else FG.GRANT_OFF} for k, v in grants.items()],
    }


inherit_mods = EN.effective_modules(Fake(_mod_tables("inherit", {})), ORG_HOUSE)
check("C1 an INHERIT tenant is all-access exactly as before this module existed",
      inherit_mods == set(EN.MODULE_CATALOG.keys()), str(sorted(inherit_mods))[:120])
gated_none = EN.effective_modules(Fake(_mod_tables("gated", {})), ORG_HOUSE)
check("C2 a GATED tenant with nothing granted has NO module — 'gated out from the get go'",
      gated_none == set(), str(sorted(gated_none)))
gated_two = EN.effective_modules(Fake(_mod_tables("gated", {"closing": True, "hr": True,
                                                            "commissions": False})), ORG_HOUSE)
check("C3 a GATED tenant has exactly what was granted, and a denial is not a grant",
      gated_two == {"closing", "hr"}, str(sorted(gated_two)))
check("C4 a DENY on an inherit tenant removes that module from an otherwise all-access plan",
      "closing" not in EN.effective_modules(
          Fake(_mod_tables("inherit", {"closing": False})), ORG_HOUSE))
check("C5 another tenant's grant row never reaches this tenant's entitlement",
      EN.effective_modules(Fake(_mod_tables("gated", {"closing": True}, org=ORG_OTHER)), ORG_HOUSE)
      == set())
check("C6 the grant home is the override table, not the derived mirror sync_tenant writes",
      "tenant_modules" not in FG.GRANT_HOME[FG.KIND_MODULE] and
      "ui_label_override" in FG.GRANT_HOME[FG.KIND_MODULE])
ctl("C can go red", gated_none != inherit_mods)

print("\nD. the connector registry dereferences it — THE OWNER'S CASE")
DECL_HOUSE = {"pos": ["b2bsoft"], "carriers": ["boost"], "vertical": None}
DECL_OTHER = {"pos": ["b2bsoft"], "carriers": ["total"], "vertical": None}
rows = CR.house_mirror()
vis_keys = lambda d, posture=FG.POSTURE_INHERIT, caps=None: [  # noqa: E731
    r["key"] for r in CR.visible(rows, d, caps, ORG_HOUSE, posture=posture)]
VIDAPAY = next(r["key"] for r in rows if "total" in (r.get("applies_to_carrier") or []))
EPAY = [r["key"] for r in rows if "boost" in (r.get("applies_to_carrier") or [])]
check("D1 the vendor-specific connector of the OTHER carrier is already withheld today",
      VIDAPAY not in vis_keys(DECL_HOUSE) and VIDAPAY in vis_keys(DECL_OTHER))
check("D2 the connectors of the tenant's OWN carrier are shown today",
      all(k in vis_keys(DECL_HOUSE) for k in EPAY))
check("D3 a GATED tenant is offered NO connector until one is granted",
      vis_keys(DECL_HOUSE, FG.POSTURE_GATED) == [], str(vis_keys(DECL_HOUSE, FG.POSTURE_GATED)))
check("D4 a granted connector reaches a gated tenant, and only that one",
      vis_keys(DECL_HOUSE, FG.POSTURE_GATED, {CR.CAP_PREFIX + EPAY[0]: True}) == [EPAY[0]])
sc = CR.scope(rows, DECL_HOUSE, VIDAPAY, None, ORG_HOUSE)
check("D5 a single-connector surface is told it does not apply, and on which axis",
      sc["applies"] is False and sc["miss"] == "carrier")
sc_g = CR.scope(rows, DECL_HOUSE, EPAY[0], None, ORG_HOUSE, posture=FG.POSTURE_GATED)
check("D6 under a gated posture the same surface is told WHY in the gate's own words",
      sc_g["applies"] is False and FG.BASIS_GATED_DEFAULT in (sc_g["why"] or ""))
check("D7 an UNREGISTERED slug is still never withheld — a tenant's own connector is its own",
      CR.scope(rows, DECL_HOUSE, "a-slug-nobody-registered", None, ORG_HOUSE,
               posture=FG.POSTURE_GATED)["applies"] is True)
ctl("D can go red", vis_keys(DECL_HOUSE) != vis_keys(DECL_HOUSE, FG.POSTURE_GATED))

print("\nE. report kinds answer through the SAME predicate, not a second one")
krows = RK.house_mirror()
kv = lambda d, posture=FG.POSTURE_INHERIT, caps=None: [  # noqa: E731
    r["key"] for r in RK.visible_kinds(krows, d, caps, ORG_HOUSE, None, posture=posture)]
check("E1 inherit is byte-identical to the answer before the posture existed",
      kv(DECL_HOUSE) == [r["key"] for r in RK.visible_kinds(krows, DECL_HOUSE, None, ORG_HOUSE)])
check("E2 a gated tenant is offered no report kind until one is granted",
      kv(DECL_HOUSE, FG.POSTURE_GATED) == [])
one = kv(DECL_HOUSE)[0]
check("E3 a granted kind reaches a gated tenant",
      kv(DECL_HOUSE, FG.POSTURE_GATED, {RK.CAP_PREFIX + one: True}) == [one])
check("E4 the hidden list says WHY in the gate's words, so a surface need not guess",
      any(FG.BASIS_GATED_DEFAULT in h["why"] for h in
          RK.hidden_kinds(krows, DECL_HOUSE, None, posture=FG.POSTURE_GATED)))
ctl("E can go red", kv(DECL_HOUSE) != kv(DECL_HOUSE, FG.POSTURE_GATED))

print("\nF. EVERY TENANT — each tenant's own declaration, and no leakage between them")
check("F1 each tenant's declaration produces ITS answer, not the house org's",
      VIDAPAY in vis_keys(DECL_OTHER) and VIDAPAY not in vis_keys(DECL_HOUSE))
check("F2 a grant is keyed on the cap rows of ONE org, so it cannot widen another's read",
      CR.visible(rows, DECL_HOUSE, {CR.CAP_PREFIX + VIDAPAY: True}, ORG_HOUSE) and
      VIDAPAY not in vis_keys(DECL_HOUSE))
gated_why = {h["key"]: h["why"] for h in CR.hidden(rows, DECL_OTHER, None, posture=FG.POSTURE_GATED)}
would_apply = [r["key"] for r in CR.visible(rows, DECL_OTHER, None, ORG_OTHER)]
check("F3 a gated tenant's zero is reported as GATED, never as 'nothing declared'",
      would_apply and all(gated_why.get(k) == FG.BASIS_GATED_DEFAULT for k in would_apply),
      str({k: gated_why.get(k) for k in would_apply})[:200])
check("F3b a connector the DECLARATION also rules out still says so — both reasons are true and the "
      "declaration one is the actionable one",
      any(k not in would_apply and FG.BASIS_GATED_DEFAULT not in (v or "")
          for k, v in gated_why.items()))
check("F4 two tenants' postures are read independently",
      FG.load_posture(Fake({("storeops", "tenants"): tenants(
          {ORG_HOUSE: "inherit", ORG_OTHER: "gated"})}), ORG_OTHER) == "gated")

print("\nG. the grid, its fold, and a new tenant's starter grants")
grid = [FG.row(FG.KIND_CONNECTOR, r["key"], r["label"], grant=None,
               posture=FG.POSTURE_GATED, by_rule=True) for r in rows]
s = FG.summary(grid)
check("G1 the summary is folded from the rows and counts nothing twice",
      s["total"] == len(rows) and s["on"] == 0 and s["off"] == len(rows) and s["decided"] == 0)
check("G2 every row says where its grant lives, so a toggle writes the registry's own row",
      all(r["grant_home"] == FG.GRANT_HOME[FG.KIND_CONNECTOR] for r in grid))
st = FG.starter_grants(FG.KIND_MODULE, list(EN.MODULE_CATALOG.keys()))
check("G3 a new gated tenant is provisioned with GRANT ROWS, so it can be set up",
      st and all(v is True for v in st.values()) and set(st) <= set(EN.MODULE_CATALOG))
check("G4 nothing else is pre-decided — absence means nobody has decided yet",
      len(st) < len(EN.MODULE_CATALOG))
check("G5 the starter policy is a module policy only, never a connector or a report kind",
      FG.starter_grants(FG.KIND_CONNECTOR, ["epay"]) == {} and
      FG.starter_grants(FG.KIND_REPORT_KIND, ["x"]) == {})

print("\nH. WHO MAY WIDEN — the escalation the security pass found")
check("H1 widening ANY gate namespace is the platform's call",
      all(FG.widening_is_platform_only("cap", p + "whatever", FG.GRANT_ON)
          for p in FG.PLATFORM_WIDEN_PREFIXES))
check("H2 the gate's own three kinds are in that list — a grant namespace missing from it is a "
      "tenant granting itself what its configuration withheld",
      all(FG.CAP_PREFIX[k] in FG.PLATFORM_WIDEN_PREFIXES for k in FG.KINDS))
check("H3 NARROWING stays the tenant's own business — it can only ever show less",
      not FG.widening_is_platform_only("cap", "module:account", FG.GRANT_OFF) and
      not FG.widening_is_platform_only("cap", "connector:anything", FG.GRANT_OFF))
check("H4 clearing a row is narrowing too (no label = no widening)",
      not FG.widening_is_platform_only("cap", "module:account", "") and
      not FG.widening_is_platform_only("cap", "module:account", None))
check("H5 a display nickname is untouched — this is about the cap scope alone",
      not FG.widening_is_platform_only("nav", "module:account", FG.GRANT_ON) and
      not FG.widening_is_platform_only("group", "Commissions", FG.GRANT_ON))
check("H6 the answer does not depend on spelling or padding",
      FG.widening_is_platform_only(" CAP ", "module:account", " SHOW "))
check("H7 a SYNONYM for yes is refused too — the test asks what the value MEANS, never whether it "
      "spells one word (the gap the security pass found in the first fix)",
      all(FG.widening_is_platform_only("cap", "module:account", v)
          for v in ("yes", "on", "1", "true", "enabled", True)),
      "a == 'show' test lets every synonym walk past the refusal")
check("H8 …and such a row would not be a grant even if it were stored",
      all(FG.stored_grant(v) is None for v in ("yes", "on", "1", "true", "enabled")))
ctl("H can go red", FG.widening_is_platform_only("cap", "module:x", "show") is True)

print("\n" + "=" * 100)
print("%d passed, %d failed" % (P, F))
print("OK — the per-tenant feature gate holds." if not F else "FAIL — the feature gate is open.")
sys.exit(1 if F else 0)
