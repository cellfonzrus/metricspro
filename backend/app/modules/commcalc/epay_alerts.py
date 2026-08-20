"""ePay discrepancy alerts — PURE email planning + HTML builders (no DB / FastAPI). (Epic P4.)

The hourly ePay reconciliation sweep (commcalc.router `_run_epay_discrepancy_alerts`) recomputes TODAY's
ePay reconciliation per tenant and feeds this module the FLAGGED store-day discrepancies plus the
resolved org hierarchy per store (storeops.router `_managers_above_dm`). This module decides WHO gets
WHICH email:

  • DISCREPANCY DIGEST → every District Manager AND every manager ABOVE the DM for a flagged store. One
    digest per manager, listing each flagged store-day: the KIND (fee / payment), what OUR SYSTEM shows,
    what the PORTAL (Boost Daily Transaction Detail) shows, and the variance. Operational/coaching tone —
    surfaced so the manager can chase the gap the SAME DAY, never a penalty.

Two flavours of gap, keyed the same way:
  • FEE      — our register rang the "ePay service charge" (raw_sales) but it disagrees with the portal's
               fee count for that store-day. (epay_fee_recon.)
  • PAYMENT  — the ePay bill-payment the store DECLARED at closing disagrees with the portal's payment
               total for that store-day. (The P2 recon embedded in the DM-Verify money reconciliation.)

Dedup is per (store, date, kind) per recipient — see the caller's alert_log ref_key (which this module
mints on each item). A given discrepancy escalates ONCE per day; a NEW discrepancy found later the same
day sends a fresh digest of only the new items. Everything here is pure so the harness can drive it with
no DB. Managers with no email are skipped.
"""

_KIND_LABEL = {"fee": "Fee", "payment": "Payment"}


