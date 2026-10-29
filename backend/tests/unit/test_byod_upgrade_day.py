"""Unit: the BYOD-and-upgrade-on-one-number-in-one-day detector (index §COMMCALC-43).

Pure logic over fixtures shaped like the live sales feed. No app, no I/O, no database.

SCOPE, stated so this file is not mistaken for the whole gate. The exhaustive proof of this module
is `backend/harness_byod_upgrade_day.py` (55 checks, mutation-tested, and the thing that was written
and run RED before the module existed). What is here is the handful of facts a reader of
`tests/unit/` should be able to see without opening a harness, and the two REGRESSIONS that cost the
most to learn:

  · the unit is an activation EVENT, never a sale line (one upgrade is five or six lines, so a line
    count read 456 number-days where an event count reads 95), and
  · a single line reading 'BYOD Upgrade' is ONE event by the org's class precedence, so it cannot
    pair with itself — a detector looking for the two words reports a case on every one of them.
"""
from app.modules.commcalc import byod_upgrade_day as M
from app.modules.commcalc import flag_registry as REG
from app.modules.commcalc import line_class as LC

HOUSE = REG.default_params(M.FLAG_TYPE)


def line(trans_id, contract_type, *, mdn="2155550143", store="B-1115", day="2026-10-04",
         department="Plans", product_desc="Unlimited Plus", ext_price=0.0, **kw):
    return {"trans_id": trans_id, "contract_type": contract_type, "mdn": mdn, "store": store,
            "trans_date": day, "department": department, "product_desc": product_desc,
            "ext_price": ext_price, "salesperson": "Arora, Tanish", "voided": None,
            "trans_type": "Sale", "category": "", "serial_1": "", **kw}


def _cases(rows, params=None, **kw):
    return M.cases(M.number_days(rows, LC.HOUSE_RULES), params or HOUSE, **kw)


def test_two_receipts_on_one_number_in_one_day_is_a_case():
    rows = [line("T-1", "BYOD"), line("T-1", "BYOD", department="Autopay"),
            line("T-2", "Upgrade"), line("T-2", "Upgrade", department="Handsets",
                                         product_desc="moto g play - 2026 Q4 Promo Upgrade",
                                         ext_price=0.99)]
    found = _cases(rows)
    assert len(found) == 1
    c = found[0]
    assert (c["store"], c["day"], c["number"]) == ("B-1115", "2026-10-04", "2155550143")
    assert sorted(c["classes"]) == ["byod", "upgrade"]
    assert sorted(c["receipts"]) == ["T-1", "T-2"]


def test_one_upgrade_spread_over_six_lines_is_not_a_pair():
    # THE COUNTING TRAP: a contract type is a property of the RECEIPT, so every line of one upgrade
    # reads 'Upgrade'. Six lines, one event, no pair.
    rows = [line("T-9", "Upgrade", department=d) for d in
            ("Plans", "Autopay", "Protection", "Handsets", "Accessories", "Fees")]
    assert _cases(rows) == []


def test_a_single_byod_upgrade_line_is_one_event_not_a_pair():
    # 46 live lines read 'BYOD Upgrade'. `activation_class` resolves it to byod because the org's
    # declared precedence puts byod first. One event — it cannot pair with itself.
    assert LC.activation_class(line("T-5", "BYOD Upgrade"), LC.HOUSE_RULES) == "byod"
    assert _cases([line("T-5", "BYOD Upgrade"), line("T-5", "BYOD Upgrade", department="Autopay")]) == []


def test_a_different_number_a_different_day_or_a_different_store_is_not_a_case():
    for diff in ({"mdn": "2155550199"}, {"day": "2026-10-05"}, {"store": "B-1598"}):
        rows = [line("T-1", "BYOD"), line("T-2", "Upgrade", **diff)]
        assert _cases(rows) == [], diff


def test_the_pair_is_the_org_s_declared_classes_and_two_is_the_floor():
    # RULE TWO: which classes make a pair is a config row. One class declared can never pair.
    rows = [line("T-1", "BYOD"), line("T-2", "Upgrade")]
    assert _cases(rows, {M.CLASSES_KEY: ["byod"]}) == []
    assert _cases(rows, {M.CLASSES_KEY: []}) == []
    assert len(_cases(rows, {M.CLASSES_KEY: ["byod", "upgrade"]})) == 1


