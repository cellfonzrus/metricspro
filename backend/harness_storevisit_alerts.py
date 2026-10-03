"""PROOF: STORE VISIT FOLLOW-THROUGH — the to-do alert, the separate accessory notification, and the
draft purchase order.

OWNER ASK 2026-10-03, verbatim:
    "based on the store visits need to create an email and whats app alert to the dm and all people
     above to send them a lit of all items which are needed to be done, also create a notification
     list for the store visit, default will be dm and above, a list of accesories to be created as a
     separate notification and a purchase oirder automatically created to be sent to vaccessorize"

WHAT THIS PINS
  A. the config is per tenant with house defaults, OFF on deploy, and a bad value degrades to the
     default rather than to "never alert" or "alert about everything";
  B. what counts as OPEN work from a visit — including that an unanswered check is not a pass;
  C. the DM-and-above default: a tenant with no notification list gets the org tree, a tenant with
     one gets both, and only a tenant that has a list and clears include_dm on all of it gets "just
     these people". A tenant cannot silence a scope by forgetting to configure anything;
  D. a named recipient and a resolved manager are ONE digest when they are the same address;
  E. the two notifications are two scopes with two dedup trails, and the dedup tail is (visit, item)
     — NOT the date, because a visit is an event, not a daily state;
  F. accessories merge by (store, item) so one order asks for one line, and a quantity change is
     news while the same line at the same quantity is not;
  G. the purchase order is a DRAFT, there is no 'submit' mode, an unpriced line is NAMED rather than
     dropped, and the total says it is a floor;
  H. the digests — both renderings carry the same facts, free text is escaped, and the unowned
     footer is present whenever work has no store;
  I. migration 1046 is tied to the code it configures and is OFF by default;
  J. NO second fan-out, NO second dedup, NO second recipient list, NO second PO insert, RULE TWO,
     and the locks are ARMED (each has a control that proves the scan can fail).

PURE: stdlib only, no DB, no network. Run: `cd backend && python3 harness_storevisit_alerts.py`
"""
import ast
import io
import sys
import tokenize

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


from app.modules.storevisit import visit_alerts as V       # noqa: E402
from app.modules.commcalc import manager_digest as MD      # noqa: E402


def code_text(path):
    """Executable text with docstrings and comments removed, spacing preserved. A lock that scans
    token-joined text can never match a multi-token pattern, which is how a build lock passes
    vacuously (the 2026-10-03 near-miss in harness_billpay_fee_basis.py §H6)."""
    src = open(path).read()
    lines = src.splitlines(keepends=True)
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                for i in range(body[0].lineno - 1, min(body[0].end_lineno, len(lines))):
                    lines[i] = "\n"
    kept = "".join(lines)
    cuts = {}
    for tok in tokenize.generate_tokens(io.StringIO(kept).readline):
        if tok.type == tokenize.COMMENT:
            cuts.setdefault(tok.start[0], tok.start[1])
    return "\n".join(ln[:cuts[n]] if n in cuts else ln
                     for n, ln in enumerate(kept.splitlines(), start=1))


TODAY = "2026-10-03"
VA_PATH = "app/modules/storevisit/visit_alerts.py"
RT_PATH = "app/modules/storevisit/router.py"
MD_PATH = "app/modules/commcalc/manager_digest.py"
SUPPLY_PATH = "app/modules/supply/store.py"
MIG = "../database/migrations/1046_store_visit_alerts.sql"

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. config: per tenant, house defaults, OFF on deploy, bad values degrade ==")
d = V.resolve_config(None)
check("A1 no tenant row -> the house defaults", d["enabled"] is False
      and d["accessory_enabled"] is False and d["lookback_days"] == 7
      and d["po_mode"] == "off" and d["min_items"] == 1, d)
check("A2 BOTH alerts are OFF by default, so nothing sends on deploy",
      V.HOUSE_CONFIG["enabled"] is False and V.HOUSE_CONFIG["accessory_enabled"] is False)
check("A3 the owner asked for email AND WhatsApp, so both are default channels",
      set(V.HOUSE_CONFIG["channels"]) == {"whatsapp", "email"}
      and set(V.HOUSE_CONFIG["accessory_channels"]) == {"whatsapp", "email"})
check("A4 the channel vocabulary is the ONE home's, not a list spelled here",
      set(V.HOUSE_CONFIG["channels"]) <= set(MD.CHANNEL_ADDRESS_FIELD))
