from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from financial_research.domain import Attribution, Attributions, Claim, Document, Fact, Parsed, Period
from financial_research.providers import relevant_passages
from financial_research.reporting import report_content
from financial_research.verification import verified_disclosure_summaries, verify


def fact(entity, value, *, year=2024, metric="revenue"):
    end = date(year, 6, 30) if entity == "MSFT" else date(year, 1, 28)
    return Fact(
        id=f"{entity}-{year}-{metric}",
        source="sec",
        title=f"{entity} Form 10-K",
        url=f"https://www.sec.gov/Archives/{entity}",
        entity=entity,
        fiscal_year=year,
        metric=metric,
        start=date(year - 1, 7, 1) if entity == "MSFT" else date(year - 1, 1, 29),
        end=end,
        value=Decimal(value),
        currency="USD",
        unit="USD",
        retrieved_at=datetime(2024, 8, 1, tzinfo=UTC),
    )


def claim(id="compare", **changes):
    return Claim.model_validate(
        {
            "id": id,
            "label": "Microsoft FY2024 revenue compared with NVIDIA",
            "kind": "financial",
            "entities": ["MSFT", "NVDA"],
            "metric": "revenue",
            "period": {"year": 2024},
            "assertion": "greater",
            **changes,
        }
    )


def report(claims, evidence, attributions=(), question=""):
    findings, _ = verify(claims, evidence, list(attributions))
    return report_content(Parsed(claims=claims), findings, evidence, list(attributions), question)


def test_comparison_answers_directly_and_deduplicates_repeated_measurements():
    left, right = fact("MSFT", 245_122_000_000), fact("NVDA", 60_922_000_000)
    claims = [
        claim("msft", entities=["MSFT"], assertion="describe"),
        claim("nvda", entities=["NVDA"], assertion="describe"),
        claim(),
    ]
    result = report(claims, [left, right])
    assert [item["text"] for item in result["answers"]] == [
        "Yes. Microsoft’s FY2024 revenue was $184.20B higher than NVIDIA’s."
    ]
    assert result["answers"][0]["evidence_ids"] == [left.id, right.id]
    assert [entry["period_end"] for entry in result["highlights"]] == ["2024-06-30", "2024-01-28"]
    assert [item["calculation"]["evidence_ids"] for item in result["highlights"]] == [[left.id], [right.id]]
    (delta,) = result["comparison_calculations"]
    assert Decimal(delta["result"]) == 184_200_000_000
    assert delta["evidence_ids"] == [left.id, right.id]
    assert [Decimal(operand["value"]) for operand in delta["operands"]] == [left.value, right.value]


@pytest.mark.parametrize(
    ("right_value", "answer"),
    [
        (258_000_000_000, "No. Microsoft’s FY2024 revenue was $12.88B lower than NVIDIA’s."),
        (245_122_000_000, "No. Microsoft and NVIDIA reported equal FY2024 revenue."),
        (
            None,
            "The available evidence couldn’t verify the requested figure. Missing comparable revenue for NVIDIA FY2024.",
        ),
    ],
)
def test_comparison_states_the_observed_relationship(right_value, answer):
    evidence = [fact("MSFT", 245_122_000_000)]
    if right_value is not None:
        evidence.append(fact("NVDA", right_value))
    result = report([claim()], evidence)
    assert result["answers"][0]["text"] == answer
    assert bool(result["comparison_calculations"]) == (right_value is not None)


def test_distinct_metrics_remain_visible_when_their_display_values_match():
    income = fact("MSFT", 1_000_000_001, metric="operating_income")
    revenue = fact("MSFT", 1_000_000_000)
    claims = [
        claim("income", entities=["MSFT"], metric="operating_income", assertion="describe"),
        claim("revenue", entities=["MSFT"], assertion="describe"),
    ]
    result = report(claims, [income, revenue])
    assert [item["text"] for item in result["answers"]] == [
        "Microsoft’s FY2024 operating income was $1.00B.",
        "Microsoft’s FY2024 revenue was $1.00B.",
    ]
    assert [item["calculation"]["evidence_ids"] for item in result["highlights"]] == [
        [income.id],
        [revenue.id],
    ]


