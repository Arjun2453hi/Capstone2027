"""run_gap_reporting.py — CLI driver for 04_gap_reporting_agent.

Run directly (NOT `python -m ...` -- 04_gap_reporting_agent starts with
a digit, same reason every other stage's runner does this):

    python "04_gap_reporting_agent/run_gap_reporting.py"

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

orchestrator_module = importlib.import_module("04_gap_reporting_agent.src.orchestrator")
severity_module = importlib.import_module("04_gap_reporting_agent.src.severity")
mapping_export = importlib.import_module("03_question_mapping.src.export")
deck_storage = importlib.import_module("01_deck_parsing.src.storage")
common_emb = importlib.import_module("common.embeddings")
common_llm = importlib.import_module("common.llm_client")

DEFAULT_DECK_PATH = _PROJECT_ROOT / "01_deck_parsing" / "parsed_deck.json"
DEFAULT_GAP_INPUT_PATH = _PROJECT_ROOT / "03_question_mapping" / "gap_verification_input.json"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DEFAULT_INDEX_PATH = None  # resolved from output_dir if not given


def run(deck_path: Path, gap_input_path: Path, output_dir: Path, index_path: Path = None, limit: int = None):
    index_path = index_path or (output_dir / "index.json")

    deck = deck_storage.load_deck_json(deck_path)
    gap_input = mapping_export.load_gap_verification_input_json(gap_input_path)
    print(f"Deck: {deck_path} ({len(deck.slides)} slides)")
    print(
        f"Gap verification input: {gap_input_path} "
        f"({gap_input.total_topics} topics, {len(gap_input.unmatched_questions)} unmatched)"
    )

    topics_to_run = gap_input.topics[:limit] if limit is not None else gap_input.topics
    unmatched_to_run = gap_input.unmatched_questions if limit is None else []
    if limit is not None:
        print(f"Scoped run: only the first {limit} topic(s) (--limit); unmatched-questions topic skipped this run.")

    embedding_model = common_emb.RetrievalEmbeddingModel()
    print(f"Embedding model: {embedding_model.name}")

    ctx = orchestrator_module.build_investigation_context(
        deck, gap_input.topics, gap_input.unmatched_questions, embedding_model
    )
    print(f"found_threshold={ctx.found_threshold:.4f} switch_threshold={ctx.switch_threshold:.4f}")

    chat_model = common_llm.get_validated_chat_groq()
    print(f"Groq model: {common_llm.resolve_model_name()}")

    results = orchestrator_module.run_all_topics(chat_model, ctx, topics_to_run, unmatched_to_run, output_dir)

    summary = orchestrator_module.summarize_outcomes(results)
    print(f"\nrun summary: {summary}")

    dossiers_by_id = {d.topic_id: d for d in gap_input.topics}
    index_entries = []
    for r in results:
        topic_id = r["topic_id"]
        backed_by_questions = (
            len(gap_input.unmatched_questions) if topic_id == -1 else dossiers_by_id[topic_id].cluster_size
        )
        index_entries.append(
            {
                "topic_id": topic_id,
                "filename": orchestrator_module.report_filename(topic_id),
                "gap_type": r["report"].gap_type,
                "confidence": r["report"].confidence,
                "backed_by_questions": backed_by_questions,
                "outcome": r["outcome"],
            }
        )
    index = severity_module.build_index(index_entries)
    severity_module.save_index_json(index, index_path)
    print(f"\nWrote: {index_path}")
    print("\ntop 5 by severity:")
    for entry in index[:5]:
        print(
            f"  topic {entry['topic_id']}: {entry['gap_type']} severity={entry['severity']:.3f} "
            f"confidence={entry['confidence']:.2f} backed_by={entry['backed_by_questions']}"
        )

    return results, index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=Path, default=DEFAULT_DECK_PATH)
    parser.add_argument("--gap-input", type=Path, default=DEFAULT_GAP_INPUT_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--index-out", type=Path, default=None)
    parser.add_argument(
        "--limit", type=int, default=None, help="Only run the first N topics (skips the unmatched-questions topic)."
    )
    args = parser.parse_args()

    run(args.deck, args.gap_input, args.output_dir, args.index_out, limit=args.limit)


if __name__ == "__main__":
    main()
