"""Proof harness — `_caller_self_keyset` must NEVER return the UNRESTRICTED sentinel.

THE DEFECT (owner-authorised fix 2026-08-08, "fix the leak")
────────────────────────────────────────────────────────────
`_caller_self_keyset()` returned `(True, None)` for a self-scoped rep with no pinned store, with the
comment "self rep, no pinned store -> don't lock them out". At its ONLY call site — the Targets read,
`GET /commcalc/targets/{period}/summary` — `None` is the UNRESTRICTED sentinel:

    is_self, self_ks = _caller_self_keyset(authorization, org_id)
    if is_self:
        ks = self_ks            # pinless rep -> None
    if ks is not None:          # <- the span filter is SKIPPED ENTIRELY
        out = [s for s in out if in_keyset(ks, s.get('store_code'), s.get('address'))]

Six live logins (4 house + 2 Luxelink) sit on that branch. This is the same `ks is None` class as the
span-keyset bugs: invisible from an admin session, because an admin's keyset is legitimately None.

⚠️ SECOND, INDEPENDENT DEFECT FOUND WHILE FIXING IT — THE WRONG SCHEMA
──────────────────────────────────────────────────────────────────────
The function read `sb().table("app_users")`, and commcalc's `sb()` is the DEFAULT-schema (public)
client. **`public.app_users` does not exist** — `app_users` lives in `storeops`. PostgREST answers

    PGRST205  "Could not find the table 'public.app_users' in the schema cache"

for EVERY role including service_role (a schema-cache miss is role-independent — verified live
2026-08-08 against the real project). The read therefore raised on every request and the outer
`except Exception: return (False, None)` swallowed it, so the whole function was a **no-op in
production**. Its address-widening step read `sb().table("stores")` = `public.stores`, a different
legacy 25-row table with **no `org_id` column**, so `.eq("org_id", …)` 400'd too.

Net live behaviour BEFORE this fix, therefore, is NOT "a pinless rep sees 20 stores" but "**every**
self rep — pinned or not — sees **0** stores", because `is_self` is False, `ks` falls back to
`scope_keyset()` = the empty set for a rep, and the filter drops everything. Section F proves that
from the real PostgREST error shapes; Section B reports BOTH numbers so neither is hidden:

  * `before_as_written`  — what the code does today, schema bug and all (the 0s).
  * `before_if_schema_ok`— what the code was WRITTEN to do, i.e. the leak that was one schema
                           correction away from going live. This is the number the fix has to kill.

THE FIX
───────
1. Fall back to the ROSTER `storeops.employees.home_store` when the login pin is empty (5 of the 6
   live pinless reps have a perfectly good one).
2. If there is still no store → an **EMPTY keyset**, never `None`. "See nothing" is the safe failure.
   The original intent (never a silent blank page) is preserved through the EXISTING `setup_hint`
   channel plus an additive `scope.no_store_assigned`.
3. **Market is deliberately ignored** for a `self` caller — one of the six carries `market='Chicago'`
   (26 store codes) and honouring it would turn a 1-store rep into a 26-store one.
4. Store tokens resolve through the SAME vocabulary the keyset uses (`app.core.scope.market_index` =
   storeops.stores ∪ commcalc.store_mapping ∪ commcalc.store_aliases), and the schema reads are fixed.

NOT A PAY CHANGE. Targets VISIBILITY only: no payout, rate, tier, plan, or paid/earned column is
read or written anywhere in this diff.

FIXTURE. `fixtures_self_keyset_live.json` is a READ-ONLY snapshot of the real tenants pulled
2026-08-08 (100 app_users, 100 employees, 46 storeops.stores, 70 store_mapping, 25 store_aliases,
17 roles, 45 targeted codes, 5 org-manager spans). Emails are reduced to their local part and the
auth ids are synthetic, so the arithmetic below is production arithmetic without carrying secrets.

Run: `cd backend && python3 harness_self_keyset_home_store.py`
"""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"   [{detail}]" if detail else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
FX = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fixtures_self_keyset_live.json")))


# ══════════════════════════════════════════════════════════════════════════════════════════════
# A fake supabase client that is SCHEMA-AWARE — that is the whole point, since the bug is a
# default-schema read. It reproduces the two real PostgREST failures verbatim:
#   • public.app_users        -> PGRST205, table not in the schema cache
#   • public.stores.org_id    -> 42703, column does not exist
# ══════════════════════════════════════════════════════════════════════════════════════════════
class FakeAPIError(Exception):
    pass


