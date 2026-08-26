"""Generic CLI driver for Similarity_gen.

The point of this file: nothing else in this package should need to
know a concrete file path. Given a directory, it figures out which file
is "the deck" (a JSON matching the Step-1 shape) and which is "the
questions" (a .txt with one question per line, or a JSON list of
strings) by inspecting content, not by hardcoding a filename — so
dropping this package next to a *different* deck+questions pair and
rerunning `python run_retrieval.py` just works.

Usage:
    python run_retrieval.py                       # auto-detect everything in this folder
    python run_retrieval.py --dir path/to/folder   # auto-detect in another folder
    python run_retrieval.py --deck d.json --questions q.txt --out report.json
    python run_retrieval.py --model tfidf          # skip sentence-transformers (fast, offline)
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import _paths  # noqa: F401
from .cache import EmbeddingCache
from .embedding_models import SentenceTransformerEmbeddingModel, TfidfEmbeddingModel
from .retriever import QuestionSlideRetriever
from .slide_index import SlideIndex

from gap_detection.parsing.storage import load_deck_json


# ─────────────────────────────────────────────
# INPUT AUTO-DETECTION
# ─────────────────────────────────────────────
def _looks_like_deck_json(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return False
    return (
        isinstance(data, dict)
        and "slides" in data
        and isinstance(data["slides"], list)
        and (len(data["slides"]) == 0 or "slide_id" in data["slides"][0])
    )


def find_deck_json(directory: Path) -> Path:
    candidates = sorted(directory.glob("*.json"))
    for path in candidates:
        if _looks_like_deck_json(path):
            return path
    raise FileNotFoundError(
        f"No deck JSON (a {{'slides': [...]}} file) found in {directory}. "
        f"Pass --deck explicitly."
    )


def find_questions_file(directory: Path) -> Path:
    # Prefer an explicit "questions" name, then any other .txt, then a
    # plain JSON list-of-strings as a fallback format.
    txt_candidates = sorted(directory.glob("*.txt"))
    named = [p for p in txt_candidates if "question" in p.name.lower()]
    if named:
        return named[0]
    if txt_candidates:
        return txt_candidates[0]

    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if isinstance(data, list) and all(isinstance(x, str) for x in data):
            return path

    raise FileNotFoundError(
        f"No questions file (.txt, one per line, or a JSON list of "
        f"strings) found in {directory}. Pass --questions explicitly."
    )


def load_questions(path: Path) -> List[str]:
    if path.suffix.lower() == ".json":
        return [q.strip() for q in json.loads(path.read_text(encoding="utf-8")) if q.strip()]
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip()]


# ─────────────────────────────────────────────
# MODEL RESOLUTION
# ─────────────────────────────────────────────
def build_models(selection: str) -> dict:
    models = {}
    if selection in ("tfidf", "both"):
        models["tfidf"] = TfidfEmbeddingModel()
    if selection in ("sentence-transformer", "both"):
        try:
            models["sentence-transformer"] = SentenceTransformerEmbeddingModel()
        except Exception as e:  # e.g. package not installed, no download available
            print(
                f"[warn] skipping sentence-transformer model ({e.__class__.__name__}: {e})",
                file=sys.stderr,
            )
    if not models:
        raise RuntimeError(f"No usable embedding model for selection={selection!r}")
    return models


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def run(
    directory: Path,
    deck_path: Optional[Path],
    questions_path: Optional[Path],
    model_selection: str,
    top_k: int,
    out_path: Optional[Path],
    low_confidence_threshold: float,
) -> dict:
    deck_path = deck_path or find_deck_json(directory)
    questions_path = questions_path or find_questions_file(directory)

    print(f"Deck:      {deck_path}")
    print(f"Questions: {questions_path}")

    deck = load_deck_json(deck_path)
    questions = load_questions(questions_path)
    n_non_empty = sum(1 for s in deck.slides if not s.is_empty)
    print(
        f"Loaded {len(deck.slides)} slides ({n_non_empty} non-empty) "
        f"and {len(questions)} questions.\n"
    )

    cache = EmbeddingCache()
    models = build_models(model_selection)

    report = {
        "deck_path": str(deck_path),
        "questions_path": str(questions_path),
        "n_slides_total": len(deck.slides),
        "n_slides_indexed": n_non_empty,
        "n_questions": len(questions),
        "top_k": top_k,
        "models": {},
    }

    for model_key, model in models.items():
        t0 = time.time()
        index = SlideIndex.build(deck, model, cache=cache)
        retriever = QuestionSlideRetriever(index)
        batch_results = retriever.retrieve_batch(questions, top_k=top_k)
        elapsed = time.time() - t0

        top1_scores = [r[0].score for r in batch_results if r]
        low_conf = [
            {"question": q, "top1_score": r[0].score, "slide_id": r[0].slide_id}
            for q, r in zip(questions, batch_results)
            if r and r[0].score < low_confidence_threshold
        ]

        per_question = [
            {
                "question": q,
                "results": [
                    {
                        "slide_id": res.slide_id,
                        "slide_number": res.slide_number,
                        "score": round(res.score, 4),
                    }
                    for res in r
                ],
            }
            for q, r in zip(questions, batch_results)
        ]

        stats = {
            "elapsed_seconds": round(elapsed, 2),
            "n_indexed_slides": len(index),
            "mean_top1_score": round(statistics.fmean(top1_scores), 4) if top1_scores else None,
            "median_top1_score": round(statistics.median(top1_scores), 4) if top1_scores else None,
            "min_top1_score": round(min(top1_scores), 4) if top1_scores else None,
            "max_top1_score": round(max(top1_scores), 4) if top1_scores else None,
            "n_low_confidence": len(low_conf),
            "low_confidence_threshold": low_confidence_threshold,
        }

        print(f"-- {model_key} ({model.name}) -- {elapsed:.1f}s --")
        print(
            f"  top-1 score: mean={stats['mean_top1_score']} "
            f"median={stats['median_top1_score']} "
            f"min={stats['min_top1_score']} max={stats['max_top1_score']}"
        )
        print(
            f"  {stats['n_low_confidence']}/{len(questions)} questions "
            f"scored below {low_confidence_threshold} (candidate gaps for Step 4)"
        )
        print("  sample:")
        for q, r in list(zip(questions, batch_results))[:5]:
            top = r[0] if r else None
            top_desc = (
                f"slide {top.slide_id} (#{top.slide_number}, score={top.score:.4f})"
                if top
                else "no match"
            )
            print(f"    Q: {q!r} -> {top_desc}")
        print()

        report["models"][model_key] = {
            "model_name": model.name,
            "stats": stats,
            "low_confidence_questions": low_conf,
            "results": per_question,
        }

    out_path = out_path or (directory / "retrieval_report.json")
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Full report written to: {out_path}")

    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--deck", type=Path, default=None)
    parser.add_argument("--questions", type=Path, default=None)
    parser.add_argument(
        "--model", choices=["tfidf", "sentence-transformer", "both"], default="both"
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--low-confidence-threshold", type=float, default=0.15)
    args = parser.parse_args()

    run(
        directory=args.dir,
        deck_path=args.deck,
        questions_path=args.questions,
        model_selection=args.model,
        top_k=args.top_k,
        out_path=args.out,
        low_confidence_threshold=args.low_confidence_threshold,
    )


if __name__ == "__main__":
    main()
