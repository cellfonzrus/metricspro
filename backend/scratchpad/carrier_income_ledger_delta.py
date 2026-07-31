"""OPERATOR DELTA REPORT — carrier-income source swap (raw_ma_commission → commission_ledger).

THE GATE-2 ARTIFACT, runnable headlessly. Prints, per TENANT and per MONTH, what the Company-Payout tab's
Commission and Spiff headings read from the OLD source vs the NEW one, with the row counts behind each
side, so the owner can see exactly which dollars move before anything is switched on.

WHY A SCRIPT. The agent that built the swap has no Supabase credentials in the codespace (only
`backend/.env.example` exists) and Supabase SQL is web-only, so it could not run this against live data.
Every number below is produced by calling the REAL `whatif._ma_carrier_income` — the same code the
endpoint runs — against a real client, so there is no second implementation to drift.

  READ-ONLY BY CONSTRUCTION: the client is wrapped so insert/update/upsert/delete raise. It cannot write.
  MULTI-TENANT: org_id is always an explicit argument; tenants are DISCOVERED from storeops.tenants,
  never hard-coded. Pass --org-id to restrict to one.

USAGE (from the backend dir, with SUPABASE_URL + SUPABASE_SERVICE_KEY exported — the same values Railway
uses; a .env in backend/ is picked up automatically by app.core.settings):

    python3 scratchpad/carrier_income_ledger_delta.py                 # every tenant, last 12 months
    python3 scratchpad/carrier_income_ledger_delta.py --months 6
    python3 scratchpad/carrier_income_ledger_delta.py --org-id 00000000-0000-0000-0000-000000000001
    python3 scratchpad/carrier_income_ledger_delta.py --markdown > delta.md

ALTERNATIVE, NO CREDENTIALS NEEDED: the same table ships in the API payload. Open
/commcalc/whatif → 💵 Company Payout / Carrier Income → "Source reconciliation" (the collapsible panel
under the chart). It renders the identical numbers for the selected carrier, in BOTH modes, so the swap
can be reviewed in the app before it is switched on.
"""
import argparse, os, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ── read-only wrapper: any write raises before it reaches PostgREST ───────────────────────────────
class _ROQuery:
    def __init__(self, q):
        self._q = q

    def __getattr__(self, name):
        if name in ("insert", "update", "upsert", "delete"):
            raise RuntimeError(f"BLOCKED: this report is read-only; refusing {name}()")
        v = getattr(self._q, name)
        if callable(v):
            def _w(*a, **k):
                r = v(*a, **k)
                return _ROQuery(r) if hasattr(r, "execute") or hasattr(r, "select") else r
            return _w
        return v


class _ROSchema:
    def __init__(self, s):
        self._s = s

    def table(self, t):
        return _ROQuery(self._s.table(t))

    def rpc(self, *a, **k):
        return _ROQuery(self._s.rpc(*a, **k))


class ReadOnlyClient:
    def __init__(self, c):
        self._c = c

    def schema(self, s):
        return _ROSchema(self._c.schema(s))


def _fmt(v):
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _tenants(client, only=None):
    """Every tenant org_id present in the platform. DISCOVERED, never hard-coded (RULE ONE)."""
    if only:
        return [only]
    out = []
    for schema, table in (("storeops", "tenants"), ("commcalc", "carrier")):
        try:
            rows = (client.schema(schema).table(table).select("org_id")
                    .limit(5000).execute().data) or []
        except Exception:
            rows = []
        for r in rows:
            o = str(r.get("org_id") or "").strip()
            if o and o not in out:
                out.append(o)
        if out:
            break
    return out


