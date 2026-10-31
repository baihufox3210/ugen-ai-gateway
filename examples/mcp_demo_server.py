"""A small MCP server to try the gateway's MCP support with.

    uv run python examples/mcp_demo_server.py            # http://127.0.0.1:8765/mcp
    uv run python examples/mcp_demo_server.py --port 9000

It offers three harmless tools: ``word_count``, ``reverse_text`` and
``convert_temperature``. Register its address under Settings → Tools → MCP
servers (see "Agents, tools and MCP" in the README).
"""

import argparse

import uvicorn
from mcp.server import MCPServer

server = MCPServer("demo", instructions="Small text and unit helpers.")


@server.tool()
def word_count(text: str) -> int:
    """Count the words in a text."""
    return len(text.split())


@server.tool()
def reverse_text(text: str) -> str:
    """Reverse a text, character by character."""
    return text[::-1]


@server.tool()
def convert_temperature(value: float, to: str) -> str:
    """Convert a temperature: ``to`` is "celsius" or "fahrenheit"."""
    if to == "fahrenheit":
        return f"{value * 9 / 5 + 32:.1f} °F"
    if to == "celsius":
        return f"{(value - 32) * 5 / 9:.1f} °C"
    raise ValueError('to must be "celsius" or "fahrenheit"')


def app():
    """The server as a web app (streamable HTTP, at /mcp)."""
    return server.streamable_http_app(stateless_http=True, json_response=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args()
    uvicorn.run(app(), host=arguments.host, port=arguments.port)
