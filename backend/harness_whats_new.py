"""Offline proof harness — WHAT'S NEW: new features + improvements for admin staff (mig 721).

OWNER DIRECTIVE 2026-08-04: "like we have the warnings for the admin who logs in, there should be 2 more
areas new features and improvements and keep them logged somewhere only for admin staff."

No database, no network: a recording fake Supabase client feeds the REAL router code. It proves

  A. PURE — entry normalization, the platform∪tenant resolver, the unseen watermark, category counts.
  B. MULTI-TENANT READ — platform-wide ∪ own, a tenant version winning, and NEVER a third tenant's row.
  C. MULTI-TENANT WRITE — a tenant admin is pinned to its own org, only a super-admin publishes
     platform-wide, and the body's org_id is ignored.
  D. GATES — it is the SAME gate as the login warnings (reused, not re-implemented): a rep sees nothing,
     an admin reads, only the edit grant writes, re-seed is super-admin only.
  E. INGEST — dual-auth, default DENY; the secret door is CLOSED when the env var is unset; the secret
     path can only ever publish PLATFORM-WIDE (it can never target a tenant).
  F. DEGRADES PRE-SQL — an un-run mig 721 returns an empty payload + hint on every route; the seeder is
     a silent no-op; nothing raises into the login path.
  G. SEEDER — never-clobber, idempotent, HOUSE-org only.
  H. CONTENT REVIEW — every seeded entry reads as plain English for an ADMIN, not a developer, and an
     unfinished item is marked in_progress instead of being announced as shipped.
  I. WIRING + ROUTE SURFACE — SEED_VERSION bumped, seeded on the house pass, 5 routes, none public.

Run:  cd backend && python3 harness_whats_new.py
"""
import asyncio
import inspect
import os
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from app.modules.core import whats_new as WN                  # noqa: E402
from app.modules.core import whats_new_seed as WS             # noqa: E402
from app.modules.core import entitlements as ENT              # noqa: E402

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


HOUSE = "00000000-0000-0000-0000-000000000001"
LUXE = "00000000-0000-0000-0000-0000000000ff"
OTHER = "00000000-0000-0000-0000-0000000000aa"
def run(x):
    """Call a route handler in either shape.

    NAV-PERF 2026-08-04: platform-core's zero-`await` route handlers were converted from `async def`
    to `def` so FastAPI runs them in the threadpool instead of on the single uvicorn event loop (an
    `async def` doing blocking Supabase I/O froze the whole product for its duration). Handlers are
    now plain functions, so this helper awaits a coroutine when it gets one and passes a plain result
    straight through — the harness works against BOTH shapes and needs no further edits if a handler
    ever legitimately becomes async again."""
    return asyncio.run(x) if inspect.isawaitable(x) else x


class _Q:
    def __init__(self, store, key, log, fail):
        self.store, self.key, self.log, self.fail = store, key, log, fail
        self.eqs, self.ins, self.cols = {}, {}, ""

    def select(self, cols="*", *a, **k):
        self.cols = cols
        return self

    def eq(self, k, v):
        self.eqs[k] = v
        return self

    def in_(self, k, vals):
        self.ins[k] = list(vals)
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def _boom(self, op):
        for pat in self.fail:
            if re.search(pat, self.key):
                raise RuntimeError(f"simulated PostgREST error on {op} {self.key}")

    def upsert(self, rows, on_conflict=None, **k):
        self._boom("upsert")
        rows = rows if isinstance(rows, list) else [rows]
        keys = (on_conflict or "").split(",") if on_conflict else []
        tbl = self.store.setdefault(self.key, [])
        for r in rows:
            hit = next((x for x in tbl if all(x.get(k2) == r.get(k2) for k2 in keys)), None) if keys else None
            if hit is not None:
                hit.update(r)
            else:
                tbl.append({"id": f"id-{len(tbl)+1}", **r})
        self.log.append({"op": "upsert", "key": self.key, "rows": rows})
        return self

    def delete(self, **k):
        self._boom("delete")
        self._mode = "delete"
        return self

    def _match(self, r):
        if not all(r.get(k) == v for k, v in self.eqs.items()):
            return False
        return all(r.get(k) in v for k, v in self.ins.items())

    def execute(self):
        if getattr(self, "_mode", None) == "delete":
            tbl = self.store.get(self.key, [])
            keep = [r for r in tbl if not self._match(r)]
            self.log.append({"op": "delete", "key": self.key, "eqs": dict(self.eqs)})
            self.store[self.key] = keep
            return SimpleNamespace(data=[])
        if self.cols:
            self._boom("select")
            self.log.append({"op": "select", "key": self.key, "eqs": dict(self.eqs), "ins": dict(self.ins)})
        return SimpleNamespace(data=[dict(r) for r in self.store.get(self.key, []) if self._match(r)])