def test_reverse_order_change_labels_the_calculated_period():
    comparison = claim(
        entities=["MSFT"], assertion="describe", measure="change", comparison_period={"year": 2025}
    )
    result = report([comparison], [fact("MSFT", 75), fact("MSFT", 100, year=2025)])
    (highlight,) = result["highlights"]
    assert highlight["period"] == "FY2024"
    assert highlight["period_end"] == "2024-06-30"
    assert "decreased by $25.00" in result["answers"][0]["text"]


def test_macro_conclusion_names_the_series_and_verified_month():
    observation = Fact(
        id="rates",
        source="fred",
        title="Federal Funds Effective Rate",
        url="https://fred.stlouisfed.org/series/FEDFUNDS",
        metric="FEDFUNDS",
        start=date(2024, 7, 1),
        end=date(2024, 7, 1),
        value=Decimal("5.33"),
        unit="%",
        vintage=date(2024, 8, 1),
        retrieved_at=datetime(2024, 8, 1, tzinfo=UTC),
    )
    request = Claim(
        id="macro",
        label="Federal funds rate",
        kind="macro",
        entities=[],
        series="FEDFUNDS",
        measure="value",
        assertion="describe",
        window_end=date(2024, 7, 31),
    )
    result = report([request], [observation], question="What was the federal funds rate in July 2024?")
    assert result["answers"][0]["text"] == "The federal funds rate was 5.33% in July 2024."
    assert result["answers"][0]["evidence_ids"] == [observation.id]


def test_causal_question_leads_with_uncertainty_before_the_verified_growth():
    growth = claim("growth", entities=["MSFT"], measure="growth", assertion="positive")
    cause = claim("cause", entities=["MSFT"], kind="causal", event="Rates drove revenue growth")
    result = report(
        [growth, cause],
        [fact("MSFT", 100_000_000, year=2023), fact("MSFT", 120_000_000)],
        question="Did interest rates explain Microsoft’s FY2024 revenue growth?",
    )
    assert [item["text"] for item in result["answers"]] == [
        "The available evidence leaves the cause unresolved.",
        "Microsoft’s FY2024 revenue increased by 20.00% compared with FY2023.",
    ]


def test_targeted_filing_excerpt_ranking_keeps_observed_drivers():
    relevant = "Revenue increased in FY2024, driven by growth in Azure and Office 365 Commercial."
    others = [
        f"Microsoft FY2024 Windows revenue increased in section {i}, driven by expansion." for i in range(24)
    ]
    query = "Microsoft FY2024 explanation of what drove the increase in revenue"
    assert relevant_passages([*others, relevant], query, maximum=12)[0] == relevant


@pytest.mark.parametrize(
    "quote",
    [
        "The increase in North America operating income in 2024 is primarily due to increased unit sales.",
        (
            "International operating income in 2024, as compared to the operating loss in the prior year, "
            "is primarily due to increased advertising sales."
        ),
    ],
)
def test_disclosed_drivers_accept_increase_nouns_and_comparative_wording(quote):
    document = Document(
        id="filing",
        title="Amazon Form 10-K",
        url="https://www.sec.gov/Archives/amazon",
        source="sec",
        entity="AMZN",
        period=Period(year=2024),
        passages=[quote],
        published=date(2025, 2, 7),
        retrieved_at=datetime(2025, 2, 8, tzinfo=UTC),
    )
    claims = [
        claim(
            "drivers",
            entities=["AMZN"],
            kind="event",
            metric=None,
            event="What did Amazon say drove operating income?",
        )
    ]
    attributions = [Attribution(claim_id="drivers", evidence_id=document.id, role="explanation", quote=quote)]
    findings, _ = verify(claims, [document], attributions)
    assert findings[0].verdict == "Supported"
    result = report(claims, [document], attributions, claims[0].event)
    assert result["explanations"] == [{"quote": quote, "evidence_id": document.id}]


