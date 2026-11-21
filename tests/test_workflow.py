import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from financial_research.fixtures import PROMPTS
from financial_research.presentation import workspace_view
from financial_research.service import ResearchService


def highlight_values(turn):
    return [item["calculation"]["display_value"] for item in workspace_view(turn)["report"]["highlights"]]


def test_reuse_restart_and_conflict_history(tmp_path):
    path = tmp_path / "research.sqlite3"
    service = ResearchService(path)
    session = service.store.new_session()
    revenue = service.start(session, PROMPTS["revenue"], "replay")
    asyncio.run(service.execute(revenue))
    followup = service.start(session, PROMPTS["operating"], "replay", revenue)
    asyncio.run(service.execute(followup))
    followup_result = service.view(followup)
    assert followup_result["report"]["overall"] == "Supported"
    assert followup_result["counts"]["reused"] == 1
    assert followup_result["counts"]["lookups"] == 0

    conflict = service.start(service.store.new_session(), PROMPTS["conflict"], "replay")
    asyncio.run(service.execute(conflict))
    before_restart = service.view(conflict)
    assert [
        (event["stage"], event["pass_no"])
        for event in before_restart["events"]
        if event["kind"] == "stage" and event["outcome"] == "complete"
    ] == [
        ("Parse", 0),
        ("Plan", 0),
        ("Retrieve", 0),
        ("Verify", 0),
        ("Plan", 1),
        ("Retrieve", 1),
        ("Verify", 1),
        ("Report", 0),
    ]
    assert before_restart["report"]["overall"] == "Contradicted"
    assert all(
        event["data"]["elapsed_ms"] >= 0
        for event in before_restart["events"]
        if event["kind"] == "stage" and event["outcome"] == "complete"
    )
    assert before_restart["report"]["conclusion"] == (
        "The two figures don’t conflict. Microsoft’s FY2024 revenue growth was 16% as reported "
        "and 15% in constant currency. Constant currency adjusts for exchange-rate changes."
    )
    first, second = [
        event["data"]["findings"][0]
        for event in before_restart["events"]
        if event["stage"] == "Verify" and event["kind"] == "stage" and event["outcome"] == "complete"
    ]
    assert first["verdict"] == "Unresolved"
    assert any(check["label"] == "Match 15%" and not check["evidence_ids"] for check in first["checks"])
    assert any(
        check["label"] == "Match 15%"
        and "in constant currency" in check["detail"]
        and "15% in constant currency" in check["excerpt"]
        and check["evidence_ids"]
        for check in second["checks"]
    )
    assert second["checks"][-1]["label"] == "Compare measurement bases"
    assert ResearchService(path).view(conflict) == before_restart


def test_saved_legacy_report_opens_with_its_original_conclusion(tmp_path):
    service = ResearchService(tmp_path / "legacy.sqlite3")
    turn = service.start(service.store.new_session(), PROMPTS["revenue"], "replay")
    asyncio.run(service.execute(turn))
    modern = service.view(turn)["report"]
    legacy = {
        key: modern[key]
        for key in (
            "overall",
            "findings",
            "evidence_ids",
            "timeline",
            "limitations",
            "carried",
            "reused_sources",
            "cutoff",
        )
    }
    legacy["conclusion"] = "Saved conclusion from before the report redesign."
    # Assign the snapshot directly to model a legacy database on disk.
    with service.store.connect() as db:
        db.execute(
            "UPDATE turns SET data = json_set(data, '$.report', json(?)) WHERE id = ?",
            (json.dumps(legacy), turn),
        )

    reopened = ResearchService(service.store.path, recover=True)
    original = reopened.view(turn)
    assert original["report"] == legacy
    report = workspace_view(original)["report"]
    assert report["conclusion"] == legacy["conclusion"]
    assert "highlights" not in report
    assert report["findings"][0]["calculations"][-1]["display_value"] == "8.79 pp"
    assert reopened.view(turn) == original


def test_pending_clarification_resumes_the_original_run_after_restart(tmp_path):
    path = tmp_path / "research.sqlite3"
    service = ResearchService(path)
    turn = service.start(service.store.new_session(), "Did Microsoft profit increase in FY2024?", "replay")
    asyncio.run(service.execute(turn))
    first = service.view(turn)
    assert [field["name"] for field in first["clarification"]["fields"]] == ["metric"]

    service = ResearchService(path)
    asyncio.run(service.execute(turn, {"metric": "operating_income"}))
    final = service.view(turn)
    assert final["run_id"] == first["run_id"]
    assert final["status"] == "Complete"
    assert any(
        event["kind"] == "clarification" and event["outcome"] == "resumed" for event in final["events"]
    )


