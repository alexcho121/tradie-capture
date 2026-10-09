import pytest

from app import numbers as nm


def hours(text):
    return [(d["hours"], d["hedged"], d["range"]) for d in nm.extract_hours(text)]


@pytest.mark.parametrize("text,expected", [
    ("it'll take two hours", [(2.0, False, False)]),
    ("it'll take 2 hours", [(2.0, False, False)]),
    ("2h on site", [(2.0, False, False)]),
    ("two and a half hours", [(2.5, False, False)]),
    ("3 and a half hours", [(3.5, False, False)]),
    ("an hour and a half", [(1.5, False, False)]),
    ("half an hour", [(0.5, False, False)]),
    ("90 minutes", [(1.5, False, False)]),
    ("thirty minutes", [(0.5, False, False)]),
    ("an hour", [(1.0, False, False)]),
    ("one hour", [(1.0, False, False)]),
])
def test_clear_durations(text, expected):
    assert hours(text) == expected


@pytest.mark.parametrize("text", [
    "probably two hours", "about 2 hours", "two hours or so", "say 90 minutes", "roughly an hour",
])
def test_hedged_durations(text):
    found = nm.extract_hours(text)
    assert len(found) == 1 and found[0]["hedged"] is True


def test_a_couple_of_hours_is_two_and_hedged():
    assert hours("a couple of hours") == [(2.0, True, False)]


def test_vague_durations_are_not_converted():
    for text in ["half a day extra", "all day", "a few hours", "back in twenty", "a while", "300 mm", "20mm copper"]:
        assert nm.extract_hours(text) == [], text


def test_range_yields_both_ends_flagged_as_range():
    assert hours("two to three hours") == [(2.0, True, True), (3.0, True, True)]


def test_overlapping_patterns_do_not_double_count():
    assert len(nm.extract_hours("half an hour")) == 1
    assert len(nm.extract_hours("an hour and a half")) == 1
    assert len(nm.extract_hours("two and a half hours")) == 1


def test_numbers_in_digits_and_words():
    got = nm.numbers_in("three hundred and fifty mil, level two, twenty-five, 1,200 and 2.5")
    assert {350.0, 2.0, 25.0, 1200.0, 2.5} <= got


def test_numbers_in_ordinals_halves_and_decimals():
    got = nm.numbers_in("the second floor, one point five metres, two and a half, half")
    assert {2.0, 1.5, 2.5, 0.5} <= got


def test_lone_one_can_be_ignored_for_values():
    assert 1.0 not in nm.numbers_in("the one on the left", ignore_lone_one=True)
    assert 1.0 in nm.numbers_in("one hour")


def test_phrase_to_number():
    assert nm.phrase_to_number("twenty-one") == 21
    assert nm.phrase_to_number("1,200") == 1200
    assert nm.phrase_to_number("2.5") == 2.5
    assert nm.phrase_to_number("hello") is None


def test_words_to_numbers():
    assert nm.words_to_numbers("three hundred mil and four hundred dollars") == "300 mil and 400 dollars"
