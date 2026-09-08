"""Offline proof harness for DEPOSIT ACCOUNTABILITY + POS-BESIDE-DECLARED (owner directive
2026-09-02, mig 943). Pure stdlib, no DB/network — drives the REAL pure functions in
app/modules/closing/deposit_accountability.py and the REAL gate truth table
(billpay_pickup.resolve_recon_access → pay_visibility), never reimplementations.

WHAT IS PROVEN
  A. pos_next_to (owner: "those numbers should be right next to these numbers"): feed absent ⇒
     every key `no_pos_data` (pos None, delta None — never a fake zero or fake mismatch); feed
     present ⇒ ok/mismatch at the $1 tolerance; missing store-day = honest zero ONLY under
     zero_missing (the processor-feed rule) and `no_pos_data` otherwise (the X-report rule).
  B. envelope_state — the per-envelope truth table: unpicked / undisposed / missing_slip /
     deposited / handed_unconfirmed / handed_confirmed.
  C. THE GREEN RULE (owner: "making the color green for the days the cash has been accounted
     for whether deposit or handed over"), exactly as implemented: a store-day is green ⇔ ≥1
     picked-up envelope AND every picked-up envelope is accounted (deposited WITH slip, or
     handed AND mgmt-confirmed). Slip posture = flag-not-block: a slip-less deposit is
     missing_slip and the day can never be green; an amount-mismatch (deposit_flagged) does NOT
     block green but is surfaced.
  D. Day aggregation: handed checkbox state, mgmt_confirmed day state (all-handed-confirmed),
     latest confirm actor/timestamp, per-day totals, and the summary counts.
  E. pickup_deposit_line — the "separate line item under cash deposit recon": only 'deposited'
     dispositions, capture amount = deposit_amount (falling back to the pickup amount), slip /
     missing-slip / flagged counts.
  F. The MANAGEMENT-CONFIRMATION GATE truth table (same gate as the cash-recon screen —
     resolve_recon_access, the mig-434 'market manager and above' posture, fail-closed): scope
     'all' passes; market_manager passes; employee/store_manager/district_manager and
     unresolvable roles are gated out; a tenant allow-list overrides the default list.

Run: `cd backend && python3 harness_deposit_accountability.py`
"""
import sys

sys.path.insert(0, ".")

from app.modules.closing.deposit_accountability import (
    pos_next_to, envelope_state, day_accountability, pickup_deposit_line)
from app.modules.closing.billpay_pickup import resolve_recon_access

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


# ── A. pos_next_to ──────────────────────────────────────────────────────────────────────────────
print("\n== A. pos_next_to — the POS figure beside the declared figure ==")
decl = {("S1", "2026-09-01"): 500.0, ("S2", "2026-09-01"): 300.0, ("S1", "2026-09-02"): 250.0}
pos = {("S1", "2026-09-01"): 500.5, ("S2", "2026-09-01"): 250.0}

r = pos_next_to(decl, pos, feed_present=False)
check("A1 feed absent -> every key no_pos_data, pos None",
      all(c["status"] == "no_pos_data" and c["pos"] is None and c["delta"] is None
          for c in r.values()))

r = pos_next_to(decl, pos, feed_present=True, tolerance=1.0, zero_missing=False)
check("A2 within $1 tolerance -> ok", r[("S1", "2026-09-01")]["status"] == "ok"
      and r[("S1", "2026-09-01")]["delta"] == -0.5)
check("A3 outside tolerance -> mismatch, delta = declared - pos",
      r[("S2", "2026-09-01")]["status"] == "mismatch" and r[("S2", "2026-09-01")]["delta"] == 50.0)
check("A4 X-report rule: missing store-day (feed present, zero_missing=False) -> no_pos_data",
      r[("S1", "2026-09-02")]["status"] == "no_pos_data" and r[("S1", "2026-09-02")]["pos"] is None)

r = pos_next_to(decl, pos, feed_present=True, tolerance=1.0, zero_missing=True)
check("A5 processor rule: missing store-day (zero_missing=True) -> honest zero, compared",
      r[("S1", "2026-09-02")]["pos"] == 0.0 and r[("S1", "2026-09-02")]["status"] == "mismatch"
      and r[("S1", "2026-09-02")]["delta"] == 250.0)
check("A6 declared echoed per cell (the store-day total the delta uses)",
      r[("S1", "2026-09-01")]["declared"] == 500.0)
