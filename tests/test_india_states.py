"""State names from anywhere in India come out as one official spelling."""
from __future__ import annotations

import pytest

from chirala_common.india import STATES, normalise_state, state_code_problem


def test_every_state_and_union_territory_is_known():
    assert len(STATES) == 36


@pytest.mark.parametrize("name", sorted(STATES))
def test_official_names_are_kept_in_any_case(name):
    assert normalise_state(name) == name
    assert normalise_state(f"  {name.upper()} ") == name


@pytest.mark.parametrize("typed,expected", [
    ("AP", "Andhra Pradesh"), ("TN", "Tamil Nadu"), ("MH", "Maharashtra"),
    ("K.A.", "Karnataka"), ("UP", "Uttar Pradesh"), ("WB", "West Bengal"),
    ("J&K", "Jammu and Kashmir"), ("Orissa", "Odisha"), ("Pondicherry", "Puducherry"),
    ("Uttaranchal", "Uttarakhand"), ("New Delhi", "Delhi"), ("TS", "Telangana"),
    ("Daman and Diu", "Dadra and Nagar Haveli and Daman and Diu"),
    ("Andaman & Nicobar Islands", "Andaman and Nicobar Islands"),
])
def test_abbreviations_and_old_names(typed, expected):
    assert normalise_state(typed) == expected


@pytest.mark.parametrize("typed,expected", [
    ("AndraPradash", "Andhra Pradesh"), ("Andra Pradash", "Andhra Pradesh"),
    ("Karnatak", "Karnataka"), ("Maharastra", "Maharashtra"), ("Tamilnadu", "Tamil Nadu"),
    ("Rajastan", "Rajasthan"), ("Telengana", "Telangana"), ("Meghalay", "Meghalaya"),
    ("Chattisgarh", "Chhattisgarh"), ("West Bangal", "West Bengal"),
])
def test_close_misspellings(typed, expected):
    assert normalise_state(typed) == expected


@pytest.mark.parametrize("typed", ["California", "Western Province", "Bavaria", "XYZ"])
def test_regions_outside_india_are_left_alone(typed):
    assert normalise_state(typed) == typed


def test_blank_is_none():
    assert normalise_state("  ") is None and normalise_state(None) is None


def test_state_code_must_belong_to_the_state():
    assert state_code_problem("Kerala", "32") is None
    assert state_code_problem("kl", "32") is None
    msg = state_code_problem("Andhra Pradesh", "36")
    assert msg and "Telangana" in msg and "Andhra Pradesh" in msg
    assert "not a GST state code" in state_code_problem("Goa", "99")


def test_state_code_is_not_checked_without_both_or_outside_india():
    assert state_code_problem("Goa", None) is None
    assert state_code_problem(None, "30") is None
    assert state_code_problem("California", "30") is None
