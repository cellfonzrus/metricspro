"""PROOF + LOCK — the tenant VERTICAL (mig 1020, index §35): what kind of business a tenant is, and what that hides.

THE CLASS: the platform had no business-type axis; every gate was carrier-shaped, and a tenant with NO carrier
saw everything (carrierOK hides nothing on an empty list, defaultActiveCarrier fell back to a named carrier,
generic wireless pages were never tagged). ONE declaration (storeops.tenants.vertical over core.tenant_vertical)
now feeds the EXISTING gates, and this file fails the build if a gate stops asking it or a second copy appears.

  §A  the code mirror (core/verticals.HOUSE_VERTICALS / HOUSE_MODULE_VERTICALS) equals the mig-1020 seed
  §B  the pure rules: default, unknown value, module scope, hidden modules, href hiding ('x$' exact, 'x' subtree)
  §C  every nav_hidden entry is a real NAV page (no stale), and no page a non-carrier store runs on is hidden
  §D  behaviour over a fake client: module_enabled / effective_modules drop out-of-vertical modules; existing
      tenants (no vertical) are unchanged; me_payload; an unreadable registry degrades to the mirror
  §E  wiring lock: every gate dereferences the one fact (backend entitlements + /core/me + wizard + provisioning;
      frontend sidebar, hub, compliance, route guard, safe home, login redirect, active carrier)
  §F  RULE TWO: no vertical key is spelled outside the mirror + the migration
  §G  negative controls: each lock goes RED on a planted defect

Stdlib only: `python backend/harness_tenant_vertical.py`.
"""
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

# entitlements imports fastapi + the DB client at module load; stub them so this stays stdlib-only.
if "fastapi" not in sys.modules:
    try:
        import fastapi  # noqa: F401
    except Exception:
        fa = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code=500, detail=""):
                super().__init__(detail)
                self.status_code, self.detail = status_code, detail
        fa.HTTPException = HTTPException
        sys.modules["fastapi"] = fa
try:
    import app.core.database  # noqa: F401
