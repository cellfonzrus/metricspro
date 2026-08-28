"""CROSS-TENANT INGEST GUARD (owner-approved 2026-08-06).

The control that stops the Diversey class from recurring. On 2026-07-14 a Luxelink sales export was
ingested under the HOUSE org: 6 line items for a Luxelink store landed in house `raw_sales`, the
July recompute paid a phantom rep $2.9995 out of them, and the feed→raw_sales promotion re-inserted
them every hour for three weeks. Nothing detected it, because nothing ever asked *"does this org
actually have a store called that?"*.

WHAT IT DOES
    Before a sales batch is written, every DISTINCT store string in it is resolved against the
    ORG'S OWN known-store set — `commcalc.store_mapping` + `storeops.stores` + `commcalc.store_aliases`,
    i.e. the identical resolver chain `/store-unmatched` already uses. Strings that resolve to
    nothing are recorded in `commcalc.ingest_store_quarantine` with row counts, dollar totals and a
    sample, and — in `block` mode ONLY — withheld from the write with their FULL payload parked so a
    human can release them. **Nothing is ever silently discarded.**

MODES (per-org config, `commcalc.ingest_store_guard.mode`)
    off    — do nothing at all. Byte-identical to life before this module existed.
    warn   — write EVERY row exactly as today; only record the flag.            ← DEFAULT
    block  — withhold the unknown-store rows and park them intact for review.

    `warn` is the default on purpose: a hard block on day one would stop a legitimate new store from
    ever being ingested. An operator moves an org to `block` from the admin UI once they trust it.

SAP-CONFIGURABLE (contract RULE TWO)
    No carrier, tenant, store or address is named anywhere in this file. The "known" set is the
    org's own real roster, and ALLOWING a store creates a normal `commcalc.store_aliases` row (the
    existing pick-don't-type machinery) instead of a parallel allowlist.

DEGRADES OPEN, ALWAYS
    Every entry point is wrapped: if migration 280 has not been run, if the config read fails, if
    the resolver raises — the guard returns "keep everything" and the ingest proceeds exactly as it
    does today. A guard that breaks an upload is worse than the leak it prevents.
"""
from app.core.database import get_supabase

SCHEMA = "commcalc"
CONFIG_TABLE = "ingest_store_guard"
QUARANTINE_TABLE = "ingest_store_quarantine"

MODES = ("off", "warn", "block")
DEFAULT_CONFIG = {
    "mode": "warn",
    "block_min_rows": 0,
    "allow_creates_alias": True,
    "notify_on_flag": True,
}
# Tables this guard screens, and the column each spells its store string in. A table not listed here
# is never touched — the guard is deliberately scoped to the SALES basis, which is what feeds pay.
GUARDED_TABLES = {"raw_sales": "store", "daily_sales_feed": "store"}
_SAMPLE_ROWS = 3


def _sb(client=None):
    return client or get_supabase()


def _f(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip() or 0)
    except Exception:
        return 0.0


