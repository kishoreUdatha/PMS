"""Inclusive tax splits into components that add back to the price.

Pure arithmetic on ``chirala_common.tax_engine``, so it needs no database and
cannot skip. Each GST component used to be rounded independently from the
taxable base; the parts then came to a paisa more or less than the price the
guest was quoted on about half of all prices, and an invoice whose lines do
not add up to its total is one an auditor rejects.
"""

from __future__ import annotations

from decimal import Decimal

from chirala_common.tax_engine import compute_tax

_GST_12_INCLUSIVE = {
    "code": "GST12", "rate_type": "percent", "rate_value": "12",
    "apply_as": "inclusive",
    "calculation_rule": {"components": [{"code": "CGST", "rate": 6},
                                        {"code": "SGST", "rate": 6}]},
}


def test_inclusive_components_always_sum_to_the_gross():
    for cents in range(100_000, 130_000, 7):
        gross = Decimal(cents) / 100
        result = compute_tax([_GST_12_INCLUSIVE], amount=gross)
        base = result.lines[0].taxable_amount
        assert base + sum(line.tax_amount for line in result.lines) == gross, \
            gross
        assert result.inclusive_total == gross - base
        assert result.exclusive_total == 0


def test_the_components_split_the_tax_by_rate():
    result = compute_tax([_GST_12_INCLUSIVE], amount=Decimal("1120.00"))
    assert [(line.tax_code, line.tax_amount) for line in result.lines] == [
        ("CGST", Decimal("60.00")), ("SGST", Decimal("60.00"))]


def test_exclusive_tax_is_unchanged():
    rule = dict(_GST_12_INCLUSIVE, apply_as="exclusive")
    result = compute_tax([rule], amount=Decimal("1000.00"))
    assert [line.tax_amount for line in result.lines] == [
        Decimal("60.00"), Decimal("60.00")]
    assert result.exclusive_total == Decimal("120.00")
