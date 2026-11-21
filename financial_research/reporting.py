"""Compose report content from verified findings and attributed passages."""

import re
from decimal import Decimal
from itertools import groupby

from .domain import COMPANIES, METRICS, Calculation, Fact, Operand, Period
from .language import COMPANIES_ZH, METRICS_ZH, question_language
from .sec import annual_results_heading
from .verification import (
    asks_for_explanation,
    calculation,
    company_disclosures,
    conflict_figures,
    disclosure_qualifiers,
    render_number,
    required_periods,
    resolve_period,
    unsummarized_disclosures,
    uses_disclosed_growth,
    valid_attributions,
)

_YES_NO = re.compile(r"^\s*(?:am|are|is|was|were|did|does|do|has|have|had)\b", re.IGNORECASE)
_YES_NO_ZH = re.compile(r"是否|能否|可否|是不是|有没有|有无|[吗嗎][？?。\s]*$")
_EARNINGS_DISCLOSURE = re.compile(
    r"\b(?:announc\w*|releas\w*|reported)\b[^.!?]*\b(?:financial results|following results|earnings)\b",
    re.IGNORECASE,
)
_QUOTE_REQUEST = re.compile(r"\b(?:quot(?:e[sd]?|ations?)|verbatim|exact wording)\b", re.IGNORECASE)
_QUOTE_OPT_OUT = re.compile(
    r"\b(?:without|no|avoid|do not|don['’]t)\s+(?:(?:any|direct|include|use)\s+)*"
    r"(?:quot(?:e[sd]?|ations?)|verbatim|exact wording)\b",
    re.IGNORECASE,
)
_QUOTE_REQUEST_ZH = re.compile(r"引[用述]|原文|逐字")
_QUOTE_OPT_OUT_ZH = re.compile(r"(?:无需|不要|不用|避免|请勿)(?:直接)?(?:引[用述]|.{0,4}原文)")
_DISCLOSURE_CLAUSE_BREAK = re.compile(r"[;.!?]\s+")
_OFFSET_VERB = re.compile(r"\boffset(?:\s+(?P<passive>by))?\b", re.IGNORECASE)
_SPENDING_SUBJECT = re.compile(
    r"(?:[a-z]+[-–](?:related|driven) )*(?P<topic>[a-z][a-z ]*) spending(?: (?:partly|partially))?",
    re.IGNORECASE,
)
_FX_MENTION = re.compile(r"\b(?:foreign exchange|currency|fx)\b", re.IGNORECASE)
_VAGUE_FX_CLAUSE = re.compile(
    r"(?P<boundary>^|[.;]\s+)(?:foreign exchange(?: rates)?|currency(?: movements| effects)?|fx)"
    r" (?:also )?(?:helped|hurt|was (?:a )?(?:tailwind|headwind))"
    r"(?: (?:the )?(?:results|gains|operating income))?[.!?]",
    re.IGNORECASE,
)
_MARGIN_NAMES = {
    "gross_profit": "gross margin",
    "operating_income": "operating margin",
    "net_income": "net margin",
}
_SERIES_NAMES = {
    "FEDFUNDS": "federal funds rate",
    "CPIAUCSL": "consumer price index",
    "UNRATE": "unemployment rate",
}
_CONFLICT_OPENING = {
    "Supported": "The figures conflict.",
    "Contradicted": "The two figures don’t conflict.",
    "Unresolved": "The evidence is insufficient to tell whether the figures conflict.",
}
_MARGIN_NAMES_ZH = {"gross_profit": "毛利率", "operating_income": "营业利润率", "net_income": "净利率"}
_SERIES_NAMES_ZH = {"FEDFUNDS": "联邦基金利率", "CPIAUCSL": "消费者价格指数", "UNRATE": "失业率"}
_MEASURES_ZH = {
    "value": "金额",
    "growth": "增速",
    "acceleration": "增速变化",
    "change": "变动额",
    "margin": "利润率",
}
_BASIS_ZH = {
    "as reported": "报告口径",
    "reported": "报告口径",
    "in constant currency": "固定汇率口径",
    "constant currency": "固定汇率口径",
    "constant-currency": "固定汇率口径",
    "on an adjusted basis": "调整后口径",
    "adjusted": "调整后口径",
    "non-gaap": "非 GAAP 口径",
}
_SCOPE_ZH = {"consolidated": "合并口径", "standalone": "单体口径"}
_SCALE_ZH = {"thousand": "千", "million": "百万", "billion": "十亿"}
_UNRESOLVED_REASONS_ZH = {
    "No eligible fiscal period was found before the cutoff.": "截止日期前没有找到符合条件的财务期间。",
    "Year-over-year growth requires consecutive years and the same fiscal quarter.": (
        "同比增速需要连续两年的同一财务季度或完整财年。"
    ),
    "Growth acceleration compares the current year-over-year growth with the prior year's.": (
        "增速变化需要比较本期和上一年同期的同比增速。"
    ),
    "The operands use different units, currencies, or period lengths.": "用来比较的数据单位、货币或期间长度不同。",
    "The requested metric does not define a supported margin.": "该指标没有支持的利润率定义。",
    "The margin needs positive revenue for the same reporting period and currency.": (
        "计算利润率需要同一报告期间、使用同一货币且大于零的营收。"
    ),
    "Growth requires a positive baseline in each comparison.": "每次比较的基期值都必须大于零，才能计算增速。",
    "The two company results use different units.": "两家公司的数据使用不同单位。",
    "Specify the company for the numeric target.": "请明确该目标数值对应哪家公司。",
    "The requested and calculated units differ.": "问题所用单位与核验结果的单位不同。",
    "An equality assertion needs a comparison value.": "判断是否相等需要提供比较值。",
    "No eligible observation was available for the requested series and vintage.": (
        "所请求的时间序列和数据版本没有可用观测值。"
    ),
    "A change requires observations for both endpoint months.": "计算变化需要起始月和结束月的观测值。",
    "The endpoint observations use different units.": "起始月和结束月的观测值使用不同单位。",
    "Year-over-year macro growth is available for the CPI index.": "宏观指标中目前仅支持计算消费者价格指数的同比增速。",
    "Year-over-year CPI requires the observation for the same month one year earlier.": (
        "计算消费者价格指数的同比增速需要上一年同月的观测值。"
    ),
    "The requested measure is unavailable for this macroeconomic series.": "该宏观时间序列不支持所请求的计算方式。",
    "No eligible supporting citation was found.": "没有找到可以支持结论的合格引文。",
}
_CONFLICT_OPENING_ZH = {
    "Supported": "这两个数据相互矛盾。",
    "Contradicted": "这两个数据并不矛盾。",
    "Unresolved": "证据不足，暂时无法判断这两个数据是否矛盾。",
}
_DRAFT_ANCHORS = re.compile(
    r"\b(?:constant currency|as reported|consolidated|standalone|year over year|FY\d+(?: Q[1-4])?)\b"
    r"|[+−-]?\$?[+−-]?\d+(?:,\d{3})*(?:\.\d+)?"
    r"(?:\s?%|\s+(?:percentage points?|basis points?|percent|pp|thousand|million|billion|trillion)\b|[BM]\b|/share\b)?",
    re.IGNORECASE,
)
_DRAFT_COMPANIES = re.compile(
    r"\b(?:" + "|".join(re.escape(name) for name, _ in COMPANIES.values()) + r")\b", re.IGNORECASE
)
_DRAFT_ANCHORS_ZH = re.compile(
    r"固定汇率口径|报告口径|合并口径|单体口径|调整后口径|非 GAAP 口径|同比|FY\d+(?: Q[1-4])?"
    r"|[+−-]?\$?[+−-]?\d+(?:,\d{3})*(?:\.\d+)?"
    r"(?:\s?%|\s*(?:个百分点|个基点|(?:十亿|百万|千)?美元(?:/股)?|指数点|年|月|日|pp)"
    r"|\s+(?:thousand|million|billion|trillion)\b|[BM]|/share|/股)?",
    re.IGNORECASE,
)
_DRAFT_COMPANIES_ZH = re.compile(
    r"(?<![A-Za-z])(?:"
    + "|".join(re.escape(name) for name, _ in COMPANIES.values())
    + r")(?![A-Za-z])"
    + "|"
    + "|".join(re.escape(name) for name in COMPANIES_ZH.values() if not name.isascii()),
    re.IGNORECASE,
)
_DIRECT_QUOTES = re.compile(r"“([^”]+)”")


