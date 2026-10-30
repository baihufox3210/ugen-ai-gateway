import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from tests.test_gateway import FakeProvider
from ugen_ai_gateway.analysis.analyzer import RequestAnalyzer
from ugen_ai_gateway.analysis.thinking import (
    ThinkingFilter,
    split_thinking,
    strip_thinking,
)
from ugen_ai_gateway.providers.openai_compatible import OpenAICompatibleProvider
from ugen_ai_gateway.providers.reasoning import Reasoning, Thinking, ways

ANALYSIS = (
    '{"complexity": "simple", "task_type": "general", "contains_personal_data": false,'
    ' "personal_data_types": [], "personal_data_values": [], "reason": "ok"}'
)


@pytest.mark.parametrize(
    ("text", "thinking", "answer"),
    [
        ("<think>hmm</think>\n\nHello", "hmm", "Hello"),
        ("<THINK>hmm</THINK>Hello", "hmm", "Hello"),
        ("<think>a</think>one<think>b</think> two", "ab", "one two"),
        ("<thinking>a</thinking>Hi", "a", "Hi"),
        ("[THINK]a[/THINK]Hi", "a", "Hi"),
        ("let me think...\n</think>\n\nThe answer", "let me think...", "The answer"),
        ("<think>never finished", "never finished", ""),
        ("no thinking here", "", "no thinking here"),
        ("a < b and b > c", "", "a < b and b > c"),
    ],
)
def test_thinking_is_told_apart_from_the_answer(
    text: str, thinking: str, answer: str
) -> None:
    assert split_thinking(text) == (thinking, answer)
    assert strip_thinking(text) == answer


def _stream(chunks: list[str], **kwargs: Any) -> tuple[str, str]:
    filtered = ThinkingFilter(**kwargs)
    answer = "".join(filtered.feed(chunk) for chunk in chunks) + filtered.flush()
    return answer, filtered.take_thinking()


def test_streamed_thinking_is_separated_wherever_the_tags_are_split() -> None:
    text = "<think>plan the answer</think>\n\nHello <b>world</b>"
    for size in (1, 2, 3, 5, 7, len(text)):
        chunks = [text[i : i + size] for i in range(0, len(text), size)]
        assert _stream(chunks) == ("Hello <b>world</b>", "plan the answer"), size


def test_a_model_whose_template_opens_the_thinking_ends_it_with_a_lone_tag() -> None:
    chunks = ["Okay, the user wants", " a greeting.</th", "ink>\n\nHi there"]

    assert _stream(chunks, start_inside=True) == (
        "Hi there",
        "Okay, the user wants a greeting.",
    )
    # Without knowing that, the thinking is shown first and then taken back.
    unknowing = ThinkingFilter()
    shown = [unknowing.feed(chunk) for chunk in chunks]
    assert shown[0] and unknowing.retracted and shown[-1] == "Hi there"
    assert unknowing.retracted_text == "Okay, the user wants a greeting."


def test_text_that_only_looks_like_a_tag_is_kept() -> None:
    assert _stream(["x <thi", "s is fine"]) == ("x <this is fine", "")
    assert _stream(["1 <", "2"]) == ("1 <2", "")


def test_thinking_is_kept_when_the_stream_ends_inside_it() -> None:
    assert _stream(["<think>still going"]) == ("", "still going")


# --- how a level reaches the server -----------------------------------------


def test_the_ways_of_asking_depend_on_where_the_model_runs() -> None:
    off = Thinking(level="off")

    local = [way.name for way in ways(off, local=True)]
    cloud = [way.name for way in ways(off, local=False)]

    assert local == ["chat_template", "extra_body", "no_think"]
    assert cloud == ["reasoning_effort"]
    assert (
        ways(Thinking(level="high", style="no_think"), local=True)[0].name == "no_think"
    )


def test_each_way_turns_the_level_into_what_that_server_understands() -> None:
    by_name = {way.name: way for way in ways(Thinking(level="off"), local=True)}

    assert by_name["chat_template"].fields == {
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
    }
    assert by_name["extra_body"].fields == {
        "extra_body": {
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
        }
    }
    messages = [{"role": "user", "content": "hi"}]
    assert by_name["no_think"].messages(messages)[0]["content"] == "hi /no_think"
    assert messages == [{"role": "user", "content": "hi"}]  # not changed in place
    high = ways(Thinking(level="high"), local=False)[0]
    assert high.fields == {"reasoning_effort": "high"}
    assert ways(Thinking(level="off"), local=False)[0].fields == {
        "reasoning_effort": "none"
    }


