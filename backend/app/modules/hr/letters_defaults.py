"""hr.letters_defaults — the seeded DEFAULT template library (categories v1) + the merge-field
registry each category exposes to the admin UI / Send-Letter preview. Content only — no I/O, no
FastAPI. Every body is plain text with {{merge_field}} tokens (rendered by letters_logic.render_template).

Owner-editable: these are only the SEED. Once a tenant edits a row, letters.py flips that row's
is_default to false so a future re-seed (e.g. a new category added later) never overwrites their edit
— it only ever INSERTs rows that are still missing.
"""

COMPANY_SIGNOFF = "{{company_name}} — Human Resources"

# Fields every category can use in addition to its own (always resolvable, never blank):
COMMON_FIELDS = ["employee_name", "employee_first_name", "company_name", "store_name",
                 "today_date", "sender_name"]

CATEGORY_MERGE_FIELDS = {
    "late_clockin": COMMON_FIELDS + ["incident_date", "scheduled_start", "actual_clockin",
                                     "minutes_late", "grace_minutes", "strike_count"],
    "cash_shortage": COMMON_FIELDS + ["incident_date", "shortage_amount"],
    "inventory_shortage": COMMON_FIELDS + ["incident_date", "shortage_detail", "shortage_amount"],
    "accessory_shortfall": COMMON_FIELDS + ["incident_date", "shortfall_detail", "shortfall_amount"],
    "kpi_miss": COMMON_FIELDS + ["period", "kpi_summary"],
    "commission_statement": COMMON_FIELDS + ["period", "commission_amount"],
    "metrics_miss_2consec": COMMON_FIELDS + ["period", "prior_period", "kpi_summary", "commission_amount"],
}

CATEGORY_LABELS = {
    "late_clockin": "Late Clock-In",
    "cash_shortage": "Cash Shortage",
    "inventory_shortage": "Inventory Shortage",
    "accessory_shortfall": "Accessory Shortfall",
    "kpi_miss": "KPI Miss",
    "commission_statement": "Commission Statement",
    "metrics_miss_2consec": "Metrics Miss — 2 Consecutive Months",
}