def _conclusion_anchors(text, pattern):
    # Keep each number next to its verified measurement basis, even when wording changes.
    return [anchor for anchor, _ in groupby(pattern.findall(text.casefold()))]


def validated_conclusion(draft, answers):
    """Accept changes to prose while keeping the verified rows and their citations."""
    if len(draft.answers) != len(answers):
        return None
    rewritten = []
    for index, (proposed, answer) in enumerate(zip(draft.answers, answers, strict=True)):
        text = " ".join(proposed.text.split())
        original = answer["text"]
        chinese = question_language(original) == "zh"
        anchors = _DRAFT_ANCHORS_ZH if chinese else _DRAFT_ANCHORS
        companies = _DRAFT_COMPANIES_ZH if chinese else _DRAFT_COMPANIES
        if (
            proposed.index != index
            or proposed.verdict != answer["verdict"]
            or not text
            or _conclusion_anchors(text, anchors) != _conclusion_anchors(original, anchors)
            or set(companies.findall(text.casefold())) != set(companies.findall(original.casefold()))
            or (chinese and _DIRECT_QUOTES.findall(text) != _DIRECT_QUOTES.findall(original))
        ):
            return None
        rewritten.append({**answer, "text": text})
    return rewritten


def _measurement_key(item):
    return (
        item.formula,
        item.unit,
        item.result,
        tuple((operand.unit, operand.value, tuple(operand.evidence_ids)) for operand in item.operands),
    )


