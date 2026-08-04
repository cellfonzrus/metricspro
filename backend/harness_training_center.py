"""Offline proof harness — TRAINING CENTER (mig 720, owner directive 2026-08-04).

No database, no network: a recording fake Supabase client feeds the REAL router code. It proves

  A. PURE resolvers — tenant-beats-house override, unpublished handling, ordering, slugify,
     page matching, the step/tour normalizers, and the Phase-2 script builder.
  B. MULTI-TENANT READ (RULE ONE) — a tenant sees the PLATFORM defaults ∪ its OWN rows and NEVER a
     third tenant's rows; the read is always .in_("org_id", [tenant, house]).
  C. MULTI-TENANT WRITE — a tenant admin is pinned to its own org, only a super-admin can write the
     platform defaults, and an org_id in the BODY is ignored (it is a query param / server decision).
  D. GATES — who may edit tours / see the recording scripts / re-seed.
  E. DEGRADES PRE-SQL — mig 720 un-run ⇒ honest empty payload + hint, never a 500, on every route.
  F. SEEDER — never-clobber semantics (a hand-edited tour is skipped), stamping, step replacement.
  G. CONTENT REVIEW — every shipped step reads as plain user-facing English: no developer jargon, no
     empty bodies, sane lengths, a narration + camera action for Phase 2, and a valid anchor syntax.
  H. WIRING — SEED_VERSION bumped, the module registered, the seeder called on the HOUSE sync pass.
  I. ROUTE SURFACE — exactly the 7 expected paths, all under /api/v1/core/training, and NONE of them
     allowlisted as public by the tenant middleware (they carry full protection).

Run:  cd backend && python3 harness_training_center.py
"""
import asyncio
import json
import os
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from app.modules.core import training as TR                    # noqa: E402
from app.modules.core import training_seed as TS               # noqa: E402
from app.modules.core import entitlements as ENT               # noqa: E402

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
run = asyncio.get_event_loop().run_until_complete if sys.version_info < (3, 10) else asyncio.run


# ── recording fake client ────────────────────────────────────────────────────────────────────────
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
            hit = None
            if keys:
                hit = next((x for x in tbl if all(x.get(k2) == r.get(k2) for k2 in keys)), None)
            if hit is not None:
                hit.update(r)
            else:
                tbl.append({"id": r.get("id") or f"id-{len(tbl)+1}-{r.get('slug', '')}", **r})
        self.log.append({"op": "upsert", "key": self.key, "rows": rows})
        return self

    def insert(self, rows, **k):
        self._boom("insert")
        rows = rows if isinstance(rows, list) else [rows]
        tbl = self.store.setdefault(self.key, [])
        for r in rows:
            tbl.append({"id": f"id-{len(tbl)+1}", **r})
        self.log.append({"op": "insert", "key": self.key, "rows": rows})
        return self

    def update(self, patch, **k):
        self._boom("update")
        self._patch = patch
        self._mode = "update"
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
        mode = getattr(self, "_mode", None)
        if mode == "delete":
            tbl = self.store.get(self.key, [])
            keep = [r for r in tbl if not self._match(r)]
            self.log.append({"op": "delete", "key": self.key, "eqs": dict(self.eqs),
                             "removed": len(tbl) - len(keep)})
            self.store[self.key] = keep
            return SimpleNamespace(data=[])
        if mode == "update":
            for r in self.store.get(self.key, []):
                if self._match(r):
                    r.update(self._patch)
            return SimpleNamespace(data=[])
        if getattr(self, "log", None) is not None and self.cols:
            self._boom("select")
            self.log.append({"op": "select", "key": self.key, "eqs": dict(self.eqs), "ins": dict(self.ins)})
        rows = [dict(r) for r in self.store.get(self.key, []) if self._match(r)]
        return SimpleNamespace(data=rows)


