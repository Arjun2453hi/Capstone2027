"""ContextBundle — Step 4's actual input, one per topic.

source_questions and cluster_size are carried through unchanged from
Global_Context's TopicCluster, not recomputed here — Step 5's severity
scoring and "backed by N questions" line depend on them, exactly as
they did one stage earlier (see Global_Context/schema.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class ContextBundle:
    topic_id: int
    representative_query: str
    anchor_slide_id: int
    window_slide_ids: List[int]  # ordered ascending, always includes the anchor
    window_text: str  # assembled, budget-enforced
    source_questions: List[str]
    cluster_size: int
    is_noise: bool = False  # carried through from TopicCluster -- Gap_Verification weights singletons differently

    def __post_init__(self):
        if self.anchor_slide_id not in self.window_slide_ids:
            # The one invariant every ContextWindowStrategy must uphold —
            # checked here so a bug in a future strategy fails loudly at
            # construction, not silently three stages later when Step 4
            # can't find the slide it's supposed to be judging.
            raise ValueError(
                f"topic {self.topic_id}: anchor_slide_id={self.anchor_slide_id} "
                f"is not in window_slide_ids={self.window_slide_ids}"
            )
        if sorted(self.window_slide_ids) != list(self.window_slide_ids):
            raise ValueError(
                f"topic {self.topic_id}: window_slide_ids must be ascending, "
                f"got {self.window_slide_ids}"
            )