# ── tiny HTML helpers (no template engine; matches the app's plain-HTML email style) ────────────────
def _esc(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _money(v):
    """0 -> '$0.00', 1234.5 -> '$1,234.50', None -> '—'."""
    if v is None:
        return "—"
    try:
        return "${:,.2f}".format(float(v))
    except (TypeError, ValueError):
        return _esc(v)


def _kind_label(kind):
    return _KIND_LABEL.get(str(kind or "").lower(), str(kind or "").title() or "ePay")


def _variance_note(item):
    """Plain-language direction of the gap for one item. `variance = system - portal`."""
    try:
        var = float(item.get("variance") or 0)
    except (TypeError, ValueError):
        var = 0.0
    if var > 0:
        return "our system is higher than the portal"
    if var < 0:
        return "the portal is higher than our system"
    return "matches"


def ref_key_for(today, email, item):
    """Stable per-(store, date, kind) dedup key for ONE recipient — the caller stores it in
    storeops.alert_log (scope 'epay_discrepancy') so a given discrepancy escalates once per day."""
    return "epay_discrepancy|{d}|{e}|{s}|{dt}|{k}".format(
        d=today, e=str(email or "").strip().lower(),
        s=item.get("store_code"), dt=item.get("close_date"), k=str(item.get("kind") or "").lower())


def _item_rows_html(items):
    """A table of a store's flagged store-days: date · type · our system · portal · variance."""
    trs = []
    for it in sorted(items, key=lambda x: (str(x.get("close_date")), str(x.get("kind")))):
        var = it.get("variance")
        var_color = "#b45309" if (var or 0) else "#6b7280"
        trs.append(
            "<tr>"
            f"<td style='padding:3px 12px 3px 0'>{_esc(it.get('close_date'))}</td>"
            f"<td style='padding:3px 12px 3px 0'>{_esc(_kind_label(it.get('kind')))}</td>"
            f"<td style='padding:3px 12px 3px 0'>{_esc(_money(it.get('system')))}</td>"
            f"<td style='padding:3px 12px 3px 0'>{_esc(_money(it.get('portal')))}</td>"
            f"<td style='padding:3px 0;color:{var_color}'>{_esc(_money(var))} "
            f"<span style='color:#6b7280'>({_esc(_variance_note(it))})</span></td>"
            "</tr>")
    return ("<table style='font-size:13px;border-collapse:collapse;margin:4px 0 2px'>"
            "<tr style='color:#6b7280;text-align:left'>"
            "<th style='padding:0 12px 4px 0'>Date</th>"
            "<th style='padding:0 12px 4px 0'>Type</th>"
            "<th style='padding:0 12px 4px 0'>Our system</th>"
            "<th style='padding:0 12px 4px 0'>Portal</th>"
            "<th style='padding:0 4px 4px 0'>Variance</th></tr>"
            + "".join(trs) + "</table>")


def build_digest(name, items):
    """Build ONE manager's ePay discrepancy digest from their flagged items. items = [{store_code,
    close_date, kind, system, portal, variance}, ...]. Returns {"subject","html"}. Pure — the caller
    re-invokes this with the NOT-yet-sent subset after the alert_log dedup filter."""
    by_store = {}
    for it in items:
        by_store.setdefault(it.get("store_code"), []).append(it)
    blocks = []
    for store in sorted(by_store, key=lambda s: str(s)):
        blocks.append(f"<h3 style='margin:16px 0 2px;font-size:15px'>Store {_esc(store)}</h3>"
                      + _item_rows_html(by_store[store]))
    n_items, n_stores = len(items), len(by_store)
    html = (
        f"<p>Hi {_esc(name or 'there')},</p>"
        f"<p>ePay reconciliation is off for the store(s) you oversee — our system and the Boost portal "
        f"(Daily Transaction Detail) don't agree on these store-days. Please review with the store today "
        f"so the gap doesn't compound:</p>"
        + "".join(blocks) +
        "<p style='color:#6b7280;font-size:12px;margin-top:16px'>Automated ePay reconciliation alert — "
        "surfaced for a same-day check with the store, not a penalty. \"Fee\" is the ePay service charge; "
        "\"Payment\" is the bill-payment the store declared at closing. The portal is the authority.</p>")
    subject = ("ePay reconciliation — {i} discrepanc{isuf} across {s} store{ssuf}"
               .format(i=n_items, isuf=("y" if n_items == 1 else "ies"),
                       s=n_stores, ssuf=("" if n_stores == 1 else "s")))
    return {"subject": subject, "html": html}


def plan_emails(flags, hierarchy_by_store, today):
    """Decide the emails to send. Inputs are already resolved by the caller:
      flags: [{store_code, close_date, kind ('fee'|'payment'), system, portal, variance}] — the flagged
             store-days for TODAY (|variance| > tolerance), across all of the tenant's stores.
      hierarchy_by_store: {store_code: {"dm":[{name,email,...}], "above":[{name,email,...}]}}
    Every District Manager AND every manager above the DM for a flagged store receives ONE digest of the
    store-days they oversee. Returns {"digests": [ {kind, to, to_name, subject, html,
      items:[{...,ref_key}]} ]}. Managers with no email are skipped."""
    by_store = {}
    for f in flags:
        by_store.setdefault(f.get("store_code"), []).append(f)

    mgr = {}   # lower(email) -> {"name","email","items":[...]}
    for store, items in by_store.items():
        h = hierarchy_by_store.get(store) or {"dm": [], "above": []}
        recipients = list(h.get("dm") or []) + list(h.get("above") or [])
        for m in recipients:
            em = (m.get("email") or "").strip()
            if not em:
                continue
            slot = mgr.setdefault(em.lower(), {"name": m.get("name") or em, "email": em, "items": []})
            for it in items:
                slot["items"].append({**it, "ref_key": ref_key_for(today, em, it)})

    digests = []
    for slot in mgr.values():
        # A recipient could oversee the same store via both the DM node and an ancestor — dedup items by
        # ref_key so a store-day isn't listed twice in their digest.
        seen, items = set(), []
        for it in slot["items"]:
            if it["ref_key"] in seen:
                continue
            seen.add(it["ref_key"])
            items.append(it)
        built = build_digest(slot["name"], items)
        digests.append({"kind": "epay_digest", "to": slot["email"], "to_name": slot["name"],
                        "subject": built["subject"], "html": built["html"], "items": items})
    digests.sort(key=lambda d: d["to"].lower())
    return {"digests": digests}


if __name__ == "__main__":
    flags = [
        {"store_code": "S1", "close_date": "2026-08-20", "kind": "fee",
         "system": 145.00, "portal": 132.00, "variance": 13.00},
        {"store_code": "S1", "close_date": "2026-08-20", "kind": "payment",
         "system": 980.00, "portal": 1015.00, "variance": -35.00},
        {"store_code": "S2", "close_date": "2026-08-20", "kind": "fee",
         "system": 60.00, "portal": 48.50, "variance": 11.50},
    ]
    hier = {
        "S1": {"dm": [{"name": "Dee DM", "email": "dee@x.com"}],
               "above": [{"name": "Rita Regional", "email": "rita@x.com"},
                         {"name": "Vic VP", "email": "vic@x.com"}]},
        "S2": {"dm": [{"name": "Dee DM", "email": "dee@x.com"}],
               "above": [{"name": "Rita Regional", "email": "rita@x.com"}]},
    }
    plan = plan_emails(flags, hier, "2026-08-20")
    tos = {d["to"] for d in plan["digests"]}
    assert tos == {"dee@x.com", "rita@x.com", "vic@x.com"}, tos          # DM + everyone above
    dee = next(d for d in plan["digests"] if d["to"] == "dee@x.com")
    assert len(dee["items"]) == 3, dee["items"]                          # S1 fee+payment + S2 fee
    assert {i["ref_key"] for i in dee["items"]} == {
        "epay_discrepancy|2026-08-20|dee@x.com|S1|2026-08-20|fee",
        "epay_discrepancy|2026-08-20|dee@x.com|S1|2026-08-20|payment",
        "epay_discrepancy|2026-08-20|dee@x.com|S2|2026-08-20|fee"}
    vic = next(d for d in plan["digests"] if d["to"] == "vic@x.com")
    assert len(vic["items"]) == 2 and {i["store_code"] for i in vic["items"]} == {"S1"}   # Vic over S1 only
    assert "Store S1" in dee["html"] and "Store S2" in dee["html"]
    assert "$13.00" in dee["html"] and "$-35.00" in dee["html"] and "$11.50" in dee["html"]
    assert "3 discrepancies across 2 stores" in dee["subject"], dee["subject"]
    assert "2 discrepancies across 1 store" in vic["subject"], vic["subject"]
    print("epay_alerts self-test OK — digests:",
          [(d["to"], len(d["items"])) for d in plan["digests"]])
