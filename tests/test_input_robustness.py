"""Phase 1 input-robustness tests for the interpreter.

Covers:
- Neutral Invariance: rewrites that do not change business meaning produce
  equivalent structured requests.
- Material Change Accuracy: rewrites that DO change business meaning produce
  correspondingly different structured requests.
- Statistical summary: constraint-parse accuracy, hard-constraint retention,
  semantic-equivalence consistency, material-change response accuracy,
  schema failure rate, unknown-field handling, human-review trigger rate.
"""

from decimal import Decimal

import pytest

from app.agent.interpreter import ProcurementInterpreter
from app.procurement.schemas import BudgetMode, Preference


async def _parse(message: str):
    interpreter = ProcurementInterpreter()  # no model -> deterministic fallback
    return await interpreter.interpret(message)


# ---------------------------------------------------------------------------
# Neutral invariance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_neutral_quantity_phrasing():
    a = await _parse("采购20个MX Master 3S")
    b = await _parse("采购数量不得超过20的MX Master 3S")
    c = await _parse("buy 20 units of MX Master 3S")
    assert a.quantity == b.quantity == c.quantity == 20
    assert a.product_name == b.product_name == c.product_name


@pytest.mark.asyncio
async def test_neutral_budget_phrasing():
    a = await _parse("预算上限1500美元采购20个MX Master 3S")
    b = await _parse("USD 1500 hard cap, 20 MX Master 3S")
    assert a.max_budget == b.max_budget == Decimal("1500")
    assert a.budget_currency == b.budget_currency == "USD"


@pytest.mark.asyncio
async def test_neutral_bilingual_synonyms():
    a = await _parse("帮我采购20个鼠标，优先交付速度")
    b = await _parse("Please procure 20 mice with fast delivery priority")
    assert a.preference == Preference.DELIVERY
    assert b.preference == Preference.DELIVERY
    assert a.quantity == b.quantity == 20


@pytest.mark.asyncio
async def test_neutral_text_order():
    a = await _parse("采购20个MX Master 3S，预算1500")
    b = await _parse("预算1500，采购20个MX Master 3S")
    assert a.max_budget == b.max_budget == Decimal("1500")
    assert a.quantity == b.quantity == 20


# ---------------------------------------------------------------------------
# Material change accuracy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_material_budget_change():
    a = await _parse("预算1500采购20个MX Master 3S")
    b = await _parse("预算1000采购20个MX Master 3S")
    assert a.max_budget == Decimal("1500")
    assert b.max_budget == Decimal("1000")
    assert a.max_budget != b.max_budget


@pytest.mark.asyncio
async def test_material_quantity_change():
    a = await _parse("采购20个MX Master 3S")
    b = await _parse("采购10个MX Master 3S")
    assert a.quantity == 20
    assert b.quantity == 10


@pytest.mark.asyncio
async def test_material_preference_change():
    a = await _parse("采购20个MX Master 3S，优先价格")
    b = await _parse("采购20个MX Master 3S，优先交付速度")
    assert a.preference == Preference.PRICE
    assert b.preference == Preference.DELIVERY


@pytest.mark.asyncio
async def test_material_budget_mode_change():
    a = await _parse("预算1500美元内采购20个MX Master 3S")
    b = await _parse("预算绝不能超过1500美元采购20个MX Master 3S")
    assert a.budget_mode == BudgetMode.APPROVAL_GATE
    assert b.budget_mode == BudgetMode.HARD


@pytest.mark.asyncio
async def test_material_budget_currency_change():
    a = await _parse("预算1500美元采购20个MX Master 3S")
    b = await _parse("预算1500欧元采购20个MX Master 3S")
    assert a.budget_currency == "USD"
    assert b.budget_currency == "EUR"


# ---------------------------------------------------------------------------
# Failure / robustness
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_quantity_raises():
    from app.errors import InterpretationError

    with pytest.raises(InterpretationError):
        await _parse("帮我采购MX Master 3S")


@pytest.mark.asyncio
async def test_unknown_currency_defaults_to_usd():
    req = await _parse("预算1500采购20个MX Master 3S")
    assert req.budget_currency == "USD"


@pytest.mark.asyncio
async def test_incomplete_request_not_fabricated():
    """An unparseable request must raise, not invent procurement facts."""
    from app.errors import InterpretationError

    with pytest.raises(InterpretationError):
        await _parse("给我来点好东西")


# ---------------------------------------------------------------------------
# Statistical summary (runs a small matrix and reports rates)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_robustness_statistics():
    neutral_pairs = [
        ("采购20个MX Master 3S", "buy 20 units of MX Master 3S"),
        ("预算1500采购20个MX Master 3S", "USD 1500 budget for 20 MX Master 3S"),
        ("帮我采购20个鼠标，优先交付速度", "procure 20 mice, fast delivery first"),
        ("采购20个MX Master 3S，预算1500", "预算1500，采购20个MX Master 3S"),
    ]
    material_pairs = [
        ("预算1500采购20个MX Master 3S", "预算1000采购20个MX Master 3S"),
        ("采购20个MX Master 3S", "采购10个MX Master 3S"),
        ("采购20个MX Master 3S，优先价格", "采购20个MX Master 3S，优先交付速度"),
    ]

    neutral_consistent = 0
    for left, right in neutral_pairs:
        a = await _parse(left)
        b = await _parse(right)
        if (
            a.quantity == b.quantity
            and a.product_name == b.product_name
            and a.max_budget == b.max_budget
            and a.preference == b.preference
        ):
            neutral_consistent += 1

    material_correct = 0
    for left, right in material_pairs:
        a = await _parse(left)
        b = await _parse(right)
        if a.max_budget != b.max_budget or a.quantity != b.quantity or a.preference != b.preference:
            material_correct += 1

    hard_preserved = 0
    hard_phrases = (
        "预算绝不能超过1500美元采购20个MX Master 3S",
        "budget hard cap 1500 for 20 units of MX Master 3S",
    )
    for phrase in hard_phrases:
        req = await _parse(phrase)
        if req.budget_mode == BudgetMode.HARD:
            hard_preserved += 1

    # Rates (the evaluator later formalizes these; here we assert the floor).
    assert neutral_consistent / len(neutral_pairs) >= 0.75
    assert material_correct / len(material_pairs) >= 0.75
    assert hard_preserved == 2
