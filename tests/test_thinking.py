import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_gateway import FakeProvider
from ugen_ai_gateway.analysis.analyzer import RequestAnalyzer
from ugen_ai_gateway.analysis.thinking import ThinkingFilter, strip_thinking
from ugen_ai_gateway.providers.reasoning import Reasoning, request_options

ANALYSIS = (
    '{"complexity": "simple", "task_type": "general", "contains_personal_data": false,'
    ' "personal_data_types": [], "personal_data_values": [], "reason": "ok"}'
)


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        ("<think>hmm</think>\n\nHello", "Hello"),
        ("<THINK>hmm</THINK>Hello", "Hello"),
        ("<think>a</think>one<think>b</think> two", "one two"),
        ("<thinking>a</thinking>Hi", "Hi"),
        ("[THINK]a[/THINK]Hi", "Hi"),
        ("let me think...\n</think>\n\nThe answer", "The answer"),  # no opening tag
        ("<think>never finished", ""),
        ("no thinking here", "no thinking here"),
        ("a < b and b > c", "a < b and b > c"),
    ],
)
def test_thinking_is_removed_from_a_whole_answer(text: str, answer: str) -> None:
    assert strip_thinking(text) == answer


def _stream(chunks: list[str], **kwargs: Any) -> str:
    filtered = ThinkingFilter(**kwargs)
    return "".join(filtered.feed(chunk) for chunk in chunks) + filtered.flush()


def test_streamed_thinking_is_removed_wherever_the_tags_are_split() -> None:
    text = "<think>plan the answer</think>\n\nHello <b>world</b>"
    for size in (1, 2, 3, 5, 7, len(text)):
        chunks = [text[i : i + size] for i in range(0, len(text), size)]
        assert _stream(chunks) == "Hello <b>world</b>", size


def test_a_model_whose_template_opens_the_thinking_ends_it_with_a_lone_tag() -> None:
    chunks = ["Okay, the user wants", " a greeting.</th", "ink>\n\nHi there"]

    assert _stream(chunks, start_inside=True) == "Hi there"
    # Without knowing that, the thinking is shown first and then taken back.
    unknowing = ThinkingFilter()
    shown = [unknowing.feed(chunk) for chunk in chunks]
    assert shown[0] and unknowing.retracted and shown[-1] == "Hi there"


def test_text_shown_before_a_lone_closing_tag_is_taken_back() -> None:
    filtered = ThinkingFilter()
    first = filtered.feed("Okay, the user says hi.")
    assert first == "Okay, the user says hi." and not filtered.retracted

    assert filtered.feed("</think>\n\nHello") == "Hello" and filtered.retracted


def test_the_client_is_told_to_discard_thinking_it_was_already_sent() -> None:
    service, _, _ = _service(["Okay, the user", " says hi.</think>", "\n\nHel", "lo"])

    events = _chat(service, stream=True)

    kinds = [e.type for e in events]
    assert "reset" in kinds
    after = "".join(
        e.data["content"] for e in events[kinds.index("reset") :] if e.type == "delta"
    )
    assert after == "Hello" and events[-1].response.content == "Hello"


def test_text_that_only_looks_like_a_tag_is_kept() -> None:
    assert _stream(["x <thi", "s is fine"]) == "x <this is fine"
    assert _stream(["1 <", "2"]) == "1 <2"


def test_thinking_is_dropped_when_the_stream_ends_inside_it() -> None:
    assert _stream(["<think>still going"]) == ""


@pytest.mark.parametrize("style", ["reasoning_effort", "chat_template", "no_think"])
def test_each_style_turns_the_level_into_what_that_server_understands(
    style: str,
) -> None:
    messages = [{"role": "user", "content": "hi"}]
    off, off_messages = request_options(Reasoning(level="off", style=style), messages)
    high, high_messages = request_options(
        Reasoning(level="high", style=style), messages
    )

    if style == "reasoning_effort":
        assert (off, high) == (
            {"reasoning_effort": "none"},
            {"reasoning_effort": "high"},
        )
    elif style == "chat_template":
        assert off == {
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
        }
        assert high == {
            "extra_body": {"chat_template_kwargs": {"enable_thinking": True}}
        }
    else:
        assert off == high == {}
        assert off_messages[0]["content"] == "hi /no_think"
        assert high_messages[0]["content"] == "hi /think"
    assert messages == [{"role": "user", "content": "hi"}]  # not changed in place


def test_nothing_is_sent_until_a_style_and_level_are_chosen() -> None:
    messages = [{"role": "user", "content": "hi"}]

    assert request_options(Reasoning(), messages) == ({}, messages)
    assert request_options(Reasoning(style="chat_template"), messages) == ({}, messages)
    assert request_options(Reasoning(level="high"), messages) == ({}, messages)