def test_delete_session_removes_linked_turns_history_cache_and_checkpoints(tmp_path):
    import sqlite3
    from contextlib import closing

    service = ResearchService(tmp_path / "research.sqlite3")
    keep = service.store.new_session()
    kept_turn = service.start(keep, PROMPTS["revenue"], "replay")
    asyncio.run(service.execute(kept_turn))
    kept = service.view(kept_turn)

    remove = service.store.new_session()
    turn = service.start(remove, PROMPTS["revenue"], "replay")
    asyncio.run(service.execute(turn))
    child = service.start(remove, PROMPTS["operating"], "replay", parent=turn)
    asyncio.run(service.execute(child))
    pending = service.start(remove, PROMPTS["profit"], "replay")
    asyncio.run(service.execute(pending))
    assert service.view(pending)["status"] == "Waiting for clarification"
    assert service.store.lookups(remove, "replay")

    service.delete_session(remove)

    service = ResearchService(service.store.path, recover=True)
    assert [session["id"] for session in service.store.sessions()] == [keep]
    assert service.store.selection() == {"session_id": keep, "turn_id": kept_turn}
    assert service.view(kept_turn) == kept
    assert service.store.turns(remove) == []
    assert service.store.lookups(remove, "replay") == []
    assert service.store.events(turn) == []
    with closing(sqlite3.connect(service.checkpoints)) as conn:
        assert conn.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall() == [(kept["run_id"],)]
        assert conn.execute("SELECT DISTINCT thread_id FROM writes").fetchall() == [(kept["run_id"],)]


def test_delete_session_protects_active_runs_and_replaces_the_last_session(tmp_path):
    service = ResearchService(tmp_path / "research.sqlite3")
    session = service.store.new_session()
    turn = service.start(session, PROMPTS["revenue"], "replay")

    with pytest.raises(ValueError, match="active investigation"):
        service.delete_session(session)
    assert service.view(turn)["status"] == "Queued"

    service.store.recover()
    service.delete_session(session)
    selection = service.store.selection()
    assert selection["session_id"] != session
    assert selection["turn_id"] is None
    assert len(service.store.sessions()) == 1


def test_followup_keeps_cached_operands_and_fetches_a_missing_year(tmp_path):
    service = ResearchService(tmp_path / "new-operand.sqlite3")
    session = service.store.new_session()
    original = service.start(session, PROMPTS["revenue"], "replay")
    asyncio.run(service.execute(original))
    next_turn = service.start(session, PROMPTS["missing"], "replay", original)
    asyncio.run(service.execute(next_turn))
    result = service.view(next_turn)
    assert result["report"]["overall"] == "Supported"
    assert result["counts"]["reused"] == 1
    assert result["counts"]["lookups"] == 1
    assert any(event["kind"] == "cache" and event["data"]["partial"] for event in result["events"])


def _config(tmp_path):
    path = tmp_path / "config.local.toml"
    path.write_text(
        '[openrouter]\napi_key = "test-router-private"\n'
        '[fred]\napi_key = "test-fred-private"\n[tavily]\napi_key = "test-tavily-private"\n'
        '[sec]\nuser_agent = "Test suite <tester@example.com>"\n',
    )
    return path


def _model_response(content):
    return httpx.Response(
        200,
        json={
            "provider": "Mock route",
            "usage": {"total_tokens": 10},
            "choices": [{"message": {"content": json.dumps(content)}}],
        },
    )


def _draft_from_payload(payload):
    context = json.loads(payload["messages"][-1]["content"])
    return _model_response({"answers": context["answers"]})


@pytest.mark.parametrize("draft_change", ["valid", "different_value", "wrong_language"])
def test_live_chinese_question_is_parsed_and_answered_in_chinese(tmp_path, draft_change):
    from financial_research.fixtures import PROMPTS_ZH, lookup_fixture, parse_fixture

    question = "截至2024年8月31日，微软 FY2024 的营收增速是否加快？"
    parsed = parse_fixture(PROMPTS_ZH["revenue"]).model_dump(mode="json")
    parsed["as_of"] = "2024-08-31"
    schemas = []
    drafted = []

    def handler(request):
        payload = json.loads(request.content)
        schema = payload["response_format"]["json_schema"]["name"]
        context = json.loads(payload["messages"][-1]["content"])
        schemas.append(schema)
        assert context["response_language"] == "Chinese"
        if schema == "Parsed":
            assert context["question"] == question
            return _model_response(parsed)
        if schema == "Plan":
            assert context["claims"][0]["entities"] == ["MSFT"]
            assert context["claims"][0]["metric"] == "revenue"
            return _model_response({"actions": context["allowed_candidates"]})
        assert schema == "ConclusionDraft"
        assert context["question"] == question
        (answer,) = context["answers"]
        assert answer["verdict"] == "Supported"
        assert "营收增速" in answer["text"]
        expected = answer["text"]
        if draft_change == "different_value":
            answer["text"] = expected.replace("8.79", "9.79")
        elif draft_change == "wrong_language":
            answer["text"] = "Yes. Microsoft's FY2024 revenue growth accelerated by 8.79 percentage points."
        else:
            answer["text"] = "经核实，" + expected
        drafted.append(answer["text"])
        return _model_response({"answers": [answer]})

    service = ResearchService(
        tmp_path / "chinese.sqlite3",
        _config(tmp_path),
        http_transport=httpx.MockTransport(handler),
        replay=SimpleNamespace(lookup=lookup_fixture),
    )
    turn = service.start(
        service.store.new_session(), question, "live", cutoff=datetime(2025, 1, 1, 15, tzinfo=UTC)
    )
    asyncio.run(service.execute(turn))
    result = service.view(turn)
    assert schemas == ["Parsed", "Plan", "ConclusionDraft"]
    assert result["status"] == "Complete"
    assert result["response_language"] == "zh"
    assert result["parsed"]["as_of"] == "2024-08-31"
    assert result["report"]["cutoff"].startswith("2024-09-01T")
    report = result["report"]
    assert report["overall"] == "Supported"
    assert "8.79 个百分点" in report["conclusion"]
    assert report["answers"][0]["evidence_ids"] == report["findings"][0]["evidence_ids"]
    assert (report["conclusion"] == drafted[0]) == (draft_change == "valid")


