from datetime import UTC, date, datetime
from decimal import Decimal

from financial_research.domain import Claim, Document, Fact, Period
from financial_research.verification import render_number, verify


def annual(metric, year, value):
    return Fact(
        id=f"{metric}-{year}",
        source="sec",
        url="https://www.sec.gov/Archives/example",
        title="Annual filing",
        published=date(2024, 7, 30),
        retrieved_at=datetime(2024, 8, 1, tzinfo=UTC),
        entity="MSFT",
        metric=metric,
        fiscal_year=year,
        start=date(year - 1, 7, 1),
        end=date(year, 6, 30),
        value=Decimal(value),
        unit="USD",
        currency="USD",
    )


def test_reference_growth_acceleration_uses_unrounded_operands():
    claim = Claim(
        id="c1",
        label="Revenue accelerated",
        kind="financial",
        entities=["MSFT"],
        metric="revenue",
        period=Period(year=2024),
        measure="acceleration",
        assertion="positive",
    )
    evidence = [
        annual("revenue", year, value) for year, value in [(2022, 198270), (2023, 211915), (2024, 245122)]
    ]
    findings, gaps = verify([claim], evidence)
    assert not gaps
    assert findings[0].verdict == "Supported"
    assert [render_number(calc.result, calc.unit) for calc in findings[0].calculations] == [
        "6.88%",
        "15.67%",
        "8.79 pp",
    ]
    assert findings[0].calculations[-1].evidence_ids == [item.id for item in evidence]


def test_negative_growth_baseline_is_unresolved():
    claim = Claim(
        id="c1",
        label="Net income increased",
        kind="financial",
        entities=["MSFT"],
        metric="net_income",
        period=Period(year=2024),
        measure="growth",
        assertion="positive",
    )
    findings, _ = verify([claim], [annual("net_income", 2023, -1), annual("net_income", 2024, 2)])
    assert findings[0].verdict == "Unresolved"
    assert "positive baseline" in findings[0].reason


def test_rate_change_compares_both_endpoint_observations():
    claim = Claim(
        id="c1",
        label="Effective federal funds rate change",
        kind="macro",
        entities=[],
        series="FEDFUNDS",
        measure="change",
        window_start=date(2024, 5, 1),
        window_end=date(2024, 6, 30),
    )
    facts = [
        Fact(
            id=f"rates-{month}",
            source="fred",
            title="Federal funds effective rate",
            url="https://fred.stlouisfed.org/series/FEDFUNDS",
            metric="FEDFUNDS",
            start=date(2024, month, 1),
            end=date(2024, month, 1),
            value=Decimal(value),
            unit="%",
            retrieved_at=datetime(2024, 8, 1, tzinfo=UTC),
        )
        for month, value in [(5, "5.08"), (6, "5.33")]
    ]
    complete = verify([claim], facts)[0][0]
    assert complete.verdict == "Supported"
    (change,) = complete.calculations
    assert (change.result, change.unit) == (Decimal("0.25"), "pp")
    assert change.evidence_ids == [fact.id for fact in facts]
    assert verify([claim], facts[1:])[0][0].verdict == "Unresolved"


def test_wrong_quarter_is_not_used_as_a_year_over_year_baseline():
    claim = Claim(
        id="c1",
        label="Q2 YoY profit growth",
        kind="financial",
        entities=["MSFT"],
        metric="net_income",
        period=Period(year=2024, quarter=2),
        comparison_period=Period(year=2023, quarter=1),
        measure="growth",
    )
    rows = [
        annual("net_income", 2023, 100).model_copy(update={"quarter": 1}),
        annual("net_income", 2024, 150).model_copy(update={"quarter": 2}),
    ]
    assert verify([claim], rows)[0][0].verdict == "Unresolved"


def test_conflict_parser_accepts_reported_percentages_with_units():
    claim = Claim(
        id="c1",
        label="Compare the two revenue growth figures",
        kind="conflict",
        entities=["MSFT"],
        metric="revenue",
        period=Period(year=2024),
        reported_values=["16%", "15.00 percent"],
    )
    assert [str(value) for value in claim.reported_values] == ["16", "15.00"]


def test_segment_percentages_cannot_verify_consolidated_conflict():
    claim = Claim(
        id="c1",
        label="Consolidated revenue conflict",
        kind="conflict",
        entities=["MSFT"],
        metric="revenue",
        period=Period(year=2024),
        reported_values=[16, 15],
    )
    evidence = [
        annual("revenue", 2023, 211915),
        annual("revenue", 2024, 245122),
        Document(
            id="segment-doc",
            source="sec",
            entity="MSFT",
            title="Earnings release",
            url="https://www.sec.gov/Archives/release",
            retrieved_at=datetime(2024, 8, 1, tzinfo=UTC),
            published=date(2024, 7, 30),
            period=Period(year=2024),
            passages=["Azure revenue increased 16% (up 15% in constant currency)."],
        ),
    ]
    findings, gaps = verify([claim], evidence)
    assert findings[0].verdict == "Unresolved"
    assert "segment-doc" not in findings[0].evidence_ids
    assert gaps[0].operation == "release"