def _metric(claim, chinese=False):
    if chinese:
        if claim.measure == "margin":
            return _MARGIN_NAMES_ZH.get(claim.metric, "利润率")
        suffix = "" if claim.measure == "value" else _MEASURES_ZH[claim.measure]
        return METRICS_ZH[claim.metric] + suffix
    if claim.measure == "margin":
        return _MARGIN_NAMES.get(claim.metric, "margin")
    metric = METRICS[claim.metric].lower()
    return metric + {"growth": " growth", "acceleration": " growth acceleration", "change": " change"}.get(
        claim.measure, ""
    )


def _comparison(claim, finding, evidence, chinese=False):
    if len(claim.entities) != 2 or claim.target is not None or finding.verdict == "Unresolved":
        return None
    results = [
        next((item for item in reversed(finding.calculations) if item.label.startswith(ticker + " ")), None)
        for ticker in claim.entities
    ]
    left, right = results
    period = resolve_period(claim, evidence)
    if not left or not right or left.unit != right.unit or not period:
        return None
    company, peer = (COMPANIES[ticker][0] for ticker in claim.entities)
    metric = _metric(claim)
    gap = abs(left.result - right.result)
    unit = "pp" if left.unit == "%" else left.unit
    operands = [
        Operand(label=item.label, value=item.result, unit=item.unit, evidence_ids=item.evidence_ids)
        for item in (left, right)
    ]
    refs = list(dict.fromkeys([*left.evidence_ids, *right.evidence_ids]))
    difference = Calculation(
        label=f"{period.label} {metric} difference",
        formula="Absolute difference between the two verified results",
        operands=operands,
        result=gap,
        unit=unit,
        evidence_ids=refs,
    )
    if chinese:
        company, peer = (COMPANIES_ZH[ticker] for ticker in claim.entities)
        metric = _metric(claim, chinese=True)
        prefix = _assertion_prefix_zh(claim, finding)
        if left.result == right.result:
            return f"{prefix}{company}和{peer}的 {period.label} {metric}相同。", difference
        direction = "高" if left.result > right.result else "低"
        distance = f" {_readable_amount(gap, unit, chinese=True)}" if gap >= Decimal("0.005") else ""
        return f"{prefix}{company} {period.label} 的{metric}比{peer}{direction}{distance}。", difference
    prefix = ""
    if claim.assertion != "describe":
        prefix = "Yes. " if finding.verdict == "Supported" else "No. "
    if left.result == right.result:
        verb = "reported" if claim.measure == "value" else "had"
        answer = f"{company} and {peer} {verb} equal {period.label} {metric}."
    else:
        direction = "higher" if left.result > right.result else "lower"
        distance = f"{render_number(gap, unit)} " if gap >= Decimal("0.005") else ""
        answer = f"{company}’s {period.label} {metric} was {distance}{direction} than {peer}’s."
    return (prefix + answer, difference)


def _answer_prefix(claim, finding, question, chinese=False):
    if chinese:
        return _assertion_prefix_zh(claim, finding) if _YES_NO_ZH.search(question) else ""
    if claim.assertion != "describe" and _YES_NO.search(question):
        return "Yes. " if finding.verdict == "Supported" else "No. "
    return ""


def _assertion_prefix_zh(claim, finding):
    if claim.assertion == "describe":
        return ""
    return "是的，" if finding.verdict == "Supported" else "不是，"


def _readable_amount(value, unit, chinese=False):
    amount = render_number(value, unit)
    if chinese:
        if unit == "pp":
            return amount.removesuffix(" pp") + " 个百分点"
        if unit == "USD/share":
            return amount.removesuffix("/share") + "/股"
        if unit == "Index (1982–84 = 100)":
            return amount.removesuffix(unit) + "指数点（1982–84 年 = 100）"
    return amount.removesuffix(" pp") + " percentage points" if unit == "pp" else amount


def _change_phrase(result, unit, chinese=False):
    if chinese:
        if not result:
            return "持平"
        direction = "上升" if result > 0 else "下降"
        return f"{direction} {_readable_amount(abs(result), unit, chinese=True)}"
    if not result:
        return "was unchanged"
    direction = "increased" if result > 0 else "decreased"
    return f"{direction} by {_readable_amount(abs(result), unit)}"


