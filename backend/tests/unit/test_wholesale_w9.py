"""Unit: THE answer to "may I sell to this customer, and is this sale taxable" (index §POS-16).

Owner, verbatim: *"add … a customer type as wholesale where the option to not charge tax will be by
default if the user confirms they received the filled out w9 and block the user to make a sale
before the system has an updated w9, only the admin can go past without an updated w9"*.

THE TWO FACTS THIS MODULE OWNS, and why they must be one answer and not two:

  1. IS THIS SALE TAXABLE. Tax-free is not a property of a customer type — it is a property of a
     customer type WITH a current exemption certificate on file. A screen that zeroed the tax
     because the account type says Wholesale, while the gate separately decided the W-9 was out of
     date, would let a sale go out tax-free on a certificate nobody holds. So the same call answers
     both, and `exempt_basis` says WHY — a tax-free sale with no stated basis is the thing an
     auditor asks about.
  2. MAY THIS SALE BE RUNG AT ALL. Blocked until a current W-9 is on file; an admin may go past,
     and that override is NAMED so the sale carries who allowed it.

Every assertion is tenant-neutral: the rules are the platform's, and how long a W-9 stays current
(and whether wholesale is tax-free at all) is each org's declaration.
"""
import datetime as dt

from app.modules.pos import wholesale_w9 as w9

NOW = dt.date(2026, 10, 30)
RETAIL = {"id": "c-1", "account_type": "Personal"}
BUSINESS = {"id": "c-2", "account_type": "Business"}
WHOLESALE = {"id": "c-3", "account_type": "Wholesale", "company_name": "A Shop"}


def doc(received="2026-01-15", status="received", **over):
    d = {"kind": "w9", "status": status, "received_at": received, "expires_at": None}
    d.update(over)
    return d


# ── the customer type ───────────────────────────────────────────────────────────────────────────
def test_wholesale_is_a_customer_type_beside_the_two_that_existed():
    assert w9.ACCOUNT_TYPES == ("Personal", "Business", "Wholesale")
    assert w9.WHOLESALE == "Wholesale"


def test_whether_a_customer_is_wholesale_is_asked_of_this_module_not_spelled_by_callers():
    assert w9.is_wholesale(WHOLESALE) is True
    assert w9.is_wholesale(BUSINESS) is False
    assert w9.is_wholesale({"account_type": "  wholesale  "}) is True   # a stored row, as typed
    assert w9.is_wholesale({}) is False


# ── a retail sale is untouched ──────────────────────────────────────────────────────────────────
def test_a_retail_sale_is_never_blocked_and_never_exempt():
    for customer in (RETAIL, BUSINESS, None):
        d = w9.may_sell(customer, None, {}, now=NOW, is_admin=False)
        assert d["blocked"] is False and d["refusal"] is None
        assert d["tax_exempt"] is False and d["exempt_basis"] is None


# ── a wholesale sale with a current W-9 ─────────────────────────────────────────────────────────
def test_a_wholesale_customer_with_a_current_w9_sells_tax_free_and_says_why():
    d = w9.may_sell(WHOLESALE, doc(), {}, now=NOW, is_admin=False)
    assert d["blocked"] is False
    assert d["tax_exempt"] is True
    assert d["exempt_basis"] == "w9_on_file"
    assert d["w9_status"] == "current"


def test_an_org_may_declare_that_wholesale_is_still_taxable():
    d = w9.may_sell(WHOLESALE, doc(), {"wholesale_tax_exempt_default": False}, now=NOW, is_admin=False)
    assert d["blocked"] is False
    assert d["tax_exempt"] is False and d["exempt_basis"] is None


def test_a_confirmation_that_was_never_given_is_not_a_w9():
    """The owner's wording: tax-free *if the user confirms they received the filled out W-9*. A row
    that only RECORDS a request is not a receipt."""
    d = w9.may_sell(WHOLESALE, doc(status="requested", received=None), {}, now=NOW, is_admin=False)
    assert d["blocked"] is True and d["tax_exempt"] is False
    assert d["w9_status"] == "missing"


# ── a wholesale sale without one ────────────────────────────────────────────────────────────────
def test_no_w9_blocks_the_sale_and_the_refusal_names_what_is_needed():
    d = w9.may_sell(WHOLESALE, None, {}, now=NOW, is_admin=False)
    assert d["blocked"] is True
    assert "W-9" in d["refusal"]
    assert d["tax_exempt"] is False and d["exempt_basis"] is None
    assert d["w9_status"] == "missing"


def test_a_w9_older_than_the_declared_refresh_is_out_of_date_and_blocks():
    d = w9.may_sell(WHOLESALE, doc(received="2022-01-01"), {}, now=NOW, is_admin=False)
    assert d["blocked"] is True and d["w9_status"] == "expired"
    assert "out of date" in d["refusal"].lower() or "expired" in d["refusal"].lower()


def test_how_long_a_w9_stays_current_is_the_orgs_declaration():
    old = doc(received="2024-06-01")
    assert w9.may_sell(WHOLESALE, old, {"w9_valid_months": 36}, now=NOW, is_admin=False)["w9_status"] == "current"
    assert w9.may_sell(WHOLESALE, old, {"w9_valid_months": 12}, now=NOW, is_admin=False)["w9_status"] == "expired"


def test_an_org_may_declare_that_a_w9_never_goes_out_of_date():
    d = w9.may_sell(WHOLESALE, doc(received="2009-01-01"), {"w9_valid_months": 0}, now=NOW, is_admin=False)
    assert d["w9_status"] == "current" and d["blocked"] is False