check("A5 a tenant's own values win",
      V.resolve_config({"store_visit_alert_enabled": True,
                        "store_visit_alert_channels": ["email"],
                        "store_visit_alert_lookback_days": 3,
                        "store_visit_alert_min_items": 4,
                        "store_visit_accessory_alert_enabled": True}) |
      {} == {**V.HOUSE_CONFIG, "enabled": True, "channels": ("email",), "lookback_days": 3,
             "min_items": 4, "accessory_enabled": True,
             "accessory_channels": MD.normalize_channels(V.HOUSE_CONFIG["accessory_channels"]),
             "accessory_vendor_id": None, "po_mode": "off"})
for bad in ("x", None, "", 0, -5):
    check(f"A6 an unusable lookback {bad!r} degrades to 7 days, never to 0 (which would look at "
          f"nothing and alert about nothing)",
          V.resolve_config({"store_visit_alert_lookback_days": bad})["lookback_days"] == 7)
check("A7 an absurd lookback is clamped rather than reading the whole history every tick",
      V.resolve_config({"store_visit_alert_lookback_days": 99999})["lookback_days"] == 90)
check("A8 min_items never falls to 0, which would alert about a visit with nothing open",
      V.resolve_config({"store_visit_alert_min_items": 0})["min_items"] == 1)
check("A9 an unknown po_mode degrades to 'off', never to one that acts",
      V.resolve_config({"store_visit_accessory_po_mode": "submit"})["po_mode"] == "off"
      and V.resolve_config({"store_visit_accessory_po_mode": "YOLO"})["po_mode"] == "off")
_nv = V.resolve_config({"store_visit_accessory_po_mode": "draft"})
check("A10 'draft' with NO vendor configured resolves to 'off' and SAYS WHY — a draft with nobody "
      "to raise it against is a failure waiting to happen at send time",
      _nv["po_mode"] == "off" and bool(_nv.get("po_mode_reason")), _nv)
_wv = V.resolve_config({"store_visit_accessory_po_mode": "draft",
                        "store_visit_accessory_vendor_id": "11111111-1111-1111-1111-111111111111"})
check("A11 'draft' WITH a vendor stands", _wv["po_mode"] == "draft"
      and _wv["accessory_vendor_id"] == "11111111-1111-1111-1111-111111111111")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. what a visit left to be done ==")
VISIT = {"id": "v1", "store_code": "S1", "market": "M", "submitted_at": "2026-10-03T12:00:00Z",
         "scheduled_rep": "A", "actual_rep": "B", "clean_store_photo_path": "p/x.jpg"}
RESP = [{"item_key": "k_pass", "label_snapshot": "Passed", "checked": True},
        {"item_key": "k_fail", "label_snapshot": "Window display", "checked": False,
         "note": "faded"},
        {"item_key": "k_blank", "label_snapshot": "Price tags", "checked": None}]
ITEMS = [{"item_key": "a_done", "title": "Talked about", "discussed": True},
         {"item_key": "a_open", "title": "Attach rate", "rep": "B", "detail": "12% vs 30%"}]
PLAN = [{"id": "p_done", "description": "Already done", "status": "done"},
        {"id": "p_open", "description": "Reset the planogram", "status": "open",
         "due_date": "2026-10-01", "rep": "B"},
        {"id": "p_future", "description": "Order signage", "status": "open",
         "due_date": "2026-12-01"}]
todos = V.todo_items(VISIT, RESP, ITEMS, PLAN, as_of=TODAY)
kinds = sorted(t["kind"] for t in todos)
check("B1 a PASSED check is not open work", not any(t["item_key"] == "checklist:k_pass"
                                                   for t in todos))
check("B2 a FAILED check is open work", any(t["item_key"] == "checklist:k_fail" for t in todos))
check("B3 an UNANSWERED check is open work — an unanswered check is not a pass",
      any(t["item_key"] == "checklist:k_blank" for t in todos))
check("B4 a discussed action item is closed; an undiscussed one is open",
      any(t["item_key"] == "action_item:a_open" for t in todos)
      and not any(t["item_key"] == "action_item:a_done" for t in todos))
check("B5 a finished plan step is closed, whatever word was used for finished",
      not any(t["item_key"] == "action_plan:p_done" for t in todos)
      and all(s in V.PLAN_DONE_STATUSES for s in ("done", "closed", "cancelled", "completed")))