class Q:
    def __init__(self, rows, schema, table):
        self.rows, self.schema_name, self.table_name = rows, schema, table
        self.filters, self.cols = [], []

    def select(self, cols="*", *a, **k):
        self.cols = [c.strip() for c in str(cols).split(",") if c.strip()]
        return self

    def eq(self, c, v):
        # public.stores genuinely has no org_id column -> PostgREST 42703, as in prod.
        if self.schema_name == "public" and self.table_name == "stores" and c == "org_id":
            raise FakeAPIError("42501/42703: column stores.org_id does not exist")
        self.filters.append((c, "eq", v))
        return self

    def in_(self, c, v):
        self.filters.append((c, "in", list(v)))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def _match(self, r):
        for c, kind, v in self.filters:
            rv = r.get(c)
            if kind == "eq" and rv != v:
                return False
            if kind == "in" and rv not in v:
                return False
        return True

    def execute(self):
        return SimpleNamespace(data=[dict(r) for r in self.rows if self._match(r)])


class FakeClient:
    """`.table(x)` = the DEFAULT (public) schema — exactly what commcalc's `sb()` gives you."""

    def __init__(self, data, schema="public"):
        self.data, self.schema_name = data, schema

    def schema(self, name):
        return FakeClient(self.data, name)

    def table(self, name):
        key = f"{self.schema_name}.{name}"
        if key not in self.data:
            raise FakeAPIError(f"PGRST205: Could not find the table '{key}' in the schema cache")
        return Q(self.data[key], self.schema_name, name)

    def rpc(self, fn, params=None):
        if fn == "org_span_for_manager":
            p = params or {}
            codes = SPANS.get((str(p.get("p_org_id")), str(p.get("p_employee_id"))), [])
            return SimpleNamespace(execute=lambda: SimpleNamespace(
                data=[{"store_code": c} for c in codes]))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[]))


SPANS = {(r["org_id"], r["employee_id"]): list(r["codes"] or []) for r in FX["org_spans"]}

# public.* is the LEGACY side: `employees` and `roles` exist, `stores` exists but has no org_id,
# and `app_users` DOES NOT EXIST AT ALL. Verified against the live project 2026-08-08.
DATA = {
    "public.stores": [{"store_code": r["store_code"], "address": None} for r in FX["stores"]],
    "public.employees": [],
    "public.roles": [],
    "storeops.app_users": FX["app_users"],
    "storeops.employees": FX["employees"],
    "storeops.stores": FX["stores"],
    "storeops.roles": FX["roles"],
    "storeops.app_config": [{"id": 1, "rbac_enabled": True}],
    "commcalc.store_mapping": FX["store_mapping"],
    "commcalc.store_aliases": FX["store_aliases"],
}

import app.modules.commcalc.router as cc            # noqa: E402
import app.modules.storeops.router as SO            # noqa: E402
import app.modules.core.router as CORE              # noqa: E402
from app.core import scope as CSCOPE                # noqa: E402

FAKE = FakeClient(DATA)
cc.sb = lambda: FAKE
SO.sb = lambda: FAKE.schema("storeops")
SO.get_supabase = lambda: FAKE
CSCOPE_GET = CSCOPE.market_index
_TOKENS = {u["auth_id"]: u for u in FX["app_users"]}
CORE._uid_from_token = lambda authorization: (str(authorization)[7:] or None
                                              if str(authorization).startswith("Bearer ") else None)
SO_ROLE_CACHE = {}


def tok(u):
    return "Bearer " + u["auth_id"]


def refresh_index():
    CSCOPE.invalidate_market_index()


