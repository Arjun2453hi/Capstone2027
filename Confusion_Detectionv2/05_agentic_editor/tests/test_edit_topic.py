import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from langchain_core.messages import AIMessage, ToolMessage

spec = importlib.util.spec_from_file_location("edit_topic", Path(__file__).resolve().parents[1] / "edit_topic.py")
editor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editor)


def test_unwrap_fastmcp_result():
    result = SimpleNamespace(isError=False, structuredContent={"result": {"slide_count": 288}}, content=[])
    assert editor.unpack(result) == {"slide_count": 288}


def test_scope_and_inspection_guard():
    with pytest.raises(ValueError, match="Reference"):
        editor.guard_call("manage_text", {"slide_index": 1}, {0}, {0, 1}, {})
    with pytest.raises(ValueError, match="Inspect"):
        editor.guard_call("manage_text", {"slide_index": 0}, {0}, {0}, {})
    arguments = {"slide_index": 0, "operation": "validate", "validation_only": False}
    editor.guard_call("manage_text", arguments, {0}, {0}, {0: {"get_slide_info", "extract_slide_text"}})
    assert arguments["validation_only"] is True


def test_loop_handles_tool_results_and_recovers_from_rejected_edit():
    def call(name, args, identifier):
        return {"name": name, "args": args, "id": identifier, "type": "tool_call"}

    replies = iter([
        AIMessage(content="", tool_calls=[call("manage_text", {"slide_index": 0, "operation": "add"}, "rejected")]),
        AIMessage(content="", tool_calls=[call("get_slide_info", {"slide_index": 0}, "info"),
                                         call("extract_slide_text", {"slide_index": 0}, "text")]),
        AIMessage(content="", tool_calls=[call("manage_text", {"slide_index": 0, "operation": "add", "text": "New explanation"}, "edit")]),
        AIMessage(content="Updated the explanation")])
    seen = []

    class Model:
        def bind_tools(self, schemas):
            return self

        def invoke(self, messages):
            seen.extend(m for m in messages if isinstance(m, ToolMessage))
            return next(replies)

    class Session:
        async def call_tool(self, name, arguments):
            assert arguments["presentation_id"] == "test-deck"
            return SimpleNamespace(isError=False, structuredContent={"message": "ok"}, content=[])

    context = {"topic_id": 20, "topic": {"slide_ids": [0]}, "report_text": "Explain tests",
               "slide_content": [{"slide_id": 0, "raw_text": "Tests"}], "reference_slide_content": []}
    result = asyncio.run(editor.tool_loop(Session(), Model(), [], context, "test-deck"))
    assert result["iterations"] == 4
    assert result["tool_calls"][0]["ok"] is False
    assert result["tool_calls"][-1]["ok"] is True
    assert any('Inspect' in message.content for message in seen)


def test_saved_copy_detects_out_of_scope_changes(tmp_path):
    before, after = tmp_path / "before.pptx", tmp_path / "after.pptx"
    for path, second in ((before, b"original"), (after, b"changed")):
        with ZipFile(path, "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", b"same")
            archive.writestr("ppt/slides/slide2.xml", second)
    with pytest.raises(RuntimeError, match="outside topic"):
        editor.verify_copy(before, after, {0})
    assert editor.verify_copy(before, after, {1}) == [1]