r = pos_next_to({("S1", "2026-09-01"): 0.0}, {("S1", "2026-09-01"): 0.0}, feed_present=True)
check("A7 zero declared vs zero POS -> ok (no phantom mismatch)",
      r[("S1", "2026-09-01")]["status"] == "ok")

# ── B. envelope_state truth table ───────────────────────────────────────────────────────────────
print("\n== B. envelope_state ==")
check("B1 not picked up -> unpicked", envelope_state({"picked_up": False}) == "unpicked")
check("B2 picked, no disposition -> undisposed", envelope_state({"picked_up": True}) == "undisposed")
check("B3 deposited WITHOUT slip -> missing_slip ('every cash deposit should be accompanied "
      "by the bank deposit slip')",
      envelope_state({"picked_up": True, "disposition": "deposited"}) == "missing_slip")
check("B4 deposited WITH slip -> deposited (accounted)",
      envelope_state({"picked_up": True, "disposition": "deposited",
                      "deposit_slip_path": "closing/slip1.jpg"}) == "deposited")
check("B5 handed, not confirmed -> handed_unconfirmed",
      envelope_state({"picked_up": True, "disposition": "handed_to_mgmt"}) == "handed_unconfirmed")
check("B6 handed + mgmt_confirmed -> handed_confirmed (accounted)",
      envelope_state({"picked_up": True, "disposition": "handed_to_mgmt",
                      "mgmt_confirmed": True}) == "handed_confirmed")
check("B7 blank-string slip path is NOT a slip",
      envelope_state({"picked_up": True, "disposition": "deposited",
                      "deposit_slip_path": "  "}) == "missing_slip")

# ── C. THE GREEN RULE ───────────────────────────────────────────────────────────────────────────
print("\n== C. the green rule, exactly ==")


def day(rows):
    out, _ = day_accountability(rows)
    assert len(out) == 1, f"expected one store-day, got {len(out)}"
    return out[0]


base = {"store_code": "S1", "close_date": "2026-09-01", "picked_up": True, "amount": 100}
check("C1 deposited WITH slip -> GREEN",
      day([{**base, "disposition": "deposited", "deposit_slip_path": "p.jpg"}])["green"])
check("C2 deposited WITHOUT slip -> NOT green (missing_slip day)",
      not day([{**base, "disposition": "deposited"}])["green"])
check("C3 handed + confirmed -> GREEN",
      day([{**base, "disposition": "handed_to_mgmt", "mgmt_confirmed": True}])["green"])
check("C4 handed, awaiting confirmation -> NOT green",
      not day([{**base, "disposition": "handed_to_mgmt"}])["green"])
check("C5 picked up, no disposition -> NOT green",
      not day([{**base}])["green"])
check("C6 no picked-up envelopes at all -> NOT green (nothing accounted, nothing to account)",
      not day([{**base, "picked_up": False}])["green"])
mixed = day([{**base, "disposition": "deposited", "deposit_slip_path": "p.jpg"},
             {**base, "employee_name": "B", "disposition": "handed_to_mgmt"}])
check("C7 mixed day: one accounted + one awaiting -> NOT green (EVERY envelope must be accounted)",
      not mixed["green"] and mixed["unconfirmed_rows"] == 1)
both = day([{**base, "disposition": "deposited", "deposit_slip_path": "p.jpg"},
            {**base, "employee_name": "B", "disposition": "handed_to_mgmt",
             "mgmt_confirmed": True, "kind": "billpay"}])
check("C8 mixed day fully accounted (deposit + confirmed hand-over) -> GREEN", both["green"])
flagged = day([{**base, "disposition": "deposited", "deposit_slip_path": "p.jpg",
                "deposit_flagged": True}])
check("C9 amount-mismatch flag does NOT block green (its own review flow) but IS surfaced",
      flagged["green"] and flagged["flagged_rows"] == 1)
check("C10 pre-943 degradation: mgmt_confirmed column absent -> handed day never green (fail-closed)",
      not day([{**base, "disposition": "handed_to_mgmt", "handed_to": "MM"}])["green"])

# ── D. day aggregation + summary ────────────────────────────────────────────────────────────────
print("\n== D. day aggregation ==")
d = day([{**base, "disposition": "handed_to_mgmt", "mgmt_confirmed": True,
          "mgmt_confirmed_by": "Aisha", "mgmt_confirmed_at": "2026-09-02T10:00:00Z"},
         {**base, "employee_name": "B", "amount": 50, "disposition": "handed_to_mgmt",
          "mgmt_confirmed": True, "mgmt_confirmed_by": "Omar",
          "mgmt_confirmed_at": "2026-09-02T11:00:00Z"}])