class _S:
    def __init__(self, store, schema, log, fail):
        self.store, self.s, self.log, self.fail = store, schema, log, fail

    def table(self, t):
        return _Q(self.store, f"{self.s}.{t}", self.log, self.fail)


class Fake:
    def __init__(self, store=None, fail=()):
        self.store, self.log, self.fail = store if store is not None else {}, [], list(fail)

    def schema(self, s):
        return _S(self.store, s, self.log, self.fail)


def use(c):
    WN.sb = lambda: c


def as_caller(c):
    WN._caller = lambda a, b: c


SUPER = {"org_id": HOUSE, "role": "admin", "super_admin": True, "perms": {"scope": "all"}}
TADMIN = {"org_id": LUXE, "role": "admin", "super_admin": False, "perms": {"scope": "all"}}
TREP = {"org_id": LUXE, "role": "rep", "super_admin": False, "perms": {"scope": "own"}}


def note(org, slug, **kw):
    return {"id": f"{org[-2:]}-{slug}", "org_id": org, "slug": slug,
            "category": kw.get("category", "new_feature"), "module": kw.get("module", "closing"),
            "title": kw.get("title", slug), "body": "b", "status": kw.get("status", "shipped"),
            "released_at": kw.get("released_at", "2026-08-04"),
            "is_published": kw.get("is_published", True)}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nA. PURE")

e = WN.clean_entry({"title": "Something Good", "category": "nonsense", "status": "maybe",
                    "org_id": OTHER, "released_at": "nope", "extra": "dropped"})
ok("A1 the slug is derived from the title", e["slug"] == "something-good")
ok("A2 an unknown category falls back to new_feature", e["category"] == "new_feature")
ok("A3 an unknown status falls back to shipped", e["status"] == "shipped")
ok("A4 an org_id in the BODY is never carried through (RULE ONE)", "org_id" not in e)
ok("A5 a malformed date falls back to today", re.match(r"^\d{4}-\d{2}-\d{2}$", e["released_at"]) is not None)
ok("A6 unknown keys are dropped", "extra" not in e)

rows = [note(HOUSE, "a", title="Platform A"), note(LUXE, "a", title="Luxe A"), note(HOUSE, "b")]
res = WN.resolve_entries(rows, LUXE)
ok("A7 a tenant's own version overrides the platform entry of the same slug",
   [r["title"] for r in res if r["slug"] == "a"] == ["Luxe A"])
ok("A8 newest first", [r["slug"] for r in WN.resolve_entries(
    [note(HOUSE, "old", released_at="2026-01-01"), note(HOUSE, "new", released_at="2026-08-04")], HOUSE)]
   == ["new", "old"])
ok("A9 an unpublished entry never shows",
   WN.resolve_entries([note(HOUSE, "z", is_published=False)], HOUSE) == [])

feed = [note(HOUSE, "n1", released_at="2026-08-04"), note(HOUSE, "n2", released_at="2026-07-01",
                                                          category="improvement")]
ok("A10 no watermark ⇒ everything is unseen (correct first-run behaviour)", len(WN.unseen(feed, "")) == 2)
ok("A11 a watermark hides everything released on or before it", len(WN.unseen(feed, "2026-08-04")) == 0)
ok("A12 …and keeps what came after it", [x["slug"] for x in WN.unseen(feed, "2026-07-15")] == ["n1"])
cnt = WN.counts_by_category(feed)
ok("A13 counts split by category and total", cnt == {"new_feature": 1, "improvement": 1, "fix": 0, "total": 2}, cnt)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nB. MULTI-TENANT READ")

store = {"core.release_note": [note(HOUSE, "a"), note(LUXE, "a", title="Luxe A"), note(LUXE, "own"),
                              note(OTHER, "secret", title="OTHER TENANT")]}
fc = Fake(store)
use(fc)
as_caller(TADMIN)
r = run(WN.list_notes())
slugs = sorted(x["slug"] for x in r["entries"])
ok("B1 an admin sees the platform-wide entries ∪ its own", slugs == ["a", "own"])
ok("B2 …and NEVER another tenant's entry", "secret" not in slugs)
ok("B3 …its own version wins the shared slug",
   [x["title"] for x in r["entries"] if x["slug"] == "a"] == ["Luxe A"])
