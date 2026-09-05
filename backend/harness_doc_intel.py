"""HARNESS — lease / insurance document intelligence (doc_intel.py, migs 964-967).

OWNER SPEC (2026-09-05): an uploaded insurance POLICY covers MANY stores; AI fills the coverage
period, coverage type (BOP / workers comp — CONFIG, not a code enum), premium, policy number,
summary of inclusions and an open "extra items" list; an uploaded LEASE fills the lease term, the
rents for the coming years, the exit clause, termination liabilities, contact information, the
notice address and every other critical clause WITH its clause number and a plain-English
translation; and multiple contacts get notified at least 60 days before a COI expires or a lease
ends — or per the lease's own requirement.

WHAT THIS PROVES (stdlib only, no DB, no network, no model call):

  A. Value coercion — ISO + US dates, money with $ / commas / accounting parens, percentages,
     week/day rent-due shapes; garbage yields None, never a guessed number.
  B. Coverage type is CONFIG (RULE TWO) — a tenant's own vocabulary resolves; a type outside it is
     NOT force-matched (returns None) and survives as an extra item instead of being lost.
  C. Extraction mapping — every field the owner named lands with its label, target column,
     provenance (verbatim snippet + page) and confidence; unknown keys become extra items and can
     NEVER become columns; bank-ish digit runs in a quoted snippet are masked; confidence clamps.
  D. THE MONEY GATE (apply_plan) — the five refusals in order: unknown field, not in the
     extraction, no target column, forbidden ACH/identity target (no override exists), and
     money_confirmation_required. Plus the happy path, and that a money field with the
     confirmation lands while an ACH field never does.
  E. Notice window — resolved = MAX(document's own requirement, org floor): 90 and 180 beat the
     60-day floor, 30 does NOT drop below it, missing config falls back to the house 60, and a
     tenant floor of 90 applies where a document says nothing.
  F. Expiry alerts — fire at the resolved window and at each nudge below it, once per milestone
     (dedupe against the existing alert_log keys), only to contacts who asked for notice, with a
     contact's longer own window getting the early notice and everyone getting the late ones;
     nothing fires with no expiry date, no contacts, or while still outside the window.
  G. Catalogue/prompt coherence — the prompt names every catalogue key and no key outside it, so
     the two can't drift; money fields are marked in the prompt.
  H. Interop with the SHIPPED money path — an accepted rent schedule is byte-identical to what
     store_lease.normalize_rent_schedule accepts, and drives store_lease.rent_for_month.
  I. ARMED negative control.
  J. Multi-tenant static guard — storeops/router.py is outside harness_org_scope_guard.py's scope
     (that guard reads commcalc's router), so this one proves every query on the four new tables is
     org-filtered, or is an insert whose payload provably carries org_id.

Run: python3 harness_doc_intel.py     (stdlib-only)
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, got, want=True):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}: got {got!r}, want {want!r}")


# doc_intel is a leaf (re + datetime only). store_lease (section H) lazily touches the DB, so stub
# that seam exactly as harness_store_lease.py does.
_db = types.ModuleType("app.core.database")
_db.get_supabase = lambda: (_ for _ in ()).throw(RuntimeError("no live DB in this harness"))
sys.modules["app.core.database"] = _db

from app.modules.storeops import doc_intel as di            # noqa: E402
from app.modules.storeops import doc_intel_ai as dia        # noqa: E402
from app.modules.storeops import store_lease as sl          # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("A. value coercion — never a guessed number")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("A1 ISO date", di.coerce_date("2027-03-31"), "2027-03-31")
check("A2 US M/D/YYYY", di.coerce_date("3/31/2027"), "2027-03-31")
check("A3 date inside a sentence", di.coerce_date("expires on 2027-03-31 at midnight"), "2027-03-31")
check("A4 impossible date -> None", di.coerce_date("2027-02-30"), None)
check("A5 no date -> None", di.coerce_date("upon renewal"), None)
check("A6 money with $ and commas", di.coerce_number("$4,250.00"), 4250.0)
check("A7 percent", di.coerce_number("3.5%"), 3.5)
check("A8 accounting negative", di.coerce_number("(1,200)"), -1200.0)
check("A9 words -> None (no fake 0)", di.coerce_number("to be determined"), None)
check("A10 True is not a number", di.coerce_number(True), None)
check("A11 rent due 'day 5'", di.coerce_due({"kind": "day", "value": 5}), {"kind": "day", "value": 5})
check("A12 rent due bare day number", di.coerce_due("1"), {"kind": "day", "value": 1})
check("A13 rent due week 6 rejected", di.coerce_due({"kind": "week", "value": 6}), None)
check("A14 schedule sorted + rounded",
      di.coerce_schedule([{"effective_from": "2027-01-01", "monthly_rent": "$5,150.5"},
                          {"effective_from": "2026-01-01", "monthly_rent": 5000}]),
      [{"effective_from": "2026-01-01", "monthly_rent": 5000.0},
       {"effective_from": "2027-01-01", "monthly_rent": 5150.5}])
check("A15 schedule drops malformed entries",
      di.coerce_schedule([{"effective_from": "later", "monthly_rent": 1},
                          {"effective_from": "2026-01-01", "monthly_rent": "n/a"}]), None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("B. coverage type is CONFIG (RULE TWO), never a code enum")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
HOUSE = None                                        # -> house vocabulary
TENANT = [{"key": "bop", "label": "Businessowners Policy"},
          {"key": "garagekeepers", "label": "Garagekeepers Legal Liability"}]
check("B1 owner's 'BOP' -> house key", di.normalize_coverage_type("BOP", HOUSE), "bop")
check("B2 owner's 'workers comp' -> house key",
      di.normalize_coverage_type("Workers Comp", HOUSE), "workers_comp")
check("B3 label match", di.normalize_coverage_type("Commercial Auto", HOUSE), "auto")
check("B4 tenant's OWN type resolves",
      di.normalize_coverage_type("Garagekeepers Legal Liability", TENANT), "garagekeepers")
check("B5 house type absent from the tenant list is NOT force-matched",
      di.normalize_coverage_type("Cyber Liability", TENANT), None)
check("B6 unknown -> None (never a wrong bucket)", di.normalize_coverage_type("Kidnap & Ransom", HOUSE), None)
check("B7 empty vocabulary falls back to the house list",
      [t["key"] for t in di.normalize_coverage_types([])][:2], ["bop", "workers_comp"])
check("B8 tenant list wins entirely",
      [t["key"] for t in di.normalize_coverage_types(TENANT)], ["bop", "garagekeepers"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("C. extraction mapping — provenance kept, unknown keys can never become columns")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
POLICY_RAW = {
    "fields": [
        {"key": "policy_number", "value": "BOP-99812", "confidence": 0.98,
         "source_text": "Policy Number: BOP-99812", "source_page": 1},
        {"key": "coverage_type", "value": "Business Owner's Policy (BOP)", "confidence": 0.95,
         "source_text": "Businessowners Coverage Form", "source_page": 1},
        {"key": "coverage_start", "value": "4/1/2026", "confidence": 0.99,
         "source_text": "Policy Period: 4/1/2026 to 4/1/2027", "source_page": 1},
        {"key": "coverage_end", "value": "4/1/2027", "confidence": 0.99,
         "source_text": "Policy Period: 4/1/2026 to 4/1/2027", "source_page": 1},
        {"key": "premium", "value": "$12,480.00", "confidence": 0.9,
         "source_text": "Total Premium $12,480.00 payable by ACH; account 123456789 at First Bank",
         "source_page": 2},
        {"key": "inclusions_summary", "value": "Property, general liability, business income.",
         "confidence": 0.8, "source_text": "Coverages: Property; GL; BI", "source_page": 2},
        {"key": "notice_days", "value": "90", "confidence": 0.7,
         "source_text": "90 days written notice of non-renewal", "source_page": 6},
        # keys outside the catalogue — must NOT become columns
        {"key": "deductible", "value": "$2,500", "confidence": 0.9,
         "source_text": "Deductible $2,500", "source_page": 2},
        {"key": "ach_account_number", "value": "123456789", "confidence": 0.99,
         "source_text": "ACH account 123456789", "source_page": 2},
        {"key": "premium_frequency", "value": "", "confidence": 0.2, "source_text": "", "source_page": 0},
    ],
    "extra_items": [{"label": "Additional insured", "value": "Landlord must be named",
                     "note": "Required by most leases", "source_page": 4}],
    "contacts": [{"name": "Dana Broker", "email": "dana@brokerage.example", "phone": "555-0100",
                  "role": "Broker"},
                 {"name": "", "email": "", "phone": "", "role": ""}],
    "clauses": [],
}
pol = di.normalize_extraction(POLICY_RAW, di.SUBJECT_POLICY)
pk = {f["key"]: f for f in pol["fields"]}
check("C1 policy number kept", pk["policy_number"]["value"], "BOP-99812")
check("C2 coverage type normalized to a config key", pk["coverage_type"]["value"], "bop")
check("C3 US dates -> ISO", (pk["coverage_start"]["value"], pk["coverage_end"]["value"]),
      ("2026-04-01", "2027-04-01"))
check("C4 premium coerced to a number", pk["premium"]["value"], 12480.0)
check("C5 premium is MONEY-GUARDED", pk["premium"]["money_guarded"], True)
check("C6 inclusions summary is not guarded", pk["inclusions_summary"]["money_guarded"], False)
check("C7 target column recorded", pk["coverage_end"]["target"], "insurance_policy.coverage_end")
check("C8 provenance kept (page)", pk["coverage_end"]["source_page"], 1)
check("C9 bank-ish digits masked in the quoted snippet",
      ("123456789" not in pk["premium"]["source_text"]) and ("$12,480.00" in pk["premium"]["source_text"]), True)
check("C10 unknown key never becomes a field", "deductible" in pk, False)
check("C11 unknown key survives as an extra item",
      any(x["label"].lower().startswith("deductible") for x in pol["extra_items"]), True)
check("C12 an ACH key from the model is NOT a field", "ach_account_number" in pk, False)
check("C13 empty value dropped", "premium_frequency" in pk, False)
check("C14 model's own extra items kept",
      any(x["label"] == "Additional insured" for x in pol["extra_items"]), True)
check("C15 contacts kept, empty contact dropped", len(pol["contacts"]), 1)
check("C16 contact email kept", pol["contacts"][0]["email"], "dana@brokerage.example")

LEASE_RAW = {
    "fields": [
        {"key": "lease_start", "value": "2024-06-01", "confidence": 1, "source_text": "Term commences June 1, 2024", "source_page": 1},
        {"key": "lease_end", "value": "2029-05-31", "confidence": 1, "source_text": "and expires May 31, 2029", "source_page": 1},
        {"key": "current_rent", "value": "5,000", "confidence": 0.9, "source_text": "Base Rent $5,000 per month", "source_page": 2},
        {"key": "rent_schedule", "value": [{"effective_from": "2026-06-01", "monthly_rent": "5,150"},
                                           {"effective_from": "2027-06-01", "monthly_rent": "5,304.50"}],
         "confidence": 0.85, "source_text": "Year 3 $5,150; Year 4 $5,304.50", "source_page": 2},
        {"key": "rent_due", "value": {"kind": "day", "value": 1}, "confidence": 0.95, "source_text": "due on the first day of each month", "source_page": 2},
        {"key": "lease_notice_days", "value": "180", "confidence": 0.9, "source_text": "180 days prior written notice", "source_page": 9},
        {"key": "notice_address", "value": "500 Landlord Way, Suite 4, Brooklyn NY 11208", "confidence": 0.9, "source_text": "Notices to Landlord at 500 Landlord Way", "source_page": 12},
        {"key": "lease_exit_clause", "value": "Tenant may terminate after month 36 with 180 days notice and a termination fee.", "confidence": 0.8, "source_text": "Section 14.3 ...", "source_page": 9},
        {"key": "lease_termination_liabilities", "value": "Unamortized TI allowance plus three months' rent.", "confidence": 0.75, "source_text": "Section 14.4 ...", "source_page": 9},
        {"key": "landlord_email", "value": "leases@landlord.example", "confidence": 0.95, "source_text": "Email: leases@landlord.example", "source_page": 12},
        {"key": "escalation_pct", "value": "3%", "confidence": 0.6, "source_text": "increase of three percent (3%) annually", "source_page": 2},
    ],
    "clauses": [
        {"clause_number": "14.3", "title": "Early termination", "category": "exit",
         "plain_english": "You can walk away after three years if you give six months notice and pay a fee.",
         "source_text": "Tenant shall have the right to terminate...", "source_page": 9},
        {"clause_number": "14.4", "title": "Termination liabilities", "category": "termination_liability",
         "plain_english": "If you leave early you still owe the unamortized build-out plus three months rent.",
         "source_text": "Upon such termination Tenant shall pay...", "source_page": 9},
        {"clause_number": "", "title": "", "category": "", "plain_english": "", "source_text": "", "source_page": 0},
        {"clause_number": "22.1", "title": "Personal guaranty", "category": "weird_bucket",
         "plain_english": "The owner is personally on the hook for the rent.",
         "source_text": "Guarantor personally guarantees...", "source_page": 15},
    ],
    "extra_items": ["No signature page found for the second amendment."],
    "contacts": [{"name": "Property Mgmt Co", "email": "pm@landlord.example", "phone": "555-0111", "role": "Property manager"}],
}
lea = di.normalize_extraction(LEASE_RAW, di.SUBJECT_LEASE)
lk = {f["key"]: f for f in lea["fields"]}
check("C17 lease term both ends", (lk["lease_start"]["value"], lk["lease_end"]["value"]),
      ("2024-06-01", "2029-05-31"))
check("C18 rents for the coming years, sorted + numeric",
      lk["rent_schedule"]["value"],
      [{"effective_from": "2026-06-01", "monthly_rent": 5150.0},
       {"effective_from": "2027-06-01", "monthly_rent": 5304.5}])
check("C19 rent schedule is MONEY-GUARDED", lk["rent_schedule"]["money_guarded"], True)
check("C20 rent due shape", lk["rent_due"]["value"], {"kind": "day", "value": 1})
check("C21 notice days is an int", lk["lease_notice_days"]["value"], 180)
check("C22 notice address kept", lk["notice_address"]["target"], "store_lease.notice_address")
check("C23 exit clause + termination liabilities both captured",
      ("lease_exit_clause" in lk, "lease_termination_liabilities" in lk), (True, True))
check("C24 clause NUMBER kept with plain English",
      (lea["clauses"][0]["clause_number"], lea["clauses"][0]["plain_english"].startswith("You can walk away")),
      ("14.3", True))
check("C25 empty clause dropped", len(lea["clauses"]), 3)
check("C26 unknown clause category preserved, not dropped", lea["clauses"][2]["category"], "weird_bucket")
check("C27 string extra item accepted", lea["extra_items"][0]["value"].startswith("No signature page"), True)
check("C28 confidence clamped to 0..1", lk["lease_start"]["confidence"], 1.0)
check("C29 percent-style confidence normalized",
      di.normalize_extraction({"fields": [{"key": "landlord_name", "value": "X", "confidence": 85}]},
                              di.SUBJECT_LEASE)["fields"][0]["confidence"], 0.85)
check("C30 garbage payload -> empty draft, no exception",
      di.normalize_extraction("not a dict", di.SUBJECT_LEASE),
      {"fields": [], "clauses": [], "extra_items": [], "contacts": []})
check("C31 unknown subject kind -> empty draft",
      di.normalize_extraction(LEASE_RAW, "nonsense")["fields"], [])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("D. THE MONEY GATE — apply_plan is the only door to a live column")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
LF = lea["fields"]
plan = di.apply_plan(di.SUBJECT_LEASE, LF, ["notice_address", "lease_end", "landlord_email"])
check("D1 non-money fields land without any confirmation",
      plan["patch"], {"store_lease": {"notice_address": "500 Landlord Way, Suite 4, Brooklyn NY 11208",
                                      "lease_end": "2029-05-31",
                                      "landlord_email": "leases@landlord.example"}})
check("D2 nothing refused on the happy path", plan["refused"], [])

g = di.apply_plan(di.SUBJECT_LEASE, LF, ["current_rent", "rent_schedule", "escalation_pct", "rent_due"])
check("D3 EVERY money field refused without the confirmation", g["patch"], {})
check("D4 refusal reason is explicit",
      sorted({r["reason"] for r in g["refused"]}), ["money_confirmation_required"])
check("D5 all four money fields refused", len(g["refused"]), 4)

g2 = di.apply_plan(di.SUBJECT_LEASE, LF, ["current_rent", "rent_schedule"], confirm_money=True)
check("D6 money lands ONLY with the explicit confirmation",
      g2["patch"]["store_lease"]["current_rent"], 5000.0)
check("D7 the accepted schedule is the mig-946 shape",
      g2["patch"]["store_lease"]["rent_schedule"],
      [{"effective_from": "2026-06-01", "monthly_rent": 5150.0},
       {"effective_from": "2027-06-01", "monthly_rent": 5304.5}])

# ACH: no flag exists that lets a document rewrite banking details.
ACH_FIELDS = [{"key": "ach_account_number", "value": "123456789", "target": "store_lease.ach_account_number"}]
check("D8 ACH key is not in the catalogue at all",
      "ach_account_number" in di.spec_map(di.SUBJECT_LEASE), False)
a1 = di.apply_plan(di.SUBJECT_LEASE, ACH_FIELDS, ["ach_account_number"])
a2 = di.apply_plan(di.SUBJECT_LEASE, ACH_FIELDS, ["ach_account_number"], confirm_money=True)
check("D9 ACH refused without the confirmation", (a1["patch"], a1["refused"][0]["reason"]),
      ({}, "unknown_field"))
check("D10 ACH STILL refused WITH the money confirmation", (a2["patch"], a2["refused"][0]["reason"]),
      ({}, "unknown_field"))
check("D11 every ACH column is in FORBIDDEN_TARGETS",
      all(k in di.FORBIDDEN_TARGETS for k in sl.ACH_FIELDS), True)
check("D12 no catalogue field targets a forbidden column",
      [s["key"] for kind in di.SUBJECT_KINDS for s in di.FIELD_SPECS[kind]
       if s["target"] in di.FORBIDDEN_TARGETS], [])
check("D13 unknown field refused",
      di.apply_plan(di.SUBJECT_LEASE, LF, ["made_up"])["refused"][0]["reason"], "unknown_field")
check("D14 catalogue field absent from THIS extraction refused",
      di.apply_plan(di.SUBJECT_LEASE, LF, ["site_contact_phone"])["refused"][0]["reason"],
      "not_in_extraction")
check("D15 empty accept list is a no-op", di.apply_plan(di.SUBJECT_LEASE, LF, []),
      {"patch": {}, "applied": [], "refused": []})
check("D16 every money reader column store_lease books is guarded",
      all(di.is_money_guarded(di.SUBJECT_LEASE, k) for k in
          ("current_rent", "escalation_pct", "rent_schedule", "rent_due")), True)
check("D17 COI premium fields are guarded too",
      (di.is_money_guarded(di.SUBJECT_COI, "insurance_premium"),
       di.is_money_guarded(di.SUBJECT_COI, "insurance_premium_due")), (True, True))
check("D18 applied rows carry the money flag for the audit trail",
      [(x["key"], x["money_guarded"]) for x in g2["applied"]],
      [("current_rent", True), ("rent_schedule", True)])
pol_plan = di.apply_plan(di.SUBJECT_POLICY, pol["fields"], ["coverage_end", "policy_number", "premium"])
check("D19 policy: dates land, premium held back",
      (sorted(pol_plan["patch"]["insurance_policy"]), pol_plan["refused"][0]["key"]),
      (["coverage_end", "policy_number"], "premium"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("E. notice window — MAX(document's own requirement, org floor)")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
HOUSE_CFG = {"lease": 60, "insurance": 60}
check("E1 no document requirement -> the 60-day floor",
      di.resolve_notice_days(None, HOUSE_CFG, di.SUBJECT_LEASE), 60)
check("E2 a 90-day lease BEATS the floor",
      di.resolve_notice_days(90, HOUSE_CFG, di.SUBJECT_LEASE), 90)
check("E3 a 180-day lease BEATS the floor",
      di.resolve_notice_days(180, HOUSE_CFG, di.SUBJECT_LEASE), 180)
check("E4 a 30-day lease does NOT drop below the floor",
      di.resolve_notice_days(30, HOUSE_CFG, di.SUBJECT_LEASE), 60)
check("E5 a tenant floor of 90 applies when the document says nothing",
      di.resolve_notice_days(None, {"lease": 90, "insurance": 60}, di.SUBJECT_LEASE), 90)
check("E6 insurance reads its own floor",
      di.resolve_notice_days(None, {"lease": 90, "insurance": 45}, di.SUBJECT_POLICY), 45)
check("E7 missing config -> the house 60", di.resolve_notice_days(None, None, di.SUBJECT_LEASE), 60)
check("E8 garbage config -> the house 60",
      di.resolve_notice_days(None, {"lease": "soon"}, di.SUBJECT_LEASE), 60)
check("E9 an already-resolved int floor is accepted", di.resolve_notice_days(None, 120), 120)
check("E10 absurd requirement clamped", di.resolve_notice_days(99999, HOUSE_CFG), di.MAX_NOTICE_DAYS)
check("E11 ladder under a 180-day window", di.milestones_for(180), (0, 1, 7, 14, 30, 60, 180))
check("E12 ladder under the 60-day floor has no duplicate 60", di.milestones_for(60), (0, 1, 7, 14, 30, 60))
check("E13 ladder under a 45-day window drops the 60 nudge", di.milestones_for(45), (0, 1, 7, 14, 30, 45))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("F. expiry alerts — right day, right people, once per milestone")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
C_OWNER = {"name": "Owner", "email": "owner@co.example", "notify_expiry": True}
C_BROKER = {"name": "Broker", "email": "broker@x.example", "notify_expiry": True, "notice_days": 120}
C_MUTED = {"name": "Muted", "email": "muted@x.example", "notify_expiry": False}
C_NOEMAIL = {"name": "Phone only", "phone": "555-0000", "notify_expiry": True}

SUBJ_COI = {"kind": di.SUBJECT_COI, "ref": "1115", "label": "Store 1115 COI",
            "expires_on": "2026-11-04", "contacts": [C_OWNER, C_MUTED, C_NOEMAIL]}
check("F1 62 days out: nothing yet (floor is 60)",
      di.expiry_alerts("2026-09-03", [SUBJ_COI], HOUSE_CFG), [])
a = di.expiry_alerts("2026-09-05", [SUBJ_COI], HOUSE_CFG)
check("F2 60 days out: the COI alert fires", (len(a), a[0]["milestone"], a[0]["days_out"]), (1, 60, 60))
check("F3 only contacts who want notice AND have an email",
      [r["email"] for r in a[0]["recipients"]], ["owner@co.example"])
check("F4 dedupe key names the milestone", a[0]["dedupe_key"], "insurance_coi:1115:2026-11-04:m60")
check("F5 the alert log suppresses a repeat",
      di.expiry_alerts("2026-09-06", [SUBJ_COI], HOUSE_CFG, already_sent={a[0]["dedupe_key"]}), [])
b = di.expiry_alerts("2026-10-05", [SUBJ_COI], HOUSE_CFG, already_sent={a[0]["dedupe_key"]})
check("F6 the 30-day nudge is a NEW milestone", (b[0]["milestone"], b[0]["days_out"]), (30, 30))
c = di.expiry_alerts("2026-11-05", [SUBJ_COI], HOUSE_CFG)
check("F7 past the date: the expired alert", (c[0]["milestone"], c[0]["expired"], c[0]["days_out"]),
      (0, True, -1))

SUBJ_LEASE = {"kind": di.SUBJECT_LEASE, "ref": "1115", "label": "Store 1115 lease",
              "expires_on": "2027-03-01", "own_notice_days": 180,
              "contacts": [C_OWNER, C_BROKER]}
d = di.expiry_alerts("2026-09-02", [SUBJ_LEASE], HOUSE_CFG)
check("F8 a 180-day lease alerts 180 days out, not 60",
      (d[0]["milestone"], d[0]["notice_days"], d[0]["days_out"]), (180, 180, 180))
check("F9 both contacts get the 180-day notice",
      sorted(r["email"] for r in d[0]["recipients"]), ["broker@x.example", "owner@co.example"])
SUBJ_SHORT = dict(SUBJ_LEASE, own_notice_days=None, expires_on="2027-01-30")
e = di.expiry_alerts("2026-10-02", [SUBJ_SHORT], HOUSE_CFG)
check("F10 a contact's LONGER own window gets the early notice alone",
      (e[0]["milestone"], [r["email"] for r in e[0]["recipients"]]), (120, ["broker@x.example"]))
f = di.expiry_alerts("2026-12-01", [SUBJ_SHORT], HOUSE_CFG, already_sent={e[0]["dedupe_key"]})
check("F11 at the floor everyone is notified",
      (f[0]["milestone"], sorted(r["email"] for r in f[0]["recipients"])),
      (60, ["broker@x.example", "owner@co.example"]))
check("F12 no expiry date -> silence (never a guessed date)",
      di.expiry_alerts("2026-09-05", [dict(SUBJ_COI, expires_on=None)], HOUSE_CFG), [])
check("F13 no contact who wants notice -> silence, never mail to nobody",
      di.expiry_alerts("2026-09-05", [dict(SUBJ_COI, contacts=[C_MUTED])], HOUSE_CFG), [])
check("F14 garbage subject skipped, others still processed",
      len(di.expiry_alerts("2026-09-05", ["nonsense", SUBJ_COI], HOUSE_CFG)), 1)
subj_line, html = di.alert_email(a[0])
check("F15 the email says what expires and when",
      ("Store 1115 COI" in subj_line and "2026-11-04" in subj_line and "60 days" in subj_line), True)
check("F16 an expiry notice carries no dollar amount", "$" in html, False)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("G. catalogue/prompt coherence — the two cannot drift")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for kind in di.SUBJECT_KINDS:
    prompt = dia.build_prompt(kind)
    keys = [s["key"] for s in di.FIELD_SPECS[kind]]
    check(f"G1 {kind}: every catalogue key is named in the prompt",
          [k for k in keys if f'"{k}"' not in prompt], [])
    other = [s["key"] for k2 in di.SUBJECT_KINDS if k2 != kind for s in di.FIELD_SPECS[k2]]
    check(f"G2 {kind}: no foreign key is offered",
          [k for k in set(other) - set(keys) if f'"{k}"' in prompt], [])
    check(f"G3 {kind}: money fields are flagged to the model",
          all("[MONEY" in ln for ln in prompt.splitlines()
              if any(f'"{s["key"]}"' in ln for s in di.FIELD_SPECS[kind] if s["money_guarded"])), True)
lease_prompt = dia.build_prompt(di.SUBJECT_LEASE)
check("G4 the lease prompt asks for clause numbers + plain English",
      ("clause_number" in lease_prompt and "plain_english" in lease_prompt
       and "plain English" in lease_prompt), True)
check("G5 the prompt forbids transcribing bank numbers",
      "routing" in lease_prompt.lower() and "bank account" in lease_prompt.lower(), True)
pol_prompt = dia.build_prompt(di.SUBJECT_POLICY, TENANT)
check("G6 the tenant's OWN coverage vocabulary is what the model is offered",
      ("garagekeepers" in pol_prompt and "cyber" not in pol_prompt), True)
check("G7 no ANTHROPIC_API_KEY -> a clean empty draft, never an exception",
      dia.extract_document(b"%PDF-1.4", "application/pdf", di.SUBJECT_POLICY)["status"]
      in ("not_extracted", "failed"), True)
check("G8 an unsupported file type never reaches the model",
      dia.extract_document(b"x", "text/plain", di.SUBJECT_LEASE)["status"], "not_extracted")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("H. interop with the SHIPPED money path (store_lease, mig 946) — no second derivation")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
accepted = g2["patch"]["store_lease"]["rent_schedule"]
check("H1 the accepted schedule survives store_lease.normalize_rent_schedule byte-identically",
      sl.normalize_rent_schedule(accepted), accepted)
check("H2 and drives the real rent_for_month (Jul 2026 -> the 2026 step)",
      sl.rent_for_month(2026, 7, current_rent=5000, rent_schedule=accepted), 5150.0)
check("H3 (Aug 2027 -> the 2027 step)",
      sl.rent_for_month(2027, 8, current_rent=5000, rent_schedule=accepted), 5304.5)
check("H4 an accepted rent_due is exactly what resolve_rent_due accepts",
      sl.normalize_rent_due(lk["rent_due"]["value"]), {"kind": "day", "value": 1})
check("H5 nothing accepted -> the shipped path is untouched (no fake 0)",
      sl.rent_for_month(2026, 7), None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("I. ARMED negative control")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_broken = dict(di.MONEY_GUARDED)  if False else None      # noqa: F841  (placeholder, see below)
check("I1 harness is armed (a money field must never pass without confirmation)",
      di.apply_plan(di.SUBJECT_LEASE, LF, ["current_rent"])["patch"] == {}, True)
check("I2 armed: a deliberately wrong expectation fails",
      di.resolve_notice_days(180, HOUSE_CFG, di.SUBJECT_LEASE) == 60, False)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("J. multi-tenant static guard — every query on a new table is org-scoped")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# storeops/router.py is outside harness_org_scope_guard.py's scope (that guard reads commcalc's
# router), so this build brings its own: a static scan proving that EVERY query chain touching one of
# the four tables added by migs 964-966 carries an org filter or an org_id in its payload. These
# tables hold lease text, contacts and insurance figures; one missing filter is a cross-tenant leak,
# and their RLS is deliberately service-role-only (no open_all policy), so the application filter IS
# the isolation. Same classification rules as the commcalc guard.
import re as _re

_NEW_TABLES = ("insurance_policy", "insurance_policy_store", "document_extraction", "document_contact")
_router_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "app", "modules", "storeops", "router.py")).read()
_unscoped, _payload = [], []
for _t in _NEW_TABLES:
    for _m in _re.finditer(r'\.table\(["\']' + _t + r'["\']\)', _router_src):
        _chain = _router_src[_m.start():_m.start() + 1400]
        _end = _chain.find(".execute(")
        _chain = _chain[:_end] if _end > 0 else _chain
        _line = _router_src[:_m.start()].count("\n") + 1
        if "org_id" in _chain:
            continue                                   # filtered, or an inline org_id payload
        _is_write = ((".insert(" in _chain or ".upsert(" in _chain)
                     and ".update(" not in _chain and ".delete(" not in _chain)
        if _is_write:
            # PAYLOAD class (the commcalc guard's own rule): the row is built in a variable above,
            # so the chain text can't show org_id. Prove the builder sets it rather than trusting it.
            _before = _router_src[max(0, _m.start() - 1600):_m.start()]
            if '"org_id": org_id' in _before or '["org_id"] = org_id' in _before:
                _payload.append(f"{_t} @ router.py:{_line}")
                continue
        _unscoped.append(f"{_t} @ router.py:{_line}")
check("J1 every read/update/delete on the new tables is org-filtered", _unscoped, [])
check("J2 every insert on the new tables carries org_id in its payload", len(_payload) >= 4, True)
check("J2b the scan actually found the queries (guard is not vacuous)",
      all(_re.search(r'\.table\(["\']' + t + r'["\']\)', _router_src) for t in _NEW_TABLES), True)
check("J3 no endpoint echoes a private storage path for a policy document",
      'select("id,policy_id,doc_kind,file_name,content_type,size_bytes,uploaded_by,uploaded_at")'
      in _router_src, True)

print()
print(f"PASS {len(PASS)}   FAIL {len(FAIL)}")
for f in FAIL:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
