#!/usr/bin/env python3
"""Score the structuring step on eval/golden_sentences.json.

    python eval/run_eval.py --provider rules                  # offline baseline (regex fallback)
    python eval/run_eval.py --provider openai [--model ...]   # the model the app uses; reads capture-app/.env
    python eval/run_eval.py --provider auto                   # whatever .env / the environment configures
    ANTHROPIC_API_KEY=... python eval/run_eval.py --provider anthropic [--model claude-haiku-5-5]

The OpenAI run makes one drafting call per sentence (15 calls) with the key in .env.

What it counts, per field:
  correct       value matches what a human would write (or correctly left empty)
  wrong         a value was returned but it does not match
  missed        expected a value, got nothing  (safe failure: the worker fills it in)
  hallucinated  expected NOTHING (speaker did not say it) but a value was returned  (the failure we care about)
The 'removed by grounding' column is how many model values the code-level check deleted before they reached the record.

The sentences are synthetic. A good score here says the pipeline behaves on 15 invented sentences;
it does NOT say how it behaves on real site audio. Run the STT noise test (eval/stt_eval.py) for that.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import numbers as nm  # noqa: E402
from app.config import Settings, load_settings  # noqa: E402
from app.extract import extract_fields  # noqa: E402
from app.schemas import FIELD_NAMES  # noqa: E402

GOLDEN = Path(__file__).with_name("golden_sentences.json")


def norm(text) -> str:
    s = nm.words_to_numbers(str(text)).lower()
    s = re.sub(r"\b(mils?|millimet(?:re|er)s?)\b", "mm", s)
    s = re.sub(r"(\d)([a-z])", r"\1 \2", s)
    s = re.sub(r"([a-z])(\d)", r"\1 \2", s)
    s = re.sub(r"[^a-z0-9. ]+", " ", s)
    return " ".join(s.split())


def has_keyword(norm_value: str, keyword: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(norm(keyword))}(?![a-z0-9])", norm_value) is not None


def strip_the(s: str) -> str:
    return re.sub(r"^the ", "", s)


def score_field(name: str, expected, got) -> str:
    """-> 'correct' | 'wrong' | 'missed' | 'hallucinated'"""
    value = got["value"]
    if expected is None:
        return "correct" if value is None else "hallucinated"
    if value is None:
        return "missed"
    if name == "extra_time_hours":
        return "correct" if abs(float(value) - float(expected)) <= 0.06 else "wrong"
    if name == "requester":
        return "correct" if strip_the(norm(value)) == strip_the(norm(expected)) else "wrong"
    nv = norm(value)
    return "correct" if all(has_keyword(nv, k) for k in expected) else "wrong"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from capture-app/.env. Real environment variables win; nothing is printed."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_settings(provider: str, model: str | None, env=None) -> Settings:
    tmp = Path(tempfile.mkdtemp(prefix="eval-"))
    if provider == "rules":
        return Settings(data_dir=tmp, extract_provider="rules")
    e = dict(os.environ if env is None else env)
    e["STT_PROVIDER"] = "mock"  # the eval never transcribes, so speech-to-text settings must not block it
    if provider in ("openai", "anthropic"):
        e["EXTRACT_PROVIDER"] = provider
    if model:
        e["EXTRACT_MODEL"] = model
    try:
        base = load_settings(e)
    except ValueError as err:  # e.g. no key, or no drafting model
        sys.exit(f"Check capture-app/.env (or pass --model): {err}")
    if provider == "anthropic":
        if not base.anthropic_api_key:
            sys.exit("Set ANTHROPIC_API_KEY to evaluate the model.")
        return Settings(data_dir=tmp, extract_provider="anthropic", anthropic_api_key=base.anthropic_api_key,
                        extract_model=model or base.extract_model)
    if provider == "openai":
        if not base.openai_api_key:
            sys.exit("Set OPENAI_API_KEY (in capture-app/.env or the environment) to evaluate the OpenAI model.")
        if not (model or base.extract_model):
            sys.exit("Set EXTRACT_MODEL in capture-app/.env, or pass --model.")
        return Settings(data_dir=tmp, extract_provider="openai", openai_api_key=base.openai_api_key,
                        extract_model=model or base.extract_model)
    return Settings(data_dir=tmp, extract_provider=base.extract_provider, anthropic_api_key=base.anthropic_api_key,
                    openai_api_key=base.openai_api_key, extract_model=model or base.extract_model)


def run(provider: str = "rules", model: str | None = None, only=None, verbose: bool = False, golden: Path = GOLDEN):
    settings = build_settings(provider, model)
    cases = json.loads(golden.read_text(encoding="utf-8"))["cases"]
    if only:
        cases = [c for c in cases if c["id"] in only]
    totals = {k: 0 for k in ("correct", "wrong", "missed", "hallucinated")}
    per_field = {n: {"correct": 0, "total": 0} for n in FIELD_NAMES}
    results, removed_total, fallbacks = [], 0, 0

    for case in cases:
        fields, meta = extract_fields(case["transcript"], case.get("contacts", []), settings)
        removed_total += len(meta["removed"])
        fallbacks += 1 if meta["fallback"] else 0
        row = {"id": case["id"], "tags": case.get("tags", []), "fields": {}, "removed": meta["removed"],
               "provider": meta["provider"], "error": meta["error"]}
        for name in FIELD_NAMES:
            verdict = score_field(name, case["expected"].get(name), fields[name])
            totals[verdict] += 1
            per_field[name]["total"] += 1
            per_field[name]["correct"] += verdict == "correct"
            row["fields"][name] = {"verdict": verdict, "got": fields[name]["value"],
                                   "confidence": fields[name]["confidence"], "expected": case["expected"].get(name)}
        row["all_correct"] = all(f["verdict"] == "correct" for f in row["fields"].values())
        results.append(row)

    n_cells = sum(totals.values()) or 1
    summary = {
        "provider": provider, "model": settings.extract_model if provider != "rules" else None,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases": len(cases), "cases_all_correct": sum(r["all_correct"] for r in results),
        "field_accuracy": round(totals["correct"] / n_cells, 3), **totals,
        "removed_by_grounding": removed_total, "llm_fallbacks": fallbacks,
        "per_field_accuracy": {n: round(v["correct"] / v["total"], 3) if v["total"] else None for n, v in per_field.items()},
    }
    return summary, results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["rules", "openai", "anthropic", "auto"], default="rules")
    ap.add_argument("--model")
    ap.add_argument("--only", help="comma-separated case ids, e.g. g01,g07")
    ap.add_argument("--verbose", "-v", action="store_true", help="show every wrong / missed / hallucinated field")
    args = ap.parse_args()
    if args.provider != "rules":
        load_dotenv()

    summary, results = run(args.provider, args.model, set(args.only.split(",")) if args.only else None, args.verbose)

    print(f"\nprovider={summary['provider']} model={summary['model']}  ({summary['cases']} synthetic sentences)")
    print(f"{'case':5} {'tags':38} correct/6  notes")
    for r in results:
        good = sum(f["verdict"] == "correct" for f in r["fields"].values())
        bad = [f"{n}:{f['verdict']}" for n, f in r["fields"].items() if f["verdict"] != "correct"]
        print(f"{r['id']:5} {','.join(r['tags'])[:38]:38} {good}/6      {' '.join(bad)}")
        if args.verbose:
            for n, f in r["fields"].items():
                if f["verdict"] != "correct":
                    print(f"        {n}: expected {f['expected']!r}, got {f['got']!r} (conf {f['confidence']})")
            for rm in r["removed"]:
                print(f"        grounding {rm['action']} {rm['field']}: {rm['reason']} (was {rm['was']!r})")
    print("\nSummary")
    for k in ("cases_all_correct", "field_accuracy", "correct", "wrong", "missed", "hallucinated",
              "removed_by_grounding", "llm_fallbacks"):
        print(f"  {k:22} {summary[k]}")
    print("  per field:", ", ".join(f"{k}={v}" for k, v in summary["per_field_accuracy"].items()))

    out = Path(__file__).with_name("results.json")
    out.write_text(json.dumps({"summary": summary, "cases": results}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