def test_a_handset_count_is_none_when_no_box_vocabulary_was_declared():
    # "Not measured" never renders as a confident zero (§19.26). With no is_box predicate the
    # handset counts are None and the period's vocabulary fit is unknown, not False.
    rows = [line("T-1", "BYOD"), line("T-2", "Upgrade", department="Handsets")]
    c = _cases(rows)[0]
    assert c["first_handsets"] is None and c["second_handsets"] is None
    s = M.summary(M.number_days(rows, LC.HOUSE_RULES), [c])
    assert s["box_vocabulary_matched"] is None


def test_the_measured_shape_of_the_live_cases_is_what_the_screen_shows():
    # All 95 live cases (Aug-Oct 2026) carry this shape: no handset on the BYOD side, a promo
    # handset on the upgrade side, on two receipts.
    rows = [line("T-1", "BYOD"), line("T-2", "Upgrade"),
            line("T-2", "Upgrade", department="Handsets", ext_price=0.99,
                 product_desc="moto g play - 2026 Q4 Promo Upgrade")]
    is_box = lambda r: r.get("department") == "Handsets"
    c = _cases(rows, is_box=is_box)[0]
    assert c["first_handsets"] == 0 and c["second_handsets"] == 1
    s = M.summary(M.number_days(rows, LC.HOUSE_RULES), [c], is_box=is_box)
    assert s["box_vocabulary_matched"] is True and s["handset_lines"] == 1


def test_the_module_states_its_limits_and_returns_no_verdict():
    assert len(M.KEY_CANNOT_ANSWER) >= 4
    c = _cases([line("T-1", "BYOD"), line("T-2", "Upgrade")])[0]
    assert "verdict" not in c
    assert isinstance(M.describe(c), str) and M.describe(c)


def test_a_voided_line_and_a_return_are_not_an_activation():
    rows = [line("T-1", "BYOD", voided="Y"), line("T-2", "Upgrade", trans_type="Return")]
    assert _cases(rows) == []


def test_the_flag_type_is_registered_with_its_house_params():
    assert REG.label_of(M.FLAG_TYPE) and REG.area_of(M.FLAG_TYPE) == "fraud"
    assert HOUSE[M.CLASSES_KEY] == ["byod", "upgrade"]


# ── The one-line summary and the chargeback amount (owner ask 2026-10-28) ─────────────────────────
# Owner: "include the $ amount of the rebate and title it as chargeback amount, make it one line
# summary top show the sales rep the store the phone sold and the phone number meid and the $ amount
# and then drill down like other menus to get more details of both transactions".

def _pair_with_a_handset():
    return [line("T-1", "BYOD"),
            line("T-1", "BYOD", department="Autopay"),
            line("T-2", "Upgrade"),
            line("T-2", "Upgrade", department="Android - XP", ext_price=0.92,
                 product_desc="moto g play - 2026 Q4 Promo Upgrade", serial_1="351449743101051")]


def _is_box(r):
    return r.get("department") == "Android - XP"


def test_the_summary_line_names_the_rep_the_store_the_phone_and_its_meid():
    c = _cases(_pair_with_a_handset(), is_box=_is_box)[0]
    assert c["rep"] == "Arora, Tanish"
    assert c["store"] == "B-1115"
    assert c["number"] == "2155550143"
    assert c["phone_sold"] == "moto g play - 2026 Q4 Promo Upgrade"
    assert c["meid"] == "351449743101051"


def test_the_phone_sold_is_the_handset_on_the_upgrade_side_not_the_brought_device():
    # The BYOD side carries the customer's OWN phone. Naming that as "the phone sold" would report
    # the device the store did not sell, which is the opposite of the question.
    rows = _pair_with_a_handset()
    rows[0] = line("T-1", "BYOD", department="Android - XP", serial_1="357217740874028",
                   product_desc="A brought-in handset")
    c = _cases(rows, is_box=_is_box)[0]
    assert c["meid"] == "351449743101051"
    assert c["phone_sold"] == "moto g play - 2026 Q4 Promo Upgrade"