def get_config(client, org_id: str) -> dict:
    """This org's guard config, defaults merged in. Never raises — a missing table (migration 280
    unrun) or an unreadable row yields the DEFAULT, and the default writes every row."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["org_id"] = org_id
    cfg["ready"] = False
    try:
        rows = (_sb(client).schema(SCHEMA).table(CONFIG_TABLE).select("*")
                .eq("org_id", org_id).limit(1).execute().data) or []
        cfg["ready"] = True
        if rows:
            r = rows[0]
            for k in DEFAULT_CONFIG:
                if r.get(k) is not None:
                    cfg[k] = r[k]
            cfg["updated_at"] = r.get("updated_at")
            cfg["updated_by"] = r.get("updated_by")
    except Exception as e:
        cfg["hint"] = f"ingest_store_guard unavailable — run migration 280 ({e})."
    if str(cfg.get("mode")) not in MODES:
        cfg["mode"] = DEFAULT_CONFIG["mode"]
    return cfg


def known_store_matcher(client, org_id: str):
    """`(is_known, known_count)` for this org.

    `is_known(raw)` is True when the string resolves to one of the org's real stores through the
    SAME chain the rest of the module uses: the alias table, `commcalc.store_mapping` (address /
    code / leading street number) and the `storeops.stores` roster. **Inactive stores still count
    as known** — a closed store's historical rows must keep flowing; this guard is about the wrong
    TENANT, not the wrong status.

    Fails OPEN: if the org has no known stores at all (a brand-new tenant, or an unreadable table),
    `is_known` returns True for everything, so the guard can never wall off a fresh tenant."""
    keys = set()

    def _add(v):
        v = str(v or "").strip().lower()
        if v:
            keys.add(v)

    lead = set()

    def _lead(v):
        import re
        m = re.match(r"\s*(\d+)", str(v or ""))
        return m.group(1) if m else ""

    try:
        for m in (_sb(client).schema(SCHEMA).table("store_mapping")
                  .select("store_code,store_address").eq("org_id", org_id).execute().data) or []:
            _add(m.get("store_code"))
            _add(m.get("store_address"))
            n = _lead(m.get("store_address"))
            if n:
                lead.add(n)
    except Exception:
        pass
    try:
        for s in (_sb(client).schema("storeops").table("stores")
                  .select("store_code,address").eq("org_id", org_id).execute().data) or []:
            _add(s.get("store_code"))
            _add(s.get("address"))
            n = _lead(s.get("address"))
            if n:
                lead.add(n)
    except Exception:
        pass
    try:
        for a in (_sb(client).schema(SCHEMA).table("store_aliases")
                  .select("alias,store_code").eq("org_id", org_id).execute().data) or []:
            _add(a.get("alias"))
            _add(a.get("store_code"))
    except Exception:
        pass

    known_count = len(keys)

    def is_known(raw) -> bool:
        v = str(raw or "").strip()
        if not v:
            return True                       # a blank store is not a tenant question
        if not known_count:
            return True                       # FAIL OPEN — new tenant / unreadable roster
        if v.lower() in keys:
            return True
        n = _lead(v)
        return bool(n and n in lead)

    return is_known, known_count


def screen(client, org_id: str, rows: list, target_table: str, *, source: str = "",
           upload_type: str = "", period: str = "", filename: str = "") -> dict:
    """Screen a parsed batch before it is written.

    Returns `{"kept": [...], "flags": [...], "mode": ..., "checked": bool}`. In `off`/`warn` mode
    `kept is rows` (the identical list object — the ingest is provably untouched). In `block` mode
    `kept` omits the rows whose store is unknown, and each flag carries every withheld row so
    nothing is lost.

    NEVER RAISES. Any failure returns the untouched batch."""
    out = {"kept": rows, "flags": [], "mode": "off", "checked": False,
           "unknown_stores": 0, "rows_flagged": 0, "rows_withheld": 0}
    try:
        col = GUARDED_TABLES.get(str(target_table or ""))
        if not col or not rows:
            return out
        cfg = get_config(client, org_id)
        mode = str(cfg.get("mode") or "warn")
        out["mode"] = mode
        if mode == "off":
            return out
        is_known, known_count = known_store_matcher(client, org_id)
        if not known_count:
            return out                        # fail open, explicitly

        by_store = {}
        for r in rows:
            raw = str(r.get(col) or "").strip()
            if not raw or is_known(raw):
                continue
            d = by_store.setdefault(raw.lower(), {"raw": raw, "rows": [], "amount": 0.0})
            d["rows"].append(r)
            d["amount"] += _f(r.get("ext_price"))
        out["checked"] = True
        if not by_store:
            return out

        min_rows = int(cfg.get("block_min_rows") or 0)
        kept, flags = None, []
        withhold_keys = set()
        for k, d in by_store.items():
            n = len(d["rows"])
            # In block mode a LARGE batch is exempt when block_min_rows is set: a real new store
            # opening arrives as a full day of sales, a mis-file as a handful of lines.
            blocked = (mode == "block") and (min_rows <= 0 or n <= min_rows)
            if blocked:
                withhold_keys.add(k)
            flags.append({
                "store_raw": d["raw"], "rows_seen": n,
                "rows_withheld": n if blocked else 0,
                "amount_seen": round(d["amount"], 2),
                "sample": d["rows"][:_SAMPLE_ROWS],
                "withheld_rows": d["rows"] if blocked else None,
                "source": source, "upload_type": upload_type, "target_table": target_table,
                "period": period, "filename": filename, "mode_at_flag": mode,
            })
        if withhold_keys:
            kept = [r for r in rows
                    if str(r.get(col) or "").strip().lower() not in withhold_keys]
        out["kept"] = rows if kept is None else kept
        out["flags"] = flags
        out["unknown_stores"] = len(flags)
        out["rows_flagged"] = sum(f["rows_seen"] for f in flags)
        out["rows_withheld"] = sum(f["rows_withheld"] for f in flags)
        return out
    except Exception as e:      # noqa: BLE001 — a guard must never break an ingest
        print(f"WARN ingest_store_guard.screen degraded open: {e}")
        return {"kept": rows, "flags": [], "mode": "off", "checked": False,
                "unknown_stores": 0, "rows_flagged": 0, "rows_withheld": 0, "error": str(e)[:200]}


def record(client, org_id: str, result: dict) -> int:
    """Persist `screen`'s flags to the review queue. Best-effort; returns how many landed."""
    flags = (result or {}).get("flags") or []
    if not flags:
        return 0
    rows = []
    for f in flags:
        rows.append({
            "org_id": org_id, "store_raw": f["store_raw"], "source": f.get("source") or None,
            "upload_type": f.get("upload_type") or None, "target_table": f.get("target_table"),
            "period": f.get("period") or None, "filename": f.get("filename") or None,
            "rows_seen": f["rows_seen"], "rows_withheld": f["rows_withheld"],
            "amount_seen": f["amount_seen"], "sample": f.get("sample"),
            "withheld_rows": f.get("withheld_rows"),
            "status": "pending", "mode_at_flag": f.get("mode_at_flag"),
        })
    try:
        saved = (_sb(client).schema(SCHEMA).table(QUARANTINE_TABLE).insert(rows).execute().data) or rows
        _intimate_quarantine(org_id, saved)
        return len(rows)
    except Exception as e:
        print(f"WARN ingest_store_guard.record failed (migration 280 unrun?): {e}")
        return 0