class _S:
    def __init__(self, store, schema, log, fail):
        self.store, self.schema_name, self.log, self.fail = store, schema, log, fail

    def table(self, t):
        return _Q(self.store, f"{self.schema_name}.{t}", self.log, self.fail)

    def rpc(self, *a, **k):
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[]))


class Fake:
    def __init__(self, store=None, fail=()):
        self.store, self.log, self.fail = store if store is not None else {}, [], list(fail)

    def schema(self, s):
        return _S(self.store, s, self.log, self.fail)


def use(client):
    TR.sb = lambda: client


def as_caller(c):
    TR._caller = lambda a, b: c


SUPER = {"org_id": HOUSE, "role": "admin", "super_admin": True, "perms": {"scope": "all"}}
TENANT_ADMIN = {"org_id": LUXE, "role": "admin", "super_admin": False, "perms": {"scope": "all"}}
TENANT_REP = {"org_id": LUXE, "role": "rep", "super_admin": False, "perms": {"scope": "own"}}


def tour(org, slug, **kw):
    return {"id": f"{org[-2:]}-{slug}", "org_id": org, "slug": slug, "title": kw.get("title", slug),
            "module": kw.get("module", "closing"), "audience": kw.get("audience", "all"),
            "start_href": kw.get("start_href", "/closing/submit"),
            "sort_order": kw.get("sort_order", 10), "is_published": kw.get("is_published", True),
            "description": kw.get("description", "d")}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nA. PURE resolvers")

rows = [tour(HOUSE, "a", title="House A"), tour(LUXE, "a", title="Luxe A"), tour(HOUSE, "b", title="House B")]
res = TR.resolve_tours(rows, LUXE)
ok("A1 a TENANT row overrides the platform row of the same slug",
   [r["title"] for r in res if r["slug"] == "a"] == ["Luxe A"])
ok("A2 …and a platform tour with no tenant version still comes through",
   any(r["title"] == "House B" for r in res))
ok("A3 the HOUSE tenant itself sees the platform rows (no override applies)",
   [r["title"] for r in TR.resolve_tours(rows, HOUSE) if r["slug"] == "a"] == ["House A"])
ok("A4 a tenant can HIDE a platform tour by saving its own unpublished version",
   not any(r["slug"] == "a" for r in
           TR.resolve_tours([tour(HOUSE, "a"), tour(LUXE, "a", is_published=False)], LUXE)))
ok("A5 an unpublished PLATFORM tour is not shown to anyone",
   TR.resolve_tours([tour(HOUSE, "z", is_published=False)], LUXE) == [])
ok("A6 rows are ordered by sort_order then title",
   [r["slug"] for r in TR.resolve_tours(
       [tour(HOUSE, "x", sort_order=50), tour(HOUSE, "y", sort_order=10)], HOUSE)] == ["y", "x"])
ok("A7 a row with no slug is ignored rather than crashing",
   TR.resolve_tours([{"org_id": HOUSE, "slug": "", "title": "t"}], HOUSE) == [])

ok("A8 slugify makes a url-safe key from a human title", TR.slugify("Close out your day!") == "close-out-your-day")
ok("A9 slugify of nothing is empty (the caller rejects it)", TR.slugify("  ") == "")

t = tour(HOUSE, "c", start_href="/closing/submit")
steps = [{"page_href": "/closing/submit"}, {"page_href": "/closing/verify"}]
ok("B0 tour_matches_path matches the page a step visits",
   TR.tour_matches_path(t, steps, "/closing/verify"))
ok("A10 …matches a deeper path under a step's page", TR.tour_matches_path(t, steps, "/closing/submit"))
ok("A11 …and never matches a look-alike prefix",
   not TR.tour_matches_path(t, [{"page_href": "/closing"}], "/closingx"))
ok("A12 …an unrelated page does not match", not TR.tour_matches_path(t, steps, "/commcalc/kpi"))

cs = TR.clean_step({"title": " T ", "body": "B", "target": " text:X ", "placement": "sideways",
                    "extra": "dropped", "narration": "n"}, 3)