except Exception:
    for name in ("app.core", "app.core.database"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["app.core.database"].get_supabase = lambda: None

from app.modules.core import verticals as V  # noqa: E402

FAILS, N = [], [0]


def check(name, cond, detail=""):
    N[0] += 1
    if not cond:
        FAILS.append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  → {detail}"))


def read(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


MIG = "database/migrations/1020_tenant_vertical.sql"
UPS, WL = "ups_store", "wireless_retail"      # the harness is the one other place allowed to name them

# ── §A mirror = seed ────────────────────────────────────────────────────────────────────────────────────
print("§A mirror = seed")


def parse_seed(sql):
    body = sql.split("INSERT INTO core.tenant_vertical", 1)[1].split("ON CONFLICT", 1)[0]
    rows = []
    for m in re.finditer(r"\(\s*'([a-z_]+)',\s*'([^']*)',\s*(true|false),\s*(true|false),\s*'\{([^}]*)\}',\s*(\d+),", body):
        rows.append({"key": m.group(1), "label": m.group(2), "is_default": m.group(3) == "true",
                     "uses_carriers": m.group(4) == "true",
                     "nav_hidden": [h for h in m.group(5).split(",") if h], "sort_order": int(m.group(6))})
    mods = {}
    mb = sql.split("INSERT INTO core.module_catalog", 1)[1].split("ON CONFLICT", 1)[0]
    for m in re.finditer(r"\(\s*'([a-z_]+)',\s*'[^']*',\s*\d+,\s*'\{([^}]*)\}'\)", mb):
        mods[m.group(1)] = [x for x in m.group(2).split(",") if x]
    for m in re.finditer(r"SET applies_to_vertical = '\{([^}]*)\}'\s*WHERE key = '([a-z_]+)'", sql.split("-- REVERT", 1)[0]):
        mods[m.group(2)] = [x for x in m.group(1).split(",") if x]
    return rows, mods


seed_rows, seed_mods = parse_seed(read(MIG))
check("A1 seed parsed (2 verticals)", len(seed_rows) == 2, seed_rows)
check("A2 vertical rows byte-equal", seed_rows == [dict(r) for r in V.HOUSE_VERTICALS],
      [(a == b, a["key"]) for a, b in zip(seed_rows, V.HOUSE_VERTICALS)])
check("A3 module scopes equal", seed_mods == V.HOUSE_MODULE_VERTICALS, (seed_mods, V.HOUSE_MODULE_VERTICALS))
check("A4 exactly one default", sum(r["is_default"] for r in seed_rows) == 1)
ent_src = read("backend/app/modules/core/entitlements.py")
check("A5 every scoped module is in the entitlement catalog",
      all(f'"{k}":' in ent_src for k in V.HOUSE_MODULE_VERTICALS), [k for k in V.HOUSE_MODULE_VERTICALS if f'"{k}":' not in ent_src])

# ── §B pure rules ───────────────────────────────────────────────────────────────────────────────────────
print("§B pure rules")
vocab = [dict(r) for r in V.HOUSE_VERTICALS]
check("B1 unset → default", V.resolve_vertical(None, vocab)["key"] == WL and V.resolve_vertical(None, vocab)["source"] == "default")
check("B2 declared", V.resolve_vertical(UPS, vocab)["key"] == UPS and V.resolve_vertical(UPS, vocab)["source"] == "tenant")
u = V.resolve_vertical("nonsense", vocab)
check("B3 unknown → default, reported", u["key"] == WL and u["source"] == "unknown_value" and u["declared"] == "nonsense")
check("B4 '{}' = any", V.module_applies([], UPS) and V.module_applies(None, WL))
check("B5 scoped", V.module_applies([UPS], UPS) and not V.module_applies([UPS], WL))
check("B6 hidden modules (wireless)", V.hidden_modules(V.HOUSE_MODULE_VERTICALS, WL) == ["franchise_ops", "royalty", "supply_ordering"])
check("B7 hidden modules (franchise)", V.hidden_modules(V.HOUSE_MODULE_VERTICALS, UPS) == ["vip"])
nh = ["/commcalc$", "/commcalc/asset$", "/closing/epay-recon", "/employee"]
check("B8 exact '$' hides only itself", V.href_hidden("/commcalc", nh) and not V.href_hidden("/commcalc/upload", nh))
check("B9 subtree", V.href_hidden("/closing/epay-recon", nh) and V.href_hidden("/closing/epay-recon/x", nh))
check("B10 no prefix bleed", not V.href_hidden("/employees", nh) and not V.href_hidden("/commcalc/asset/purchase-orders", nh))
check("B11 query + trailing slash", V.href_hidden("/employee/?x=1", nh))
check("B12 valid_choice", V.valid_choice(UPS, vocab) and not V.valid_choice("Robert'); DROP", vocab) and not V.valid_choice("", vocab))
p = V.payload(V.resolve_vertical(UPS, vocab), V.HOUSE_MODULE_VERTICALS, vocab)
check("B13 payload", p["uses_carriers"] is False and "vip" in p["hidden_modules"] and len(p["choices"]) == 2 and p["nav_hidden"])

# ── §C nav_hidden vs the real NAV ───────────────────────────────────────────────────────────────────────
print("§C nav_hidden vs NAV")
rbac = read("frontend/src/lib/rbac.ts")
nav_block = rbac.split("export const NAV: NavGroup[] = [", 1)[1]
nav_hrefs = set(re.findall(r"href: '([^']+)'", nav_block.split("\n]\n", 1)[0]))
check("C0 NAV parsed", len(nav_hrefs) > 150, len(nav_hrefs))
ups_hidden = next(r for r in V.HOUSE_VERTICALS if r["key"] == UPS)["nav_hidden"]
stale = [h for h in ups_hidden if not any(n == h.rstrip("$") or n.startswith(h.rstrip("$") + "/") for n in nav_hrefs)]
check("C1 no stale entry (each names a real page or subtree)", not stale, stale)
KEEP = ["/franchise", "/closing", "/closing/submit", "/closing/pickup", "/closing/deposit-recon",
        "/closing/external-credit-recon", "/closing/tender-recon-3way", "/closing/imports", "/commcalc/upload",
        "/commcalc/email-imports", "/commcalc/ftp-imports", "/commcalc/connectors", "/commcalc/onboarding",
        "/commcalc/asset/purchase-orders", "/commcalc/payables", "/commcalc/expenses", "/commcalc/tax-collected",
        "/accounts/pl", "/accounts/journal", "/storeops/payroll", "/storeops/schedule", "/storeops/employees",
        "/pos/sales", "/pos/inventory", "/pos/products", "/commcalc/store-match", "/commcalc/column-mapping"]
wrongly = [k for k in KEEP if V.href_hidden(k, ups_hidden)]
check("C2 no page a non-carrier store runs on is hidden", not wrongly, wrongly)
check("C3 the dashboard group exists in NAV with its module",
      "href: '/franchise'" in rbac and "module: 'franchise_ops'" in rbac)

# ── §D behaviour over a fake client ─────────────────────────────────────────────────────────────────────
print("§D behaviour (fake client)")


class Q:
    def __init__(self, db, schema, table):
        self.db, self.key, self.filters = db, (schema, table), []

    def select(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def upsert(self, rows, **_k):
        self.db.setdefault(self.key, [])
        for r in rows:
            self.db[self.key] = [x for x in self.db[self.key] if not (x["org_id"] == r["org_id"] and x["module_key"] == r["module_key"])] + [r]
        return self

    def execute(self):
        if self.key in self.db.get("_broken", ()):
            raise RuntimeError("table missing")
        rows = [r for r in self.db.get(self.key, []) if all(r.get(c) == v for c, v in self.filters)]
        return types.SimpleNamespace(data=rows)


class S:
    def __init__(self, db, schema): self.db, self.schema_ = db, schema
    def table(self, t): return Q(self.db, self.schema_, t)
    def rpc(self, *_a, **_k): return types.SimpleNamespace(execute=lambda: None)


class Client:
    def __init__(self, db): self.db = db
    def schema(self, s): return S(self.db, s)


A, B = "org-a", "org-b"
cat = [{"key": k, "label": k, "sort_order": i, "applies_to_vertical": V.HOUSE_MODULE_VERTICALS.get(k, [])}
       for i, k in enumerate(["commissions", "closing", "vip", "marketing", "franchise_ops", "supply_ordering", "royalty"])]
db = {("core", "tenant_vertical"): [dict(r, is_active=True) for r in V.HOUSE_VERTICALS],
      ("core", "module_catalog"): cat,
      ("storeops", "tenants"): [{"org_id": A, "vertical": None}, {"org_id": B, "vertical": UPS}],
      ("storeops", "tenant_modules"): [{"org_id": o, "module_key": m["key"], "is_enabled": True} for o in (A, B) for m in cat],
      ("storeops", "billing_plan"): []}
c = Client(db)
from app.modules.core import entitlements as E  # noqa: E402
check("D1 existing tenant: every module it had stays enabled",
      all(E.module_enabled(A, k, c) for k in ("commissions", "closing", "vip", "marketing")))
check("D2 existing tenant: franchise modules off", not any(E.module_enabled(A, k, c) for k in ("franchise_ops", "supply_ordering", "royalty")))
check("D3 franchise tenant: its modules on, vip off",
      all(E.module_enabled(B, k, c) for k in ("franchise_ops", "supply_ordering", "royalty")) and not E.module_enabled(B, "vip", c))
db2 = dict(db, _broken={("storeops", "tenant_modules")})
check("D4 tenant_modules unreachable still never opens a module to the wrong vertical",
      not E.module_enabled(A, "royalty", Client(db2)) and E.module_enabled(A, "closing", Client(db2)))
check("D5 effective_modules", "vip" in E.effective_modules(c, A) and "royalty" not in E.effective_modules(c, A)
      and "royalty" in E.effective_modules(c, B) and "vip" not in E.effective_modules(c, B))
db3 = dict(db, _broken={("core", "tenant_vertical"), ("core", "module_catalog")})
check("D6 registry unreadable → the mirror answers identically",
      V.tenant_vertical(Client(db3), B)["key"] == UPS and not V.module_applies_to_tenant(Client(db3), A, "royalty"))
mp = V.me_payload(c, B)
check("D7 me_payload", mp["key"] == UPS and mp["uses_carriers"] is False and "vip" in mp["hidden_modules"] and mp["registry_ready"])
check("D8 me_payload existing tenant hides only new modules",
      V.me_payload(c, A)["hidden_modules"] == ["franchise_ops", "royalty", "supply_ordering"] and V.me_payload(c, A)["nav_hidden"] == [])

# ── §E wiring lock ──────────────────────────────────────────────────────────────────────────────────────
print("§E wiring lock")
core_src = read("backend/app/modules/core/router.py")
wiz_src = read("backend/app/modules/commcalc/router.py")
WIRES = [
    ("entitlements.module_enabled asks the vertical", ent_src, r"def module_enabled[\s\S]{0,900}?module_applies_to_tenant"),
    ("entitlements.effective_modules drops out-of-vertical", ent_src, r"def effective_modules[\s\S]{0,1400}?hidden_modules"),
    ("/core/me carries tenant.vertical", core_src, r'tenant\["vertical"\] = _vert\.me_payload'),
    ("provisioning writes the vertical before sync_tenant", core_src, r"_set_tenant_vertical\(client, new_org, vertical\)[\s\S]{0,1500}?sync_tenant\(client, new_org\)"),
    ("the vertical write validates against the vocabulary", core_src, r"def _set_tenant_vertical[\s\S]{0,400}?valid_choice"),
    ("wizard profile drops carrier questions by the vertical flag", wiz_src, r"_onboarding_profile_step\(carrier_rows,\s*uses_carriers="),
]
layout = read("frontend/src/app/(platform)/layout.tsx")
hub = read("frontend/src/app/(platform)/hub/[group]/page.tsx")
comp = read("frontend/src/app/(platform)/compliance/page.tsx")
auth = read("frontend/src/lib/auth-context.tsx")
login = read("frontend/src/app/login/page.tsx")
WIRES += [
    ("sidebar filters by verticalOK", layout, r"verticalOK\(it, tenant\?\.vertical, caps\)"),
    ("route guard asks verticalPathOK", layout, r"verticalPathOK\(pathname, tenant\?\.vertical\)"),
    ("guard's safe home skips vertical-hidden pages", layout, r"safeHomeFor\(permissions, tenant\?\.vertical\)"),
    ("hub dashboards filter by verticalOK", hub, r"verticalOK\(it, tenant\?\.vertical, caps\)"),
    ("compliance dashboard filters by verticalOK", comp, r"verticalOK\(it, tenant\?\.vertical, caps\)"),
    ("active carrier honours uses_carriers", auth, r"defaultActiveCarrier\(carriers, tenant\?\.vertical\?\.uses_carriers\)"),
    ("login redirect skips vertical-hidden pages", login, r"safeHomeFor\(permissions, tenant\?\.vertical\)"),
    ("defaultActiveCarrier returns no carrier when the vertical uses none", rbac, r"if \(usesCarriers === false\) return ''"),
    ("safeHomeFor consults verticalPathOK", rbac, r"export function safeHomeFor\(perms: Permissions, vertical\?[\s\S]{0,500}?verticalPathOK"),
]


def wired(src, pat):
    return re.search(pat, src) is not None


for name, src, pat in WIRES:
    check("E " + name, wired(src, pat))

# ── §F RULE TWO ─────────────────────────────────────────────────────────────────────────────────────────
print("§F no vertical key spelled in code")
ALLOWED = {os.path.normpath("backend/app/modules/core/verticals.py")}
spelled = []
for base in ("backend/app", "frontend/src"):
    for root, dirs, files in os.walk(os.path.join(ROOT, base)):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".next", "__pycache__")]
        for fn in files:
            if not fn.endswith((".py", ".ts", ".tsx")):
                continue
            rel = os.path.normpath(os.path.relpath(os.path.join(root, fn), ROOT))
            if rel in ALLOWED:
                continue
            txt = open(os.path.join(root, fn), encoding="utf-8", errors="ignore").read()
            if re.search(r"['\"](%s|%s)['\"]" % (UPS, WL), txt):
                spelled.append(rel)
check("F1 vertical keys appear only in the mirror", not spelled, spelled)

# ── §G negative controls ────────────────────────────────────────────────────────────────────────────────
print("§G negative controls")
check("G1 removing the sidebar gate goes RED", not wired(layout.replace("verticalOK(it, tenant?.vertical, caps)", "true"),
                                                         r"verticalOK\(it, tenant\?\.vertical, caps\)"))
check("G2 dropping the entitlement ask goes RED",
      not wired(ent_src.replace("module_applies_to_tenant", "nothing"), WIRES[0][2]))
check("G3 a drifted mirror goes RED", parse_seed(read(MIG).replace("'Wireless retail'", "'Wireless'"))[0] != [dict(r) for r in V.HOUSE_VERTICALS])
check("G4 a stale nav_hidden entry is caught",
      bool([h for h in ["/commcalc/no-such-page"] if not any(n == h or n.startswith(h + "/") for n in nav_hrefs)]))
check("G5 hiding a kept page is caught", bool([k for k in KEEP if V.href_hidden(k, ups_hidden + ["/closing/submit"])]))
check("G6 a spelled key is caught", bool(re.search(r"['\"](%s|%s)['\"]" % (UPS, WL), "if (v === '%s')" % UPS)))

print(f"\n{N[0] - len(FAILS)}/{N[0]} passed")
sys.exit(1 if FAILS else 0)