reads = [x for x in fc.log if x.get("op") == "select"]
ok("B4 the read is org-scoped to exactly {tenant, house}",
   sorted(reads[-1]["ins"].get("org_id", [])) == sorted([LUXE, HOUSE]))
ok("B5 ?category= filters", run(WN.list_notes(category="improvement"))["entries"] == [])
ok("B6 ?module= filters", run(WN.list_notes(module="asset"))["entries"] == [])
ok("B7 a date window filters", run(WN.list_notes(from_date="2027-01-01"))["entries"] == [])
ok("B8 ?since= returns the unseen slice and its counts",
   run(WN.list_notes(since="2026-08-04"))["unseen"] == []
   and run(WN.list_notes(since="2026-01-01"))["unseen_counts"]["total"] == 2)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nC. MULTI-TENANT WRITE")

ok("C1 a tenant admin is pinned to its own org", WN._write_org(TADMIN, HOUSE) == LUXE)
ok("C2 a super-admin may publish platform-wide", WN._write_org(SUPER, HOUSE) == HOUSE)
ok("C3 a super-admin may act as a tenant", WN._write_org(SUPER, LUXE) == LUXE)

fc2 = Fake({})
use(fc2)
as_caller(TADMIN)
res = run(WN.save_note({"title": "Our own note", "org_id": OTHER}))
saved = fc2.store["core.release_note"][0]
ok("C4 the saved entry carries the CALLER's org, not the body's", saved["org_id"] == LUXE)
ok("C5 …and is not platform-wide", res["is_platform_wide"] is False)
as_caller(SUPER)
res2 = run(WN.save_note({"title": "Platform note"}, org_id=HOUSE))
ok("C6 a super-admin CAN publish platform-wide", res2["is_platform_wide"] is True)
as_caller(TADMIN)
try:
    run(WN.save_note({"title": "  "}))
    ok("C7 an entry with no title is rejected", False)
except Exception as ex:
    ok("C7 an entry with no title is rejected", getattr(ex, "status_code", None) == 422)
run(WN.delete_note("ff-own", org_id=HOUSE))
d = [x for x in fc2.log if x.get("op") == "delete"]
ok("C8 DELETE is org-scoped to the caller's own org even when house is requested",
   d and d[-1]["eqs"].get("org_id") == LUXE)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nD. GATES — the SAME gate as the login warnings")

from app.modules.core.import_health import can_view_attention   # noqa: E402
for c, label in [(SUPER, "super-admin"), (TADMIN, "scope-all admin"), (TREP, "rep"), (None, "nobody")]:
    ok(f"D1 view gate matches the warnings gate exactly for a {label}",
       WN.can_view(c) == bool(can_view_attention(c)))
ok("D2 a rep cannot view", not WN.can_view(TREP))
ok("D3 a super-admin can edit", WN.can_edit(SUPER))
ok("D4 a scope-all admin can edit", WN.can_edit(TADMIN))
ok("D5 a rep cannot edit", not WN.can_edit(TREP))
ok("D6 an explicit role DENY on the whats_new setting wins over scope-all",
   not WN.can_edit({"org_id": LUXE, "role": "admin", "super_admin": False,
                    "perms": {"scope": "all", "settings": {"whats_new": False}}}))
ok("D7 a caller who cannot VIEW can never edit, whatever the setting says",
   not WN.can_edit({"org_id": LUXE, "role": "rep", "super_admin": False,
                    "perms": {"scope": "own", "settings": {"whats_new": True}}}))

use(Fake({"core.release_note": [note(HOUSE, "a")]}))
as_caller(TREP)
for fn, name in [(lambda: run(WN.list_notes()), "read the feed"),
                 (lambda: run(WN.save_note({"title": "x"})), "publish"),
                 (lambda: run(WN.delete_note("x")), "remove"),
                 (lambda: run(WN.reseed()), "re-seed")]:
    try:
        fn()
        ok(f"D8 a rep is refused: {name}", False)
    except Exception as ex:
        ok(f"D8 a rep is refused: {name}", getattr(ex, "status_code", None) == 403)
as_caller(TADMIN)
try:
    run(WN.reseed())
    ok("D9 re-seeding the platform pack is super-admin only", False)
