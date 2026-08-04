"""Tenant-configurable Daily-Closing expense categories (migration 506).

Doctrine (EEP spec §mod-retail-ops): 5 preset categories — Salary(payroll), Commission(commission),
Petty Expenses / Office Expenses / Supplies (expense) — are LAZY-SEEDED into
commcalc.closing_expense_category the first time GET /closing/expense-categories runs for an org whose
table is empty (mirrors commcalc.router._item_category_values' upsert-on-first-GET pattern, NOT the
read-time-only fallback tender_config.py/count_config.py use — unlike a tender/count field, a category
is a REAL FK every closing_expense row must point at, so it has to actually exist as a row, not just be
assumed at read time).

`kind` drives behaviour everywhere else in the EEP package:
  payroll    -> employee REQUIRED; a line records a salary ADVANCE (mod-people ledger), never P&L.
  commission -> employee REQUIRED; a line records a commission ADVANCE (mod-commission ledger), never P&L.
  expense    -> plain P&L expense; an APPROVED line rolls up to Store Expenses (system-line, source_key
               'closing_expense:<category-id>').
"""

TABLE = "closing_expense_category"
KINDS = ("payroll", "commission", "expense")

# (name, kind, sort_order) — the 5 built-in presets. is_preset=True on these rows so the admin UI can
# label them distinctly (still renameable/deactivatable — only NOT deletable outright would be a hard
# lock, which we deliberately don't impose: a tenant may fully retire a preset if it never applies).
PRESET_DEFS = [
    ("Salary", "payroll", 10),
    ("Commission", "commission", 20),
    ("Petty Expenses", "expense", 30),
    ("Office Expenses", "expense", 40),
    ("Supplies", "expense", 50),
]


def _normalize_kind(k) -> str:
    k = str(k or "").strip().lower()
    return k if k in KINDS else "expense"


def load_categories(client, org_id, active_only: bool = True, seed_if_empty: bool = True):
    """The org's expense categories, sorted. Lazy-seeds the 5 presets (best-effort persisted) when the
    org has none yet. Degrades to the coded presets (unsaved) if the table isn't migrated yet or the
    seed write itself fails — never raises, so GET /closing/expense-categories and every consumer
    (submit form, DM verify, admin page) stay usable pre-migration."""
    try:
        q = client.schema("commcalc").table(TABLE).select("*").eq("org_id", org_id)
        if active_only:
            q = q.eq("is_active", True)
        rows = q.execute().data or []
    except Exception:
        return [{"id": None, "name": n, "kind": k, "is_preset": True, "is_active": True, "sort_order": so,
                  "source": "default"} for (n, k, so) in PRESET_DEFS]
    if not rows:
        if seed_if_empty:
            try:
                seed = [{"org_id": org_id, "name": n, "kind": k, "is_preset": True, "is_active": True,
                         "sort_order": so} for (n, k, so) in PRESET_DEFS]
                ins = client.schema("commcalc").table(TABLE).insert(seed).execute()
                rows = ins.data or []
            except Exception:
                pass
        if not rows:
            return [{"id": None, "name": n, "kind": k, "is_preset": True, "is_active": True, "sort_order": so,
                      "source": "default"} for (n, k, so) in PRESET_DEFS]
    rows.sort(key=lambda r: (r.get("sort_order") if r.get("sort_order") is not None else 100,
                             r.get("name") or ""))
    return rows


def category_by_id(client, org_id, category_id):
    """One category row (or None) — used to snapshot kind/name onto a closing_expense line at insert
    time. Ensures the org's categories exist first (lazy-seed) so a first-ever submit against a
    never-configured tenant still resolves the presets."""
    if not category_id:
        return None
    cats = load_categories(client, org_id, active_only=False)
    for c in cats:
        if str(c.get("id")) == str(category_id):
            return c
    return None
