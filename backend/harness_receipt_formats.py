"""Offline harness for the per-POS structured receipt formats (no PDF, no DB, no network).

Feeds SYNTHETIC word lists (fake data — never a real customer receipt) shaped like the RQ and B2B
layouts into the parsers, and asserts the engine reconstructs columns/items/totals/parties correctly,
that a long product name stays in its own column, that word-boundary totals matching works, that the
generic renderer reproduces the data, and that an edit recomputes the derived search fields.

Run:  cd backend && python harness_receipt_formats.py
"""
import sys

from app.modules.pos.receipt_formats import base, rq, b2b, render

_p = _f = 0


def ok(name, cond):
    global _p, _f
    if cond:
        _p += 1; print(f"  ok   {name}")
    else:
        _f += 1; print(f"  FAIL {name}")


def W(text, x0, top, w=6.0):
    return {"text": text, "x0": float(x0), "x1": float(x0) + len(text) * w, "top": float(top), "bottom": float(top) + 10}


def row(top, cells):
    return [W(t, x0, top) for (t, x0) in cells]


# ── RQ synthetic layout (column x's mirror the real header) ───────────────────────────────────────
def rq_words():
    ws = []
    ws += row(37, [("Sale", 516)])
    ws += row(107, [("Invoice", 477), (":", 510), ("INV-9", 515)])
    ws += row(99, [("Test", 70), ("Wireless", 100), ("Store", 150)])
    ws += row(114, [("1", 70), ("Test", 80), ("St", 110)])
    ws += row(129, [("Testville", 70), ("NY", 130), ("10000", 150)])
    ws += row(138, [("Tendered", 380), ("On:", 416), ("28-Nov-2025", 463), ("06:19", 511), ("PM", 533)])
    ws += row(144, [("(555)123-4567", 70)])
    ws += row(153, [("Sales", 380), ("Person:", 402), ("Jane", 463), ("R", 482)])
    ws += row(190, [("Bill", 28), ("To:", 46)])
    ws += row(192, [("JANE", 98), ("DOE", 133)])
    ws += row(210, [("2", 98), ("Sample", 118), ("Ave", 150)])
    ws += row(225, [("Sampletown", 98), ("NY", 160), ("20000", 185)])
    # item header
    ws += row(303, [("Product", 28), ("SKU", 60), ("Product", 109), ("Name", 142),
                    ("Tracking", 341), ("#", 376), ("Qty", 449), ("Your", 493), ("Price", 512),
                    ("Your", 546), ("Total", 566)])
    # item 1: LONG name that runs past its header centre + a 15-digit serial
    ws += row(317, [("SKU1", 28), ("SUPER", 109), ("PHONE", 140), ("256GB", 175), ("MIDNIGHT", 210),
                    ("BLACK", 260), ("111111111111111", 341), ("1", 449), ("$100.00", 497), ("$100.00", 549)])
    # item 2: no serial, zero price
    ws += row(331, [("SKU2", 28), ("Rate", 109), ("Plan", 130), ("1", 449), ("$0.00", 497), ("$0.00", 549)])
    # item 3: negative financed offset line (must NOT stop the table despite the word 'Financed')
    ws += row(345, [("SKU3", 28), ("Device", 109), ("Financed", 145), ("Amount", 190),
                    ("222222", 341), ("-1", 449), ("$100.00", 497), ("($100.00)", 549)])
    # totals
    ws += row(400, [("Subtotal:", 380), ("$0.00", 500), ("Payment:", 545)])
    ws += row(414, [("Cash", 460), ("$110.00", 520)])
    ws += row(428, [("Sales", 380), ("Tax", 402), ("(NYC):", 430), ("$10.00", 520)])
    ws += row(442, [("$500.00", 460), ("Financed:", 520)])
    ws += row(456, [("Total:", 460), ("$110.00", 520)])
    ws += row(470, [("Change:", 460), ("$0.00", 520)])
    ws += row(600, [("Wireless", 28), ("Zone", 70), ("Terms", 100), ("and", 140), ("Conditions", 165)])
    ws += row(614, [("Return", 28), ("policy", 70), ("text", 110)])
    return ws


