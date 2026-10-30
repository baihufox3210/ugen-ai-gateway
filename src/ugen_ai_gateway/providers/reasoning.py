"""Asking a model to think more, less or not at all.

There is no single way, and a proxy in front of the model changes it again:

* ``chat_template``: Qwen3 and other open models served by vLLM, SGLang or
  TensorRT-LLM take ``chat_template_kwargs.enable_thinking`` in the request;
* ``extra_body``: the same field wrapped in ``extra_body``, which is how LiteLLM
  hands unknown fields on to its backend (sent bare, it fails with "unexpected
  keyword argument 'chat_template_kwargs'");
* ``no_think``: Qwen3 also reads ``/no_think`` and ``/think`` written in the
  prompt, which works through any proxy;
* ``reasoning_effort``: OpenAI-style APIs (GPT-5, o-series, LiteLLM backends).

A person only chooses a level. The provider tries the ways that suit the model
in turn, remembers the one that worked, and sends nothing if none does.
"""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

Level = Literal["default", "off", "low", "medium", "high"]
Style = Literal[
    "auto", "none", "reasoning_effort", "chat_template", "extra_body", "no_think"
]

# OpenAI's reasoning_effort values, as a level.
EFFORT_LEVELS: dict[str, Level] = {
    "none": "off",
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}

# Which ways to try, most likely first.
LOCAL_WAYS = ("chat_template", "extra_body", "no_think")
CLOUD_WAYS = ("reasoning_effort",)


class Reasoning(BaseModel):
    # How much the model thinks to answer; "default" leaves it to the server.
    level: Level = "default"
    # "auto": try the ways that suit the model. A way named here is tried first;
    # "none" sends nothing, whatever the level.
    style: Style = "auto"
    # The thinking starts in the prompt, so the answer holds only "...</think>".
    prefilled: bool = False

    def level_for(self, effort: str | None, *, analysis: bool = False) -> Level:
        """The level for one request: the analysis step never thinks, and a
        client's own ``reasoning_effort`` beats the model's setting."""
        if analysis:
            return "off"
        if effort in EFFORT_LEVELS:
            return EFFORT_LEVELS[effort]
        return self.level

    def thinks(self, effort: str | None = None) -> bool:
        """Whether the answer starts with thinking that has no opening tag."""
        return self.prefilled and self.level_for(effort) != "off"

    def thinking(
        self, effort: str | None = None, *, analysis: bool = False
    ) -> "Thinking | None":
        """What to ask of the model for one request, or None to ask nothing."""
        level = self.level_for(effort, analysis=analysis)
        if self.style == "none" or level == "default":
            return None
        return Thinking(level=level, style=self.style)


@dataclass(frozen=True)
class Thinking:
    """What a request asks of the model about thinking."""

    level: Level
    style: Style = "auto"


@dataclass(frozen=True)
class Way:
    """One way of asking: extra request fields and/or a note added to the prompt."""

    name: str
    fields: dict[str, Any]
    note: str = ""

    def messages(self, messages: list[Any]) -> list[Any]:
        if not self.note:
            return messages
        adjusted = list(messages)
        for index in range(len(adjusted) - 1, -1, -1):
            message = adjusted[index]
            if message.get("role") == "user" and isinstance(
                message.get("content"), str
            ):
                adjusted[index] = {**message, "content": message["content"] + self.note}
                break
        return adjusted


def _way(name: str, level: Level) -> Way:
    on = level != "off"
    if name == "reasoning_effort":
        return Way(name, {"reasoning_effort": "none" if level == "off" else level})
    template = {"chat_template_kwargs": {"enable_thinking": on}}
    if name == "chat_template":
        return Way(name, {"extra_body": template})
    if name == "extra_body":
        return Way(name, {"extra_body": {"extra_body": template}})
    return Way(name, {}, " /think" if on else " /no_think")


def ways(thinking: Thinking, *, local: bool) -> list[Way]:
    """The ways to ask for this, in the order to try them."""
    order = list(LOCAL_WAYS if local else CLOUD_WAYS)
    if thinking.style in order:
        order.remove(thinking.style)
        order.insert(0, thinking.style)
    elif thinking.style in (*LOCAL_WAYS, *CLOUD_WAYS):
        order.insert(0, thinking.style)
    return [_way(name, thinking.level) for name in order]
