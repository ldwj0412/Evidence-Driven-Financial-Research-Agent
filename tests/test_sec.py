from datetime import UTC, date, datetime
from decimal import Decimal

from financial_research.domain import Document, Period
from financial_research.sec import (
    document_record,
    normalize_company_facts,
    period_passages,
    reported_quarter_eps,
)


def test_fiscal_year_uses_native_end_and_comparatives_and_tracks_restatement():
    dates = [
        ("2023-01-30", "2024-01-28", 2024, "2024-02-21", "24", 100),
        ("2022-01-31", "2023-01-29", 2023, "2023-02-22", "23", 60),
        ("2022-01-31", "2023-01-29", 2024, "2024-02-21", "24", 61),
        ("2022-01-31", "2023-01-29", 2025, "2025-02-22", "25", 99),
    ]
    rows = [
        {
            "start": start,
            "end": end,
            "fy": year,
            "fp": "FY",
            "filed": filed,
            "accn": acc,
            "form": "10-K",
            "val": value,
        }
        for start, end, year, filed, acc, value in dates
    ]
    filings = {
        "24": {"reportDate": "2024-01-28", "primaryDocument": "nvidia24.htm"},
        "23": {"reportDate": "2023-01-29", "primaryDocument": "nvidia23.htm"},
        "25": {"reportDate": "2025-01-26", "primaryDocument": "nvidia25.htm"},
    }
    companyfacts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": rows}}}}}
    facts = normalize_company_facts(
        "NVDA",
        companyfacts,
        filings,
        [Period(year=2023), Period(year=2024)],
        datetime(2024, 3, 1, tzinfo=UTC),
    )
    assert [(fact.fiscal_year, fact.value, fact.accession) for fact in facts] == [
        (2023, 61, "24"),
        (2024, 100, "24"),
    ]
    assert facts[0].restatement


def test_a_quarter_is_never_a_year_to_date_eps():
    tags = {
        "Revenues": {
            "units": {
                "USD": [
                    {
                        "start": "2023-10-01",
                        "end": "2023-12-30",
                        "fy": 2024,
                        "fp": "Q1",
                        "filed": "2024-02-02",
                        "accn": "q1",
                        "form": "10-Q",
                        "val": 20,
                    },
                    {
                        "start": "2023-10-01",
                        "end": "2024-03-30",
                        "fy": 2024,
                        "fp": "Q2",
                        "filed": "2024-05-02",
                        "accn": "q2",
                        "form": "10-Q",
                        "val": 50,
                    },
                ]
            }
        },
        "EarningsPerShareDiluted": {
            "units": {
                "USD/shares": [
                    {
                        "start": "2023-10-01",
                        "end": "2024-03-30",
                        "fy": 2024,
                        "fp": "Q2",
                        "filed": "2024-05-02",
                        "accn": "q2",
                        "form": "10-Q",
                        "val": "4.00",
                    },
                ]
            }
        },
    }
    filings = {"q1": {"reportDate": "2023-12-30"}, "q2": {"reportDate": "2024-03-30"}}
    result = normalize_company_facts(
        "AAPL",
        {"facts": {"us-gaap": tags}},
        filings,
        [Period(year=2024, quarter=2)],
        datetime(2024, 6, 1, tzinfo=UTC),
    )
    quarterly = [item for item in result if item.fiscal_year == 2024 and item.quarter == 2]
    assert [(item.metric, item.value) for item in quarterly] == [("revenue", 30)]
    assert all(any(record.id == key for record in result) for key in quarterly[0].derived_from)


def test_derives_gross_profit_from_consolidated_cost_of_revenue():
    row = {
        "start": "2024-01-01",
        "end": "2024-12-31",
        "fy": 2024,
        "fp": "FY",
        "filed": "2025-02-05",
        "accn": "goog24",
        "form": "10-K",
    }
    raw = {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [{**row, "val": 350018}]}},
                "CostOfRevenue": {"units": {"USD": [{**row, "val": 146306}]}},
            }
        }
    }
    records = normalize_company_facts(
        "GOOGL",
        raw,
        {"goog24": {"reportDate": "2024-12-31"}},
        [Period(year=2024)],
        datetime(2025, 3, 1, tzinfo=UTC),
    )
    gross = next(item for item in records if item.metric == "gross_profit")
    assert gross.value == 203712
    assert {item.metric for item in records if item.id in gross.derived_from} == {
        "revenue",
        "cost_of_revenue",
    }


def test_standalone_eps_uses_directly_disclosed_gaap_figure():
    document = Document(
        id="apple-exhibit",
        source="sec",
        title="Apple FY2024 results",
        url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000120/a8-kex991q4202409282024.htm",
        entity="AAPL",
        form="8-K exhibit",
        accession="0000320193-24-000120",
        published=date(2024, 10, 31),
        retrieved_at=datetime(2024, 11, 1, tzinfo=UTC),
        period=Period(year=2024, quarter=4),
        passages=[
            "The company reported quarterly diluted earnings per share of $0.97.",
            "Adjusted quarterly diluted earnings per share was $1.64 excluding the tax charge.",
        ],
    )
    result = reported_quarter_eps(document, date(2024, 6, 30), date(2024, 9, 28))
    assert result.value == Decimal("0.97")
    assert result.basis == "reported"
    assert result.derived_from == [document.id]


def test_exhibit_keeps_quarterly_eps_in_a_long_dated_opening_paragraph():
    period = Period(year=2024, quarter=4)
    opening = (
        "Apple announced financial results for its fiscal 2024 fourth quarter ended September 28, 2024. "
        "Quarterly diluted earnings per share was $0.97. "
        "Adjusted diluted earnings per share was $1.64 excluding the one-time tax charge. "
        "This paragraph includes additional background on that adjustment and why the company believes "
        "that its presentation may help investors compare results across different reporting periods."
    )
    assert len(opening) > 330
    cutoff = datetime(2024, 11, 2, tzinfo=UTC)
    quarter_end = date(2024, 9, 28)
    args = (
        "AAPL",
        "0000320193-24-000120",
        {"filingDate": "2024-10-31", "form": "8-K"},
        "a8-kex991q4202409282024.htm",
        [opening],
        cutoff,
        period,
        True,
    )
    exhibit = document_record(*args, quarter_end)
    assert exhibit.period == period
    eps = reported_quarter_eps(exhibit, date(2024, 6, 30), quarter_end)
    assert eps.value == Decimal("0.97")
    assert eps.derived_from == [exhibit.id]
    assert document_record(*args, date(2023, 9, 30)).period is None


def test_explicit_full_year_heading_identifies_the_annual_earnings_section():
    passages = [
        "Fourth Quarter 2024",
        "Operating income rose in the fourth quarter.",
        "Full Year 2023",
        "Operating income was $36.9 billion.",
        "Full Year 2024",
        "Operating income was $68.6 billion.",
        "First Quarter 2025 Guidance",
        "We expect operating income to rise.",
    ]
    assert period_passages(passages, Period(year=2024), date(2024, 12, 31)) == passages[4:6]
