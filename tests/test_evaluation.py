import asyncio
from collections import Counter
from types import SimpleNamespace

from financial_research import evaluate
from financial_research.evaluate import read_catalog, run_smoke, run_suite


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
