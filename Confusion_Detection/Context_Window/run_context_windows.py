"""Generic CLI driver for Context_Window.

Same auto-detection posture as Similiarity_gen/run_retrieval.py and
Global_Context/run_topology.py: given a directory, find the deck JSON
and questions file by content, not filename. Builds the real pipeline
end to end — SlideIndex -> QuestionSlideRetriever -> QuestionTopologyBuilder
-> ContextWindowBuilder — and writes one JSON report plus a console
summary.

Usage:
    python -m Context_Window.run_context_windows
    python -m Context_Window.run_context_windows --max-context-chars 2000
    python -m Context_Window.run_context_windows --dir path/to/folder --out report.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Optional

from . import _paths  # noqa: F401
from .builder import ContextWindowBuilder
from .window_strategy import FixedRadiusWindow

from Global_Context.clustering import HDBSCANClusterer
from Global_Context.distillation import CentroidClosestDistiller
from Global_Context.topology_builder import QuestionTopologyBuilder
from Similiarity_gen.embedding_models import SentenceTransformerEmbeddingModel
from Similiarity_gen.retriever import QuestionSlideRetriever
from Similiarity_gen.slide_index import SlideIndex
from gap_detection.parsing.storage import load_deck_json

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "Similiarity_gen"


def find_deck_json(directory: Path) -> Path:
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if isinstance(data, dict) and "slides" in data:
            return path
    raise FileNotFoundError(f"No deck JSON found in {directory}")


def find_questions_file(directory: Path) -> Path:
    txt_candidates = sorted(directory.glob("*.txt"))
    named = [p for p in txt_candidates if "question" in p.name.lower()]
    if named:
        return named[0]
    if txt_candidates:
        return txt_candidates[0]
    raise FileNotFoundError(f"No questions .txt found in {directory}")


def load_questions(path: Path):
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(
    directory: Path,
    deck_path: Optional[Path],
    questions_path: Optional[Path],
    min_cluster_size: int,
    radius: int,
    max_context_chars: int,
    out_path: Optional[Path],
) -> dict:
    deck_path = deck_path or find_deck_json(directory)
    questions_path = questions_path or find_questions_file(directory)

    deck = load_deck_json(deck_path)
    questions = load_questions(questions_path)
    print(f"Deck:      {deck_path} ({len(deck.slides)} slides)")
    print(f"Questions: {questions_path} ({len(questions)} questions)\n")

    model = SentenceTransformerEmbeddingModel()  # one instance, reused for slides + questions
    index = SlideIndex.build(deck, model)
    retriever = QuestionSlideRetriever(index)

    topology = QuestionTopologyBuilder(
        model, HDBSCANClusterer(min_cluster_size=min_cluster_size), CentroidClosestDistiller()
    ).build(questions)

    strategy = FixedRadiusWindow(radius=radius)
    unbudgeted = ContextWindowBuilder(retriever, deck, strategy, max_context_chars=10**9)
    budgeted = ContextWindowBuilder(retriever, deck, strategy, max_context_chars=max_context_chars)

    bundles = budgeted.build_all(topology.topics)
    full_bundles = unbudgeted.build_all(topology.topics)  # cheap: same retrieval, no re-embedding

    n_slides = [len(b.window_slide_ids) for b in bundles]
    n_chars = [len(b.window_text) for b in bundles]
    n_truncated = sum(1 for b, fb in zip(bundles, full_bundles) if len(fb.window_text) > max_context_chars)

    stats = {
        "n_topics": len(bundles),
        "n_real_clusters": len(topology.real_topics),
        "n_noise_singletons": len(topology.noise_topics),
        "max_context_chars": max_context_chars,
        "window_size_slides": {"min": min(n_slides), "mean": round(statistics.fmean(n_slides), 2), "max": max(n_slides)},
        "window_size_chars": {"min": min(n_chars), "mean": round(statistics.fmean(n_chars), 2), "max": max(n_chars)},
        "n_truncated": n_truncated,
    }

    print(f"-- {len(bundles)} context bundles built --")
    print(f"  {stats}")

    real_bundles = [b for b in bundles if not next(t for t in topology.topics if t.topic_id == b.topic_id).is_noise]
    real_bundles.sort(key=lambda b: -b.cluster_size)
    print("\n  4 example bundles (largest clusters first):")
    for b in real_bundles[:4]:
        print(f"  - topic {b.topic_id} (cluster_size={b.cluster_size}, anchor_slide_id={b.anchor_slide_id}, "
              f"window_slide_ids={b.window_slide_ids}, window_chars={len(b.window_text)})")
        print(f"      query: {b.representative_query!r}")

    report = {
        "deck_path": str(deck_path),
        "questions_path": str(questions_path),
        "min_cluster_size": min_cluster_size,
        "radius": radius,
        "stats": stats,
        "bundles": [
            {
                "topic_id": b.topic_id,
                "representative_query": b.representative_query,
                "anchor_slide_id": b.anchor_slide_id,
                "window_slide_ids": b.window_slide_ids,
                "window_text": b.window_text,
                "source_questions": b.source_questions,
                "cluster_size": b.cluster_size,
                "is_noise": next(t for t in topology.topics if t.topic_id == b.topic_id).is_noise,
            }
            for b in bundles
        ],
    }

    out_path = out_path or (Path(__file__).resolve().parent / "context_windows_report.json")
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull report written to: {out_path}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--deck", type=Path, default=None)
    parser.add_argument("--questions", type=Path, default=None)
    parser.add_argument("--min-cluster-size", type=int, default=3)
    parser.add_argument("--radius", type=int, default=1)
    parser.add_argument("--max-context-chars", type=int, default=4000)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    run(
        directory=args.dir,
        deck_path=args.deck,
        questions_path=args.questions,
        min_cluster_size=args.min_cluster_size,
        radius=args.radius,
        max_context_chars=args.max_context_chars,
        out_path=args.out,
    )


if __name__ == "__main__":
    main()
