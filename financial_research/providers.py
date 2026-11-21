import asyncio
import json
import logging
import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from time import monotonic
from types import MappingProxyType
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from .domain import COMPANIES, Action, Document, Fact
from .sec import (
    EASTERN,
    FORMS,
    annual_results_heading,
    document_record,
    exhibit_names,
    filing_rows,
    filing_time,
    filing_url,
    html_passages,
    inline_facts,
    normalize_company_facts,
    reported_quarter_eps,
    stable_id,
)
from .storage import utc_now
from .verification import asks_for_explanation, disclosed_driver, named_metrics

INVESTIGATION_TIMEOUT_SECONDS = 120.0
MODEL_ATTEMPT_TIMEOUT_SECONDS = 120.0
FRED_TIMEZONE = ZoneInfo("America/Chicago")

# FRED requires its credential in the request URL. Keep the libraries' raw request logging disabled.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class ProviderError(Exception):
    """A safe, actionable provider error."""


class Budget:
    def __init__(self, seconds=INVESTIGATION_TIMEOUT_SECONDS):
        self.started = monotonic()
        self.seconds = max(0.0, seconds)

    @property
    def remaining(self):
        return max(0.0, self.seconds - self.elapsed)

    @property
    def elapsed(self):
        return monotonic() - self.started


class Transport:
    RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})

    def __init__(self, client, budget, emit, settings):
        self.client, self.budget, self.emit, self.settings = client, budget, emit, settings

    async def request(
        self,
        tool,
        method,
        url,
        *,
        purpose,
        params=None,
        payload=None,
        headers=None,
        limit=20_000_000,
        timeout=8.0,
        model=None,
    ):
        # Payloads and request credentials never enter the trace.
        kind = "model" if model else "tool"
        trace = {
            "tool": tool,
            "url": url,
            "purpose": purpose,
            "parameters": {
                key: value
                for key, value in (params or {}).items()
                if key.lower() not in {"api_key", "token", "key"}
            },
        }
        if model:
            trace["model"] = model
        for attempt in (1, 2):
            remaining = min(self.budget.remaining - 0.15, timeout)
            if remaining <= 0:
                self.emit(kind, "error", {**trace, "message": "The active run budget expired."})
                raise ProviderError("The active run budget expired before the lookup finished.")
            started = monotonic()
            event = {**trace, "attempt": attempt}
            self.emit(kind, "started", event)
            try:
                async with asyncio.timeout(remaining):
                    async with self.client.stream(
                        method,
                        url,
                        params=params,
                        json=payload,
                        headers=headers,
                        timeout=remaining,
                    ) as response:
                        if response.status_code >= 300:
                            status = response.status_code
                            problem = f"{tool} returned HTTP {status}."
                            retry = status in self.RETRYABLE
                            if model and status in {400, 404, 422}:
                                problem = (
                                    f"{tool} rejected the request (HTTP {status}). Check the endpoint, "
                                    "model ID, and compatible strict JSON Schema support."
                                )
                            elif status in {401, 403}:
                                problem = f"{tool} denied access. Check the provider's local configuration."
                        else:
                            body = bytearray()
                            async for chunk in response.aiter_bytes():
                                body.extend(chunk)
                                if len(body) > limit:
                                    raise ProviderError(f"{tool} returned a response above the size limit.")
                            self.emit(
                                kind,
                                "complete",
                                {
                                    **event,
                                    "elapsed_ms": round((monotonic() - started) * 1000),
                                    "bytes": len(body),
                                },
                            )
                            return bytes(body)
            except (httpx.TimeoutException, TimeoutError):
                problem, retry = f"{tool} timed out.", True
            except httpx.TransportError:
                problem, retry = f"{tool} could not be reached.", True
            except ProviderError as exc:
                problem, retry = str(exc), False
            details = {**event, "message": problem, "elapsed_ms": round((monotonic() - started) * 1000)}
            if attempt == 2 or not retry or self.budget.remaining < 0.5:
                self.emit(kind, "error", details)
                raise ProviderError(problem)
            self.emit(kind, "retry", details)
            await asyncio.sleep(0.15)
        raise AssertionError("Request attempts exhausted")

    async def json(self, *args, **kwargs):
        data = await self.request(*args, **kwargs)
        try:
            result = json.loads(data, parse_float=Decimal)
        except (UnicodeError, ValueError):
            raise ProviderError("The provider returned malformed JSON.") from None
        if not isinstance(result, dict):
            raise ProviderError("The provider returned an unexpected response format.")
        return result