_od = [t for t in todos if t["item_key"] == "action_plan:p_open"][0]
_fu = [t for t in todos if t["item_key"] == "action_plan:p_future"][0]
check("B6 a step past its due date is overdue; one still ahead is not",
      _od["is_overdue"] is True and _fu["is_overdue"] is False)
check("B7 a step with NO due date is never overdue — a step we cannot date must not be reported "
      "as late", V.overdue(None, TODAY) is False and V.overdue("not-a-date", TODAY) is False)
check("B8 a rep change with no reason recorded is open work",
      any(t["kind"] == "rep_coverage" for t in todos))
check("B9 a rep change WITH a reason is answered, not open work",
      not any(t["kind"] == "rep_coverage" for t in V.todo_items(
          {**VISIT, "rep_discrepancy_reason": "swapped, approved"}, RESP, ITEMS, PLAN, TODAY)))
check("B10 the same rep scheduled and actual is not a discrepancy",
      not any(t["kind"] == "rep_coverage" for t in V.todo_items(
          {**VISIT, "actual_rep": "A"}, RESP, ITEMS, PLAN, TODAY)))
check("B11 a missing clean-store photo is ONE evidence item, not a list",
      len([t for t in V.todo_items({**VISIT, "clean_store_photo_path": ""}, RESP, ITEMS, PLAN,
                                   TODAY) if t["kind"] == "evidence"]) == 1
      and not any(t["kind"] == "evidence" for t in todos))
check("B12 every item carries the store and the visit, so a digest can be addressed at all",
      all(t["store_code"] == "S1" and t["visit_id"] == "v1" for t in todos))
check("B13 every kind the module can emit is in the ONE vocabulary",
      set(kinds) <= set(V.kind_labels()), sorted(set(kinds) - set(V.kind_labels())))
check("B14 garbage rows are skipped, not crashed on",
      V.todo_items(VISIT, [None, {}, {"checked": False}], [None, {}], [None, {}], TODAY) is not None)
_sm = V.summarize(todos)
check("B15 the roll-up counts what the list holds",
      _sm["totals"]["open"] == len(todos) and _sm["totals"]["visits"] == 1
      and _sm["by_store"]["S1"] == len(todos))