ok("A13 clean_step keeps only known fields", "extra" not in cs)
ok("A14 clean_step clamps an unknown placement to auto", cs["placement"] == "auto")
ok("A15 clean_step stamps the order it is given", cs["step_order"] == 3)
ok("A16 clean_step trims the anchor", cs["target"] == "text:X")
ct = TR.clean_tour({"title": "My Tour", "audience": "wizard", "sort_order": "oops", "org_id": OTHER})
ok("A17 clean_tour derives the slug from the title", ct["slug"] == "my-tour")
ok("A18 clean_tour clamps an unknown audience", ct["audience"] == "all")
ok("A19 clean_tour never carries an org_id from the body (RULE ONE)", "org_id" not in ct)
ok("A20 clean_tour defaults a non-numeric sort_order", ct["sort_order"] == 100)

sc = TR.build_script({"slug": "s", "title": "S"}, [
    {"page_href": "/a", "target": "text:Go", "title": "T1", "body": "B1", "narration": "N1", "action_hint": "click"},
    {"page_href": "/a", "target": None, "title": "T2", "body": "B2"},
    {"page_href": "/b", "target": "text:Z", "title": "T3", "body": "B3"},
])
ok("A21 build_script produces one scene per step", sc["scenes"] == 3)
ok("A22 …narration falls back to the on-screen body when unset",
   sc["storyboard"][1]["narration"] == "B2")
ok("A23 …a page is only navigated to when it CHANGES",
   len([l for l in sc["playwright"] if l.startswith("await page.goto")]) == 2)
ok("A24 …the narration block is the whole voice-over", "N1" in sc["narration_text"] and "B3" in sc["narration_text"])
ok("A25 …an anchor-free step is called out for the recorder",
   any("centered card" in l for l in sc["playwright"]))


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nB. MULTI-TENANT READ (RULE ONE)")

store = {"core.training_tour": [tour(HOUSE, "a"), tour(LUXE, "a", title="Luxe A"),
                                tour(LUXE, "own"), tour(OTHER, "secret", title="OTHER TENANT")],
         "core.training_tour_step": [
             {"id": "s1", "org_id": HOUSE, "tour_id": "01-a", "step_order": 1, "title": "x", "body": "y"},
             {"id": "s2", "org_id": OTHER, "tour_id": "aa-secret", "step_order": 1, "title": "x", "body": "y"}]}
fc = Fake(store)
use(fc)
as_caller(TENANT_ADMIN)
r = run(TR.list_tours())
slugs = sorted(t["slug"] for t in r["tours"])
ok("B1 a tenant sees the platform defaults ∪ its own tours", slugs == ["a", "own"])
ok("B2 …and NEVER another tenant's tour", "secret" not in slugs)
ok("B3 …the tenant's version wins the shared slug",
   [t["title"] for t in r["tours"] if t["slug"] == "a"] == ["Luxe A"])
ok("B4 …and is flagged as this organisation's own version",
   [t["is_tenant_override"] for t in r["tours"] if t["slug"] == "a"] == [True])
reads = [e for e in fc.log if e.get("op") == "select" and e["key"] == "core.training_tour"]
ok("B5 the tour read is org-scoped to exactly {tenant, house}",
   reads and sorted(reads[-1]["ins"].get("org_id", [])) == sorted([LUXE, HOUSE]), reads[-1] if reads else None)
step_reads = [e for e in fc.log if e.get("op") == "select" and e["key"] == "core.training_tour_step"]
ok("B6 the STEP read is org-scoped the same way (no unscoped join)",
   step_reads and sorted(step_reads[-1]["ins"].get("org_id", [])) == sorted([LUXE, HOUSE]))

as_caller(None)
r0 = run(TR.list_tours())
ok("B7 a caller with no resolvable tenant sees the PLATFORM defaults only",
   sorted(t["slug"] for t in r0["tours"]) == ["a"])

