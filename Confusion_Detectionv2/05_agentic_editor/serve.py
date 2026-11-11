"""Serve the React editor and local API for every registered deck.
Build frontend/ first with npm run build.

    python 05_agentic_editor/serve.py                 # opens the first processed deck; switch in the UI
    python 05_agentic_editor/serve.py --deck se-unit-4
"""
import argparse
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    from common import deck_workspace

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--deck", default=None, help="deck opened first (default: $DECK_ID, else the first processed deck)")
    parser.add_argument("--with-saved", action="store_true",
                        help="also load the pre-generated, reviewed suggestions (offline demo); "
                             "by default every suggestion is generated live")
    parser.add_argument("--state-dir", type=Path, default=None,
                        help="optional root for per-deck workspace state (default: each deck's editor/workspace)")
    args = parser.parse_args()
    ready = [d.deck_id for d in deck_workspace.list_decks() if d.parsed_deck.exists()]
    default = args.deck or os.getenv("DECK_ID") or (ready[0] if ready else None)
    if default is None:
        sys.exit("No processed decks. Add one and run it first:\n"
                 "  python run_pipeline.py add <file.pdf|file.pptx>\n  python run_pipeline.py run <deck_id>")
    deck_workspace.load(default)  # fail now, clearly, if the id is wrong
    print(f"Decks: {', '.join(ready)}; opening '{default}' first")
    server = importlib.import_module("05_agentic_editor.src.web_api")
    uvicorn.run(server.create_server(default_deck=default, state_root=args.state_dir,
                                     include_saved=True if args.with_saved else None),
                host="127.0.0.1", port=args.port, log_level="info")