except Exception as ex:
    ok("D9 re-seeding the platform pack is super-admin only", getattr(ex, "status_code", None) == 403)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nE. INGEST — dual-auth, default DENY")

os.environ.pop("RELEASE_NOTE_SECRET", None)
ok("E1 with no RELEASE_NOTE_SECRET set the secret door is CLOSED", not WN._secret_ok("anything"))
fc3 = Fake({})
use(fc3)
as_caller(None)
try:
    run(WN.ingest({"title": "sneak"}, x_release_secret="guess"))
    ok("E2 an unauthenticated caller with a bogus secret is refused", False)
except Exception as ex:
    ok("E2 an unauthenticated caller with a bogus secret is refused", getattr(ex, "status_code", None) == 403)
as_caller(TADMIN)
try:
    run(WN.ingest({"title": "tenant admin tries"}))
    ok("E3 even a tenant ADMIN cannot publish through the ship door", False)
except Exception as ex:
    ok("E3 even a tenant ADMIN cannot publish through the ship door", getattr(ex, "status_code", None) == 403)

os.environ["RELEASE_NOTE_SECRET"] = "s3cr3t-value"
ok("E4 a matching secret opens the door", WN._secret_ok("s3cr3t-value"))
ok("E5 a wrong secret does not", not WN._secret_ok("s3cr3t-valuf"))
as_caller(None)
r = run(WN.ingest({"entries": [{"title": "Shipped X", "org_id": OTHER},
                               {"title": ""}]}, x_release_secret="s3cr3t-value"))
ok("E6 the secret path publishes", r["published"] == 1 and r["skipped"] == 1 and r["via"] == "secret")
ok("E7 …ALWAYS platform-wide, never into a tenant even if the body says so",
   all(x["org_id"] == HOUSE for x in fc3.store["core.release_note"]))
ok("E8 …and is stamped as coming from the ship process",
   fc3.store["core.release_note"][0]["updated_by"] == "ship-process")
as_caller(SUPER)
r2 = run(WN.ingest({"title": "By hand"}))
ok("E9 a super-admin JWT is the other accepted door", r2["via"] == "super_admin")
os.environ.pop("RELEASE_NOTE_SECRET", None)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nF. DEGRADES PRE-SQL (mig 721 un-run)")

use(Fake({}, fail=[r"core\.release_note"]))
as_caller(TADMIN)
r = run(WN.list_notes())
ok("F1 the feed is an honest empty payload, not a 500", r["entries"] == [] and r["ready"] is False)
ok("F2 …and names the migration", "721" in (r.get("hint") or ""))
ok("F3 …with zero unseen, so the popup shows nothing extra", r["unseen_counts"]["total"] == 0)
try:
    run(WN.save_note({"title": "T"}))
    ok("F4 a publish before the migration fails with a migration hint", False)
except Exception as ex:
    ok("F4 a publish before the migration fails with a migration hint",
       getattr(ex, "status_code", None) == 500 and "721" in str(getattr(ex, "detail", "")))
ok("F5 the seeder is a silent no-op before the migration",
   WS.seed_release_notes(Fake({}, fail=[r"core\.release_note"]), HOUSE)["ok"] is False)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nG. SEEDER — never-clobber")

PACK = WS.load_seed_entries()
ok("G1 the bundled pack parses", len(PACK) >= 9, len(PACK))
fs = Fake({})
r1 = WS.seed_release_notes(fs, HOUSE)
ok("G2 a first seed inserts every entry", r1["inserted"] == len(PACK) and r1["ok"] is True, r1)
ok("G3 …into the HOUSE org only", all(x["org_id"] == HOUSE for x in fs.store["core.release_note"]))
ok("G4 …stamped is_seed + updated_by='seed'",
   all(x["is_seed"] and x["updated_by"] == "seed" for x in fs.store["core.release_note"]))
n = len(fs.store["core.release_note"])
r2 = WS.seed_release_notes(fs, HOUSE)
ok("G5 re-seeding is idempotent (refresh, never duplicate)",
   r2["updated"] == len(PACK) and len(fs.store["core.release_note"]) == n)
