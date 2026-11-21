from decimal import Decimal

from .domain import COMPANIES, METRICS
from .fixtures import PROMPTS, PROMPTS_ZH
from .verification import render_number

SOURCES = {"sec": "SEC EDGAR", "fred": "FRED", "news": "News search"}


def workspace_view(turn):
    """Prepare a display projection without changing the saved report."""
    if not turn:
        return None
    result = dict(turn)
    result["company_names"] = {ticker: name for ticker, (name, _) in COMPANIES.items()}
    evidence = result.get("evidence", [])
    result["events"] = [
        {
            **event,
            "data": {
                **event["data"],
                "findings": [display_finding(item) for item in event["data"]["findings"]],
            },
        }
        if event["stage"] == "Verify" and event["kind"] == "stage" and "findings" in event["data"]
        else event
        for event in result.get("events", [])
    ]
    report = result.get("report")
    if report:
        report = {**report, "findings": [display_finding(item) for item in report["findings"]]}
        if "answers" in report:
            report["highlights"] = [
                {**item, "calculation": display_calculation(item["calculation"])}
                for item in report["highlights"]
            ]
            report["comparison_calculations"] = [
                display_calculation(item) for item in report["comparison_calculations"]
            ]
        result["report"] = report
        claims = result["parsed"]["claims"]
        prompts = PROMPTS_ZH if result.get("response_language") == "zh" else PROMPTS
        if (
            len(claims) == 1
            and claims[0]["entities"] == ["MSFT"]
            and claims[0]["measure"] == "acceleration"
            and claims[0]["metric"] == "revenue"
        ):
            report["suggestion"] = prompts["operating"]
        elif claims and result["mode"] == "replay" and claims[0]["metric"] == "operating_income":
            report["suggestion"] = prompts["missing"]
        refs = set(report["evidence_ids"]) | {item["evidence_id"] for item in report["timeline"]}
        report["sources"] = list(
            dict.fromkeys(SOURCES[item["source"]] for item in evidence if item["id"] in refs)
        )
    result["evidence"] = [
        {
            **item,
            "metric_label": METRICS.get(item.get("metric"), item.get("metric")),
            "display_value": render_number(Decimal(item["value"]), item["unit"])
            if item["type"] == "fact"
            else None,
        }
        for item in evidence
    ]
    return result


def display_finding(finding):
    return {
        **finding,
        "calculations": [display_calculation(item) for item in finding["calculations"]],
    }


def display_calculation(item):
    return {
        **item,
        "display_value": render_number(Decimal(item["result"]), item["unit"]),
        "operands": [
            {**operand, "display_value": render_number(Decimal(operand["value"]), operand["unit"])}
            for operand in item["operands"]
        ],
    }
