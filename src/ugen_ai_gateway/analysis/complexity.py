"""How demanding a request is, judged from its text alone.

Keyword rules cannot tell "幫我設計一句生日祝福" from "幫我設計一個會員系統"
in every case, so they only decide when the signal is unambiguous: a greeting,
a one-line factual question, a short rewrite or translation, a definition, or
an explicit design, proof or algorithm task. Everything in between is marked
uncertain and left to the analysis model. A short follow-up ("第二點再詳細一點",
"continue") takes the difficulty of the request it follows.
"""

import re
from dataclasses import dataclass
from typing import Literal

Complexity = Literal["simple", "moderate", "complex"]


@dataclass(frozen=True)
class Assessment:
    complexity: Complexity
    # True when the rules are sure; otherwise the analysis model decides.
    certain: bool
    reason: str
    # A follow-up that only makes sense together with the previous message.
    follow_up: bool = False


def _any(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(patterns), re.IGNORECASE)


# Demanding on their own: design and architecture, proofs, algorithms,
# debugging, planning under constraints.
_HARD = _any(
    r"architect(?:ure|ural)|system design|trade[ -]?offs?|step[ -]?by[ -]?step|"
    r"comprehensive|in[ -]depth|thorough|multi[ -]?step|refactor|debug|"
    r"distributed|scalab|high[ -]availability|concurren|race condition|deadlock|"
    r"memory leak|time complexity|big[ -]o\b|\bprove\b|\bproof\b|theorem|derive|"
    r"\b(?:design|build|implement|architect|write|create|develop) (?:a|an|the|my|our) "
    r"(?:[\w-]+ ){0,3}(?:system|service|api|backend|database|schema|architecture|"
    r"pipeline|protocol|limiter|cache|queue|platform|compiler|interpreter|engine|"
    r"scheduler|parser|framework|microservices?|app|application)\b|"
    r"\bimplement(?:ing)? (?:an? |the )?(?:[\w-]+ ){0,3}(?:tree|graph|heap|trie|"
    r"cache|queue|algorithm|sort|search|hash ?map|linked list|lru|lfu|parser)\b|"
    r"\b(?:lru|lfu) cache\b|re-?render|stack ?trace|traceback|segfault|"
    r"business plan|go-to-market|financial model|due diligence|"
    r"架構(?!是什麼|是甚麼|的意思)|取捨|權衡|逐步|一步一步|多步驟|重構|除錯|偵錯|"
    r"完整方案|全面分析|深入分析|詳細分析|分散式|高可用|高併發|併發|競態|死結|記憶體洩漏|"
    r"時間複雜度|證明|推導|演算法|資料結構|商業計畫|營運計畫|可行性(?:分析|評估)|"
    r"風險評估|預算(?:規劃|編列)|時程(?:規劃|安排)|"
    r"(?:設計|建置|規劃|開發|實作|架設)[^，。、；：！？,.;:!?\n]{0,24}?"
    r"(?:系統|資料庫|API|後端|架構|演算法|平台|服務|網站|App|應用程式|微服務|流程)",
)
# Everyday task words: demanding in some requests, casual in others.
_TASK_WORDS = _any(
    r"\b(?:designs?|designed|designing|compar(?:e|es|ed|ing|isons?)|"
    r"evaluat(?:e|es|ed|ing|ions?)|analy(?:[sz]e|[sz]es|[sz]ed|[sz]ing|sis|ses)|"
    r"implement(?:s|ed|ing|ations?)?|optimi[sz](?:e|es|ed|ing|ations?)|"
    r"strateg(?:y|ies)|plan(?:s|ning)?|explain|why|how (?:does|do|can|should|would)|"
    r"pros and cons|difference between|recommend)\b|"
    r"設計|比較|評估|規劃|策略|整合|優化|分析|解釋|說明|為什麼|為何|怎麼做|如何|"
    r"優缺點|差別|差異|建議|計畫|計劃",
)
# Short tasks a small model does well whatever their topic.
_LIGHT_TASK = _any(
    r"\btranslat|\bin (?:english|chinese|japanese)\b|翻譯|譯成|翻成|換成(?:英文|中文|日文)|"
    r"\b(?:rewrite|rephrase|paraphrase|polish|proofread|shorten|simplify)\b|"
    r"\bfix (?:the |my )?(?:typos?|grammar|spelling)|\bmake it (?:shorter|more formal|"
    r"friendlier|polite)|潤飾|潤稿|改寫|換句話說|修飾|錯字|文法|語氣|口吻|縮短|精簡|"
    r"\b(?:summari[sz]e|tl;?dr)\b|摘要|總結|重點整理|"
    r"祝福|賀詞|生日|卡片|標語|slogan|caption|hashtag|取名|起名|名字|暱稱|"
    r"\bname (?:ideas|suggestions)|\bbirthday|\bwish(?:es)?\b|\bjoke\b|笑話|"
    r"一句話|一句|一行|簡短|短短",
)
# Asking what something is: simple even when the topic is not.
_DEFINITION = _any(
    r"^\s*(?:what(?:'s| is| are| does)|who (?:is|was)|define|meaning of)\b(?!.*"
    r"\b(?:difference|between|compare|versus|vs\.?)\b)",
    r"^\s*(?:什麼是|甚麼是|何謂|請問什麼是)",
    r"(?:是什麼|是甚麼|是誰|的意思|什麼意思|甚麼意思|的定義)\s*[?？。!！]*\s*$",
)
_CHITCHAT = _any(
    r"^\s*(?:hi|hello|hey|yo|good (?:morning|afternoon|evening|night)|thanks?(?: you)?|"
    r"thx|ok(?:ay)?|cool|great|nice|got it|bye|你好|您好|哈囉|嗨|早安|午安|晚安|謝謝|"
    r"感謝|好的|好|了解|收到|沒問題|掰掰|再見|讚)[\s!！.。~～?？,，]*$",
    r"^\s*(?:你是誰|你叫什麼|who are you|what can you do|你會什麼|你能做什麼)"
    r"[\s?？!！。]*$",
)
# A message that only makes sense with the one before it.
_FOLLOW_UP = _any(
    r"^\s*(?:繼續|接著|然後呢|還有呢|再來|下一步|go on|continue|keep going|and then|"
    r"next)[\s!！.。?？]*$",
    r"詳細|展開|再.{0,6}(?:說明|解釋|一點|一些|多一點)|第[一二三四五六七八九十\d]+(?:點|步|項|部分|段|個)|"
    r"那如果|如果改|改用|改成|換用|那(?:要|該)?怎麼|為什麼要這樣|這(?:個|樣|段)|上面|剛剛|剛才|"
    r"\bmore detail|\belaborate|\bexpand\b|\bstep \d|\bwhat about\b|\bhow about\b|"
    r"\band if\b|\bwhat if\b|\binstead\b|\bthat\b|\bthis one\b|\babove\b|\bpart \d",
)
_ACKNOWLEDGEMENT = _any(
    r"^\s*(?:thanks?(?: you| a lot| so much)?|thx|ok(?:ay)?|great|perfect|got it|cool|"
    r"nice|謝謝|感謝|好的|好|了解|收到|讚|太好了|沒問題|懂了)[\s!！.。~～,，]*"
    r"(?:謝謝|thanks)?[\s!！.。~～]*$",
)
_CODE = re.compile(
    r"```|^\s{4,}\S|\b(?:def|class|function|import|return)\b|[{};]\s*$", re.MULTILINE
)
_LIST_ITEM = re.compile(r"(?:^|\n)\s*(?:\d+[.)、]|[-*•])\s*\S")
_CJK = re.compile(r"[㐀-鿿豈-﫿]")