def strict_schema(schema, *, portable=False):
    """All fields must be present in an OpenAI strict object, including nullable fields."""
    if isinstance(schema, list):
        return [strict_schema(item, portable=portable) for item in schema]
    if not isinstance(schema, dict):
        return schema
    omitted = {"default"}
    if portable:
        # Alibaba's Qwen route can emit array filler when these constraints are in its schema.
        # Pydantic still enforces all bounds and formats on the returned object.
        omitted.update({"maxItems", "minItems", "pattern", "maxLength", "minLength", "minimum", "maximum"})
    normalized = {
        key: strict_schema(item, portable=portable) for key, item in schema.items() if key not in omitted
    }
    if normalized.get("type") == "object":
        normalized["required"] = list(normalized.get("properties", {}))
        normalized["additionalProperties"] = False
    return normalized


class OpenAICompatible:
    label = "OpenAI-compatible"

    def __init__(self, transport, settings):
        self.transport, self.settings = transport, settings

    @property
    def portable_schema(self):
        return False

    def request_options(self):
        return {self.settings.token_limit_parameter: 6500}

    async def ask(self, schema, purpose, system, data=None):
        key = self.settings.api_key.get_secret_value()
        model = self.settings.model
        result = await self.transport.json(
            f"{self.label} Chat Completions",
            "POST",
            self.settings.base_url + "/chat/completions",
            headers={"Authorization": f"Bearer {key}"} if key else None,
            purpose=purpose,
            timeout=MODEL_ATTEMPT_TIMEOUT_SECONDS,
            model=model,
            limit=1_500_000,
            payload={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(data or {}, ensure_ascii=False)},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "strict": True,
                        "schema": strict_schema(schema.model_json_schema(), portable=self.portable_schema),
                    },
                },
                **self.request_options(),
            },
        )
        usage = result.get("usage") or {}
        provider = result.get("provider")
        self.transport.emit(
            "model",
            "metadata",
            {
                "model": model,
                "routed_provider": provider if isinstance(provider, str) else None,
                "usage": {
                    key: usage[key]
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if isinstance(usage, dict) and isinstance(usage.get(key), int)
                },
            },
        )
        try:
            if result.get("error"):
                raise KeyError
            message = result["choices"][0]["message"]
            if message.get("refusal"):
                raise ValueError
            decoded = self.transport.settings.redact(json.loads(message["content"]))
            return schema.model_validate(decoded)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, ValidationError):
            raise ProviderError(
                f"{self.label} did not return valid {purpose} output. Check the selected model."
            ) from None


class OpenRouter(OpenAICompatible):
    label = "OpenRouter"

    @property
    def portable_schema(self):
        model = self.settings.model
        return model == "qwen/qwen3.7-plus" or model.startswith("qwen/qwen3.7-plus-")

    def request_options(self):
        options = {"provider": {"require_parameters": True}, "max_tokens": 6500}
        if self.portable_schema:
            options["reasoning"] = {"enabled": False}
        return options

    async def ask(self, schema, purpose, system, data=None):
        if not self.settings.api_key.get_secret_value():
            raise ProviderError("Set OPENROUTER_API_KEY or openrouter.api_key in config.local.toml.")
        return await super().ask(schema, purpose, system, data)


def model_client(transport):
    settings = transport.settings
    if not settings.model_ready:
        raise ProviderError(settings.model_error)
    if settings.model_provider == "openrouter":
        return OpenRouter(transport, settings.openrouter)
    return OpenAICompatible(transport, settings.openai_compatible)