@pytest.mark.parametrize(
    ("question", "language"),
    [
        ("MSFT FY2024 的 revenue 增速是否加快？", "zh"),
        ("What was 微软's revenue in FY2024?", "en"),
        ('Was Microsoft’s "营收" higher in FY2024?', "en"),
    ],
)
def test_question_language_uses_prose_around_mixed_financial_terms(question, language):
    from financial_research.language import question_language

    assert question_language(question) == language


def test_chinese_fiscal_year_end_is_not_an_evidence_cutoff():
    from financial_research.planning import asks_for_as_of

    assert not asks_for_as_of("截至2024年6月30日止的财年，微软的营收是多少？", None)
    assert asks_for_as_of("只使用截至2024年8月15日披露的信息。", None)


@pytest.mark.parametrize(
    ("case_id", "question", "expected_amounts"),
    [
        (
            "D07",
            "为微软撰写研究报告时，按 2024 年 8 月 15 日可获得的数据版本，2024 年 6 月的有效联邦基金利率是多少？",
            (),
        ),
        (
            "C05",
            "苹果 2024 财年第二季度营收为 907.53 亿美元，全年营收为 3,910.35 亿美元，这两个数字相互矛盾吗？",
            ("90.753 billion USD", "391.035 billion USD"),
        ),
    ],
)
def test_live_chinese_evaluation_cases_preserve_vintage_and_dollar_scale(
    tmp_path, case_id, question, expected_amounts
):
    from financial_research.evaluate import CaseReplay, read_catalog

    catalog, evidence = read_catalog()
    case = next(case for case in catalog["cases"] if case["id"] == case_id)

    def handler(request):
        payload = json.loads(request.content)
        schema = payload["response_format"]["json_schema"]["name"]
        context = json.loads(payload["messages"][-1]["content"])
        if schema == "Parsed":
            assert all(amount in context["question"] for amount in expected_amounts)
            return _model_response(case["parsed"])
        if schema == "Plan":
            return _model_response({"actions": context["allowed_candidates"]})
        assert schema == "ConclusionDraft"
        assert context["question"] == question
        return _draft_from_payload(payload)

    service = ResearchService(
        tmp_path / "assessment.sqlite3",
        _config(tmp_path),
        http_transport=httpx.MockTransport(handler),
        replay=CaseReplay(case, evidence),
    )
    turn = service.start(
        service.store.new_session(), question, "live", cutoff=datetime(2025, 1, 1, 15, tzinfo=UTC)
    )
    asyncio.run(service.execute(turn))
    result = service.view(turn)
    assert result["status"] == "Complete"
    assert result["parsed"].get("as_of") == case.get("as_of")
    assert result["report"]["overall"] == case["expectations"][0]["verdict"]
    assert set(case["expectations"][0]["citations"]) <= {record["url"] for record in result["evidence"]}


@pytest.mark.parametrize("missing_year", [False, True])
def test_live_chinese_profit_clarification_uses_a_localized_template(tmp_path, missing_year):
    fields = [
        {
            "name": "metric",
            "label": "Profit metric",
            "control": "select",
            "options": [
                {"value": "revenue", "label": "Revenue"},
                {"value": "net_income", "label": "Net income"},
                {"value": "diluted_eps", "label": "Diluted EPS"},
            ],
        }
    ]
    if missing_year:
        fields.append(
            {
                "name": "year",
                "label": "Fiscal year",
                "control": "select",
                "options": [{"value": "2024", "label": "FY2024"}],
            }
        )

    def handler(request):
        payload = json.loads(request.content)
        assert payload["response_format"]["json_schema"]["name"] == "Parsed"
        return _model_response(
            {
                "clarification": {
                    "message": "请 уточ明您指的是哪项利润指标？",
                    "fields": fields,
                }
            }
        )

    service = ResearchService(
        tmp_path / "clarification.sqlite3", _config(tmp_path), http_transport=httpx.MockTransport(handler)
    )
    question = "微软的利润增长了吗？" if missing_year else "微软 2024 财年的利润增长了吗？"
    turn = service.start(service.store.new_session(), question, "live")
    asyncio.run(service.execute(turn))
    result = service.view(turn)

    assert result["status"] == "Waiting for clarification"
    expected = "请选择利润指标和目标财年。" if missing_year else "请选择要比较的利润指标。"
    assert result["clarification"]["message"] == expected
    field, *others = result["clarification"]["fields"]
    assert field["label"] == "利润指标"
    assert field["options"] == [{"value": "net_income", "label": "净利润"}]
    if missing_year:
        (year,) = others
        assert year["label"] == "目标财年"
        assert year["options"] == [{"value": "2024", "label": "2024 财年"}]
    else:
        assert not others