def test_the_analysis_step_never_thinks_and_a_clients_effort_wins() -> None:
    messages = [{"role": "user", "content": "hi"}]
    chat = Reasoning(level="high", style="chat_template")

    analysis, _ = request_options(chat, messages, analysis=True)
    assert analysis["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    client, _ = request_options(chat, messages, effort="none")
    assert client["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    # With no style chosen, the client's own effort goes to the server as given.
    assert request_options(Reasoning(), messages, effort="low")[0] == {
        "reasoning_effort": "low"
    }


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


def test_the_analysis_is_sent_with_thinking_off_and_room_to_answer() -> None:
    _, call = _analyze(
        ANALYSIS,
        reasoning=Reasoning(level="high", style="chat_template"),
        max_tokens=512,
    )

    assert call["max_tokens"] == 512
    assert call["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def _service(chunks: list[str] | None = None, *, reply: str = "hello", **config: Any):
    from tests.test_gateway import make_service, text_chunk

    local = FakeProvider()
    local.completion.choices[0].message.content = reply
    if chunks is not None:
        local.chunks = [text_chunk(chunk) for chunk in chunks]
    service, _, cloud, _ = make_service(local_provider=local, output_pii_action="off")
    model = service._engine.config.providers[0].models[0]
    for name, value in config.items():
        if name == "hide_thinking":
            service._engine.config.hide_thinking = value
        else:
            setattr(model, name, value)
    return service, local, cloud


def _chat(service: Any, *, stream: bool, **fields: Any):
    from ugen_ai_gateway.api.schemas import ChatRequest

    request = ChatRequest(messages=[{"role": "user", "content": "hi"}], **fields)

    async def run():
        return [event async for event in service.events(request, stream=stream)]

    return asyncio.run(run())


def _deltas(events: list[Any]) -> str:
    return "".join(e.data["content"] for e in events if e.type == "delta")


def test_the_answer_is_shown_without_its_thinking_by_default() -> None:
    service, _, _ = _service(["<think>pla", "n it</th", "ink>\n\nHel", "lo"])

    events = _chat(service, stream=True)

    assert _deltas(events) == "Hello" and events[-1].response.content == "Hello"


def test_a_model_that_opens_its_thinking_in_the_prompt_is_hidden_from_the_start() -> (
    None
):
    reasoning = Reasoning(level="high", style="chat_template", prefilled=True)
    service, local, _ = _service(
        ["Okay, the user says hi", ".</think>\n\nHel", "lo"], reasoning=reasoning
    )

    events = _chat(service, stream=True)

    assert _deltas(events) == "Hello" and events[-1].response.content == "Hello"
    assert local.calls[-1]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": True}
    }


def test_thinking_can_be_left_in_the_answer() -> None:
    reply = "<think>plan</think>Hello"
    service, _, _ = _service(reply=reply, hide_thinking=False)

    assert _chat(service, stream=False)[-1].response.content == reply
    shown, _, _ = _service(reply=reply)
    assert _chat(shown, stream=False)[-1].response.content == "Hello"


def test_a_clients_reasoning_effort_reaches_the_model() -> None:
    service, local, _ = _service()

    _chat(service, stream=False, reasoning_effort="low")

    assert local.calls[-1]["reasoning_effort"] == "low"


def test_nothing_about_thinking_is_sent_by_default() -> None:
    service, local, _ = _service()

    _chat(service, stream=False)

    assert (
        "reasoning_effort" not in local.calls[-1]
        and "extra_body" not in local.calls[-1]
    )


def test_the_dashboards_thinking_switch_counts_only_for_a_model_with_a_style() -> None:
    styled = Reasoning(level="default", style="chat_template")
    service, local, _ = _service(reasoning=styled)

    _chat(service, stream=False, thinking="off")
    assert local.calls[-1]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }
    _chat(service, stream=False, thinking="high")
    assert local.calls[-1]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": True}
    }

    # A model with no style set is never sent anything the switch picked.
    plain, plain_local, _ = _service()
    _chat(plain, stream=False, thinking="low")
    assert "reasoning_effort" not in plain_local.calls[-1]
    assert "extra_body" not in plain_local.calls[-1]


def test_the_dashboard_is_told_when_a_model_can_be_asked_to_think(monkeypatch) -> None:
    from tests.test_accounts import AccountEnv, bearer

    env = AccountEnv(monkeypatch)
    token = env.token("alice")
    url = "/v1/ugen/config"

    assert (
        env.client.get(url, headers=bearer(token)).json()["thinking_control"] is False
    )
