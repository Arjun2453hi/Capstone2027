"""run_segmentation.py — CLI driver for 02_topic_segmentation.

Run directly (NOT `python -m ...` -- 02_topic_segmentation starts with a
digit, which breaks module-path resolution the same way pytest's
default import mode breaks on it):

    python "02_topic_segmentation/run_segmentation.py"

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

seg_src = importlib.import_module("02_topic_segmentation.src")
seg_storage = importlib.import_module("02_topic_segmentation.src.storage")
seg_viz = importlib.import_module("02_topic_segmentation.src.visualization")
deck_storage = importlib.import_module("01_deck_parsing.src.storage")
common_emb = importlib.import_module("common.embeddings")

DEFAULT_DECK_PATH = _PROJECT_ROOT / "01_deck_parsing" / "parsed_deck.json"
DEFAULT_TOPICS_OUT_PATH = Path(__file__).resolve().parent / "topics.json"
DEFAULT_PLOT_OUT_PATH = Path(__file__).resolve().parent / "segmentation_plot.png"


def run(deck_path: Path, topics_out_path: Path, plot_out_path: Path):
    deck = deck_storage.load_deck_json(deck_path)
    print(f"Deck: {deck_path} ({len(deck.slides)} slides)")

    model = common_emb.SentenceTransformerEmbeddingModel()
    print(f"Embedding model: {model.name}")

    segmenter = seg_src.TopicSegmenter(model)
    topics, diagnostics = segmenter.segment(deck)

    sizes = [len(t.slide_ids) for t in topics]
    print(f"\ntopics found: {len(topics)}")
    print(f"segment sizes: {sizes} (mean {sum(sizes) / len(sizes):.2f})")
    print(f"global adaptive threshold (reporting only): {diagnostics['threshold']:.4f}")
    print("\npredicted boundaries (start_slide_id, boundary_confidence):")
    for t in topics[1:]:
        print(f"  ({t.start_slide_id}, {t.boundary_confidence:.4f})")

    seg_storage.save_topics_json(topics, topics_out_path)
    print(f"\nWrote: {topics_out_path}")

    seg_viz.plot_segmentation(
        diagnostics["combined_depth"],
        diagnostics["thresholds"],
        diagnostics["boost_mask"],
        topics,
        plot_out_path,
    )
    print(f"Wrote: {plot_out_path}")

    return topics, diagnostics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=Path, default=DEFAULT_DECK_PATH)
    parser.add_argument("--topics-out", type=Path, default=DEFAULT_TOPICS_OUT_PATH)
    parser.add_argument("--plot-out", type=Path, default=DEFAULT_PLOT_OUT_PATH)
    args = parser.parse_args()

    run(args.deck, args.topics_out, args.plot_out)


if __name__ == "__main__":
    main()