def test_chinese_clarification_resumes_with_canonical_choice_values(tmp_path):
    from financial_research.fixtures import PROMPTS_ZH

    path = tmp_path / "chinese-clarification.sqlite3"
    service = ResearchService(path)
    turn = service.start(service.store.new_session(), PROMPTS_ZH["profit"], "replay")
    asyncio.run(service.execute(turn))
    initial = service.view(turn)
    assert initial["status"] == "Waiting for clarification"
    assert "利润" in initial["clarification"]["message"]
    fields = {item["name"]: item for item in initial["clarification"]["fields"]}
    assert {item["value"]: item["label"] for item in fields["metric"]["options"]}[
        "operating_income"
    ] == "营业利润"

    service = ResearchService(path, recover=True)
    asyncio.run(service.execute(turn, {"metric": "operating_income", "year": "2024"}))
    result = service.view(turn)
    assert result["status"] == "Complete"
    assert result["run_id"] == initial["run_id"]
    assert "营业利润" in result["report"]["conclusion"]


@pytest.mark.parametrize("provider", ["openrouter", "openai_compatible"])
@pytest.mark.parametrize("company", ["MSFT", "AAPL"])
def test_simple_growth_acceleration_uses_financial_only_parse(tmp_path, company, provider, monkeypatch):
    from financial_research.fixtures import lookup_fixture

    schemas = []
    config = _config(tmp_path)
    host = "openrouter.ai"
    if provider == "openai_compatible":
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        with config.open("a", encoding="utf-8") as stream:
            stream.write(
                '\n[model]\nprovider = "openai_compatible"\n[openai_compatible]\n'
                'base_url = "http://127.0.0.1:8080/v1"\nmodel = "native-model"\n'
                'token_limit_parameter = "max_tokens"\n'
            )
        host = "127.0.0.1"

    def handler(request):
        assert request.url.host == host
        payload = json.loads(request.content)
        if provider == "openai_compatible":
            assert "Authorization" not in request.headers
            assert "provider" not in payload
            assert payload["max_tokens"] == 6500
        shape = payload["response_format"]["json_schema"]
        schemas.append(shape["name"])
        if shape["name"] == "ConclusionDraft":
            return _draft_from_payload(payload)
        if shape["name"] == "FinancialSubject":
            assert set(shape["schema"]["properties"]) == {"company", "metric", "period"}
            assert shape["strict"] is True
            return _model_response(
                {"company": company, "metric": "revenue", "period": {"year": 2024, "quarter": None}}
            )
        assert shape["name"] == "Plan"
        context = json.loads(payload["messages"][-1]["content"])
        assert len(context["claims"]) == 1
        assert context["claims"][0]["kind"] == "financial"
        assert context["claims"][0]["measure"] == "acceleration"
        return _model_response({"actions": context["allowed_candidates"]})

    service = ResearchService(
        tmp_path / "acceleration.sqlite3",
        config,
        http_transport=httpx.MockTransport(handler),
        replay=SimpleNamespace(lookup=lookup_fixture),
    )
    cutoff = datetime(2025, 1, 1, 15, tzinfo=UTC)
    turn = service.start(service.store.new_session(), PROMPTS["revenue"], "live", cutoff=cutoff)
    asyncio.run(service.execute(turn))
    item = service.view(turn)

    if company == "MSFT":
        assert schemas == ["FinancialSubject", "Plan", "ConclusionDraft"]
        assert item["status"] == "Complete"
        assert item["counts"]["lookups"] == 1
        assert item["report"]["overall"] == "Supported"
        assert "revenue growth accelerated" in item["report"]["conclusion"]
        assert "8.79 pp" in highlight_values(item)
    else:
        assert schemas == ["FinancialSubject"]
        assert item["status"] == "Failed"
        assert "company, metric, or fiscal period" in item["error"]
        assert item["counts"]["lookups"] == 0


@pytest.mark.parametrize("draft_change", ["valid", "different_value", "swapped_bases", "changed_verdict"])
def test_live_source_conflict_hears_both_percentages_and_checks_the_release(tmp_path, draft_change):
    from financial_research.fixtures import lookup_fixture

    planned_sources = []
    drafted_answer = (
        "Microsoft’s FY2024 revenue growth figures are compatible. "
        "The 16% figure is as reported and the 15% figure is in constant currency, which adjusts for exchange-rate changes."
    )

    def handler(request):
        assert request.url.host == "openrouter.ai"
        payload = json.loads(request.content)
        schema = payload["response_format"]["json_schema"]
        context = json.loads(payload["messages"][-1]["content"])
        if schema["name"] == "ConclusionDraft":
            assert context["question"] == PROMPTS["conflict"]
            assert context["answers"][0]["verdict"] == "Contradicted"
            text = drafted_answer
            if draft_change == "different_value":
                text = text.replace("15%", "25%")
            if draft_change == "swapped_bases":
                text = text.replace(
                    "16% figure is as reported", "16% figure is in constant currency"
                ).replace("15% figure is in constant currency", "15% figure is as reported")
            return _model_response(
                {
                    "answers": [
                        {
                            "index": 0,
                            "verdict": "Supported" if draft_change == "changed_verdict" else "Contradicted",
                            "text": text,
                        }
                    ]
                }
            )
        if schema["name"] == "FinancialSubject":
            assert set(schema["schema"]["properties"]) == {"company", "metric", "period"}
            return _model_response(
                {"company": "MSFT", "metric": "revenue", "period": {"year": 2024, "quarter": None}}
            )
        assert schema["name"] == "Plan"
        (claim,) = context["claims"]
        assert claim["kind"] == "conflict"
        assert claim["reported_values"] == ["16", "15"]
        actions = context["allowed_candidates"]
        planned_sources.extend(action["operation"] for action in actions)
        return _model_response({"actions": actions})

    service = ResearchService(
        tmp_path / "conflict.sqlite3",
        _config(tmp_path),
        http_transport=httpx.MockTransport(handler),
        replay=SimpleNamespace(lookup=lookup_fixture),
    )
    turn = service.start(
        service.store.new_session(), PROMPTS["conflict"], "live", cutoff=datetime(2025, 1, 1, 15, tzinfo=UTC)
    )
    asyncio.run(service.execute(turn))
    item = service.view(turn)
    assert item["status"] == "Complete"
    assert planned_sources == ["financials", "release"]
    assert item["report"]["overall"] == "Contradicted"
    if draft_change == "valid":
        assert item["report"]["conclusion"] == drafted_answer
    else:
        assert "16% as reported and 15% in constant currency" in item["report"]["conclusion"]
    assert item["report"]["answers"][0]["evidence_ids"] == item["report"]["findings"][0]["evidence_ids"]
    assert any(
        event["kind"] == "conclusion"
        and event["outcome"] == ("generated" if draft_change == "valid" else "fallback")
        for event in item["events"]
    )
    assert ResearchService(service.store.path).view(turn) == item


