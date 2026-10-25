"""MCP server: the tool functions, and the tools as an MCP client sees them."""
from __future__ import annotations

import json

import pytest

from citation_needed import mcp_server
from tests.conftest import EXAMPLE, ROOT


def test_check_citations_tool_gives_verdicts_and_fixes():
    out = mcp_server.check_citations(EXAMPLE.read_text())
    assert [c["status"] for c in out["citations"]] == [
        "matches_record", "retracted", "distorted", "fabricated", "abstained"]
    assert [f["citation"] for f in out["fix"]] == [2, 3, 4]
    assert "do not invent" in out["fix"][2]["action"]


def test_check_numbers_tool_accepts_texts_and_paths():
    answer = (ROOT / "examples" / "numbers-answer.md").read_text()
    by_path = mcp_server.check_numbers(answer, source_paths=[str(ROOT / "examples" / "sources")])
    assert [f["number"] for f in by_path["fix"]] == ["43,631", "2.14", "76.4%"]
    src = (ROOT / "examples" / "sources" / "turk2026-abstract-results.md").read_text()
    by_text = mcp_server.check_numbers(answer, source_texts=[src])
    assert by_text["summary"] == by_path["summary"]
    assert by_text["sources"] == ["source_texts[0]"]


def test_check_numbers_tool_requires_a_source():
    with pytest.raises(ValueError):
        mcp_server.check_numbers("7.82%")


def test_server_lists_and_calls_both_tools():
    pytest.importorskip("mcp")
    import anyio

    server = mcp_server.build_server()

    async def run():
        tools = {t.name: t for t in await server.list_tools()}
        assert set(tools) == {"check_citations", "check_numbers"}
        schema = getattr(tools["check_numbers"], "input_schema", None) \
            or tools["check_numbers"].inputSchema
        assert {"text", "source_texts", "source_paths"} <= set(schema["properties"])
        result = await server.call_tool("check_numbers", {
            "text": "Fabrication was 7.82%, not 9.1%.", "source_texts": ["pooled 7.82%"]})
        return result

    result = anyio.run(run)
    # SDK 1.x returns (content, structured) or a content list; 2.x a CallToolResult
    content = getattr(result, "content", None) or (result[0] if isinstance(result, tuple) else result)
    payload = json.loads(content[0].text)
    assert [n["status"] for n in payload["numbers"]] == ["found", "not_found"]


def test_main_version(capsys):
    assert mcp_server.main(["--version"]) == 0
    assert "citation-needed-mcp" in capsys.readouterr().out
