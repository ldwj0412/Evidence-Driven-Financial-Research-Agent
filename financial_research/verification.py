import itertools
import re
from decimal import ROUND_HALF_UP, Decimal, localcontext

from .domain import (
    METRICS,
    Attribution,
    Calculation,
    Claim,
    ConflictFigure,
    Document,
    EvidenceRecord,
    Fact,
    Finding,
    Gap,
    Operand,
    Period,
)
from .sec import TAGS


def render_number(value: Decimal, unit: str, digits: int = 2) -> str:
    with localcontext() as context:
        context.prec = max(34, value.adjusted() + digits + 3)
        if unit == "USD" and abs(value) >= 1_000_000:
            scale, suffix = (1_000_000_000, "B") if abs(value) >= 1_000_000_000 else (1_000_000, "M")
            rounded = (value / scale).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
            return f"${rounded:,.{digits}f}{suffix}"
        rounded = value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
        if unit == "%":
            return f"{rounded:,.{digits}f}%"
        if unit in {"USD", "USD/share"}:
            return f"${rounded:,.{digits}f}" + ("/share" if unit == "USD/share" else "")
        return f"{rounded:,.{digits}f} {unit}".strip()


def required_periods(claim: Claim) -> list[Period]:
    if not claim.period:
        return []
    current = claim.period
    if claim.comparison_period and claim.measure in {"growth", "change"}:
        return [claim.comparison_period, current]
    offsets = {"growth": [1, 0], "change": [1, 0], "acceleration": [2, 1, 0]}
    return [
        Period(year=current.year - offset, quarter=current.quarter)
        for offset in offsets.get(claim.measure, [0])
    ]


def select_fact(evidence, entity, metric, period, basis="reported", scope="consolidated"):
    options = [
        item
        for item in evidence
        if isinstance(item, Fact)
        and item.entity == entity
        and item.metric == metric
        and item.fiscal_year == period.year
        and item.quarter == period.quarter
        and item.basis == basis
        and item.scope == scope
    ]
    aliases = TAGS.get(metric, ())
    priorities = {tag: len(aliases) - index for index, tag in enumerate(aliases)}
    return max(
        options,
        default=None,
        key=lambda item: (
            not item.derived_from,
            priorities.get(item.tag, 0),
            bool(item.restatement),
            item.published or item.end,
        ),
    )


def ids(items):
    return list(dict.fromkeys(reference for item in items for reference in [item.id, *item.derived_from]))


def operand(item: Fact) -> Operand:
    quarter = f" Q{item.quarter}" if item.quarter else ""
    label = f"{item.entity or ''} {METRICS.get(item.metric, item.metric)} ({item.start} to {item.end})"
    if item.fiscal_year:
        label = f"{item.entity} {METRICS.get(item.metric, item.metric)} FY{item.fiscal_year}{quarter}"
    return Operand(label=label, value=item.value, unit=item.unit, evidence_ids=ids([item]))


def calculation(label, formula, items, value, unit):
    return Calculation(
        label=label,
        formula=formula,
        operands=[operand(item) for item in items],
        result=value,
        unit=unit,
        evidence_ids=ids(items),
    )


def resolve_period(claim, facts):
    if claim.period or not claim.latest:
        return claim.period
    per_entity = [
        {
            (item.fiscal_year, item.quarter)
            for item in facts
            if isinstance(item, Fact)
            and item.entity == entity
            and item.metric == claim.metric
            and item.fiscal_year
            and item.fiscal_year >= 2022
            and bool(item.quarter) == claim.quarterly
        }
        for entity in claim.entities
    ]
    common = set.intersection(*per_entity)
    if common:
        year, quarter = max(common)
        return Period(year=year, quarter=quarter)
    return None