def test_explicit_standalone_quarterly_eps_uses_financial_subject_parse(tmp_path):
    schemas = []
    expected = {"company": "AAPL", "metric": "diluted_eps", "period": {"year": 2024, "quarter": 4}}

    def handler(request):
        payload = json.loads(request.content)
        schema = payload["response_format"]["json_schema"]["name"]
        schemas.append(schema)
        context = json.loads(payload["messages"][-1]["content"])
        if schema == "ConclusionDraft":
            return _draft_from_payload(payload)
        if schema == "FinancialSubject":
            assert context["explicit_fields"] == expected
            return _model_response(expected)
        if schema == "Plan":
            (claim,) = context["claims"]
            assert claim["period"] == expected["period"]
            assert claim["metric"] == "diluted_eps"
            assert claim["scope"] == "consolidated"
            assert claim["basis"] == "reported"
            assert claim["measure"] == "value"
            for candidate in context["allowed_candidates"]:
                if candidate["operation"] == "release":
                    assert candidate["query"] == "Quarterly diluted earnings per share as reported"
            return _model_response({"actions": context["allowed_candidates"]})
        return _model_response({"claims": [], "clarification": None, "out_of_scope": None})

    async def lookup(action, cutoff):
        return []

    service = ResearchService(
        tmp_path / "eps.sqlite3",
        _config(tmp_path),
        http_transport=httpx.MockTransport(handler),
        replay=SimpleNamespace(lookup=lookup),
    )
    turn = service.start(
        service.store.new_session(),
        "What was Apple’s reported standalone FY2024 Q4 diluted EPS?",
        "live",
        cutoff=datetime(2025, 1, 1, 15, tzinfo=UTC),
    )
    asyncio.run(service.execute(turn))
    assert schemas == ["FinancialSubject", "Plan", "Plan", "ConclusionDraft"]
    assert service.view(turn)["status"] == "Complete"


def test_live_clarification_ignores_an_unrequested_as_of_date(tmp_path):
    from financial_research.fixtures import lookup_fixture, parse_fixture

    initial = parse_fixture("Did Microsoft profit increase in FY2024?").model_dump(mode="json")
    initial["clarification"]["fields"].append(
        {"name": "period", "label": "Which fiscal periods?", "control": "text", "options": []}
    )
    resolved = parse_fixture(
        "Did Microsoft profit increase in FY2024?", None, {"metric": "operating_income"}
    ).model_dump(mode="json")
    resolved["as_of"] = "2099-12-31"

    def handler(request):
        assert request.url.host == "openrouter.ai"
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][-1]["content"])
        if payload["response_format"]["json_schema"]["name"] == "ConclusionDraft":
            return _draft_from_payload(payload)
        if payload["response_format"]["json_schema"]["name"] == "Parsed":
            return _model_response(resolved if context["clarification_answers"] else initial)
        return _model_response({"actions": context["allowed_candidates"]})

    service = ResearchService(
        tmp_path / "resume.sqlite3",
        _config(tmp_path),
        http_transport=httpx.MockTransport(handler),
        replay=SimpleNamespace(lookup=lookup_fixture),
    )
    cutoff = datetime(2025, 1, 1, 15, tzinfo=UTC)
    turn = service.start(service.store.new_session(), PROMPTS["profit"], "live", cutoff=cutoff)
    asyncio.run(service.execute(turn))
    assert service.view(turn)["status"] == "Waiting for clarification"
    asyncio.run(service.execute(turn, {"metric": "operating_income", "period": "FY2024 vs FY2023"}))
    item = service.view(turn)
    assert item["status"] == "Complete"
    assert item["cutoff"] == cutoff.isoformat()
    assert item["parsed"].get("as_of") is None
    assert any(event["kind"] == "cutoff" and event["outcome"] == "ignored" for event in item["events"])
    assert item["report"]["overall"] == "Supported"


