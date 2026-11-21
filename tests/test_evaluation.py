import asyncio
from collections import Counter
from types import SimpleNamespace

from financial_research import evaluate
from financial_research.evaluate import read_catalog, run_smoke, run_suite
from financial_research.planning import explicit_financial_claim


def test_frozen_cases_match_required_assessment_mix_and_run_through_the_graph():
    catalog, evidence = read_catalog()
    held_out = [case for case in catalog["cases"] if case["split"] == "held_out"]
    assert len(held_out) == 20
    assert Counter(case["category"] for case in held_out) == {
        "filing": 7,
        "event": 5,
        "conflict": 5,
        "uncertainty": 3,
    }
    assert sum(case["split"] == "development" for case in catalog["cases"]) == 10
    result = asyncio.run(run_suite("replay", "all", catalog, evidence))
    assert all(row["status"] in {"Complete", "Waiting for clarification"} for row in result["runs"])
    assert result["workflow"]["verdicts"]["percent"] == 100
    assert result["workflow"]["financial_values"]["percent"] == 100
    assert result["workflow"]["conflict_cases"]["percent"] == 100
    assert result["workflow"]["uncertainty_cases"]["percent"] == 100


def test_missing_announcement_does_not_steal_financial_finding():
    catalog, evidence = read_catalog()
    case = next(case for case in catalog["cases"] if case["id"] == "E03")
    facts = [evidence[key].model_dump(mode="json") for key in case["evidence"] if key.endswith("income")]
    expected = case["expectations"][1]["values"][0]
    finding = {
        "claim_id": "financial-change",
        "verdict": "Supported",
        "evidence_ids": [fact["id"] for fact in facts],
        "calculations": [
            {
                "result": expected["value"],
                "unit": expected["unit"],
                "operands": [{"value": fact["value"]} for fact in facts],
                "evidence_ids": [fact["id"] for fact in facts],
            }
        ],
    }
    score = evaluate._score(case, [finding], [], facts)
    assert score["verdict_correct"] == 1
    assert score["financial_correct"] == 1
    assert score["citation_correct"] == 2


def test_issuer_8k_can_cite_the_announcement_but_not_the_growth_figure():
    catalog, _ = read_catalog()
    case = next(case for case in catalog["cases"] if case["id"] == "E01")
    filing = {
        "id": "primary",
        "url": "https://www.sec.gov/Archives/edgar/data/789019/000095017024087835/msft-20240730.htm",
    }
    findings = [
        {"kind": "financial", "verdict": "Supported", "evidence_ids": ["primary"]},
        {"kind": "event", "verdict": "Supported", "evidence_ids": ["primary"]},
    ]
    score = evaluate._score(case, findings, [], [filing], baseline=True)
    assert score["citation_total"] == 2
    assert score["citation_correct"] == 1


def test_conflict_question_can_put_both_percentages_before_the_metric():
    question = "Do NVIDIA’s 13% and 12% FY2025 Q2 revenue growth figures conflict?"
    claim = explicit_financial_claim(question)
    assert claim.kind == "conflict"
    assert claim.entities == ["NVDA"]
    assert claim.period.label == "FY2025 Q2"
    assert claim.reported_values == [13, 12]
    assert explicit_financial_claim(question.replace("conflict", "indicate acceleration")) is None


def test_causal_finding_may_name_the_financial_metric_when_expected_metric_is_unspecified():
    catalog, _ = read_catalog()
    case = next(case for case in catalog["cases"] if case["id"] == "E05")
    financial, causal = case["parsed"]["claims"]
    claims = [financial, {**causal, "metric": "revenue"}]
    findings = [{"claim_id": "c2", "verdict": "Unresolved"}, {"claim_id": "c1", "verdict": "Supported"}]
    score = evaluate._score(case, findings, [], [], claims=claims)
    assert score["verdict_correct"] == score["verdict_total"] == 2
    other_company = [financial, {**claims[1], "entities": ["AMZN"]}]
    assert evaluate._score(case, findings, [], [], claims=other_company)["verdict_correct"] == 1


def test_live_smoke_resumes_eps_with_the_consolidated_option(monkeypatch):
    request = {
        "message": "Which scope do you mean?",
        "fields": [
            {
                "name": "basis",
                "label": "Scope",
                "control": "select",
                "options": [
                    {"value": "parent_only", "label": "Standalone (parent-only)"},
                    {"value": "issuer_total", "label": "Consolidated (reported)"},
                ],
            }
        ],
    }
    submissions = []

    async def execute(turn, answers=None):
        submissions.append((turn, answers))

    def view(turn):
        assert turn == "original-turn"
        item = {"counts": {}, "events": [], "active_seconds": 1}
        if len(submissions) == 1:
            return {**item, "status": "Waiting for clarification", "clarification": request}
        return {**item, "status": "Complete", "report": {"overall": "Supported"}}

    service = SimpleNamespace(
        store=SimpleNamespace(new_session=lambda: "session"),
        start=lambda session, question, mode: "original-turn",
        execute=execute,
        view=view,
    )
    monkeypatch.setattr(evaluate, "ResearchService", lambda *args: service)
    result = asyncio.run(run_smoke(case_ids=("quarterly_eps",)))

    assert submissions == [("original-turn", None), ("original-turn", {"basis": "issuer_total"})]
    row = result["runs"][0]
    assert row["status"] == "Complete"
    assert row["clarification"]["answers"] == {"basis": "issuer_total"}
