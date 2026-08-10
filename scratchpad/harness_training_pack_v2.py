"""HARNESS — the 2026-08-10 training pack (v2) is loadable, seedable and honest.

Runs the REAL cleaners and the REAL seeder against the bundled pack, so a malformed step, a stripped
href or a tour that would silently lose steps fails here rather than on a tenant's screen.

Run:  python3 scratchpad/harness_training_pack_v2.py     (from the worktree root)
"""
import io, json, os, sys, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
PACK = os.path.join(ROOT, "backend", "app", "data", "training_tours_seed.json")

from app.modules.core.training import (  # noqa: E402
    clean_tour, clean_step, build_script, resolve_tours, tour_matches_path,
)
from app.modules.core.training_seed import load_seed_tours, seed_training_tours  # noqa: E402

HOUSE = "00000000-0000-0000-0000-000000000001"
TENANT = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
NEW_SLUGS = ["pos-setup-wizard", "pos-sales-tax", "pos-register-sale",
             "pos-stock-and-activations", "pos-who-can-use-it", "imports-and-daily-uploads"]
FAILS = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


raw_pack = json.load(io.open(PACK, encoding="utf-8"))
tours = load_seed_tours(PACK)
by_slug = {t["slug"]: t for t in tours}

check("the pack parses and carries every tour", len(tours) == len(raw_pack["tours"]) == 12,
      f"{len(tours)} tours")
check("the six new walk-throughs are present", all(s in by_slug for s in NEW_SLUGS),
      str([s for s in NEW_SLUGS if s not in by_slug]))
check("no slug is duplicated", len({t["slug"] for t in tours}) == len(tours))

# ── every step survives the cleaners the writer path uses ────────────────────────────────────────
for slug in NEW_SLUGS:
    raw = by_slug[slug]
    t = clean_tour(raw)
    steps = [clean_step(s, i) for i, s in enumerate(raw["steps"], 1)]
    kept = [s for s in steps if s["title"] and s["body"]]
    check(f"{slug}: no step is dropped by clean_step", len(kept) == len(raw["steps"]),
          f"{len(kept)}/{len(raw['steps'])}")
    check(f"{slug}: every page_href survives safe_href",
          all(k["page_href"] == r.get("page_href") for k, r in zip(kept, raw["steps"])))
    check(f"{slug}: start_href survives safe_href", t["start_href"] == raw["start_href"])
    check(f"{slug}: every step has narration (the recording script)",
          all(s.get("narration") for s in kept))
    sc = build_script(t, kept)
    check(f"{slug}: renders a complete storyboard", sc["scenes"] == len(kept) and sc["narration_text"])

# ── the honesty rule: where a module is incomplete, the step must SAY SO ─────────────────────────
# Not decoration. The owner's instruction for this pack was that where a module is incomplete the
# training states it plainly rather than describing a flow that does not work, so it is asserted.
CAVEAT_PHRASES = ("not finished", "has not been built", "not on screen yet", "known fault",
                  "known problem", "had little real use", "does not yet", "until you turn it on",
                  "used to go green", "is not the same as being finished")
warned = [(slug, s["title"]) for slug in NEW_SLUGS for s in by_slug[slug]["steps"]
          if any(p in (s["title"] + " " + s["body"] + " " + (s.get("narration") or "")).lower()
                 for p in CAVEAT_PHRASES)]
check("incomplete areas are stated in the content, not glossed over", len(warned) >= 5,
      f"{len(warned)} explicit caveats: {[t for _, t in warned]}")
check("every POS walk-through that touches an unfinished area carries at least one caveat",
      {slug for slug, _ in warned} >= {"pos-setup-wizard", "pos-sales-tax",
                                       "pos-stock-and-activations", "pos-who-can-use-it",
                                       "imports-and-daily-uploads"},
      str(sorted({slug for slug, _ in warned})))

# ── multi-tenancy: platform rows are readable by a tenant, overridable, never leaked ─────────────
house_rows = [{**clean_tour(t), "org_id": HOUSE, "id": f"h-{t['slug']}"} for t in tours]
tenant_override = {**clean_tour(by_slug["pos-sales-tax"]), "org_id": TENANT, "id": "t-1",
                   "title": "OUR OWN tax walk-through"}
other_tenant = {**clean_tour(by_slug["pos-sales-tax"]), "org_id": "99999999-9999-9999-9999-999999999999",
                "id": "x-1", "title": "SOMEONE ELSE'S"}