def _day(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        try:
            from email.utils import parsedate_to_datetime

            return parsedate_to_datetime(value).date()
        except (TypeError, ValueError, OverflowError):
            return None


def relevant_passages(passages, query, maximum=20):
    words = {
        word
        for word in re.findall(r"[a-z]{4,}", (query or "").lower())
        if word not in {"with", "from", "about", "when", "this", "what", "which", "that", "find", "company"}
    }
    scored = sorted(
        enumerate(passages),
        key=lambda pair: (
            -sum(word in pair[1].lower() for word in words),
            pair[0],
        ),
    )
    if asks_for_explanation(query or ""):
        metrics = named_metrics(query or "")
        drivers = [(index, passage) for index, passage in scored if disclosed_driver(passage, metrics)]
        # Keep the company-wide result visible alongside the highest-scoring segment excerpts.
        headline = next(
            (item for item in drivers if disclosed_driver(item[1], metrics, metric_first=True)), None
        )
        if headline:
            drivers = [headline, *(item for item in drivers if item != headline)]
        drivers = drivers[: min(3, maximum // 2)]
        prioritized = {index for index, _ in drivers}
        scored = drivers + [pair for pair in scored if pair[0] not in prioritized]
    return [passage for _, passage in scored[:maximum]]


class LiveProviders:
    FRED_UNITS: Mapping = MappingProxyType(
        {"FEDFUNDS": "%", "CPIAUCSL": "Index (1982–84 = 100)", "UNRATE": "%"}
    )

    def __init__(self, transport):
        self.transport = transport
        self.settings = transport.settings
        self.sec_requests = {}

    async def lookup(self, action: Action, cutoff: datetime, known=None):
        if action.source == "sec":
            return await self.sec(action, cutoff, known or [])
        if action.source == "fred":
            return await self.fred(action, cutoff)
        return await self.news(action, cutoff)

    async def _sec_json(self, url, tool, purpose):
        if not self.settings.sec.user_agent:
            raise ProviderError(
                "Set SEC_USER_AGENT or sec.user_agent in config.local.toml to your project name and contact email."
            )
        if url not in self.sec_requests:
            self.sec_requests[url] = asyncio.create_task(
                self.transport.json(
                    tool,
                    "GET",
                    url,
                    purpose=purpose,
                    headers={"User-Agent": self.settings.sec.user_agent, "Accept": "application/json"},
                )
            )
        try:
            return await self.sec_requests[url]
        except ProviderError:
            self.sec_requests.pop(url, None)
            raise

    async def _sec_html(self, entity, accession, filename, purpose):
        return await self.transport.request(
            "SEC filing archive",
            "GET",
            filing_url(entity, accession, filename),
            purpose=purpose,
            headers={"User-Agent": self.settings.sec.user_agent, "Accept": "text/html"},
        )

    async def _submissions(self, entity, earliest, purpose):
        cik = COMPANIES[entity][1]
        data = await self._sec_json(
            f"https://data.sec.gov/submissions/CIK{cik:010}.json", "SEC Submissions", purpose
        )
        filings = data.get("filings", {})
        result = filing_rows(filings.get("recent", {}))
        recent_dates = [item.get("filingDate") for item in result.values() if item.get("filingDate")]
        if earliest and recent_dates and earliest.isoformat() < min(recent_dates):
            for block in filings.get("files", []):
                name, end = block.get("name", ""), _day(block.get("filingTo"))
                if end and end >= earliest and re.fullmatch(r"CIK\d{10}-submissions-\d+\.json", name):
                    archive = await self._sec_json(
                        f"https://data.sec.gov/submissions/{name}", "SEC Submissions archive", purpose
                    )
                    result.update(filing_rows(archive))
        return result

    async def _companyfacts(self, entity, purpose):
        cik = COMPANIES[entity][1]
        return await self._sec_json(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010}.json", "SEC Company Facts", purpose
        )

    async def sec(self, action, cutoff, known):
        entity = action.entity
        earliest = action.start or (
            date(min(item.year for item in action.periods) - 1, 1, 1) if action.periods else None
        )
        if action.operation == "financials":
            filings, facts = await asyncio.gather(
                self._submissions(entity, earliest, action.purpose),
                self._companyfacts(entity, action.purpose),
                return_exceptions=True,
            )
            for response in (filings, facts):
                if isinstance(response, asyncio.CancelledError):
                    raise ProviderError("The active run budget expired.")
                if isinstance(response, Exception):
                    raise response
            return await asyncio.to_thread(
                normalize_company_facts, entity, facts, filings, action.periods, cutoff
            )
        filings = await self._submissions(entity, earliest, action.purpose)
        period = action.periods[-1] if action.periods else None
        period_facts = [
            item
            for item in known
            if isinstance(item, Fact)
            and item.entity == entity
            and period
            and (item.fiscal_year, item.quarter) == (period.year, period.quarter)
        ]
        if period and not period_facts:
            raw = await self._companyfacts(entity, action.purpose)
            period_facts = [
                item
                for item in await asyncio.to_thread(
                    normalize_company_facts,
                    entity,
                    raw,
                    filings,
                    [period],
                    cutoff,
                )
                if item.fiscal_year == period.year and item.quarter == period.quarter
            ]
        period_end = max((item.end for item in period_facts), default=None)
        eligible = []
        for accession, row in filings.items():
            filed = _day(row.get("filingDate"))
            if not filed or filing_time(row, filed) > cutoff:
                continue
            form = row.get("form", "")
            if action.operation == "release":
                if form not in {"8-K", "8-K/A"} or row.get("items") and "2.02" not in row["items"]:
                    continue
                if period_end and not period_end <= filed <= period_end + timedelta(days=110):
                    continue
            elif form not in FORMS | {"8-K", "8-K/A"}:
                continue
            if (action.start and filed < action.start) or (action.end and filed > action.end):
                continue
            if (
                period
                and not period_end
                and not action.start
                and not date(period.year - 1, 1, 1) <= filed <= date(period.year + 1, 3, 31)
            ):
                continue
            distance = abs((filed - period_end).days) if period_end else 0
            native = bool(period_end and row.get("reportDate") == period_end.isoformat())
            eligible.append(((not native, distance, -filed.toordinal()), accession, row))
        documents, errors = [], []
        for _, accession, row in sorted(eligible)[: 3 if action.operation == "release" else 2]:
            if self.transport.budget.remaining < 0.65:
                break
            try:
                filename = row.get("primaryDocument")
                if action.operation == "release":
                    index = await self._sec_html(entity, accession, None, action.purpose)
                    names = exhibit_names(index)
                    if not names:
                        continue
                    filename = names[0]
                raw = await self._sec_html(entity, accession, filename, action.purpose)
            except ProviderError as error:
                errors.append(error)
                continue
            passages = await asyncio.to_thread(html_passages, raw)
            document = document_record(
                entity,
                accession,
                row,
                filename,
                passages,
                cutoff,
                period,
                action.operation == "release",
                period_end,
            )
            if document:
                ranked = relevant_passages(document.passages, action.query or action.purpose)
                if (
                    action.operation == "release"
                    and document.period
                    and annual_results_heading(document.passages[0], document.period)
                ):
                    heading = document.passages[0]
                    ranked = [heading, *(passage for passage in ranked if passage != heading)][:20]
                documents.append(document.model_copy(update={"passages": ranked}))
                if action.operation == "release" and document.period and period_facts:
                    start = min(item.start for item in period_facts)
                    eps = await asyncio.to_thread(reported_quarter_eps, document, start, period_end, raw)
                    if eps:
                        documents.append(eps)
                if action.operation == "filing" and action.periods and not action.query:
                    documents.extend(await asyncio.to_thread(inline_facts, document, raw, action.periods))
        if not documents and errors:
            raise errors[0]
        return documents

    async def fred(self, action, cutoff):
        key = self.settings.fred.api_key.get_secret_value()
        if not key:
            raise ProviderError(
                "Set FRED_API_KEY or fred.api_key in config.local.toml to retrieve macro observations."
            )
        vintage = cutoff.astimezone(FRED_TIMEZONE).date()
        end = min(action.end or vintage, vintage)
        first = action.start or end - timedelta(days=420)
        data = await self.transport.json(
            "FRED series observations",
            "GET",
            "https://api.stlouisfed.org/fred/series/observations",
            purpose=action.purpose,
            params={
                "api_key": key,
                "file_type": "json",
                "series_id": action.series,
                "realtime_start": vintage.isoformat(),
                "realtime_end": vintage.isoformat(),
                "observation_start": first.isoformat(),
                "observation_end": end.isoformat(),
            },
        )
        output = []
        for row in data.get("observations", []):
            day = _day(row.get("date"))
            if not day or not first <= day <= end:
                continue
            try:
                value = Decimal(str(row["value"]))
            except (KeyError, InvalidOperation):
                continue
            if not value.is_finite():
                continue
            output.append(
                Fact(
                    id=stable_id("fred", action.series, day, vintage, value),
                    source="fred",
                    title=f"{action.series} monthly observation",
                    url=f"https://alfred.stlouisfed.org/series?seid={action.series}&vintage_date={vintage}",
                    metric=action.series,
                    start=day,
                    end=day,
                    unit=self.FRED_UNITS[action.series],
                    frequency="Monthly",
                    vintage=vintage,
                    retrieved_at=utc_now(),
                    available_at=cutoff,
                    value=value,
                    publisher="Federal Reserve Bank of St. Louis",
                )
            )
        return output

    async def news(self, action, cutoff):
        key = self.settings.tavily.api_key.get_secret_value()
        if not key:
            raise ProviderError(
                "Set TAVILY_API_KEY or tavily.api_key in config.local.toml to retrieve news coverage."
            )
        end = min(action.end or cutoff.astimezone(EASTERN).date(), cutoff.astimezone(EASTERN).date())
        payload = {
            "query": action.query,
            "topic": "news",
            "search_depth": "basic",
            "max_results": 5,
            "include_raw_content": "text",
            "include_published_date": True,
            "filter_by_published_date": True,
            "end_date": end.isoformat(),
        }
        if action.start:
            payload["start_date"] = action.start.isoformat()
        data = await self.transport.json(
            "Tavily Search",
            "POST",
            "https://api.tavily.com/search",
            purpose=action.purpose,
            headers={"Authorization": f"Bearer {key}"},
            payload=payload,
            limit=2_500_000,
        )
        documents = []
        issuer_domains = {
            "MSFT": ("microsoft.com",),
            "AAPL": ("apple.com",),
            "NVDA": ("nvidia.com",),
            "AMZN": ("amazon.com", "aboutamazon.com"),
            "GOOGL": ("google.com", "abc.xyz"),
            "META": ("meta.com", "fb.com", "facebook.com"),
            "TSLA": ("tesla.com",),
            "COST": ("costco.com",),
        }
        for item in data.get("results", []):
            url = item.get("url", "")
            parts = urlsplit(url)
            published = _day(item.get("published_date"))
            text = item.get("raw_content") or ""
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username
                or not published
                or published > end
                or action.start
                and published < action.start
                or not isinstance(text, str)
                or len(text) < 200
            ):
                continue
            if re.search(
                r"access denied|subscribe to (?:continue|read)|enable javascript|sign in to continue",
                text[:1600],
                re.IGNORECASE,
            ):
                continue
            passage_candidates = [
                passage.strip()
                for passage in re.split(r"\n+|(?<=[.!?])\s+", text[:65_000])
                if 45 <= len(passage.strip()) <= 1400
            ]
            if not passage_candidates:
                continue
            domain = parts.hostname.removeprefix("www.")
            attributed_domains = (
                "sec.gov",
                "prnewswire.com",
                "businesswire.com",
                "globenewswire.com",
                *issuer_domains.get(action.entity, ()),
            )
            independent = not any(
                domain == name or domain.endswith("." + name) for name in attributed_domains
            )
            documents.append(
                Document(
                    id=stable_id("news", url, published, stable_id(text[:65_000])),
                    source="news",
                    entity=action.entity,
                    url=url,
                    title=str(item.get("title") or domain)[:240],
                    publisher=domain,
                    published=published,
                    retrieved_at=utc_now(),
                    passages=relevant_passages(passage_candidates, action.query, 12),
                    independent=independent,
                )
            )
        return documents
