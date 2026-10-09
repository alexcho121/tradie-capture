"""Structuring + grounding. The model call is always mocked here; nothing in this file needs a network."""
import importlib.util
import json
from pathlib import Path

import pytest

from app import extract as ex
from app.config import Settings
from app.extract import blank_fields, compute_flags, extract_fields, ground_check, normalize_fields, rules_extract

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)
CASES = {c["id"]: c for c in json.loads((ROOT / "eval" / "golden_sentences.json").read_text(encoding="utf-8"))["cases"]}

# Sentences the regex fallback is expected to get fully right (a regression guard, not a quality claim:
# they are the same team's own sentences, so this number says little about real speech).
RULES_SHOULD_PASS = ["g01", "g02", "g03", "g04", "g05", "g06", "g09", "g10", "g12", "g13", "g15"]


def mk(**vals):
    f = blank_fields()
    for name, (value, conf) in vals.items():
        f[name] = {"value": value, "confidence": conf, "source": "llm", "edited": False}
    return f


def values(fields):
    return {k: v["value"] for k, v in fields.items() if v["value"] is not None}


# ------------------------------------------------------------------ rules fallback

@pytest.fixture
def rules_settings(tmp_path):
    return Settings(data_dir=tmp_path, extract_provider="rules")


@pytest.mark.parametrize("cid", RULES_SHOULD_PASS)
def test_rules_fallback_on_simple_sentences(cid, rules_settings):
    case = CASES[cid]
    fields, meta = extract_fields(case["transcript"], case["contacts"], rules_settings)
    bad = {n: (case["expected"][n], fields[n]["value"]) for n in fields
           if run_eval.score_field(n, case["expected"][n], fields[n]) != "correct"}
    assert not bad, bad
    assert meta["provider"] == "rules" and meta["fallback"] is False


def test_rules_hedged_hours_get_lower_confidence(rules_settings):
    fields, _ = extract_fields(CASES["g01"]["transcript"], ["John"], rules_settings)
    assert fields["extra_time_hours"]["value"] == 2 and fields["extra_time_hours"]["confidence"] <= 0.6


def test_empty_transcript_gives_blank_record_and_flag(rules_settings):
    fields, meta = extract_fields("   ", [], rules_settings)
    assert values(fields) == {} and meta["flags"] == ["no_speech"]


# ------------------------------------------------------------------ grounding

TX = "John asked me to move the bathroom pipe 300 mil to the left on level 2, probably two hours extra."


def removed_fields(log):
    return {e["field"] for e in log if e["action"] == "removed"}


def test_invented_number_removes_the_whole_field():
    fields, log = ground_check(mk(change=("move the bathroom pipe 350mm left", 0.9)), TX)
    assert fields["change"]["value"] is None and "350" in log[0]["reason"]


def test_spoken_number_words_match_digits():
    fields, log = ground_check(mk(change=("move it 300mm", 0.9)), "move it three hundred mil to the left")
    assert fields["change"]["value"] == "move it 300mm" and not log


def test_unspoken_duration_is_removed():
    fields, log = ground_check(mk(extra_time_hours=(2, 0.9)), "John said move the pipe")
    assert fields["extra_time_hours"]["value"] is None and removed_fields(log) == {"extra_time_hours"}


def test_hedged_duration_is_kept_but_capped():
    fields, log = ground_check(mk(extra_time_hours=(2, 0.95)), TX)
    assert fields["extra_time_hours"]["value"] == 2 and fields["extra_time_hours"]["confidence"] == 0.7
    assert log[0]["action"] == "capped"


def test_summed_durations_are_removed():
    fields, _ = ground_check(mk(extra_time_hours=(3, 0.9)), "an hour to dig and two hours to patch")
    assert fields["extra_time_hours"]["value"] is None


def test_range_is_not_a_single_duration():
    fields, log = ground_check(mk(extra_time_hours=(3, 0.9)), "downlights, two to three hours")
    assert fields["extra_time_hours"]["value"] is None and "range" in log[0]["reason"]


def test_surname_not_spoken_is_trimmed():
    fields, log = ground_check(mk(requester=("John Smith", 0.95)), TX, ["John Smith"])
    assert fields["requester"]["value"] == "John" and fields["requester"]["confidence"] <= 0.7
    assert log[0]["action"] == "trimmed"


def test_contact_who_was_not_mentioned_is_removed():
    fields, _ = ground_check(mk(requester=("Sarah", 0.9)), TX, ["John", "Sarah"])
    assert fields["requester"]["value"] is None


def test_mishearing_of_a_contact_is_accepted():
    fields, _ = ground_check(mk(requester=("John", 0.9)), "Jon said move it", ["John"])
    assert fields["requester"]["value"] == "John"


def test_amount_must_be_spoken():
    ok, _ = ground_check(mk(amount_mentioned=("$400", 0.8)), "he'll pay four hundred dollars")
    bad, _ = ground_check(mk(amount_mentioned=("$500", 0.8)), "he'll pay four hundred dollars")
    assert ok["amount_mentioned"]["value"] == "$400" and bad["amount_mentioned"]["value"] is None