def test_growth_and_disclosure_report_leads_with_verified_values_instead_of_quote_fragment():
    opening = "The increase in North America operating income in 2024, compared to the prior year"
    explanation = opening + " is primarily due to increased unit sales and increased advertising sales."
    context = "Foreign exchange did not significantly affect North America operating income in 2024."
    document = Document(
        id="annual-filing",
        title="Amazon Form 10-K",
        url="https://www.sec.gov/Archives/amazon",
        source="sec",
        entity="AMZN",
        publisher="Amazon",
        period=Period(year=2024),
        passages=[explanation, context],
        published=date(2025, 2, 7),
        retrieved_at=datetime(2025, 2, 8, tzinfo=UTC),
    )
    earlier = fact("AMZN", 36_852_000_000, metric="operating_income", year=2023)
    current = fact("AMZN", 68_593_000_000, metric="operating_income")
    claims = [
        claim("growth", entities=["AMZN"], metric="operating_income", measure="growth", assertion="describe"),
        claim(
            "drivers",
            entities=["AMZN"],
            kind="event",
            metric=None,
            event="What did Amazon say drove the increase in operating income?",
        ),
    ]
    attributions = [
        Attribution(claim_id="drivers", evidence_id=document.id, role="event", quote=opening),
        Attribution(claim_id="drivers", evidence_id=document.id, role="explanation", quote=explanation),
        Attribution(claim_id="drivers", evidence_id=document.id, role="explanation", quote=context),
    ]
    source_text = "Amazon credited higher unit and ad sales for North America’s improvement. Currency effects were immaterial."
    wrong = "Amazon attributed 99% of the increase to higher unit sales."
    proposed = Attributions.model_validate(
        {
            "entries": [item.model_dump() for item in attributions],
            "summaries": [
                {"claim_id": "drivers", "text": source_text, "quote_indices": [1, 2]},
                {"claim_id": "drivers", "text": source_text, "quote_indices": [0]},
                {"claim_id": "drivers", "text": source_text, "quote_indices": [2]},
                {"claim_id": "drivers", "text": source_text, "quote_indices": [9]},
                {"claim_id": "growth", "text": source_text, "quote_indices": [1]},
                {"claim_id": "drivers", "text": wrong, "quote_indices": [1]},
                {"claim_id": "drivers", "text": explanation, "quote_indices": [1]},
                {"claim_id": "drivers", "text": "Amazon said higher unit and", "quote_indices": [1]},
            ],
        }
    )
    evidence = [earlier, current, document]
    summaries = verified_disclosure_summaries(proposed, claims, evidence)
    assert [item["text"] for item in summaries] == [source_text]
    findings, _ = verify(claims, evidence, attributions)
    parsed = Parsed(claims=claims)
    result = report_content(
        parsed, findings, evidence, attributions, "How much did it grow and what drove it?", summaries
    )
    assert [item["text"] for item in result["answers"]] == [
        "Amazon’s operating income grew 86.13%, from $36.85B in FY2023 to $68.59B in FY2024.",
    ]
    assert result["answers"][0]["evidence_ids"] == [earlier.id, current.id]
    assert result["disclosure_summary"] == [{"text": source_text, "evidence_ids": [document.id]}]
    assert result["show_disclosure_quotes"] is False
    assert result["highlights"][0]["in_answer"]
    assert result["explanations"] == [
        {"quote": explanation, "evidence_id": document.id},
        {"quote": context, "evidence_id": document.id},
    ]
    quoted = report_content(
        parsed, findings, evidence, attributions, "What drove growth? Quote the filing.", summaries
    )
    assert quoted["show_disclosure_quotes"] is True

    no_quotes = report_content(
        parsed, findings, evidence, attributions, "What drove growth? No direct quotes.", summaries
    )
    assert no_quotes["show_disclosure_quotes"] is False