# ── B2B synthetic layout ──────────────────────────────────────────────────────────────────────────
def b2b_words():
    ws = []
    ws += row(18, [("Sale", 511), ("Receipt", 536)])
    ws += row(39, [("Transaction", 331), ("ID", 381), ("55501", 447)])
    ws += row(54, [("Sale", 331), ("Date", 350), ("5/5/2025", 447), ("12:03", 485), ("PM", 510)])
    ws += row(68, [("Salesperson", 331)])
    ws += row(69, [("Sam", 447), ("T.", 470)])
    ws += row(104, [("Test", 33), ("Store", 62), ("TCC", 90)])
    ws += row(119, [("9", 33), ("Test", 55), ("Rd", 80)])
    ws += row(133, [("BROOKLYN", 33), ("NY", 80), ("11229", 94)])
    ws += row(164, [("PH:", 33), ("555-000-1111", 50), ("FAX", 108), (":", 126), ("555-000-2222", 132)])
    ws += row(228, [("Bill", 34), ("To:", 49), ("Ship", 325), ("To:", 345)])
    ws += row(241, [("ACME", 34), ("CORP", 72), ("ACME", 325), ("CORP", 363)])
    ws += row(256, [("5", 34), ("Trade", 57), ("Way", 120), ("5", 325), ("Trade", 348), ("Way", 411)])
    ws += row(271, [("BROOKLYN,", 34), ("NY", 95), ("ACME2", 325)])
    # item header
    ws += row(299, [("Product", 23), ("ID", 66), ("Qty", 132), ("Description", 153),
                    ("Serial", 353), ("Retail", 449), ("Price", 482), ("Ext.", 526), ("Price", 548)])
    # item with a 2-word description ("cc fee") — must NOT bleed into Qty
    ws += row(314, [("8957", 23), ("1", 145), ("Samsung", 153), ("tab", 189), ("a9+", 202),
                    ("350842063100490", 353), ("$260.00", 468), ("$260.00", 544)])
    ws += row(338, [("1982", 23), ("1", 145), ("cc", 153), ("fee", 164), ("$20.28", 472), ("$20.28", 549)])
    # totals
    ws += row(395, [("Sub", 435), ("Total", 460), ("$280.28", 544)])
    ws += row(409, [("Pre-Tax", 435), ("Subtotal", 465), ("$280.28", 546)])
    ws += row(423, [("Sales", 453), ("tax", 473), ("LI", 487), ("$1.80", 553)])
    ws += row(437, [("Sales", 453), ("tax", 473), ("City", 487), ("$20.00", 553)])
    ws += row(451, [("Total", 453), ("Due", 475), ("$302.08", 553)])
    ws += row(411, [("CREDIT", 26), ("CARD", 56), ("EXTERNAL", 79), ("$302.08", 160)])
    ws += row(500, [("Service", 26), ("Agreement", 70), ("terms", 120)])
    return ws