# ══════════════════════════════════════════════════════════════════════════════════════════════
# The PRE-FIX function, copied verbatim from origin/main 88dcac9, so BEFORE/AFTER are the same
# code path under the same fixture — not a description of the old behaviour but the old behaviour.
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _caller_self_keyset_BEFORE(authorization: str, org_id: str, sb=None):
    sb = sb or cc.sb
    try:
        from app.modules.storeops.router import _rbac_enabled, _role_scope
        from app.modules.core.router import _uid_from_token
    except Exception:
        return (False, None)
    try:
        if not _rbac_enabled(org_id):
            return (False, None)
        uid = _uid_from_token(authorization)
        if not uid:
            return (False, None)
        rows = (sb().table("app_users")
                .select("role,store_code,store_codes")
                .eq("org_id", org_id).eq("auth_id", uid).limit(1).execute().data) or []
        if not rows:
            return (False, None)
        u = rows[0]
        if _role_scope(org_id, (u.get("role") or "").strip()) != "self":
            return (False, None)
        codes = set()
        if u.get("store_code"):
            codes.add(str(u.get("store_code")).strip().upper())
        for c in (u.get("store_codes") or []):
            if str(c).strip():
                codes.add(str(c).strip().upper())
        codes.discard("")
        if not codes:
            return (True, None)   # <- THE LEAK
        keys = set(codes)
        try:
            meta = (sb().table("stores").select("store_code,address")
                    .eq("org_id", org_id).execute().data) or []
            for s in meta:
                if str(s.get("store_code") or "").strip().upper() in codes:
                    ad = str(s.get("address") or "").strip().upper()
                    if ad:
                        keys.add(ad)
        except Exception:
            pass
        return (True, keys)
    except Exception:
        return (False, None)


class _StoreopsSchemaClient:
    """A `sb()` whose DEFAULT schema is storeops — i.e. what the pre-fix code would have read from
    if the author's `public.app_users` assumption had been true. Used ONLY to compute the
    `before_if_schema_ok` column: the leak the fix exists to close."""

    def __init__(self, data):
        self.data = data

    def schema(self, name):
        return FakeClient(self.data, name)

    def table(self, name):
        remap = {"app_users": "storeops.app_users", "stores": "storeops.stores"}
        key = remap.get(name, f"public.{name}")
        return Q(self.data[key], "storeops", name)

    def rpc(self, *a, **k):
        return FAKE.rpc(*a, **k)


SCHEMA_OK = _StoreopsSchemaClient(DATA)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# The CALL SITE, reproduced exactly: build the store universe the endpoint builds, then apply the
# one filter line. "Stores visible" below is literally `len(out)` after that line.
# ══════════════════════════════════════════════════════════════════════════════════════════════
def universe(org_id):
    """storeops.stores (ACTIVE) UNION every targeted store_code — the same union get_targets_summary
    builds (`_storeops_roster` + the targeted-code enrichment from commcalc.store_mapping)."""
    smap = {str(m["store_code"] or "").strip().upper(): m for m in FX["store_mapping"]
            if m["org_id"] == org_id}
    rows, seen = [], set()
    for s in FX["stores"]:
        if s["org_id"] != org_id or s.get("is_active") is False:
            continue
        cu = str(s.get("store_code") or "").strip().upper()
        if cu:
            seen.add(cu)
        rows.append({"store_code": s.get("store_code"), "address": s.get("address")})
    for t in FX["target_codes"]:
        if t["org_id"] != org_id:
            continue
        cu = str(t.get("store_code") or "").strip().upper()
        if cu and cu not in seen:
            seen.add(cu)
            rows.append({"store_code": t.get("store_code"),
                         "address": (smap.get(cu) or {}).get("store_address")})
    return rows


def visible(ks, org_id):
    uni = universe(org_id)
    if ks is None:
        return len(uni)          # UNRESTRICTED -> the filter line never runs
    return len([s for s in uni if SO.in_keyset(ks, s.get("store_code"), s.get("address"))])


def after(u):
    refresh_index()
    return cc._caller_self_keyset(tok(u), u["org_id"])


def before_as_written(u):
    refresh_index()
    return _caller_self_keyset_BEFORE(tok(u), u["org_id"])


def before_if_schema_ok(u):
    refresh_index()
    return _caller_self_keyset_BEFORE(tok(u), u["org_id"], sb=lambda: SCHEMA_OK)


def eff_before(u, fn):
    """`ks` at the call site under the pre-fix code: the self substitution, else scope_keyset."""
    is_self, self_ks = fn(u)
    if is_self:
        return self_ks
    return SO.scope_keyset(tok(u), u["org_id"])


def eff_after(u):
    is_self, self_ks = after(u)
    if is_self:
        return self_ks if self_ks is not None else set()
    return SO.scope_keyset(tok(u), u["org_id"])


def by_label(org_id, label):
    for u in FX["app_users"]:
        if u["org_id"] == org_id and u["label"] == label:
            return u
    raise KeyError(label)