# (template_key, category, escalation_tier, label, subject, body, delivery_mode)
# Delivery mode default is conservative ('approval') across the board — an owner can flip any
# individual template to 'auto' from the admin UI once they're comfortable with it. Tier-3/5 (and any
# termination-basis letter) MUST default to 'approval' per the owner's explicit directive; the others
# default to 'approval' too as the safer posture for a v1 disciplinary/money-adjacent feature.
DEFAULT_TEMPLATES = [
    ("late_clockin_tier1", "late_clockin", 1,
     "Late Clock-In — Notice (1st/2nd occurrence)",
     "Attendance Notice — Late Clock-In on {{incident_date}}",
     "Dear {{employee_first_name}},\n\n"
     "Our records show you clocked in at {{actual_clockin}} on {{incident_date}} at {{store_name}}, "
     "which was {{minutes_late}} minute(s) after your scheduled start time of {{scheduled_start}} "
     "(a {{grace_minutes}}-minute grace period is already applied).\n\n"
     "This is notice #{{strike_count}} of a late clock-in on your record. Punctuality is important to "
     "our team and our customers — please make sure you arrive and clock in on time for your "
     "scheduled shifts going forward.\n\n"
     "If you believe this was recorded in error, or if something outside your control caused this "
     "delay, please reach out to your manager or HR right away so we can review it.\n\n"
     "Thank you,\n" + COMPANY_SIGNOFF,
     "approval"),

    ("late_clockin_tier3", "late_clockin", 3,
     "Late Clock-In — Escalated Notice (3rd/4th occurrence)",
     "IMPORTANT — Attendance Notice — Late Clock-In on {{incident_date}}",
     "Dear {{employee_first_name}},\n\n"
     "Our records show you clocked in at {{actual_clockin}} on {{incident_date}} at {{store_name}}, "
     "which was {{minutes_late}} minute(s) after your scheduled start time of {{scheduled_start}} "
     "(a {{grace_minutes}}-minute grace period is already applied).\n\n"
     "This is notice #{{strike_count}} of a late clock-in on your record within the past review period. "
     "Because this is now the 3rd or more occurrence, please be aware that continued late arrivals "
     "can become the basis for a suspension without pay under our attendance policy.\n\n"
     "We want to help you succeed — if there is a recurring issue affecting your ability to arrive on "
     "time, please speak with your manager or HR as soon as possible so we can discuss it together.\n\n"
     "Please treat this notice seriously.\n\n" + COMPANY_SIGNOFF,
     "approval"),

    ("late_clockin_tier5", "late_clockin", 5,
     "Late Clock-In — Final Notice (5th+ occurrence)",
     "FINAL NOTICE — Attendance — Late Clock-In on {{incident_date}}",
     "Dear {{employee_first_name}},\n\n"
     "Our records show you clocked in at {{actual_clockin}} on {{incident_date}} at {{store_name}}, "
     "which was {{minutes_late}} minute(s) after your scheduled start time of {{scheduled_start}} "
     "(a {{grace_minutes}}-minute grace period is already applied).\n\n"
     "This is notice #{{strike_count}} of a late clock-in on your record within the past review period. "
     "Repeated late arrivals at this level can become a basis for termination of employment under our "
     "attendance policy.\n\n"
     "We are required to document this formally. Please contact your manager or HR immediately to "
     "discuss this notice and any steps you can take going forward.\n\n" + COMPANY_SIGNOFF,
     "approval"),

    ("cash_shortage", "cash_shortage", None,
     "Cash Shortage Notice",
     "Cash Shortage Notice — {{incident_date}}",
     "Dear {{employee_first_name}},\n\n"
     "During the closing reconciliation for {{incident_date}} at {{store_name}}, the cash declared did "
     "not match the system total — a shortage of approximately {{shortage_amount}}.\n\n"
     "Please review your closing procedures and count carefully going forward. If you have any "
     "information about this discrepancy, please contact your manager as soon as possible.\n\n"
     + COMPANY_SIGNOFF,
     "approval"),

    ("inventory_shortage", "inventory_shortage", None,
     "Inventory Shortage Notice",
     "Inventory Shortage Notice — {{incident_date}}",
     "Dear {{employee_first_name}},\n\n"
     "A device/inventory discrepancy was identified at {{store_name}} on or around {{incident_date}}: "
     "{{shortage_detail}} (approximate value {{shortage_amount}}).\n\n"
     "Please review inventory handling and RMA/return procedures. If you have information that would "
     "help resolve this, please contact your manager right away.\n\n" + COMPANY_SIGNOFF,
     "approval"),

    ("accessory_shortfall", "accessory_shortfall", None,
     "Accessory Shortfall Notice",
     "Accessory Shortfall Notice — {{incident_date}}",
     "Dear {{employee_first_name}},\n\n"
     "An accessory sales/chargeback discrepancy was identified at {{store_name}} on or around "
     "{{incident_date}}: {{shortfall_detail}} (amount {{shortfall_amount}}).\n\n"
     "Please review the accessory sale/chargeback details with your manager. If you have any questions "
     "about this amount, please reach out to HR.\n\n" + COMPANY_SIGNOFF,
     "approval"),

    ("kpi_miss", "kpi_miss", None,
     "KPI Performance Notice",
     "Performance Notice — {{period}}",
     "Dear {{employee_first_name}},\n\n"
     "For {{period}}, your performance did not meet one or more of our required KPI metrics:\n"
     "{{kpi_summary}}\n\n"
     "We would like to work with you to improve these numbers. Please speak with your manager about a "
     "plan to help you hit target next period.\n\n" + COMPANY_SIGNOFF,
     "approval"),

    ("commission_statement", "commission_statement", None,
     "Commission Statement",
     "Your Commission Statement — {{period}}",
     "Dear {{employee_first_name}},\n\n"
     "This is a summary of your commission for {{period}}: {{commission_amount}}.\n\n"
     "If you have any questions about this statement, please reach out to your manager or HR.\n\n"
     + COMPANY_SIGNOFF,
     "approval"),

    ("metrics_miss_2consec", "metrics_miss_2consec", None,
     "Performance Notice — 2 Consecutive Months Below Target",
     "IMPORTANT — Performance Notice ({{prior_period}} & {{period}})",
     "Dear {{employee_first_name}},\n\n"
     "Our records show that for two consecutive months — {{prior_period}} and {{period}} — your "
     "performance did not meet one or more required metrics:\n{{kpi_summary}}\n\n"
     "Your commission for {{period}} was {{commission_amount}}.\n\n"
     "Because this shortfall has continued for two months in a row, we need to meet with you to "
     "discuss a performance improvement plan. Please contact your manager to schedule this "
     "conversation as soon as possible.\n\n" + COMPANY_SIGNOFF,
     "approval"),
]
