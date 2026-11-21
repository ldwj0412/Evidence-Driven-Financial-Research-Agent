"""Supported question languages and Chinese names for display."""

import re

LANGUAGE_NAMES = {"en": "English", "zh": "Chinese"}
COMPANIES_ZH = {
    "MSFT": "微软",
    "AAPL": "苹果",
    "NVDA": "英伟达",
    "AMZN": "亚马逊",
    "GOOGL": "Alphabet",
    "META": "Meta",
    "TSLA": "特斯拉",
    "COST": "开市客",
}
METRICS_ZH = {
    "revenue": "营收",
    "gross_profit": "毛利润",
    "operating_income": "营业利润",
    "net_income": "净利润",
    "diluted_eps": "稀释每股收益",
}

_HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\U00020000-\U000323af]")
_QUOTED = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"]*"')
_ENGLISH_QUESTION = re.compile(
    r"^\s*(?:what|why|when|where|which|who|how|is|was|are|were|did|do|does|has|have|"
    r"can|could|compare|check|verify|explain|show)\b",
    re.IGNORECASE,
)


def question_language(text: str) -> str:
    """Infer the language of the question prose, ignoring quoted material when possible."""
    prose = _QUOTED.sub(" ", text)
    if not re.search(r"[A-Za-z]", prose) and not _HAN.search(prose):
        prose = text
    han_count = len(_HAN.findall(prose))
    if not han_count:
        return "en"
    # An English question can use a Chinese company name or other short label.
    if _ENGLISH_QUESTION.match(prose) and len(re.findall(r"[A-Za-z]+", prose)) > han_count + 1:
        return "en"
    return "zh"
