"""03_question_mapping: score every real student question against
every topic (topic -> question direction). See project root CLAUDE.md.
"""
from .export import GapVerificationInput, TopicDossier, build_gap_verification_input, save_gap_verification_input_json
from .llm_fallback import AmbiguityResolver, GroqAmbiguityResolver
from .mapper import QuestionMapper
from .schema import MappingResult, QuestionMatch, TopicMapping, UnmatchedQuestion

__all__ = [
    "QuestionMapper",
    "AmbiguityResolver",
    "GroqAmbiguityResolver",
    "MappingResult",
    "TopicMapping",
    "QuestionMatch",
    "UnmatchedQuestion",
    "GapVerificationInput",
    "TopicDossier",
    "build_gap_verification_input",
    "save_gap_verification_input_json",
]
