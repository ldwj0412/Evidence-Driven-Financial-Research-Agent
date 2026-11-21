import asyncio
import json
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


@pytest.mark.parametrize("valid", [True, False])
def test_compatible_adapter_sends_only_chat_completions_fields(tmp_path, valid):
    from financial_research.providers import model_client

    config = tmp_path / "config.local.toml"
    config.write_text(
        '[model]\nprovider="openai_compatible"\n[openai_compatible]\n'
        'api_key="compatible-private"\nbase_url="https://chat.example.com/v1/"\nmodel="native-model"'
    )
    settings = load_config(config)
    assert settings.model_ready
    assert settings.redact("compatible-private") == "[redacted]"
    assert not any(message.startswith("OpenRouter") for message in settings.messages)

    def handler(request):
        assert str(request.url) == "https://chat.example.com/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer compatible-private"
        payload = json.loads(request.content)
        assert payload["model"] == "native-model"
        assert payload["max_completion_tokens"] == 6500
        assert "provider" not in payload
        assert "reasoning" not in payload
        assert payload["response_format"]["json_schema"]["strict"]
        content = {"out_of_scope": "Test fixture"} if valid else {"unexpected": "schema violation"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = model_client(Transport(client, Budget(3), lambda *_: None, settings))
            if valid:
                assert (await adapter.ask(Parsed, "Parse", "Question")).out_of_scope == "Test fixture"
            else:
                with pytest.raises(ProviderError, match="did not return valid Parse"):
                    await adapter.ask(Parsed, "Parse", "Question")

    asyncio.run(run())


@pytest.mark.parametrize(
    ("url", "ready"),
    [
        ("http://127.0.0.1:8000/v1", True),
        ("http://[::1]:8000/v1", True),
        ("http://chat.example.com/v1", False),
        ("https://chat.example.com/v1", False),
    ],
)
def test_compatible_only_allows_keyless_or_http_endpoints_on_loopback(tmp_path, monkeypatch, url, ready):
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    config = tmp_path / "config.local.toml"
    config.write_text(
        f'[model]\nprovider="openai_compatible"\n[openai_compatible]\nbase_url="{url}"\nmodel="native-model"'
    )
    assert load_config(config).model_ready is ready


def test_invalid_model_selection_does_not_fall_back_to_openrouter(tmp_path):
    config = tmp_path / "config.local.toml"
    config.write_text('[model]\nprovider="typo"\n[openrouter]\napi_key="router-private"')
    assert not load_config(config).model_ready


def test_browser_model_settings_preserve_only_keys_for_the_same_endpoint(tmp_path):
    config = tmp_path / "config.local.toml"
    config.write_text(
        '[openrouter]\napi_key="test-router-from-file"\n'
        '[openai_compatible]\napi_key="test-compatible-from-file"\n'
        'base_url="https://company.example/v1"\nmodel="base-model"\n'
        '[fred]\napi_key="test-fred-from-file"\n'
    )
    settings = load_config(config)
    form = {
        "provider": "openai_compatible",
        "base_url": "https://company.example/v1/",
        "model": "browser-model",
        "token_limit_parameter": "max_tokens",
        "api_key": "",
    }
    effective = settings.with_model(settings.model_from_form(form))
    assert effective.model_ready
    assert effective.model_provider == "openai_compatible"
    assert effective.openai_compatible.api_key.get_secret_value() == "test-compatible-from-file"
    assert effective.openrouter == settings.openrouter
    assert effective.fred == settings.fred
    view = effective.model_settings_view()
    assert view["provider"] == "openai_compatible"
    assert view["providers"]["openai_compatible"] == {
        "base_url": "https://company.example/v1",
        "model": "browser-model",
        "token_limit_parameter": "max_tokens",
        "has_key": True,
    }
    assert "test-router-from-file" not in str(view)
    assert "test-compatible-from-file" not in str(view)
    assert load_config(config) == settings

    with pytest.raises(ValueError, match="Enter an API key for this endpoint"):
        effective.model_from_form({**form, "base_url": "https://other.example/v1"})
    loopback = effective.model_from_form({**form, "base_url": "http://127.0.0.1:8000/v1"})
    assert loopback.ready
    assert not loopback.api_key.get_secret_value()


def test_session_data_source_settings_preserve_defaults_and_later_blank_fields(tmp_path, monkeypatch):
    from financial_research.config import DataSourceOverrides

    monkeypatch.setenv("FRED_API_KEY", "fred-from-environment")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    config = tmp_path / "config.local.toml"
    config.write_text('[openrouter]\napi_key="router-from-file"\n')
    base = load_config(config)
    assert base.data_source_settings_view()["providers"] == {
        "sec": {"configured": False},
        "fred": {"configured": True},
        "tavily": {"configured": False},
    }
    form = {
        "sec_project": "Session Evidence Lab",
        "sec_email": "session-contact@example.test",
        "fred_api_key": " ",
        "tavily_api_key": "tavily-session-token",
    }
    overrides = DataSourceOverrides().updated_from_form(form)
    model = base.model_from_form({"provider": "openrouter", "model": "openai/session-model"})
    active = base.with_model(model).with_data_sources(overrides)
    assert active.openrouter.model == "openai/session-model"
    assert active.fred == base.fred
    assert form["sec_project"] in active.sec.user_agent
    assert form["sec_email"] in active.sec.user_agent
    assert active.tavily.api_key.get_secret_value() == form["tavily_api_key"]
    assert all(not message.startswith(("SEC EDGAR ", "FRED ", "Tavily ")) for message in active.messages)

    edited = overrides.updated_from_form({"fred_api_key": "fred-session-token", "tavily_api_key": " "})
    updated = base.with_data_sources(edited)
    assert updated.sec == active.sec
    assert updated.tavily == active.tavily
    assert updated.fred.api_key.get_secret_value() == "fred-session-token"
    assert updated.data_source_settings_view()["providers"] == {
        provider: {"configured": True} for provider in ("sec", "fred", "tavily")
    }
    view_text = str(updated.data_source_settings_view())
    for secret in (form["sec_project"], form["sec_email"], form["tavily_api_key"], "fred-session-token"):
        assert secret not in view_text
    for secret in (updated.sec.user_agent, form["sec_email"], form["tavily_api_key"], "fred-session-token"):
        assert updated.redact(secret) == "[redacted]"
    assert load_config(config) == base


@pytest.mark.parametrize(
    "form",
    [
        {"sec_project": "Incomplete SEC project"},
        {"sec_email": "incomplete-contact@example.test"},
        {"sec_project": "Private Example Lab", "sec_email": "invalid-private-contact"},
        {"sec_project": "Private Lab\r\nHeader injection", "sec_email": "contact@example.test"},
        {"fred_api_key": "malformed fred token"},
        {"tavily_api_key": "malformed tavily token"},
    ],
)
def test_invalid_session_data_source_inputs_never_enter_validation_errors(form):
    from financial_research.config import DataSourceOverrides

    with pytest.raises(ValueError) as detail:
        DataSourceOverrides().updated_from_form(form)
    assert all(value not in str(detail.value) for value in form.values())


def test_data_source_overrides_are_sent_only_to_their_provider(tmp_path):
    from financial_research.config import DataSourceOverrides
    from financial_research.domain import Action, Period
    from financial_research.providers import LiveProviders

    form = {
        "sec_project": "Provider Transport Lab",
        "sec_email": "transport-contact@example.test",
        "fred_api_key": "transport-fred-token",
        "tavily_api_key": "transport-tavily-token",
    }
    settings = load_config(tmp_path / "config.local.toml").with_data_sources(
        DataSourceOverrides().updated_from_form(form)
    )
    requests = []
    events = []

    def handler(request):
        host = request.url.host
        requests.append(host)
        if host == "data.sec.gov":
            assert request.headers["User-Agent"] == settings.sec.user_agent
            assert "Authorization" not in request.headers
            assert "api_key" not in request.url.params
        elif host == "api.stlouisfed.org":
            assert request.url.params["api_key"] == form["fred_api_key"]
            assert "Authorization" not in request.headers
        else:
            assert host == "api.tavily.com"
            assert request.headers["Authorization"] == f"Bearer {form['tavily_api_key']}"
            assert "api_key" not in request.url.params
        if host != "data.sec.gov":
            assert form["sec_email"] not in request.headers.get("User-Agent", "")
        return httpx.Response(200, json={})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LiveProviders(
                Transport(client, Budget(3), lambda *event: events.append(event), settings)
            )
            cutoff = datetime(2024, 8, 15, tzinfo=UTC)
            for action in (
                Action(
                    source="sec",
                    operation="financials",
                    entity="MSFT",
                    periods=[Period(year=2024)],
                    claim_ids=["c1"],
                    purpose="Check financials",
                ),
                Action(
                    source="fred",
                    operation="observations",
                    series="FEDFUNDS",
                    claim_ids=["c1"],
                    purpose="Check rates",
                ),
                Action(
                    source="news",
                    operation="articles",
                    query="Microsoft earnings",
                    claim_ids=["c1"],
                    purpose="Find coverage",
                ),
            ):
                await provider.lookup(action, cutoff)

    asyncio.run(run())
    assert sorted(requests) == ["api.stlouisfed.org", "api.tavily.com", "data.sec.gov", "data.sec.gov"]
    assert all(value not in str(events) for value in form.values())
    assert "api_key" not in str(events)


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
