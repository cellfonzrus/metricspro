"""THE ONE product-filter implementation behind `GET /pos/products` (owner 2026-09-24: "create product
filters on the pos page").

Both POS screens that list products — the register's product picker (`pos/sales/page.tsx`) and the
catalog (`pos/products/page.tsx`) — filter through the SAME endpoint, and the endpoint filters through
THIS module. Before it, the handler carried its own `department_id` / `system_category` / search chain
and the special-order catalog carried a second copy of the search clause; both now dereference
`search_clause` / `apply`, and `harness_pos_product_filters_lock.py` fails the build if a caller grows
its own filter chain back.

Rules:
  * Every read is org-scoped (`.eq("org_id", …)` first, before any filter).
  * An absent / blank parameter adds NO filter, so a call with none of the new params builds the exact
    query the handler built before (proved in `harness_pos_product_filters.py` §A).
  * Filters are applied SERVER-side, so they work past the 500-row list cap.
  * No carrier, tenant, POS or brand word: the values are the org's own departments, categories,
    system categories (mig 745) and free-text manufacturers; `inventory_type` / serial `status` are
    the schema's own CHECK values (mig 724 / 725).
"""
from app.modules.pos.catalog_suggest import _page

# The equality filters, in the order they are applied. The first two predate this module (the
# handler's own params); the rest are new. ONE tuple so the handler, the harness and the index agree.
EQ_FILTERS = ("department_id", "category_id", "system_category", "manufacturer", "inventory_type")

# pos.products.inventory_type CHECK (mig 724) — the schema's two values, not a vendor vocabulary.
INVENTORY_TYPES = ("standard", "serial")

LIST_CAP = 500          # the list cap the handler always had
_ID_CHUNK = 150         # ids per `in.(…)` so the request URL stays well under proxy limits


def search_clause(search):
    """The PostgREST `or=` clause the product search has always used (name, full name, UPC), or None
    for a blank search. `%` and `,` are stripped — they would break out of the ilike / or grammar."""
    if not (search or "").strip():
        return None
    s = search.strip().replace("%", "").replace(",", " ")
    return f"short_name.ilike.%{s}%,full_name.ilike.%{s}%,upc.ilike.%{s}%"


def clean(**params) -> dict:
    """The filters actually in force: a blank value means "any" and is dropped. Values are passed
    through un-stripped (a manufacturer is free text and must match exactly as stored)."""
    out = {}
    for k in EQ_FILTERS:
        v = params.get(k)
        if v is not None and str(v).strip():
            out[k] = v
    return out


def apply(q, filters: dict, search: str = ""):
    """Narrow an ALREADY org-scoped products query by the equality filters, then the search.
    THE one place a products query is filtered."""
    for k in EQ_FILTERS:
        if k in filters:
            q = q.eq(k, filters[k])
    clause = search_clause(search)
    if clause:
        q = q.or_(clause)
    return q


def in_stock_product_ids(client, org_id: str, store_code: str = "") -> set:
    """Products with at least one unit ON HAND: a serial unit whose status is `in_stock`, or a
    standard-stock row with `qty_on_hand > 0` — at `store_code` when given, else at any store.
    Org-scoped paged reads through catalog_suggest._page (the one paged reader)."""
    def narrow(extra):
        def w(q):
            q = extra(q)
            if store_code:
                q = q.eq("store_code", store_code)
            return q.order("id")
        return w

    serial = _page(client, "pos", "inventory_serial", "product_id", org_id, cap=200000,
                   where=narrow(lambda q: q.eq("status", "in_stock")))
    standard = _page(client, "pos", "inventory_standard", "product_id", org_id, cap=200000,
                     where=narrow(lambda q: q.gt("qty_on_hand", 0)))
    return {r.get("product_id") for r in serial + standard if r.get("product_id")}


def list_rows(client, org_id: str, *, search: str = "", active_only: bool = True,
              filters: dict | None = None, in_stock: bool = False, store_code: str = "") -> list:
    """The product rows `GET /pos/products` answers, newest product first, capped at LIST_CAP.

    Without `in_stock` this is ONE query — the handler's original chain plus any equality filters.
    With it, the on-hand product ids are read first and the same filtered query runs per id chunk;
    the chunks are merged back into one newest-first list under the same cap."""
    filters = filters or {}

    def base():
        q = client.schema("pos").table("products").select("*").eq("org_id", org_id)
        if active_only:
            q = q.eq("is_active", True)
        return apply(q, filters, search)

    if not in_stock:
        return base().order("product_code", desc=True).limit(LIST_CAP).execute().data or []

    ids = sorted(in_stock_product_ids(client, org_id, store_code))
    rows, seen = [], set()
    for i in range(0, len(ids), _ID_CHUNK):
        for r in (base().in_("id", ids[i:i + _ID_CHUNK]).order("product_code", desc=True)
                  .limit(LIST_CAP).execute().data or []):
            if r.get("id") not in seen:
                seen.add(r.get("id"))
                rows.append(r)
    # product_code is an identity column (never NULL), so a plain numeric sort reproduces the single
    # query's `order=product_code.desc` across the merged chunks.
    rows.sort(key=lambda r: int(r.get("product_code") or 0), reverse=True)
    return rows[:LIST_CAP]


def manufacturers(client, org_id: str, active_only: bool = True) -> list:
    """The org's distinct non-blank manufacturers on its products, A→Z ignoring case — the option
    list for the Manufacturer filter. Values are returned exactly as stored so the filter's equality
    match finds them."""
    def w(q):
        if active_only:
            q = q.eq("is_active", True)
        return q.order("id")

    rows = _page(client, "pos", "products", "manufacturer", org_id, cap=200000, where=w)
    names = {r.get("manufacturer") for r in rows if (r.get("manufacturer") or "").strip()}
    return sorted(names, key=lambda s: (s.strip().lower(), s))
