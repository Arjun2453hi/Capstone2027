"""Gap_Verification — Step 4: Synthesized Verification.

Given a Context_Window ContextBundle, judges completeness and produces
a GapVerdict: gap_type, confidence (genuine model score, adjusted for
known data-quality caveats), guidance, and an optional suggested_addition.
Scoped to judgment only — aggregation into a report is Step 5.
"""
from .classifier import GapClassifier, ZeroShotNLIClassifier
from .generator import FlanT5Generator, GapContentGenerator
from .schema import ClassificationResult, GapVerdict, GeneratedContent
from .verifier import DEFAULT_SINGLETON_MODE, SINGLETON_MODES, GapVerifier

__all__ = [
    "GapClassifier",
    "ZeroShotNLIClassifier",
    "GapContentGenerator",
    "FlanT5Generator",
    "ClassificationResult",
    "GeneratedContent",
    "GapVerdict",
    "GapVerifier",
    "SINGLETON_MODES",
    "DEFAULT_SINGLETON_MODE",
]
