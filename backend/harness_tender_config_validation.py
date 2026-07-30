"""Offline proof harness for the nit-sweep's off-axis `put_tender_config` validation (2026-07-30,
dispatch: "durable off-axis validation at put_tender_config save"). No live DB/network — same
convention as harness_dmverify_parity.py: runs the REAL `put_tender_config`/`get_tender_config`
functions against a stateful fake Supabase client.

Root cause (retail-ops-15, N1): a `closing_tender_map` row's `tender_key` was NEVER validated
against the tenant's active `closing_tender_def` set at save time — a deactivated/typo'd/removed
`tender_key` saved silently, and its dollars only surfaced as a problem later, at READ time (3-way
recon), with no signal at all pre-retail-ops-15 (retail-ops-15 hardened the READ side to route those
dollars into `x_report_unmapped`/`sales_unmapped` instead of vanishing — this harness proves the
complementary WRITE-side fix: reject the save itself before the mismatch can ever land).

Proves:
  A. A map row referencing an ACTIVE custom tender_key being saved in the SAME payload succeeds.
  B. A map row referencing a def in the SAME payload but marked `is_active: false` is REJECTED
     (matches load_tender_config's own `.eq("is_active", True)` — an inactive def is off the REAL
     axis resolve_x will ever see).
  C. A map row referencing a tender_key that doesn't exist in the payload's defs at all is REJECTED.
  D. Empty defs (no custom tenders configured) + a map row using a STANDARD key (e.g. "cash") is
     accepted — the empty-config-falls-back-to-CANON_TENDERS rule applied to validation too.
  E. Empty defs + a map row using a bogus, non-standard key is REJECTED.
  F. A rejected save performs ZERO writes — the tenant's PREVIOUS config (defs + maps) is completely
     untouched (proven by comparing store contents before/after the rejected call).
  G. A payload with defs but NO maps at all always succeeds (nothing to validate against the axis).
  H. Regression: the standard 7-tender payload (matches the tender-config wizard's own seed-standard
     shape) saves cleanly, one map row per tender, all on-axis.
"""
import sys
sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"


# ── minimal stateful fake supabase client (same convention as harness_dmverify_parity.py) ───────────
class Q:
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.op, self.payload = "select", None
        self.filters = []

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k):
        self.op = "insert"; self.payload = rows if isinstance(rows, list) else [rows]; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def delete(self, **k): self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, v)); return self
    def order(self, *a, **k): return self
    def limit(self, n, *a, **k): return self

    def _match(self, row):
        return all(row.get(c) == v for c, v in self.filters)

    def execute(self):
        rows = self.s.setdefault(self.t, [])
        if self.op == "select":
            return type("R", (), {"data": [r for r in rows if self._match(r)]})()
        if self.op == "insert":
            for r in self.payload:
                rows.append(dict(r))
            return type("R", (), {"data": list(self.payload)})()
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload); out.append(dict(r))
            return type("R", (), {"data": out})()
        if self.op == "delete":
            keep = [r for r in rows if not self._match(r)]
            deleted = [r for r in rows if self._match(r)]
            self.s[self.t] = keep
            return type("R", (), {"data": deleted})()
        return type("R", (), {"data": []})()


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


def fresh_store():
    return {"closing_tender_def": [], "closing_tender_map": [], "tenants": []}


import app.modules.closing.router as cr   # noqa: E402

AUTH_NONE = ""


def wire(store):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    # This harness targets the axis-validation logic, not access control (already covered by
    # harness_settings_audit.py) — bypass the permission gate directly.
    cr._can_edit_closing_setting = lambda perms: True
    return fake


def get_httpexc():
    from fastapi import HTTPException
    return HTTPException


HTTPException = get_httpexc()


# ═══════════════════════════ A. On-axis custom map — saves cleanly ═════════════════════════════════
st = fresh_store(); wire(st)
payload_a = {
    "defs": [{"tender_key": "cash", "label": "Cash", "is_active": True},
             {"tender_key": "custom1", "label": "House Credit", "is_active": True}],
    "maps": [{"tender_key": "custom1", "source_labels": ["House Credit Line"], "report": "both"}],
}
resp_a = cr.put_tender_config(payload_a, org_id=HOUSE, authorization=AUTH_NONE)
check("A. on-axis custom map_row (custom1, active in the SAME payload) saves cleanly",
      resp_a.get("ok") is True and resp_a.get("defs") == 2 and resp_a.get("maps") == 1, str(resp_a))

# ═══════════════════════════ B. Map row references a DEACTIVATED def — REJECTED ════════════════════
st = fresh_store(); wire(st)
payload_b = {
    "defs": [{"tender_key": "cash", "label": "Cash", "is_active": True},
             {"tender_key": "custom_dead", "label": "Retired Tender", "is_active": False}],
    "maps": [{"tender_key": "custom_dead", "source_labels": ["Old Label"], "report": "both"}],
}
try:
    cr.put_tender_config(payload_b, org_id=HOUSE, authorization=AUTH_NONE)
    check("B. map row referencing a DEACTIVATED def in the same payload is REJECTED", False, "did not raise")
except HTTPException as e:
    check("B. map row referencing a DEACTIVATED def in the same payload is REJECTED",
          e.status_code == 400 and "custom_dead" in str(e.detail), f"{e.status_code}: {e.detail}")

