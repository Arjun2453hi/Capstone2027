"""Serve the React editor and local API. Build frontend/ first with npm run build."""
import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--state-dir", type=Path, default=None, help="Optional separate local workspace state directory")
    args = parser.parse_args()
    server = importlib.import_module("05_agentic_editor.src.web_api")
    uvicorn.run(server.create_app(state_dir=args.state_dir), host="127.0.0.1", port=args.port, log_level="info")