res = resolve_tours(house_rows, TENANT)
check("a tenant with no overrides reads all 12 platform tours", len(res) == 12, str(len(res)))
res2 = resolve_tours(house_rows + [tenant_override], TENANT)
check("a tenant override WINS over the platform row of the same slug",
      len(res2) == 12 and any(r["title"] == "OUR OWN tax walk-through" for r in res2))
check("LEAK CONTROL: another tenant's row is never resolved into this tenant's list",
      all(r["id"] != "x-1" for r in resolve_tours(house_rows + [other_tenant], TENANT)))

# ── the help panel's "walk me through this page" match ───────────────────────────────────────────
for path, slug in [("/pos/settings", "pos-sales-tax"), ("/pos/sales", "pos-register-sale"),
                   ("/pos/onboarding", "pos-setup-wizard"), ("/admin/roles", "pos-who-can-use-it"),
                   ("/commcalc/connectors", "imports-and-daily-uploads")]:
    t = by_slug[slug]
    check(f"{path} offers '{slug}'", tour_matches_path(clean_tour(t), t["steps"], path))
check("NEGATIVE CONTROL: /hr/people offers none of the POS walk-throughs",
      not any(tour_matches_path(clean_tour(by_slug[s]), by_slug[s]["steps"], "/hr/people")
              for s in NEW_SLUGS if s != "pos-who-can-use-it"))

# ── the seeder: writes HOUSE only, never clobbers an edited tour ─────────────────────────────────
STORE = {"tour": [], "step": []}


class _Q:
    def __init__(self, t):
        self.t, self.f, self.rows_in = t, [], None

    def select(self, *_a, **_k):
        return self

    def eq(self, c, v):
        self.f.append((c, v)); return self

    def limit(self, *_a):
        return self

    def upsert(self, row, **_k):
        self.rows_in = ("upsert", row); return self

    def insert(self, rows):
        self.rows_in = ("insert", rows); return self

    def delete(self):
        self.rows_in = ("delete", None); return self

    def execute(self):
        key = "tour" if self.t == "training_tour" else "step"
        if self.rows_in and self.rows_in[0] == "upsert":
            row = self.rows_in[1]
            STORE[key] = [r for r in STORE[key]
                          if not (r.get("org_id") == row.get("org_id") and r.get("slug") == row.get("slug"))]
            STORE[key].append({**row, "id": f"id-{row.get('slug')}"})
            return types.SimpleNamespace(data=[STORE[key][-1]])
        if self.rows_in and self.rows_in[0] == "insert":
            STORE[key].extend(self.rows_in[1]); return types.SimpleNamespace(data=self.rows_in[1])
        rows = STORE[key]
        for c, v in self.f:
            rows = [r for r in rows if r.get(c) == v]
        if self.rows_in and self.rows_in[0] == "delete":
            for r in rows:
                STORE[key].remove(r)
            return types.SimpleNamespace(data=rows)
        return types.SimpleNamespace(data=[dict(r) for r in rows])


class Fake:
    def schema(self, _n):
        return types.SimpleNamespace(table=lambda t: _Q(t))


r1 = seed_training_tours(Fake(), HOUSE, tours)
check("first seed writes all 12 tours to the HOUSE org", r1["inserted"] == 12 and r1["ok"], str(r1))
check("every seeded tour row is stamped with the house org",
      all(t["org_id"] == HOUSE for t in STORE["tour"]))
check("every seeded STEP row is stamped with the house org too (write-side scoping)",
      STORE["step"] and all(s["org_id"] == HOUSE for s in STORE["step"]))

r2 = seed_training_tours(Fake(), HOUSE, tours)
check("re-seeding is idempotent (updates, never duplicates)",
      r2["updated"] == 12 and r2["inserted"] == 0 and len(STORE["tour"]) == 12, str(r2))

for t in STORE["tour"]:
    if t["slug"] == "pos-sales-tax":
        t["updated_by"] = "a-human"
r3 = seed_training_tours(Fake(), HOUSE, tours)
check("NEVER-CLOBBER: a hand-edited tour is skipped by the reseed",
      r3["skipped"] == 1 and r3["updated"] == 11, str(r3))
check("...and the human's row is still the one stored",
      any(t["slug"] == "pos-sales-tax" and t.get("updated_by") == "a-human" for t in STORE["tour"]))

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}")
sys.exit(1 if FAILS else 0)
