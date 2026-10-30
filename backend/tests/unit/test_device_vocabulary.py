"""WHICH SALE LINE IS A DEVICE IS THE ORG'S OWN DECLARATION — not one tenant's POS spelling.

THE DEFECT THESE TESTS LOCK (owner-reported 2026-10-30): the Accessory Order Plan, the device order
forecast and the purchase-order recommendations all asked `demand_projection.is_device_line`, whose
house default was `('android - xp', 'iphone - xp', 'tablet - xp')` — which is CELLFONZ'S department
vocabulary. Luxelink files its handsets under `BrandedHandset`, so all three features reported that
Luxelink had sold no devices at all: 1,034 September handsets, read as zero.

The declaration already existed and was already right. `accessory_config.box_departments` (mig 218)
is the org's own list, read by the box count, the product mix, the GP report and the multi-handset
flag. Only this test had its own copy. Owner: *"it should be universal add on of a feture ... all
features should be for all tenanats"* — and RULE TWO already said it: no tenant vocabulary in code.

So the vocabulary is now a REQUIRED ARGUMENT. A caller cannot forget to ask the org, because there is
nothing to fall back to.
"""
import pytest

from app.modules.commcalc import demand_projection as dp

# The two tenants' REAL declarations, read from production 2026-10-30. Fixtures, not config.
CELLFONZ = ("Android - XP", "IPHONE - XP", "TABLET - XP")
LUXELINK = ("Android - XP", "IPHONE - XP", "TABLET - XP", "BrandedHandset", "Handset")

# Real (department, category) pairs from each tenant's September 2026 feed.
CELLFONZ_HANDSET = ("IPHONE - XP", "APPLE")
LUXELINK_HANDSET = ("BrandedHandset", "KittedBranded")
BILL_PAYMENT = ("Bill Payments", "Boost RTR")


class TestTheDeclarationDecides:
    def test_each_tenant_identifies_its_own_handset(self):
        assert dp.is_device_line(*CELLFONZ_HANDSET, CELLFONZ) is True
        assert dp.is_device_line(*LUXELINK_HANDSET, LUXELINK) is True

    def test_the_defect_itself_cellfonz_vocabulary_is_blind_to_luxelinks_handset(self):
        """The exact reported defect. Luxelink's handset against Cellfonz's list is NOT a device —
        which is correct behaviour for the wrong list, and is why the list may not be hardcoded."""
        assert dp.is_device_line(*LUXELINK_HANDSET, CELLFONZ) is False

    def test_a_tenants_declaration_does_not_leak_into_another(self):
        """Luxelink declaring `BrandedHandset` must not make it a device for Cellfonz. Each read is
        the asking org's own declaration — there is no union and no accumulation."""
        dp.is_device_line(*LUXELINK_HANDSET, LUXELINK)
        assert dp.is_device_line(*LUXELINK_HANDSET, CELLFONZ) is False

    def test_a_bill_payment_is_not_a_device_under_either_declaration(self):
        assert dp.is_device_line(*BILL_PAYMENT, CELLFONZ) is False
        assert dp.is_device_line(*BILL_PAYMENT, LUXELINK) is False

    def test_case_and_padding_do_not_decide(self):
        """The feed's own spelling drifts in case ('IPHONE - XP' vs 'iPhone - XP'). The declaration is
        matched case-insensitively, so a tenant is not punished for its POS's shift key."""
        assert dp.is_device_line("  brandedhandset ", "", LUXELINK) is True
        assert dp.is_device_line("BRANDEDHANDSET", "", LUXELINK) is True


class TestThereIsNothingToFallBackTo:
    def test_the_vocabulary_is_required_not_defaulted(self):
        """A caller that does not ask the org cannot get an answer. This is the whole fix: the old
        signature made the vocabulary optional, and every caller silently took one tenant's."""
        with pytest.raises(TypeError):
            dp.is_device_line("BrandedHandset", "KittedBranded")

    def test_no_tenant_department_vocabulary_survives_in_the_module(self):
        """RULE TWO, as an AST scan. The module may not carry a department list at all — the
        departments are the org's, and the house fallback has ONE home in the router (`_BOX_DEPTS`).

        AST, NOT A TEXT SEARCH, and deliberately so: the module's header NAMES the old spellings to
        record the defect they caused, and a check that forbade saying so would push the history out
        of the file that needs it. What is forbidden is a VALUE in the code, so only string constants
        that are not docstrings are scanned."""
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(dp))
        docstrings = {id(n.value) for n in ast.walk(tree)
                      if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
        values = [n.value.lower() for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)
                  and id(n) not in docstrings]
        for word in ("android - xp", "iphone - xp", "tablet - xp", "brandedhandset", "handset"):
            assert not [v for v in values if word in v], (
                f"{word!r} is a tenant's POS spelling and must not be a value in code")

    def test_an_empty_declaration_identifies_nothing_rather_than_guessing(self):
        """An org that has declared nothing has NOT declared Cellfonz's list. Nothing is a device,
        the report says it found no device sales, and a human fixes the declaration — which is
        honest, where silently using another tenant's words is not."""
        assert dp.is_device_line(*LUXELINK_HANDSET, ()) is False
        assert dp.is_device_line(*CELLFONZ_HANDSET, ()) is False


class TestTheCategoryHalfIsAFeedFormat:
    def test_a_category_marked_handset_is_a_device_with_no_department(self):
        """The older export format carries `category='CellPhone'` and a BLANK department. That word
        is a format marker, not a tenant or product name, so it stays a house tolerance."""
        assert dp.is_device_line("", "CellPhone", CELLFONZ) is True
        assert dp.is_device_line("", "cellphone", ()) is True

    def test_a_declared_category_vocabulary_replaces_the_house_one(self):
        assert dp.is_device_line("", "KittedBranded", (), categories=("kittedbranded",)) is True
        assert dp.is_device_line("", "CellPhone", (), categories=("kittedbranded",)) is False
