"""Generic CLI driver for Global_Context.

Same auto-detection posture as Similiarity_gen/run_retrieval.py: given a
directory, find the questions file (a .txt, one per line, or a JSON
list of strings) by content, not by hardcoding a filename. Defaults to
looking in the sibling Similiarity_gen/ folder, since that's where the
real question set actually lives — this folder never touches deck JSON
so it has no data files of its own.

Usage:
    python -m Global_Context.run_topology                              # real data, HDBSCAN default
    python -m Global_Context.run_topology --clusterer kmeans           # compare against KMeans
    python -m Global_Context.run_topology --sweep-min-cluster-size 2 3 5 8
    python -m Global_Context.run_topology --questions path/to/q.txt --model tfidf
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import List, Optional

from . import _paths  # noqa: F401
from .clustering import Clusterer, HDBSCANClusterer, KMeansClusterer
from .distillation import CentroidClosestDistiller
from .schema import TopologyResult
from .topology_builder import QuestionTopologyBuilder

from Similiarity_gen.embedding_models import SentenceTransformerEmbeddingModel, TfidfEmbeddingModel

DEFAULT_QUESTIONS_DIR = Path(__file__).resolve().parent.parent / "Similiarity_gen"


def find_questions_file(directory: Path) -> Path:
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
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_model(name: str):
    if name == "tfidf":
        return TfidfEmbeddingModel()
    if name == "sentence-transformer":
        return SentenceTransformerEmbeddingModel()
    raise ValueError(f"unknown model {name!r}")


def build_clusterer(name: str, min_cluster_size: int, k_range) -> Clusterer:
    if name == "hdbscan":
        return HDBSCANClusterer(min_cluster_size=min_cluster_size)
    if name == "kmeans":
        return KMeansClusterer(k_range=k_range)
    raise ValueError(f"unknown clusterer {name!r}")


def cluster_size_distribution(result: TopologyResult) -> dict:
    sizes = sorted(t.size for t in result.real_topics)
    return {
        "n_real_clusters": len(sizes),
        "n_noise_singletons": len(result.noise_topics),
        "min": sizes[0] if sizes else None,
        "median": statistics.median(sizes) if sizes else None,
        "mean": round(statistics.fmean(sizes), 2) if sizes else None,
        "max": sizes[-1] if sizes else None,
    }


def print_examples(result: TopologyResult, n: int = 6, n_sources: int = 3) -> None:
    real = sorted(result.real_topics, key=lambda t: -t.size)
    print(f"\n  {min(n, len(real))} example clusters (largest first):")
    for topic in real[:n]:
        print(f"  - topic {topic.topic_id} (size={topic.size}): {topic.representative_query!r}")
        for q in topic.source_questions[:n_sources]:
            print(f"      - {q}")


def run(
    questions_path: Path,
    model_name: str,
    clusterer_name: str,
    min_cluster_size: int,
    k_range_str: str,
    sweep_min_cluster_size: Optional[List[int]],
    out_path: Optional[Path],
) -> dict:
    questions = load_questions(questions_path)
    print(f"Questions: {questions_path} ({len(questions)} questions)\n")

    k_parts = [int(x) for x in k_range_str.split(":")]
    k_range = range(*k_parts)  # "lo:hi" or "lo:hi:step"

    model = build_model(model_name)
    clusterer = build_clusterer(clusterer_name, min_cluster_size, k_range)
    builder = QuestionTopologyBuilder(model, clusterer, CentroidClosestDistiller())
    result = builder.build(questions)

    stats = cluster_size_distribution(result)
    print(f"-- {clusterer.name} + {model.name} --")
    print(f"  {stats}")
    print_examples(result)

    report = {
        "questions_path": str(questions_path),
        "n_questions": len(questions),
        "model_name": model.name,
        "clusterer_name": clusterer.name,
        "stats": stats,
        "topics": [
            {
                "topic_id": t.topic_id,
                "representative_query": t.representative_query,
                "is_noise": t.is_noise,
                "size": t.size,
                "source_questions": t.source_questions,
                "question_indices": t.question_indices,
            }
            for t in result.topics
        ],
    }

    if sweep_min_cluster_size and clusterer_name == "hdbscan":
        print("\n-- min_cluster_size sweep (HDBSCAN) --")
        sweep_report = {}
        for mcs in sweep_min_cluster_size:
            sweep_clusterer = HDBSCANClusterer(min_cluster_size=mcs)
            sweep_result = builder_with(model, sweep_clusterer).build(questions)
            sweep_stats = cluster_size_distribution(sweep_result)
            print(f"  min_cluster_size={mcs}: {sweep_stats}")
            sweep_report[str(mcs)] = sweep_stats
        report["min_cluster_size_sweep"] = sweep_report

    if out_path:
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nFull report written to: {out_path}")

    return report


def builder_with(model, clusterer) -> QuestionTopologyBuilder:
    # Re-embedding per sweep value would be wasteful and, worse, is
    # unnecessary: the embeddings don't depend on min_cluster_size at
    # all. But QuestionTopologyBuilder.build() re-embeds every call
    # today (it owns the model), so this helper exists as the one place
    # to optimize later (e.g. accept precomputed embeddings) without
    # touching call sites. Left simple for now since a sweep over a few
    # values is still fast for TF-IDF/sentence-transformer at 800 rows.
    return QuestionTopologyBuilder(model, clusterer, CentroidClosestDistiller())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_QUESTIONS_DIR)
    parser.add_argument("--questions", type=Path, default=None)
    parser.add_argument("--model", choices=["tfidf", "sentence-transformer"], default="sentence-transformer")
    parser.add_argument("--clusterer", choices=["hdbscan", "kmeans"], default="hdbscan")
    parser.add_argument("--min-cluster-size", type=int, default=3)
    parser.add_argument("--k-range", type=str, default="2:60", help="lo:hi[:step], exclusive of hi, for KMeans")
    parser.add_argument("--sweep-min-cluster-size", type=int, nargs="*", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    questions_path = args.questions or find_questions_file(args.dir)
    run(
        questions_path=questions_path,
        model_name=args.model,
        clusterer_name=args.clusterer,
        min_cluster_size=args.min_cluster_size,
        k_range_str=args.k_range,
        sweep_min_cluster_size=args.sweep_min_cluster_size,
        out_path=args.out,
    )


if __name__ == "__main__":
    main()
