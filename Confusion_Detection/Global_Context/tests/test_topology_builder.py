"""Numeric ground-truth clustering evaluation (claude.md Section 4).

Runs all four combinations of {HDBSCANClusterer, KMeansClusterer} x
{TfidfEmbeddingModel, SentenceTransformerEmbeddingModel} against the
14-question ground-truth fixture, scores each with Adjusted Rand Index,
and prints the full cluster assignment table. Run with `pytest -s` to
see it.
"""
from __future__ import annotations

import statistics

import pytest
from sklearn.metrics import adjusted_rand_score

from .. import _paths  # noqa: F401
from ..clustering import HDBSCANClusterer, KMeansClusterer
from ..distillation import CentroidClosestDistiller
from ..topology_builder import QuestionTopologyBuilder
from .fixtures.ground_truth_topics import OUTLIER_INDICES, QUESTIONS, TRUE_LABELS

from Similiarity_gen.embedding_models import SentenceTransformerEmbeddingModel, TfidfEmbeddingModel

CLUSTERER_FACTORIES = {
    "hdbscan": lambda: HDBSCANClusterer(min_cluster_size=3),
    "kmeans": lambda: KMeansClusterer(k_range=range(2, 8)),
}
MODEL_FACTORIES = {
    "tfidf": TfidfEmbeddingModel,
    "sentence-transformer": pytest.param(
        SentenceTransformerEmbeddingModel, marks=pytest.mark.slow
    ),
}

# Cross product built explicitly (rather than pytest's built-in
# parametrize-stacking) so each combo gets one clear id and the slow
# mark travels with the sentence-transformer half only.
COMBOS = []
for c_name, c_factory in CLUSTERER_FACTORIES.items():
    for m_name, m_factory in MODEL_FACTORIES.items():
        combo_id = f"{c_name}+{m_name}"
        if isinstance(m_factory, type):
            COMBOS.append(pytest.param(c_name, c_factory, m_factory, id=combo_id))
        else:
            # m_factory is a pytest.param wrapping the real factory —
            # unwrap it but keep its marks on our own combined param.
            COMBOS.append(
                pytest.param(c_name, c_factory, m_factory.values[0], id=combo_id, marks=m_factory.marks)
            )


def _predicted_labels_for(result, n_questions):
    """Flatten a TopologyResult back into a (n_questions,) label array,
    aligned to the original input order, for adjusted_rand_score."""
    labels = [None] * n_questions
    for topic in result.topics:
        for idx in topic.question_indices:
            labels[idx] = topic.topic_id
    assert all(l is not None for l in labels), "every question must land in exactly one topic"
    return labels


@pytest.mark.parametrize("clusterer_name,clusterer_factory,model_factory", COMBOS)
def test_ari_against_ground_truth(clusterer_name, clusterer_factory, model_factory):
    model = model_factory()
    clusterer = clusterer_factory()
    builder = QuestionTopologyBuilder(model, clusterer, CentroidClosestDistiller())

    result = builder.build(QUESTIONS)
    predicted_labels = _predicted_labels_for(result, len(QUESTIONS))

    ari = adjusted_rand_score(TRUE_LABELS, predicted_labels)

    print(f"\n=== {clusterer_name} + {model.name} ===")
    print(f"{'question':<75} true  pred  noise")
    for i, q in enumerate(QUESTIONS):
        topic = next(t for t in result.topics if i in t.question_indices)
        print(f"{q[:73]:<75} {TRUE_LABELS[i]:<5} {predicted_labels[i]:<5} {topic.is_noise}")
    print(f"Adjusted Rand Index: {ari:.4f}")
    print(f"n_topics={len(result.topics)} n_real_clusters={len(result.real_topics)} "
          f"n_noise={len(result.noise_topics)}")

    # Every representative_query must be a real member of its own
    # cluster's source_questions — the whole point of centroid-closest
    # distillation, verified rather than just claimed.
    for topic in result.topics:
        assert topic.representative_query in topic.source_questions

    # No question may be silently dropped, regardless of clusterer/model.
    all_indices = sorted(i for t in result.topics for i in t.question_indices)
    assert all_indices == list(range(len(QUESTIONS)))

    if clusterer_name == "hdbscan":
        # HDBSCAN explicitly supports noise -- the two outliers must
        # each land as their own singleton, never folded into one of
        # the 4 real topics (or into each other).
        for idx in OUTLIER_INDICES:
            topic = next(t for t in result.topics if idx in t.question_indices)
            print(f"outlier {QUESTIONS[idx]!r} -> is_noise={topic.is_noise}, size={topic.size}")
            assert topic.is_noise, (
                f"outlier question {idx} was merged into a real cluster "
                f"instead of being flagged as noise"
            )
            assert topic.size == 1
    else:
        # KMeans has no noise concept by design (claude.md Section 3) —
        # report whether it happened to isolate the outliers anyway,
        # but don't hard-fail the baseline for not having a feature it
        # was never meant to have.
        for idx in OUTLIER_INDICES:
            topic = next(t for t in result.topics if idx in t.question_indices)
            print(
                f"[kmeans baseline, informational] outlier {QUESTIONS[idx]!r} "
                f"landed in a cluster of size {topic.size}"
            )

    # Stash the ARI for the cross-combo summary assertion below.
    _ARI_RESULTS[f"{clusterer_name}+{model_factory.__name__ if hasattr(model_factory, '__name__') else model.name}"] = ari


_ARI_RESULTS: dict = {}


def test_at_least_one_combo_clears_a_useful_ari_bar():
    # Regression baseline across combos, not per-combo: the whole point
    # of running all four is to compare them, so a weak baseline (e.g.
    # kmeans+tfidf on 14 tiny sentences) shouldn't fail the suite on its
    # own. But *something* must actually work, or the module is broken.
    if not _ARI_RESULTS:
        pytest.skip("test_ari_against_ground_truth must run first to populate ARI results")
    best_combo = max(_ARI_RESULTS, key=_ARI_RESULTS.get)
    print(f"\nARI summary: {_ARI_RESULTS}")
    print(f"Best combo: {best_combo} (ARI={_ARI_RESULTS[best_combo]:.4f})")
    assert _ARI_RESULTS[best_combo] >= 0.6, (
        f"even the best combo only reached ARI={_ARI_RESULTS[best_combo]:.4f} "
        f"— investigate before loosening this bar"
    )