def scope_of(u):
    for r in FX["roles"]:
        if r["org_id"] == u["org_id"] and r["name"] == (u.get("role") or ""):
            return (r.get("permissions") or {}).get("scope") or "all"
    return "all"


def home_of(u):
    for e in FX["employees"]:
        if e["org_id"] == u["org_id"] and e["employee_id"] == u.get("employee_id"):
            return e.get("home_store")
    return None


print("=" * 100)
print("A · CONTRACT — a SELF caller can never be handed the UNRESTRICTED sentinel")
print("=" * 100)

self_logins = [u for u in FX["app_users"] if scope_of(u) == "self"]
check("fixture carries the real self-scoped logins", len(self_logins) >= 40, f"{len(self_logins)} logins")

nones_before, nones_after = [], []
for u in self_logins:
    if before_if_schema_ok(u)[1] is None:
        nones_before.append(u["label"])
    isf, ks = after(u)
    if isf and ks is None:
        nones_after.append(u["label"])
check("BEFORE (schema-corrected): at least one self login resolves to None = unrestricted",
      len(nones_before) > 0, f"{len(nones_before)} logins: {sorted(nones_before)}")
check("AFTER: ZERO self logins resolve to None, across BOTH tenants",
      len(nones_after) == 0, f"{len(nones_after)} still None: {sorted(nones_after)}")
check("AFTER: every self login gets a set() instance, never None",
      all(isinstance(after(u)[1], set) for u in self_logins))
check("AFTER: is_self is True for every one of them (identity read now hits the right schema)",
      all(after(u)[0] is True for u in self_logins))

non_self = [u for u in FX["app_users"] if scope_of(u) != "self"]
check("AFTER: a non-self login is still (False, None) — scope_keyset keeps governing",
      all(after(u) == (False, None) for u in non_self), f"{len(non_self)} admin/manager logins")


print()
print("=" * 100)
print("B · THE SIX LIVE LOGINS — stores visible on GET /commcalc/targets/{period}/summary")
print("=" * 100)

SIX = [(HOUSE, "imkhaled1993", "Ali", "E006", "B-3PL"),
       (HOUSE, "anthonycastellanos1030", "Anthony Castellanos", "E227", "B-5135"),
       (HOUSE, "l2127martinez", "Leslie Martinez", "E170", "B-1800"),
       (HOUSE, "suannyhidalgo", "Suanny Hidalgo", "E224", "B-151"),
       (LUX, "lcricocareer37", "Laura Rico", "E230", "Diversey"),
       (LUX, "maria.gutierrez", "(no employee record)", None, None)]

print(f"{'tenant':9} {'who':22} {'eid':6} {'roster home_store':18} "
      f"{'before(as-written)':>18} {'before(if schema ok)':>21} {'AFTER':>7}")
print("-" * 100)
rows_six = []
for org, label, who, eid, home in SIX:
    u = by_label(org, label)
    n_uni = len(universe(org))
    b_written = visible(eff_before(u, before_as_written), org)
    b_schemaok = visible(eff_before(u, before_if_schema_ok), org)
    a = visible(eff_after(u), org)
    rows_six.append((org, label, who, eid, home, b_written, b_schemaok, a, n_uni))
    print(f"{('House' if org == HOUSE else 'Luxelink'):9} {who[:22]:22} {str(eid or '-'):6} "
          f"{str(home or '(NONE)'):18} {b_written:>18} {b_schemaok:>21} {a:>7}   /{n_uni} in tenant")

for org, label, who, eid, home, bw, bs, a, n_uni in rows_six:
    if home:
        check(f"{who}: BEFORE (schema-corrected) saw the WHOLE tenant", bs == n_uni, f"{bs}/{n_uni}")
        check(f"{who}: AFTER sees exactly ONE store", a == 1, f"{a} stores")
        u = by_label(org, label)
        ks = eff_after(u)
        vis = [s["store_code"] for s in universe(org)
               if SO.in_keyset(ks, s.get("store_code"), s.get("address"))]
        check(f"{who}: and it is their roster home_store {home!r}",
              [str(v).strip().upper() for v in vis] == [home.strip().upper()], f"saw {vis}")
    else:
        check(f"{who}: BEFORE (schema-corrected) saw the WHOLE tenant", bs == n_uni, f"{bs}/{n_uni}")
        check(f"{who}: AFTER sees ZERO stores (no store anywhere -> sees nothing, not everything)",
              a == 0, f"{a} stores")