def _financial_answer_zh(claim, period, prefix, calculations):
    company = COMPANIES_ZH[claim.entities[0]]
    qualifiers = [
        labels.get(value, value)
        for value, default, labels in (
            (claim.scope, "consolidated", _SCOPE_ZH),
            (claim.basis, "reported", _BASIS_ZH),
        )
        if value != default
    ]
    scope = f"（{'、'.join(qualifiers)}）" if qualifiers else ""
    head = f"{prefix}{company} {period.label} 的{_metric(claim, chinese=True)}{scope}"
    item = calculations[-1]
    disclosed_growth = uses_disclosed_growth(claim)

    def render_rate(rate):
        digits = max(0, -rate.result.as_tuple().exponent) if disclosed_growth else 2
        return render_number(rate.result, rate.unit, digits)

    if claim.measure == "acceleration":
        direction = "加快" if item.result > 0 else "放缓" if item.result < 0 else "持平"
        magnitude = f" {_readable_amount(abs(item.result), item.unit, chinese=True)}" if item.result else ""
        metric = METRICS_ZH[claim.metric]
        topic = f"{prefix}{company} {period.label} 的{metric}增速{scope}"
        comparison = "较上年同期" if period.quarter else "较上一财年"
        answer = f"{topic}{comparison}{direction}{magnitude}"
        if len(calculations) >= 3:
            prior_period = Period(year=period.year - 1, quarter=period.quarter)
            prior, current = (render_rate(rate) for rate in calculations[-3:-1])
            answer += f"，上期（{prior_period.label}）为 {prior}，本期为 {current}"
        return answer + "。"
    if disclosed_growth:
        return f"{head}为 {render_rate(item)}。"
    if claim.measure in {"value", "margin"}:
        amount = _readable_amount(item.result, item.unit, chinese=True)
        return f"{head}为 {amount}。"
    baseline, _ = required_periods(claim.model_copy(update={"period": period}))
    subject = f"{prefix}{company} {period.label} 的{METRICS_ZH[claim.metric]}{scope}"
    return f"{subject}较 {baseline.label} {_change_phrase(item.result, item.unit, chinese=True)}。"


def _financial_answer(claim, finding, evidence, question, chinese=False):
    period = resolve_period(claim, evidence)
    if len(claim.entities) != 1 or not period or not finding.calculations:
        return "核验得出的数值见下方计算明细。" if chinese else finding.reason
    prefix = _answer_prefix(claim, finding, question, chinese)
    item = finding.calculations[-1]
    if chinese:
        return _financial_answer_zh(claim, period, prefix, finding.calculations)
    (ticker,) = claim.entities
    subject = f"{COMPANIES[ticker][0]}’s {period.label} {_metric(claim)}"
    qualifiers = [value for value in (claim.scope, claim.basis) if value not in {"consolidated", "reported"}]
    scope = f" ({', '.join(qualifiers)})" if qualifiers else ""
    if uses_disclosed_growth(claim):

        def rate(operand):
            digits = max(0, -operand.value.as_tuple().exponent)
            return render_number(operand.value, "%", digits)

        topic = f"{COMPANIES[ticker][0]}’s {METRICS[claim.metric].lower()} growth in constant currency"
        if claim.measure == "growth":
            return f"{prefix}{topic} was {rate(item.operands[0])} in {period.label}."
        prior, current = item.operands
        prior_period = Period(year=period.year - 1, quarter=period.quarter)
        if not item.result:
            return (
                f"{prefix}{topic} held steady at {rate(current)} in {prior_period.label} and {period.label}."
            )
        direction = "accelerated" if item.result > 0 else "slowed"
        points = abs(item.result)
        change = f"{points:f} percentage point" + ("s" if points != 1 else "")
        return (
            f"{prefix}{topic} {direction} by about {change}, "
            f"from {rate(prior)} in {prior_period.label} to {rate(current)} in {period.label}."
        )
    if claim.measure == "acceleration":
        outcome = "accelerated" if item.result > 0 else "slowed" if item.result < 0 else "held steady"
        magnitude = f" by {_readable_amount(abs(item.result), item.unit)}" if item.result else ""
        return f"{prefix}{subject.removesuffix(' acceleration')}{scope} {outcome}{magnitude}."
    if claim.measure in {"growth", "change"}:
        subject = subject.removesuffix(" growth").removesuffix(" change")
        baseline, _ = required_periods(claim.model_copy(update={"period": period}))
        return f"{prefix}{subject}{scope} {_change_phrase(item.result, item.unit)} compared with {baseline.label}."
    return f"{prefix}{subject}{scope} was {_readable_amount(item.result, item.unit)}."


def _macro_answer(claim, finding, records, question, chinese=False):
    item = finding.calculations[-1]
    name = _SERIES_NAMES[claim.series]
    dates = [
        next((records[ref].end for ref in operand.evidence_ids if isinstance(records.get(ref), Fact)), None)
        for operand in item.operands
    ]
    if not dates or any(day is None for day in dates):
        return "核验得出的数值见下方计算明细。" if chinese else finding.reason
    prefix = _answer_prefix(claim, finding, question, chinese)
    if chinese:
        start, end = (f"{day.year} 年 {day.month} 月" for day in (dates[0], dates[-1]))
        name = _SERIES_NAMES_ZH[claim.series]
        if claim.measure == "change":
            return (
                f"{prefix}从 {start} 到 {end}，{name}{_change_phrase(item.result, item.unit, chinese=True)}。"
            )
        if claim.measure == "growth":
            return f"{prefix}{end}的{name}同比{_change_phrase(item.result, item.unit, chinese=True)}。"
        return f"{prefix}{end}的{name}为 {_readable_amount(item.result, item.unit, chinese=True)}。"
    if claim.measure == "change":
        return f"{prefix}The {name} {_change_phrase(item.result, item.unit)} from {dates[0]:%B %Y} to {dates[-1]:%B %Y}."
    if claim.measure == "growth":
        return f"{prefix}The {name} {_change_phrase(item.result, item.unit)} year over year in {dates[-1]:%B %Y}."
    return f"{prefix}The {name} was {_readable_amount(item.result, item.unit)} in {dates[-1]:%B %Y}."


