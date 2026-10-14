"""Start the downloaded stdio server, discover tools, and inspect a deck."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent


async def main():
    server = ROOT / "vendor/Office-PowerPoint-MCP-Server/ppt_mcp_server.py"
    parameters = StdioServerParameters(command=sys.executable, args=[str(server)], cwd=str(server.parent))
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"Connected: {len(tools.tools)} tools")
            print(", ".join(tool.name for tool in tools.tools))
            result = await session.call_tool("open_presentation", {
                "file_path": str(ROOT.parent / "data/cc-unit2-slides.pptx"), "id": "prototype_deck"
            })
            for item in result.content:
                if item.type == "text":
                    print(item.text)
                    payload = json.loads(item.text)
                    if "error" in payload or payload.get("slide_count") != 288:
                        raise RuntimeError("MCP failed to open the expected 288-slide deck")
            if result.isError:
                raise RuntimeError("MCP tool returned an error")


if __name__ == "__main__":
    asyncio.run(main())
