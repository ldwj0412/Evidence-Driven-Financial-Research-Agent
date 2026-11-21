import re
import unicodedata
from datetime import UTC, date, datetime
from decimal import Decimal

from .domain import Attributions, Claim, Document, Fact, Parsed, Period
from .language import METRICS_ZH, question_language
from .providers import ProviderError
from .sec import stable_id

PROMPTS = {
    "revenue": "Did Microsoft’s revenue growth accelerate in FY2024?",
    "conflict": "Why do two sources say Microsoft’s FY2024 revenue grew 16% and 15%?",
    "rates": "Did interest rates explain Microsoft’s FY2024 revenue growth acceleration?",
    "event": "What happened when Microsoft reported FY2024 earnings on July 30, 2024?",
    "profit": "Did Microsoft’s profit increase?",
    "operating": "What about operating income?",
    "missing": "What about FY2025?",
}
PROMPTS_ZH = {
    "revenue": "微软 FY2024 的营收增速是否加快？",
    "conflict": "为什么两个来源分别称微软 FY2024 的营收增长了 16% 和 15%？",
    "rates": "利率能否解释微软 FY2024 营收增速加快？",
    "event": "微软在 2024 年 7 月 30 日发布 FY2024 财报时发生了什么？",
    "profit": "微软的利润增加了吗？",
    "operating": "营业利润呢？",
    "missing": "FY2025 呢？",
}
EXAMPLES = {
    "Revenue growth": "revenue",
    "Source conflict": "conflict",
    "Rates and earnings": "rates",
    "Company event": "event",
    "Clarify profit": "profit",
}
FILING = "https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm"
RELEASE = "https://www.sec.gov/Archives/edgar/data/789019/000095017024087835/msft-ex99_1.htm"
NEWS = "https://www.axios.com/2024/07/30/microsoft-stock-earnings-ai-azure-cloud"
FROZEN_AT = datetime(2024, 8, 15, tzinfo=UTC)
_MEASURES = {
    "revenue": (198270, 211915, 245122),
    "gross_profit": (135620, 146052, 171008),
    "operating_income": (83383, 88523, 109433),
    "net_income": (72738, 72361, 88136),
    "diluted_eps": ("9.65", "9.68", "11.80"),
}
_RELEASE_TEXT = "Revenue was $245.1 billion and increased 16% (up 15% in constant currency)"
_EVENT_TEXT = "Microsoft Corp. today announced the following results for the fiscal year ended June 30, 2024"


def _key(text, chinese=False):
    text = unicodedata.normalize("NFKC", text).replace("’", "'").lower()
    return "".join(text.split()).rstrip("?。") if chinese else " ".join(text.split())


def _base(metric="revenue", year=2024, measure="acceleration", label=None):
    return Claim(
        id="c1",
        label=label or "Microsoft’s revenue growth accelerated",
        kind="financial",
        entities=["MSFT"],
        metric=metric,
        period=Period(year=year),
        measure=measure,
        assertion="positive",
    )


