"""QuestionTopologyBuilder — the only file that imports all three
interfaces (EmbeddingModel, Clusterer, Distiller) together.

Depends on the interfaces, never a concrete class directly: swapping
any one of the three (a different embedding model, HDBSCAN <-> KMeans,
a future LLM-paraphrase distiller) means changing the constructor call
site, not this file.
"""
from __future__ import annotations

from collections import defaultdict
from typing import List

import numpy as np

from . import _paths  # noqa: F401  (side effect: puts Similiarity_gen on sys.path)
from .clustering import Clusterer
from .distillation import Distiller
from .schema import TopicCluster, TopologyResult

from Similiarity_gen.embedding_models import EmbeddingModel


def _l2_normalize(embeddings: np.ndarray) -> np.ndarray:
    """Normalized once here so the clusterer and the distiller agree on
    the same notion of "close" (cosine, via normalized Euclidean) — see
    clustering.py's module docstring for the math."""
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return embeddings / norms


class QuestionTopologyBuilder:
    def __init__(self, model: EmbeddingModel, clusterer: Clusterer, distiller: Distiller):
        self.model = model
        self.clusterer = clusterer
        self.distiller = distiller

    def build(self, questions: List[str]) -> TopologyResult:
        if not questions:
            return TopologyResult(
                topics=[],
                n_input_questions=0,
                model_name=self.model.name,
                clusterer_name=self.clusterer.name,
            )

        self.model.fit(questions)  # no-op for models without corpus-dependent state
        embeddings = _l2_normalize(np.asarray(self.model.embed(questions)))
        labels = self.clusterer.fit_predict(embeddings)

        label_to_indices: dict = defaultdict(list)
        for i, label in enumerate(labels):
            if label == -1:
                continue  # handled below, as singleton topics — never dropped
            label_to_indices[label].append(i)

        topics: List[TopicCluster] = []
        next_topic_id = 0

        # Real clusters first, in a stable order (sorted by original
        # label) so topic_id assignment is deterministic run to run.
        for label in sorted(label_to_indices):
            indices = label_to_indices[label]
            cluster_questions = [questions[i] for i in indices]
            cluster_embeddings = embeddings[indices]
            representative = self.distiller.distill(cluster_questions, cluster_embeddings)
            topics.append(
                TopicCluster(
                    topic_id=next_topic_id,
                    representative_query=representative,
                    source_questions=cluster_questions,
                    question_indices=list(indices),
                    is_noise=False,
                )
            )
            next_topic_id += 1

        # Every -1 becomes its own singleton topic. Critical rule (claude.md
        # Section 3): an unclustered question is still a real signal for
        # gap detection — it must never be silently filtered out.
        for i, label in enumerate(labels):
            if label != -1:
                continue
            topics.append(
                TopicCluster(
                    topic_id=next_topic_id,
                    representative_query=questions[i],
                    source_questions=[questions[i]],
                    question_indices=[i],
                    is_noise=True,
                )
            )
            next_topic_id += 1

        return TopologyResult(
            topics=topics,
            n_input_questions=len(questions),
            model_name=self.model.name,
            clusterer_name=self.clusterer.name,
        )