def _intimate_quarantine(org_id, rows):
    """Intimation-only bridge to the unified approvals inbox for each flagged cross-tenant store.

    The guard decision (allow/reject) is binary, but 'allow' requires the reviewer to PICK which of our
    stores the foreign string maps to (create the alias) and it RELEASES withheld cross-tenant rows into
    the ledger — a store-code pick the generic inbox cannot supply and a data effect that must stay a
    deliberate human action. So the decision stays on the guard board (which has the store picker); we
    only MIRROR each pending flag into the inbox as an intimation. Best-effort; never raises."""
    try:
        from app.modules.approvals import engine as _approvals
        for r in rows:
            rid = r.get("id")
            if not rid or (r.get("status") or "pending") != "pending":
                continue
            _approvals.create_request(
                org_id, type="ingest_guard", source_table=QUARANTINE_TABLE, source_id=rid,
                title=f"Cross-tenant store flagged: {r.get('store_raw')} ({r.get('target_table')})",
                summary=(f"{r.get('rows_withheld')} row(s) withheld, "
                         f"${float(r.get('amount_seen') or 0):,.2f} seen — review on the ingest guard board."),
                payload={"store_raw": r.get("store_raw"), "target_table": r.get("target_table"),
                         "rows_withheld": r.get("rows_withheld"), "amount_seen": r.get("amount_seen"),
                         "period": r.get("period")},
                priority="high", notify=False)
    except Exception:
        pass


def screen_and_record(client, org_id: str, rows: list, target_table: str, **kw) -> list:
    """The one call an ingest path makes. Returns the rows to write."""
    res = screen(client, org_id, rows, target_table, **kw)
    if res.get("flags"):
        record(client, org_id, res)
        print(f"GUARD {org_id} {target_table}: {res['unknown_stores']} unknown store(s), "
              f"{res['rows_flagged']} row(s) flagged, {res['rows_withheld']} withheld "
              f"(mode={res['mode']})")
    return res["kept"]