def _size(text: str) -> int:
    """Length with each CJK character counted twice (Chinese says more per
    character)."""
    return len(text) + len(_CJK.findall(text))


def assess_complexity(text: str, previous: str | None = None) -> Assessment:
    """Judge ``text``, the latest user message; ``previous`` is the user
    message before it, if any."""
    stripped = text.strip()
    size = _size(stripped)

    if previous and size <= 60:
        if _ACKNOWLEDGEMENT.search(stripped):
            return Assessment("simple", True, "An acknowledgement.")
        if _FOLLOW_UP.search(stripped) and not _LIGHT_TASK.search(stripped):
            earlier = assess_complexity(previous)
            return Assessment(
                earlier.complexity,
                earlier.certain,
                "A follow-up to the previous request, judged with it.",
                follow_up=True,
            )

    if _CHITCHAT.search(stripped):
        return Assessment("simple", True, "Small talk.")

    hard = _HARD.search(stripped)
    has_code = bool(_CODE.search(stripped))
    definition = bool(_DEFINITION.search(stripped)) and size <= 80

    if definition and not has_code:
        return Assessment("simple", True, "Asks what something is.")
    if hard and not (_LIGHT_TASK.search(stripped) and size <= 120):
        return Assessment(
            "complex", True, "Design, proof, algorithm or debugging work."
        )

    light = bool(_LIGHT_TASK.search(stripped))
    task_words = {word.lower() for word in _TASK_WORDS.findall(stripped)}
    list_items = len(_LIST_ITEM.findall(stripped))

    if light and not has_code and size <= 600 and len(task_words) <= 1:
        return Assessment("simple", True, "A short rewrite, translation or summary.")

    # Uncertain: the analysis model decides. Best guess for rules-only mode.
    if size >= 1400 or list_items >= 4 or len(task_words) >= 3:
        guess: Complexity = "complex"
    elif has_code or size >= 280 or task_words:
        guess = "moderate"
    else:
        guess = "simple"
    return Assessment(guess, False, "No clear signal; judged by the analysis model.")
