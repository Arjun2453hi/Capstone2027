# Local PowerPoint MCP setup

Run commands from `Confusion_Detectionv2/05_agentic_editor` in PowerShell.

The downloaded server is in `vendor/Office-PowerPoint-MCP-Server` at commit
`3631ba2ec0c24504476f78bf74d329c9be11caaa`. Its virtual environment is
`.venv-ppt-mcp`. Both directories are ignored by Git.

## Verify the local stdio server

```powershell
.\.venv-ppt-mcp\Scripts\python.exe check_mcp.py
```

This launches the server, initializes an MCP session, lists tools, opens the
converted deck and checks its 288-slide count. It closes the server on exit.
It does not run the LLM or apply edits.

## Run the server directly

```powershell
.\.venv-ppt-mcp\Scripts\python.exe vendor/Office-PowerPoint-MCP-Server/ppt_mcp_server.py
```

The default stdio transport waits for an MCP client; it is not an interactive
terminal prompt. The future editor will launch this subprocess automatically.
For an HTTP server instead:

```powershell
.\.venv-ppt-mcp\Scripts\python.exe vendor/Office-PowerPoint-MCP-Server/ppt_mcp_server.py --transport http --port 8000
```

The MCP endpoint is `http://localhost:8000/mcp`. Stop a manually launched
server with Ctrl+C.

## Recreate the environment

```powershell
git clone https://github.com/GongRzhe/Office-PowerPoint-MCP-Server.git vendor/Office-PowerPoint-MCP-Server
git -C vendor/Office-PowerPoint-MCP-Server checkout 3631ba2ec0c24504476f78bf74d329c9be11caaa
python -m venv .venv-ppt-mcp
.\.venv-ppt-mcp\Scripts\python.exe -m pip install -r requirements-mcp.txt
```

MCP is pinned to 1.28.1 for the archived server's FastMCP API.

## Converted deck

`../data/cc-unit2-slides.pptx` contains all 288 PDF pages in their original
order. Zero-based PDF slide IDs map to zero-based PowerPoint slide indexes.
Extractable PDF text is reconstructed as editable text boxes. Diagrams and
other non-text content remain raster backgrounds. Image-only pages have no
reconstructed editable text. This is not the original PowerPoint; fonts,
spacing, bullets and complex layouts require visual review. Text spans are
separate boxes, not semantic bullet placeholders.

To regenerate (overwrites the converted output):

```powershell
.\.venv-ppt-mcp\Scripts\python.exe convert_pdf.py
```

The original PDF is read only and is not modified.

## Run the Groq editor

From `Confusion_Detectionv2` using the existing project Python environment:

```powershell
python 05_agentic_editor/edit_topic.py --topic-id 20
```

The script launches the MCP server automatically via stdio. No HTTP server
needs to be running. It uses the shared Groq LangChain client, the configured
`GROQ_MODEL_NAME`, existing key rotation, and local usage monitoring.

Set `LANGSMITH_API_KEY` in the project `.env` (the existing
`LANGCHAIN_API_KEY` also works). Optional `LANGSMITH_PROJECT` selects the
trace project. Tracing is enabled for the editor run, LLM invocations and MCP
calls. Topic text and tool arguments/results are included in LangSmith traces.

The default output is `05_agentic_editor/output/topic_20_edited.pptx`, with
an adjacent `.changes.json` audit log. Existing output files are not
overwritten; use `--output` with a fresh filename for another run.

Only the selected topic's existing slides can be edited. Discovered reference
slides can be inspected. The LLM gets three discovered tools: `get_slide_info`,
`extract_slide_text`, and `manage_text`. Text context avoids repeated fields
and fetches reference slide details only when needed. A bounded loop and
context-size check stop runaway runs. Saved-slide XML is checked for changes
outside the topic; visual layout and educational quality still need review.
