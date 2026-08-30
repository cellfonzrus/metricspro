"""Offline harness for the POS catalog SUGGESTION engine (no DB, no network).

Feeds SYNTHETIC device/plan/item strings and asserts the deterministic classification, the preset
assembly, the cross-org "learned taxonomy" privacy rule (a label surfaces only when ≥2 orgs share
it), and the idempotent apply path through a Supabase-style stub client.

Run:  cd backend && python harness_catalog_suggest.py
"""
import sys

from app.modules.pos import catalog_suggest as C

_p = _f = 0


def ok(name, cond):
    global _p, _f
    if cond:
        _p += 1; print(f"  ok   {name}")
    else:
        _f += 1; print(f"  FAIL {name}")


def main():
    # ── classifier ────────────────────────────────────────────────────────────────────────────────
    ok("maker canonicalises case", C._maker("APPLE IPHONE 15") == "Apple")
    ok("maker MOTO → Motorola", C._maker("MOTO G STYLUS") == "Motorola")
    ok("maker unknown first token title-cased", C._maker("celero 5g") == "Celero")
    ok("iphone → Smartphones", C._device_type("APPLE IPHONE 15 PRO") == "Smartphones")
    ok("ipad → Tablets", C._device_type("APPLE IPAD 10TH GEN") == "Tablets")
    ok("watch → Wearables", C._device_type("SAMSUNG GALAXY WATCH 6") == "Wearables")
    ok("duraxv → Basic Phones", C._device_type("KYOCERA DURAXV EXTREME") == "Basic Phones")
    ok("hotspot → Hotspots", C._device_type("INSEEGO MIFI HOTSPOT") == "Hotspots")
    ok("unknown model defaults to Smartphones", C._device_type("SOME NEW THING") == "Smartphones")

    # ── derive_catalog ────────────────────────────────────────────────────────────────────────────
    devices = ["APPLE IPHONE 15", "APPLE IPHONE 14", "SAMSUNG GALAXY S25", "SAMSUNG GALAXY A15",
               "KYOCERA DURAXV", "APPLE IPAD 10TH GEN", "SAMSUNG GALAXY WATCH 6"]
    plans = ["Unlimited Plus", "Prepaid 15GB", "Unlimited Plus"]
    products = ["OtterBox Defender Case", "Tempered Glass Screen Protector", "USB-C Wall Charger",
                "Random Widget"]
    d = C.derive_catalog(devices, plans, products)
    ok("apple most frequent maker first", d["manufacturers"][0] == "Apple")
    ok("makers de-duplicated", d["manufacturers"].count("Samsung") == 1)
    ok("device types include Smartphones/Tablets/Wearables/Basic Phones",
       set(d["device_types"]) >= {"Smartphones", "Tablets", "Wearables", "Basic Phones"})
    ok("accessory buckets from item text", set(d["accessory_cats"]) ==
       {"Cases", "Screen Protection", "Chargers & Cables"})
    ok("has_plans true when plans given", d["has_plans"] is True)
    ok("devices ranked, capped", "APPLE IPHONE 15" in d["devices"] and len(d["devices"]) == 7)
    ok("plans de-duplicated", sorted(d["plans"]) == ["Prepaid 15GB", "Unlimited Plus"])

    empty = C.derive_catalog([], [], [])
    ok("empty signals safe", empty["manufacturers"] == [] and empty["has_plans"] is False)

    # ── presets ───────────────────────────────────────────────────────────────────────────────────
    presets = C.build_presets(d)
    ids = {p["id"] for p in presets}
    ok("three presets offered", ids == {"by_type", "by_maker", "simple"})
    by_type = next(p for p in presets if p["id"] == "by_type")
    dept_names = {x["short_name"] for x in by_type["departments"]}
    ok("by_type has Phones/Accessories/Services", {"Phones", "Accessories", "Plans & Services"} <= dept_names)
    phones = next(x for x in by_type["departments"] if x["short_name"] == "Phones")
    ok("by_type phone cats are device types seen", "Basic Phones" in phones["categories"])
    ok("by_type accessories personalised from items",
       "Cases" in next(x for x in by_type["departments"]
                       if x["short_name"] == "Accessories")["categories"])
    by_maker = next(p for p in presets if p["id"] == "by_maker")
    maker_phones = next(x for x in by_maker["departments"] if x["short_name"] == "Phones")
    ok("by_maker phone cats are manufacturers + Other",
       "Apple" in maker_phones["categories"] and "Other" in maker_phones["categories"])
    ok("system_category set on every dept",
       all(x.get("system_category") for pr in presets for x in pr["departments"]))

    # a store with NO data still gets a complete, sensible preset (the reliable floor)
    bare = C.build_presets(C.derive_catalog([], [], []))
    bare_by_type = next(p for p in bare if p["id"] == "by_type")
    ok("bare preset still has default accessory cats",
       "Chargers & Cables" in next(x for x in bare_by_type["departments"]
                                    if x["short_name"] == "Accessories")["categories"])

    # ── learned taxonomy privacy rule (≥2 orgs) ─────────────────────────────────────────────────────
    dept_rows = [
        {"org_id": "A", "short_name": "Phones"}, {"org_id": "B", "short_name": "Phones"},
        {"org_id": "A", "short_name": "Accessories"}, {"org_id": "B", "short_name": "Accessories"},
        {"org_id": "A", "short_name": "John's Clearance Corner"},   # one org only — must NOT surface
    ]
    cat_rows = [
        {"org_id": "A", "name": "Smartphones", "department": "Phones"},
        {"org_id": "B", "name": "Smartphones", "department": "Phones"},
        {"org_id": "A", "name": "Cases", "department": "Accessories"},
        {"org_id": "B", "name": "Cases", "department": "Accessories"},
        {"org_id": "A", "name": "Secret Private Bucket", "department": "Phones"},  # one org — hidden
    ]
    learned = C.rank_learned(dept_rows, cat_rows)
    ldepts = {x["department"] for x in learned}
    ok("common departments surface", {"Phones", "Accessories"} <= ldepts)
    ok("single-org department hidden", "John's Clearance Corner" not in ldepts)
    all_cats = {c for x in learned for c in x["categories"]}
    ok("common categories surface", {"Smartphones", "Cases"} <= all_cats)
    ok("single-org category hidden", "Secret Private Bucket" not in all_cats)

    # ── apply path (stub client) ────────────────────────────────────────────────────────────────────
    store = {("pos", "departments"): [{"id": "d-existing", "org_id": "org1", "short_name": "Phones"}],
             ("pos", "categories"): [],
             ("pos", "system_categories"): [{"id": "sc1", "org_id": "org1", "name": "Cell Phone"}]}
    client = _StubClient(store)
    payload = {
        "departments": [{"short_name": "Phones", "system_category": "Cell Phone"},
                        {"short_name": "Accessories", "system_category": "Accessory"}],
        "categories": [{"name": "Smartphones", "department": "Phones"},
                       {"name": "Cases", "department": "Accessories"}],
        "system_categories": ["Cell Phone", "Accessory"],
    }
    out = C.apply_suggestion(client, "org1", **payload)
    ok("existing department not duplicated (Phones skipped)", out["created"]["departments"] == 1)
    ok("new department created (Accessories)",
       any(d["short_name"] == "Accessories" for d in store[("pos", "departments")]))
    ok("categories created", out["created"]["categories"] == 2)
    ok("existing system_category skipped, new one added", out["created"]["system_categories"] == 1)
    cases = next(c for c in store[("pos", "categories")] if c["name"] == "Cases")
    acc_id = next(d["id"] for d in store[("pos", "departments")] if d["short_name"] == "Accessories")
    ok("category linked to its department by name", cases["department_id"] == acc_id)

    out2 = C.apply_suggestion(client, "org1", **payload)
    ok("re-apply is a no-op (idempotent)",
       out2["created"] == {"departments": 0, "categories": 0, "system_categories": 0})

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


# ── Minimal Supabase-style stub: schema().table().select()/.insert()/.eq()/.range() ────────────────
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def range(self, lo, hi):
        self._slice = (lo, hi)
        return self

    def execute(self):
        lo, hi = getattr(self, "_slice", (0, 10 ** 9))
        return _Result([dict(r) for r in self._rows[lo:hi + 1]])

    def insert(self, rows):
        rows = rows if isinstance(rows, list) else [rows]
        for r in rows:
            row = dict(r)
            row.setdefault("id", f"id{len(self._rows)}")
            self._rows.append(row)
        return self


class _Schema:
    def __init__(self, store, schema):
        self._store, self._schema = store, schema

    def table(self, name):
        return _Query(self._store.setdefault((self._schema, name), []))


class _StubClient:
    def __init__(self, store):
        self._store = store

    def schema(self, name):
        return _Schema(self._store, name)


if __name__ == "__main__":
    sys.exit(main())