def _carriers(client, org_id):
    try:
        return (client.schema("commcalc").table("carrier")
                .select("id,name,code,is_default").eq("org_id", org_id)
                .order("name").execute().data) or []
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser(description="Carrier-income source-swap delta (read-only).")
    ap.add_argument("--org-id", default="", help="restrict to ONE tenant (default: every tenant found)")
    ap.add_argument("--months", type=int, default=12, help="trailing months to compare (default 12)")
    ap.add_argument("--markdown", action="store_true", help="emit a markdown table (default: aligned text)")
    args = ap.parse_args()

    from app.core.database import get_supabase
    from app.modules.commcalc import whatif

    client = ReadOnlyClient(get_supabase())
    orgs = _tenants(client, args.org_id.strip() or None)
    if not orgs:
        print("No tenants found (storeops.tenants and commcalc.carrier both empty/unreadable).")
        return 2

    print("# Carrier-income source swap — delta report")
    print()
    print("OLD source: `commcalc.raw_ma_commission` — Σ spiff_m1..m6 → Commission, Σ rebate → Spiff")
    print("NEW source: `commcalc.commission_ledger` — canonical buckets (commission / spiff / equipment")
    print("            rebate / unmapped 'other'), ORIGIN-AGNOSTIC, residual-order lines excluded")
    print("UNCHANGED : residual + airtime margin (always `commcalc.raw_ma_daily_tx`), Boost/ePay tenants")
    print(f"Window    : trailing {args.months} month(s). Amounts are income-positive.")
    print()

    grand = {"old": 0.0, "new": 0.0}
    for org_id in orgs:
        cars = _carriers(client, org_id)
        # Every carrier is evaluated: carrier_mode decides whether the MA path even applies, and the
        # per-carrier config row can point different carriers at different sources.
        targets = cars or [None]
        for car in targets:
            cid = str(car.get("id")) if car else None
            cname = (car or {}).get("name") or "(no carrier row)"
            _cs, picked, mode = whatif._carrier_ctx(client, org_id, cid)
            cfg = whatif._whatif_source_config(client, org_id, (picked or {}).get("id"), mode)
            src = (cfg.get("income_source") or "").strip().lower()
            if src not in whatif.MA_INCOME_SOURCES:
                print(f"## {org_id} · {cname} — SKIPPED (carrier_mode={mode}, income_source={src!r}; "
                      f"not an MA-fed carrier — the swap does not touch it)")
                print()
                continue
            payload = whatif._ma_carrier_income(client, org_id, args.months, cfg)
            swap = payload.get("source_swap") or {}
            rows = swap.get("by_month") or []
            tot = swap.get("totals") or {}
            print(f"## {org_id} · {cname} (carrier_mode={mode}, income_source={src}, "
                  f"ledger_ready={payload.get('ledger_ready')}, "
                  f"effective={payload.get('income_source_effective')})")
            print()
            if not rows:
                print("_No MA/ledger rows in the window._")
                print()
                continue
            hdr = ["Month", "OLD commission", "OLD spiff", "OLD total", "NEW commission", "NEW spiff",
                   "NEW equip.rebate", "NEW unmapped", "NEW total", "DELTA", "MA rows", "Ledger lines",
                   "Ledger origins"]
            print("| " + " | ".join(hdr) + " |")
            print("|" + "|".join(["---"] * len(hdr)) + "|")
            for r in rows:
                print("| " + " | ".join([
                    str(r["period"]) + ("" if r.get("on_payload", True) else " *"),
                    _fmt(r["old_commission"]), _fmt(r["old_spiff"]), _fmt(r["old_total"]),
                    _fmt(r["new_commission"]), _fmt(r["new_spiff"]), _fmt(r["new_equipment_rebate"]),
                    _fmt(r["new_other"]), _fmt(r["new_total"]),
                    ("+" if r["delta_total"] > 0 else "") + _fmt(r["delta_total"]),
                    str(r["commission_rows"]), str(r["ledger_lines"]),
                    ",".join(r.get("ledger_origins") or []) or "—",
                ]) + " |")
            print("| **TOTAL** | " + " | ".join([
                f"**{_fmt(tot.get('old_commission'))}**", f"**{_fmt(tot.get('old_spiff'))}**",
                f"**{_fmt(tot.get('old_total'))}**", f"**{_fmt(tot.get('new_commission'))}**",
                f"**{_fmt(tot.get('new_spiff'))}**", f"**{_fmt(tot.get('new_equipment_rebate'))}**",
                f"**{_fmt(tot.get('new_other'))}**", f"**{_fmt(tot.get('new_total'))}**",
                f"**{('+' if (tot.get('delta_total') or 0) > 0 else '')}{_fmt(tot.get('delta_total'))}**",
                f"**{tot.get('commission_rows')}**", f"**{tot.get('ledger_lines')}**", "",
            ]) + " |")
            print()
            if tot.get("residual_overlap_lines"):
                print(f"> {tot['residual_overlap_lines']} ledger line(s) totalling "
                      f"${_fmt(tot['residual_overlap_total'])} carry the configured residual order type "
                      f"(`{cfg.get('residual_order_type')}`) and are EXCLUDED from the NEW totals — the "
                      f"Residual heading already counts those dollars.")
                print()
            if tot.get("new_other"):
                print(f"> ${_fmt(tot['new_other'])} of the NEW total is the ledger's 'other' bucket: real "
                      f"carrier payout whose label no rule classifies yet. Map those labels on "
                      f"/commcalc/commission-category-map to move it into a named bucket.")
                print()
            if payload.get("data_note"):
                print("> " + payload["data_note"])
                print()
            if any(not r.get("on_payload", True) for r in rows):
                print("> `*` = a month the ledger knows about that the MA tables do not cover. It appears "
                      "on the page only once the ledger source is active.")
                print()
            grand["old"] += float(tot.get("old_total") or 0)
            grand["new"] += float(tot.get("new_total") or 0)

    print("## Grand total across every MA-fed tenant/carrier")
    print()
    print(f"- OLD (raw_ma_commission): **${_fmt(grand['old'])}**")
    print(f"- NEW (commission_ledger): **${_fmt(grand['new'])}**")
    print(f"- DELTA: **{'+' if grand['new'] - grand['old'] > 0 else ''}${_fmt(grand['new'] - grand['old'])}**")
    print()
    print("Residual and airtime margin are NOT in these totals — the swap does not touch them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