def test_a_date_the_certificate_itself_carries_wins_over_the_declared_refresh():
    """A customer may give a certificate with its own expiry. That date is the fact; the org's
    refresh period is only what to assume when nobody stated one."""
    assert w9.may_sell(WHOLESALE, doc(received="2026-10-01", expires_at="2026-10-15"),
                     {}, now=NOW, is_admin=False)["w9_status"] == "expired"
    assert w9.may_sell(WHOLESALE, doc(received="2019-01-01", expires_at="2027-01-01"),
                     {}, now=NOW, is_admin=False)["w9_status"] == "current"


def test_an_org_may_declare_that_a_missing_w9_does_not_block_the_sale():
    d = w9.may_sell(WHOLESALE, None, {"w9_block_sale": False}, now=NOW, is_admin=False)
    assert d["blocked"] is False
    assert d["tax_exempt"] is False       # still not exempt — the certificate is what buys that
    assert d["w9_status"] == "missing"


# ── the admin override ──────────────────────────────────────────────────────────────────────────
def test_only_an_admin_may_go_past_a_missing_w9():
    blocked = w9.may_sell(WHOLESALE, None, {}, now=NOW, is_admin=False)
    assert blocked["blocked"] is True and blocked["override_available"] is False
    admin = w9.may_sell(WHOLESALE, None, {}, now=NOW, is_admin=True)
    assert admin["override_available"] is True
    assert admin["blocked"] is True       # offered, not taken — an override is a decision, not a role


def test_an_override_that_was_taken_unblocks_the_sale_and_is_named():
    d = w9.may_sell(WHOLESALE, None, {}, now=NOW, is_admin=True, override="E240")
    assert d["blocked"] is False
    assert d["override_by"] == "E240"
    assert d["tax_exempt"] is False       # going past a missing certificate never grants exemption
    assert d["exempt_basis"] is None


def test_a_non_admin_claiming_an_override_is_ignored_not_obeyed():
    d = w9.may_sell(WHOLESALE, None, {}, now=NOW, is_admin=False, override="E999")
    assert d["blocked"] is True and d["override_by"] is None


def test_an_override_on_a_sale_that_was_never_blocked_records_nothing():
    d = w9.may_sell(WHOLESALE, doc(), {}, now=NOW, is_admin=True, override="E240")
    assert d["blocked"] is False and d["override_by"] is None


# ── what the screen is told ─────────────────────────────────────────────────────────────────────
def test_the_cashier_is_told_what_to_do_next_not_only_that_it_is_blocked():
    d = w9.may_sell(WHOLESALE, None, {}, now=NOW, is_admin=False)
    assert d["needs_w9_request"] is True
    assert d["refusal"].rstrip().endswith((".", "?"))


def test_the_blank_form_link_is_the_declared_one_with_the_house_default_behind_it():
    assert w9.blank_form_url({}) == w9.HOUSE_W9_FORM_URL
    assert w9.blank_form_url({"w9_blank_form_url": "https://example.test/w9.pdf"}) \
        == "https://example.test/w9.pdf"


def test_a_declared_form_link_that_is_not_an_https_url_is_ignored_rather_than_shown():
    for bad in ("javascript:alert(1)", "http://x.test/w9.pdf", "not a url", ""):
        assert w9.blank_form_url({"w9_blank_form_url": bad}) == w9.HOUSE_W9_FORM_URL


def test_the_exemption_and_the_tax_rate_are_one_answer():
    """The register must not apply its own idea of a rate to an exempt sale."""
    assert w9.taxable_rate(0.08875, w9.may_sell(WHOLESALE, doc(), {}, now=NOW, is_admin=False)) == 0
    assert w9.taxable_rate(0.08875, w9.may_sell(RETAIL, None, {}, now=NOW, is_admin=False)) == 0.08875


# ── the return link: a credential, and the words that carry it ──────────────────────────────────
def test_a_return_token_is_not_derivable_from_the_customer():
    a, b = w9.new_token(), w9.new_token()
    assert a != b
    assert len(a) >= 32
    for row in (WHOLESALE, doc()):
        for value in row.values():
            assert str(value) not in a


def test_a_link_with_no_token_is_dead_and_so_is_an_expired_one():
    assert w9.token_live(None, now=NOW) is False
    assert w9.token_live({"return_token": ""}, now=NOW) is False
    assert w9.token_live({"return_token": "t", "token_expires_at": "2026-10-01T00:00:00"},
                         now=NOW) is False
    assert w9.token_live({"return_token": "t", "token_expires_at": "2026-12-01T00:00:00"},
                         now=NOW) is True
    assert w9.token_live({"return_token": "t"}, now=NOW) is True


def test_a_token_expiry_that_cannot_be_read_is_dead_rather_than_assumed_live():
    assert w9.token_live({"return_token": "t", "token_expires_at": "whenever"}, now=NOW) is False


def test_the_link_is_built_in_one_place():
    assert w9.return_link("https://app.test/", "abc") == "https://app.test/w9/abc"
    assert w9.RETURN_PATH in w9.return_link("https://app.test", "abc")


def test_the_email_carries_the_blank_form_and_the_return_link_and_nothing_else_about_the_customer():
    html = w9.request_html("A Shop", w9.HOUSE_W9_FORM_URL, "https://app.test/w9/abc", "Dana")
    assert w9.HOUSE_W9_FORM_URL in html and "https://app.test/w9/abc" in html
    assert "Dana" in html
    assert "<script" not in html


def test_the_email_escapes_what_it_is_given():
    html = w9.request_html("<script>x</script>", w9.HOUSE_W9_FORM_URL, "https://app.test/w9/a")
    assert "<script>" not in html


def test_every_dead_link_gets_the_same_sentence_so_a_probe_learns_nothing():
    assert w9.LINK_DEAD and "no longer valid" in w9.LINK_DEAD
