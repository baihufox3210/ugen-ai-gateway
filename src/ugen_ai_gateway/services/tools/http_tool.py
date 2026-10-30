"""Tools people describe themselves: a name, what it does, the parameters the
model must give, and a web address to call.

``{name}`` in the address is replaced by the (URL-encoded) argument of that
name; the other arguments go in the query string for GET and DELETE and as a
JSON body for the other methods.
"""

import json
import re
from typing import Any
from urllib.parse import quote, urljoin

from ugen_ai_gateway.config import get_settings
from ugen_ai_gateway.services.tools.netguard import check_url, guarded_client

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
MAX_REDIRECTS = 3
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def filled_url(template: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The address with its ``{name}`` parts filled, and the arguments left over."""
    rest = dict(arguments)

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in rest:
            raise ValueError(f"The argument '{name}' is missing")
        return quote(str(rest.pop(name)), safe="")

    return _PLACEHOLDER.sub(fill, template), rest


async def call_http_tool(
    config: dict[str, Any], headers: dict[str, str], arguments: dict[str, Any]
) -> str:
    """Send the request a tool describes; return the body as text."""
    method = str(config.get("method", "GET")).upper()
    url, rest = filled_url(str(config["url"]), arguments)
    settings = get_settings()
    options: dict[str, Any] = {}
    if method in ("GET", "DELETE"):
        options["params"] = {
            key: value if isinstance(value, str) else json.dumps(value)
            for key, value in rest.items()
        }
    else:
        options["json"] = rest
    async with guarded_client(
        settings.tool_timeout_seconds, {"User-Agent": "UGenGateway-Tool/1.0", **headers}
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(url)
            response = await client.request(method, url, **options)
            if response.is_redirect and (location := response.headers.get("location")):
                url = urljoin(url, location)
                if response.status_code in (301, 302, 303):
                    method, options = "GET", {}
                continue
            break
        else:
            raise ValueError("Too many redirects")
    body = response.text
    if response.status_code >= 400:
        raise ValueError(f"HTTP {response.status_code}: {body[:500]}")
    return body