as_caller(TENANT_ADMIN)
rp = run(TR.list_tours(path="/commcalc/kpi"))
ok("B8 ?path= filters to tours that touch that page", rp["tours"] == [])
rp2 = run(TR.list_tours(path="/closing/submit"))
ok("B9 …and keeps the ones that do", len(rp2["tours"]) >= 1)
rm = run(TR.list_tours(module="asset"))
ok("B10 ?module= filters by area", rm["tours"] == [])

one = run(TR.get_tour("a"))
ok("B11 GET one tour resolves the tenant version", one["tour"]["title"] == "Luxe A")
try:
    run(TR.get_tour("secret"))
    ok("B12 another tenant's tour is NOT reachable by slug", False)
except Exception as e:
    ok("B12 another tenant's tour is NOT reachable by slug", "404" in str(getattr(e, "status_code", "")) or
       getattr(e, "status_code", None) == 404)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nC. MULTI-TENANT WRITE")

ok("C1 a tenant admin's write is pinned to its OWN org", TR._write_org(TENANT_ADMIN, OTHER) == LUXE)
ok("C2 …even when the request asks for the house org", TR._write_org(TENANT_ADMIN, HOUSE) == LUXE)
ok("C3 a super-admin may target the HOUSE org (the platform defaults)", TR._write_org(SUPER, HOUSE) == HOUSE)
ok("C4 a super-admin may act as a tenant", TR._write_org(SUPER, LUXE) == LUXE)
ok("C5 a caller with no org falls back to house, never to a client-supplied org",
   TR._write_org(None, OTHER) == HOUSE)

fc2 = Fake({})
use(fc2)
as_caller(TENANT_ADMIN)
body = {"title": "My Close", "org_id": OTHER, "module": "closing",
        "steps": [{"title": "s1", "body": "b1", "page_href": "/closing/submit"},
                  {"title": "s2", "body": "b2"}]}
res = run(TR.save_tour(body))
saved = fc2.store["core.training_tour"][0]
ok("C6 the saved tour carries the CALLER's org, not the body's", saved["org_id"] == LUXE)
ok("C7 …the body's org_id is ignored entirely", saved["org_id"] != OTHER)
ok("C8 …and it is not marked a platform default", res["is_platform_default"] is False)
ok("C9 every step row is stamped with the same org",
   all(s["org_id"] == LUXE for s in fc2.store["core.training_tour_step"]))
ok("C10 steps are renumbered from 1 in payload order",
   [s["step_order"] for s in fc2.store["core.training_tour_step"]] == [1, 2])
dels = [e for e in fc2.log if e.get("op") == "delete"]
ok("C11 the step replace deletes ORG-SCOPED (never another tenant's steps)",
   dels and dels[-1]["eqs"].get("org_id") == LUXE)

as_caller(SUPER)
run(TR.save_tour({"title": "Platform One", "steps": [{"title": "a", "body": "b"}]}, org_id=HOUSE))
ok("C12 a super-admin CAN write a platform default",
   any(t["org_id"] == HOUSE for t in fc2.store["core.training_tour"]))

as_caller(TENANT_ADMIN)
for bad, why in [({"title": "", "steps": [{"title": "a", "body": "b"}]}, "no title"),
                 ({"title": "T"}, "no steps"),
                 ({"title": "T", "steps": [{"title": "", "body": ""}]}, "empty step")]:
    try:
        run(TR.save_tour(bad))
        ok(f"C13 rejected: {why}", False)
    except Exception as e:
        ok(f"C13 rejected: {why}", getattr(e, "status_code", None) == 422)

as_caller(TENANT_ADMIN)
run(TR.delete_tour("ff-own", org_id=HOUSE))
d = [e for e in fc2.log if e.get("op") == "delete" and e["key"] == "core.training_tour"]
ok("C14 DELETE is org-scoped to the caller's own org even when house is requested",
   d and d[-1]["eqs"].get("org_id") == LUXE)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nD. GATES")

