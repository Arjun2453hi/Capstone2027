import json
from types import SimpleNamespace
import pytest
from ..src.question_assessment import assess_questions


def test_invalid_assessment_never_silently_marks_questions_covered():
    deck = SimpleNamespace(get=lambda sid: SimpleNamespace(raw_text="A real explanation"))
    dossier = SimpleNamespace(slide_ids=[0], matched_questions=[SimpleNamespace(question="Explain this?")])
    for verdicts in ([], [{"question": "A different question", "verdict": "covered", "reason": "Reason", "slide_ids": [0]}],
                     [{"question": "Explain this?", "verdict": "covered", "reason": "Reason", "slide_ids": []}],
                     [{"question": "Explain this?", "verdict": "covered", "reason": "Reason", "slide_ids": [99]}]):
        generator = SimpleNamespace(generate=lambda *args: json.dumps({"verdicts": verdicts}))
        with pytest.raises(ValueError):
            assess_questions(generator, dossier, deck)
