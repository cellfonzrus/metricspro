"""Unit: the paste-once portal-login import, the pure half (index §COMMCALC-83).

Owner, 2026-11-20, of the 29 per-store ePay logins: *"Enter those for me"*. What makes that
answerable is that the block he already has becomes 29 sealed rows in one post — and what makes it
SAFE is that the parser is pure, places no store it was not told about, and never puts a password in
anything it hands back.
"""
from app.modules.commcalc import portal_login_import as pli

PW = "n0t-a-real-password-9f3c"
ROSTER = {"b-1115": "B-1115", "b-1750": "B-1750"}


def resolve(raw):
    k = str(raw or "").strip().lower()
    if k in ROSTER:
        return ROSTER[k], "code"
    if k == "jamaica ave":
        return "B-1750", "address"
    return None, None


def parse(text, **kw):
    kw.setdefault("processor", "epay")
    kw.setdefault("resolve", resolve)
    return pli.parse(text, **kw)


# ── what the operator pastes ──────────────────────────────────────────────────────────────────────
def test_a_tab_separated_block_becomes_one_row_per_store():
    out = parse("B-1115\tstore1115\t%s\nB-1750\tstore1750\t%s" % (PW, PW))
    assert out["ready"] == 2
    assert [(r["store_code"], r["username"], r["password"]) for r in out["rows"]] == [
        ("B-1115", "store1115", PW), ("B-1750", "store1750", PW)]


def test_commas_and_pipes_work_too_but_a_comma_inside_a_tabbed_line_does_not_split_it():
    assert parse("B-1115,store1115,%s" % PW)["ready"] == 1
    assert parse("B-1115|store1115|%s" % PW)["ready"] == 1
    cells = pli.split_line("4640 W Diversey, Unit A\tuser\tpw")
    assert cells == ["4640 W Diversey, Unit A", "user", "pw"]


def test_an_optional_header_row_names_the_columns_in_any_order():
    out = parse("Password\tUser ID\tStore Code\n%s\tstore1115\tB-1115" % PW)
    assert out["ready"] == 1
    assert out["rows"][0] == {"processor": "epay", "store_code": "B-1115",
                              "username": "store1115", "password": PW}


def test_a_store_code_that_looks_like_a_header_word_does_not_eat_its_own_line():
    """A row is a header only when EVERY cell is a header word — otherwise a store coded "ID"
    would turn its own data into a header and vanish."""
    assert pli.header_fields(["ID", "store1115", PW]) is None


def test_the_shared_portal_address_is_applied_to_every_login():
    out = parse("B-1115\tu\t%s" % PW, portal_url=" https://portal.example.com ")
    assert out["rows"][0]["portal_url"] == "https://portal.example.com"


def test_a_label_column_is_carried_when_pasted_and_absent_when_not():
    out = parse("Store\tUsername\tPassword\tLabel\nB-1115\tu\t%s\tJamaica Ave" % PW)
    assert out["rows"][0]["label"] == "Jamaica Ave"
    assert "label" not in parse("B-1115\tu\t%s" % PW)["rows"][0]


# ── three states, not two ─────────────────────────────────────────────────────────────────────────
def test_a_store_this_org_has_not_declared_is_reported_not_guessed():
    out = parse("Nowhere Mall\tu\t%s" % PW)
    assert out["ready"] == 0 and out["unresolved"] == 1 and out["rows"] == []
    assert "not a store this organisation has declared" in out["lines"][0]["reason"]


def test_a_declared_address_resolves_to_its_code_and_says_how():
    out = parse("jamaica ave\tu\t%s" % PW)
    assert out["rows"][0]["store_code"] == "B-1750"
    assert "matched by address" in out["lines"][0]["reason"]


def test_a_line_missing_a_field_is_skipped_and_names_the_field():
    out = parse("B-1115\tstore1115\t\nB-1750\t\t%s" % PW)
    assert out["ready"] == 0 and out["skipped"] == 2
    assert "no password on this line" in out["lines"][0]["reason"]
    assert "no username on this line" in out["lines"][1]["reason"]


def test_the_same_store_twice_in_one_paste_is_skipped_naming_the_earlier_line():
    """Two logins for one store makes "which login is this store's" a question with two answers,
    and the download console's grid is keyed on the store."""
    out = parse("B-1115\tfirst\t%s\nb-1115\tsecond\t%s" % (PW, PW))
    assert out["ready"] == 1 and out["skipped"] == 1
    assert "already on line 1" in out["lines"][1]["reason"]


def test_every_non_blank_line_is_accounted_for_so_nothing_is_dropped_in_silence():
    out = parse("B-1115\tu\t%s\n\n   \nNowhere\tu\t%s" % (PW, PW))
    assert len(out["lines"]) == 2
    assert out["ready"] + out["skipped"] + out["unresolved"] == 2


def test_a_resolver_that_fails_places_nothing_rather_than_placing_it_wrongly():
    def angry(_):
        raise RuntimeError("roster unreadable")
    out = parse("B-1115\tu\t%s" % PW, resolve=angry)
    assert out["ready"] == 0 and out["unresolved"] == 1


def test_a_blank_paste_claims_nothing():
    assert parse("") == {"rows": [], "lines": [], "ready": 0, "skipped": 0, "unresolved": 0}


# ── a credential is never in an answer ────────────────────────────────────────────────────────────
def test_no_password_appears_in_any_line_verdict_reason_or_echo():
    out = parse("B-1115\tu\t%s\nNowhere\tu2\t%s" % (PW, PW))
    assert PW not in repr(out["lines"])


def test_a_password_cell_is_quoted_back_as_its_length_only():
    out = parse("B-1115\tu\t%s" % PW)
    assert out["lines"][0]["cells"] == ["B-1115", "u", "%d character password" % len(PW)]


def test_the_rows_do_carry_the_password_because_they_are_what_gets_sealed():
    assert parse("B-1115\tu\t%s" % PW)["rows"][0]["password"] == PW
