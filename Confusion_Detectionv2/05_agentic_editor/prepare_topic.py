"""Prepare one topic; optionally save its context and initialize the LLM."""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

context_module = importlib.import_module("05_agentic_editor.src.context")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic-id", type=int, default=20)
    parser.add_argument("--output", type=Path, help="Optional JSON snapshot path")
    parser.add_argument("--init-llm", action="store_true")
    parser.add_argument("--print-content", action="store_true",
                        help="Print the full topic context as JSON")
    args = parser.parse_args()
    context = context_module.load_topic_context(args.topic_id)
    if args.print_content:
        print(json.dumps(context, ensure_ascii=False, indent=2))
    if args.output:
        context_module.save_topic_context(context, args.output)
    print(f"Topic {args.topic_id}: {len(context['slide_content'])} editable slides, "
          f"{len(context['reference_slide_content'])} reference slides, "
          f"{len(context['report_text'])} report characters")
    if args.init_llm:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
        llm_module = importlib.import_module("05_agentic_editor.src.llm")
        llm = llm_module.build_editor_llm()
        print(f"LangChain LLM initialized: {llm.model_name}")


if __name__ == "__main__":
    main()