def _unresolved_amount(claim, finding, chinese=False):
    reason = finding.reason
    if chinese:
        detail = _UNRESOLVED_REASONS_ZH.get(reason, "")
        if missing := re.fullmatch(r"Missing comparable .+ for ([A-Z]+) (.+)\.", reason):
            company = COMPANIES_ZH.get(missing[1], missing[1])
            detail = f"缺少{company}在 {missing[2]} 的可比{METRICS_ZH[claim.metric]}数据。"
        elif disclosed := re.fullmatch(
            r"(Missing a disclosed constant-currency growth rate for|Multiple constant-currency growth rates matched)"
            r" ([A-Z]+) (.+)\.",
            reason,
        ):
            company = COMPANIES_ZH.get(disclosed[2], disclosed[2])
            problem = "未找到披露的" if disclosed[1].startswith("Missing") else "找到多个不同的"
            detail = f"{company}在 {disclosed[3]} {problem}固定汇率口径增速。"
        return "现有证据无法核实所请求的数值。" + detail
    for ticker, (name, _) in COMPANIES.items():
        reason = re.sub(rf"\b{ticker}\b", name, reason)
    return "The available evidence couldn’t verify the requested figure. " + reason


def _conflict_answer(claim, finding, chinese=False):
    if not chinese:
        return f"{_CONFLICT_OPENING[finding.verdict]} {finding.reason}"
    opening = _CONFLICT_OPENING_ZH[finding.verdict]
    if finding.verdict == "Unresolved":
        return opening + "还需要确认两项数据的口径、期间和范围。"
    first, second = conflict_figures(claim)

    def display(figure):
        scale = _SCALE_ZH.get(figure.scale, "")
        unit = {"USD": "美元", "USD/share": "美元/股", "%": "%", "pp": "个百分点"}[figure.unit]
        return f"{figure.value}{scale}{unit}"

    first_value, second_value = display(first), display(second)
    decision = finding.checks[-1].label if finding.checks else ""
    if decision == "Compare measurement bases":
        basis_a, basis_b = [
            next((name for name in _BASIS_ZH if f"({name})." in check.detail), None)
            for check in finding.checks[:2]
        ]
        if basis_a and basis_b:
            detail = f"{first_value} 为{_BASIS_ZH[basis_a]}，{second_value} 为{_BASIS_ZH[basis_b]}。"
            if "in constant currency" in (basis_a, basis_b):
                detail += "固定汇率口径剔除了汇率变动的影响。"
            return opening + detail
        return opening + f"{first_value} 和 {second_value} 采用不同的计算口径。"
    if decision == "Compare fiscal periods":
        return (
            opening + f"{first_value} 对应 {(first.period or claim.period).label}，"
            f"{second_value} 对应 {(second.period or claim.period).label}。"
        )
    if decision == "Compare measurement definitions":
        return opening + f"{first_value} 和 {second_value} 的范围、计算方式或单位不同。"
    if decision == "Check rounding":
        return opening + f"{first_value} 和 {second_value} 是同一核验数值按不同精度显示的结果。"
    if decision == "Compare like-for-like figures":
        return opening + f"不同来源在相同报告期间和口径下分别给出了 {first_value} 和 {second_value}。"
    return opening


def _growth_sentence(claim, finding, records, chinese=False):
    if claim.measure != "growth" or len(claim.entities) != 1 or len(finding.calculations) != 1:
        return None
    (item,) = finding.calculations
    if item.unit != "%" or len(item.operands) != 2 or not item.result:
        return None
    facts = [
        next((records[ref] for ref in operand.evidence_ids if isinstance(records.get(ref), Fact)), None)
        for operand in item.operands
    ]
    if any(not fact or not fact.fiscal_year for fact in facts):
        return None
    earlier, current = facts
    before = Period(year=earlier.fiscal_year, quarter=earlier.quarter).label
    after = Period(year=current.fiscal_year, quarter=current.quarter).label
    prior, new = (render_number(operand.value, operand.unit) for operand in item.operands)
    direction = "grew" if item.result > 0 else "fell"
    company = COMPANIES[claim.entities[0]][0]
    metric = METRICS[claim.metric].lower()
    change = render_number(abs(item.result), item.unit)
    if chinese:
        company = COMPANIES_ZH[claim.entities[0]]
        metric = METRICS_ZH[claim.metric]
        direction = "增长" if item.result > 0 else "下降"
        return (
            f"{company}的{metric}从 {before} 的 {prior} 变为 {after} 的 {new}，{direction} {change}。",
            _measurement_key(item),
        )
    return (
        f"{company}’s {metric} {direction} {change}, from {prior} in {before} to {new} in {after}.",
        _measurement_key(item),
    )