fs.store["core.release_note"][0]["updated_by"] = "owner@tenant.com"
fs.store["core.release_note"][0]["title"] = "HAND EDITED"
r3 = WS.seed_release_notes(fs, HOUSE)
ok("G6 a HAND-EDITED entry is skipped, never clobbered", r3["skipped"] == 1)
ok("G7 …and its wording survives", fs.store["core.release_note"][0]["title"] == "HAND EDITED")
ok("G8 an empty pack is a silent no-op", WS.seed_release_notes(Fake({}), HOUSE, entries=[])["ok"] is False)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nH. CONTENT REVIEW — written for an ADMIN, not a developer")

JARGON = re.compile(r"\b(endpoint|API|payload|org_id|JSON|SQL|schema|backend|frontend|migration|"
                    r"null|boolean|jsonb|RPC|middleware|repo|commit|localStorage|CSS|selector|DOM|"
                    r"refactor|deploy)\b", re.I)
seen = set()
for e in PACK:
    s = e["slug"]
    ok(f"H-{s}: slug is unique", s not in seen)
    seen.add(s)
    ok(f"H-{s}: a real category", e["category"] in WN.CATEGORIES)
    ok(f"H-{s}: an honest status", e.get("status") in WN.STATUSES)
    ok(f"H-{s}: a dated release", re.match(r"^\d{4}-\d{2}-\d{2}$", str(e.get("released_at"))) is not None)
    ok(f"H-{s}: title is one short plain line (≤ 90 chars)", 10 <= len(e["title"]) <= 90, len(e["title"]))
    ok(f"H-{s}: no developer jargon in the title", not JARGON.search(e["title"]))
    ok(f"H-{s}: body is 1–3 plain sentences (60–500 chars)", 60 <= len(e.get("body") or "") <= 500,
       len(e.get("body") or ""))
    ok(f"H-{s}: no developer jargon in the body", not JARGON.search(e.get("body") or ""))
    ok(f"H-{s}: a deep link that looks like a page", str(e.get("deep_link") or "/").startswith("/"))
cats = {e["category"] for e in PACK}
ok("H-COVERAGE the pack has BOTH new features and improvements (the two areas the owner asked for)",
   {"new_feature", "improvement"} <= cats, cats)
ok("H-HONESTY unfinished work is marked 'coming shortly', never announced as shipped",
   len([e for e in PACK if e.get("status") == "in_progress"]) >= 3)
ok("H-SHIPPED the finished work is present and marked live",
   len([e for e in PACK if e.get("status") == "shipped"]) >= 5)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nI. WIRING + ROUTE SURFACE")

ok("I1 SEED_VERSION was bumped to 9", ENT.SEED_VERSION == 9)
src = open(os.path.join(os.path.dirname(__file__), "app/modules/core/entitlements.py"), encoding="utf-8").read()
house_block = src.split("if org_id == ORG_ID:")[1]
ok("I2 the release-note seeder runs on the HOUSE org's sync pass only", "seed_release_notes" in house_block)
ok("I3 …wrapped so an un-run migration can never break a login",
   re.search(r"try:\s*\n\s*from app\.modules\.core\.whats_new_seed import seed_release_notes\s*\n"
             r"\s*seed_release_notes\(client, org_id\)\s*\n\s*except Exception:\s*\n\s*pass", house_block)
   is not None)
ok("I4 NO new entitlement module was invented (it is an admin surface, not billable)",
   "whats_new" not in ENT.MODULE_CATALOG and "release_note" not in ENT.MODULE_CATALOG)

from app.main import app                                            # noqa: E402
paths = sorted({r.path for r in app.routes if "whats-new" in r.path})
EXPECT = sorted(["/api/v1/core/whats-new", "/api/v1/core/whats-new/ingest",
                 "/api/v1/core/whats-new/seed", "/api/v1/core/whats-new/{note_id}"])
ok("I5 exactly the expected paths exist", paths == EXPECT, paths)
n_routes = len([r for r in app.routes if "whats-new" in getattr(r, "path", "")])
ok("I6 the package adds exactly 5 routes", n_routes == 5, n_routes)
from app.core import tenant_middleware as TM                        # noqa: E402
ok("I7 NO whats-new path is allowlisted as public today (the ship door needs a JWT until it is)",
   not any(TM._is_public(p) for p in paths))
expect_routes = int(os.environ.get("EXPECT_ROUTES", "1018"))
ok(f"I8 total app route count is {expect_routes} (1003 base + 7 training + 5 what's-new)",
   len(app.routes) == expect_routes, len(app.routes))

print(f"\n{'='*78}\n  {PASS} passed, {FAIL} failed\n{'='*78}")
sys.exit(1 if FAIL else 0)