check("B16 oldest_due is the earliest OVERDUE date, and None when nothing is overdue",
      _sm["totals"]["oldest_due"] == "2026-10-01"
      and V.summarize([_fu])["totals"]["oldest_due"] is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. the notification list: DM and above is the DEFAULT, not a thing you can lose ==")
check("C1 NO list configured -> the org tree (DM and above) is used — the owner's default",
      MD.use_hierarchy([]) is True and MD.use_hierarchy(None) is True)
check("C2 a list that keeps include_dm -> the tree AND the named people",
      MD.use_hierarchy([{"include_dm": True, "email": "a@x"}]) is True)
check("C3 only a tenant that HAS a list and cleared include_dm on all of it gets just those people",
      MD.use_hierarchy([{"include_dm": False, "email": "a@x"}]) is False)
check("C4 a scope's rows are its own plus the catch-all 'all', the rule the one list already uses",
      [m["email"] for m in MD.named_extras(
          [{"scope": "store_visit_todo", "email": "a@x"},
           {"scope": "all", "email": "b@x"},
           {"scope": "something_else", "email": "c@x"}],
          V.ALERT_SCOPE, ("email",))] == ["a@x", "b@x"])
check("C5 the two notifications are TWO scopes, so one list can serve different people",
      V.ALERT_SCOPE != V.ACCESSORY_SCOPE
      and [m["email"] for m in MD.named_extras(
          [{"scope": "store_visit_todo", "email": "a@x"}], V.ACCESSORY_SCOPE, ("email",))] == [])
check("C6 via_email off means that row is not an email address",
      MD.named_extras([{"scope": "all", "email": "a@x", "via_email": False}],
                      V.ALERT_SCOPE, ("email",)) == [])
check("C7 a whatsapp number is only a whatsapp address when via_whatsapp is on",
      [m["phone"] for m in MD.named_extras(
          [{"scope": "all", "whatsapp": "+15551234567", "via_whatsapp": True}],
          V.ALERT_SCOPE, ("whatsapp",))] == ["+15551234567"]
      and MD.named_extras([{"scope": "all", "whatsapp": "+15551234567", "via_whatsapp": False}],
                          V.ALERT_SCOPE, ("whatsapp",)) == [])
check("C8 the row's 'whatsapp' column is mapped to the ONE channel field name, so no caller "
      "learns a second spelling", MD.CHANNEL_ADDRESS_FIELD["whatsapp"] == "phone")
check("C9 a row with no usable address on any requested channel is not a recipient",
      MD.named_extras([{"scope": "all", "name": "Nobody"}], V.ALERT_SCOPE, ("email", "whatsapp"))
      == [])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. one digest per recipient, however they were found ==")
HIER = {"S1": {"dm": [{"name": "Dee", "email": "dm@x", "phone": "+1555000111"}],
               "above": [{"name": "Ava", "email": "vp@x"}]},
        "S2": {"dm": [{"name": "Dee", "email": "dm@x"}], "above": []}}
ITEMS2 = [{"store_code": "S1", "visit_id": "v1", "item_key": "checklist:a", "kind": "checklist",
           "title": "A", "is_overdue": False},
          {"store_code": "S2", "visit_id": "v2", "item_key": "checklist:b", "kind": "checklist",
           "title": "B", "is_overdue": False}]


def _b(name, its):
    return V.build_digest(name, its)


fan = MD.plan_digests(ITEMS2, HIER, "", scope=V.ALERT_SCOPE, build=_b, key_parts=V.key_parts,
                      channels=("email", "whatsapp"))
check("D1 the DM and everyone above get a digest — the owner's ask, from the ONE house rule",
      sorted(g["to"] for g in fan["digests"]) == ["dm@x", "vp@x"])
dm = [g for g in fan["digests"] if g["to"] == "dm@x"][0]
check("D2 a DM over two stores gets ONE digest carrying both", len(dm["items"]) == 2)
check("D3 a manager reached by two paths is listed once",
      len({i["ref_key"] for i in dm["items"]}) == 2)
fan2 = MD.plan_digests(ITEMS2, HIER, "", scope=V.ALERT_SCOPE, build=_b, key_parts=V.key_parts,
                       channels=("email",),
                       extra_recipients=MD.named_extras(
                           [{"scope": "all", "name": "Ops", "email": "ops@x"},
                            {"scope": "all", "name": "Dee again", "email": "DM@x"}],
                           V.ALERT_SCOPE, ("email",)))
check("D4 a NAMED recipient gets every store's items — they asked about the scope, not a store",
      len([g for g in fan2["digests"] if g["to"] == "ops@x"][0]["items"]) == 2)
check("D5 someone who is BOTH named and in the tree gets ONE digest, because the identity is the "
      "address, not how they were found",
      [g["to"] for g in fan2["digests"]].count("dm@x") == 1
      and len([g for g in fan2["digests"] if g["to"] == "dm@x"][0]["items"]) == 2)
fan3 = MD.plan_digests(ITEMS2, HIER, "", scope=V.ALERT_SCOPE, build=_b, key_parts=V.key_parts,
                       channels=("email",), use_tree=False,
                       extra_recipients=[{"name": "Ops", "email": "ops@x"}])
check("D6 use_tree False means just the named list", [g["to"] for g in fan3["digests"]] == ["ops@x"])
check("D7 a recipient with no address on any requested channel is skipped, never crashed on",
      [g["to"] for g in MD.plan_digests(
          ITEMS2, {"S1": {"dm": [{"name": "No contact"}], "above": []}, "S2": {"dm": [], "above": []}},
          "", scope=V.ALERT_SCOPE, build=_b, key_parts=V.key_parts)["digests"]] == [])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. dedup: per (visit, item), NOT per day ==")
k = V.key_parts({"visit_id": "v1", "item_key": "checklist:k_fail"})
check("E1 the tail is the visit and the thing inside it", k == ("v1", "checklist:k_fail"))
rk = MD.ref_key(V.ALERT_SCOPE, "", "DM@X", *k)
check("E2 the ref_key is the house spelling, with the email folded",
      rk == "store_visit_todo||dm@x|v1|checklist:k_fail", rk)
check("E3 the SAME item on a later day is the SAME key — a visit is an event, so its to-do list is "
      "announced once, not re-sent every morning",
      MD.ref_key(V.ALERT_SCOPE, "", "dm@x", *k) == rk)
check("E4 a NEW item on the same visit is a new key, so later work is still announced",
      MD.ref_key(V.ALERT_SCOPE, "", "dm@x", "v1", "action_item:new") != rk)
check("E5 the two notifications dedup separately",
      MD.ref_key(V.ACCESSORY_SCOPE, "", "dm@x", *k) != rk)
ak = V.accessory_key_parts({"visit_id": "v1", "key": "case", "qty": 5})
check("E6 an accessory's tail carries the QUANTITY, so asking for more is news",
      ak == ("v1", "case", 5)
      and V.accessory_key_parts({"visit_id": "v1", "key": "case", "qty": 9}) != ak)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. the accessory list ==")
ACC = [{"visit_id": "v1", "accessory_name": "Clear case", "qty": 5, "note": "small"},
       {"visit_id": "v2", "accessory_name": "clear  CASE", "qty": 5},
       {"visit_id": "v1", "accessory_name": "Screen protector", "qty": 2},
       {"visit_id": "v3", "accessory_name": "Clear case", "qty": 1},
       {"visit_id": "v1", "accessory_name": "   ", "qty": 9}]
VMAP = {"v1": {"store_code": "S1"}, "v2": {"store_code": "S1"}, "v3": {"store_code": "S2"}}
lines = V.accessory_lines(ACC, VMAP)
case1 = [l for l in lines if l["store_code"] == "S1" and l["key"] == "clear case"][0]
check("F1 the same item at the same store merges — two reps asking for five is one order for ten",
      case1["qty"] == 10 and case1["name"] == "Clear case")
check("F2 the merge folds case and inner spacing, which is how a human types the same thing twice",
      len([l for l in lines if l["key"] == "clear case" and l["store_code"] == "S1"]) == 1)
check("F3 the SAME item at a DIFFERENT store is a different line — they ship to different places",
      len([l for l in lines if l["key"] == "clear case"]) == 2)
check("F4 a nameless row is dropped, because there is nothing to order",
      all(l["name"].strip() for l in lines))
check("F5 notes are kept and de-duplicated, never silently lost", case1["notes"] == "small")
check("F6 every visit that asked is remembered on the line", sorted(case1["visits"]) == ["v1", "v2"])
check("F7 a quantity of 0 or junk still orders one, never zero — the ask was real",
      V.accessory_lines([{"visit_id": "v1", "accessory_name": "X", "qty": 0}],
                        VMAP)[0]["qty"] == 1
      and V.accessory_lines([{"visit_id": "v1", "accessory_name": "X", "qty": "many"}],
                            VMAP)[0]["qty"] == 1)
check("F8 the lines are stably ordered, so one order reads the same twice",
      [l["name"] for l in lines] == [l["name"] for l in V.accessory_lines(ACC, VMAP)])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. the purchase order is a DRAFT, and never claims a price it does not have ==")
check("G1 there is no 'submit' mode — no transport to a supplier exists, and a mode that silently "
      "did nothing would be worse than refusing the word",
      "submit" not in V.PO_MODES and set(V.PO_MODES) == {"off", "draft"})
po = V.po_draft(lines, "vend-1", "Supplier",
                unit_costs={"clear case": 3.5}, ship_to_store="S1", market="M")
check("G2 a priced line is extended by quantity",
      [l for l in po["lines"] if l["device_model"] == "Clear case"][0]["extended_cost"]
      == round(3.5 * 10, 2))
check("G3 an UNPRICED line is kept and NAMED, never dropped so the total looks complete",
      "Screen protector" in po["unpriced"]
      and any(l["device_model"] == "Screen protector" for l in po["lines"]))
check("G4 with any line unpriced the total is declared a FLOOR, so nobody reads it as the price",
      po["total_is_floor"] is True)
check("G5 with every line priced the total is not a floor",
      V.po_draft([lines[0]], "v", "S", unit_costs={lines[0]["key"]: 1})["total_is_floor"] is False)
check("G6 the total is the sum of the lines, and shipping is not invented",
      po["total"] == po["subtotal"] == round(sum(l["extended_cost"] for l in po["lines"]), 2)
      and po["shipping_estimate"] == 0.0)
check("G7 the draft is in the shape the ONE PO path consumes (mig-301 line columns)",
      all(set(l) >= {"line_no", "device_model", "qty_ordered", "unit_cost", "extended_cost"}
          for l in po["lines"]))
check("G8 every visit that contributed is recorded on the order",
      sorted(po["meta"]["visits"]) == ["v1", "v2", "v3"])
check("G9 a junk unit cost is treated as NO price, not as zero dollars agreed",
      "Clear case" in V.po_draft(lines, "v", "S",
                                 unit_costs={"clear case": "about three"})["unpriced"])
check("G10 the PO source names where it came from, in the column every PO reader already filters on",
      V.PO_SOURCE == "store_visit")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. the digests ==")
dg = V.build_digest("Dee", todos, totals={**_sm["totals"], "no_store": 2}, link="https://x/y")
check("H1 the subject leads with the count and names the overdue work",
      "to do" in dg["subject"] and "past due" in dg["subject"], dg["subject"])
check("H2 both renderings exist and the WhatsApp one is the short one",
      dg["html"] and dg["text"] and len(dg["text"]) < len(dg["html"]))
check("H3 every open item appears in the email",
      all(t["title"] in dg["html"] for t in todos if "&" not in t["title"]))
check("H4 the overdue step is called out in BOTH renderings",
      "past due" in dg["html"].lower() and "PAST DUE" in dg["text"])
check("H5 work with NO store is counted in the footer, never silently dropped — the ownership rule",
      "cannot be assigned to a manager" in dg["html"])
check("H6 with nothing unowned there is no footer claiming there is",
      "cannot be assigned" not in V.build_digest("Dee", todos, totals=_sm["totals"])["html"])
check("H7 the link is offered when there is one, and nothing is faked when there is not",
      "https://x/y" in dg["html"] and "href" not in V.build_digest("Dee", todos)["html"])
_evil = V.build_digest("Dee <b>", [{"store_code": "S&1", "visit_id": "v", "kind": "checklist",
                                    "item_key": "k", "title": "<script>x</script>",
                                    "is_overdue": False, "due_date": None}])
check("H8 free text typed by a DM is ESCAPED — a note with a tag in it must not become markup",
      "<script>" not in _evil["html"] and "&lt;script&gt;" in _evil["html"]
      and "S&amp;1" in _evil["html"])
ad = V.build_accessory_digest("Dee", lines, vendor_name="Supplier", link="https://x/y",
                              po={"po_number": "PO-9", "total": 35.0, "unpriced": ["Screen protector"]})
check("H9 the accessory digest counts lines AND units, which are different numbers",
      "unit" in ad["subject"] and str(sum(l["qty"] for l in lines)) in ad["subject"], ad["subject"])
check("H10 it says the order is a DRAFT and that nothing was sent — it must never imply otherwise",
      "DRAFT" in ad["html"] and "nothing has been sent" in ad["html"]
      and "not sent" in ad["text"])
check("H11 an unpriced line makes the stated total a floor in the notification too",
      "floor" in ad["html"])
check("H12 with no draft raised it says so rather than leaving a blank",
      "No draft purchase order was raised" in V.build_accessory_digest(
          "Dee", lines, vendor_name="Supplier")["html"])
check("H13 the accessory digest escapes free text as well",
      "&lt;b&gt;" in V.build_accessory_digest("D", [{"store_code": "S", "name": "<b>",
                                                     "qty": 1, "notes": None}])["html"])
check("H14 an empty list still renders rather than raising",
      V.build_digest("D", [])["subject"] and V.build_accessory_digest("D", [])["subject"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. migration 1046 is tied to the code it configures ==")
mig = open(MIG).read()
for col in ("store_visit_alert_enabled", "store_visit_alert_channels",
            "store_visit_alert_lookback_days", "store_visit_alert_min_items",
            "store_visit_accessory_alert_enabled", "store_visit_accessory_channels",
            "store_visit_accessory_vendor_id", "store_visit_accessory_po_mode"):
    check(f"I1 {col} is added by the migration", f"IF NOT EXISTS {col}" in mig)
check("I2 every config column the code reads is in the migration",
      all(c in mig for c in ("store_visit_alert_enabled", "store_visit_accessory_po_mode")))
check("I3 both switches default FALSE, so applying the migration sends nothing",
      mig.count("boolean NOT NULL DEFAULT false") == 2)
check("I4 po_mode defaults 'off' and the database REFUSES any mode the code does not implement",
      "store_visit_accessory_po_mode        text    NOT NULL DEFAULT 'off'" in mig
      and "CHECK (store_visit_accessory_po_mode IN ('off','draft'))" in mig)
check("I5 the idempotency key is a UNIQUE index, so one visit cannot raise two drafts",
      "CREATE UNIQUE INDEX IF NOT EXISTS ux_po_org_store_visit" in mig
      and "WHERE store_visit_id IS NOT NULL" in mig)
check("I6 it is additive and idempotent (no DROP/DELETE/UPDATE outside the revert note)",
      all(w not in mig.split("-- ── 1.")[1].upper() for w in ("DROP TABLE", "DELETE FROM",
                                                              "TRUNCATE")))
check("I7 it carries a REVERT note", "REVERT:" in mig)
check("I8 it does NOT create a second recipient table or a second dedup table",
      "CREATE TABLE" not in mig)
check("I9 the schema cache is reloaded, or PostgREST would 404 the new columns",
      "NOTIFY pgrst" in mig)
check("I10 the migration number is unique in the tree",
      len([1 for p in __import__("os").listdir("../database/migrations")
           if p.startswith("1046_")]) == 1)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== J. no second mechanism — the LOCKS ==")
va = code_text(VA_PATH)
rt = code_text(RT_PATH)
md = code_text(MD_PATH)
sup = code_text(SUPPLY_PATH)
blk = rt.split("STORE VISIT FOLLOW-THROUGH ALERTS")[-1] if "STORE VISIT FOLLOW-THROUGH ALERTS" in rt \
    else rt[rt.index("def _tenant_row("):]
check("J1 the pure module is PURE: no DB, no network, no framework",
      not any(s in va for s in ("import requests", "supabase", "fastapi", "httpx",
                                "get_supabase")))
check("J2 LOCK: the sweep fans out through the ONE home and resolves no recipients itself",
      "_md.plan_digests(" in blk and "plan_digests" in md
      and "recipients_for" not in blk and "\"above\"" not in blk and "'above'" not in blk)
check("J3 LOCK: the sweep writes NO digest markup of its own — one renderer, so email and WhatsApp "
      "cannot drift", "<table" not in blk and "<p>" not in blk)
check("J4 LOCK: the notification list is read from the ONE table, in ONE place",
      blk.count('table("alert_recipient")') == 1)
check("J5 LOCK: the dedup trail is the EXISTING alert_log pair, never a second insert",
      '_lateness_already_sent' in blk and '_lateness_record_sent' in blk
      and 'table("alert_log")' not in blk)
check("J6 LOCK: the purchase order goes through the ONE PO path — no insert into the PO tables here",
      "_supply_store.create_order(" in blk
      and 'table("purchase_order")' not in blk.replace(
          'table("purchase_order").select("store_visit_id")', "")
      and "purchase_order_line" not in blk)
check("J7 LOCK: the ONE PO path still numbers from the ONE numbering home",
      "_next_po_number" in sup and "create_order" in sup)
check("J8 LOCK: the supply-cart-only columns are written ONLY for a supply cart, so a caller on a "
      "database without migration 1021 is not broken by columns it does not use",
      "if source == L.SUPPLY_SOURCE:" in sup
      and sup.count('"supply_cart_ref": cart_ref') == 1
      and sup.split("if source == L.SUPPLY_SOURCE:")[1].split("row.update({k: v")[0].count(
          '"supply_cart_ref": cart_ref') == 1)
check("J9 LOCK: the sweep never submits an order to a supplier",
      not any(s in blk for s in ("/orders/", "submit_order", "place_order"))
      and '"status": "draft"' in blk)
check("J10 LOCK: the dedup ref_key is spelled by the one home, never here",
      "ref_key(" not in va.replace("def accessory_key_parts", "").replace("def key_parts", "")
      or "def ref_key" in md)
_low = va.lower()
for banned in ("vaccessorize", "boost", "epay", "cellfonz", "luxelink", "xfinity", "shopify"):
    check(f"J11 RULE TWO: {banned!r} appears in no executable line of the pure module",
          banned not in _low)
check("J12 control: the RULE TWO scan can fail — a word that IS in the module is found",
      "accessory" in _low)
check("J13 control: the scan dropped the prose (the docstring's 'owner ask' is gone)",
      "owner ask" in open(VA_PATH).read().lower() and "owner ask" not in _low)
check("J14 control: the router block really was isolated (it holds the sweep and not the whole file)",
      "_run_store_visit_alerts" in blk and "def upload_photo" not in blk)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
