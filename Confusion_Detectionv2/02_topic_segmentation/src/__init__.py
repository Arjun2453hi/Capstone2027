"""02_topic_segmentation: group slides into topics by content shift. See CLAUDE.md."""
from .schema import Topic
from .segmenter import TopicSegmenter

__all__ = ["Topic", "TopicSegmenter"]