def test_disclosure_summary_recovers_qualifiers_from_the_matching_filing_paragraph():
    quote = "The increase in Cloud operating income in 2024 was primarily due to increased sales"
    offset = "partially offset by spending on technology infrastructure"
    fx = "Changes in foreign exchange rates positively impacted operating income by $240 million in 2024."
    document = Document(
        id="filing",
        title="Annual filing",
        url="https://www.sec.gov/Archives/filing",
        source="sec",
        entity="AMZN",
        period=Period(year=2024),
        passages=[
            f"{quote}, {offset}. {fx}",
            "The increase in operating income was due to higher volume, partially offset by legal costs.",
        ],
        published=date(2025, 2, 7),
        retrieved_at=datetime(2025, 2, 8, tzinfo=UTC),
    )
    driver = claim(
        "drivers",
        entities=["AMZN"],
        kind="event",
        metric=None,
        event="What drove the increase in operating income?",
    )
    entries = [Attribution(claim_id="drivers", evidence_id=document.id, role="explanation", quote=quote)]
    findings, _ = verify([driver], [document], entries)
    summary = "The company attributed Cloud’s operating-income increase to higher sales."
    quantified_fx = "Foreign exchange added $240 million to operating income."
    for text, expected_text, expected_details in [
        (
            summary,
            summary,
            [
                "Spending on technology infrastructure partly offset the change.",
                quantified_fx,
            ],
        ),
        (
            summary + " Technology infrastructure spending partly offset the gains.",
            summary + " Technology infrastructure spending partly offset the gains.",
            [quantified_fx],
        ),
        (
            summary + " Infrastructure spending partly offset these gains; foreign exchange helped.",
            summary + " Infrastructure spending partly offset these gains. " + quantified_fx,
            [],
        ),
        (
            summary + " Advertising spending partly offset these gains.",
            summary + " Advertising spending partly offset these gains.",
            ["Spending on technology infrastructure partly offset the change.", quantified_fx],
        ),
        (
            summary + " Growth-related infrastructure spending partly offset gains.",
            summary + " Growth-related infrastructure spending partly offset gains.",
            [quantified_fx],
        ),
    ]:
        proposed = Attributions(
            entries=entries, summaries=[{"claim_id": "drivers", "text": text, "quote_indices": [0]}]
        )
        summaries = verified_disclosure_summaries(proposed, [driver], [document])
        result = report_content(
            Parsed(claims=[driver]), findings, [document], entries, "What drove the increase?", summaries
        )
        (point,) = result["answers"]
        assert point["text"] == " ".join([expected_text, *expected_details])
        assert point["evidence_ids"] == [document.id]
        assert not result["show_disclosure_quotes"]
        assert {item["quote"] for item in result["explanations"]} >= {offset, fx}
        assert all("legal costs" not in item["quote"] for item in result["explanations"])


def test_event_report_quotes_verified_company_drivers_and_shows_revenue_and_growth():
    announcement = "Microsoft announced its financial results for the fiscal year ended June 30, 2024."
    driver = "Revenue growth was driven by Azure and Office 365 Commercial."
    wrong_metric = "Cost of revenue increased 13% driven by growth in Microsoft Cloud and Gaming."
    risk = "Revenue may decline due to inflation."
    negation = "Revenue was not driven by changes in interest rates."
    release = Document(
        id="earnings",
        title="Microsoft earnings release",
        url="https://www.sec.gov/Archives/earnings",
        source="sec",
        entity="MSFT",
        publisher="Microsoft",
        period=Period(year=2024),
        passages=[announcement, driver, wrong_metric, risk, negation],
        published=date(2024, 7, 30),
        retrieved_at=datetime(2024, 8, 1, tzinfo=UTC),
    )
    evidence = [fact("MSFT", 211_915_000_000, year=2023), fact("MSFT", 245_122_000_000), release]
    claims = [
        claim("growth", entities=["MSFT"], assertion="describe", measure="growth"),
        claim(
            "event", entities=["MSFT"], kind="event", event="earnings report", event_date=date(2024, 7, 30)
        ),
        claim("drivers", entities=["MSFT"], kind="event", event="What the company said drove revenue growth"),
    ]
    attributions = [
        Attribution(
            claim_id="event" if role == "event" else "drivers", evidence_id=release.id, role=role, quote=quote
        )
        for role, quote in [
            ("event", announcement),
            ("explanation", driver),
            ("explanation", wrong_metric),
            ("explanation", risk),
            ("explanation", negation),
            ("explanation", "The source did not actually contain this sentence."),
        ]
    ]
    findings, _ = verify(claims, evidence, attributions)
    assert [item.verdict for item in findings] == ["Supported", "Supported", "Supported"]
    result = report(claims, evidence, attributions, "What did Microsoft announce and what drove its growth?")
    assert result["answers"][0]["text"] == "Microsoft announced its FY2024 results on July 30, 2024."
    assert result["answers"][0]["evidence_ids"] == [release.id]
    assert [explanation["quote"] for explanation in result["explanations"]] == [driver]
    assert result["explanations"][0]["evidence_id"] == release.id
    assert [item["calculation"]["unit"] for item in result["highlights"]] == ["USD", "%"]

    mismatched = release.model_copy(update={"period": Period(year=2025)})
    result = report(claims, [*evidence[:-1], mismatched], attributions, "What drove the increase?")
    assert [item["verdict"] for item in result["answers"]] == ["Supported", "Unresolved"]
    assert result["explanations"] == []
    assert result["explanation_note"]