def financial_calculations(claim, evidence):
    current = resolve_period(claim, evidence)
    if not current:
        return [], "No eligible fiscal period was found before the cutoff."
    periods = required_periods(claim.model_copy(update={"period": current}))
    if claim.measure in {"growth", "acceleration"} and any(
        first.quarter != second.quarter or second.year != first.year + 1
        for first, second in itertools.pairwise(periods)
    ):
        return [], "Year-over-year growth requires consecutive years and the same fiscal quarter."
    if claim.measure == "acceleration" and claim.comparison_period and claim.comparison_period != periods[1]:
        return [], "Growth acceleration compares the current year-over-year growth with the prior year's."
    results = []
    for entity in claim.entities:
        items = [
            select_fact(evidence, entity, claim.metric, period, claim.basis, claim.scope)
            for period in periods
        ]
        if any(item is None for item in items):
            missing = ", ".join(period.label for period, item in zip(periods, items) if item is None)
            return results, f"Missing comparable {claim.metric.replace('_', ' ')} for {entity} {missing}."
        if len({(item.unit, item.currency, bool(item.quarter)) for item in items}) != 1:
            return results, "The operands use different units, currencies, or period lengths."
        if claim.measure == "value":
            results.append(
                calculation(
                    f"{entity} {current.label}", "Reported amount", items, items[-1].value, items[-1].unit
                )
            )
        elif claim.measure == "margin":
            revenue = select_fact(evidence, entity, "revenue", current, claim.basis, claim.scope)
            if claim.metric not in {"gross_profit", "operating_income", "net_income"}:
                return results, "The requested metric does not define a supported margin."
            if (
                not revenue
                or revenue.value <= 0
                or revenue.currency != items[-1].currency
                or (revenue.start, revenue.end) != (items[-1].start, items[-1].end)
            ):
                return (
                    results,
                    "The margin needs positive revenue for the same reporting period and currency.",
                )
            results.append(
                calculation(
                    f"{entity} {current.label} margin",
                    "Profit ÷ revenue × 100",
                    [items[-1], revenue],
                    items[-1].value / revenue.value * 100,
                    "%",
                )
            )
        elif claim.measure == "change":
            results.append(
                calculation(
                    f"{entity} {current.label} change",
                    "Current − prior",
                    items,
                    items[-1].value - items[0].value,
                    items[-1].unit,
                )
            )
        elif claim.measure in {"growth", "acceleration"}:
            if any(item.value <= 0 for item in items[:-1]):
                return results, "Growth requires a positive baseline in each comparison."
            rates = [(new.value - old.value) / old.value * 100 for old, new in itertools.pairwise(items)]
            for index, rate in enumerate(rates):
                results.append(
                    calculation(
                        f"{entity} {periods[index + 1].label} growth",
                        "(Current − prior) ÷ prior × 100",
                        items[index : index + 2],
                        rate,
                        "%",
                    )
                )
            if claim.measure == "acceleration":
                results.append(
                    calculation(
                        f"{entity} {current.label} acceleration",
                        "Current YoY growth − prior YoY growth",
                        items,
                        rates[-1] - rates[0],
                        "pp",
                    )
                )
    return results, None


def numeric_verdict(claim, results):
    result = results[-1]
    value = result.result
    target = claim.target
    if len(claim.entities) == 2 and target is None and claim.assertion != "describe":
        first = next(item for item in reversed(results) if item.label.startswith(claim.entities[0] + " "))
        if first.unit != result.unit:
            return "Unresolved", "The two company results use different units."
        value, target = first.result, result.result
    elif len(claim.entities) == 2 and target is not None:
        return "Unresolved", "Specify the company for the numeric target."
    elif target is not None and result.unit != claim.target_unit:
        return "Unresolved", "The requested and calculated units differ."
    if claim.assertion == "equal" and target is None:
        return "Unresolved", "An equality assertion needs a comparison value."
    if claim.assertion == "equal" and claim.target is not None:
        value = value.quantize(Decimal(1).scaleb(-claim.target_precision), rounding=ROUND_HALF_UP)
    target = target if target is not None else Decimal(0)
    supported = {
        "describe": True,
        "positive": value > target,
        "negative": value < target,
        "equal": value == target,
        "greater": value > target,
        "less": value < target,
    }[claim.assertion]
    values = [f"{item.label}: {render_number(item.result, item.unit)}" for item in results]
    detail = " · ".join(values)
    message = detail + "." if supported else f"The calculated figures contradict the assertion. {detail}."
    return ("Supported" if supported else "Contradicted"), message