def parse_fixture(question, previous=None, answers=None):
    chinese = question_language(question) == "zh"
    question, answers = _key(question, chinese), answers or {}
    prompts = {key: _key(text, chinese) for key, text in (PROMPTS_ZH if chinese else PROMPTS).items()}
    revenue_label = "微软的营收增速加快" if chinese else None
    if question == prompts["revenue"]:
        return Parsed(claims=[_base(label=revenue_label)])
    if question == prompts["conflict"]:
        return Parsed(
            claims=[
                Claim(
                    id="c1",
                    label="16% 和 15% 两个增幅互相矛盾"
                    if chinese
                    else "The 16% and 15% growth figures contradict each other",
                    kind="conflict",
                    entities=["MSFT"],
                    metric="revenue",
                    period=Period(year=2024),
                    measure="growth",
                    reported_values=[Decimal(16), Decimal(15)],
                )
            ]
        )
    if question == prompts["rates"]:
        return Parsed(
            claims=[
                _base(label=revenue_label),
                Claim(
                    id="c2",
                    label="利率能解释营收增速加快"
                    if chinese
                    else "Interest rates explain the revenue growth acceleration",
                    kind="causal",
                    entities=["MSFT"],
                    event="interest rates and earnings",
                    period=Period(year=2024),
                    event_date=date(2024, 7, 30),
                    series="FEDFUNDS",
                    window_start=date(2024, 6, 1),
                    window_end=date(2024, 7, 31),
                ),
            ]
        )
    if question == prompts["event"]:
        return Parsed(
            claims=[
                _base(
                    measure="growth",
                    label="微软 FY2024 的营收增长" if chinese else "Microsoft’s FY2024 revenue grew",
                ),
                Claim(
                    id="c2",
                    label="微软在 2024 年 7 月 30 日发布了 FY2024 财报"
                    if chinese
                    else "Microsoft released its FY2024 results on July 30, 2024",
                    kind="event",
                    entities=["MSFT"],
                    period=Period(year=2024),
                    event="earnings release",
                    event_date=date(2024, 7, 30),
                ),
            ]
        )
    if question in {prompts["operating"], prompts["missing"]}:
        if not previous or not previous.claims:
            return Parsed(
                out_of_scope="请先打开微软营收的回放报告，再提交这条追问。"
                if chinese
                else "Open the Microsoft revenue replay report before running this follow-up."
            )
        old = previous.claims[0]
        if question == prompts["operating"]:
            claim = old.model_copy(
                update={
                    "metric": "operating_income",
                    "label": "微软的营业利润增速加快"
                    if chinese
                    else "Microsoft’s operating income growth accelerated",
                }
            )
            return Parsed(claims=[claim], carried=["company", "period", "comparison"])
        claim = old.model_copy(
            update={
                "period": Period(year=2025),
                "assertion": "describe",
                "label": f"微软 FY2025 的{METRICS_ZH.get(old.metric, '财务指标')}"
                if chinese
                else f"Microsoft FY2025 {old.metric.replace('_', ' ')}",
            }
        )
        return Parsed(claims=[claim], carried=["company", "metric", "comparison"])
    if ("microsoft" in question and "profit" in question and "increase" in question) or (
        "微软" in question and "利润" in question and re.search(r"增加|增长|上升", question)
    ):
        match = re.search(r"(?:fy\s*)?(20\d{2})", question)
        year = match[1] if match else answers.get("year")
        metric = answers.get("metric")
        fields = []
        if not metric:
            fields.append(
                {
                    "name": "metric",
                    "label": "利润指标" if chinese else "Profit metric",
                    "control": "select",
                    "options": [
                        {"value": value, "label": METRICS_ZH[value] if chinese else label}
                        for value, label in (
                            ("gross_profit", "Gross profit"),
                            ("operating_income", "Operating income"),
                            ("net_income", "Net income"),
                        )
                    ],
                }
            )
        if not year:
            fields.append(
                {
                    "name": "year",
                    "label": "财年" if chinese else "Fiscal year",
                    "control": "select",
                    "options": [{"value": str(y), "label": f"FY{y}"} for y in (2024, 2023)],
                }
            )
        if fields:
            return Parsed(
                clarification={
                    "message": "请选择要比较的利润指标和期间。"
                    if chinese
                    else "Choose the missing details to compare profit.",
                    "fields": fields,
                }
            )
        return Parsed(
            claims=[
                _base(
                    metric,
                    int(year),
                    "change",
                    f"微软 FY{year} 的{METRICS_ZH[metric]}增加"
                    if chinese
                    else f"Microsoft {metric.replace('_', ' ')} increased in FY{year}",
                )
            ]
        )
    return Parsed(
        out_of_scope="示例回放支持预设问题和推荐追问。其他历史财务问题请使用实时研究模式。"
        if chinese
        else "Fixture replay covers the example questions and suggested follow-ups. "
        "Choose Live for other historical questions."
    )


