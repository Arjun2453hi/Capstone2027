"""TopicCluster / TopologyResult — the output contract for Step 2.

Why source_questions + question_indices are kept in full (never
collapsed to just the representative query): Step 5's severity scoring
needs "this gap is backed by N student questions", and question_indices
is what lets a later stage trace a topic back to the exact original
questions if it ever needs to re-inspect them (e.g. to show a human the
raw phrasings behind a flagged gap).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class TopicCluster:
    topic_id: int
    representative_query: str
    source_questions: List[str]
    question_indices: List[int]
    is_noise: bool = False

    def __post_init__(self):
        if len(self.source_questions) != len(self.question_indices):
            raise ValueError(
                "source_questions and question_indices must stay in "
                "lockstep — every source question must be traceable "
                "back to its position in the original input list."
            )
        if self.is_noise and len(self.source_questions) != 1:
            # Noise topics are singletons by construction (one unclustered
            # question each) — catching a mismatch here early is cheaper
            # than debugging a downstream severity score that silently
            # assumed size==1 for every is_noise topic.
            raise ValueError(
                f"topic {self.topic_id}: is_noise=True but has "
                f"{len(self.source_questions)} source questions (expected 1)"
            )

    @property
    def size(self) -> int:
        return len(self.source_questions)


@dataclass
class TopologyResult:
    topics: List[TopicCluster]
    n_input_questions: int
    model_name: str
    clusterer_name: str

    @property
    def real_topics(self) -> List[TopicCluster]:
        """Topics backed by an actual cluster (excludes singleton noise)."""
        return [t for t in self.topics if not t.is_noise]

    @property
    def noise_topics(self) -> List[TopicCluster]:
        return [t for t in self.topics if t.is_noise]

    def summary(self) -> dict:
        sizes = sorted(t.size for t in self.real_topics)
        return {
            "n_input_questions": self.n_input_questions,
            "n_topics_total": len(self.topics),
            "n_real_clusters": len(self.real_topics),
            "n_noise_singletons": len(self.noise_topics),
            "real_cluster_sizes": {
                "min": sizes[0] if sizes else None,
                "median": sizes[len(sizes) // 2] if sizes else None,
                "max": sizes[-1] if sizes else None,
            },
            "model_name": self.model_name,
            "clusterer_name": self.clusterer_name,
        }
