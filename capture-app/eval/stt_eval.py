#!/usr/bin/env python3
"""Measure speech-to-text on YOUR clips (the part of the pipeline nobody can test from a laptop).

1. Record clips on a phone, ideally with real or played-back site noise (drill, saw, traffic, a fan).
   Say sentences like the ones in eval/golden_sentences.json, with numbers and names.
2. Save each as eval/audio/NAME.webm|m4a|mp3|wav|ogg  and write exactly what you said in eval/audio/NAME.txt
3. OPENAI_API_KEY=... python eval/stt_eval.py --names "John,Sarah,Dave"

Per clip it reports word error rate (WER) and, more usefully for this product, whether every NUMBER and
NAME you said survived transcription - a 10% WER that drops "300" is worse than a 25% WER that keeps it.
Label the result honestly in the deck (e.g. "12 clips, 1 speaker, played-back drill noise at ~70 dB").
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import numbers as nm  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.stt import SttError, transcribe  # noqa: E402

AUDIO_EXT = {".webm", ".m4a", ".mp4", ".mp3", ".wav", ".ogg"}


def words(text: str) -> list[str]:
    text = nm.words_to_numbers(text).lower().replace("’", "'")
    text = re.sub(r"\b(mils?|millimet(?:re|er)s?)\b", "mm", text)
    return re.findall(r"[a-z0-9']+", text)


def wer(ref: list[str], hyp: list[str]) -> float:
    """Word error rate = word-level edit distance / reference length."""
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def survived_numbers(ref: str, hyp: str) -> tuple[int, int]:
    want, got = nm.numbers_in(ref), nm.numbers_in(hyp)
    return sum(1 for n in want if n in got), len(want)


def survived_names(ref: str, hyp: str, names: list[str]) -> tuple[int, int]:
    ref_w, hyp_w = set(words(ref)), words(hyp)
    said = [n.lower() for n in names if n.lower() in ref_w]
    ok = sum(1 for n in said if any(SequenceMatcher(None, n, h).ratio() >= 0.8 for h in hyp_w))
    return ok, len(said)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(Path(__file__).with_name("audio")))
    ap.add_argument("--names", default="", help="comma-separated names you said in the clips")
    ap.add_argument("--hint-contacts", default=None,
                    help="contact names passed to the STT as a spelling hint (default: same as --names)")
    args = ap.parse_args()

    names = [n.strip() for n in args.names.split(",") if n.strip()]
    hint = [n.strip() for n in (args.hint_contacts if args.hint_contacts is not None else args.names).split(",") if n.strip()]
    settings = load_settings()
    if settings.stt_provider != "openai":
        sys.exit("Set OPENAI_API_KEY (STT_PROVIDER=openai) to run this.")

    clips = sorted(p for p in Path(args.dir).glob("*") if p.suffix.lower() in AUDIO_EXT and p.with_suffix(".txt").exists())
    if not clips:
        sys.exit(f"No clips found in {args.dir} (each needs NAME.<audio ext> and NAME.txt).")

    rows = []
    print(f"{'clip':28} {'WER':>5} {'numbers':>8} {'names':>6}  transcript")
    for p in clips:
        ref = p.with_suffix(".txt").read_text(encoding="utf-8").strip()
        try:
            hyp, meta = transcribe(p, settings, hint)
        except SttError as e:
            print(f"{p.name:28} ERROR {e}")
            continue
        w = wer(words(ref), words(hyp))
        n_ok, n_all = survived_numbers(ref, hyp)
        m_ok, m_all = survived_names(ref, hyp, names)
        rows.append({"clip": p.name, "wer": round(w, 3), "numbers_ok": n_ok, "numbers_total": n_all,
                     "names_ok": m_ok, "names_total": m_all, "reference": ref, "hypothesis": hyp,
                     "latency_ms": meta["latency_ms"], "prompt_echo": meta["prompt_echo"]})
        print(f"{p.name:28} {w:5.0%} {n_ok}/{n_all:<6} {m_ok}/{m_all:<4}  {hyp[:70]}")

    if rows:
        all_nums = sum(r["numbers_total"] for r in rows)
        summary = {
            "clips": len(rows), "model": settings.stt_model,
            "median_wer": round(statistics.median(r["wer"] for r in rows), 3),
            "numbers_survived": f"{sum(r['numbers_ok'] for r in rows)}/{all_nums}",
            "names_survived": f"{sum(r['names_ok'] for r in rows)}/{sum(r['names_total'] for r in rows)}",
            "median_latency_ms": int(statistics.median(r["latency_ms"] for r in rows)),
        }
        print("\n" + json.dumps(summary, indent=2))
        Path(__file__).with_name("stt_results.json").write_text(
            json.dumps({"summary": summary, "clips": rows}, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