# ═══════════════════ C. Map row references a tender_key not in the payload at all — REJECTED ═══════
st = fresh_store(); wire(st)
payload_c = {
    "defs": [{"tender_key": "cash", "label": "Cash", "is_active": True}],
    "maps": [{"tender_key": "totally_unknown", "source_labels": ["???"], "report": "both"}],
}
try:
    cr.put_tender_config(payload_c, org_id=HOUSE, authorization=AUTH_NONE)
    check("C. map row referencing a tender_key absent from defs entirely is REJECTED", False, "did not raise")
except HTTPException as e:
    check("C. map row referencing a tender_key absent from defs entirely is REJECTED",
          e.status_code == 400 and "totally_unknown" in str(e.detail), f"{e.status_code}: {e.detail}")

# ═══ D. Empty defs (no custom tenders) + a map row using a STANDARD key — accepted (CANON_TENDERS
#      fallback, the exact "empty config == today's behaviour" doctrine, extended to validation) ═════
st = fresh_store(); wire(st)
payload_d = {"defs": [], "maps": [{"tender_key": "cash", "source_labels": ["CASH"], "report": "sales"}]}
resp_d = cr.put_tender_config(payload_d, org_id=HOUSE, authorization=AUTH_NONE)
check("D. empty defs + a STANDARD tender_key ('cash') map row saves cleanly (CANON_TENDERS fallback)",
      resp_d.get("ok") is True and resp_d.get("defs") == 0 and resp_d.get("maps") == 1, str(resp_d))

# ═══ E. Empty defs + a map row using a BOGUS, non-standard key — REJECTED ═══════════════════════════
st = fresh_store(); wire(st)
payload_e = {"defs": [], "maps": [{"tender_key": "not_a_real_tender", "source_labels": ["???"], "report": "both"}]}
try:
    cr.put_tender_config(payload_e, org_id=HOUSE, authorization=AUTH_NONE)
    check("E. empty defs + a bogus non-standard tender_key map row is REJECTED", False, "did not raise")
except HTTPException as e:
    check("E. empty defs + a bogus non-standard tender_key map row is REJECTED",
          e.status_code == 400 and "not_a_real_tender" in str(e.detail), f"{e.status_code}: {e.detail}")

# ═══ F. A rejected save performs ZERO writes — the tenant's PREVIOUS config is untouched ════════════
st = fresh_store(); wire(st)
# Seed a real, previously-saved, valid config first.
good_payload = {
    "defs": [{"tender_key": "cash", "label": "Cash", "is_active": True}],
    "maps": [{"tender_key": "cash", "source_labels": ["CASH", "Cash Tender"], "report": "both"}],
}
cr.put_tender_config(good_payload, org_id=HOUSE, authorization=AUTH_NONE)
before_defs = [dict(r) for r in st["closing_tender_def"]]
before_maps = [dict(r) for r in st["closing_tender_map"]]
bad_payload = {
    "defs": [{"tender_key": "cash", "label": "Cash", "is_active": True}],
    "maps": [{"tender_key": "cash", "source_labels": ["CASH"], "report": "both"},
             {"tender_key": "nonexistent", "source_labels": ["???"], "report": "both"}],
}
try:
    cr.put_tender_config(bad_payload, org_id=HOUSE, authorization=AUTH_NONE)
    check("F. a rejected save leaves the PREVIOUS config untouched (no partial delete)", False, "did not raise")
except HTTPException as e:
    after_defs = [dict(r) for r in st["closing_tender_def"]]
    after_maps = [dict(r) for r in st["closing_tender_map"]]
    check("F. a rejected save leaves the PREVIOUS config untouched (no partial delete)",
          e.status_code == 400 and after_defs == before_defs and after_maps == before_maps,
          f"before_defs={before_defs} after_defs={after_defs} before_maps={before_maps} after_maps={after_maps}")

# ═══ G. defs but NO maps at all — nothing to validate against the axis, always succeeds ═════════════
st = fresh_store(); wire(st)
payload_g = {"defs": [{"tender_key": "cash", "label": "Cash", "is_active": True},
                      {"tender_key": "custom_unused", "label": "Unused", "is_active": True}],
             "maps": []}
resp_g = cr.put_tender_config(payload_g, org_id=HOUSE, authorization=AUTH_NONE)
check("G. defs with no maps at all always succeeds (nothing to validate)",
      resp_g.get("ok") is True and resp_g.get("defs") == 2 and resp_g.get("maps") == 0, str(resp_g))

# ═══ H. Regression: the standard 7-tender payload (tender-config wizard's own seed-standard shape)
#      saves cleanly, one map row per tender, all on-axis ═══════════════════════════════════════════
st = fresh_store(); wire(st)
from app.modules.closing.tender_config import STANDARD_DEFS
std_defs = [{"tender_key": k, "label": lbl, "recon_class": rc, "include_in_total": intot, "is_active": True}
            for (k, lbl, rc, intot) in STANDARD_DEFS]
std_maps = [{"tender_key": k, "source_labels": [lbl.upper()], "report": "both"} for (k, lbl, rc, intot) in STANDARD_DEFS]
resp_h = cr.put_tender_config({"defs": std_defs, "maps": std_maps}, org_id=HOUSE, authorization=AUTH_NONE)
check("H. the standard 7-tender payload (wizard's seed-standard shape) saves cleanly, all on-axis",
      resp_h.get("ok") is True and resp_h.get("defs") == 7 and resp_h.get("maps") == 7, str(resp_h))

# ── Summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