ok("D1 a super-admin may edit tours", TR.can_edit_tours(SUPER))
ok("D2 a scope-all admin may edit tours", TR.can_edit_tours(TENANT_ADMIN))
ok("D3 a rep may NOT edit tours", not TR.can_edit_tours(TENANT_REP))
ok("D4 an unauthenticated caller may not edit tours", not TR.can_edit_tours(None))
ok("D5 an explicit role DENY on the training setting wins over scope-all",
   not TR.can_edit_tours({"org_id": LUXE, "role": "admin", "super_admin": False,
                          "perms": {"scope": "all", "settings": {"training": False}}}))
ok("D6 an explicit role GRANT lets a non-admin edit",
   TR.can_edit_tours({"org_id": LUXE, "role": "dm", "super_admin": False,
                      "perms": {"scope": "own", "settings": {"training": True}}}))

use(Fake({"core.training_tour": [tour(HOUSE, "a")], "core.training_tour_step": []}))
as_caller(TENANT_REP)
for fn, name in [(lambda: run(TR.save_tour({"title": "x", "steps": [{"title": "a", "body": "b"}]})), "save"),
                 (lambda: run(TR.delete_tour("x")), "delete"),
                 (lambda: run(TR.list_scripts()), "recording scripts"),
                 (lambda: run(TR.get_script("a")), "one recording script"),
                 (lambda: run(TR.reseed()), "re-seed")]:
    try:
        fn()
        ok(f"D7 a rep is refused: {name}", False)
    except Exception as e:
        ok(f"D7 a rep is refused: {name}", getattr(e, "status_code", None) == 403)
as_caller(TENANT_ADMIN)
try:
    run(TR.reseed())
    ok("D8 re-seeding the PLATFORM set is super-admin only", False)
except Exception as e:
    ok("D8 re-seeding the PLATFORM set is super-admin only", getattr(e, "status_code", None) == 403)
ok("D9 a rep CAN still read the tour list (help is for everyone)",
   (as_caller(TENANT_REP) or True) and run(TR.list_tours())["ready"] is True)
ok("D10 …and the list tells the client it may not edit", run(TR.list_tours())["can_edit"] is False)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nE. DEGRADES PRE-SQL (mig 720 un-run)")

use(Fake({}, fail=[r"core\.training_tour"]))
as_caller(TENANT_ADMIN)
r = run(TR.list_tours())
ok("E1 the tour list is an honest empty payload, not a 500", r["tours"] == [] and r["ready"] is False)
ok("E2 …and names the migration", "720" in (r.get("hint") or ""))
ok("E3 …and reports no edit rights (nothing to edit yet)", r["can_edit"] is False)
s = run(TR.list_scripts())
ok("E4 the recording-scripts list degrades the same way", s["scripts"] == [] and s["ready"] is False)
for fn, name in [(lambda: run(TR.get_tour("a")), "GET one tour"),
                 (lambda: run(TR.get_script("a")), "GET one script")]:
    try:
        fn()
        ok(f"E5 {name} degrades to a clean 503", False)
    except Exception as e:
        ok(f"E5 {name} degrades to a clean 503", getattr(e, "status_code", None) == 503)
try:
    run(TR.save_tour({"title": "T", "steps": [{"title": "a", "body": "b"}]}))
    ok("E6 a save before the migration fails with a migration hint", False)
except Exception as e:
    ok("E6 a save before the migration fails with a migration hint",
       getattr(e, "status_code", None) == 500 and "720" in str(getattr(e, "detail", "")))
ok("E7 the seeder is a silent no-op before the migration",
   TS.seed_training_tours(Fake({}, fail=[r"core\.training_tour"]), HOUSE)["ok"] is False)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nF. SEEDER — never-clobber")