u_maria = by_label(LUX, "maria.gutierrez")
check("maria.gutierrez carries market='Chicago' in the fixture (the over-grant trap)",
      (u_maria.get("market") or "").strip() == "Chicago")
chicago_codes = CSCOPE.market_store_codes(FAKE, LUX, "Chicago")
check("that market really does resolve to many stores", len(chicago_codes) >= 20,
      f"{len(chicago_codes)} codes")
check("AFTER: the market is IGNORED for a self caller — keyset is empty, not the market's stores",
      eff_after(u_maria) == set(), f"{sorted(eff_after(u_maria))[:5]}")


print()
print("=" * 100)
print("C · CONTROLS — pinned reps, managers and admins must be UNCHANGED")
print("=" * 100)

pinned = [u for u in self_logins
          if str(u.get("store_code") or "").strip() or (u.get("store_codes") or [])]
check("fixture has real pinned self reps to control against", len(pinned) >= 20, f"{len(pinned)}")


def up(v):
    return str(v or "").strip().upper()


def pins_of(u):
    return [c for c in ([u.get("store_code")] + list(u.get("store_codes") or [])) if str(c or "").strip()]


def stores_named_by(org_id, tokens):
    """INDEPENDENT bound, computed straight off the raw fixture rather than through the code under
    test: the store_codes a set of raw tokens can LEGITIMATELY name — exact code, any recorded
    address spelling, or any recorded synonym. Nothing outside this set may ever become visible."""
    toks = {up(t) for t in tokens if up(t)}
    out = set()
    for s in FX["stores"]:
        if s["org_id"] == org_id and up(s.get("store_code")) and (
                up(s.get("store_code")) in toks or up(s.get("address")) in toks):
            out.add(up(s.get("store_code")))
    for m in FX["store_mapping"]:
        if m["org_id"] == org_id and up(m.get("store_code")) and (
                up(m.get("store_code")) in toks or up(m.get("store_address")) in toks):
            out.add(up(m.get("store_code")))
    for a in FX["store_aliases"]:
        if a["org_id"] == org_id and up(a.get("alias")) in toks:
            out.add(up(a.get("store_code")))
    return out


def visible_codes(ks, org_id):
    return {up(s["store_code"]) for s in universe(org_id)
            if SO.in_keyset(ks, s.get("store_code"), s.get("address"))}


escaped = []
for u in pinned:
    allowed = stores_named_by(u["org_id"], pins_of(u)) | {up(p) for p in pins_of(u)}
    got = visible_codes(eff_after(u), u["org_id"])
    if not (got <= allowed):
        escaped.append((u["label"], sorted(got - allowed)))
check("EVERY pinned self rep sees ONLY stores their OWN pins name (code / address / synonym) — "
      "no colleague's store, checked against the raw fixture, not against the code under test",
      not escaped, f"{len(pinned)} reps; escapes={escaped[:4]}")

same, restored, regressed = 0, [], []
for u in pinned:
    b = visible(eff_before(u, before_if_schema_ok), u["org_id"])
    a = visible(eff_after(u), u["org_id"])
    if a == b:
        same += 1
    elif a > b:
        restored.append((u["label"], pins_of(u)[:1], b, a))
    else:
        regressed.append((u["label"], b, a))
check("no pinned self rep LOSES a store to this fix", not regressed, f"regressed={regressed[:4]}")
check("a pinned self rep's visible-store count is otherwise unchanged vs the schema-corrected "
      "pre-fix code", same == len(pinned) - len(restored), f"same={same}/{len(pinned)}")
# The only movers are the logins pinned to an ADDRESS rather than a code: the pre-fix keyset was the
# bare pin string, which matched no store row, so they saw NOTHING. They now see THEIR OWN store.
for label, pin, b, a in restored:
    print(f"      · pinned-to-an-address restore: {label} pin={pin} {b} -> {a} store")
check("every mover went 0 -> exactly 1 store, and that store is one their own pin names",
      all(b == 0 and a == 1 for _l, _p, b, a in restored)
      and all(visible_codes(eff_after(by_label(
          [u for u in pinned if u["label"] == _l][0]["org_id"], _l)),
          [u for u in pinned if u["label"] == _l][0]["org_id"])
          <= stores_named_by([u for u in pinned if u["label"] == _l][0]["org_id"],
                             pins_of([u for u in pinned if u["label"] == _l][0]))
          for _l, _p, b, a in restored),
      f"{len(restored)} movers")