def test_nothing_is_asked_until_a_level_is_chosen() -> None:
    assert Reasoning().thinking() is None
    assert Reasoning(level="high", style="none").thinking() is None
    assert Reasoning(level="off").thinking() == Thinking(level="off", style="auto")
    # The analysis step and the memory never think; a client's effort beats the setting.
    assert Reasoning(level="high").thinking(analysis=True).level == "off"
    assert Reasoning(level="high").thinking("none").level == "off"
    assert Reasoning().thinking("low").level == "low"


# --- a server (or proxy) that refuses some of the ways ----------------------


def _status_error(status: int, message: str) -> openai.APIStatusError:
    request = httpx.Request("POST", "http://litellm/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return openai.APIStatusError(message, response=response, body=None)


class ProxyClient:
    """Like LiteLLM: a bare chat_template_kwargs is an error, but one wrapped in
    extra_body reaches the model."""

    def __init__(self, accepts: set[str]) -> None:
        self.calls: list[dict[str, Any]] = []
        self.accepts = accepts
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        body = kwargs.get("extra_body") or {}
        if "chat_template_kwargs" in body and "chat_template" not in self.accepts:
            raise _status_error(
                500,
                "litellm.InternalServerError: AsyncCompletions.create() got an "
                "unexpected keyword argument 'chat_template_kwargs'",
            )
        if "extra_body" in body and "extra_body" not in self.accepts:
            raise _status_error(400, "extra_body is not permitted")
        last = kwargs["messages"][-1]["content"]
        if last.endswith("/no_think") and "no_think" not in self.accepts:
            raise _status_error(400, "bad request")
        return "answered"


def _ask(provider: OpenAICompatibleProvider, thinking: Thinking | None) -> str:
    return asyncio.run(
        provider.complete(
            model="qwen",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.0,
            thinking=thinking,
        )
    )


def test_the_way_that_works_through_litellm_is_found_and_remembered() -> None:
    client = ProxyClient(accepts={"extra_body"})
    provider = OpenAICompatibleProvider(client, name="local", locality="local")  # type: ignore[arg-type]

    assert _ask(provider, Thinking(level="off")) == "answered"
    # The bare field failed as in the log, then the wrapped one went through.
    assert [("extra_body" in c.get("extra_body", {})) for c in client.calls] == [
        False,
        True,
    ]

    assert _ask(provider, Thinking(level="off")) == "answered"
    assert len(client.calls) == 3  # no second round of trying


def test_a_server_that_takes_the_bare_field_needs_one_call() -> None:
    client = ProxyClient(accepts={"chat_template"})
    provider = OpenAICompatibleProvider(client, name="local", locality="local")  # type: ignore[arg-type]

    _ask(provider, Thinking(level="off"))

    assert len(client.calls) == 1
    assert client.calls[0]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


def test_the_prompt_switch_is_the_last_resort() -> None:
    client = ProxyClient(accepts={"no_think"})
    provider = OpenAICompatibleProvider(client, name="local", locality="local")  # type: ignore[arg-type]

    assert _ask(provider, Thinking(level="off")) == "answered"
    assert client.calls[-1]["messages"][-1]["content"] == "hi /no_think"


def test_when_no_way_works_the_request_goes_without_and_is_not_tried_again() -> None:
    client = ProxyClient(accepts=set())
    provider = OpenAICompatibleProvider(client, name="local", locality="local")  # type: ignore[arg-type]

    assert _ask(provider, Thinking(level="off")) == "answered"
    assert (
        "extra_body" not in client.calls[-1]
        and client.calls[-1]["messages"][-1]["content"] == "hi"
    )
    calls = len(client.calls)
    assert _ask(provider, Thinking(level="off")) == "answered"
    assert len(client.calls) == calls + 1


def test_a_real_failure_is_not_taken_for_a_refused_field() -> None:
    client = ProxyClient(accepts={"chat_template"})

    async def broken(**kwargs: Any) -> str:
        raise _status_error(500, "the model server crashed")

    client.chat.completions.create = broken
    provider = OpenAICompatibleProvider(client, name="local", locality="local")  # type: ignore[arg-type]

    with pytest.raises(openai.APIStatusError):
        _ask(provider, Thinking(level="off"))


# --- the analysis step --------------------------------------------------------


def _analyze(
    content: str | None, *, reasoning_content: str | None = None, **kwargs: Any
):
    provider = FakeProvider()
    provider.completion.choices[0].message = SimpleNamespace(
        content=content, reasoning_content=reasoning_content
    )
    result = asyncio.run(
        RequestAnalyzer(provider=provider, model="qwen", **kwargs).analyze("hello")  # type: ignore[arg-type]
    )
    return result, provider.calls[-1]


@pytest.mark.parametrize(
    "reply",
    [
        ANALYSIS,
        f"<think>The user says hello.</think>\n\n{ANALYSIS}",
        f"The user says hello, so it is simple.\n</think>\n\n{ANALYSIS}",
        f"```json\n{ANALYSIS}\n```",
        f"Sure! Here is the result: {ANALYSIS} Hope that helps.",
        f"<think>maybe {{'a': 1}} or {{\"x\": 2}}</think>{ANALYSIS}",
    ],
)
def test_the_analysis_is_found_after_thinking_text_and_around_fences(
    reply: str,
) -> None:
    result, _ = _analyze(reply)

    assert result.complexity == "simple" and result.task_type == "general"


def test_the_analysis_falls_back_to_a_separate_reasoning_field() -> None:
    result, _ = _analyze("", reasoning_content=f"thinking... final: {ANALYSIS}")

    assert result.complexity == "simple"


@pytest.mark.parametrize(
    "reply", ["", "<think>cut off while thinking", "no json at all"]
)
def test_a_reply_without_an_analysis_is_still_an_error(reply: str) -> None:
    with pytest.raises(ValueError, match="invalid analysis result"):
        _analyze(reply)


def test_the_analysis_never_thinks_and_has_room_to_answer() -> None:
    _, call = _analyze(ANALYSIS, reasoning=Reasoning(level="high"), max_tokens=512)

    assert call["max_tokens"] == 512
    assert call["thinking"] == Thinking(level="off", style="auto")
    _, plain = _analyze(ANALYSIS, reasoning=Reasoning(style="none"))
    assert plain["thinking"] is None


# --- answers ----------------------------------------------------------------


def _service(chunks: list[str] | None = None, *, reply: str = "hello", **config: Any):
    from tests.test_gateway import make_service, text_chunk

    local = FakeProvider()
    local.completion.choices[0].message.content = reply
    if chunks is not None:
        local.chunks = [text_chunk(chunk) for chunk in chunks]
    service, _, cloud, _ = make_service(local_provider=local, output_pii_action="off")
    model = service._engine.config.providers[0].models[0]
    for name, value in config.items():
        if name == "show_thinking":
            service._engine.config.show_thinking = value
        else:
            setattr(model, name, value)
    return service, local, cloud


def _chat(service: Any, *, stream: bool, **fields: Any):
    from ugen_ai_gateway.api.schemas import ChatRequest

    request = ChatRequest(messages=[{"role": "user", "content": "hi"}], **fields)

    async def run():
        return [event async for event in service.events(request, stream=stream)]

    return asyncio.run(run())


def _deltas(events: list[Any], kind: str = "delta") -> str:
    return "".join(e.data["content"] for e in events if e.type == kind)


def test_the_answer_and_its_thinking_are_sent_apart() -> None:
    service, _, _ = _service(["<think>pla", "n it</th", "ink>\n\nHel", "lo"])

    events = _chat(service, stream=True)

    assert _deltas(events) == "Hello" and _deltas(events, "thinking_delta") == "plan it"
    response = events[-1].response
    assert response.content == "Hello" and response.reasoning == "plan it"


def test_thinking_a_server_sends_in_its_own_field_is_kept_apart_too() -> None:
    from tests.test_gateway import text_chunk

    service, local, _ = _service()
    thought = text_chunk("")
    thought.choices[0].delta.content = None
    thought.choices[0].delta.reasoning_content = "weighing options"
    local.chunks = [thought, text_chunk("Hello")]

    events = _chat(service, stream=True)

    assert _deltas(events, "thinking_delta") == "weighing options"
    assert events[-1].response.content == "Hello"
    assert events[-1].response.reasoning == "weighing options"


def test_a_model_that_opens_its_thinking_in_the_prompt_needs_no_taking_back() -> None:
    reasoning = Reasoning(level="high", prefilled=True)
    service, _, _ = _service(
        ["Okay, the user says hi", ".</think>\n\nHel", "lo"], reasoning=reasoning
    )

    events = _chat(service, stream=True)

    assert "reset" not in [e.type for e in events]
    assert _deltas(events) == "Hello"
    assert events[-1].response.reasoning == "Okay, the user says hi."


def test_the_thinking_shown_as_the_answer_is_taken_back_and_shown_as_thinking() -> None:
    service, _, _ = _service(["Okay, the user", " says hi.</think>", "\n\nHel", "lo"])

    events = _chat(service, stream=True)

    kinds = [e.type for e in events]
    assert "reset" in kinds
    after = "".join(
        e.data["content"] for e in events[kinds.index("reset") :] if e.type == "delta"
    )
    assert after == "Hello"
    assert events[-1].response.content == "Hello"
    assert events[-1].response.reasoning == "Okay, the user says hi."


def test_the_thinking_of_a_whole_answer_is_returned_apart_from_it() -> None:
    service, _, _ = _service(reply="<think>plan</think>Hello")

    response = _chat(service, stream=False)[-1].response

    assert response.content == "Hello" and response.reasoning == "plan"


def test_thinking_can_be_left_out_entirely() -> None:
    service, _, _ = _service(reply="<think>plan</think>Hello", show_thinking=False)

    response = _chat(service, stream=False)[-1].response

    assert response.content == "Hello" and response.reasoning is None


def test_a_clients_reasoning_effort_reaches_the_model_as_a_level() -> None:
    service, local, _ = _service()

    _chat(service, stream=False, reasoning_effort="low")

    assert local.calls[-1]["thinking"] == Thinking(level="low", style="auto")


def test_the_dashboards_thinking_switch_is_a_level_too() -> None:
    service, local, _ = _service()

    _chat(service, stream=False, thinking="off")
    assert local.calls[-1]["thinking"] == Thinking(level="off", style="auto")
    _chat(service, stream=False)
    assert local.calls[-1]["thinking"] is None  # nothing chosen: nothing asked


def test_nothing_about_thinking_is_sent_by_default() -> None:
    service, local, _ = _service()

    _chat(service, stream=False)

    assert local.calls[-1]["thinking"] is None


def test_the_administrators_system_prompt_is_not_sent_to_the_analysis_model() -> None:
    from tests.test_gateway import FakeAnalyzer, make_service
    from ugen_ai_gateway.api.schemas import ChatRequest

    analyzer = FakeAnalyzer()
    service, _, _, _ = make_service(analyzer=analyzer, analysis_mode="llm")
    request = ChatRequest(
        messages=[
            {"role": "system", "content": "You are Kiri, the assistant. " * 40},
            {"role": "system", "content": "Earlier: we talked about rotas."},
            {"role": "user", "content": "what can you do"},
        ],
        trusted_messages=1,
    )

    asyncio.run(service._analyze(request, "what can you do"))

    sent = " ".join(analyzer.inputs)
    assert "You are Kiri" not in sent
    assert (
        "rotas" in sent and "what can you do" in sent
    )  # memory and the message still are


def test_the_diagnosis_command_shows_each_call_and_the_raw_answer(
    monkeypatch, capsys
) -> None:
    from tests.test_gateway import make_service
    from ugen_ai_gateway import cli, dependencies

    service, _, _, _ = make_service(analysis_mode="llm")
    provider = FakeProvider()
    provider.completion.choices[0].message = SimpleNamespace(
        content="Thinking Process: ...", reasoning_content=None
    )
    provider.completion.choices[0].finish_reason = "length"
    service._analyzer = RequestAnalyzer(provider=provider, model="qwen", max_tokens=64)  # type: ignore[arg-type]
    monkeypatch.setattr(dependencies, "get_gateway_service", lambda: service)
    monkeypatch.setattr(dependencies, "get_usage_store", lambda: None)

    asyncio.run(
        cli._diagnose_analysis(["hello"], with_system_prompt=True, always_ask=True)
    )

    shown = capsys.readouterr().out
    assert "finish_reason=length" in shown and "cut off by the token limit" in shown
    assert "Thinking Process: ..." in shown and "FAILED" in shown
    assert "'max_tokens': 64" in shown