check("D1 handed checkbox = day has handed envelopes", d["handed"] is True)
check("D2 day mgmt_confirmed = ALL handed rows confirmed", d["mgmt_confirmed"] is True)
check("D3 latest confirmation actor/timestamp surfaces",
      d["mgmt_confirmed_by"] == "Omar" and d["mgmt_confirmed_at"] == "2026-09-02T11:00:00Z")
check("D4 handed_total sums handed amounts", d["handed_total"] == 150.0)
d2 = day([{**base, "disposition": "handed_to_mgmt", "mgmt_confirmed": True},
          {**base, "employee_name": "B", "disposition": "handed_to_mgmt"}])
check("D5 one confirmed + one not -> day NOT mgmt_confirmed, still handed",
      d2["handed"] and not d2["mgmt_confirmed"] and d2["confirmed_rows"] == 1
      and d2["unconfirmed_rows"] == 1)

rows, summary = day_accountability([
    {"store_code": "S1", "close_date": "2026-09-01", "picked_up": True, "amount": 100,
     "disposition": "deposited", "deposit_slip_path": "p.jpg"},
    {"store_code": "S1", "close_date": "2026-09-02", "picked_up": True, "amount": 80,
     "disposition": "deposited"},
    {"store_code": "S2", "close_date": "2026-09-01", "picked_up": True, "amount": 60,
     "disposition": "handed_to_mgmt"},
    {"store_code": "S2", "close_date": "2026-09-02", "picked_up": True, "amount": 40},
])
check("D6 one row per (store, day), sorted by day then store",
      [(r["store_code"], r["day"]) for r in rows] ==
      [("S1", "2026-09-01"), ("S2", "2026-09-01"), ("S1", "2026-09-02"), ("S2", "2026-09-02")])
check("D7 summary counts: 1 green / 1 missing-slip / 1 awaiting-confirm / 1 undisposed day",
      summary["green_days"] == 1 and summary["missing_slip_days"] == 1
      and summary["awaiting_confirm_days"] == 1 and summary["undisposed_days"] == 1)
check("D8 summary money totals", summary["picked_total"] == 280.0
      and summary["deposited_total"] == 180.0 and summary["handed_total"] == 60.0)
check("D9 rows with no close_date are dropped, blank store bucketed '?'",
      day_accountability([{"store_code": "S1", "picked_up": True}])[0] == [] and
      day_accountability([{"close_date": "2026-09-01", "picked_up": True, "amount": 1}])[0][0]["store_code"] == "?")

# ── E. pickup_deposit_line — the separate line item under cash deposit recon ────────────────────
print("\n== E. pickup_deposit_line ==")
caps = pickup_deposit_line([
    {"store_code": "S1", "close_date": "2026-09-01", "picked_up": True, "amount": 100,
     "disposition": "deposited", "deposit_amount": 98.0, "deposit_slip_path": "p.jpg",
     "deposit_flagged": True, "deposited_at": "2026-09-01T22:00:00Z"},
    {"store_code": "S1", "close_date": "2026-09-01", "employee_name": "B", "picked_up": True,
     "amount": 55, "disposition": "deposited", "kind": "billpay"},
    {"store_code": "S1", "close_date": "2026-09-01", "employee_name": "C", "picked_up": True,
     "amount": 70, "disposition": "handed_to_mgmt"},
    {"store_code": "S1", "close_date": "2026-09-01", "employee_name": "D", "picked_up": True,
     "amount": 30},
])
c = caps[("S1", "2026-09-01")]
check("E1 only 'deposited' dispositions become capture lines (handed/undisposed excluded)",
      c["rows"] == 2 and len(caps) == 1)
check("E2 capture amount = deposit_amount, falling back to the pickup amount",
      c["amount"] == 153.0)
check("E3 slip / missing-slip / flagged counts",
      c["slips"] == 1 and c["missing_slip"] == 1 and c["flagged"] == 1)
check("E4 kinds preserved on the capture detail",
      sorted(d0["kind"] for d0 in c["deposits"]) == ["billpay", "cash"])