def test_live_parse_repairs_a_clarification_that_asks_for_filing_figures(tmp_path):
    from financial_research.fixtures import lookup_fixture, parse_fixture

    replies = [
        {
            "clarification": {
                "message": "Please enter the figures below.",
                "fields": [
                    {"name": "period", "label": "FY2024 consolidated revenue (USD)", "control": "text"},
                    {"name": "comparison", "label": "FY2023 consolidated revenue (USD)", "control": "text"},
                ],
            }
        },
        parse_fixture(PROMPTS["event"]).model_dump(mode="json"),
    ]

    def handler(request):
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][-1]["content"])
        schema = payload["response_format"]["json_schema"]["name"]
        if schema == "ConclusionDraft":
            return _draft_from_payload(payload)
        if schema == "Parsed":
            return _model_response(replies.pop(0))
        if schema == "Attributions":
            return _model_response({"entries": []})
        return _model_response({"actions": context["allowed_candidates"]})

    service = ResearchService(
        tmp_path / "parse-repair.sqlite3",
        _config(tmp_path),
        http_transport=httpx.MockTransport(handler),
        replay=SimpleNamespace(lookup=lookup_fixture),
    )
    turn = service.start(
        service.store.new_session(),
        "What did Microsoft announce in its FY2024 earnings release, and how much did revenue grow?",
        "live",
        cutoff=datetime(2025, 1, 1, 15, tzinfo=UTC),
    )
    asyncio.run(service.execute(turn))
    result = service.view(turn)
    assert result["status"] == "Complete"
    assert "15.67%" in highlight_values(result)
    assert any(e["kind"] == "validation" and e["outcome"] == "retry" for e in result["events"])


@pytest.mark.parametrize("repair", ["unneeded", "complete", "empty"])
def test_live_disclosure_synthesis_covers_verified_drivers_and_survives_restart(tmp_path, repair):
    from financial_research.fixtures import lookup_fixture, parse_fixture

    parsed = parse_fixture(PROMPTS["event"]).model_dump(mode="json")
    quote = "Revenue grew in FY2024, driven by Azure sales and Office 365 Commercial growth."
    summary = "Microsoft credited higher Azure and Office 365 Commercial sales for its revenue growth."
    missed_quote = "The increase in Gaming revenue was primarily due to Activision Blizzard sales."
    missed_summary = "Microsoft attributed the Gaming revenue increase mainly to Activision Blizzard."
    schemas = []

    async def lookup(action, cutoff):
        records = await lookup_fixture(action, cutoff)
        if action.operation == "release":
            (release,) = records
            passages = [*release.passages, quote]
            if repair != "unneeded":
                passages.append(missed_quote)
            return [release.model_copy(update={"passages": passages})]
        return records

    def handler(request):
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][-1]["content"])
        name = payload["response_format"]["json_schema"]["name"]
        schemas.append(name)
        if name == "ConclusionDraft":
            return _draft_from_payload(payload)
        if name == "Parsed":
            return _model_response(parsed)
        if name == "Plan":
            return _model_response({"actions": context["allowed_candidates"]})
        if name == "DisclosureSynthesis":
            assert [entry["quote"] for entry in context["excerpts"]] == [missed_quote]
            summaries = (
                [{"claim_id": "c2", "text": missed_summary, "quote_indices": [0]}]
                if repair == "complete"
                else []
            )
            return _model_response({"summaries": summaries})
        assert name == "Attributions"
        assert context["summarize_disclosures"] is True
        return _model_response(
            {
                "entries": [
                    {
                        "claim_id": "c2",
                        "evidence_id": "replay-msft-release",
                        "quote": "Microsoft Corp. today announced the following results for the fiscal year ended June 30, 2024",
                        "role": "event",
                    },
                    {
                        "claim_id": "c2",
                        "evidence_id": "replay-msft-release",
                        "quote": quote,
                        "role": "explanation",
                    },
                    *(
                        [
                            {
                                "claim_id": "c2",
                                "evidence_id": "replay-msft-release",
                                "quote": missed_quote,
                                "role": "explanation",
                            }
                        ]
                        if repair != "unneeded"
                        else []
                    ),
                ],
                "summaries": [{"claim_id": "c2", "text": summary, "quote_indices": [1]}],
            }
        )

    path = tmp_path / "synthesized-report.sqlite3"
    service = ResearchService(
        path,
        _config(tmp_path),
        http_transport=httpx.MockTransport(handler),
        replay=SimpleNamespace(lookup=lookup),
    )
    turn = service.start(
        service.store.new_session(),
        "What did Microsoft announce for FY2024, and what did it say drove revenue growth?",
        "live",
        cutoff=datetime(2025, 1, 1, 15, tzinfo=UTC),
    )
    asyncio.run(service.execute(turn))
    result = service.view(turn)
    assert result["status"] == "Complete"
    expected_schemas = ["Parsed", "Plan", "Attributions"]
    expected_summaries = [summary]
    expected_quotes = [quote]
    if repair != "unneeded":
        expected_schemas.append("DisclosureSynthesis")
        expected_quotes.append(missed_quote)
    if repair == "complete":
        expected_summaries.append(missed_summary)
    expected_schemas.append("ConclusionDraft")
    assert schemas == expected_schemas
    assert [point["text"] for point in result["report"]["disclosure_summary"]] == expected_summaries
    assert all(
        point["evidence_ids"] == ["replay-msft-release"] for point in result["report"]["disclosure_summary"]
    )
    assert [item["quote"] for item in result["report"]["explanations"]] == expected_quotes
    assert bool(result["report"].get("explanation_note")) == (repair == "empty")
    assert not result["report"]["show_disclosure_quotes"]
    assert ResearchService(path, recover=True).view(turn) == result


