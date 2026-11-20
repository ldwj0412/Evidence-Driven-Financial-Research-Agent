"""Compose report content from verified findings and attributed passages."""

import re
from decimal import Decimal

from .domain import COMPANIES, METRICS, Calculation, Fact, Operand, Period
from .verification import (
    asks_for_explanation,
    calculation,
    company_disclosures,
    disclosure_qualifiers,
    render_number,
    resolve_period,
    unsummarized_disclosures,
    valid_attributions,
)

_YES_NO = re.compile(r"^\s*(?:am|are|is|was|were|did|does|do|has|have|had)\b", re.IGNORECASE)
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


def _measurement_key(item):
    return (
        item.formula,
        item.unit,
        item.result,
        tuple((operand.unit, operand.value, tuple(operand.evidence_ids)) for operand in item.operands),
    )


def _metric(claim):
    if claim.measure == "margin":
        return _MARGIN_NAMES.get(claim.metric, "margin")
    metric = METRICS[claim.metric].lower()
    return metric + {"growth": " growth", "acceleration": " growth acceleration", "change": " change"}.get(
        claim.measure, ""
    )


def _comparison(claim, finding, evidence):
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


def _financial_answer(claim, finding, evidence, question):
    period = resolve_period(claim, evidence)
    if len(claim.entities) != 1 or not period or not finding.calculations:
        return finding.reason
    (ticker,) = claim.entities
    subject = f"{COMPANIES[ticker][0]}’s {period.label} {_metric(claim)}"
    prefix = ""
    if claim.assertion != "describe" and _YES_NO.search(question):
        prefix = "Yes. " if finding.verdict == "Supported" else "No. "
    result = finding.calculations[-1].result
    if claim.target is None and claim.assertion in {"positive", "negative", "describe"}:
        if claim.measure == "acceleration":
            outcome = "accelerated" if result > 0 else "slowed" if result < 0 else "held steady"
            return f"{prefix}{subject.removesuffix(' acceleration')} {outcome}."
        if claim.measure in {"growth", "change"}:
            subject = subject.removesuffix(" growth").removesuffix(" change")
            outcome = "increased" if result > 0 else "decreased" if result < 0 else "was unchanged"
            return f"{prefix}{subject} {outcome}."
    return prefix + finding.reason


def _growth_sentence(claim, finding, records):
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
    return (
        f"{company}’s {metric} {direction} {change}, from {prior} in {before} to {new} in {after}.",
        _measurement_key(item),
    )


def _highlights(findings, claims, records, inline_measurements):
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
                label = f"{COMPANIES[fact.entity][0]} {METRICS.get(fact.metric, fact.metric).lower()}"
            elif fact and fact.entity and claim.kind == "financial":
                label = f"{COMPANIES[fact.entity][0]} {_metric(claim)}"
                if claim.measure == "acceleration" and item.unit == "%":
                    label = label.removesuffix(" acceleration")
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


def _explanations(claims, records, attributions, question):
    asks_why = asks_for_explanation(question) and any(
        claim.kind in {"event", "causal"} for claim in claims.values()
    )
    explanations = company_disclosures(claims, records, attributions) if asks_why else []
    note = (
        "The retrieved sources did not verify a company-disclosed explanation."
        if asks_why and not explanations
        else None
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


def _qualifier_text(kind, value, summary):
    if kind == "offset":
        if _offset_is_covered(value, summary):
            return None
        return f"{value[0].upper()}{value[1:]} partly offset the change."
    verb, amount, metric = value
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


def _disclosure_content(explanations, attributions, summaries, claims, records):
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
            or text.casefold() in seen
            or not set(keys) <= accepted
            or any(ref["evidence_id"] not in driver_documents for ref in quotes)
        ):
            continue
        point = {"text": text, "evidence_ids": list(dict.fromkeys(ref["evidence_id"] for ref in quotes))}
        details = {}
        for kind, value, _ in (qualifier for key in point_drivers for qualifier in drivers[key]):
            detail = _qualifier_text(kind, value, point["text"])
            if not detail:
                continue
            if kind == "fx" and len(point_drivers) == 1:
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


def _event_caption(claim, document, quote, evidence):
    day = claim.event_date or document.published
    event_date = f"{day:%B} {day.day}, {day.year}"
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
            company = COMPANIES[document.entity][0]
            return f"{company} announced its {period.label} results on {event_date}."
    source = document.publisher or document.title
    return f"{source} reported “{quote}”"


def report_content(parsed, findings, evidence, attributions, question, disclosure_summaries=()):
    claims = {claim.id: claim for claim in parsed.claims}
    records = {item.id: item for item in evidence}
    attributions = valid_attributions(parsed.claims, evidence, attributions)
    explanations, note = _explanations(claims, records, attributions, question)
    explained = {item.claim_id for item in explanations}
    points, quotes, incomplete = _disclosure_content(
        explanations, attributions, disclosure_summaries, claims, records
    )
    show_quotes = bool(quotes and _QUOTE_REQUEST.search(question) and not _QUOTE_OPT_OUT.search(question))
    if points and incomplete and not show_quotes:
        note = "Additional company-disclosed drivers are quoted under Supporting details."
    separate_event = any(
        claim.kind == "event" and not asks_for_explanation(claim.event) for claim in claims.values()
    )
    answers, event_answers, differences = [], [], []
    inline_measurements = set()
    for finding in findings:
        claim = claims[finding.claim_id]
        answer, refs = finding.reason, finding.evidence_ids
        if claim.kind == "financial" and (comparison := _comparison(claim, finding, evidence)):
            answer, difference = comparison
            refs = difference.evidence_ids
            differences.append(difference.model_dump(mode="json"))
        elif finding.verdict != "Unresolved" and claim.kind in {"financial", "macro"}:
            has_direction = claim.kind == "financial" and claim.measure in {
                "growth",
                "change",
                "acceleration",
            }
            if claim.assertion == "describe" and finding.calculations and not has_direction:
                # The sourced figure card is the direct answer to a descriptive numeric question.
                continue
            if claim.kind == "financial":
                answer = _financial_answer(claim, finding, evidence, question)
                if (
                    explained
                    and not separate_event
                    and claim.assertion == "describe"
                    and (growth := _growth_sentence(claim, finding, records))
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
                answer = _event_caption(claim, document, attributed_event.quote, evidence)
                refs = [document.id]
            elif claim.id in explained:
                # Verified disclosure excerpts are displayed with their citations below the answer.
                continue
        answer_row = {"text": answer, "evidence_ids": refs, "verdict": finding.verdict}
        group = event_answers if claim.kind == "event" and finding.verdict == "Supported" else answers
        if answer_row not in group:
            group.append(answer_row)
    return {
        "answers": event_answers + answers,
        "highlights": _highlights(findings, claims, records, inline_measurements),
        "disclosure_summary": points,
        "explanations": quotes,
        "show_disclosure_quotes": show_quotes,
        "explanation_note": note,
        "comparison_calculations": differences,
    }