# ── F. the management-confirmation gate (same table as the cash-recon screen) ───────────────────
print("\n== F. confirmation gate — market manager and above, fail-closed ==")
check("F1 scope 'all' passes regardless of role", resolve_recon_access("employee", "all"))
check("F2 market_manager (narrow scope) passes", resolve_recon_access("market_manager", "market"))
check("F3 admin passes", resolve_recon_access("admin", "market"))
check("F4 employee gated out", not resolve_recon_access("employee", "store"))
check("F5 store_manager gated out", not resolve_recon_access("store_manager", "store"))
check("F6 district_manager gated out (owner: 'dm is gated out of it')",
      not resolve_recon_access("district_manager", "market"))
check("F7 unresolvable role + narrow scope -> gated (fail-closed)",
      not resolve_recon_access(None, "store"))
check("F8 tenant allow-list overrides the default (RULE TWO — config, never code)",
      resolve_recon_access("district_manager", "market", visible_roles=["district_manager"])
      and not resolve_recon_access("market_manager", "market", visible_roles=["district_manager"]))

# ── G. actual-vs-declared visibility on the day view (owner 2026-09-04; mig 949) ────────────────
print("\n== G. actual cash picked from envelope — board visibility ==")
gshort = day([{**base, "disposition": "deposited", "deposit_slip_path": "p.jpg",
               "actual_picked_amount": 80}])
check("G1 short pickup surfaces on the day: per-envelope actual/variance/status + day chips",
      gshort["envelopes"][0]["actual_picked_amount"] == 80.0
      and gshort["envelopes"][0]["pickup_variance"] == -20.0
      and gshort["envelopes"][0]["pickup_variance_status"] == "short"
      and gshort["pickup_short_rows"] == 1 and gshort["pickup_variance_total"] == -20.0)
check("G2 variance is DISPLAY + FLAG only: a short pickup never blocks green, and picked_total "
      "stays the declared movement figure (the money posture lives in _cash_position_core's knob)",
      gshort["green"] and gshort["picked_total"] == 100.0)
gnone = day([{**base, "disposition": "deposited", "deposit_slip_path": "p.jpg"}])
check("G3 no actual recorded -> honest None everywhere, zero variance chips (never a fake 100% short)",
      gnone["envelopes"][0]["actual_picked_amount"] is None
      and gnone["envelopes"][0]["pickup_variance_status"] is None
      and gnone["pickup_short_rows"] == 0 and gnone["pickup_variance_total"] == 0.0)
gover = day([{**base, "actual_picked_amount": 105},
             {**base, "employee_name": "B", "amount": 50, "actual_picked_amount": 40}])
check("G4 mixed day: one over + one short aggregate independently (variance_total = +5 - 10 = -5)",
      gover["pickup_over_rows"] == 1 and gover["pickup_short_rows"] == 1
      and gover["pickup_variance_total"] == -5.0)
_grows, _gsum = day_accountability([
    {"store_code": "S1", "close_date": "2026-09-01", "picked_up": True, "amount": 100,
     "actual_picked_amount": 90},
    {"store_code": "S2", "close_date": "2026-09-01", "picked_up": True, "amount": 100,
     "actual_picked_amount": 100},
])
check("G5 summary.short_pickup_days counts only days with a short pickup (match day excluded)",
      _gsum["short_pickup_days"] == 1)
check("G6 unpicked envelope's actual (if any) never counted — cash still in the store",
      day([{**base, "picked_up": False, "actual_picked_amount": 10},
           {**base, "employee_name": "B", "disposition": "deposited",
            "deposit_slip_path": "p.jpg"}])["pickup_variance_total"] == 0.0)


# ═══ H. CASH SHORT BY DM (owner directive 2026-09-08) ═══════════════════════════════════════════
# Owner, verbatim: "if the cash is short then it should generate a cash short report by DM."
#
# The report is ONE MORE FOLD over the day rows this board already produces — same rows, grouped on
# cash_pickup.picked_up_by instead of (store, day) — so it cannot disagree with the board and there
# is no second query to drift. Short/over is NOT re-derived here: it arrives on each envelope from
# pickup_actual.row_variance (envelope_report.count_fields), the same truth table §G proves above.
from app.modules.closing.deposit_accountability import dm_shortage_rows   # noqa: E402