def test_live_clarification_keeps_answers_from_earlier_rounds_after_restart(tmp_path):
    from financial_research.fixtures import lookup_fixture, parse_fixture

    initial = parse_fixture(PROMPTS["profit"]).model_dump(mode="json")
    metric, year = initial["clarification"]["fields"]
    replies = [
        {**initial, "clarification": {**initial["clarification"], "fields": [field]}}
        for field in (metric, year)
    ]
    metric_answer = {"metric": "operating_income"}
    year_answer = {"year": "2024"}
    resolved = parse_fixture(PROMPTS["profit"], answers=metric_answer | year_answer).model_dump(mode="json")
    received = []

    def handler(request):
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][-1]["content"])
        if payload["response_format"]["json_schema"]["name"] == "ConclusionDraft":
            return _draft_from_payload(payload)
        if payload["response_format"]["json_schema"]["name"] == "Parsed":
            answers = context["clarification_answers"]
            received.append(answers)
            if answers == metric_answer | year_answer:
                return _model_response(resolved)
            return _model_response(replies[0 if not answers or answers == year_answer else 1])
        return _model_response({"actions": context["allowed_candidates"]})

    options = {
        "http_transport": httpx.MockTransport(handler),
        "replay": SimpleNamespace(lookup=lookup_fixture),
    }
    path = tmp_path / "multi-round.sqlite3"
    config = _config(tmp_path)
    service = ResearchService(path, config, **options)
    cutoff = datetime(2025, 1, 1, 15, tzinfo=UTC)
    turn = service.start(service.store.new_session(), PROMPTS["profit"], "live", cutoff=cutoff)
    asyncio.run(service.execute(turn))
    initial_run_id = service.view(turn)["run_id"]
    asyncio.run(service.execute(turn, metric_answer))
    assert service.view(turn)["status"] == "Waiting for clarification"

    service = ResearchService(path, config, recover=True, **options)
    asyncio.run(service.execute(turn, year_answer))
    result = service.view(turn)
    assert received == [None, metric_answer, metric_answer | year_answer]
    assert result["status"] == "Complete"
    assert result["run_id"] == initial_run_id
    assert "$20.91B" in highlight_values(result)
    assert [
        event["data"]["answers"]
        for event in result["events"]
        if event["kind"] == "clarification" and event["outcome"] == "resumed"
    ] == [metric_answer, year_answer]


def test_unresolved_causation_does_not_turn_a_risk_disclosure_into_a_driver(tmp_path):
    from financial_research.fixtures import lookup_fixture, parse_fixture

    quote = "Monetary policy and inflation may adversely affect our results of operations."

    async def lookup(action, cutoff):
        records = await lookup_fixture(action, cutoff)
        if action.operation == "release":
            (release,) = records
            return [release.model_copy(update={"passages": [quote]})]
        return records

    def handler(request):
        payload = json.loads(request.content)
        schema = payload["response_format"]["json_schema"]["name"]
        context = json.loads(payload["messages"][-1]["content"])
        if schema == "ConclusionDraft":
            return _draft_from_payload(payload)
        if schema == "Parsed":
            return _model_response(parse_fixture(PROMPTS["rates"]).model_dump(mode="json"))
        if schema == "Plan":
            return _model_response({"actions": context["allowed_candidates"]})
        assert schema == "Attributions"
        return _model_response(
            {
                "entries": [
                    {
                        "claim_id": "c2",
                        "evidence_id": "replay-msft-release",
                        "quote": quote,
                        "role": "explanation",
                    }
                ]
            }
        )

    service = ResearchService(
        tmp_path / "rates.sqlite3",
        _config(tmp_path),
        http_transport=httpx.MockTransport(handler),
        replay=SimpleNamespace(lookup=lookup),
    )
    turn = service.start(
        service.store.new_session(), PROMPTS["rates"], "live", cutoff=datetime(2025, 1, 1, 15, tzinfo=UTC)
    )
    asyncio.run(service.execute(turn))
    result = service.view(turn)
    assert result["status"] == "Complete"
    report = result["report"]
    assert report["overall"] == "Partially supported"
    assert [finding["verdict"] for finding in report["findings"]] == ["Supported", "Unresolved"]
    assert any(item["passage"] == quote for item in report["timeline"])
    assert "8.79 pp" in highlight_values(result)
    assert quote not in report["conclusion"]
    assert report["explanations"] == []


def test_live_parser_rejects_a_requested_future_as_of_date(tmp_path):
    from financial_research.fixtures import parse_fixture

    parsed = parse_fixture(PROMPTS["revenue"]).model_dump(mode="json")
    parsed["as_of"] = "2099-12-31"

    def handler(request):
        assert request.url.host == "openrouter.ai"
        return _model_response(parsed)

    service = ResearchService(
        tmp_path / "future.sqlite3", _config(tmp_path), http_transport=httpx.MockTransport(handler)
    )
    turn = service.start(
        service.store.new_session(),
        "As of December 31, 2099, did Microsoft revenue growth accelerate in FY2024?",
        "live",
    )
    asyncio.run(service.execute(turn))
    item = service.view(turn)
    assert item["status"] == "Failed"
    assert "Choose an as-of date" in item["error"]
    assert item["counts"]["lookups"] == 0