u_pin_over_roster = by_label(HOUSE, "avramos2005")
check("pin BEATS roster: avramos2005 is pinned B-5135 while the roster says B-1750",
      (u_pin_over_roster.get("store_code") == "B-5135" and home_of(u_pin_over_roster) == "B-1750"))
ks_pr = eff_after(u_pin_over_roster)
vis_pr = [s["store_code"] for s in universe(HOUSE)
          if SO.in_keyset(ks_pr, s.get("store_code"), s.get("address"))]
check("  → they see B-5135 (the pin), NOT B-1750 (the roster) — the roster is a FALLBACK only",
      [str(v).upper() for v in vis_pr] == ["B-5135"], f"saw {vis_pr}")

for org, label in [(HOUSE, "sanjot"), (HOUSE, "pw2022llc"), (HOUSE, "abid.akhter"),
                   (HOUSE, "ismaeelkhan2229"), (LUX, "rj"), (LUX, "brenda.romero"),
                   (LUX, "silvia.nava"), (LUX, "jose.utrera")]:
    u = by_label(org, label)
    b = visible(eff_before(u, before_if_schema_ok), org)
    b2 = visible(eff_before(u, before_as_written), org)
    a = visible(eff_after(u), org)
    check(f"{scope_of(u):6} {label:18} unchanged ({b} stores)", a == b == b2, f"{b2}/{b} -> {a}")


print()
print("=" * 100)
print("D · VOCABULARY — a roster home_store may be a CODE, an ADDRESS, a SYNONYM or nothing")
print("=" * 100)

ks_laura = eff_after(by_label(LUX, "lcricocareer37"))
check("Diversey (code) widens to its storeops address spelling",
      "4640 DIVERSEY CHICAGO" in ks_laura, sorted(ks_laura))
check("Diversey (code) widens to its DIVERGENT commcalc.store_mapping spelling",
      "4640-A W DIVERSEY AVE" in ks_laura, sorted(ks_laura))
check("Diversey does NOT reach any other Luxelink store's address",
      "3966 W GRAND AVE" not in ks_laura and "5601 W BELMONT AVE" not in ks_laura)

ks_ali = eff_after(by_label(HOUSE, "imkhaled1993"))
check("B-3PL widens to its store_mapping address '3 Palisade Ave'",
      "3 PALISADE AVE" in ks_ali, sorted(ks_ali))
check("B-3PL ALSO picks up its commcalc.store_aliases synonym '3 Palisade Ave Yonkers' "
      "(the POS spelling every attribution path already honours)",
      "3 PALISADE AVE YONKERS" in ks_ali, sorted(ks_ali))

ks_leslie = eff_after(by_label(HOUSE, "l2127martinez"))
check("B-1800 picks up its alias '1800 Great Neck rd'", "1800 GREAT NECK RD" in ks_leslie,
      sorted(ks_leslie))
check("…and still cannot reach B-3PL's synonym", "3 PALISADE AVE YONKERS" not in ks_leslie)

u_addr = by_label(LUX, "ramosbonilla19")
check("live login pinned to an ADDRESS, not a code: '7812 bergenline ave'",
      str(u_addr.get("store_code") or "").strip().lower() == "7812 bergenline ave")
ks_addr = eff_after(u_addr)
vis_addr = [s["store_code"] for s in universe(LUX)
            if SO.in_keyset(ks_addr, s.get("store_code"), s.get("address"))]
check("  → resolves through store_mapping back to store_code 7812 and shows exactly that store",
      [str(v).strip().upper() for v in vis_addr] == ["7812"], f"saw {vis_addr}")

u_unknown = by_label(LUX, "vanessa.jacobo")
check("live login pinned to a string that is in NO vocabulary: 'Floating'",
      str(u_unknown.get("store_code") or "").strip() == "Floating")
ks_unknown = eff_after(u_unknown)
check("  → degrades to the bare literal, which is a keyset of ONE and matches no real store",
      ks_unknown == {"FLOATING"} and visible(ks_unknown, LUX) == 0, f"{sorted(ks_unknown)}")


print()
print("=" * 100)
print("E · FAILURE DIRECTION — every failure NARROWS; none can widen")
print("=" * 100)


HEALTHY = {u["label"]: visible_codes(eff_after(u), u["org_id"]) for u in self_logins}