_h_rows, _h_sum = day_accountability([
    # DM Alpha: one SHORT (200 declared, 180 counted), one exact match, one collected SEALED
    {"store_code": "S1", "close_date": "2026-09-08", "picked_up": True, "picked_up_by": "DM Alpha",
     "employee_name": "Jane", "amount": 200, "actual_picked_amount": 180, "envelope_opened": True,
     "disposition": "deposited", "deposit_slip_path": "s.jpg"},
    {"store_code": "S1", "close_date": "2026-09-08", "picked_up": True, "picked_up_by": "DM Alpha",
     "employee_name": "Mo", "amount": 100, "actual_picked_amount": 100,
     "disposition": "deposited", "deposit_slip_path": "s.jpg"},
    {"store_code": "S2", "close_date": "2026-09-08", "picked_up": True, "picked_up_by": "DM Alpha",
     "employee_name": "Sam", "amount": 50},
    # DM Beta: one OVER, and one envelope still sitting in the store (never picked up)
    {"store_code": "S3", "close_date": "2026-09-08", "picked_up": True, "picked_up_by": "DM Beta",
     "employee_name": "Ali", "amount": 100, "actual_picked_amount": 115},
    {"store_code": "S3", "close_date": "2026-09-08", "picked_up": False, "picked_up_by": None,
     "employee_name": "Nia", "amount": 900, "actual_picked_amount": 10},
])
_hd_rows, _hd_sum = dm_shortage_rows(_h_rows)
_h = {r["dm"]: r for r in _hd_rows}

check("H1 grouped by the DM WHO PICKED UP (cash_pickup.picked_up_by), not by store or rep",
      sorted(_h) == ["DM Alpha", "DM Beta"], str(sorted(_h)))
check("H2 the SHORT is attributed to the DM who took it: 180 counted vs 200 declared = -20",
      _h["DM Alpha"]["short_rows"] == 1 and _h["DM Alpha"]["short_amount"] == -20.0
      and _h["DM Alpha"]["is_short"] is True, str(_h["DM Alpha"]))
check("H3 UNCOUNTED IS NOT SHORT — the sealed envelope counts as uncounted and lands in NEITHER "
      "bucket (a store-day nobody counted is not a shortage)",
      _h["DM Alpha"]["uncounted_rows"] == 1 and _h["DM Alpha"]["counted_rows"] == 2
      and _h["DM Alpha"]["short_rows"] == 1 and _h["DM Alpha"]["over_rows"] == 0,
      str(_h["DM Alpha"]))
check("H4 an exact count is a MATCH, not a shortage", _h["DM Alpha"]["match_rows"] == 1)
check("H5 OVER is reported separately and never nets away a shortage — Beta is +15 over, 0 short, "
      "and is NOT flagged as short",
      _h["DM Beta"]["over_rows"] == 1 and _h["DM Beta"]["over_amount"] == 15.0
      and _h["DM Beta"]["short_rows"] == 0 and _h["DM Beta"]["is_short"] is False,
      str(_h["DM Beta"]))
check("H6 an UNPICKED envelope is excluded entirely — nobody has picked it up to be short of it, "
      "and its stray actual (900 vs 10) must not become a $890 phantom shortage",
      _h["DM Beta"]["envelopes"] == 1 and _h["DM Beta"]["counted_rows"] == 1,
      str(_h["DM Beta"]))
check("H7 the report NAMES THE CASH, not just the DM: each shortage carries its day, store, rep, "
      "declared and counted figures",
      len(_h["DM Alpha"]["shorts"]) == 1
      and _h["DM Alpha"]["shorts"][0]["declared"] == 200.0
      and _h["DM Alpha"]["shorts"][0]["actual"] == 180.0
      and _h["DM Alpha"]["shorts"][0]["variance"] == -20.0
      and _h["DM Alpha"]["shorts"][0]["employee_name"] == "Jane"
      and _h["DM Alpha"]["shorts"][0]["day"] == "2026-09-08", str(_h["DM Alpha"]["shorts"]))
check("H8 the mig-990 opened flag rides along, so the report distinguishes a shortage found in an "
      "envelope the DM OPENED AND COUNTED from one inferred any other way",
      _h["DM Alpha"]["shorts"][0]["envelope_opened"] is True
      and _h["DM Alpha"]["opened_rows"] == 1)
check("H9 declared/counted totals are per DM and only count what was actually counted",
      _h["DM Alpha"]["declared_total"] == 350.0 and _h["DM Alpha"]["actual_total"] == 280.0,
      str(_h["DM Alpha"]))
check("H10 worst shortage FIRST — the DM the report exists to surface is at the top",
      [r["dm"] for r in _hd_rows][0] == "DM Alpha", str([r["dm"] for r in _hd_rows]))
check("H11 the summary totals the shortage without letting the over cancel it",
      _hd_sum["dms"] == 2 and _hd_sum["dms_short"] == 1 and _hd_sum["short_rows"] == 1
      and _hd_sum["short_amount"] == -20.0 and _hd_sum["over_amount"] == 15.0
      and _hd_sum["uncounted_rows"] == 1, str(_hd_sum))
