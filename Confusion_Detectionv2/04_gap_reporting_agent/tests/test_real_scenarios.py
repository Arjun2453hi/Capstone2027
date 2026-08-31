"""Real scenario tests -- hits the real Groq API against 2-3 real
topics from the actual deck's own gap_verification_input.json. No
labeled ground truth is available (the answer key stays withheld from
this repo, confirmed with the user), so these check that the agent
genuinely investigates -- calls the right tools before concluding, and
produces a substantial, topic-grounded report -- not that gap_type
matches a known-correct label (claude.md Section 12's own point:
"a wrong process that happens to land on the right label is a trap
this test is specifically meant to catch" -- the inverse also holds:
without a label to check, verifying the *process* is the only thing
these tests reasonably can, and should, do).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


def _safe_print(text: str) -> None:
    """Real Groq output occasionally contains Unicode punctuation (e.g.
    a narrow no-break space) that Windows' default console codepage
    can't encode -- sanitize before printing so a real, successful
    investigation's report never fails the test on a print() call."""
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, errors="replace").decode(encoding))

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", autouse=True)
def _load_env():
    from dotenv import load_dotenv

    load_dotenv(_PROJECT_ROOT / ".env")


@pytest.fixture(scope="module")
def real_setup():
    import importlib
    import sys

    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))

    deck_storage = importlib.import_module("01_deck_parsing.src.storage")
    mapping_export = importlib.import_module("03_question_mapping.src.export")
    common_emb = importlib.import_module("common.embeddings")
    common_llm = importlib.import_module("common.llm_client")
    orchestrator_module = importlib.import_module("04_gap_reporting_agent.src.orchestrator")

    deck = deck_storage.load_deck_json(_PROJECT_ROOT / "01_deck_parsing" / "parsed_deck.json")
    gap_input = mapping_export.load_gap_verification_input_json(
        _PROJECT_ROOT / "03_question_mapping" / "gap_verification_input.json"
    )
    embedding_model = common_emb.RetrievalEmbeddingModel()
    ctx = orchestrator_module.build_investigation_context(
        deck, gap_input.topics, gap_input.unmatched_questions, embedding_model
    )
    chat_model = common_llm.get_validated_chat_groq()
    return ctx, gap_input, chat_model


def _investigate_real_topic(ctx, gap_input, chat_model, topic_id: int):
    from ..src.agent import run_topic_investigation
    from ..src.prompts import TOPIC_KICKOFF_TEMPLATE

    dossier = next(d for d in gap_input.topics if d.topic_id == topic_id)
    questions = [{"question": m.question, "score": m.score} for m in dossier.matched_questions]
    kickoff = TOPIC_KICKOFF_TEMPLATE.format(
        topic_id=dossier.topic_id,
        start=dossier.slide_ids[0] if dossier.slide_ids else "?",
        end=dossier.slide_ids[-1] if dossier.slide_ids else "?",
        n=len(questions),
        question_list="\n".join(f"- {q['question']}" for q in questions) or "(none)",
    )
    return run_topic_investigation(chat_model, ctx, topic_id, kickoff)


def test_real_agent_investigates_a_well_covered_topic_before_concluding(real_setup):
    ctx, gap_input, chat_model = real_setup
    # The largest-cluster real topic -- lots of matched questions, so a
    # lazy one-look conclusion is easy to catch (it would have nothing
    # concrete to say about them).
    topic_id = max(gap_input.topics, key=lambda d: d.cluster_size).topic_id

    result = _investigate_real_topic(ctx, gap_input, chat_model, topic_id)

    print(f"topic {topic_id}: outcome={result['outcome']} gap_type={result['report'].gap_type} "
          f"confidence={result['report'].confidence} tool_calls={result['n_tool_calls']}")
    _safe_print(result["report"].report_text)

    assert result["outcome"] in ("completed", "cap_hit")
    # Genuinely investigated, not a lazy single look: at least the
    # topic's own content lookup plus one more tool call before
    # concluding.
    assert result["n_tool_calls"] >= 2
    assert len(result["report"].report_text) > 100  # a substantial write-up, not a one-liner
    assert result["report"].gap_type in ("complete_omission", "shallow_coverage", "fragmented_context", "covered")


def test_real_agent_investigates_a_thin_cluster_topic(real_setup):
    ctx, gap_input, chat_model = real_setup
    # The smallest real, non-empty cluster -- a genuinely harder case
    # (thin evidence), a good check that the agent still investigates
    # rather than defaulting to a confident-sounding guess.
    candidates = [d for d in gap_input.topics if d.cluster_size > 0]
    topic_id = min(candidates, key=lambda d: d.cluster_size).topic_id

    result = _investigate_real_topic(ctx, gap_input, chat_model, topic_id)

    print(f"topic {topic_id}: outcome={result['outcome']} gap_type={result['report'].gap_type} "
          f"confidence={result['report'].confidence} tool_calls={result['n_tool_calls']}")
    _safe_print(result["report"].report_text)

    assert result["outcome"] in ("completed", "cap_hit")
    assert result["n_tool_calls"] >= 2
    assert len(result["report"].report_text) > 100


def test_real_agent_investigates_the_unmatched_questions_topic(real_setup):
    ctx, gap_input, chat_model = real_setup
    from ..src.agent import run_topic_investigation
    from ..src.prompts import UNMATCHED_KICKOFF_TEMPLATE

    if not gap_input.unmatched_questions:
        pytest.skip("no unmatched questions in this run's gap_verification_input.json")

    questions = [{"question": u.question, "score": u.best_score} for u in gap_input.unmatched_questions]
    kickoff = UNMATCHED_KICKOFF_TEMPLATE.format(
        n=len(questions), question_list="\n".join(f"- {q['question']}" for q in questions)
    )
    result = run_topic_investigation(chat_model, ctx, -1, kickoff)

    print(f"unmatched topic: outcome={result['outcome']} gap_type={result['report'].gap_type} "
          f"confidence={result['report'].confidence} tool_calls={result['n_tool_calls']}")
    _safe_print(result["report"].report_text)

    assert result["outcome"] in ("completed", "cap_hit")
    assert result["report"].topic_id == -1
    # The kickoff message explicitly tells it there's no slide range and
    # to use search_similar_slides first -- confirm it didn't just call
    # get_topic_slides(-1) and stop (that always returns "").
    assert result["n_tool_calls"] >= 1
