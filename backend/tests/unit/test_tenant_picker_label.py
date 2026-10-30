"""Unit: what a human CALLS a company on a picker — the one home (index §POS-02 / §CORE-26).

THE DEFECT THIS REPRODUCES (owner, 2026-10-30, with a screenshot): the mobile app's "Choose a
company" screen listed four raw UUIDs instead of Cellfonz R Us / Luxelink Wireless LLC /
NY LOGISTICS / Vzone. A login cannot pick a company it cannot read.

THE CLASS, not the instance. "The mobile picker prints an id" is the instance. The class is that
*a company's human label was derived in three places* — `core/router._my_tenants_payload` with its
own `tenants` read and a `"Tenant"` stand-in, the web picker rendering `t.name` raw, and the app's
`tenantName()` falling all the way through to `org_id`. Three derivations, and the one at the end
of the chain could print a UUID. So the label becomes ONE fact with one home
(`core/tenant_identity`), and this file pins what that fact promises:

  • never blank — a picker row with no label is a button that cannot be chosen;
  • NEVER a UUID — the whole point, and the shape the live defect took;
  • the onboarding name when there is one, the slug read back as words when there is not.
"""
import pytest

from app.modules.core import tenant_identity as ti

HOUSE = "00000000-0000-0000-0000-000000000001"
OTHER = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"


def test_the_onboarding_name_is_the_label():
    assert ti.picker_label_from({"name": "Cellfonz R Us", "slug": "cellfonzrus"}, HOUSE) == "Cellfonz R Us"


def test_a_name_is_trimmed_to_one_line_exactly_as_the_other_reader_does():
    assert ti.picker_label_from({"name": "  Luxelink   Wireless  LLC \n"}, OTHER) == "Luxelink Wireless LLC"


def test_no_name_falls_back_to_the_slug_read_back_as_words():
    assert ti.picker_label_from({"name": "", "slug": "ny-logistics"}, OTHER) == "Ny Logistics"


def test_neither_name_nor_slug_is_a_plain_word_never_the_id():
    label = ti.picker_label_from({}, OTHER)
    assert label and OTHER not in label


def test_no_row_at_all_is_still_pickable_and_still_not_the_id():
    label = ti.picker_label_from(None, HOUSE)
    assert label and HOUSE not in label


@pytest.mark.parametrize("row", [None, {}, {"name": ""}, {"name": None, "slug": None},
                                 {"name": HOUSE}, {"slug": OTHER}])
def test_a_label_is_never_a_uuid_whatever_the_row_holds(row):
    """The live defect's exact shape, including the case where the stored name IS an id: a label
    that looks like a UUID is refused here, so it cannot reach a screen through any caller."""
    assert not ti.looks_like_uuid(ti.picker_label_from(row, HOUSE))


def test_looks_like_uuid_knows_the_shape_and_nothing_else():
    assert ti.looks_like_uuid(HOUSE) and ti.looks_like_uuid(OTHER.upper())
    assert not ti.looks_like_uuid("Vzone") and not ti.looks_like_uuid("") and not ti.looks_like_uuid(None)


def test_labels_for_a_list_are_the_same_fact_read_once(monkeypatch):
    """A picker asks for several at once. It must be the SAME answer as the single read, or the
    switcher and the picker drift — which is how this platform got three labels in the first place."""
    class _Client:
        def schema(self, _name):
            return self

        def table(self, _name):
            return self

        def select(self, _cols):
            return self

        def in_(self, _col, _vals):
            return self

        def order(self, _col):
            return self

        def execute(self):
            class R:
                data = [{"org_id": HOUSE, "name": "Cellfonz R Us", "slug": "cellfonzrus"},
                        {"org_id": OTHER, "name": "", "slug": "ny-logistics"}]
            return R()

    got = ti.picker_labels(_Client(), [HOUSE, OTHER])
    assert got[HOUSE] == ti.picker_label_from({"name": "Cellfonz R Us"}, HOUSE)
    assert got[OTHER] == ti.picker_label_from({"slug": "ny-logistics"}, OTHER)


def test_an_org_with_no_row_still_gets_a_label_from_the_list_read(monkeypatch):
    """A membership whose tenants row is missing is exactly the row the old code labelled `Tenant`
    for everyone. It still has to be pickable, and still must not read as an id."""
    class _Dead:
        def schema(self, _n):
            raise RuntimeError("no such table")

    got = ti.picker_labels(_Dead(), [HOUSE])
    assert got[HOUSE] and not ti.looks_like_uuid(got[HOUSE])
