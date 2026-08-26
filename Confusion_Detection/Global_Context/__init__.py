"""Global_Context — Step 2 of Gap Detection: collapses raw questions
into topic buckets, each with one clean representative query.

Scoped narrowly (see claude.md Section 1): embeds + clusters + distills
questions only. Does not touch the deck JSON (that's Similiarity_gen's
job) and does not do retrieval or LLM judgment (Steps 3/4).
"""
from .clustering import Clusterer, HDBSCANClusterer, KMeansClusterer
from .distillation import CentroidClosestDistiller, Distiller
from .schema import TopicCluster, TopologyResult
from .topology_builder import QuestionTopologyBuilder

__all__ = [
    "Clusterer",
    "HDBSCANClusterer",
    "KMeansClusterer",
    "Distiller",
    "CentroidClosestDistiller",
    "TopicCluster",
    "TopologyResult",
    "QuestionTopologyBuilder",
]