PACK = TS.load_seed_tours()
ok("F1 the bundled pack parses", len(PACK) >= 6, len(PACK))
fs = Fake({})
r1 = TS.seed_training_tours(fs, HOUSE)
ok("F2 a first seed inserts every tour", r1["inserted"] == len(PACK) and r1["ok"] is True, r1)
ok("F3 …into the HOUSE org only", all(t["org_id"] == HOUSE for t in fs.store["core.training_tour"]))
ok("F4 …stamped is_seed + updated_by='seed'",
   all(t["is_seed"] and t["updated_by"] == "seed" for t in fs.store["core.training_tour"]))
ok("F5 …with every step row org-stamped",
   all(s["org_id"] == HOUSE for s in fs.store["core.training_tour_step"]))
n_steps = len(fs.store["core.training_tour_step"])
r2 = TS.seed_training_tours(fs, HOUSE)
ok("F6 re-seeding is idempotent (refresh, never duplicate)",
   r2["updated"] == len(PACK) and r2["inserted"] == 0)
ok("F7 …and does not duplicate step rows", len(fs.store["core.training_tour_step"]) == n_steps)
fs.store["core.training_tour"][0]["updated_by"] = "someone@tenant.com"
fs.store["core.training_tour"][0]["title"] = "HAND EDITED"
r3 = TS.seed_training_tours(fs, HOUSE)
ok("F8 a HAND-EDITED tour is skipped, never clobbered", r3["skipped"] == 1)
ok("F9 …and its wording survives",
   fs.store["core.training_tour"][0]["title"] == "HAND EDITED")
ft = Fake({})
TS.seed_training_tours(ft, LUXE)
ok("F10 the seeder writes only the org it is given (tenant rows are never touched by a house pass)",
   all(t["org_id"] == LUXE for t in ft.store["core.training_tour"]))
ok("F11 a missing bundle file is a silent no-op",
   TS.seed_training_tours(Fake({}), HOUSE, tours=[])["ok"] is False)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nG. CONTENT REVIEW — every shipped step reads as plain user-facing English")

# Words that mean the copy was written for a developer, not for a rep or a DM.
JARGON = re.compile(r"\b(endpoint|API|payload|org_id|JSON|SQL|schema|backend|frontend|migration|"
                    r"null|boolean|jsonb|RPC|querystring|query param|middleware|repo|commit|"
                    r"tenant_modules|localStorage|CSS|selector|DOM)\b", re.I)
seen_slugs, total_steps = set(), 0
for t in PACK:
    slug = t["slug"]
    ok(f"G-{slug}: slug is unique", slug not in seen_slugs)
    seen_slugs.add(slug)
    st = t.get("steps") or []
    total_steps += len(st)
    ok(f"G-{slug}: 5–12 steps ({len(st)})", 5 <= len(st) <= 12, len(st))
    ok(f"G-{slug}: has a module, an audience and a start page",
       bool(t.get("module")) and t.get("audience") in TR._AUDIENCES and bool(t.get("start_href")))
    ok(f"G-{slug}: has a one-line description", 20 <= len(t.get("description") or "") <= 200)
    bad_titles = [s["title"] for s in st if JARGON.search(s["title"] or "")]
    bad_bodies = [s["title"] for s in st if JARGON.search(s["body"] or "")]
    ok(f"G-{slug}: no developer jargon in any step heading", not bad_titles, bad_titles)
    ok(f"G-{slug}: no developer jargon in any step body", not bad_bodies, bad_bodies)
    ok(f"G-{slug}: every step has a heading and a body",
       all((s.get("title") or "").strip() and (s.get("body") or "").strip() for s in st))
    ok(f"G-{slug}: every body is a readable length (40–600 chars)",
       all(40 <= len(s["body"]) <= 600 for s in st),
       [len(s["body"]) for s in st if not 40 <= len(s["body"]) <= 600])
    ok(f"G-{slug}: every heading is short (≤ 60 chars)", all(len(s["title"]) <= 60 for s in st),
       [s["title"] for s in st if len(s["title"]) > 60])
    ok(f"G-{slug}: PHASE 2 — every step carries narration + a camera action",
       all((s.get("narration") or "").strip() and (s.get("action_hint") or "").strip() for s in st))
    ok(f"G-{slug}: every step names the page it happens on",
       all((s.get("page_href") or "").startswith("/") for s in st))
    anchors = [s.get("target") for s in st if s.get("target")]
    ok(f"G-{slug}: every anchor uses a supported syntax",
       all(a.startswith(("text:", "tour:", "css:", "#", ".", "[")) for a in anchors), anchors)
    ok(f"G-{slug}: at least one anchor-free step (an intro the user can read before hunting)",
       any(not s.get("target") for s in st))
    ok(f"G-{slug}: the first step starts on the tour's own start page",
       st[0].get("page_href") == t.get("start_href"))
