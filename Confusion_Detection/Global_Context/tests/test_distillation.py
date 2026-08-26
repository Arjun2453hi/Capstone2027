"""Unit tests for CentroidClosestDistiller."""
from __future__ import annotations

import numpy as np

from ..distillation import CentroidClosestDistiller


def test_singleton_cluster_returns_its_only_question():
    distiller = CentroidClosestDistiller()
    result = distiller.distill(["only question"], np.array([[1.0, 0.0]]))
    assert result == "only question"


def test_picks_the_question_actually_closest_to_centroid():
    # Three points on a line: centroid sits at (1, 0), so "middle" (at
    # (1,0) exactly) must win over the two points flanking it.
    questions = ["left", "middle", "right"]
    embeddings = np.array(
        [
            [0.0, 0.0],
            [1.0, 0.0],
            [2.0, 0.0],
        ]
    )
    distiller = CentroidClosestDistiller()
    result = distiller.distill(questions, embeddings)
    print(f"distilled: {result!r} from {questions}")
    assert result == "middle"


def test_result_is_always_a_member_of_the_input_questions():
    rng = np.random.default_rng(0)
    questions = [f"q{i}" for i in range(10)]
    embeddings = rng.normal(size=(10, 16))
    distiller = CentroidClosestDistiller()
    result = distiller.distill(questions, embeddings)
    assert result in questions