def _highlights(findings, claims, records, inline_measurements, chinese=False):
    highlights = {}
    has_event = any(
        claim.kind == "event" and not asks_for_explanation(claim.event) for claim in claims.values()
    )
    for finding in findings:
        claim = claims[finding.claim_id]
        calculations = list(finding.calculations)
        if (
            has_event
            and finding.verdict == "Supported"
            and claim.metric == "revenue"
            and claim.measure == "growth"
        ):
            for item in finding.calculations:
                current = next(
                    (
                        records[ref]
                        for ref in item.operands[-1].evidence_ids
                        if isinstance(records.get(ref), Fact)
                    ),
                    None,
                )
                if (
                    current
                    and current.metric == "revenue"
                    and current.fiscal_year
                    and not current.derived_from
                ):
                    period = Period(year=current.fiscal_year, quarter=current.quarter)
                    calculations.insert(
                        0,
                        calculation(
                            f"{current.entity} {period.label}",
                            "Reported amount",
                            [current],
                            current.value,
                            current.unit,
                        ),
                    )
        for item in calculations:
            operand = item.operands[0 if claim.measure == "margin" else -1]
            fact = next(
                (records[ref] for ref in operand.evidence_ids if isinstance(records.get(ref), Fact)), None
            )
            label = item.label
            if fact and fact.entity and item.formula == "Reported amount":
                label = (
                    f"{COMPANIES_ZH[fact.entity]} {METRICS_ZH.get(fact.metric, fact.metric)}"
                    if chinese
                    else f"{COMPANIES[fact.entity][0]} {METRICS.get(fact.metric, fact.metric).lower()}"
                )
            elif fact and fact.entity and claim.kind == "financial":
                company = COMPANIES_ZH[fact.entity] if chinese else COMPANIES[fact.entity][0]
                metric = _metric(claim, chinese)
                if claim.measure == "acceleration" and item.unit == "%":
                    metric = metric.removesuffix("变化" if chinese else " acceleration")
                label = f"{company} {metric}"
            elif chinese and claim.kind == "macro":
                label = _SERIES_NAMES_ZH[claim.series]
            period = (
                Period(year=fact.fiscal_year, quarter=fact.quarter) if fact and fact.fiscal_year else None
            )
            highlights.setdefault(
                _measurement_key(item),
                {
                    "label": label,
                    "period": period.label if period else None,
                    "period_end": fact.end.isoformat() if fact else None,
                    "calculation": item.model_dump(mode="json"),
                    "in_answer": _measurement_key(item) in inline_measurements,
                },
            )
    return list(highlights.values())


def _explanations(claims, records, attributions, question, chinese=False):
    asks_why = asks_for_explanation(question) and any(
        claim.kind in {"event", "causal"} for claim in claims.values()
    )
    explanations = company_disclosures(claims, records, attributions) if asks_why else []
    note = None
    if asks_why and not explanations:
        note = (
            "检索到的资料未能核实公司是否披露过相关原因。"
            if chinese
            else "The retrieved sources did not verify a company-disclosed explanation."
        )
    return explanations, note


def _offset_terms(text):
    generic = {"spending", "higher", "increased"}
    return {word.rstrip("s") for word in re.findall(r"\b[a-z]{4,}\b", text.lower()) if word not in generic}


def _offset_is_covered(value, summary):
    terms = _offset_terms(value)
    if not terms:
        return False
    spending_topic = value.lower().removeprefix("spending on ")
    for clause in _DISCLOSURE_CLAUSE_BREAK.split(summary):
        if not (offset := _OFFSET_VERB.search(clause)):
            continue
        subject = clause[offset.end() :] if offset["passive"] else clause[: offset.start()]
        if terms <= _offset_terms(subject):
            return True
        # A compact "infrastructure spending" can describe "spending on technology
        # infrastructure". Keep distinct or coordinated spending topics available.
        if (
            spending_topic != value.lower()
            and not re.search(r"\b(?:and|or)\b|[,/]", spending_topic)
            and (compact := _SPENDING_SUBJECT.fullmatch(subject.strip()))
            and spending_topic.endswith(" " + compact["topic"].lower())
        ):
            return True
    return False


def _qualifier_text(kind, value, summary, chinese=False):
    if kind == "offset":
        if _offset_is_covered(value, summary):
            return None
        if chinese:
            return f"公司还披露了部分抵消因素：“{value}”。"
        return f"{value[0].upper()}{value[1:]} partly offset the change."
    verb, amount, metric = value
    if chinese:
        if amount in summary and "汇率" in summary:
            return None
        direction = "增加" if verb == "added" else "减少"
        metric = next(
            (METRICS_ZH[key] for key, name in METRICS.items() if name.lower() == metric),
            metric,
        )
        return f"汇率变动使{metric}{direction}了 {amount}。"
    if any(
        amount in clause and _FX_MENTION.search(clause) for clause in _DISCLOSURE_CLAUSE_BREAK.split(summary)
    ):
        return None
    if verb == "added":
        return f"Foreign exchange added {amount} to {metric}."
    return f"Foreign exchange reduced {metric} by {amount}."


