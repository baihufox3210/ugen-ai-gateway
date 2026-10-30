"""Tools every account can use without setting anything up. They have no side
effects on the world: they read the clock, calculate, and fetch a web page."""

import ast
import html
import math
import operator
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ugen_ai_gateway.config import get_settings
from ugen_ai_gateway.services.tools.netguard import check_url, guarded_client

MAX_FETCH_BYTES = 1_000_000
MAX_REDIRECTS = 3

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_NAMES: dict[str, Any] = {
    "pi": math.pi,
    "e": math.e,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
}
_MAX_EXPONENT = 10_000


def calculate(expression: str) -> float | int:
    """Evaluate arithmetic: + - * / // % **, parentheses and a few functions."""
    if len(expression) > 500:
        raise ValueError("The expression is too long")

    def walk(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return node.value
        if isinstance(node, ast.Name) and node.id in _NAMES:
            return _NAMES[node.id]
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](walk(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
                raise ValueError("The exponent is too large")
            return _BINARY[type(node.op)](left, right)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and callable(_NAMES.get(node.func.id))
            and not node.keywords
        ):
            return _NAMES[node.func.id](*[walk(arg) for arg in node.args])
        raise ValueError("Only arithmetic is allowed")

    return walk(ast.parse(expression.strip(), mode="eval"))


async def _calculator(args: dict[str, Any]) -> str:
    return str(calculate(str(args.get("expression", ""))))


async def _current_time(args: dict[str, Any]) -> str:
    name = str(args.get("timezone") or "UTC")
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Unknown time zone: {name}") from None
    now = datetime.now(UTC).astimezone(zone)
    return now.strftime(f"%Y-%m-%d %H:%M:%S %A ({name}, UTC%z)")


def _text_of(page: str, content_type: str) -> str:
    if "html" not in content_type.lower():
        return page
    page = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", page)
    page = re.sub(r"(?s)<!--.*?-->", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", page)
    page = html.unescape(re.sub(r"<[^>]+>", " ", page))
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in page.split("\n"))
    return "\n".join(line for line in lines if line)


async def _fetch_url(args: dict[str, Any]) -> str:
    url = str(args.get("url", "")).strip()
    timeout = get_settings().tool_timeout_seconds
    async with guarded_client(
        timeout, {"User-Agent": "UGenGateway-Tool/1.0"}
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(url)
            async with client.stream("GET", url) as response:
                if response.is_redirect and (
                    location := response.headers.get("location")
                ):
                    url = urljoin(url, location)
                    continue
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) >= MAX_FETCH_BYTES:
                        break
                text = bytes(body).decode(response.encoding or "utf-8", "replace")
                content_type = response.headers.get("content-type", "")
                if response.status_code >= 400:
                    raise ValueError(f"HTTP {response.status_code} from {url}")
                return _text_of(text, content_type)
    raise ValueError("Too many redirects")


def _schema(**properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": [
            name for name, spec in properties.items() if spec.pop("required", False)
        ],
    }


BUILTIN_TOOLS: dict[str, dict[str, Any]] = {
    "current_time": {
        "description": "The current date and time, optionally in an IANA time zone.",
        "parameters": _schema(
            timezone={"type": "string", "description": "For example Asia/Taipei"}
        ),
        "run": _current_time,
    },
    "calculator": {
        "description": "Calculate an arithmetic expression exactly, such as (12.5 * 3) / 4.",
        "parameters": _schema(
            expression={
                "type": "string",
                "description": "The expression",
                "required": True,
            }
        ),
        "run": _calculator,
    },
    "fetch_url": {
        "description": "Download a web page or text file and return its text.",
        "parameters": _schema(
            url={"type": "string", "description": "http(s) address", "required": True}
        ),
        "run": _fetch_url,
    },
}
