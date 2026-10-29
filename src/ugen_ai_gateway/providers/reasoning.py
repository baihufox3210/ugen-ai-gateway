"""Asking a model to think more, less or not at all.

There is no single way: OpenAI-style APIs take ``reasoning_effort``; Qwen3 and
other open models served by vLLM, SGLang or TensorRT-LLM take
``chat_template_kwargs.enable_thinking``; Qwen3 also reads a ``/no_think`` or
``/think`` switch written in the prompt. Each model says which way it uses.
Nothing is sent unless one is chosen.
"""

from typing import Any, Literal

from pydantic import BaseModel

Level = Literal["default", "off", "low", "medium", "high"]
Style = Literal["none", "reasoning_effort", "chat_template", "no_think"]

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


class Reasoning(BaseModel):
    # How much the model thinks to answer; "default" leaves it to the server.
    level: Level = "default"
    # How this model is told. "none": send nothing, whatever the level.
    style: Style = "none"
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


def request_options(
    reasoning: Reasoning,
    messages: list[Any],
    *,
    effort: str | None = None,
    analysis: bool = False,
) -> tuple[dict[str, Any], list[Any]]:
    """The extra request parameters and the (possibly adjusted) messages that
    carry the thinking setting."""
    level = reasoning.level_for(effort, analysis=analysis)
    if reasoning.style == "none":
        # Nothing chosen for this model: a client's own effort passes through.
        if effort and not analysis:
            return {"reasoning_effort": effort}, messages
        return {}, messages
    if level == "default":
        return {}, messages
    if reasoning.style == "reasoning_effort":
        return {"reasoning_effort": "none" if level == "off" else level}, messages
    if reasoning.style == "chat_template":
        return {
            "extra_body": {"chat_template_kwargs": {"enable_thinking": level != "off"}}
        }, messages
    switch = " /no_think" if level == "off" else " /think"
    adjusted = list(messages)
    for index in range(len(adjusted) - 1, -1, -1):
        message = adjusted[index]
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            adjusted[index] = {**message, "content": message["content"] + switch}
            break
    return {}, adjusted
