import asyncio
from datetime import UTC, date, datetime

import httpx
import pytest

from financial_research.config import load_config
from financial_research.domain import Parsed
from financial_research.providers import Budget, OpenRouter, ProviderError, Transport, strict_schema


def test_model_requests_can_use_up_to_two_minutes(tmp_path):
    config = tmp_path / "config.local.toml"
    config.write_text('[openrouter]\napi_key = "test-router-timeout"')
    settings = load_config(config)
    assert Budget().seconds == 120

    def handler(request):
        assert request.extensions["timeout"]["read"] == 120
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"out_of_scope":"Outside scope"}'}}]}
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = Transport(client, Budget(180), lambda *_: None, settings)
            return await OpenRouter(transport, settings.openrouter).ask(Parsed, "Parse", "Test request")

    assert asyncio.run(run()).out_of_scope == "Outside scope"


def test_credentials_can_come_from_environment_without_a_local_config(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-router-from-env")
    monkeypatch.setenv("FRED_API_KEY", "test-fred-from-env")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-from-env")
    monkeypatch.setenv("SEC_USER_AGENT", "Research Test <test@example.com>")

    settings = load_config(tmp_path / "config.local.toml")

    assert settings.messages == ()
    assert settings.openrouter.api_key.get_secret_value() == "test-router-from-env"
    assert settings.fred.api_key.get_secret_value() == "test-fred-from-env"
    assert settings.tavily.api_key.get_secret_value() == "test-tavily-from-env"
    assert settings.sec.user_agent == "Research Test <test@example.com>"
    assert settings.redact("Unexpected test-router-from-env value") == "Unexpected [redacted] value"


def test_nonblank_local_settings_override_environment_and_blank_settings_fall_back(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-router-from-env")
    monkeypatch.setenv("FRED_API_KEY", "test-fred-from-env")
    config = tmp_path / "config.local.toml"
    config.write_text('[openrouter]\napi_key = "test-router-from-file"\n[fred]\napi_key = "  "')

    settings = load_config(config)

    assert settings.openrouter.api_key.get_secret_value() == "test-router-from-file"
    assert settings.fred.api_key.get_secret_value() == "test-fred-from-env"


def test_config_can_reference_a_named_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("NLP_RESEARCH_KEY", "test-resolved-key")
    config = tmp_path / "config.local.toml"
    config.write_text('[openrouter]\napi_key = "${NLP_RESEARCH_KEY}"')

    settings = load_config(config)

    assert settings.openrouter.api_key.get_secret_value() == "test-resolved-key"
    assert settings.redact("test-resolved-key") == "[redacted]"


def test_missing_explicit_environment_reference_names_the_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("NLP_RESEARCH_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-default-key")
    config = tmp_path / "config.local.toml"
    config.write_text('[openrouter]\napi_key = "${NLP_RESEARCH_KEY}"')

    settings = load_config(config)

    assert not settings.openrouter.api_key.get_secret_value()
    assert any("NLP_RESEARCH_KEY" in message for message in settings.messages)


def test_qwen_schema_preserves_types_and_keeps_bounds_in_local_validation():
    shaped = strict_schema(Parsed.model_json_schema(), portable=True)
    claims = shaped["properties"]["claims"]
    assert claims["type"] == "array"
    assert "maxItems" not in claims
    assert shaped["$defs"]["Claim"]["additionalProperties"] is False
    assert "metric" in shaped["$defs"]["Claim"]["required"]
    with pytest.raises(ValueError, match="too_long"):
        Parsed.model_validate({"claims": [{}] * 9})


def test_configuration_preserves_valid_providers_when_another_section_is_invalid(tmp_path):
    config = tmp_path / "config.local.toml"
    config.write_text(
        '[openrouter]\napi_key="router-private"\nbase_url="invalid"\n[fred]\napi_key="fred-private"'
    )
    settings = load_config(config)
    assert "OpenRouter" in settings.messages[0]
    assert settings.fred.api_key.get_secret_value() == "fred-private"
    assert "fred-private" not in str(settings)


def test_openrouter_requires_structured_routing_and_redacts_errors(tmp_path):
    config = tmp_path / "config.local.toml"
    config.write_text('[openrouter]\napi_key="router-private"')
    settings = load_config(config)
    events = []

    def handler(request):
        import json

        payload = json.loads(request.content)
        assert payload["provider"]["require_parameters"] is True
        assert payload["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(404, json={"error": {"message": "router-private"}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = Transport(client, Budget(3), lambda *event: events.append(event), settings)
            with pytest.raises(ProviderError, match="compatible"):
                await OpenRouter(transport, settings.openrouter).ask(Parsed, "Parse", "Question")

    asyncio.run(run())
    assert "router-private" not in str(events)


def test_recover_marks_active_runs_interrupted(tmp_path):
    from financial_research.storage import Store

    store = Store(tmp_path / "test.sqlite3")
    session = store.new_session()
    turn = store.create_turn(session, "Question", "replay", None, datetime.now(UTC))
    store.transition(turn, "Queued", "Running")
    store.event(turn, "Parse", 0, "stage", "started", {})
    Store(store.path).recover()
    assert store.turn(turn)["status"] == "Interrupted"
    assert [row["outcome"] for row in store.events(turn)] == ["started", "interrupted"]


@pytest.mark.parametrize("cutoff_hour", [1, 4])
def test_fred_uses_observation_vintage_as_of_cutoff(tmp_path, cutoff_hour):
    from financial_research.domain import Action
    from financial_research.providers import LiveProviders

    config = tmp_path / "config.local.toml"
    config.write_text('[fred]\napi_key="fred-private"')
    settings = load_config(config)

    def handler(request):
        assert request.url.params["realtime_start"] == request.url.params["realtime_end"] == "2024-08-05"
        return httpx.Response(
            200,
            json={
                "observations": [
                    {"date": "2024-06-01", "value": "5.33", "realtime_start": "2024-08-05"},
                ]
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = Transport(client, Budget(3), lambda *_: None, settings)
            return await LiveProviders(transport).lookup(
                Action(
                    source="fred",
                    operation="observations",
                    claim_ids=["c1"],
                    purpose="Check rates",
                    series="FEDFUNDS",
                    start=date(2024, 6, 1),
                    end=date(2024, 6, 30),
                ),
                datetime(2024, 8, 6, cutoff_hour, tzinfo=UTC),
            )

    result = asyncio.run(run())
    assert str(result[0].value) == "5.33"
    assert result[0].vintage == date(2024, 8, 5)