def _replace_vague_fx(summary, detail):
    def replace(match):
        boundary = match["boundary"]
        return (". " if boundary.startswith(";") else boundary) + detail

    return _VAGUE_FX_CLAUSE.sub(replace, summary, count=1)


def _disclosure_content(explanations, attributions, summaries, claims, records, chinese=False):
    drivers = {
        (item.claim_id, item.evidence_id, item.quote): disclosure_qualifiers(
            claims[item.claim_id], records[item.evidence_id], item
        )
        for item in explanations
    }
    accepted = {
        (item.claim_id, item.evidence_id, item.quote) for item in attributions if item.role == "explanation"
    }
    points, displayed, selected, seen = [], [], {}, set()
    for item in summaries:
        quotes = item["quotes"]
        text = item["text"]
        keys = [(item["claim_id"], ref["evidence_id"], ref["quote"]) for ref in quotes]
        point_drivers = [key for key in keys if key in drivers]
        driver_documents = {key[1] for key in point_drivers}
        if (
            not quotes
            or (chinese and question_language(text) != "zh")
            or text.casefold() in seen
            or not set(keys) <= accepted
            or any(ref["evidence_id"] not in driver_documents for ref in quotes)
        ):
            continue
        point = {"text": text, "evidence_ids": list(dict.fromkeys(ref["evidence_id"] for ref in quotes))}
        details = {}
        for kind, value, _ in (qualifier for key in point_drivers for qualifier in drivers[key]):
            detail = _qualifier_text(kind, value, point["text"], chinese)
            if not detail:
                continue
            if kind == "fx" and len(point_drivers) == 1 and not chinese:
                revised = _replace_vague_fx(point["text"], detail)
                if revised != point["text"]:
                    point["text"] = revised
                    continue
            detail_key = frozenset(_offset_terms(value)) if kind == "offset" else detail
            details.setdefault(detail_key, detail)
        if details:
            point["details"] = list(details.values())
        points.append(point)
        seen.add(text.casefold())
        displayed.append(item)
        selected.update({(ref["evidence_id"], ref["quote"]): ref for ref in quotes})
    for (claim_id, evidence_id, quote), qualifiers in drivers.items():
        selected.setdefault((evidence_id, quote), {"quote": quote, "evidence_id": evidence_id})
        for _, _, excerpt in qualifiers:
            if not any(
                evidence_id == ref["evidence_id"] and excerpt in ref["quote"] for ref in selected.values()
            ):
                selected[(evidence_id, excerpt)] = {"quote": excerpt, "evidence_id": evidence_id}
    return points, list(selected.values()), bool(unsummarized_disclosures(explanations, displayed))


def _event_caption(claim, document, quote, evidence, chinese=False):
    day = claim.event_date or document.published
    event_date = f"{day:%B} {day.day}, {day.year}"

    def announcement():
        if chinese:
            company = COMPANIES_ZH[document.entity]
            return f"{company}于 {day.year} 年 {day.month} 月 {day.day} 日发布了 {claim.period.label} 财报。"
        company = COMPANIES[document.entity][0]
        return f"{company} announced its {claim.period.label} results on {event_date}."

    if (
        document.source == "sec"
        and document.form
        and "exhibit" in document.form
        and document.entity
        and document.published == day
        and document.period == claim.period
        and annual_results_heading(quote, claim.period)
    ):
        return announcement()
    if (
        claim.period
        and document.entity
        and document.period == claim.period
        and _EARNINGS_DISCLOSURE.search(quote)
        and (claim.event_date or event_date.casefold() in quote.casefold())
    ):
        text = " ".join(quote.casefold().split())
        period = claim.period
        if any(
            isinstance(item, Fact)
            and item.entity == document.entity
            and (item.fiscal_year, item.quarter) == (period.year, period.quarter)
            and f"ended {item.end:%B} {item.end.day}, {item.end.year}".casefold() in text
            for item in evidence
        ):
            return announcement()
    source = document.publisher or document.title
    if chinese:
        return f"{source} 报道：“{quote}”"
    return f"{source} reported “{quote}”"