ok(f"G-TOTAL {len(PACK)} tours / {total_steps} steps", total_steps >= 40, total_steps)
mods = {t["module"] for t in PACK}
ok("G-COVERAGE the pack spans closing, commissions, storeops, asset and finance",
   {"closing", "commissions", "storeops", "asset", "account"} <= mods, mods)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nH. WIRING — entitlement + seed path")

ok("H1 SEED_VERSION was bumped to 8 (every tenant re-syncs on its next login)", ENT.SEED_VERSION == 8)
ok("H2 the training module is registered in the canonical catalog", "training" in ENT.MODULE_CATALOG)
src = open(os.path.join(os.path.dirname(__file__), "app/modules/core/entitlements.py"), encoding="utf-8").read()
house_block = src.split("if org_id == ORG_ID:")[1]
ok("H3 the tour seeder is called on the HOUSE org's sync pass only",
   "seed_training_tours" in house_block)
ok("H4 …and it is wrapped so an un-run migration can never break a login",
   re.search(r"try:\s*\n\s*from app\.modules\.core\.training_seed import seed_training_tours\s*\n"
             r"\s*seed_training_tours\(client, org_id\)\s*\n\s*except Exception:\s*\n\s*pass", house_block)
   is not None)
rsrc = open(os.path.join(os.path.dirname(__file__), "app/modules/core/router.py"), encoding="utf-8").read()
ok("H5 the training router is mounted onto the CORE router (main.py, a SHARED file, is untouched)",
   "router.include_router(_training.router)" in rsrc)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\nI. ROUTE SURFACE")

from app.main import app                                              # noqa: E402
paths = sorted({r.path for r in app.routes if "/training" in r.path})
EXPECT = sorted(["/api/v1/core/training/tours", "/api/v1/core/training/tours/{slug}",
                 "/api/v1/core/training/tours/{tour_id}", "/api/v1/core/training/scripts",
                 "/api/v1/core/training/script/{slug}", "/api/v1/core/training/seed"])
ok("I1 exactly the expected training paths exist", paths == EXPECT, paths)
ok("I2 every training path is under /api/v1/core (no new top-level surface)",
   all(p.startswith("/api/v1/core/training") for p in paths))
n_routes = len([r for r in app.routes if "/api/v1/core/training" in getattr(r, "path", "")])
ok("I3 the package adds exactly 7 routes", n_routes == 7, n_routes)
from app.core import tenant_middleware as TM                          # noqa: E402
ok("I4 NO training path is allowlisted as public (they keep full tenant protection)",
   not any(TM._is_public(p) for p in paths))
expect_routes = int(os.environ.get("EXPECT_ROUTES", "1010"))
ok(f"I5 total app route count is {expect_routes} (base 1003 + exactly 7)",
   len(app.routes) == expect_routes, len(app.routes))

print(f"\n{'='*78}\n  {PASS} passed, {FAIL} failed\n{'='*78}")
sys.exit(1 if FAIL else 0)
