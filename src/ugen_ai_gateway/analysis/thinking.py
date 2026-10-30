"""Thinking text in a model's answer.

Reasoning models write their thinking in the answer, in one of a few formats:

* ``<think> ... </think>`` before the answer (also ``<thinking>``,
  ``<reasoning>`` and Magistral's ``[THINK] ... [/THINK]``);
* only the closing tag, ``... </think> answer``, when the model's chat template
  already opened the block in the prompt (Qwen3.5, Qwen3 "Thinking-2507").

Servers started with a reasoning parser move the thinking into a separate field
and never show it here. For the rest, ``strip_thinking`` removes it from a whole
text and ``ThinkingFilter`` does the same while the text streams in.
"""

# (opening tag, closing tag), compared without regard to case.
TAGS: tuple[tuple[str, str], ...] = (
    ("<think>", "</think>"),
    ("<thinking>", "</thinking>"),
    ("<reasoning>", "</reasoning>"),
    ("[think]", "[/think]"),
)
_OPENERS = {open_: close for open_, close in TAGS}
_ALL_TAGS = [tag for pair in TAGS for tag in pair]


def split_thinking(text: str) -> tuple[str, str]:
    """(thinking, answer) of a whole reply."""
    filtered = ThinkingFilter()
    answer = filtered.feed(text) + filtered.flush()
    thinking = filtered.take_thinking()
    if filtered.retracted:
        thinking = filtered.retracted_text + thinking
    return thinking.strip(), answer.strip()


def strip_thinking(text: str) -> str:
    """``text`` without its thinking: complete blocks, a block cut off by the
    end of the text, and a thinking part that ends with a lone closing tag."""
    return split_thinking(text)[1]


class ThinkingFilter:
    """Remove thinking from streamed text, chunk by chunk.

    ``start_inside`` is for models whose template opens the thinking in the
    prompt: the text starts inside it and ends at the first closing tag.
    A closing tag with no opening one before it is dropped. If text was already
    passed on by then, it was thinking after all: ``retracted`` is set, and the
    caller tells the client to discard what it showed. ``start_inside`` avoids
    that by hiding the thinking from the start.
    """

    def __init__(self, *, start_inside: bool = False) -> None:
        self._close: str | None = "</think>" if start_inside else None
        self._buffer = ""
        self._trim = start_inside
        self._emitted = False
        self._shown: list[str] = []  # answer text passed on so far
        self._thinking: list[str] = []
        self.retracted = False
        # The text passed on that turned out to be thinking (see ``retracted``).
        self.retracted_text = ""

    def take_thinking(self) -> str:
        """The thinking removed since the last call."""
        text, self._thinking = "".join(self._thinking), []
        return text

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        return self._drain(final=False)

    def flush(self) -> str:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> str:
        out: list[str] = []
        while True:
            lowered = self._buffer.lower()
            if self._close is not None:
                end = lowered.find(self._close)
                if end == -1:
                    # Still thinking: keep only what could be the start of the tag.
                    keep = 0 if final else _partial_tail(lowered, [self._close])
                    cut = len(self._buffer) - keep
                    self._thinking.append(self._buffer[:cut])
                    self._buffer = self._buffer[cut:]
                    break
                self._thinking.append(self._buffer[:end])
                self._buffer = self._buffer[end + len(self._close) :]
                self._close = None
                # The blank lines after the thinking go; one inside an answer stays.
                self._trim = not self._emitted
                continue
            hit = min(
                ((lowered.find(tag), tag) for tag in _ALL_TAGS),
                key=lambda item: (item[0] == -1, item[0]),
            )
            index, tag = hit
            if index == -1:
                keep = 0 if final else _partial_tail(lowered, _ALL_TAGS)
                text = self._buffer[: len(self._buffer) - keep]
                self._buffer = self._buffer[len(text) :]
                out.append(self._emit(text))
                break
            out.append(self._emit(self._buffer[:index]))
            self._buffer = self._buffer[index + len(tag) :]
            if tag in _OPENERS:
                self._close = _OPENERS[tag]
            elif self._emitted:
                # A closing tag with no block open: what came before was thinking,
                # including what this very call has produced so far.
                out.clear()
                self.retracted_text = "".join(self._shown)
                self._shown = []
                self.retracted, self._emitted, self._trim = True, False, True
        return "".join(out)

    def _emit(self, text: str) -> str:
        if self._trim:
            text = text.lstrip()
            if text:
                self._trim = False
        if text:
            self._emitted = True
            self._shown.append(text)
        return text


def _partial_tail(lowered: str, tags: list[str]) -> int:
    """Length of the longest end of ``lowered`` that starts one of ``tags``."""
    longest = 0
    for tag in tags:
        for size in range(min(len(tag) - 1, len(lowered)), 0, -1):
            if lowered.endswith(tag[:size]):
                longest = max(longest, size)
                break
    return longest
