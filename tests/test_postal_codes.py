"""The postal code rule shared by guests, accounts, invoices and properties."""
from __future__ import annotations

import pytest

from chirala_common.postal import problem


@pytest.mark.parametrize("code", ["523155", "500018", "110001", " 524341 "])
def test_real_indian_pin_codes_pass(code):
    assert problem(code, "India") is None


@pytest.mark.parametrize("code", ["34536", "4242345", "023155", "52315A", "523 155"])
def test_malformed_indian_pin_codes_are_refused(code):
    msg = problem(code, "India")
    assert msg is not None and "6 digits" in msg


def test_country_match_ignores_case_and_spaces():
    assert problem("34536", " india ") is not None


@pytest.mark.parametrize("code,country", [
    ("SW1A 1AA", "United Kingdom"), ("10001", "United States"),
    ("10115", "Germany"), ("018956", "Singapore"), ("SW1A 1AA", None),
])
def test_foreign_codes_only_need_a_plausible_shape(code, country):
    assert problem(code, country) is None


@pytest.mark.parametrize("code", ["1", "ABCDEFGHIJK", "12#45", "-1234"])
def test_foreign_codes_with_the_wrong_shape_are_refused(code):
    assert problem(code, "United Kingdom") is not None


@pytest.mark.parametrize("code", [None, "", "   "])
def test_an_empty_code_is_not_a_problem(code):
    assert problem(code, "India") is None