_METRIC_WORDS = {
    "revenue": r"\b(?:revenues?|net sales)\b",
    "gross_profit": r"\bgross (?:profit|margin)\b",
    "operating_income": r"\b(?:operating income|income from operations)\b",
    "net_income": r"\bnet (?:income|earnings)\b",
    "diluted_eps": r"\b(?:diluted (?:earnings per share|eps))\b",
}
_RATE = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*%")
_MONEY = re.compile(r"\$\s*(-?\d[\d,]*(?:\.\d+)?)\s*(billion|million|thousand)?", re.IGNORECASE)
_SCALE = {"one": 1, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_SUBJECT = re.compile(
    r"(?:(?:consolidated|total|full.year|quarterly|gaap|adjusted|non-gaap|reported)\s+)*",
    re.IGNORECASE,
)
_GROWTH = re.compile(
    r"\b(growth|grew|up|down|increase\w*|decrease\w*|declin\w*|change\w*|rose|fell)\b",
    re.IGNORECASE,
)


def conflict_figures(claim):
    return (
        claim.figures
        if len(claim.figures) == 2
        else [ConflictFigure(value=value, unit="%", period=claim.period) for value in claim.reported_values]
    )


def _basis(text):
    if re.search(r"constant.currency|exclud\w*.{0,85}foreign exchange", text, re.IGNORECASE):
        return "in constant currency"
    if re.search(r"non.gaap|adjusted", text, re.IGNORECASE):
        return "on an adjusted basis"
    if re.search(r"as reported|\bgaap\b", text, re.IGNORECASE):
        return "as reported"
    return None


def _quoted_value_matches(figure, amount):
    scale = _SCALE[figure.scale]
    precision = Decimal(1).scaleb(figure.value.as_tuple().exponent)
    return (amount / scale).quantize(precision, rounding=ROUND_HALF_UP) == figure.value


def measure_disclosures(claim, figure, evidence):
    """Require the metric to be the subject of the reported measurement."""
    records = {}
    for document in evidence:
        if (
            not isinstance(document, Document)
            or document.entity != claim.entities[0]
            or document.period != (figure.period or claim.period)
            or document.source != "sec"
        ):
            continue
        for passage in document.passages:
            text = passage.lstrip(" •–-·")
            metric = re.search(_METRIC_WORDS[claim.metric], text, re.IGNORECASE)
            if not metric:
                continue
            prefix = text[: metric.start()].strip()
            if figure.scope == "consolidated":
                if prefix and not _SUBJECT.fullmatch(prefix + " "):
                    continue
            elif not re.fullmatch(re.escape(figure.scope) + r"(?: segment)?", prefix, re.IGNORECASE):
                continue
            remainder = text[metric.end() :]
            pattern = (
                _RATE
                if figure.unit == "%" and figure.measure == "growth"
                else (_MONEY if figure.unit in {"USD", "USD/share"} and figure.measure == "value" else None)
            )
            if not pattern or "respectively" in remainder.lower():
                continue
            previous = 0
            for index, match in enumerate(pattern.finditer(remainder)):
                left = remainder[previous : match.start()]
                right = re.split(
                    r"[,;(]|\b(?:and|or)\b",
                    remainder[match.end() : match.end() + 40],
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0]
                previous = match.end()
                if any(
                    re.search(words, left, re.IGNORECASE)
                    for name, words in _METRIC_WORDS.items()
                    if name != claim.metric
                ):
                    break
                if pattern == _RATE and not (_GROWTH.search(left) or index and _basis(right)):
                    continue
                basis = _basis(right) or _basis(left) or (index == 0 and (_basis(prefix) or "as reported"))
                if not basis or (figure.basis and (_basis(figure.basis) or figure.basis) != basis):
                    continue
                value = Decimal(match[1].replace(",", ""))
                if pattern == _MONEY:
                    value *= _SCALE[(match[2] or "one").lower()]
                if re.search(r"(?:down|decreased|declined|fell)\s*(?:by\s*)?$", left, re.IGNORECASE):
                    value = -abs(value)
                if _quoted_value_matches(figure, value):
                    records[basis] = document.id
    return records


def verify_conflict(claim, evidence):
    first, second = conflict_figures(claim)
    matches, display, calculations = [], [], {}
    for figure in (first, second):
        observed = measure_disclosures(claim, figure, evidence)
        if figure.scope == "consolidated":
            baseline = claim.model_copy(
                update={
                    "period": figure.period or claim.period,
                    "measure": figure.measure,
                    "basis": "reported",
                    "scope": "consolidated",
                }
            )
            computed, _ = financial_calculations(baseline, evidence)
            calculations.update({item.label: item for item in computed})
            if (
                computed
                and computed[-1].unit == figure.unit
                and _quoted_value_matches(figure, computed[-1].result)
                and (
                    not figure.basis or (_basis(figure.basis) or figure.basis) in {"reported", "as reported"}
                )
            ):
                observed.setdefault("as reported", computed[-1].evidence_ids[-1])
        matches.append(observed)
        suffix = (
            "%" if figure.unit == "%" else (" " + (figure.scale if figure.scale != "one" else figure.unit))
        )
        display.append(f"{figure.value}{suffix}")
    a, b = matches
    calculations = list(calculations.values())
    calc_refs = list(dict.fromkeys(ref for item in calculations for ref in item.evidence_ids))
    if len(a) == len(b) == 1:
        basis_a, basis_b = next(iter(a)), next(iter(b))
        evidence_ids = list(dict.fromkeys([a[basis_a], b[basis_b], *calc_refs]))
        if basis_a != basis_b:
            reason = f"{display[0]} {basis_a} and {display[1]} {basis_b} use different measurement bases."
            return "Contradicted", reason, calculations, evidence_ids, None
        period_a, period_b = first.period or claim.period, second.period or claim.period
        if period_a != period_b:
            reason = f"{display[0]} covers {period_a.label}. {display[1]} covers {period_b.label}."
            return "Contradicted", reason, calculations, evidence_ids, None
        if first.scope != second.scope or first.measure != second.measure or first.unit != second.unit:
            reason = (
                f"{display[0]} ({first.scope} {first.measure}) and {display[1]} "
                f"({second.scope} {second.measure}) use different measurement definitions."
            )
            return "Contradicted", reason, calculations, evidence_ids, None
        if any(
            item.unit == first.unit
            and _quoted_value_matches(first, item.result)
            and _quoted_value_matches(second, item.result)
            for item in calculations
        ):
            reason = f"{display[0]} and {display[1]} are display forms of the same verified measurement."
            return "Contradicted", reason, calculations, evidence_ids, None
        if (
            a[basis_a] != b[basis_b]
            and first.value * _SCALE[first.scale] != second.value * _SCALE[second.scale]
        ):
            reason = (
                f"The sources give {display[0]} and {display[1]} {basis_a} for the same reporting period."
            )
            return "Supported", reason, calculations, evidence_ids, None
    reason = "The measurement basis, period, or scope of one of the figures is still missing."
    return (
        "Unresolved",
        reason,
        calculations,
        calc_refs,
        Gap(claim_id=claim.id, operation="release", reason=reason),
    )


def verify_macro(claim, evidence):
    facts = sorted(
        (
            item
            for item in evidence
            if isinstance(item, Fact)
            and item.source == "fred"
            and item.metric == claim.series
            and (not claim.window_end or item.end <= claim.window_end)
        ),
        key=lambda item: (item.end, item.retrieved_at),
    )
    if not facts:
        return [], "No eligible observation was available for the requested series and vintage."
    observation = next(
        (item for item in reversed(facts) if not claim.window_start or item.end >= claim.window_start), None
    )
    if not observation:
        return [], "No eligible observation was available for the requested series and vintage."
    if claim.measure == "change":
        start, end = claim.window_start, claim.window_end
        prior = next(
            (
                item
                for item in reversed(facts)
                if start
                and start <= item.end < observation.end
                and (item.end.year, item.end.month) == (start.year, start.month)
            ),
            None,
        )
        if not prior or (end and (observation.end.year, observation.end.month) != (end.year, end.month)):
            return [], "A change requires observations for both endpoint months."
        if prior.unit != observation.unit:
            return [], "The endpoint observations use different units."
        return [
            calculation(
                f"{claim.series} change ({prior.end:%b %Y} to {observation.end:%b %Y})",
                "Ending observation − starting observation",
                [prior, observation],
                observation.value - prior.value,
                "pp" if observation.unit == "%" else observation.unit,
            )
        ], None
    if claim.measure == "growth":
        if claim.series != "CPIAUCSL":
            return [], "Year-over-year macro growth is available for the CPI index."
        prior = next(
            (
                item
                for item in reversed(facts)
                if (item.end.year, item.end.month) == (observation.end.year - 1, observation.end.month)
            ),
            None,
        )
        if not prior or prior.value <= 0:
            return [], "Year-over-year CPI requires the observation for the same month one year earlier."
        return [
            calculation(
                f"Year-over-year CPI growth ({observation.end:%b %Y})",
                "(Current CPI − prior CPI) ÷ prior CPI × 100",
                [prior, observation],
                (observation.value - prior.value) / prior.value * 100,
                "%",
            )
        ], None
    if claim.measure != "value":
        return [], "The requested measure is unavailable for this macroeconomic series."
    return [
        calculation(
            f"{claim.series} ({observation.end:%b %Y})",
            "Published observation",
            [observation],
            observation.value,
            observation.unit,
        )
    ], None


_EXPLANATION_REQUEST = re.compile(
    r"\b(?:why|drove|drivers?|explain\w*|explanations?|reasons?|caus(?:e[ds]?|al|ing))\b",
    re.IGNORECASE,
)
_STATED_DRIVER = re.compile(
    r"\b(?:driven by|due to|reflect(?:ed|s)|resulted from|attribut(?:ed|able) to|contributed to|because of)\b",
    re.IGNORECASE,
)
_TENTATIVE_DRIVER = re.compile(r"\b(?:no|not|never|may|might|could|would|can)\b", re.IGNORECASE)
_OBSERVED_CHANGE = re.compile(
    r"\b(?:increas(?:e|ed)|decreas(?:e|ed)|rose|fell|grew|declin(?:e|ed)|expanded|contracted|"
    r"improv(?:ed|ement)|chang(?:e|ed)|growth|(?:as )?compared (?:to|with))\b",
    re.IGNORECASE,
)


def asks_for_explanation(text):
    return bool(_EXPLANATION_REQUEST.search(text))


def named_metrics(text):
    text = re.sub(r"\bcosts? of (?:net )?(?:revenues?|sales)\b", "", text, flags=re.IGNORECASE)
    return {key for key, pattern in _METRIC_WORDS.items() if re.search(pattern, text, re.IGNORECASE)}


def _disclosed_driver_cause(text, metrics, *, metric_first=False):
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if (driver := _STATED_DRIVER.search(sentence)) and not _TENTATIVE_DRIVER.search(sentence):
            outcome = sentence[: driver.start()]
            if metric_first and not any(
                re.match(
                    r"\s*(?:(?:total|consolidated|reported)\s+)*" + _METRIC_WORDS[metric],
                    outcome,
                    re.IGNORECASE,
                )
                for metric in metrics
            ):
                continue
            if _OBSERVED_CHANGE.search(outcome) and (not metrics or metrics & named_metrics(outcome)):
                cause = sentence[driver.end() :].strip()
                if cause:
                    return cause
    return None


def disclosed_driver(text, metrics=frozenset(), *, metric_first=False):
    return _disclosed_driver_cause(text, metrics, metric_first=metric_first) is not None


def _is_company_explanation(claim, document, attribution):
    return (
        attribution.role == "explanation"
        and document.source == "sec"
        and document.entity in claim.entities
        and (not claim.event_date or document.published == claim.event_date)
    )


def _explanation_metrics(claim):
    return {claim.metric} if claim.metric else named_metrics(claim.event or "")


def company_disclosed_cause(claim, document, attribution):
    if _is_company_explanation(claim, document, attribution):
        return _disclosed_driver_cause(attribution.quote, _explanation_metrics(claim))
    return None


def valid_attributions(claims, evidence, proposed):
    documents = {item.id: item for item in evidence if isinstance(item, Document) and item.published}
    by_claim = {claim.id: claim for claim in claims}
    checked = []
    for item in proposed:
        document = documents.get(item.evidence_id)
        claim = by_claim.get(item.claim_id)
        if (
            document
            and claim
            and (not document.entity or document.entity in claim.entities)
            and (not claim.period or not document.period or claim.period == document.period)
            and any(
                " ".join(item.quote.split()) in " ".join(passage.split()) for passage in document.passages
            )
        ):
            checked.append(item)
    return checked


def company_disclosures(claims, records, checked):
    return [
        item
        for item in checked
        if company_disclosed_cause(claims[item.claim_id], records[item.evidence_id], item)
    ]


_PARTIAL_OFFSET = re.compile(r"\b(?:partly|partially) offset by\s+(?P<cost>.+?)[.!?]?$", re.IGNORECASE)
_QUANTIFIED_FX = re.compile(
    r"Changes in foreign exchange rates (?P<sign>positively|negatively) impacted "
    r"(?P<metric>[a-z ]+) by (?P<amount>\$[\d,.]+ (?:thousand|million|billion))\b.*",
    re.IGNORECASE,
)


def disclosure_qualifiers(claim, document, attribution):
    """Find explicit qualifications in the same filing paragraph as a verified driver."""
    anchor = re.split(r"(?<=[.!?])\s+", " ".join(attribution.quote.split()), maxsplit=1)[0]
    metrics = _explanation_metrics(claim)
    for passage in document.passages:
        sentences = re.split(r"(?<=[.!?])\s+", " ".join(passage.split()))
        for index, sentence in enumerate(sentences):
            # Match the driver sentence itself, then consider only the immediately following FX sentence.
            if anchor not in sentence or not (cause := _disclosed_driver_cause(sentence, metrics)):
                continue
            qualifiers = []
            if offset := _PARTIAL_OFFSET.search(cause):
                subject = re.split(r"\s+that (?:was|were|is|are)\s+", offset["cost"], maxsplit=1)[0]
                qualifiers.append(("offset", subject, offset[0].rstrip(".!?")))
            if index + 1 < len(sentences) and (fx := _QUANTIFIED_FX.fullmatch(sentences[index + 1])):
                outcome = _STATED_DRIVER.split(sentence, maxsplit=1)[0]
                if named_metrics(fx["metric"]) & named_metrics(outcome):
                    verb = "added" if fx["sign"].lower() == "positively" else "reduced"
                    qualifiers.append(("fx", (verb, fx["amount"], fx["metric"].lower()), fx[0]))
            return qualifiers
    return []


def unsummarized_disclosures(disclosures, summaries):
    covered = {
        (summary["claim_id"], ref["evidence_id"], ref["quote"])
        for summary in summaries
        for ref in summary["quotes"]
    }
    return [item for item in disclosures if (item.claim_id, item.evidence_id, item.quote) not in covered]


_SUMMARY_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def verified_disclosure_summaries(attributions, claims, evidence):
    """Keep synthesis only when its cited excerpts survive source and disclosure checks."""
    checked = valid_attributions(claims, evidence, attributions.entries)
    by_claim = {claim.id: claim for claim in claims}
    by_id = {item.id: item for item in evidence}
    eligible = {
        index: item
        for index, item in enumerate(attributions.entries)
        if item in checked and _is_company_explanation(by_claim[item.claim_id], by_id[item.evidence_id], item)
    }
    drivers = {
        index
        for index, item in eligible.items()
        if company_disclosed_cause(by_claim[item.claim_id], by_id[item.evidence_id], item)
    }
    summaries = []
    for item in attributions.summaries:
        indices = list(dict.fromkeys(item.quote_indices))
        refs = [eligible.get(index) for index in indices]
        if any(not ref or ref.claim_id != item.claim_id for ref in refs):
            continue
        # Context may supplement a driver from the same filing and claim.
        driver_documents = {eligible[index].evidence_id for index in indices if index in drivers}
        metrics = _explanation_metrics(by_claim[item.claim_id])
        if any(
            ref.evidence_id not in driver_documents
            or (index not in drivers and not metrics & named_metrics(ref.quote))
            for index, ref in zip(indices, refs, strict=True)
        ):
            continue
        text = " ".join(item.text.split())
        paraphrase = text.strip(' .“”"').casefold()
        quotes = [" ".join(ref.quote.split()).casefold() for ref in refs]
        source_numbers = set(_SUMMARY_NUMBER.findall(" ".join(quotes)))
        if not paraphrase or text[-1:] not in {".", "!", "?"} or any(paraphrase in quote for quote in quotes):
            continue
        if set(_SUMMARY_NUMBER.findall(text)) - source_numbers:
            continue
        summaries.append(
            {
                "claim_id": item.claim_id,
                "text": text,
                "quotes": [{"quote": ref.quote, "evidence_id": ref.evidence_id} for ref in refs],
            }
        )
    return summaries


def verify(
    claims: list[Claim], evidence: list[EvidenceRecord], attributions: list[Attribution] | None = None
):
    findings, gaps = [], []
    attributions = valid_attributions(claims, evidence, attributions or [])
    for claim in claims:
        calculations, references, gap = [], [], None
        if claim.kind in {"financial", "macro"}:
            calculations, problem = (
                financial_calculations(claim, evidence)
                if claim.kind == "financial"
                else verify_macro(claim, evidence)
            )
            if problem:
                verdict, reason = "Unresolved", problem
                operation = "observations" if claim.kind == "macro" else "filing"
                if claim.kind == "financial" and claim.period and claim.period.quarter == 4:
                    operation = "release"
                if problem.startswith(("Missing", "No eligible", "The margin needs")):
                    gap = Gap(claim_id=claim.id, operation=operation, reason=problem)
            else:
                verdict, reason = numeric_verdict(claim, calculations)
            references = list(dict.fromkeys(ref for item in calculations for ref in item.evidence_ids))
        elif claim.kind == "conflict":
            verdict, reason, calculations, references, gap = verify_conflict(claim, evidence)
        else:
            related = [item for item in attributions if item.claim_id == claim.id]
            references = list(dict.fromkeys(item.evidence_id for item in related))
            relevant_documents = {item.id: item for item in evidence if isinstance(item, Document)}
            if claim.kind == "event" and any(
                (
                    item.role == "event"
                    and (
                        not claim.event_date
                        or relevant_documents[item.evidence_id].published == claim.event_date
                        or claim.event_date.isoformat() in item.quote
                    )
                )
                or (
                    asks_for_explanation(claim.event)
                    and company_disclosed_cause(claim, relevant_documents[item.evidence_id], item)
                )
                for item in related
            ):
                verdict, reason = "Supported", "Dated source evidence documents the event."
            else:
                # An attributed explanation is valuable context. Causality requires an identified study.
                causal = any(
                    item.role == "causal_evidence"
                    and relevant_documents[item.evidence_id].independent
                    and re.search(
                        r"causal|counterfactual|randomized|natural experiment", item.quote, re.IGNORECASE
                    )
                    and not re.search(r"\b(?:no|not|cannot|correlat\w*)\b", item.quote, re.IGNORECASE)
                    for item in related
                )
                if claim.kind == "causal" and causal:
                    verdict, reason = "Supported", "Independent evidence identifies the stated relationship."
                else:
                    verdict, reason = (
                        "Unresolved",
                        (
                            "The cited observations provide context without establishing the claimed cause."
                            if claim.kind == "causal"
                            else "No dated, relevant passage verified the event."
                        ),
                    )
                    if claim.kind == "event":
                        gap = Gap(claim_id=claim.id, operation="articles", reason=reason)
            if claim.kind == "causal":
                references.extend(item.id for item in evidence if item.source == "fred")
        if verdict != "Unresolved" and not references:
            verdict, reason = "Unresolved", "No eligible supporting citation was found."
        findings.append(
            Finding(
                claim_id=claim.id,
                label=claim.label,
                verdict=verdict,
                reason=reason,
                evidence_ids=references,
                calculations=calculations,
            )
        )
        if gap:
            gaps.append(gap)
    return findings, gaps


def overall(findings: list[Finding]) -> str:
    verdicts = {item.verdict for item in findings}
    if {"Supported", "Unresolved"} <= verdicts:
        return "Partially supported"
    if "Contradicted" in verdicts:
        return "Contradicted"
    if verdicts == {"Supported"}:
        return "Supported"
    return "Unresolved"