def test_no_box_vocabulary_means_the_phone_and_meid_are_unmeasured_not_blank():
    c = _cases(_pair_with_a_handset())[0]            # no is_box injected
    assert c["phone_sold"] is None and c["meid"] is None


def test_two_reps_on_one_case_names_both_rather_than_picking_one():
    rows = _pair_with_a_handset()
    rows[2] = line("T-2", "Upgrade", salesperson="Ranganath, Vinay")
    c = _cases(rows, is_box=_is_box)[0]
    assert c["rep"] == "Arora, Tanish + Ranganath, Vinay"


def test_the_chargeback_amount_is_the_carrier_money_against_the_phone_sold():
    rebate = {"351449743101051": 249.0}
    c = _cases(_pair_with_a_handset(), is_box=_is_box, rebate_of=rebate.get)[0]
    assert c["chargeback_amount"] == 249.0
    assert c["chargeback_measured"] is True


def test_no_carrier_row_for_the_phone_is_unmeasured_never_zero_dollars():
    # A rebate lands months after the sale. $0.00 would state a measurement nobody made.
    c = _cases(_pair_with_a_handset(), is_box=_is_box, rebate_of=lambda _m: None)[0]
    assert c["chargeback_amount"] is None
    assert c["chargeback_measured"] is False


def test_no_rebate_lookup_injected_at_all_is_also_unmeasured():
    c = _cases(_pair_with_a_handset(), is_box=_is_box)[0]
    assert c["chargeback_amount"] is None and c["chargeback_measured"] is False


def test_the_summary_totals_the_chargeback_it_measured_and_counts_what_it_could_not():
    rows = _pair_with_a_handset()
    more = [line("T-3", "BYOD", mdn="2155550199"),
            line("T-4", "Upgrade", mdn="2155550199", department="Android - XP", ext_price=0.99,
                 product_desc="IPHONE 16E", serial_1="352000000000001")]
    found = _cases(rows + more, is_box=_is_box, rebate_of={"351449743101051": 249.0}.get)
    s = M.summary(M.number_days(rows + more, LC.HOUSE_RULES), found, is_box=_is_box)
    assert len(found) == 2
    assert s["chargeback_dollars"] == 249.0           # Σ of what WAS measured
    assert s["chargeback_measured_cases"] == 1
    assert s["chargeback_unmeasured_cases"] == 1      # stated, not folded into the total as zero


def test_two_handsets_on_the_upgrade_side_are_both_counted_never_silently_dropped():
    # MEASURED 2026-10-28: all 95 live cases carry exactly ONE handset on the upgrade side, so this
    # path is unexercised in production today. It exists because naming only the first handset would
    # UNDERSTATE the exposure the day a second one appears, and an understated money figure that
    # looks complete is worse than one that says it is partial.
    rows = _pair_with_a_handset() + [
        line("T-2", "Upgrade", department="Android - XP", ext_price=0.99,
             product_desc="IPHONE 16E", serial_1="352700328829816")]
    c = _cases(rows, is_box=_is_box,
               rebate_of={"351449743101051": 249.0, "352700328829816": 450.0}.get)[0]
    assert c["meids"] == ["351449743101051", "352700328829816"]
    assert c["phones_sold"] == 2
    assert c["chargeback_amount"] == 699.0          # BOTH, not the first one found


def test_one_handset_measured_and_one_not_still_states_a_partial_amount_as_partial():
    rows = _pair_with_a_handset() + [
        line("T-2", "Upgrade", department="Android - XP", ext_price=0.99,
             product_desc="IPHONE 16E", serial_1="352700328829816")]
    c = _cases(rows, is_box=_is_box, rebate_of={"351449743101051": 249.0}.get)[0]
    assert c["chargeback_amount"] == 249.0
    assert c["chargeback_measured"] is True
    # The figure is real but covers one of two handsets, and the screen has to be able to say so.
    assert c["chargeback_phones_measured"] == 1 and c["phones_sold"] == 2
    assert c["chargeback_partial"] is True


def test_a_single_handset_fully_measured_is_not_partial():
    c = _cases(_pair_with_a_handset(), is_box=_is_box,
               rebate_of={"351449743101051": 249.0}.get)[0]
    assert c["chargeback_partial"] is False