check("H12 net_variance is reported SEPARATELY from short_amount, so a DM who is $20 short on one "
      "envelope and $15 over on another is never shown as merely '$5 short'",
      _hd_sum["net_variance"] == -5.0 and _hd_sum["short_amount"] == -20.0, str(_hd_sum))

# ── no shortages, and the empty case ─────────────────────────────────────────────────────────────
_c_rows, _c_sum = day_accountability([
    {"store_code": "S1", "close_date": "2026-09-08", "picked_up": True, "picked_up_by": "DM Clean",
     "employee_name": "Jane", "amount": 100, "actual_picked_amount": 100},
])
_cr, _cs = dm_shortage_rows(_c_rows)
check("H13 a DM whose counts all tie out is reported with zero short and is_short False — the "
      "report exists whether or not anyone is short, so 'no shortages' is a stated result",
      len(_cr) == 1 and _cr[0]["is_short"] is False and _cs["dms_short"] == 0
      and _cs["short_amount"] == 0.0, str(_cr))
_s_rows, _s_sum = day_accountability([
    {"store_code": "S1", "close_date": "2026-09-08", "picked_up": True, "picked_up_by": "DM Sealed",
     "employee_name": "Jane", "amount": 100},
])
_sr, _ss = dm_shortage_rows(_s_rows)
check("H14 a DM whose whole round was collected SEALED shows 0 short and every envelope "
      "uncounted — an honest description of a sealed round, never a clean bill of health",
      _sr[0]["short_rows"] == 0 and _sr[0]["counted_rows"] == 0
      and _sr[0]["uncounted_rows"] == 1 and _ss["uncounted_rows"] == 1, str(_sr))
check("H15 no rows in, nothing out (no fabricated DM, no divide-by-zero)",
      dm_shortage_rows([]) == ([], dm_shortage_rows([])[1]) and dm_shortage_rows([])[0] == []
      and dm_shortage_rows(None)[0] == [])

# ── attribution and keyset safety ────────────────────────────────────────────────────────────────
_u_rows, _ = day_accountability([
    {"store_code": "S1", "close_date": "2026-09-08", "picked_up": True, "picked_up_by": "",
     "employee_name": "Jane", "amount": 100, "actual_picked_amount": 80},
])
_ur, _us = dm_shortage_rows(_u_rows)
check("H16 a pickup with no recorded DM is reported under '(unattributed)' — a shortage nobody is "
      "named for is still a shortage, and must not be silently dropped",
      _ur[0]["dm"] == "(unattributed)" and _ur[0]["short_amount"] == -20.0, str(_ur))
check("H17 IT FOLDS THE DAY ROWS, not the raw pickup table — so the endpoint's keyset filter "
      "(applied to those rows) governs the report too, and a store outside the viewer's span can "
      "never leak in through a shortage line",
      dm_shortage_rows([r for r in _h_rows if r["store_code"] == "S1"])[1]["short_rows"] == 1
      and dm_shortage_rows([r for r in _h_rows if r["store_code"] == "S3"])[1]["short_rows"] == 0)
check("H18 the tolerance band is a parameter, not a second invented default: at $25 the $20 "
      "shortage falls inside the band and is a match, not a short",
      dm_shortage_rows(_h_rows, tolerance=25.0)[1]["short_rows"] == 0
      and dm_shortage_rows(_h_rows, tolerance=25.0)[1]["over_rows"] == 0
      and dm_shortage_rows(_h_rows)[1]["short_rows"] == 1)
check("H19 short/over is NOT re-derived — the fold reads the variance day_accountability already "
      "computed via pickup_actual.row_variance (envelope_report.count_fields)",
      "row_variance" not in open("app/modules/closing/deposit_accountability.py")
      .read().split("def dm_shortage_rows")[1].split("def pickup_deposit_line")[0])
check("H20 both pickup kinds are covered — a bill-pay envelope short is the DM's shortage too "
      "(mig 942: one machinery, one report)",
      dm_shortage_rows(day_accountability([
          {"store_code": "S1", "close_date": "2026-09-08", "picked_up": True, "kind": "billpay",
           "picked_up_by": "DM K", "employee_name": "J", "amount": 60,
           "actual_picked_amount": 45}])[0])[0][0]["shorts"][0]["kind"] == "billpay")


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
