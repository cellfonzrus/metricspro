"""HARNESS — Tiled-dashboard layout designer (dashboard-builder Phase D1).

Owner spec 2026-09-01: every module gets a tiled dashboard whose layout is USER-DESIGNED per
module. Super admin designs for all modules and ANY tenant; a layout saved on the HOUSE org is the
PLATFORM DEFAULT all tenants inherit; tenant admins may override for their OWN tenant only; every
write is permission-gated (fail-closed).

Everything proven here is PURE — no database, no fastapi, no pandas, no network (stdlib + the pure
module only): the sanitizer (caps / trims / drops malformed items / href allow-list / ValueError on
garbage), the tenant>house resolver (malformed tenant JSON degrades to house, never raises), the
save semantics (None / empty = delete = revert to inheritance) and the WRITE-GATE decision truth
table (`tile_write_gate` / `tile_write_org`).

  python3 backend/harness_tile_layout.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import tile_layout as tl                       # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


def raises(fn, *a):
    try:
        fn(*a)
        return False
    except ValueError:
        return True
    except Exception:
        return False


HOUSE = tl.HOUSE_ORG
T1 = "11111111-1111-1111-1111-111111111111"
T2 = "22222222-2222-2222-2222-222222222222"

print("── A. module key ──")
check("A1 normal key passes", tl.normalize_module_key(" payroll ") == "payroll")
check("A2 path-ish key passes", tl.normalize_module_key("hr/total-comp") == "hr/total-comp")
check("A3 empty raises", raises(tl.normalize_module_key, ""))
check("A4 whitespace-only raises", raises(tl.normalize_module_key, "   "))
check("A5 61 chars raises", raises(tl.normalize_module_key, "x" * 61))
check("A6 spaces inside raise", raises(tl.normalize_module_key, "pay roll"))

print("── B. sanitizer: canonical shape, trims, clamps ──")
raw = {"version": 99, "junk": True, "tiles": [
    {"title": "  Payroll  ", "icon": "💰", "desc": "  run pay  ", "extra": 1,
     "items": [
         {"href": " /payroll/run ", "label": " Run payroll ", "icon": "▶", "desc": " go "},
         {"href": "/payroll/history"},
     ]},
]}
out = tl.sanitize_tile_layout(raw)
check("B1 version pinned to 1", out["version"] == 1)
check("B2 unknown top-level keys dropped", set(out.keys()) == {"version", "tiles"})
check("B3 tile title trimmed", out["tiles"][0]["title"] == "Payroll")
check("B4 tile desc trimmed", out["tiles"][0]["desc"] == "run pay")
check("B5 unknown tile keys dropped", "extra" not in out["tiles"][0])
check("B6 item href trimmed", out["tiles"][0]["items"][0]["href"] == "/payroll/run")
check("B7 item label trimmed", out["tiles"][0]["items"][0]["label"] == "Run payroll")
check("B8 bare-href item kept minimal", out["tiles"][0]["items"][1] == {"href": "/payroll/history"})
long = tl.sanitize_tile_layout({"tiles": [{"title": "T" * 200, "desc": "d" * 500,
                                           "icon": "i" * 20,
                                           "items": [{"href": "/x", "label": "L" * 200,
                                                      "desc": "D" * 500, "icon": "i" * 20}]}]})
check("B9 title clamped to 80", len(long["tiles"][0]["title"]) == 80)
check("B10 tile desc clamped to 200", len(long["tiles"][0]["desc"]) == 200)
check("B11 tile icon clamped to 8", len(long["tiles"][0]["icon"]) == 8)
check("B12 item label clamped to 80", len(long["tiles"][0]["items"][0]["label"]) == 80)
check("B13 item desc clamped to 200", len(long["tiles"][0]["items"][0]["desc"]) == 200)
check("B14 tile with no items -> items=[]",
      tl.sanitize_tile_layout({"tiles": [{"title": "T"}]})["tiles"][0]["items"] == [])
check("B15 empty tiles list is valid (caller treats as clear)",
      tl.sanitize_tile_layout({"tiles": []}) == {"version": 1, "tiles": []})

print("── C. sanitizer: malformed ITEMS dropped, href allow-list ──")
mixed = tl.sanitize_tile_layout({"tiles": [{"title": "T", "items": [
    {"href": "/good"},
    {"href": "https://evil.example/x"},          # absolute URL -> dropped
    {"href": "javascript:alert(1)"},              # scheme payload -> dropped
    {"href": "//protocol-relative.example"},      # protocol-relative -> dropped
    {"href": "relative/path"},                    # not rooted -> dropped
    {"href": ""},                                 # empty -> dropped
    {"label": "no href"},                         # missing href -> dropped
    "not-a-dict",                                 # -> dropped
    None,                                         # -> dropped
    {"href": "/also-good", "label": "ok"},
]}]})
check("C1 only rooted internal hrefs survive",
      [i["href"] for i in mixed["tiles"][0]["items"]] == ["/good", "/also-good"])

print("── D. sanitizer: structural garbage raises ValueError ──")
check("D1 non-dict payload", raises(tl.sanitize_tile_layout, "nope"))
check("D2 None payload", raises(tl.sanitize_tile_layout, None))
check("D3 list payload", raises(tl.sanitize_tile_layout, [{"title": "T"}]))
check("D4 missing tiles", raises(tl.sanitize_tile_layout, {}))
check("D5 tiles not a list", raises(tl.sanitize_tile_layout, {"tiles": {"a": 1}}))
check("D6 tile not a dict", raises(tl.sanitize_tile_layout, {"tiles": ["x"]}))
check("D7 tile without title", raises(tl.sanitize_tile_layout, {"tiles": [{"items": []}]}))
check("D8 tile with blank title", raises(tl.sanitize_tile_layout, {"tiles": [{"title": "   "}]}))
check("D9 items not a list", raises(tl.sanitize_tile_layout, {"tiles": [{"title": "T", "items": 5}]}))

print("── E. sanitizer: caps ──")
ok40 = {"tiles": [{"title": f"T{i}"} for i in range(40)]}
check("E1 40 tiles pass", len(tl.sanitize_tile_layout(ok40)["tiles"]) == 40)
check("E2 41 tiles raise",
      raises(tl.sanitize_tile_layout, {"tiles": [{"title": f"T{i}"} for i in range(41)]}))
ok60 = {"tiles": [{"title": "T", "items": [{"href": f"/p{i}"} for i in range(60)]}]}
check("E3 60 items/tile pass", len(tl.sanitize_tile_layout(ok60)["tiles"][0]["items"]) == 60)
check("E4 61 items/tile raise",
      raises(tl.sanitize_tile_layout,
             {"tiles": [{"title": "T", "items": [{"href": f"/p{i}"} for i in range(61)]}]}))
# 400-item TOTAL cap: 7 tiles x 58 valid items = 406 > 400 raises; dropped-malformed items do NOT
# count toward the total (only what would actually be stored does).
over_total = {"tiles": [{"title": f"T{t}", "items": [{"href": f"/p{t}/{i}"} for i in range(58)]}
                        for t in range(7)]}
check("E5 >400 total items raise", raises(tl.sanitize_tile_layout, over_total))
under_total_with_junk = {"tiles": [
    {"title": f"T{t}", "items": [{"href": f"/p{t}/{i}"} for i in range(57)]} for t in range(7)]}
check("E6 399 total items pass",
      sum(len(t["items"]) for t in tl.sanitize_tile_layout(under_total_with_junk)["tiles"]) == 399)
dropped_dont_count = {"tiles": [
    {"title": f"T{t}",
     "items": [{"href": f"/p{t}/{i}"} for i in range(57)] + [{"href": "bad"}] * 3}
    for t in range(7)]}   # 60 raw/tile (at the per-tile cap); 420 raw total, 399 stored
check("E7 dropped malformed items don't count toward the total cap",
      sum(len(t["items"]) for t in tl.sanitize_tile_layout(dropped_dont_count)["tiles"]) == 399)

print("── F. resolve order: tenant > house > none; malformed degrades ──")
TEN = json.dumps({"version": 1, "tiles": [{"title": "Tenant", "items": []}]})
HOU = json.dumps({"version": 1, "tiles": [{"title": "House", "items": []}]})
lay, src = tl.resolve_tile_layout(TEN, HOU)
check("F1 tenant wins", src == "tenant" and lay["tiles"][0]["title"] == "Tenant")
lay, src = tl.resolve_tile_layout(None, HOU)
check("F2 no tenant row -> house", src == "house" and lay["tiles"][0]["title"] == "House")
lay, src = tl.resolve_tile_layout("{corrupt json", HOU)
check("F3 malformed tenant JSON degrades to house (no raise)", src == "house")
lay, src = tl.resolve_tile_layout(json.dumps({"tiles": "not-a-list"}), HOU)
check("F4 wrong-shaped tenant row degrades to house", src == "house")
lay, src = tl.resolve_tile_layout(json.dumps(["array"]), HOU)
check("F5 non-dict tenant row degrades to house", src == "house")
lay, src = tl.resolve_tile_layout(None, None)
check("F6 nothing anywhere -> (None, None)", lay is None and src is None)
lay, src = tl.resolve_tile_layout("{bad", "{also-bad")
check("F7 both malformed -> (None, None), never raises", lay is None and src is None)
lay, src = tl.resolve_tile_layout(TEN, None)
check("F8 tenant only -> tenant", src == "tenant")


class _FakeQuery:
    """Minimal supabase-shaped stub recording the one delete/upsert save_tile_layout issues."""
    def __init__(self, log):
        self.log = log
        self.filters = {}
        self.op = None
        self.payload = None

    def schema(self, s): return self
    def table(self, t): return self
    def select(self, *a): return self
    def in_(self, k, v): self.filters[k] = list(v); return self
    def eq(self, k, v): self.filters[k] = v; return self

    def delete(self):
        self.op = "delete"
        return self

    def upsert(self, row, on_conflict=""):
        self.op = "upsert"
        self.payload = (row, on_conflict)
        return self

    def execute(self):
        self.log.append((self.op, dict(self.filters), self.payload))
        class R: data = []
        return R()


print("── G. save semantics: None/empty deletes (revert to inheritance) ──")
log = []
res = tl.save_tile_layout(_FakeQuery(log), T1, "payroll", None)
check("G1 None -> delete + cleared", res == {"cleared": True} and log[-1][0] == "delete")
check("G2 delete is pinned to (org,'tiles',module)",
      log[-1][1] == {"org_id": T1, "scope": "tiles", "key": "payroll"})
res = tl.save_tile_layout(_FakeQuery(log), T1, "payroll", {"version": 1, "tiles": []})
check("G3 empty tiles -> delete too", res == {"cleared": True} and log[-1][0] == "delete")
lay = tl.sanitize_tile_layout({"tiles": [{"title": "T", "items": [{"href": "/x"}]}]})
res = tl.save_tile_layout(_FakeQuery(log), T1, "payroll", lay, now_iso="2026-09-01T00:00:00+00:00")
row, oc = log[-1][2]
check("G4 layout -> upsert cleared=False", res == {"cleared": False} and log[-1][0] == "upsert")
check("G5 upsert row targets (org,'tiles',module) with JSON label",
      row["org_id"] == T1 and row["scope"] == "tiles" and row["key"] == "payroll"
      and json.loads(row["label"]) == lay and row["updated_at"] == "2026-09-01T00:00:00+00:00")
check("G6 upsert on_conflict matches mig-068 unique key", oc == "org_id,scope,key")
check("G7 save validates the module key", raises(tl.save_tile_layout, _FakeQuery(log), T1, "", lay))

print("── H. load: one query spans caller org + HOUSE, then pure resolve ──")
class _LoadQuery(_FakeQuery):
    def __init__(self, log, rows):
        super().__init__(log)
        self.rows = rows

    def execute(self):
        self.log.append((self.op, dict(self.filters), self.payload))
        class R: pass
        r = R(); r.data = self.rows
        return r


log = []
lay, src = tl.load_tile_layout(
    _LoadQuery(log, [{"org_id": T1, "label": TEN}, {"org_id": HOUSE, "label": HOU}]), T1, "payroll")
check("H1 tenant row wins on load", src == "tenant" and lay["tiles"][0]["title"] == "Tenant")
check("H2 query spans exactly {tenant, HOUSE} orgs, scope='tiles', key=module",
      sorted(log[-1][1]["org_id"]) == sorted({T1, HOUSE})
      and log[-1][1]["scope"] == "tiles" and log[-1][1]["key"] == "payroll")
lay, src = tl.load_tile_layout(_LoadQuery([], [{"org_id": HOUSE, "label": HOU}]), T1, "payroll")
check("H3 tenant without a row inherits house", src == "house")
lay, src = tl.load_tile_layout(_LoadQuery([], [{"org_id": HOUSE, "label": HOU}]), HOUSE, "payroll")
check("H4 the HOUSE org's own row resolves as 'house' (it IS the platform default)", src == "house")
lay, src = tl.load_tile_layout(_LoadQuery([], []), T1, "payroll")
check("H5 no rows anywhere -> (None, None)", lay is None and src is None)

print("── I. write-gate truth table ──")
SUPER = {"org_id": HOUSE, "super_admin": True, "can_edit": True}
SUPER_ACTING_T1 = {"org_id": T1, "super_admin": True, "can_edit": True}
TADMIN = {"org_id": T1, "super_admin": False, "can_edit": True}
TUSER = {"org_id": T1, "super_admin": False, "can_edit": False}
g = tl.tile_write_gate
check("I1 super + house target -> allow", g(SUPER, "house", "") == "allow")
check("I2 super acting-as-tenant + house target -> allow (flag is login-level)",
      g(SUPER_ACTING_T1, "house", T1) == "allow")
check("I3 super + foreign tenant -> allow (design for ANY tenant)",
      g(SUPER, "tenant", T2) == "allow")
check("I4 super + own tenant -> allow", g(SUPER_ACTING_T1, "tenant", T1) == "allow")
check("I5 tenant admin + own org -> allow", g(TADMIN, "tenant", T1) == "allow")
check("I6 tenant admin + blank org (defaults to own) -> allow", g(TADMIN, "tenant", "") == "allow")
check("I7 tenant admin + house target -> forbid",
      g(TADMIN, "house", "") == "forbid_house_requires_super_admin")
check("I8 tenant admin + foreign org -> forbid", g(TADMIN, "tenant", T2) == "forbid_foreign_org")
check("I9 tenant admin + HOUSE org as 'tenant' target -> forbid (house row via side door)",
      g(TADMIN, "tenant", HOUSE) == "forbid_foreign_org")
check("I10 tenant user w/o grant + own org -> forbid",
      g(TUSER, "tenant", T1) == "forbid_no_setting_grant")
check("I11 tenant user w/o grant + house -> forbid",
      g(TUSER, "house", "") == "forbid_house_requires_super_admin")
check("I12 no caller -> forbid", g(None, "tenant", T1) == "forbid_unauthenticated")
check("I13 caller without org -> forbid",
      g({"org_id": "", "super_admin": True, "can_edit": True}, "house", "") == "forbid_unauthenticated")
check("I14 bad target -> forbid", g(TADMIN, "global", T1) == "forbid_bad_target")
check("I15 target is case/space tolerant", g(SUPER, " House ", "") == "allow")
check("I16 default gate posture is deny (unknown decision strings never appear)",
      all(g(c, t, o) in ("allow", "forbid_unauthenticated", "forbid_house_requires_super_admin",
                         "forbid_bad_target", "forbid_foreign_org", "forbid_no_setting_grant")
          for c in (SUPER, SUPER_ACTING_T1, TADMIN, TUSER, None)
          for t in ("house", "tenant", "", "weird")
          for o in ("", T1, T2, HOUSE)))

print("── J. write-org pinning: the request never decides a non-super caller's org ──")
w = tl.tile_write_org
check("J1 house target -> HOUSE row regardless of org param", w(SUPER, "house", T2) == HOUSE)
check("J2 super + explicit tenant -> that tenant's row", w(SUPER, "tenant", T2) == T2)
check("J3 super + blank org -> own acting org", w(SUPER_ACTING_T1, "tenant", "") == T1)
check("J4 tenant admin pinned to OWN org even when the request names another",
      w(TADMIN, "tenant", T2) == T1)
check("J5 tenant admin pinned to own org when request names HOUSE", w(TADMIN, "tenant", HOUSE) == T1)

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