def report_content(
    parsed, findings, evidence, attributions, question, disclosure_summaries=(), *, language="en"
):
    chinese = language == "zh"
    claims = {claim.id: claim for claim in parsed.claims}
    records = {item.id: item for item in evidence}
    attributions = valid_attributions(parsed.claims, evidence, attributions)
    explanations, note = _explanations(claims, records, attributions, question, chinese)
    explained = {item.claim_id for item in explanations}
    points, quotes, incomplete = _disclosure_content(
        explanations, attributions, disclosure_summaries, claims, records, chinese
    )
    quote_request = _QUOTE_REQUEST.search(question) or (chinese and _QUOTE_REQUEST_ZH.search(question))
    quote_opt_out = _QUOTE_OPT_OUT.search(question) or (chinese and _QUOTE_OPT_OUT_ZH.search(question))
    show_quotes = bool(quotes and quote_request and not quote_opt_out)
    if points and incomplete and not show_quotes:
        note = (
            "其他公司披露的原因可在证据详情中查看。"
            if chinese
            else "Additional company-disclosed drivers are quoted under Supporting details."
        )
    separate_event = any(
        claim.kind == "event" and not asks_for_explanation(claim.event) for claim in claims.values()
    )
    answers, event_answers, causal_answers, differences = [], [], [], []
    numeric_question = "" if any(claim.kind == "causal" for claim in claims.values()) else question
    inline_measurements = set()
    comparisons = {
        finding.claim_id: comparison
        for finding in findings
        if (claim := claims[finding.claim_id]).kind == "financial"
        and (comparison := _comparison(claim, finding, evidence, chinese))
    }
    compared_measurements = {
        _measurement_key(calculation)
        for finding in findings
        if finding.claim_id in comparisons
        for calculation in finding.calculations
    }
    for finding in findings:
        claim = claims[finding.claim_id]
        answer, refs = finding.reason, finding.evidence_ids
        if comparison := comparisons.get(claim.id):
            answer, difference = comparison
            refs = difference.evidence_ids
            differences.append(difference.model_dump(mode="json"))
        elif claim.kind == "conflict":
            answer = _conflict_answer(claim, finding, chinese)
        elif claim.kind in {"financial", "macro"}:
            if (
                finding.verdict == "Supported"
                and claim.assertion == "describe"
                and finding.calculations
                and all(_measurement_key(item) in compared_measurements for item in finding.calculations)
            ):
                continue
            if finding.verdict == "Unresolved":
                answer = _unresolved_amount(claim, finding, chinese)
            elif claim.kind == "macro":
                answer = _macro_answer(claim, finding, records, numeric_question, chinese)
            else:
                answer = _financial_answer(claim, finding, evidence, numeric_question, chinese)
                if (
                    explained
                    and not separate_event
                    and claim.assertion == "describe"
                    and claim.scope == "consolidated"
                    and claim.basis == "reported"
                    and (growth := _growth_sentence(claim, finding, records, chinese))
                ):
                    answer, measurement = growth
                    inline_measurements.add(measurement)
        elif claim.kind == "event" and finding.verdict == "Supported":
            attributed_event = next(
                (
                    item
                    for item in attributions
                    if item.claim_id == claim.id
                    and item.role == "event"
                    and (claim.id not in explained or _EARNINGS_DISCLOSURE.search(item.quote))
                    and (
                        not claim.event_date
                        or records[item.evidence_id].published == claim.event_date
                        or claim.event_date.isoformat() in item.quote
                    )
                ),
                None,
            )
            if attributed_event:
                document = records[attributed_event.evidence_id]
                answer = _event_caption(claim, document, attributed_event.quote, evidence, chinese)
                refs = [document.id]
            elif claim.id in explained:
                # The sourced synthesis below supplies the explanation.
                continue
            elif chinese:
                answer = "有符合日期要求的来源证实了该事件。"
        elif claim.kind == "event":
            answer = (
                "现有来源无法证实这一事件。"
                if chinese
                else "The available sources couldn’t confirm what happened."
            )
        elif claim.kind == "causal":
            if chinese:
                answer = (
                    "独立来源支持所述因果关系。"
                    if finding.verdict == "Supported"
                    else "现有证据无法确定所述因果关系。"
                )
                if finding.verdict == "Unresolved" and refs:
                    answer += "相关来源提供了事件背景。"
            else:
                answer = (
                    "An independent source supports the claimed cause."
                    if finding.verdict == "Supported"
                    else "The available evidence leaves the cause unresolved."
                )
                if finding.verdict == "Unresolved" and refs:
                    answer += " The sources provide context around the event."
        answer_row = {"text": answer, "evidence_ids": refs, "verdict": finding.verdict}
        group = (
            causal_answers
            if claim.kind == "causal"
            else (event_answers if claim.kind == "event" and finding.verdict == "Supported" else answers)
        )
        if answer_row not in group:
            group.append(answer_row)
    if not answers and not event_answers and not causal_answers and explained:
        if points:
            lead = points.pop(0)
            event_answers.append(
                {
                    "text": " ".join([lead["text"], *lead.get("details", [])]),
                    "evidence_ids": lead["evidence_ids"],
                    "verdict": "Supported",
                }
            )
        else:
            event_answers.append(
                {
                    "text": (
                        "公司披露了这一变化的原因。原始文件摘录可在证据详情中查看。"
                        if chinese
                        else "The company disclosed reasons for the change. The filing excerpts are under Supporting details."
                    ),
                    "evidence_ids": list(dict.fromkeys(item["evidence_id"] for item in quotes)),
                    "verdict": "Supported",
                }
            )
    return {
        "answers": causal_answers + event_answers + answers,
        "highlights": _highlights(findings, claims, records, inline_measurements, chinese),
        "disclosure_summary": points,
        "explanations": quotes,
        "show_disclosure_quotes": show_quotes,
        "explanation_note": note,
        "comparison_calculations": differences,
    }