def main():
    # ── RQ ──
    d = rq.parse(rq_words())
    ok("RQ title", d["title"] == "Sale")
    ok("RQ invoice", any(m["key"] == "invoice_no" and m["value"] == "INV-9" for m in d["meta"]))
    ok("RQ sale_date ISO", any(m["key"] == "sale_date_iso" and m["value"] == "2025-11-28" for m in d["meta"]))
    ok("RQ salesperson", any(m["key"] == "salesperson" and m["value"] == "Jane R" for m in d["meta"]))
    ok("RQ store", d["store"]["lines"][0] == "Test Wireless Store")
    ok("RQ store phone", d["store"]["phone"] == "(555)123-4567")
    ok("RQ bill_to name", d["bill_to"]["name"] == "JANE DOE")
    ok("RQ 3 items", len(d["items"]) == 3)
    c0 = d["items"][0]["cells"]
    ok("RQ long name stays in its column", c0["name"] == "SUPER PHONE 256GB MIDNIGHT BLACK")
    ok("RQ serial not swallowed by name", c0["tracking"] == "111111111111111")
    ok("RQ qty/price/total split", (c0["qty"], c0["price"], c0["total"]) == ("1", "$100.00", "$100.00"))
    ok("RQ item2 has no serial", d["items"][1]["cells"]["tracking"] == "")
    ok("RQ 'Financed Amount' item did NOT stop the table", d["items"][2]["cells"]["sku"] == "SKU3")
    tot = {t["key"]: t["amount"] for t in d["totals"]}
    ok("RQ subtotal", tot.get("subtotal") == 0.0)
    ok("RQ sales_tax", tot.get("sales_tax") == 10.0)
    ok("RQ financed (word-boundary, not the item)", tot.get("financed") == 500.0)
    ok("RQ total != subtotal (word boundary)", tot.get("total") == 110.0)
    ok("RQ payment cash", d["payments"] and d["payments"][0]["amount"] == 110.0)
    ok("RQ footer captured", d["footer_text"] and "Terms and Conditions" in d["footer_text"])
    ok("RQ derived imei", d["derived"]["imei"] == "111111111111111")
    ok("RQ derived total", d["derived"]["total"] == 110.0)
    ok("RQ item editable keys = name/qty/price/total",
       set(d["items"][0]["editable"]) == {"name", "qty", "price", "total"})

    # ── B2B ──
    b = b2b.parse(b2b_words())
    ok("B2B title", b["title"] == "Sale Receipt")
    ok("B2B transaction id", any(m["key"] == "transaction_id" and m["value"] == "55501" for m in b["meta"]))
    ok("B2B sale_date ISO", any(m["key"] == "sale_date_iso" and m["value"] == "2025-05-05" for m in b["meta"]))
    ok("B2B store fax", b["store"]["fax"] == "555-000-2222")
    ok("B2B bill_to name", b["bill_to"]["name"] == "ACME CORP")
    ok("B2B ship_to present", b["ship_to"] and b["ship_to"]["lines"][0] == "ACME CORP")
    ok("B2B ship_to did not eat the table header", all("Serial" not in ln for ln in b["ship_to"]["lines"]))
    ok("B2B 2 items", len(b["items"]) == 2)
    ok("B2B 'cc fee' stays in description, not qty",
       b["items"][1]["cells"]["description"] == "cc fee" and b["items"][1]["cells"]["qty"] == "1")
    tb = {t["key"]: t["amount"] for t in b["totals"]}
    ok("B2B pre-tax subtotal", tb.get("pretax_subtotal") == 280.28)
    ok("B2B sub total (only 'Sub Total', not 'Pre-Tax Subtotal')", tb.get("subtotal") == 280.28)
    ok("B2B two tax lines", tb.get("sales_tax_li") == 1.8 and tb.get("sales_tax_city") == 20.0)
    ok("B2B total due", tb.get("total_due") == 302.08)
    ok("B2B payment", b["payments"] and b["payments"][0]["amount"] == 302.08)
    ok("B2B derived total", b["derived"]["total"] == 302.08)
    ok("B2B derived imei", b["derived"]["imei"] == "350842063100490")

    # ── renderer (generic) ──
    for tag, doc in (("RQ", d), ("B2B", b)):
        h = render.render_html(doc)
        ok(f"{tag} render has all columns", all(c["label"] in h for c in doc["columns"]))
        ok(f"{tag} render has grand total", f"{doc['derived']['total']:,.2f}" in h)
        ok(f"{tag} editable render has contenteditable", 'contenteditable="true"' in render.render_html(doc, editable=True))

    # ── edit round-trip (pure: edit a cell → recompute derived) ──
    d["items"][0]["cells"]["name"] = "EDITED DEVICE NAME"
    d["items"][0]["cells"]["tracking"] = "999999999999999"
    d["derived"] = base.compute_derived(d)
    ok("edit updates derived device_name", d["derived"]["device_name"] == "EDITED DEVICE NAME")
    ok("edit updates derived imei", d["derived"]["imei"] == "999999999999999")

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
