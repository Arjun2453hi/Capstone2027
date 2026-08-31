"""run_mapping.py — CLI driver for 03_question_mapping.

Run directly (NOT `python -m ...` -- 03_question_mapping starts with a
digit, which breaks module-path resolution the same way pytest's
default import mode breaks on it):

    python "03_question_mapping/run_mapping.py"

from the Confusion_Detectionv2 directory, or with any absolute/relative
path to this file from elsewhere.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_PROJECT_ROOT / ".env")

mapping_src = importlib.import_module("03_question_mapping.src")
mapping_storage = importlib.import_module("03_question_mapping.src.storage")
deck_storage = importlib.import_module("01_deck_parsing.src.storage")
topic_storage = importlib.import_module("02_topic_segmentation.src.storage")
common_emb = importlib.import_module("common.embeddings")

DEFAULT_DECK_PATH = _PROJECT_ROOT / "01_deck_parsing" / "parsed_deck.json"
DEFAULT_TOPICS_PATH = _PROJECT_ROOT / "02_topic_segmentation" / "topics.json"
DEFAULT_OUT_PATH = Path(__file__).resolve().parent / "question_mapping.json"
DEFAULT_GAP_INPUT_OUT_PATH = Path(__file__).resolve().parent / "gap_verification_input.json"


def find_questions_file(directory: Path) -> Path:
    for path in sorted(directory.glob("*.txt")):
        if "question" in path.name.lower():
            return path
    raise FileNotFoundError(f"No questions .txt found in {directory}")


def load_questions(path: Path):
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(
    deck_path: Path,
    topics_path: Path,
    questions_path: Path,
    out_path: Path,
    gap_input_out_path: Path = DEFAULT_GAP_INPUT_OUT_PATH,
    use_llm_fallback: bool = True,
):
    deck = deck_storage.load_deck_json(deck_path)
    topics = topic_storage.load_topics_json(topics_path)
    questions = load_questions(questions_path)

    print(f"Deck:      {deck_path} ({len(deck.slides)} slides)")
    print(f"Topics:    {topics_path} ({len(topics)} topics)")
    print(f"Questions: {questions_path} ({len(questions)} questions)")

    model = common_emb.RetrievalEmbeddingModel()
    print(f"Embedding model: {model.name}")

    resolver = None
    if use_llm_fallback:
        try:
            resolver = mapping_src.GroqAmbiguityResolver()
            print(f"LLM fallback: {resolver.name}")
        except RuntimeError as e:
            print(f"[warn] LLM fallback disabled: {e}")

    mapper = mapping_src.QuestionMapper(model, resolver=resolver)
    result, diagnostics = mapper.map_questions(questions, topics, deck)

    n_semantic = sum(1 for t in result.topics for q in t.matched_questions if q.method == "semantic")
    n_llm = sum(1 for t in result.topics for q in t.matched_questions if q.method == "llm_fallback")
    print(f"\nmatched via semantic similarity: {n_semantic}")
    print(f"matched via LLM fallback: {n_llm} (of {diagnostics['n_llm_calls']} ambiguous calls made)")
    print(f"unmatched: {len(result.unmatched_questions)}")

    real_topics_with_matches = sum(1 for t in result.topics if t.matched_questions)
    print(f"topics with at least one matched question: {real_topics_with_matches}/{len(result.topics)}")

    mapping_storage.save_mapping_json(result, out_path)
    print(f"\nWrote: {out_path}")

    gap_input = mapping_src.build_gap_verification_input(result, topics, deck)
    mapping_src.save_gap_verification_input_json(gap_input, gap_input_out_path)
    print(f"Wrote: {gap_input_out_path} (final input for 04_gap_verification)")

    return result, diagnostics


def rebuild_gap_input_only(deck_path: Path, topics_path: Path, mapping_path: Path, gap_input_out_path: Path):
    """Regenerates gap_verification_input.json from an already-computed
    question_mapping.json, without re-running QuestionMapper (no
    embedding pass, no Groq LLM-fallback calls) -- for when only
    export.py's assembly logic changed (e.g. the topic_text max_chars
    fix), not the actual question-to-topic mapping itself. Real
    question-mapping results are expensive (real LLM-fallback calls);
    this must not be redone just to pick up an unrelated export fix."""
    deck = deck_storage.load_deck_json(deck_path)
    topics = topic_storage.load_topics_json(topics_path)
    result = mapping_storage.load_mapping_json(mapping_path)

    print(f"Deck:     {deck_path} ({len(deck.slides)} slides)")
    print(f"Topics:   {topics_path} ({len(topics)} topics)")
    print(f"Mapping:  {mapping_path} (reused as-is, not recomputed)")

    gap_input = mapping_src.build_gap_verification_input(result, topics, deck)
    mapping_src.save_gap_verification_input_json(gap_input, gap_input_out_path)
    print(f"Wrote: {gap_input_out_path}")
    return gap_input


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=Path, default=DEFAULT_DECK_PATH)
    parser.add_argument("--topics", type=Path, default=DEFAULT_TOPICS_PATH)
    parser.add_argument("--questions", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--gap-input-out", type=Path, default=DEFAULT_GAP_INPUT_OUT_PATH)
    parser.add_argument("--no-llm-fallback", action="store_true")
    parser.add_argument(
        "--rebuild-gap-input-only",
        action="store_true",
        help="Skip QuestionMapper entirely and just rebuild gap_verification_input.json from the existing --out file.",
    )
    args = parser.parse_args()

    if args.rebuild_gap_input_only:
        rebuild_gap_input_only(args.deck, args.topics, args.out, args.gap_input_out)
        return

    questions_path = args.questions or find_questions_file(_PROJECT_ROOT / "data")
    run(
        args.deck,
        args.topics,
        questions_path,
        args.out,
        gap_input_out_path=args.gap_input_out,
        use_llm_fallback=not args.no_llm_fallback,
    )


if __name__ == "__main__":
    main()
