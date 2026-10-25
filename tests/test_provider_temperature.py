import asyncio
from types import SimpleNamespace

import httpx
import openai

from ugen_ai_gateway.providers.openai_compatible import OpenAICompatibleProvider


def _rejection() -> openai.BadRequestError:
    request = httpx.Request("POST", "http://litellm/v1/chat/completions")
    response = httpx.Response(400, request=request)
    return openai.BadRequestError(
        "litellm.UnsupportedParamsError: claude-haiku-5-5 does not support "
        "temperature=0.2. Only temperature=1 is supported.",
        response=response,
        body=None,
    )


class FakeClient:
    def __init__(self, reject: openai.BadRequestError | None) -> None:
        self.calls: list[dict] = []
        self._reject = reject
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if "temperature" in kwargs and self._reject is not None:
            raise self._reject
        return "ok"


def _complete(provider: OpenAICompatibleProvider, model: str = "claude-haiku-5-5"):
    return asyncio.run(provider.complete(model=model, messages=[], temperature=0.2))


def test_a_model_that_refuses_temperature_is_retried_without_it() -> None:
    client = FakeClient(_rejection())
    provider = OpenAICompatibleProvider(client, name="cloud", locality="cloud")  # type: ignore[arg-type]

    assert _complete(provider) == "ok"
    assert [("temperature" in call) for call in client.calls] == [True, False]

    # Remembered: the next request does not try the temperature again.
    assert _complete(provider) == "ok"
    assert [("temperature" in call) for call in client.calls] == [True, False, False]

    # Other models still get their temperature.
    assert _complete(provider, "other") == "ok"
    assert client.calls[-2]["model"] == "other" and "temperature" in client.calls[-2]


def test_other_bad_requests_are_not_retried() -> None:
    request = httpx.Request("POST", "http://x/v1/chat/completions")
    error = openai.BadRequestError(
        "model not found", response=httpx.Response(400, request=request), body=None
    )
    client = FakeClient(error)
    client._reject = error
    provider = OpenAICompatibleProvider(client, name="cloud", locality="cloud")  # type: ignore[arg-type]

    try:
        _complete(provider)
    except openai.BadRequestError:
        pass
    else:
        raise AssertionError("expected the error to propagate")
    assert len(client.calls) == 1