class Broken(FakeClient):
    """The store-resolution reads fail; the identity read still works."""

    def __init__(self, data, kill):
        super().__init__(data)
        self.kill = kill

    def schema(self, name):
        c = Broken(self.data, self.kill)
        c.schema_name = name
        return c

    def table(self, name):
        if f"{self.schema_name}.{name}" in self.kill:
            raise FakeAPIError(f"boom: {self.schema_name}.{name}")
        return super().table(name)


for kill, label in [({"storeops.employees"}, "roster read fails"),
                    ({"storeops.stores", "commcalc.store_mapping", "commcalc.store_aliases"},
                     "the whole store vocabulary fails"),
                    ({"storeops.employees", "storeops.stores", "commcalc.store_mapping",
                      "commcalc.store_aliases"}, "roster AND vocabulary fail")]:
    cc.sb = lambda k=kill: Broken(DATA, k)
    SO.sb = lambda k=kill: Broken(DATA, k).schema("storeops")
    SO.get_supabase = lambda k=kill: Broken(DATA, k)
    refresh_index()
    bad = []
    for u in self_logins:
        isf, ks = cc._caller_self_keyset(tok(u), u["org_id"])
        if not isf or ks is None:
            bad.append((u["label"], isf, ks))
        elif visible_codes(ks, u["org_id"]) > HEALTHY[u["label"]]:
            bad.append((u["label"], "widened", sorted(visible_codes(ks, u["org_id"]) - HEALTHY[u["label"]])))
    check(f"{label}: still (True, set()), and never wider than the healthy answer, for all "
          f"{len(self_logins)} self logins — never None",
          not bad, f"{bad[:4]}")

cc.sb = lambda: FAKE
SO.sb = lambda: FAKE.schema("storeops")
SO.get_supabase = lambda: FAKE
refresh_index()

check("a self login the roster cannot place is EMPTY, and empty means zero stores — not all",
      eff_after(by_label(LUX, "maria.gutierrez")) == set()
      and visible(set(), LUX) == 0 and visible(None, LUX) == len(universe(LUX)),
      f"empty->{visible(set(), LUX)}  None->{visible(None, LUX)}")


print()
print("=" * 100)
print("F · THE SCHEMA DEFECT — proof the pre-fix function was a no-op in production")
print("=" * 100)

raised = []
try:
    FAKE.table("app_users").select("role").eq("org_id", HOUSE).execute()
except FakeAPIError as e:
    raised.append(str(e))
check("sb().table('app_users') raises PGRST205 — public.app_users does not exist",
      raised and "PGRST205" in raised[0], raised[0] if raised else "did NOT raise")

raised2 = []
try:
    FAKE.table("stores").select("store_code,address").eq("org_id", HOUSE).execute()
except FakeAPIError as e:
    raised2.append(str(e))
check("sb().table('stores').eq('org_id',…) raises — public.stores has no org_id column",
      bool(raised2), raised2[0] if raised2 else "did NOT raise")

check("=> the PRE-FIX function returned (False, None) for EVERY self login, i.e. it never ran",
      all(before_as_written(u) == (False, None) for u in self_logins), f"{len(self_logins)} logins")
check("=> and the live symptom was a BLANK page: 0 stores for every self rep, pinned or not",
      all(visible(eff_before(u, before_as_written), u["org_id"]) == 0 for u in self_logins))
check("AFTER: the identity read reaches storeops.app_users and every self rep is recognised",
      all(cc._caller_self_keyset(tok(u), u["org_id"])[0] is True for u in self_logins))

placed = [u for u in self_logins if eff_after(u)]
real = [u for u in placed if visible(eff_after(u), u["org_id"]) >= 1]
check("AFTER: reps who can be placed in a real store now actually see it (My Targets stops being "
      "blank) — the only exception is the one login pinned to 'Floating', which is not a store",
      len(real) == len(placed) - 1
      and {u["label"] for u in placed if u not in real} == {"vanessa.jacobo"},
      f"{len(real)} see >=1 store; {len(placed)} placed; {len(self_logins)} self logins")

orphans = [u["label"] for u in self_logins if not eff_after(u)]
check("AFTER: exactly the logins with no store anywhere end up seeing nothing",
      set(orphans) == {"maria.gutierrez"}, f"orphans={sorted(orphans)}")