def test_words_that_were_never_said_are_removed():
    fields, _ = ground_check(mk(change=("install new hot water system", 0.9)), "move the pipe left")
    assert fields["change"]["value"] is None


def test_invented_material_detail_is_removed():
    fields, _ = ground_check(mk(materials=("copper pipe", 0.8)), "John said we'll need some pipe")
    assert fields["materials"]["value"] is None


def test_slang_and_synonyms_ground_correctly():
    fields, _ = ground_check(mk(change=("move pipe 300mm left", 0.9), location=("Level 2", 0.9)),
                             "move the pipe three hundred mil left on the second floor")
    assert fields["change"]["value"] and fields["location"]["value"]


# ------------------------------------------------------------------ model path (mocked)

def llm_settings(tmp_path):
    return Settings(data_dir=tmp_path, extract_provider="anthropic", anthropic_api_key="test-key",
                    extract_model="test-model")


def test_model_output_is_grounded_before_it_reaches_the_record(monkeypatch, tmp_path):
    hallucinated = {
        "requester": {"value": "John Smith", "confidence": 0.95},
        "change": {"value": "move the bathroom pipe 350mm left", "confidence": 0.9},
        "location": {"value": "Level 2", "confidence": 0.9},
        "extra_time_hours": {"value": 2, "confidence": 0.9},
        "materials": {"value": "copper pipe", "confidence": 0.8},
        "amount_mentioned": {"value": "$400", "confidence": 0.8},
    }
    monkeypatch.setattr(ex, "_call_anthropic", lambda t, c, s: hallucinated)
    fields, meta = extract_fields(TX, ["John Smith"], llm_settings(tmp_path))
    assert meta["provider"] == "anthropic" and meta["model"] == "test-model" and not meta["fallback"]
    assert fields["requester"]["value"] == "John"            # surname trimmed
    assert fields["change"]["value"] is None                 # 350 was never said
    assert fields["location"]["value"] == "Level 2"
    assert fields["extra_time_hours"]["value"] == 2 and fields["extra_time_hours"]["confidence"] <= 0.7
    assert fields["materials"]["value"] is None and fields["amount_mentioned"]["value"] is None
    assert {e["field"] for e in meta["removed"]} == {"requester", "change", "materials", "amount_mentioned"}
    assert fields["location"]["source"] == "llm"
    assert "missing:change" in meta["flags"]


def test_model_failure_falls_back_to_rules(monkeypatch, tmp_path):
    def boom(*a):
        raise RuntimeError("network down")
    monkeypatch.setattr(ex, "_call_anthropic", boom)
    fields, meta = extract_fields(TX, ["John"], llm_settings(tmp_path))
    assert meta["fallback"] is True and meta["provider"] == "rules" and "network down" in meta["error"]
    assert fields["requester"]["value"] == "John"


def test_unparseable_model_output_falls_back(monkeypatch, tmp_path):
    def junk(*a):
        raise ValueError("no JSON object in model output")
    monkeypatch.setattr(ex, "_call_anthropic", junk)
    _, meta = extract_fields(TX, [], llm_settings(tmp_path))
    assert meta["fallback"] is True


def test_provider_anthropic_without_key_uses_rules(tmp_path):
    s = Settings(data_dir=tmp_path, extract_provider="anthropic", anthropic_api_key="")
    _, meta = extract_fields(TX, [], s)
    assert meta["provider"] == "rules" and meta["fallback"] is False


# ------------------------------------------------------------------ plumbing

def test_parse_json_handles_fences_and_chatter():
    assert ex._parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ex._parse_json('Sure! {"a": {"b": 2}} Hope that helps') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        ex._parse_json("no json here")


def test_normalize_handles_messy_model_output():
    raw = {
        "requester": "None", "change": {"value": "  move   pipe ", "confidence": 7},
        "location": {"value": ["Level 2", "kitchen"], "confidence": "high"},
        "extra_time_hours": {"value": "2", "confidence": 0.8},
        "materials": {"value": None, "confidence": 0.9},
        "amount_mentioned": {"value": 400, "confidence": 0.8},
    }
    f = normalize_fields(raw, "llm")
    assert f["requester"]["value"] is None
    assert f["change"]["value"] == "move pipe" and f["change"]["confidence"] == 1.0
    assert f["location"]["value"] == "Level 2, kitchen" and f["location"]["confidence"] == 0.5
    assert f["extra_time_hours"]["value"] == 2.0
    assert f["materials"]["value"] is None and f["materials"]["confidence"] == 0.0
    assert f["amount_mentioned"]["value"] == "400"
    assert normalize_fields("garbage", "llm") == blank_fields()


def test_flags_list_what_needs_review():
    f = mk(change=("move pipe", 0.5), location=("kitchen", 0.9))
    flags = compute_flags(f, "move the pipe in the kitchen please")
    assert "missing:requester" in flags and "low_confidence:change" in flags and "missing:location" not in flags