def test_invalid_live_plan_does_not_execute_any_provider_action(tmp_path):
    def handler(request):
        assert request.url.host == "openrouter.ai"
        payload = json.loads(request.content)
        assert payload["provider"]["require_parameters"]
        data = json.loads(payload["messages"][-1]["content"])
        if payload["response_format"]["json_schema"]["name"] == "FinancialSubject":
            return _model_response(data["explicit_fields"])
        unauthorized = {**data["allowed_candidates"][0], "entity": "TSLA"}
        return _model_response({"actions": [unauthorized]})

    service = ResearchService(
        tmp_path / "plan.sqlite3", _config(tmp_path), http_transport=httpx.MockTransport(handler)
    )
    turn = service.start(service.store.new_session(), PROMPTS["revenue"], "live")
    asyncio.run(service.execute(turn))
    item = service.view(turn)
    assert item["status"] == "Failed"
    assert "failed validation" in item["error"]
    assert item["counts"]["lookups"] == 0
    assert item["counts"]["models"] == 2


def test_two_quarter_margins_share_an_approved_financial_lookup():
    from datetime import date

    from financial_research.domain import Claim, Period, Plan
    from financial_research.planning import candidates, validate_plan

    periods = [Period(year=year, quarter=2) for year in (2025, 2024)]
    claims = [
        Claim(
            id=f"margin-{period.year}",
            label=f"Microsoft operating margin in {period.label}",
            kind="financial",
            entities=["MSFT"],
            period=period,
            metric="operating_income",
            measure="margin",
        )
        for period in periods
    ]
    choices = candidates(claims, date(2025, 2, 1))
    combined = choices[0].model_copy(update={"periods": periods, "claim_ids": [claim.id for claim in claims]})
    assert validate_plan(Plan(actions=[combined]), choices, claims, followup=False).actions == [combined]
    unauthorized = combined.model_copy(update={"periods": [*periods, Period(year=2025, quarter=3)]})
    with pytest.raises(ValueError, match="outside the approved"):
        validate_plan(Plan(actions=[unauthorized]), choices, claims, followup=False)


def test_retry_and_provider_failure_preserve_confirmed_finding(tmp_path):
    from financial_research.fixtures import parse_fixture

    def handler(request):
        if request.url.host == "openrouter.ai":
            payload = json.loads(request.content)
            if payload["response_format"]["json_schema"]["name"] == "ConclusionDraft":
                return _draft_from_payload(payload)
            if payload["response_format"]["json_schema"]["name"] == "Parsed":
                return _model_response(parse_fixture(PROMPTS["rates"]).model_dump(mode="json"))
            data = json.loads(payload["messages"][-1]["content"])
            return _model_response(
                {
                    "actions": [
                        action
                        for action in data["allowed_candidates"]
                        if action["operation"] in {"financials", "observations"}
                    ]
                }
            )
        if request.url.host == "api.stlouisfed.org":
            return httpx.Response(503, json={"error": request.url.params["api_key"]})
        if "submissions" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "filings": {
                        "recent": {
                            "accessionNumber": ["22", "23", "24"],
                            "reportDate": ["2022-06-30", "2023-06-30", "2024-06-30"],
                            "filingDate": ["2022-07-30", "2023-07-30", "2024-07-30"],
                        }
                    }
                },
            )
        assert "companyfacts" in request.url.path
        rows = [
            {
                "start": f"{year - 1}-07-01",
                "end": f"{year}-06-30",
                "val": amount,
                "accn": str(year)[2:],
                "filed": f"{year}-07-30",
                "fp": "FY",
                "fy": year,
                "form": "10-K",
            }
            for year, amount in [(2022, 198270), (2023, 211915), (2024, 245122)]
        ]
        return httpx.Response(200, json={"facts": {"us-gaap": {"Revenues": {"units": {"USD": rows}}}}})

    service = ResearchService(
        tmp_path / "partial.sqlite3", _config(tmp_path), http_transport=httpx.MockTransport(handler)
    )
    turn = service.start(
        service.store.new_session(), PROMPTS["rates"], "live", cutoff=datetime(2024, 8, 15, tzinfo=UTC)
    )
    asyncio.run(service.execute(turn))
    item = service.view(turn)
    assert item["report"]["overall"] == "Partially supported"
    assert item["counts"]["retries"] == 1
    assert item["counts"]["lookups"] == 4
    assert "HTTP 503" in str(item["events"])
    assert "test-fred-private" not in str(item)
    assert b"test-fred-private" not in service.store.path.read_bytes()


def test_timeout_stops_external_calls_within_the_active_budget(tmp_path):
    async def handler(request):
        assert request.url.host == "openrouter.ai"
        await asyncio.sleep(1)
        return _model_response({})

    service = ResearchService(
        tmp_path / "deadline.sqlite3", _config(tmp_path), http_transport=httpx.MockTransport(handler)
    )
    turn = service.start(service.store.new_session(), PROMPTS["revenue"], "live")
    asyncio.run(service.execute(turn, seconds=0.35))
    item = service.view(turn)
    assert item["status"] == "Failed"
    assert item["active_seconds"] < 0.5
    assert item["counts"]["lookups"] == 0
    assert any(event["outcome"] == "error" for event in item["events"])