print()
print("=" * 100)
print("G · MULTI-TENANT — a keyset can never cross the org boundary")
print("=" * 100)

for u in self_logins:
    ks = eff_after(u)
    other = LUX if u["org_id"] == HOUSE else HOUSE
    if ks and visible(ks, other) > 0:
        check(f"{u['label']} leaks into the other tenant", False,
              f"{visible(ks, other)} stores in {other[:8]}")
        break
else:
    check("no self login's keyset matches ANY store in the other tenant",
          True, f"{len(self_logins)} logins x 2 tenants")

check("the identity read is org-scoped: the same token under the WRONG org_id is not self-scoped",
      all(cc._caller_self_keyset(tok(u), LUX if u["org_id"] == HOUSE else HOUSE) == (False, None)
          for u in self_logins))


print()
print("=" * 100)
print("H · THE REAL ENDPOINT — `get_targets_summary` driven end to end over the fixture")
print("=" * 100)

import asyncio                                                   # noqa: E402

for t in ("commcalc.targets", "commcalc.payout_config", "commcalc.rep_commissions",
          "commcalc.raw_sales", "commcalc.daily_sales_feed", "commcalc.store_daily_actuals",
          "storeops.shifts", "storeops.timelog", "storeops.app_config"):
    DATA.setdefault(t, [])
DATA["commcalc.targets"] = [{"org_id": LUX, "period": "August 2026", "store_code": c,
                             "accessories_monthly": 1000, "activations_monthly": 10}
                            for c in ("Diversey", "Grand", "Belmont")]


def call(label, org):
    refresh_index()
    u = by_label(org, label)
    return asyncio.new_event_loop().run_until_complete(
        cc.get_targets_summary("August 2026", authorization=tok(u), org_id=org,
                               stores=None, markets=None, reps=None, include_untargeted=True))


r_orphan = call("maria.gutierrez", LUX)
r_laura = call("lcricocareer37", LUX)
r_admin = call("rj", LUX)

print(f"      maria.gutierrez  stores={len(r_orphan['stores']):2}  scope={r_orphan['scope']}")
print(f"      Laura Rico       stores={len(r_laura['stores']):2}   scope={r_laura['scope']}")
print(f"      rj (admin)       stores={len(r_admin['stores']):2}   restricted={r_admin['scope']['restricted']}")

check("endpoint · the store-less rep gets ZERO stores", len(r_orphan["stores"]) == 0)
check("endpoint · …and the EXISTING setup_hint channel explains WHY, in plain English",
      any("No store is assigned to your login" in h for h in r_orphan["setup_hint"]),
      f"{r_orphan['setup_hint']}")
check("endpoint · …and the misleading generic hints are suppressed for them",
      len(r_orphan["setup_hint"]) == 1
      and not any("Store Matching" in h or "RBAC" in h for h in r_orphan["setup_hint"]),
      f"{r_orphan['setup_hint']}")
check("endpoint · the additive scope.no_store_assigned flag is set (lets the page render an "
      "explicit empty state instead of a dead picker)",
      r_orphan["scope"].get("no_store_assigned") is True, f"{r_orphan['scope']}")
check("endpoint · scope.restricted is TRUE for them — the span filter RAN (it used to be skipped)",
      r_orphan["scope"]["restricted"] is True and r_orphan["scope"]["self_scoped"] is True)

check("endpoint · the roster-placed rep gets exactly her OWN store", len(r_laura["stores"]) == 1
      and up(r_laura["stores"][0]["store_code"]) == "DIVERSEY",
      f"{[s['store_code'] for s in r_laura['stores']]}")
check("endpoint · …with no no_store_assigned flag and no store-less hint",
      r_laura["scope"].get("no_store_assigned") is False
      and not any("No store is assigned" in h for h in r_laura["setup_hint"]))

check("endpoint · the admin is unrestricted and unchanged",
      r_admin["scope"]["restricted"] is False
      and r_admin["scope"].get("no_store_assigned") is False
      and len(r_admin["stores"]) >= len(r_laura["stores"]),
      f"{len(r_admin['stores'])} stores")
check("endpoint · no money key is present on this payload — visibility only",
      not any(k in json.dumps(r_laura) for k in
              ('"total_payout"', '"final_payout"', '"commission"', '"paid_amount"', '"earned"')))


print()
print("=" * 100)
print(f"RESULT  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
print("=" * 100)
sys.exit(1 if FAIL else 0)