def _financials():
    facts = []
    for metric, values in _MEASURES.items():
        for year, value in zip((2022, 2023, 2024), values):
            facts.append(
                Fact(
                    id=stable_id("fixture", "MSFT24", metric, year),
                    source="sec",
                    url=FILING,
                    title="Microsoft FY2024 Form 10-K",
                    accession="0000950170-24-087843",
                    form="10-K",
                    published=date(2024, 7, 30),
                    available_at=datetime(2024, 7, 30, 20, tzinfo=UTC),
                    retrieved_at=FROZEN_AT,
                    entity="MSFT",
                    metric=metric,
                    fiscal_year=year,
                    start=date(year - 1, 7, 1),
                    end=date(year, 6, 30),
                    value=Decimal(value) * (1 if metric == "diluted_eps" else 1_000_000),
                    unit="USD/share" if metric == "diluted_eps" else "USD",
                    currency="USD",
                )
            )
    # Preserve a second accession to exercise a follow-up that needs a new operand.
    for metric, value in {"revenue": 281724, "operating_income": 128528}.items():
        facts.append(
            Fact(
                id=stable_id("fixture", "MSFT25", metric, 2025),
                source="sec",
                entity="MSFT",
                metric=metric,
                url="https://www.sec.gov/Archives/edgar/data/789019/000095017025100235/msft-20250630.htm",
                title="Microsoft FY2025 Form 10-K",
                accession="0000950170-25-100235",
                form="10-K",
                published=date(2025, 7, 30),
                available_at=datetime(2025, 7, 30, 21, tzinfo=UTC),
                retrieved_at=datetime(2025, 8, 1, tzinfo=UTC),
                fiscal_year=2025,
                start=date(2024, 7, 1),
                end=date(2025, 6, 30),
                value=Decimal(value) * 1_000_000,
                unit="USD",
                currency="USD",
            )
        )
    return facts


def _release():
    return Document(
        id="replay-msft-release",
        source="sec",
        entity="MSFT",
        url=RELEASE,
        title="Microsoft FY2024 earnings release",
        published=date(2024, 7, 30),
        available_at=datetime(2024, 7, 30, 20, tzinfo=UTC),
        accession="0000950170-24-087835",
        form="8-K exhibit 99.1",
        publisher="Microsoft",
        retrieved_at=FROZEN_AT,
        period=Period(year=2024),
        passages=[_EVENT_TEXT, _RELEASE_TEXT],
    )


async def lookup_fixture(action, cutoff):
    if action.operation == "financials":
        year = max((period.year for period in action.periods), default=2024)
        return [fact for fact in _financials() if fact.fiscal_year <= year and fact.available_at <= cutoff]
    if action.operation == "release":
        return [_release()] if action.periods and action.periods[-1].year == 2024 else []
    if action.operation == "filing":
        return []
    if action.operation == "observations":
        if action.series != "FEDFUNDS":
            return []
        return [
            Fact(
                id="replay-fedfunds-june-2024",
                source="fred",
                url="https://fred.stlouisfed.org/series/FEDFUNDS",
                title="Federal Funds Effective Rate",
                metric="FEDFUNDS",
                start=date(2024, 6, 1),
                end=date(2024, 6, 1),
                value=Decimal("5.33"),
                unit="%",
                frequency="Monthly",
                retrieved_at=FROZEN_AT,
                vintage=date(2024, 8, 15),
                available_at=FROZEN_AT,
                publisher="Federal Reserve Bank of St. Louis",
            )
        ]
    if action.operation == "articles":
        # Store the review preview's paraphrase as a replay lead. There is no frozen verbatim article body.
        return [
            Document(
                id="replay-axios-lead",
                source="news",
                url=NEWS,
                title="Axios coverage of Microsoft earnings · replay lead",
                entity="MSFT",
                publisher="Axios",
                retrieved_at=FROZEN_AT,
                passages=[
                    "Preview summary: The July 30, 2024 coverage discussed Azure growth and analyst expectations."
                ],
            )
        ]
    raise ProviderError("The operation is unavailable in fixture replay.")


def fixture_attributions(claims, evidence):
    if not any(item.id == "replay-msft-release" for item in evidence):
        return Attributions(entries=[])
    entries = [
        {"claim_id": claim.id, "evidence_id": "replay-msft-release", "quote": _EVENT_TEXT, "role": "event"}
        for claim in claims
        if claim.kind == "event"
    ]
    return Attributions(entries=entries)
